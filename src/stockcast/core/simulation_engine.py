"""
SimulationEngine for the DataFrame-based multi-SKU inventory management system.

This module provides:
    - SimulationEngine: Orchestrates multi-period inventory simulation (like PyTorch DataLoader)
    - SimulationResult: Container for simulation outputs with summary statistics

The engine wraps the existing simulation primitives (process_demand, update_inventory_with_orders,
policy.predict) into an automated loop. Typed ``SimulationCallback`` objects are
the supported extension surface; they never receive live mutable engine state.

Usage:
    # Simple usage
    engine = SimulationEngine()
    result = engine.run(
        policy=policy,
        demand_source=demand_df,
        inventory=inventory,
        n_periods=365,
        period_frequency="D",
        initial_decision="none",
        warmup_periods=0,
        scoring_periods=365,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="example_demand",
        random_seed=None,
    )
    print(result.summary())

    # Ordered, removable callbacks are passed with callbacks=[...].
"""

import copy
import hashlib
import importlib.metadata
import json
import platform
import warnings
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Union
from uuid import uuid4

import numpy as np
import pandas as pd

from stockcast.core._array_state import (
    AUDIT_COLUMNS,
    ArrayState,
    DemandPath,
    FastRecord,
    FrameRecord,
    RunLog,
    assert_flow_balance,
    audit_default,
)
from stockcast.core.base_policy import BasePolicy
from stockcast.core.callbacks import (
    CallbackContext,
    CallbackError,
    InventoryAdjustmentResult,
    OrderAdjustmentResult,
    SimulationCallback,
)
from stockcast.core.data_structures import (
    InventoryStateDataFrame,
    OrderDecision,
    _identifier_sample,
    _require_forward_frequency,
    _require_identifiers,
)
from stockcast.core._open_orders import (
    DELIVERY_OUTCOME_COLUMNS,
    ORDER_FRAME_COLUMNS,
    _Deliveries,
    _objects as _object_series,
    build_order_frame,
    pipeline_mismatch,
)
from stockcast.core.order_constraints import ConstraintContext, OrderingConstraints
from stockcast.core.processes import (
    PROCESS_FLOW_COLUMNS,
    InventoryProcess,
    ProcessRunner,
    StockChange,
    prepare_processes,
    process_manifest,
)
from stockcast.core.supply import AllocationContext, DeliveryContext, SupplyModel

CALLBACK_AUDIT_COLUMNS = (
    "callback_position",
    "callback_module",
    "callback_class",
    "phase",
    "period",
    "date",
    "run_window",
    "initial_decision",
    "unique_id",
    "before_value",
    "after_value",
    "quantity_delta",
    "order_quantity",
    "reason",
    "source",
    "received_date",
    "lot_evidence",
)

# Stable top-level sections of ``SimulationResult.run_manifest`` for 0.1.x.
# Their nested contents are documented as additive: a patch release may add
# descriptive fields but must not remove or reinterpret these sections.
RUN_MANIFEST_REQUIRED_SECTIONS = (
    "run_id",
    "created_at_utc",
    "demand_source",
    "package",
    "policy",
    "opening_inventory",
    "run_settings",
    "dependencies",
)


# ============================================================================
# SIMULATION RESULT
# ============================================================================

class SimulationResult:
    """The outcome of one simulation run.

    Returned by ``SimulationEngine.run``. The event ledger is the main record;
    the other tables and the manifest add detail.

    Attributes:
        inventory: The final ``InventoryStateDataFrame``.
        history: State snapshots after each period.
        n_periods: Number of simulated demand periods.
        policy_name: Name of the policy.
        run_settings: The run's settings (also in ``run_manifest``).
        run_manifest: A JSON-friendly record of inputs, settings and versions,
            with the sections in ``RUN_MANIFEST_REQUIRED_SECTIONS``.

    Example:
        ```python
        events = result.to_event_frame(window="scoring")
        orders = result.to_order_frame()
        result.summary()["fill_rate"]
        ```
    """

    def __init__(
        self,
        history: pd.DataFrame,
        inventory: InventoryStateDataFrame,
        n_periods: int,
        policy_name: str,
        event_frame: Optional[pd.DataFrame] = None,
        run_settings: Optional[dict] = None,
        run_manifest: Optional[dict] = None,
        callback_audit: Optional[pd.DataFrame] = None,
        order_frame: Optional[pd.DataFrame] = None,
        process_flows: Optional[pd.DataFrame] = None,
    ):
        self.history = history
        self.inventory = inventory
        self.n_periods = n_periods
        self.policy_name = policy_name
        self.run_settings = dict(run_settings or {})
        self.run_manifest = dict(run_manifest or {})
        self._event_frame = (
            event_frame.copy()
            if event_frame is not None
            else pd.DataFrame()
        )
        self._callback_audit = pd.DataFrame(
            callback_audit.copy(deep=True) if callback_audit is not None else None,
            columns=CALLBACK_AUDIT_COLUMNS,
        )
        order_columns = ORDER_FRAME_COLUMNS
        if order_frame is not None and set(DELIVERY_OUTCOME_COLUMNS) <= set(order_frame.columns):
            # Runs with supplier delivery outcomes add their resolution columns.
            order_columns = ORDER_FRAME_COLUMNS + DELIVERY_OUTCOME_COLUMNS
        self._order_frame = pd.DataFrame(
            order_frame.copy(deep=True) if order_frame is not None else None,
            columns=order_columns,
        )
        self._process_flows = pd.DataFrame(
            process_flows.copy(deep=True) if process_flows is not None else None,
            columns=PROCESS_FLOW_COLUMNS,
        )

    def to_event_frame(self, window: Optional[str] = None) -> pd.DataFrame:
        """Return the event ledger: one row per SKU and period.

        Every row satisfies the stock, pipeline and backorder balance identities.
        Columns are described in the output-tables reference
        (``CANONICAL_EVENT_COLUMNS`` plus optional columns).

        Args:
            window: ``"warmup"``, ``"scoring"``, ``"settlement"``, or ``"all"``
                / ``None`` for every row.

        Returns:
            A copy of the ledger rows.
        """
        result = self._event_frame.copy()
        if window is None or window == "all":
            return result
        if window not in {"warmup", "scoring", "settlement"}:
            raise ValueError("window must be 'all', 'warmup', 'scoring', or 'settlement'")
        if "run_window" not in result.columns:
            raise ValueError("event frame does not contain run-window metadata")
        return result[result["run_window"] == window].copy()

    def to_callback_audit_frame(self) -> pd.DataFrame:
        """Return one row per accepted callback effect (``CALLBACK_AUDIT_COLUMNS``).
        """
        return self._callback_audit.copy(deep=True)

    def to_order_frame(self) -> pd.DataFrame:
        """
        Return the order-level ledger: one row per scheduled delivery.

        Rows cover the opening pipeline and every order placed during the
        run. Columns are ``stockcast.core.ORDER_FRAME_COLUMNS``:

            - order_id: order line identifier (deliveries of one line share it)
            - unique_id, supplier_id: SKU and supplier (``None`` when unknown,
              for example a pipeline given only as ``in_transit`` or a run
              without ``supply``)
            - source: ``"opening"`` for the opening pipeline, ``"placed"``
              for orders placed in the run
            - order_period / order_date: when the line was ordered (missing
              for opening orders without a declared order period)
            - due_period / due_date: when this delivery is received, before
              that period's demand
            - lead_time: ``due_period - order_period`` (realized, per delivery)
            - ordered_quantity: quantity of the whole order line
            - delivery_quantity: quantity of this delivery
            - status: ``"received"`` or ``"open"`` at the end of the run

        Per SKU and period, received deliveries add up to the event ledger's
        ``received_units`` and open ones to ``on_order_end``.
        """
        return self._order_frame.copy(deep=True)

    def to_process_flow_frame(self) -> pd.DataFrame:
        """
        Return one row per nonzero process flow, SKU and period.

        Columns are ``stockcast.core.PROCESS_FLOW_COLUMNS``: the SKU, the
        event-ledger ``period``/``date``/``demand_period``/``run_window``,
        the ``process`` and ``flow`` names, the flow's ``direction``
        (``inflow``/``outflow``) and ``category`` (``general``/``expiry``),
        the ``phase`` it acted in, and its nonnegative ``quantity``.

        Per SKU and period, expiry flows add up to the event ledger's
        ``expired_units``; general inflows and outflows add up to
        ``process_inflow_units`` and ``process_outflow_units``. The frame is
        empty for a run without processes.
        """
        return self._process_flows.copy(deep=True)

    def summary(self) -> Dict:
        """A few headline numbers for the scoring window.

        Returns:
            dict with keys:
                - fill_rate: share of scoring demand served from stock in its own
                  period, ``1 - shortage / demand``.
                - demand_period_service_level: share of SKU-period rows with demand
                  and no shortage.
                - mean_ending_on_hand_per_sku_period: mean ending stock per row.
                - stockout_periods: periods in which at least one SKU was short.
                - total_order_units: units ordered.
                - order_event_count: decisions that placed a positive order.
                - sku_order_line_count: positive order lines.
        """
        e = self.to_event_frame(window="scoring")
        if e.empty:
            return {
                'fill_rate': 1.0,
                'demand_period_service_level': 1.0,
                'mean_ending_on_hand_per_sku_period': 0.0,
                'stockout_periods': 0,
                'total_order_units': 0.0,
                'order_event_count': 0,
                'sku_order_line_count': 0,
            }

        period_events = e[e['event_type'] == 'period']
        if period_events.empty:
            raise ValueError("scoring event frame does not contain period events")

        required_numbers = {
            "period events": (
                period_events,
                ['demand', 'shortage_units', 'ending_on_hand'],
            ),
            "scoring events": (
                e,
                ['order_quantity', 'order_event_count', 'sku_order_line_count'],
            ),
        }
        for label, (frame, columns) in required_numbers.items():
            missing = sorted(set(columns) - set(frame.columns))
            if missing:
                raise ValueError(f"{label} are missing columns: {missing}")
            numbers = frame[columns].apply(pd.to_numeric, errors='coerce')
            if numbers.isna().any().any() or not np.isfinite(numbers.to_numpy()).all():
                raise ValueError(f"{label} must contain complete finite numeric values")
            if (numbers < 0).any().any():
                raise ValueError(f"{label} must contain non-negative numeric values")
        if 'stockout_flag' not in period_events.columns:
            raise ValueError("period events are missing columns: ['stockout_flag']")
        if period_events['stockout_flag'].isna().any():
            raise ValueError("period events must contain complete stockout flags")

        total_demand = period_events['demand'].sum()
        total_shortage = period_events['shortage_units'].sum()
        fill_rate = 1.0 if total_demand <= 0 else max(0.0, 1.0 - (total_shortage / total_demand))

        eligible = period_events[period_events['demand'] > 0]
        demand_period_service = 1.0 if eligible.empty else float(
            (eligible['shortage_units'] <= 0).mean()
        )

        stockout_periods = int(
            (period_events.groupby('demand_period')['stockout_flag'].any()).sum()
        )
        return {
            'fill_rate': fill_rate,
            'demand_period_service_level': demand_period_service,
            'mean_ending_on_hand_per_sku_period': period_events['ending_on_hand'].mean(),
            'stockout_periods': stockout_periods,
            'total_order_units': e['order_quantity'].sum(),
            'order_event_count': int(e['order_event_count'].sum()),
            'sku_order_line_count': int(e['sku_order_line_count'].sum()),
        }

    def __repr__(self) -> str:
        return (
            f"SimulationResult(policy={self.policy_name}, "
            f"n_periods={self.n_periods}, "
            f"history_rows={len(self.history)})"
        )


def _sum_in_transit(value) -> float:
    """Return the total pipeline inventory for a row."""
    if hasattr(value, "sum"):
        return float(value.sum())
    return 0.0


def _build_period_event_frame(
    inventory_before: InventoryStateDataFrame,
    inventory_after_demand: InventoryStateDataFrame,
    inventory_after_orders: InventoryStateDataFrame,
    policy: BasePolicy,
    demand_period: int,
    order_event_count: int,
    sku_order_line_counts: Mapping[str, int],
    order_line_quantity_squared_sums: Mapping[str, float],
) -> pd.DataFrame:
    """
    Normalize one simulation step to one event row per SKU.

    The event frame is the evaluation source of truth for inventory metrics.
    """
    before_df = inventory_before.get_dataframe().copy()
    after_demand_df = inventory_after_demand.get_dataframe().copy()
    after_orders_df = inventory_after_orders.get_dataframe().copy()

    sku_column = inventory_before.sku_column
    before_df['starting_on_order'] = before_df['in_transit'].apply(_sum_in_transit)
    after_orders_df['on_order_end'] = after_orders_df['in_transit'].apply(_sum_in_transit)

    event_df = before_df[[
        sku_column,
        'on_hand',
        'safety_stock',
        'backorders',
        'starting_on_order',
    ]].merge(
        after_demand_df[[
            sku_column,
            'period',
            'date',
            'on_hand',
            'backorders',
            'latest_received',
            'latest_fulfilled',
            'latest_backorders_fulfilled',
            'latest_incoming_demand',
            'latest_shortage',
            'is_review_period',
        ]],
        on=sku_column,
        how='left',
        suffixes=('_start', '_end'),
    ).merge(
        after_orders_df[[
            sku_column,
            'target_level',
            'latest_order',
            'on_order_end',
        ]],
        on=sku_column,
        how='left',
        suffixes=('', '_post_order'),
    )

    event_df = event_df.rename(
        columns={
            sku_column: 'unique_id',
            'on_hand_start': 'starting_on_hand',
            'backorders_start': 'starting_backorders',
            'on_hand_end': 'ending_on_hand',
            'backorders': 'backorders_end',
            'latest_incoming_demand': 'demand',
            'latest_shortage': 'shortage_units',
            'latest_received': 'received_units',
            'latest_fulfilled': 'fulfilled_units',
            'latest_backorders_fulfilled': 'backorders_fulfilled',
            'latest_order': 'order_quantity',
        }
    )

    numeric_columns = [
        'starting_on_hand',
        'starting_backorders',
            'starting_on_order',
            'safety_stock',
        'ending_on_hand',
        'backorders_end',
        'demand',
        'received_units',
        'fulfilled_units',
        'backorders_fulfilled',
        'shortage_units',
        'order_quantity',
        'target_level',
        'safety_stock',
        'on_order_end',
    ]
    for column in numeric_columns:
        event_df[column] = pd.to_numeric(event_df[column], errors='coerce')

    event_df['policy'] = policy.policy_name
    event_df['event_type'] = 'period'
    event_df['demand_period'] = pd.Series(
        [demand_period] * len(event_df),
        index=event_df.index,
        dtype="Int64",
    )
    event_df['date'] = pd.to_datetime(event_df['date']).astype("datetime64[ns]")
    event_df['allow_backorders'] = policy.allow_backorders
    event_df['decision_flag'] = event_df['is_review_period'].fillna(False).astype(bool)
    event_df['inventory_adjustment_units'] = 0.0
    event_df['expired_units'] = 0.0
    event_df['backorder_increment'] = 0.0
    if policy.allow_backorders:
        event_df['backorder_increment'] = event_df['shortage_units'].fillna(0.0)
    event_df['lost_sales_units'] = 0.0
    if not policy.allow_backorders:
        event_df['lost_sales_units'] = event_df['shortage_units'].fillna(0.0)
    event_df['inventory_position_end'] = (
        event_df['ending_on_hand'].fillna(0.0)
        + event_df['on_order_end'].fillna(0.0)
        - event_df['backorders_end'].fillna(0.0)
    )
    event_df['stockout_flag'] = event_df['shortage_units'].fillna(0.0) > 0
    event_df['backorder_flag'] = event_df['backorders_end'].fillna(0.0) > 0
    event_df['sku_order_line_count'] = (
        event_df['unique_id'].map(sku_order_line_counts).fillna(0).astype(int)
    )
    event_df['order_line_quantity_squared_sum'] = (
        event_df['unique_id']
        .map(order_line_quantity_squared_sums)
        .fillna(0.0)
        .astype(float)
    )
    event_df['order_event_count'] = 0
    if len(event_df):
        # Store the period-level count once so it remains additive across SKUs.
        event_df.loc[event_df.index[0], 'order_event_count'] = order_event_count

    ordered_columns = [
        'unique_id',
        'event_type',
        'demand_period',
        'period',
        'date',
        'policy',
        'allow_backorders',
        'is_review_period',
        'decision_flag',
        'starting_on_hand',
        'starting_backorders',
        'starting_on_order',
        'received_units',
        'demand',
        'fulfilled_units',
        'backorders_fulfilled',
        'shortage_units',
        'lost_sales_units',
        'backorder_increment',
        'ending_on_hand',
        'backorders_end',
        'on_order_end',
        'inventory_position_end',
        'order_quantity',
        'order_event_count',
        'sku_order_line_count',
        'order_line_quantity_squared_sum',
        'expired_units',
        'inventory_adjustment_units',
        'target_level',
        'safety_stock',
        'stockout_flag',
        'backorder_flag',
    ]
    return event_df[ordered_columns]


def _flow_balance_tolerance(*terms: pd.Series) -> pd.Series:
    """Absolute floor plus rounding allowance scaled by the flows being summed.

    A fixed 1e-9 tolerance is below one floating-point ulp once quantities
    reach about 1e7 (e.g. grams or millilitres), which made valid runs fail.
    """
    magnitude = sum(term.astype(float).abs() for term in terms)
    return 1e-9 + 1e-12 * magnitude


def _assert_event_flow_balance(event_df: pd.DataFrame) -> None:
    """Assert physical-stock, backlog, and pipeline balances per event row."""
    physical_terms = [
        event_df['starting_on_hand'],
        event_df['received_units'],
        event_df['backorders_fulfilled'],
        event_df['fulfilled_units'],
        event_df['expired_units'],
        event_df['inventory_adjustment_units'],
    ]
    physical_expected = (
        event_df['starting_on_hand']
        + event_df['received_units']
        - event_df['backorders_fulfilled']
        - event_df['fulfilled_units']
        - event_df['expired_units']
        + event_df['inventory_adjustment_units']
    )
    if 'process_inflow_units' in event_df:
        # Present only when the run's processes declare general flows.
        physical_terms += [event_df['process_inflow_units'], event_df['process_outflow_units']]
        physical_expected = (
            physical_expected
            + event_df['process_inflow_units']
            - event_df['process_outflow_units']
        )
    backlog_terms = [
        event_df['starting_backorders'],
        event_df['backorder_increment'],
        event_df['backorders_fulfilled'],
    ]
    backlog_expected = (
        event_df['starting_backorders']
        + event_df['backorder_increment']
        - event_df['backorders_fulfilled']
    )
    pipeline_terms = [
        event_df['starting_on_order'],
        event_df['received_units'],
        event_df['order_quantity'],
    ]
    pipeline_expected = (
        event_df['starting_on_order']
        - event_df['received_units']
        + event_df['order_quantity']
    )
    if 'supplier_shortfall_units' in event_df:
        # Present only in runs with supplier delivery outcomes.
        pipeline_terms.append(event_df['supplier_shortfall_units'])
        pipeline_expected = pipeline_expected - event_df['supplier_shortfall_units']
    checks = [
        ('physical inventory', physical_expected, event_df['ending_on_hand'], physical_terms),
        ('backlog', backlog_expected, event_df['backorders_end'], backlog_terms),
        ('pipeline', pipeline_expected, event_df['on_order_end'], pipeline_terms),
    ]
    for name, expected, actual, terms in checks:
        tolerance = _flow_balance_tolerance(*terms, actual)
        valid = (expected.astype(float) - actual.astype(float)).abs() <= tolerance
        if not valid.all():
            row = event_df.loc[~valid].iloc[0]
            raise AssertionError(
                f"{name} flow balance failed for SKU {row['unique_id']} at "
                f"period {row['period']}"
            )


# ============================================================================
# COMPARISON RESULT
# ============================================================================

class ComparisonResult:
    """Results of ``SimulationEngine.run_comparison``, one per policy.

    Behaves like a read-only mapping from label to ``SimulationResult``.

    Attributes:
        results: Dict of label to ``SimulationResult``.

    Example:
        ```python
        comparison.summary()          # one row per policy
        comparison["95% target"]      # one SimulationResult
        list(comparison)              # the labels
        ```
    """

    def __init__(self, results: Dict[str, SimulationResult]):
        self.results = results

    def summary(self) -> pd.DataFrame:
        """``SimulationResult.summary()`` of every run, one row per label.
        """
        rows = []
        for name, result in self.results.items():
            s = result.summary()
            s['policy'] = name
            rows.append(s)
        return pd.DataFrame(rows).set_index('policy')

    def __getitem__(self, key: str) -> SimulationResult:
        return self.results[key]

    def __iter__(self):
        return iter(self.results)

    def __len__(self):
        return len(self.results)

    def __repr__(self) -> str:
        return f"ComparisonResult(policies={list(self.results.keys())})"


# ============================================================================
# PERIOD EXECUTION
# ============================================================================

class _PeriodRun:
    """Engine-private executor for the periods of one ``SimulationEngine.run``.

    Each period follows the documented before-demand sequence. A state is
    either an ``ArrayState`` or an ``InventoryStateDataFrame``; every phase
    has both forms, and a phase that meets a state the array form cannot
    represent exactly continues on the DataFrame path.
    """

    def __init__(self, *, engine, demand_fn, period_offset, decision_periods,
                 allow_backorders, max_lead_time, policy_schedule, update_log,
                 use_arrays, processes=None):
        self.engine = engine
        self.demand_fn = demand_fn
        self.period_offset = period_offset
        self.decision_periods = decision_periods
        self.allow_backorders = allow_backorders
        self.max_lead_time = max_lead_time
        self.policy_schedule = policy_schedule
        self.update_log = update_log
        self.use_arrays = use_arrays
        # ProcessRunner, or None: a run without processes executes no
        # process code at all.
        self.processes: Optional[ProcessRunner] = processes
        self.demand: Optional[DemandPath] = None
        self.inventory_callbacks = False
        self.log = RunLog()
        # Order-level deliveries placed at each decision (for to_order_frame).
        self.placements: List[_Deliveries] = []
        # Suppliers with a DeliveryOutcome (empty set: no outcome code runs),
        # and the deliveries they resolved with (received, delayed) arrays.
        supply = engine._active_supply
        self.outcome_suppliers = frozenset(
            supplier.supplier_id for supplier in supply.suppliers if supplier.delivery is not None
        ) if supply is not None else frozenset()
        self.resolutions: List[tuple] = []

    def record_placements(self, book) -> None:
        if book is not None and len(book.placed):
            self.placements.append(book.placed)

    def frame(self, state, history) -> InventoryStateDataFrame:
        """DataFrame-backed state for a boundary or the DataFrame path."""
        if not isinstance(state, ArrayState):
            return state
        return state.to_inventory(
            history,
            max_lead_time=self.max_lead_time,
            allow_backorders=self.allow_backorders,
        )

    def absorb(self, frame: InventoryStateDataFrame, previous):
        """Return an array state for ``frame`` when exact, else the frame."""
        if not self.use_arrays:
            return frame
        hint = previous.schema if isinstance(previous, ArrayState) else None
        return ArrayState.from_inventory(frame, opening=False, hint=hint) or frame

    def period(self, opening, period: int, run_window: str, active_policy):
        engine = self.engine
        opening_frame = None
        if isinstance(opening, ArrayState):
            state = engine._before_demand_arrays(opening, self.demand.dates[period])
        else:
            opening_frame = copy.deepcopy(opening)
            state = engine._before_demand_transition(opening, self.demand_fn(period), period)
        processes = self.processes
        if processes is not None:
            processes.begin_period(
                demand_period=period,
                date=(
                    self.demand.dates[period] if self.demand is not None
                    else self.demand_fn(period)['date'].iloc[0]
                ),
                run_window=run_window,
            )
            state = processes.before_demand(state)

        shortfall = None
        if self.outcome_suppliers:
            # Supplier outcomes decide what of the deliveries due now arrives.
            state, shortfall = self.resolve_deliveries(state)

        # Open the demand epoch and receive due stock before the policy
        # sees state. Latest demand fields are reset, preventing look-ahead.
        review = period in self.decision_periods
        if isinstance(state, ArrayState) and state.ready(self.allow_backorders):
            state = state.advanced(
                self.period_offset,
                is_review=review,
                allow_backorders=self.allow_backorders,
            )
            sim_period = int(state.period)
        else:
            state = self.frame(state, []).advance_period(
                period_frequency=self.period_offset.freqstr,
                is_review_period=review,
            )
            sim_period = int(state.data['period'].iloc[0])
        if processes is not None:
            processes.receipt(
                state,
                state.latest['latest_received'] if isinstance(state, ArrayState)
                else state.data['latest_received'].to_numpy(dtype=float),
            )

        engine._begin_order_capture()
        if review:
            active_policy = engine._policy_for_decision(
                active_policy, self.policy_schedule, period, self.update_log,
            )
            state = self._decide(state, period, sim_period, run_window, active_policy)

        demand = self.demand
        if (
            isinstance(state, ArrayState)
            and demand.aligned(state.schema)
            and state.ready(self.allow_backorders)
            and state.date == demand.dates[period]
        ):
            state = state.fulfilled(demand.values(period), allow_backorders=self.allow_backorders)
            engine._after_demand_arrays(state)
        else:
            frame = self.frame(state, [])
            frame._history = []
            state = frame.fulfill_demand(self.demand_fn(period))
            state = engine._after_demand_transition(state, sim_period)
        if processes is not None:
            state = processes.after_demand(state)

        if isinstance(state, ArrayState) and not self.inventory_callbacks:
            adjustments = {}
        else:
            frame = self.frame(state, [])
            adjustments = engine._run_inventory_callbacks(
                frame, period=sim_period, run_window=run_window,
            )
            state = self.absorb(frame, state) if isinstance(state, ArrayState) else frame

        expired = engine._period_expired_units()
        process_flows = None
        if processes is not None:
            expired = processes.merged_expired(expired)
            if processes.general_flows:
                process_flows = (processes.inflow, processes.outflow)
        if (
            isinstance(opening, ArrayState)
            and isinstance(state, ArrayState)
            and _same_skus(opening.schema.sku_event(), state.schema.sku_event())
        ):
            self._record_arrays(
                opening, state, period, run_window, active_policy, review,
                expired, adjustments, process_flows, shortfall,
            )
        else:
            if opening_frame is None:
                opening_frame = self.frame(opening, [])
            final = self.frame(state, [])
            period_event = _build_period_event_frame(
                inventory_before=opening_frame,
                inventory_after_demand=final,
                inventory_after_orders=final,
                policy=active_policy,
                demand_period=period,
                order_event_count=engine._captured_order_event_count,
                sku_order_line_counts=engine._captured_sku_order_line_counts,
                order_line_quantity_squared_sums=(
                    engine._captured_order_line_quantity_squared_sums
                ),
            )
            period_event['run_window'] = run_window
            period_event = engine._attach_order_audit(period_event)
            period_event['expired_units'] = (
                period_event['unique_id'].map(expired).fillna(0.0)
            )
            period_event['inventory_adjustment_units'] = (
                period_event['unique_id'].map(adjustments).fillna(0.0)
            )
            if process_flows is not None:
                inflow, outflow = process_flows
                period_event['process_inflow_units'] = (
                    period_event['unique_id'].map(inflow).fillna(0.0).astype(float)
                )
                period_event['process_outflow_units'] = (
                    period_event['unique_id'].map(outflow).fillna(0.0).astype(float)
                )
            if self.outcome_suppliers:
                period_event['supplier_shortfall_units'] = (
                    period_event['unique_id'].map(shortfall or {}).fillna(0.0).astype(float)
                )
            _assert_event_flow_balance(period_event)
            # History is a completed-period snapshot, including orders and
            # accepted physical callback adjustments.
            self.log.records.append(FrameRecord(period_event, final.data.copy(deep=True)))
        return state, active_policy

    def _decide(self, state, period, sim_period, run_window, active_policy):
        """One order decision; the order is applied on arrays when exact."""
        engine = self.engine
        arrays = isinstance(state, ArrayState) and state.ready(self.allow_backorders)
        history = self.log.history_view(period)
        before = self.frame(state, history)
        before._history = history
        engine._captured_decision_positions = (
            state.inventory_positions() if arrays else engine._decision_positions(before)
        )
        orders = engine._decide_order(
            before, active_policy, sim_period,
            run_window=run_window, initial_decision=False,
        )
        supply = engine._active_supply
        # Constraints receive the live state in their context, so with
        # constraints the primitive always runs on that same object.
        if arrays and engine._active_order_constraints is None and supply is None:
            after_state = state.with_order(
                orders,
                allow_backorders=getattr(active_policy, 'allow_backorders', self.allow_backorders),
            )
            if after_state is not None:
                engine._after_order_receipt_arrays(state, after_state)
                if self.processes is not None:
                    self.processes.order_receipt(state, after_state)
                self.record_placements(after_state.book)
                return after_state
        if supply is None:
            after = engine._update_inventory_primitive(before, orders, policy=active_policy)
        else:
            after = engine._place_with_supply(before, orders, active_policy, period)
        engine._after_order_receipt(before, after)
        if self.processes is not None:
            self.processes.order_receipt(before, after)
        self.record_placements(after._open_orders)
        return self.absorb(after, state)

    def resolve_deliveries(self, state):
        """Apply supplier delivery outcomes to the deliveries due next.

        Runs before the period's receipt. Per due delivery of a supplier with
        a ``DeliveryOutcome``, the received part stays due now, a delayed
        part moves to a later pipeline slot and the rest leaves stock on
        order. Returns the new state and the undelivered units per SKU.
        """
        if isinstance(state, ArrayState):
            book, current = state.book, int(state.period)
        else:
            book, current = state._open_orders, int(state.data['period'].iloc[0])
        due_period = current + 1
        if book is None or not len(book.open):
            return state, {}
        deliveries = book.open
        candidates = np.flatnonzero(deliveries.due == due_period)
        positions = candidates[np.fromiter(
            (supplier in self.outcome_suppliers for supplier in deliveries.supplier[candidates]),
            dtype=bool, count=len(candidates),
        )]
        if not len(positions):
            return state, {}

        engine = self.engine
        supply = engine._active_supply
        frame = self.frame(state, [])
        sku_column = frame.sku_column
        inventory = frame.get_dataframe()
        date = pd.Timestamp(inventory['date'].iloc[0]) + self.period_offset
        received = np.zeros(len(positions))
        delayed = np.zeros(len(positions))
        delay = np.zeros(len(positions), dtype=np.int64)
        suppliers = deliveries.supplier[positions]
        for supplier_id in supply.supplier_ids:
            rows = np.flatnonzero(np.fromiter(
                (value == supplier_id for value in suppliers), dtype=bool, count=len(suppliers),
            ))
            if not len(rows) or supplier_id not in self.outcome_suppliers:
                continue
            chosen = positions[rows]
            due = pd.DataFrame({
                'order_id': deliveries.order_id[chosen],
                sku_column: _object_series(deliveries.sku[chosen]),
                'supplier_id': _object_series(deliveries.supplier[chosen]),
                'source': _object_series(deliveries.source[chosen]),
                'order_period': deliveries.order_period[chosen],
                'scheduled_due_period': deliveries.scheduled[chosen],
                'due_period': deliveries.due[chosen],
                'quantity': deliveries.quantity[chosen],
            })
            context = DeliveryContext(
                inventory=inventory.copy(deep=True),
                sku_column=sku_column,
                period=due_period,
                date=date,
                supplier_id=supplier_id,
                rng=supply._outcome_rngs[supplier_id],
            )
            received[rows], delayed[rows], delay[rows] = supply._resolve(
                supplier_id, due, context,
            )
        later = delayed > 0
        if later.any():
            too_late = delay[later] > frame.max_lead_time - 1
            if too_late.any():
                raise ValueError(
                    f"a DeliveryOutcome delayed a delivery by {int(delay[later][too_late].max())} "
                    f"periods at period {due_period}, beyond the pipeline window; "
                    f"increase inventory max_lead_time (now {frame.max_lead_time})"
                )
            engine._warn_supply_timing()

        quantity = deliveries.quantity[positions]
        new_book, rescheduled = book.resolved(positions, received, delayed, delay)
        sku_index = pd.Index(inventory[sku_column].tolist(), dtype=object)
        rows = sku_index.get_indexer(pd.Index(deliveries.sku[positions], dtype=object))
        pipelines = frame._stacked_pipelines().copy()
        removed = np.zeros(len(sku_index))
        np.add.at(removed, rows, quantity - received)
        undelivered = np.zeros(len(sku_index))
        np.add.at(undelivered, rows, quantity - received - delayed)
        affected = removed > 0
        still_due = np.zeros(len(sku_index))
        keep = new_book.open.due == due_period
        np.add.at(
            still_due,
            sku_index.get_indexer(pd.Index(new_book.open.sku[keep], dtype=object)),
            new_book.open.quantity[keep],
        )
        # Rounding never leaves a negative or phantom slot.
        pipelines[affected, 0] = np.where(
            still_due[affected] > 0,
            np.maximum(pipelines[affected, 0] - removed[affected], 0.0),
            0.0,
        )
        np.add.at(pipelines, (rows[later], delay[later]), delayed[later])
        inventory['in_transit'] = pd.Series(
            [row.copy() for row in pipelines], index=inventory.index, dtype=object,
        )
        resolved_frame = InventoryStateDataFrame(
            inventory,
            sku_column=sku_column,
            max_lead_time=frame.max_lead_time,
            allow_backorders=frame.allow_backorders,
            _history=frame._history,
            _open_orders=new_book,
        )
        self.resolutions.append((deliveries.select(positions), received, delayed))
        if len(rescheduled):
            self.placements.append(rescheduled)
        shortfall = {
            sku: float(value)
            for sku, value in zip(sku_index, undelivered) if value > 0
        }
        return self.absorb(resolved_frame, state), shortfall

    def _record_arrays(self, opening, state, period, run_window, active_policy,
                       review, expired, adjustments, process_flows=None,
                       shortfall=None) -> None:
        engine = self.engine
        uid = opening.schema.sku_event()
        audit = None
        if review:
            audit = self._decision_audit(uid, state.latest['latest_order'])
            uid = audit['unique_id']
        record = FastRecord(
            demand_period=period,
            run_window=run_window,
            policy_name=active_policy.policy_name,
            allow_backorders=active_policy.allow_backorders,
            start=opening,
            final=state,
            order_event_count=engine._captured_order_event_count,
            line_counts=engine._captured_sku_order_line_counts,
            squared_sums=engine._captured_order_line_quantity_squared_sums,
            audit=audit,
            expired=uid.map(expired).fillna(0.0).to_numpy() if expired else None,
            adjustments=uid.map(adjustments).fillna(0.0).to_numpy() if adjustments else None,
            process_columns=process_flows is not None,
            process_in=(
                uid.map(process_flows[0]).fillna(0.0).to_numpy(dtype=float)
                if process_flows is not None and process_flows[0] else None
            ),
            process_out=(
                uid.map(process_flows[1]).fillna(0.0).to_numpy(dtype=float)
                if process_flows is not None and process_flows[1] else None
            ),
            supply_columns=bool(self.outcome_suppliers),
            shortfall=(
                uid.map(shortfall).fillna(0.0).to_numpy(dtype=float) if shortfall else None
            ),
        )
        assert_flow_balance(record)
        self.log.records.append(record)

    def _decision_audit(self, uid: pd.Series, order_quantity: np.ndarray) -> dict:
        """Order-audit columns for one decision period, keyed by column name.

        Equal to ``_attach_order_audit`` on the full event frame. The common
        case (one decision, no constraints, trail in ledger SKU order with
        plain dtypes) is computed directly: grouping unique SKUs and summing
        one value each is ``value + 0.0`` for floats and the value for ints.
        Otherwise the pandas merges run on a two-column frame.
        """
        engine = self.engine
        trails = engine._captured_callback_order_audits
        if not engine._captured_order_audits and len(trails) == 1:
            trail = trails[0]
            ids = trail['unique_id']
            if ids.dtype == uid.dtype and ids.equals(uid):
                audit = {
                    'unique_id': uid,
                    'decision_inventory_position': uid.map(
                        engine._captured_decision_positions
                    ),
                }
                for name in (
                    'requested_order_quantity',
                    'callback_adjusted_order_quantity',
                    'callback_adjustment_units',
                ):
                    values = trail[name]
                    if values.dtype == np.dtype('float64'):
                        audit[name] = values.to_numpy() + 0.0
                    elif values.dtype == np.dtype('int64'):
                        audit[name] = values.to_numpy()
                    else:
                        audit = None
                        break
                if audit is not None:
                    for name in AUDIT_COLUMNS[4:]:
                        audit[name] = audit_default(name, order_quantity)
                    return audit
        frame = engine._attach_order_audit(pd.DataFrame({
            'unique_id': uid,
            'order_quantity': order_quantity,
        }))
        return {name: frame[name] for name in ('unique_id',) + AUDIT_COLUMNS}


def _same_skus(left: pd.Series, right: pd.Series) -> bool:
    """Whether two states list the same SKUs, in order, with one dtype."""
    return left is right or (left.dtype == right.dtype and left.equals(right))


# ============================================================================
# SIMULATION ENGINE
# ============================================================================

class SimulationEngine:
    """Run inventory simulations: the clock and the only place stock changes.

    Each period the engine receives due deliveries, lets the policy decide on
    scheduled periods (then applies callbacks, ordering constraints and the
    supply model), serves demand, applies inventory processes and after-demand
    callbacks, and records one checked ledger row per SKU. Everything that can be
    validated is checked before the first period.

    Example:
        ```python
        result = SimulationEngine().run(
            policy=policy, demand_source=demand, inventory=inventory,
            n_periods=56, period_frequency="D",
            warmup_periods=0, scoring_periods=56, settlement_periods=0,
            order_during_settlement=False,
            demand_source_name="tea_shop", random_seed=3,
        )
        ```
    """

    # Processes an engine subclass contributes to every run (ShelfLifeEngine).
    _engine_processes: tuple = ()
    _process_runner: Optional[ProcessRunner] = None

    def __init__(self, verbose: int = 0):
        """Create an engine.

        Args:
            verbose: 0 silent (default), 1 start/end/milestones, 2 one line per
                period.
        """
        self.verbose = verbose

    def _log(self, msg: str, level: int = 1):
        """Print msg if self.verbose >= level."""
        if self.verbose >= level:
            print(msg)

    def run(
        self,
        policy: BasePolicy,
        demand_source: Union[pd.DataFrame, Callable],
        inventory: InventoryStateDataFrame,
        n_periods: int,
        *,
        period_frequency: str,
        initial_decision: str = "none",
        warmup_periods: int,
        scoring_periods: int,
        settlement_periods: int,
        order_during_settlement: bool,
        demand_source_name: str,
        random_seed: Optional[int],
        policy_schedule: Optional[Mapping[int, BasePolicy]] = None,
        order_constraints: Optional[OrderingConstraints] = None,
        callbacks: Optional[Sequence[SimulationCallback]] = None,
        supply: Optional[SupplyModel] = None,
        processes: Optional[Sequence[InventoryProcess]] = None,
    ) -> SimulationResult:
        """Simulate a policy against a demand path.

        The engine copies ``inventory`` and ``policy``, validates the whole
        experiment, then plays ``n_periods`` demand periods: receive, decide (on
        scheduled periods), meet demand, record. Your input objects are not changed.

        Args:
            policy: A fitted policy.
            demand_source: A DataFrame with ``unique_id``, ``period``, ``date`` and
                ``y`` covering every SKU and period, or a callable
                ``period -> DataFrame`` (called once per period before the run).
            inventory: The opening ``InventoryStateDataFrame``. Its
                ``max_lead_time`` must cover the policy's lead time and the supply
                model's longest delivery.
            n_periods: Number of demand periods.
            period_frequency: Length of one period, a pandas frequency such as
                ``"D"``. Period ``p`` is dated opening date + ``(p + 1)`` periods.
            initial_decision: Kept for compatibility; only ``"none"``. The first
                decision comes from the policy's schedule.
            warmup_periods: Leading periods excluded from scoring.
            scoring_periods: Periods that metrics describe by default (>= 1).
            settlement_periods: Trailing periods excluded from scoring. The three
                windows must add up to ``n_periods``.
            order_during_settlement: Whether the policy may order in settlement.
            demand_source_name: A label for the demand, stored in the manifest.
            random_seed: The seed behind the demand, or ``None`` if there is none.
            policy_schedule: ``{decision_period: fitted_policy}`` with refitted
                policies for later decisions. Each must match ``policy``'s class and
                configuration, with a forecast origin equal to the decision's
                information date.
            order_constraints: An ``OrderingConstraints`` sequence.
            callbacks: ``SimulationCallback`` objects, applied in order.
            supply: A ``SupplyModel`` for suppliers, random lead times and split or
                unreliable deliveries. ``None``: one delivery ``lead_time`` periods
                after each order.
            processes: ``InventoryProcess`` objects (for example
                ``ShelfLife``), applied in order.

        Returns:
            A ``SimulationResult``.

        Raises:
            ValueError: If an input is incomplete or inconsistent, for example an
                unfitted policy, a demand calendar with gaps, a target for the wrong
                window, or windows that do not add up.
        """
        if not isinstance(n_periods, int) or isinstance(n_periods, bool) or n_periods < 0:
            raise ValueError("n_periods must be a non-negative integer")
        window_lengths = {
            'warmup_periods': warmup_periods,
            'scoring_periods': scoring_periods,
            'settlement_periods': settlement_periods,
        }
        for name, value in window_lengths.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be an integer >= 0")
        if scoring_periods < 1:
            raise ValueError("scoring_periods must be an integer >= 1")
        if sum(window_lengths.values()) != n_periods:
            raise ValueError(
                "warmup_periods + scoring_periods + settlement_periods must equal n_periods"
            )
        if not isinstance(order_during_settlement, bool):
            raise ValueError("order_during_settlement must be boolean")
        if not isinstance(demand_source_name, str) or not demand_source_name.strip():
            raise ValueError("demand_source_name must be a non-empty string")
        if random_seed is not None and (
            not isinstance(random_seed, int) or isinstance(random_seed, bool)
        ):
            raise ValueError("random_seed must be an integer or explicit None")
        if order_constraints is not None and not isinstance(order_constraints, OrderingConstraints):
            raise TypeError("order_constraints must be an OrderingConstraints instance")
        if supply is not None and not isinstance(supply, SupplyModel):
            raise TypeError("supply must be a SupplyModel instance or None")
        # Engine-owned processes (ShelfLifeEngine) come first; they are
        # prepared by the subclass and are not listed as user processes.
        engine_processes = list(self._engine_processes)
        user_processes = prepare_processes(
            processes, reserved=[process.name for process in engine_processes],
        )
        all_processes = engine_processes + user_processes
        if all_processes and self._lifecycle_hooks_overridden():
            raise ValueError(
                "processes cannot be combined with an engine subclass that "
                "overrides the private lifecycle hooks"
            )
        supply_manifest = supply.to_manifest() if supply is not None else None
        constraint_manifest = (
            order_constraints.to_manifest() if order_constraints is not None else None
        )
        if not isinstance(policy, BasePolicy) or not policy.fitted_:
            raise ValueError("policy must be fitted before simulation")
        if not isinstance(inventory, InventoryStateDataFrame):
            raise TypeError("inventory must be an InventoryStateDataFrame")
        period_offset = _require_forward_frequency(
            period_frequency,
            "period_frequency",
        )
        if initial_decision != "none":
            raise ValueError(
                "initial_decision must be 'none': decisions now occur before demand; "
                "use OneTimeSchedule(0) or PeriodicSchedule(..., start=0), "
                "and explicit opening pipeline for pre-run orders"
            )
        schedule_manifest = policy.schedule.to_manifest()
        if not isinstance(schedule_manifest, dict):
            raise TypeError("decision schedule manifest must be a dictionary")
        json.dumps(schedule_manifest, allow_nan=False)
        decision_periods = set()
        for period in range(n_periods):
            enabled = policy.schedule.should_decide(period)
            if not isinstance(enabled, bool):
                raise ValueError("schedule.should_decide must return bool")
            if enabled and (period < warmup_periods + scoring_periods or order_during_settlement):
                decision_periods.add(period)
        # A run owns its state. Caller state and pre-run history remain
        # untouched, while result.history contains snapshots from this run.
        inventory = copy.deepcopy(inventory)
        inventory.clear_history()
        inventory.allow_backorders = policy.allow_backorders
        inventory._validate_ready_state()
        if inventory.max_lead_time < policy.lead_time:
            raise ValueError("inventory max_lead_time must cover policy lead_time")
        if supply is not None and inventory.max_lead_time < supply.max_delivery_offset:
            raise ValueError(
                "inventory max_lead_time must cover the supply model's longest "
                f"delivery offset ({supply.max_delivery_offset})"
            )
        if supply is not None:
            supply._check_outcome_timing()
        for process in user_processes:
            # Engine-private opening preparation (ShelfLife: validate and
            # seed opening lots, write off stock expired at the opening).
            prepare_opening = getattr(process, "_prepare_opening", None)
            if callable(prepare_opening):
                prepare_opening(inventory)
        process_manifests = (
            process_manifest(all_processes) if user_processes else None
        )
        # The run tracks stock on order at order level. A pipeline given only
        # as in_transit arrays is attributed to opening orders; values are
        # unchanged.
        if inventory._open_orders is None:
            inventory._open_orders = inventory._order_book()
        opening_book = inventory._open_orders
        opening_inventory_fingerprint = self._inventory_fingerprint(inventory)
        if opening_book.declared:
            # Declared opening orders carry supplier and order-period evidence
            # that in_transit alone does not.
            declared_orders = inventory.open_orders()
            declared_orders['supplier_id'] = declared_orders['supplier_id'].map(repr)
            opening_inventory_fingerprint['open_orders'] = {
                'sha256': self._dataframe_checksum(declared_orders, sort_columns=['order_id']),
                'rows': len(declared_orders),
            }
        opening_period = int(inventory.get_dataframe()['period'].iloc[0])
        opening_date = pd.Timestamp(inventory.get_dataframe()['date'].iloc[0])
        self._active_callbacks, callback_manifest = self._prepare_callbacks(
            callbacks,
            opening_period=opening_period,
            opening_date=opening_date,
            period_offset=period_offset,
        )
        self._callback_audit_rows = []
        self._validate_policy_information_origin(
            policy,
            latest_allowed_origin=opening_date + min(decision_periods, default=0) * period_offset,
            exact=False,
            expected_frequency=period_offset,
        )
        policy_schedule = self._validate_policy_schedule(
            policy,
            policy_schedule,
            opening_date=opening_date,
            period_offset=period_offset,
            decision_periods=decision_periods,
        )
        # Validate target coverage at every actual opportunity before callbacks
        # reset or demand callables are materialized.
        coverage_policy = policy
        for period in sorted(decision_periods):
            coverage_policy = policy_schedule.get(period, coverage_policy)
            if not callable(getattr(coverage_policy, "validate_decision_window", None)):
                continue
            validate_window = getattr(copy.deepcopy(coverage_policy), "validate_decision_window", None)
            if callable(validate_window):
                validate_window(period, opening_date + period * period_offset, period_offset)

        # Deferred import to avoid circular dependency (core ↔ utils)
        from stockcast.utils.inventory_operations import update_inventory_with_orders
        self._update_inventory_primitive = update_inventory_with_orders
        self._update_inventory = self._tracked_inventory_update

        demand_source_type = "dataframe" if isinstance(demand_source, pd.DataFrame) else "callable"
        demand_data = self._materialize_demand_source(demand_source, n_periods)
        demand_data = self._validate_demand_calendar(
            demand_data,
            inventory,
            n_periods,
            period_offset,
        )
        for snapshot in [policy, *policy_schedule.values()]:
            if not callable(getattr(snapshot, "validate_demand_window", None)):
                continue
            validate_window = getattr(copy.deepcopy(snapshot), "validate_demand_window", None)
            if callable(validate_window):
                validate_window(demand_data.copy(deep=True), n_periods)
        demand_fn = self._resolve_demand_source(demand_data)
        self._active_supply = copy.deepcopy(supply)
        self._supply_timing_warned = False
        self._supply_policy_lead_time = policy.lead_time
        if self._active_supply is not None:
            self._active_supply._prepare(
                inventory.data[inventory.sku_column].tolist(), n_periods,
            )
        self._active_order_constraints = copy.deepcopy(order_constraints)
        if self._active_order_constraints is not None:
            self._active_order_constraints.reset(ConstraintContext(
                inventory=inventory,
                policy=policy,
                decision_period=opening_period,
            ))

        active_policy = copy.deepcopy(policy)
        self._reset_callbacks(
            inventory=inventory,
            opening_period=opening_period,
            opening_date=opening_date,
        )
        self._process_runner = None
        if all_processes:
            runner = ProcessRunner(all_processes, opening_period=opening_period)
            runner.reset(inventory, opening_period=opening_period, opening_date=opening_date)
            self._process_runner = runner
        update_log = []
        n_skus = len(inventory.get_dataframe())
        self._log(
            f"[SimEngine] Starting: {n_periods} periods, "
            f"policy={policy.policy_name}, {n_skus} SKUs"
        )
        inventory, history, event_frame, placements = self._simulate_periods(
            inventory=inventory,
            demand_data=demand_data,
            demand_fn=demand_fn,
            n_periods=n_periods,
            period_offset=period_offset,
            decision_periods=decision_periods,
            warmup_periods=warmup_periods,
            scoring_periods=scoring_periods,
            active_policy=active_policy,
            policy_schedule=policy_schedule,
            update_log=update_log,
        )

        resolved_commit = self._repository_commit()
        run_settings = {
            'period_frequency': period_offset.freqstr,
            'initial_decision': initial_decision,
            'timing_convention': 'receive_decide_receive_zero_lead_demand',
            'decision_schedule': copy.deepcopy(schedule_manifest),
            'decision_period_convention': 'zero_based_demand_period',
            'input_period_convention': 'zero_based',
            'event_period_convention': 'opening_period_plus_one',
            'warmup_periods': warmup_periods,
            'scoring_periods': scoring_periods,
            'settlement_periods': settlement_periods,
            'order_during_settlement': order_during_settlement,
            'policy_update_periods': sorted(policy_schedule),
            'policy_update_log': update_log,
            'order_constraints': copy.deepcopy(constraint_manifest),
            'callbacks': copy.deepcopy(callback_manifest),
        }
        if supply_manifest is not None:
            run_settings['supply'] = copy.deepcopy(supply_manifest)
        if process_manifests is not None:
            run_settings['processes'] = copy.deepcopy(process_manifests)
        run_manifest = self._build_run_manifest(
            demand_data=demand_data,
            demand_source_name=demand_source_name.strip(),
            demand_source_type=demand_source_type,
            random_seed=random_seed,
            source_commit=resolved_commit,
            policy=policy,
            sku_column=inventory.sku_column,
            run_settings=run_settings,
            opening_inventory=opening_inventory_fingerprint,
        )
        result = SimulationResult(
            history=history,
            inventory=inventory,
            n_periods=n_periods,
            policy_name=policy.policy_name,
            event_frame=event_frame,
            run_settings=run_settings,
            run_manifest=run_manifest,
            callback_audit=pd.DataFrame(self._callback_audit_rows),
            order_frame=self._order_frame(
                inventory,
                opening_book=opening_book,
                placements=placements,
                opening_period=opening_period,
                opening_date=opening_date,
                period_offset=period_offset,
                resolutions=self._run_resolutions,
                outcome_suppliers=self._run_outcome_suppliers,
            ),
            process_flows=(
                self._process_runner.flow_frame()
                if self._process_runner is not None else None
            ),
        )

        if self.verbose >= 1:
            s = result.summary()
            self._log(
                f"[SimEngine] Complete. fill_rate={s['fill_rate']:.3f}, "
                f"demand_period_service_level={s['demand_period_service_level']:.3f}, "
                "mean_ending_on_hand_per_sku_period="
                f"{s['mean_ending_on_hand_per_sku_period']:.1f}, "
                f"stockout_periods={s['stockout_periods']}"
            )

        return result

    # ---- Period execution ----

    _LIFECYCLE_HOOKS = (
        "_before_demand_transition", "_after_order_receipt", "_after_demand_transition",
        "_before_demand_arrays", "_after_order_receipt_arrays", "_after_demand_arrays",
        "_period_expired_units", "_apply_lot_adjustment", "_validate_lot_adjustment",
        "_after_inventory_adjustment_batch",
    )

    def _lifecycle_hooks_overridden(self) -> bool:
        """Whether a subclass replaces any private lifecycle hook."""
        engine = type(self)
        return any(
            getattr(engine, hook) is not getattr(SimulationEngine, hook)
            for hook in self._LIFECYCLE_HOOKS
        )

    def _array_hooks_supported(self) -> bool:
        """Whether every overridden lifecycle hook also has an array form."""
        engine = type(self)
        for frame_hook, array_hook in (
            ("_before_demand_transition", "_before_demand_arrays"),
            ("_after_order_receipt", "_after_order_receipt_arrays"),
            ("_after_demand_transition", "_after_demand_arrays"),
        ):
            if (
                getattr(engine, frame_hook) is not getattr(SimulationEngine, frame_hook)
                and getattr(engine, array_hook) is getattr(SimulationEngine, array_hook)
            ):
                return False
        return True

    def _simulate_periods(
        self,
        *,
        inventory: InventoryStateDataFrame,
        demand_data: pd.DataFrame,
        demand_fn: Callable,
        n_periods: int,
        period_offset,
        decision_periods: set,
        warmup_periods: int,
        scoring_periods: int,
        active_policy: BasePolicy,
        policy_schedule: Mapping[int, BasePolicy],
        update_log: list,
    ) -> tuple:
        """Execute every period; return final state, history, event ledger
        and the order-level deliveries placed at each decision.

        Live state is held as NumPy arrays (``ArrayState``). Policies,
        constraints and callbacks receive DataFrame-backed states built at
        their boundary, and the history and event ledger are assembled once
        from the run log. A state without an exact array form stays a
        DataFrame, and that period runs on the pandas path, so results are
        identical either way.
        """
        run = _PeriodRun(
            engine=self,
            demand_fn=demand_fn,
            period_offset=period_offset,
            decision_periods=decision_periods,
            allow_backorders=inventory.allow_backorders,
            max_lead_time=inventory.max_lead_time,
            policy_schedule=policy_schedule,
            update_log=update_log,
            use_arrays=self._array_hooks_supported(),
            processes=self._process_runner,
        )
        current = inventory
        if run.use_arrays:
            run.demand = DemandPath(
                demand_data,
                inventory.sku_column,
                pd.Index(inventory.data[inventory.sku_column].reset_index(drop=True)),
                n_periods,
            )
            current = ArrayState.from_inventory(inventory, opening=True) or inventory
        run.inventory_callbacks = any(
            type(callback).on_after_demand is not SimulationCallback.on_after_demand
            for callback in self._active_callbacks
        )
        milestones = {n_periods // 4, n_periods // 2, 3 * n_periods // 4}

        for period in range(n_periods):
            run_window = self._run_window(period, warmup_periods, scoring_periods)
            current, active_policy = run.period(current, period, run_window, active_policy)

            if period in milestones:
                pct = int(100 * (period + 1) / n_periods)
                self._log(f"[SimEngine] Period {period + 1}/{n_periods} ({pct}%)")
            if self.verbose >= 2:
                self._log_period_detail(current)

        final = run.frame(current, run.log.history_view(n_periods))
        final._history = run.log.history_view(n_periods)
        self._run_resolutions = run.resolutions
        self._run_outcome_suppliers = run.outcome_suppliers
        return final, run.log.history(), run.log.event_frame(), run.placements

    def _log_period_detail(self, state) -> None:
        if isinstance(state, ArrayState):
            total_demand = pd.Series(state.latest['latest_incoming_demand']).sum()
            total_orders = pd.Series(state.latest['latest_order']).sum()
            total_on_hand = pd.Series(state.on_hand).sum()
            sim_period = int(state.period)
        else:
            inv_df = state.get_dataframe()
            total_demand = inv_df['latest_incoming_demand'].sum()
            total_orders = inv_df['latest_order'].sum()
            total_on_hand = inv_df['on_hand'].sum()
            sim_period = int(inv_df['period'].iloc[0])
        stockout_flag = 'YES' if state.has_stockout else 'no'
        self._log(
            f"  Period {sim_period}: demand={total_demand:.0f}, "
            f"orders={total_orders:.0f}, on_hand={total_on_hand:.0f}, "
            f"stockout={stockout_flag}",
            level=2,
        )

    def _before_demand_arrays(self, state: ArrayState, current_date: pd.Timestamp) -> ArrayState:
        """Array form of ``_before_demand_transition``."""
        return state

    def _after_order_receipt_arrays(self, before: ArrayState, after: ArrayState) -> None:
        """Array form of ``_after_order_receipt``."""

    def _after_demand_arrays(self, state: ArrayState) -> None:
        """Array form of ``_after_demand_transition``."""

    def _prepare_callbacks(
        self,
        callbacks,
        *,
        opening_period: int,
        opening_date: pd.Timestamp,
        period_offset,
    ) -> tuple[list[SimulationCallback], list[dict]]:
        if callbacks is None:
            prepared = []
        elif isinstance(callbacks, Sequence) and not isinstance(callbacks, (str, bytes)):
            prepared = list(callbacks)
        else:
            raise TypeError("callbacks must be an ordered sequence of SimulationCallback objects")
        invalid = [type(value).__name__ for value in prepared if not isinstance(value, SimulationCallback)]
        if invalid:
            raise TypeError(
                "callbacks must contain only SimulationCallback objects; "
                f"got {invalid[0]}"
            )
        manifests = []
        for position, callback in enumerate(prepared):
            try:
                schedule = getattr(callback, "schedule", None)
                if (
                    isinstance(schedule, pd.DataFrame)
                    and "period" in schedule
                    and "date" in schedule
                ):
                    expected_dates = schedule["period"].map(
                        lambda value: opening_date
                        + (int(value) - opening_period) * period_offset
                    )
                    if not pd.to_datetime(schedule["date"]).eq(expected_dates).all():
                        raise ValueError(
                            "callback schedule period and date coordinates must identify "
                            "the same simulation point"
                        )
                config = callback.get_config()
                if not isinstance(config, dict):
                    raise TypeError("get_config() must return a dictionary")
                json.dumps(config, allow_nan=False)
            except Exception as exc:
                raise self._callback_error(
                    callback, position, "preflight", opening_period, opening_date, exc
                ) from exc
            enabled = []
            if type(callback).on_after_demand is not SimulationCallback.on_after_demand:
                enabled.append("on_after_demand")
            if type(callback).on_after_prediction is not SimulationCallback.on_after_prediction:
                enabled.append("on_after_prediction")
            manifests.append({
                "position": position,
                "module": type(callback).__module__,
                "class": type(callback).__name__,
                "enabled_phases": enabled,
                "config": copy.deepcopy(config),
            })
        return prepared, manifests

    def _reset_callbacks(
        self,
        *,
        inventory: InventoryStateDataFrame,
        opening_period: int,
        opening_date: pd.Timestamp,
    ) -> None:
        """Reset caller callbacks after non-mutating run preflight succeeds."""
        reset_context = self._callback_context(
            inventory,
            period=opening_period,
            run_window="opening",
            phase="reset",
            initial_decision=False,
            date=opening_date,
        )
        for position, callback in enumerate(self._active_callbacks):
            try:
                callback.reset(reset_context)
            except Exception as exc:
                raise self._callback_error(
                    callback, position, "reset", opening_period, opening_date, exc
                ) from exc

    @staticmethod
    def _callback_error(callback, position, phase, period, date, cause) -> CallbackError:
        return CallbackError(
            f"callback[{position}] {type(callback).__module__}.{type(callback).__name__} "
            f"failed during {phase} at period {period}, date {pd.Timestamp(date)}: {cause}"
        )

    @staticmethod
    def _callback_context(
        inventory,
        *,
        period,
        run_window,
        phase,
        initial_decision,
        date=None,
    ) -> CallbackContext:
        state = inventory.get_dataframe()
        current_date = pd.Timestamp(state["date"].iloc[0]) if date is None else pd.Timestamp(date)
        return CallbackContext(
            inventory=state,
            sku_column=inventory.sku_column,
            period=int(period),
            date=current_date,
            run_window=run_window,
            phase=phase,
            initial_decision=initial_decision,
        )

    def _before_demand_transition(self, inventory, demand_df, period):
        """Private engine-owned work before the standard demand transition."""
        return inventory

    def _after_order_receipt(self, before, after):
        """Private receipt integration for engine-owned lot accounting."""

    def _after_demand_transition(self, inventory, period):
        """Private engine-owned work after demand and before callbacks."""
        return inventory

    def _period_expired_units(self) -> Mapping[object, float]:
        return {}

    def _apply_lot_adjustment(
        self, unique_id, quantity_delta, received_date, current_date
    ) -> str:
        """Apply subclass-owned lot accounting and return deterministic evidence."""
        return ""

    def _validate_lot_adjustment(
        self, unique_id, quantity_delta, received_date, current_date
    ) -> None:
        if quantity_delta <= 0 and not pd.isna(received_date):
            raise ValueError("received_date is supported only for positive inventory additions")
        if (
            quantity_delta > 0
            and not pd.isna(received_date)
            and pd.Timestamp(received_date) > pd.Timestamp(current_date)
        ):
            raise ValueError("inventory adjustment.received_date cannot be in the future")

    def _after_inventory_adjustment_batch(self, inventory) -> None:
        """Validate subclass-owned physical-state mirrors after a full batch."""
        if self._process_runner is not None:
            self._process_runner.check(inventory, "callback_adjustment")

    @staticmethod
    def _validated_reason_source(frame: pd.DataFrame, label: str) -> pd.DataFrame:
        for column in ("reason", "source"):
            if (
                frame[column].isna().any()
                or not frame[column].map(lambda value: isinstance(value, str)).all()
                or frame[column].str.strip().eq("").any()
            ):
                raise ValueError(f"{label}.{column} must contain nonblank strings")
        return frame

    def _run_inventory_callbacks(self, inventory, *, period, run_window) -> dict:
        aggregate = {}
        for position, callback in enumerate(self._active_callbacks):
            context = self._callback_context(
                inventory,
                period=period,
                run_window=run_window,
                phase="on_after_demand",
                initial_decision=False,
            )
            try:
                result = callback.on_after_demand(context)
                if result is None:
                    continue
                if not isinstance(result, InventoryAdjustmentResult):
                    raise TypeError(
                        "on_after_demand must return InventoryAdjustmentResult or None"
                    )
                frame = self._validate_inventory_adjustment_result(result, inventory)
                audit = self._apply_inventory_adjustment_batch(
                    inventory, frame, callback, position, context
                )
            except Exception as exc:
                if isinstance(exc, CallbackError):
                    raise
                raise self._callback_error(
                    callback, position, "on_after_demand", period, context.date, exc
                ) from exc
            self._callback_audit_rows.extend(audit)
            for row in frame.itertuples(index=False):
                aggregate[row.unique_id] = aggregate.get(row.unique_id, 0.0) + float(
                    row.quantity_delta
                )
        if inventory._history:
            inventory._history[-1] = inventory.data.copy()
        return aggregate

    def _validate_inventory_adjustment_result(self, result, inventory) -> pd.DataFrame:
        frame = result.get_dataframe()
        required = {"unique_id", "quantity_delta", "reason", "source"}
        allowed = required | {"received_date"}
        missing = sorted(required - set(frame.columns))
        extra = sorted(set(frame.columns) - allowed)
        if missing:
            raise ValueError(f"inventory adjustment is missing required columns: {missing}")
        if extra:
            raise ValueError(f"inventory adjustment contains unsupported columns: {extra}")
        if frame.empty:
            return frame.copy()
        actual = _require_identifiers(frame, "unique_id", "inventory adjustment", unique=True)
        state = inventory.get_dataframe()
        expected = _require_identifiers(
            state, inventory.sku_column, "inventory_state", unique=True
        )
        unknown = actual - expected
        if unknown:
            raise ValueError(f"inventory adjustment contains unknown SKUs: {_identifier_sample(unknown)}")
        quantity = pd.to_numeric(frame["quantity_delta"], errors="coerce")
        if quantity.isna().any() or not np.isfinite(quantity.to_numpy(dtype=float)).all():
            raise ValueError("inventory adjustment.quantity_delta must contain finite numbers")
        prepared = self._validated_reason_source(frame.copy(), "inventory adjustment")
        prepared["quantity_delta"] = quantity.astype(float)
        if "received_date" in prepared:
            raw_dates = prepared["received_date"]
            dates = pd.to_datetime(raw_dates, errors="coerce")
            if (raw_dates.notna() & dates.isna()).any():
                raise ValueError(
                    "inventory adjustment.received_date must contain valid timestamps or missing values"
                )
            prepared["received_date"] = dates
        return prepared

    def _apply_inventory_adjustment_batch(
        self, inventory, frame, callback, position, context
    ) -> list[dict]:
        state = inventory.data
        indexed = state.set_index(inventory.sku_column)
        runner = self._process_runner
        process_context = (
            runner.context(inventory, "callback_adjustment", with_period_flows=True)
            if runner is not None else None
        )
        # Validate the complete batch before applying any row.
        for row in frame.itertuples(index=False):
            before = float(indexed.loc[row.unique_id, "on_hand"])
            backlog = float(indexed.loc[row.unique_id, "backorders"])
            delta = float(row.quantity_delta)
            if delta < 0 and -delta > before + 1e-9:
                raise ValueError(
                    f"inventory adjustment removal exceeds on_hand for SKU {row.unique_id}"
                )
            if delta > 0 and backlog > 1e-9:
                raise ValueError(
                    f"positive inventory adjustment is not supported while SKU {row.unique_id} has backlog"
                )
            received_date = getattr(row, "received_date", pd.NaT)
            self._validate_lot_adjustment(
                row.unique_id, delta, received_date, context.date
            )
            if runner is not None:
                runner.validate_change(
                    self._callback_stock_change(callback, row.unique_id, delta, received_date),
                    process_context,
                )
        audit = []
        for row in frame.itertuples(index=False):
            mask = state[inventory.sku_column] == row.unique_id
            before = float(state.loc[mask, "on_hand"].iloc[0])
            delta = float(row.quantity_delta)
            received_date = getattr(row, "received_date", pd.NaT)
            lot_evidence = self._apply_lot_adjustment(
                row.unique_id, delta, received_date, context.date
            )
            if runner is not None:
                process_evidence = runner.notify_change(
                    self._callback_stock_change(callback, row.unique_id, delta, received_date),
                    process_context,
                )
                if process_evidence:
                    lot_evidence = (
                        process_evidence if not lot_evidence
                        else json.dumps(
                            {"engine": lot_evidence, "processes": process_evidence},
                            sort_keys=True, separators=(",", ":"),
                        )
                    )
            after = before + delta
            state.loc[mask, "on_hand"] = after
            audit.append(self._audit_row(
                callback, position, context, row.unique_id,
                before=before, after=after, delta=delta,
                order_quantity=np.nan, reason=row.reason, source=row.source,
                received_date=received_date, lot_evidence=lot_evidence,
            ))
        inventory._validate_ready_state()
        self._after_inventory_adjustment_batch(inventory)
        return audit

    @staticmethod
    def _callback_stock_change(callback, unique_id, delta, received_date) -> StockChange:
        return StockChange(
            unique_id=unique_id,
            quantity=float(delta),
            received_date=pd.Timestamp(received_date) if not pd.isna(received_date) else pd.NaT,
            origin="callback",
            source=type(callback).__name__,
        )

    def _execute_order_decision(
        self, inventory, policy, period, *, run_window, initial_decision
    ):
        self._captured_decision_positions = self._decision_positions(inventory)
        orders = self._decide_order(
            inventory, policy, period,
            run_window=run_window, initial_decision=initial_decision,
        )
        return self._update_inventory_primitive(inventory, orders, policy=policy)

    @staticmethod
    def _decision_positions(inventory) -> dict:
        return inventory.inventory_position().set_index(
            inventory.sku_column
        )["inventory_position"].to_dict()

    def _decide_order(
        self, inventory, policy, period, *, run_window, initial_decision
    ) -> OrderDecision:
        """Predict, apply order callbacks and constraints; return the final order."""
        raw = policy.predict(copy.deepcopy(inventory), current_period=period)
        if not isinstance(raw, OrderDecision):
            raise TypeError("policy.predict must return an OrderDecision")
        if raw.lead_time != policy.lead_time:
            raise ValueError("order lead_time must match the policy execution contract")
        adjusted = raw
        for position, callback in enumerate(self._active_callbacks):
            context = self._callback_context(
                inventory,
                period=period,
                run_window=run_window,
                phase="on_after_prediction",
                initial_decision=initial_decision,
            )
            decision_view = OrderDecision(
                adjusted.get_dataframe(),
                sku_column=adjusted.sku_column,
                lead_time=adjusted.lead_time,
                review_period=adjusted.review_period,
            )
            try:
                result = callback.on_after_prediction(decision_view, context)
                if result is None:
                    continue
                if not isinstance(result, OrderAdjustmentResult):
                    raise TypeError(
                        "on_after_prediction must return OrderAdjustmentResult or None"
                    )
                adjusted, audit = self._apply_order_adjustment_result(
                    adjusted, result, inventory, callback, position, context
                )
            except Exception as exc:
                if isinstance(exc, CallbackError):
                    raise
                raise self._callback_error(
                    callback, position, "on_after_prediction", period, context.date, exc
                ) from exc
            self._callback_audit_rows.extend(audit)
        self._capture_callback_order_trail(raw, adjusted, inventory)
        return self._constrain_and_count(inventory, adjusted, policy=policy)

    def _apply_order_adjustment_result(
        self, decision, result, inventory, callback, position, context
    ) -> tuple[OrderDecision, list[dict]]:
        frame = result.get_dataframe()
        required = {"unique_id", "order_quantity", "reason", "source"}
        missing = sorted(required - set(frame.columns))
        extra = sorted(set(frame.columns) - required)
        if missing:
            raise ValueError(f"order adjustment is missing required columns: {missing}")
        if extra:
            raise ValueError(f"order adjustment contains unsupported columns: {extra}")
        if frame.empty:
            return decision, []
        actual = _require_identifiers(frame, "unique_id", "order adjustment", unique=True)
        current = decision.get_dataframe()
        current_ids = _require_identifiers(
            current, decision.sku_column, "order decision", unique=True
        )
        unknown = actual - current_ids
        if unknown:
            raise ValueError(
                "order adjustment targets SKUs absent from the predicted decision: "
                f"{_identifier_sample(unknown)}"
            )
        quantities = pd.to_numeric(frame["order_quantity"], errors="coerce")
        if (
            quantities.isna().any()
            or not np.isfinite(quantities.to_numpy(dtype=float)).all()
            or (quantities < 0).any()
        ):
            raise ValueError("order adjustment.order_quantity must contain finite values >= 0")
        prepared = self._validated_reason_source(frame.copy(), "order adjustment")
        prepared["order_quantity"] = quantities.astype(float)
        output = current.copy()
        audit = []
        for row in prepared.itertuples(index=False):
            mask = output[decision.sku_column] == row.unique_id
            before = float(output.loc[mask, "order_quantity"].iloc[0])
            after = float(row.order_quantity)
            output.loc[mask, "order_quantity"] = after
            audit.append(self._audit_row(
                callback, position, context, row.unique_id,
                before=before, after=after, delta=after - before,
                order_quantity=after, reason=row.reason, source=row.source,
                received_date=pd.NaT, lot_evidence="",
            ))
        return OrderDecision(
            output,
            sku_column=decision.sku_column,
            lead_time=decision.lead_time,
            review_period=decision.review_period,
        ), audit

    @staticmethod
    def _audit_row(
        callback, position, context, unique_id, *, before, after, delta,
        order_quantity, reason, source, received_date, lot_evidence,
    ) -> dict:
        return {
            "callback_position": position,
            "callback_module": type(callback).__module__,
            "callback_class": type(callback).__name__,
            "phase": context.phase,
            "period": context.period,
            "date": context.date,
            "run_window": context.run_window,
            "initial_decision": context.initial_decision,
            "unique_id": unique_id,
            "before_value": float(before),
            "after_value": float(after),
            "quantity_delta": float(delta),
            "order_quantity": order_quantity,
            "reason": reason,
            "source": source,
            "received_date": received_date,
            "lot_evidence": lot_evidence,
        }

    def _capture_callback_order_trail(self, raw, adjusted, inventory) -> None:
        # Read-only access to the engine-owned frames; nothing here mutates them.
        state_ids = inventory.data[inventory.sku_column].tolist()
        ids = pd.Series(state_ids)
        raw_map = raw.data.set_index(raw.sku_column)["order_quantity"]
        requested = ids.map(raw_map).fillna(0.0)
        if adjusted is raw:
            adjusted_quantity = requested.copy()
        else:
            adjusted_map = adjusted.data.set_index(adjusted.sku_column)["order_quantity"]
            adjusted_quantity = ids.map(adjusted_map).fillna(0.0)
        self._captured_callback_order_audits.append(pd.DataFrame({
            "unique_id": state_ids,
            "requested_order_quantity": requested,
            "callback_adjusted_order_quantity": adjusted_quantity,
            "callback_adjustment_units": adjusted_quantity - requested,
        }))

    @staticmethod
    def _run_window(period: int, warmup_periods: int, scoring_periods: int) -> str:
        if period < warmup_periods:
            return 'warmup'
        if period < warmup_periods + scoring_periods:
            return 'scoring'
        return 'settlement'

    def _begin_order_capture(self) -> None:
        """Reset direct order counts for one decision opportunity."""
        self._captured_decision_positions = {}
        self._captured_order_event_count = 0
        self._captured_sku_order_line_counts = {}
        self._captured_order_line_quantity_squared_sums = {}
        self._captured_order_audits = []
        self._captured_callback_order_audits = []

    def _place_with_supply(self, inventory, orders, policy, demand_period):
        """Split one accepted order across suppliers and place the deliveries.

        The order is validated exactly as ``update_inventory_with_orders``
        validates it. Per SKU, ``latest_order`` receives the accepted quantity;
        the supplier lines only decide where and when it arrives.
        """
        from stockcast.core.data_structures import OrderLines
        from stockcast.utils.inventory_operations import (
            _apply_orders,
            _state_order_lines,
            _validate_order_decision,
        )

        supply = self._active_supply
        allow_backorders, _ = _validate_order_decision(inventory, orders, policy)
        sku_column = inventory.sku_column
        order_frame = orders.get_dataframe()
        positive = order_frame.loc[
            order_frame['order_quantity'] > 0, [orders.sku_column, 'order_quantity']
        ].rename(columns={orders.sku_column: sku_column}).reset_index(drop=True)
        current_period = int(inventory.data['period'].iloc[0])
        if len(positive):
            context = AllocationContext(
                inventory=inventory.get_dataframe(),
                open_orders=inventory.open_orders(),
                sku_column=sku_column,
                period=current_period,
                date=pd.Timestamp(inventory.data['date'].iloc[0]),
                suppliers=supply.supplier_ids,
                decision=order_frame.copy(deep=True),
            )
            allocated = supply._allocate(positive, context)
            deliveries = supply._deliveries(
                allocated,
                sku_column=sku_column,
                demand_period=demand_period,
                order_period=current_period,
            )
            lead_times = deliveries['due_period'].to_numpy() - current_period
            if (lead_times != self._supply_policy_lead_time).any():
                self._warn_supply_timing()
            for sku, quantity in allocated[[sku_column, 'order_quantity']].itertuples(
                index=False, name=None
            ):
                self._captured_sku_order_line_counts[sku] = (
                    self._captured_sku_order_line_counts.get(sku, 0) + 1
                )
                self._captured_order_line_quantity_squared_sums[sku] = (
                    self._captured_order_line_quantity_squared_sums.get(sku, 0.0)
                    + float(quantity) ** 2
                )
        else:
            deliveries = pd.DataFrame({
                sku_column: pd.Series(dtype=object),
                'supplier_id': pd.Series(dtype=object),
                'order_quantity': pd.Series(dtype=float),
                'order_period': pd.Series(dtype='int64'),
                'due_period': pd.Series(dtype='int64'),
                'order_line': pd.Series(dtype='int64'),
            })
        lines = _state_order_lines(inventory, OrderLines(deliveries, sku_column=sku_column))
        return _apply_orders(
            inventory, orders, allow_backorders,
            lines=lines[lines['order_quantity'] > 0],
        )

    def _warn_supply_timing(self) -> None:
        """Warn once per run when deliveries arrive off the policy's lead time."""
        if self._supply_timing_warned:
            return
        self._supply_timing_warned = True
        warnings.warn(
            "supplier deliveries arrive at times other than the policy's lead_time "
            f"({self._supply_policy_lead_time} periods). The policy's targets were set "
            "for that fixed lead time and are not adjusted, so its protection window "
            "does not describe this supply. Results are valid for this assumption; "
            "see 'Lead-time assumption' in the suppliers-and-open-orders guide.",
            UserWarning,
            stacklevel=2,
        )

    @staticmethod
    def _order_frame(final, *, opening_book, placements, opening_period, opening_date,
                     period_offset, resolutions=None,
                     outcome_suppliers=frozenset()) -> pd.DataFrame:
        """Order-level ledger of a run; the final book must match the pipeline."""
        final_period = int(final.data['period'].iloc[0])
        book = final._open_orders
        if book is not None:
            mismatch = pipeline_mismatch(
                book,
                pd.Index(final.data[final.sku_column].tolist(), dtype=object),
                final_period,
                final.max_lead_time,
                final._stacked_pipelines(),
            )
            if mismatch:
                raise AssertionError(f"final open-order book is inconsistent: {mismatch}")
        deliveries = _Deliveries.concat_all([opening_book.open, *placements])
        outcomes = None
        if outcome_suppliers:
            # Every delivery of a supplier with an outcome that fell due was
            # resolved exactly once; those rows come from the resolutions,
            # all others from the book.
            resolved_by_outcome = np.fromiter(
                (supplier in outcome_suppliers for supplier in deliveries.supplier),
                dtype=bool, count=len(deliveries),
            ) & (deliveries.due <= final_period)
            others = deliveries.select(~resolved_by_outcome)
            done = others.due <= final_period
            parts = [others] + [part for part, _, _ in resolutions]
            received = np.concatenate(
                [np.where(done, others.quantity, 0.0)]
                + [values for _, values, _ in resolutions]
            )
            delayed = np.concatenate(
                [np.zeros(len(others))] + [values for _, _, values in resolutions]
            )
            deliveries = _Deliveries.concat_all(parts)
            undelivered = np.where(
                deliveries.due <= final_period,
                np.maximum(deliveries.quantity - received - delayed, 0.0),
                0.0,
            )
            outcomes = {
                'received': received,
                'delayed': delayed,
                'undelivered': undelivered,
                'disrupted': (deliveries.due <= final_period) & (
                    (delayed > 0) | (undelivered > 0)
                ),
            }
        return build_order_frame(
            deliveries,
            final_period=final_period,
            opening_period=opening_period,
            opening_date=opening_date,
            period_offset=period_offset,
            outcomes=outcomes,
        )

    def _tracked_inventory_update(self, inventory, orders, policy=None):
        """Execute one order decision and retain its direct event counts."""
        orders = self._constrain_and_count(inventory, orders, policy=policy)
        return self._update_inventory_primitive(inventory, orders, policy=policy)

    def _constrain_and_count(self, inventory, orders, policy=None) -> OrderDecision:
        """Apply constraints to one order decision and retain its event counts."""
        if self._active_order_constraints is not None:
            order_frame = orders.get_dataframe()
            if order_frame.empty:
                decision_period = int(inventory.get_dataframe()["period"].iloc[0])
            else:
                decision_period = int(order_frame["order_period"].iloc[0])
            result = self._active_order_constraints.apply(
                orders,
                ConstraintContext(
                    inventory=inventory,
                    policy=policy,
                    decision_period=decision_period,
                ),
            )
            orders = result.order
            self._captured_order_audits.append(result.audit)
        order_frame = orders.data
        positive = order_frame['order_quantity'].fillna(0.0) > 0
        if positive.any():
            self._captured_order_event_count += 1
            if getattr(self, "_active_supply", None) is not None:
                # Supplier order lines are counted when the supply stage
                # places them (``_place_with_supply``).
                return orders
            sku_column = orders.sku_column
            positive_lines = order_frame.loc[
                positive,
                [sku_column, 'order_quantity'],
            ]
            for sku, quantity in positive_lines.itertuples(index=False, name=None):
                quantity = float(quantity)
                self._captured_sku_order_line_counts[sku] = (
                    self._captured_sku_order_line_counts.get(sku, 0) + 1
                )
                self._captured_order_line_quantity_squared_sums[sku] = (
                    self._captured_order_line_quantity_squared_sums.get(sku, 0.0)
                    + quantity ** 2
                )
        return orders

    @staticmethod
    def _repository_commit() -> Optional[str]:
        """Resolve HEAD without invoking Git; return None outside a checkout."""
        def validated(value: str) -> Optional[str]:
            candidate = value.strip()
            if len(candidate) != 40:
                return None
            if any(character not in "0123456789abcdefABCDEF" for character in candidate):
                return None
            return candidate.lower()

        repository = Path(__file__).resolve().parents[3]
        git_dir = repository / ".git"
        head_path = git_dir / "HEAD"
        if not head_path.is_file():
            return None
        head = head_path.read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref_name = head.removeprefix("ref: ")
            ref_path = git_dir / ref_name
            if ref_path.is_file():
                return validated(ref_path.read_text(encoding="utf-8"))
            packed_refs = git_dir / "packed-refs"
            if packed_refs.is_file():
                for line in packed_refs.read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith(("#", "^")):
                        commit, name = line.split(" ", 1)
                        if name == ref_name:
                            return validated(commit)
            return None
        return validated(head)

    @staticmethod
    def _build_run_manifest(
        *,
        demand_data: pd.DataFrame,
        demand_source_name: str,
        demand_source_type: str,
        random_seed: Optional[int],
        source_commit: Optional[str],
        policy: BasePolicy,
        sku_column: str,
        run_settings: dict,
        opening_inventory: dict,
    ) -> dict:
        """Build a serializable manifest for one simulation run."""
        demand_checksum = SimulationEngine._dataframe_checksum(
            demand_data,
            sort_columns=["period", sku_column],
        )
        try:
            package_version = importlib.metadata.version("stockcast")
        except importlib.metadata.PackageNotFoundError:
            package_version = None
        try:
            matplotlib_version = importlib.metadata.version("matplotlib")
        except importlib.metadata.PackageNotFoundError:
            matplotlib_version = None
        get_metadata = getattr(policy, "get_target_metadata", None)
        target_metadata = get_metadata() if callable(get_metadata) else {}
        return {
            'run_id': str(uuid4()),
            'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'demand_source': {
                'name': demand_source_name,
                'type': demand_source_type,
                'sha256': demand_checksum,
                'rows': len(demand_data),
                'random_seed': random_seed,
                'generation_provenance': copy.deepcopy(
                    demand_data.attrs.get("stockcast_demand_provenance")
                ),
            },
            'package': {
                'version': package_version,
                'source': {
                    'commit': source_commit,
                    'dirty': None,
                },
            },
            'policy': {
                'class': type(policy).__name__,
                'name': policy.policy_name,
                'lead_time': policy.lead_time,
                'review_period': policy.review_period,
                'decision_schedule': policy.schedule.to_manifest(),
                'service_level': policy.service_level,
                'allow_backorders': policy.allow_backorders,
                'target_metadata': target_metadata,
                'target_data': SimulationEngine._policy_target_fingerprint(policy),
            },
            'opening_inventory': copy.deepcopy(opening_inventory),
            'run_settings': copy.deepcopy(run_settings),
            'dependencies': {
                'python': platform.python_version(),
                'numpy': np.__version__,
                'pandas': pd.__version__,
                'matplotlib': matplotlib_version,
            },
        }

    @staticmethod
    def _dataframe_checksum(frame: pd.DataFrame, sort_columns: List[str]) -> str:
        """Hash a DataFrame's schema and values in a deterministic row order."""
        ordered = frame.sort_values(sort_columns, kind="stable").reset_index(drop=True)
        row_hashes = pd.util.hash_pandas_object(ordered, index=True).to_numpy()
        hasher = hashlib.sha256()
        hasher.update("|".join(map(str, ordered.columns)).encode("utf-8"))
        hasher.update("|".join(map(str, ordered.dtypes)).encode("utf-8"))
        hasher.update(row_hashes.tobytes())
        return hasher.hexdigest()

    @staticmethod
    def _inventory_fingerprint(inventory: InventoryStateDataFrame) -> dict:
        """Identify the complete opening state, including its pipeline."""
        state = inventory.get_dataframe().copy()
        state['in_transit'] = state['in_transit'].map(
            lambda value: json.dumps(np.asarray(value, dtype=float).tolist(), separators=(",", ":"))
        )
        return {
            'sha256': SimulationEngine._dataframe_checksum(
                state,
                sort_columns=[inventory.sku_column],
            ),
            'rows': len(state),
            'columns': list(state.columns),
            'sku_column': inventory.sku_column,
            'max_lead_time': inventory.max_lead_time,
            'allow_backorders': inventory.allow_backorders,
        }

    @staticmethod
    def _policy_target_fingerprint(policy: BasePolicy) -> Optional[dict]:
        """Identify the exact fitted target table without copying it into a manifest."""
        target_data = getattr(policy, "target_df_", None)
        if not isinstance(target_data, pd.DataFrame):
            target_data = getattr(policy, "target_table", None)
        if not isinstance(target_data, pd.DataFrame):
            return None
        sort_columns = [
            column
            for column in ["unique_id", "fh", "date", "period"]
            if column in target_data.columns
        ]
        if not sort_columns:
            sort_columns = list(target_data.columns)
        return {
            "sha256": SimulationEngine._dataframe_checksum(target_data, sort_columns),
            "rows": len(target_data),
            "columns": list(target_data.columns),
        }

    @staticmethod
    def _validate_policy_schedule(
        policy: BasePolicy,
        policy_schedule: Optional[Mapping[int, BasePolicy]],
        *,
        opening_date: pd.Timestamp,
        period_offset,
        decision_periods,
    ) -> Dict[int, BasePolicy]:
        """Validate explicit fitted-policy snapshots for decision dates."""
        if policy_schedule is None:
            return {}
        if not isinstance(policy_schedule, Mapping):
            raise TypeError("policy_schedule must be a mapping of decision period to policy")

        allowed_periods = decision_periods

        validated = {}
        configuration = (
            type(policy),
            policy.lead_time,
            policy.schedule.to_manifest(),
            policy.service_level,
            policy.allow_backorders,
            getattr(policy, "selling_horizon", None),
        )
        for period, snapshot in policy_schedule.items():
            if not isinstance(period, int) or isinstance(period, bool):
                raise ValueError("policy_schedule keys must be integer decision periods")
            if period not in allowed_periods:
                raise ValueError(
                    f"policy_schedule period {period} is not a decision event in this run"
                )
            if not isinstance(snapshot, BasePolicy) or not snapshot.fitted_:
                raise ValueError(
                    f"policy_schedule period {period} must contain a fitted BasePolicy"
                )
            snapshot_configuration = (
                type(snapshot),
                snapshot.lead_time,
                snapshot.schedule.to_manifest(),
                snapshot.service_level,
                snapshot.allow_backorders,
                getattr(snapshot, "selling_horizon", None),
            )
            if snapshot_configuration != configuration:
                raise ValueError(
                    "policy_schedule snapshots may change fitted targets only; policy class, "
                    "lead_time, review_period, service_level, and backorder mode must match"
                )
            decision_date = opening_date + period * period_offset
            SimulationEngine._validate_policy_information_origin(
                snapshot,
                latest_allowed_origin=decision_date,
                exact=True,
                expected_frequency=period_offset,
            )
            validated[period] = copy.deepcopy(snapshot)
        return validated

    @staticmethod
    def _validate_policy_information_origin(
        policy: BasePolicy,
        *,
        latest_allowed_origin: pd.Timestamp,
        exact: bool,
        expected_frequency,
    ) -> None:
        """Validate declared target origin against the simulation information date."""
        get_metadata = getattr(policy, "get_target_metadata", None)
        if not callable(get_metadata):
            return
        metadata = get_metadata()
        if "forecast_origin" not in metadata:
            raise ValueError("fitted policy target metadata must include forecast_origin")
        if "forecast_frequency" not in metadata:
            raise ValueError("fitted policy target metadata must include forecast_frequency")
        policy_frequency = _require_forward_frequency(
            metadata["forecast_frequency"],
            "policy forecast_frequency",
        )
        if policy_frequency != expected_frequency:
            raise ValueError(
                f"policy forecast_frequency {policy_frequency.freqstr} must match "
                f"simulation period_frequency {expected_frequency.freqstr}"
            )
        origin = pd.Timestamp(metadata["forecast_origin"])
        allowed = pd.Timestamp(latest_allowed_origin)
        if (exact and origin != allowed) or (not exact and origin > allowed):
            relation = "equal" if exact else "not be after"
            raise ValueError(
                f"policy forecast_origin {origin} must {relation} decision information "
                f"date {allowed}"
            )

    @staticmethod
    def _policy_for_decision(
        active_policy: BasePolicy,
        policy_schedule: Mapping[int, BasePolicy],
        period: int,
        update_log: list,
    ) -> BasePolicy:
        """Select the fitted snapshot for a decision and record its provenance."""
        if period not in policy_schedule:
            return active_policy
        snapshot = copy.deepcopy(policy_schedule[period])
        metadata = {}
        get_metadata = getattr(snapshot, "get_target_metadata", None)
        if callable(get_metadata):
            metadata = get_metadata()
        update_log.append({
            'decision_period': period,
            'policy_name': snapshot.policy_name,
            'target_metadata': metadata,
            'target_data': SimulationEngine._policy_target_fingerprint(snapshot),
        })
        return snapshot

    # ---- Demand source resolution ----

    @staticmethod
    def _materialize_demand_source(demand_source, n_periods: int) -> pd.DataFrame:
        """Materialize callable demand once so a run has one immutable input."""
        if isinstance(demand_source, pd.DataFrame):
            return demand_source.copy()
        if not callable(demand_source):
            raise TypeError("demand_source must be a pandas DataFrame or callable")

        frames = []
        provenance_rows = []
        for period in range(n_periods):
            frame = demand_source(period)
            if not isinstance(frame, pd.DataFrame):
                raise TypeError("demand_source callable must return a pandas DataFrame")
            frames.append(frame.copy())
            provenance = frame.attrs.get("stockcast_demand_provenance")
            if provenance is not None:
                provenance_rows.append(copy.deepcopy(provenance))
        if not frames:
            return pd.DataFrame(columns=['unique_id', 'period', 'date', 'y'])
        materialized = pd.concat(frames, ignore_index=True)
        if provenance_rows:
            if len(provenance_rows) != len(frames):
                raise ValueError(
                    "callable demand source supplied generation provenance for only "
                    "some periods"
                )
            modes = {row.get("negative_demand_handling") for row in provenance_rows}
            if len(modes) != 1:
                raise ValueError("callable demand source changed negative-demand handling mode")
            minima = [
                row.get("minimum_clipped_value")
                for row in provenance_rows
                if row.get("minimum_clipped_value") is not None
            ]
            materialized.attrs["stockcast_demand_provenance"] = {
                "negative_demand_handling": modes.pop(),
                "clipped_negative_count": sum(
                    int(row.get("clipped_negative_count", 0)) for row in provenance_rows
                ),
                "minimum_clipped_value": min(minima) if minima else None,
            }
        return materialized

    @staticmethod
    def _validate_demand_calendar(
        demand_data: pd.DataFrame,
        inventory: InventoryStateDataFrame,
        n_periods: int,
        period_offset,
    ) -> pd.DataFrame:
        """Validate the complete SKU-period-date grid before state mutation."""
        sku_column = inventory.sku_column
        required = [sku_column, 'period', 'date', 'y']
        missing_columns = [column for column in required if column not in demand_data.columns]
        if missing_columns:
            raise ValueError(f"demand_source is missing required columns: {missing_columns}")

        validated = demand_data.copy()
        periods = pd.to_numeric(validated['period'], errors='coerce')
        if periods.isna().any() or not np.isfinite(periods.to_numpy(dtype=float)).all():
            raise ValueError("demand_source.period must contain finite integers")
        if not np.equal(periods, np.floor(periods)).all():
            raise ValueError("demand_source.period must contain integers")
        validated['period'] = periods.astype(int)

        demand_values = pd.to_numeric(validated['y'], errors='coerce')
        if demand_values.isna().any() or not np.isfinite(demand_values.to_numpy(dtype=float)).all():
            raise ValueError("demand_source.y must contain finite values")
        if (demand_values < 0).any():
            raise ValueError("demand_source.y must be non-negative")
        validated['y'] = demand_values.astype(float)

        _require_identifiers(
            validated,
            sku_column,
            'demand_source',
            unique=False,
        )
        if validated.duplicated(['period', sku_column]).any():
            raise ValueError("demand_source contains duplicate SKU-period rows")

        expected_periods = set(range(n_periods))
        actual_periods = set(validated['period'].unique().tolist())
        if actual_periods != expected_periods:
            raise ValueError(
                f"demand_source periods must equal 0..{max(n_periods - 1, 0)}; "
                f"got {sorted(actual_periods)}"
            )

        expected_skus = _require_identifiers(
            inventory.get_dataframe(),
            sku_column,
            'inventory_state',
            unique=True,
        )
        # Rows of each period in input order, without rescanning per period.
        period_values = validated['period'].to_numpy()
        row_order = np.argsort(period_values, kind='stable')
        bounds = np.searchsorted(period_values[row_order], np.arange(n_periods + 1))
        sku_values = validated[sku_column].tolist()
        ordered_skus = [sku_values[row] for row in row_order]
        for period in range(n_periods):
            period_skus = set(ordered_skus[bounds[period]:bounds[period + 1]])
            if period_skus != expected_skus:
                missing = _identifier_sample(expected_skus - period_skus)
                extra = _identifier_sample(period_skus - expected_skus)
                raise ValueError(
                    f"demand_source period {period} has an incomplete SKU grid; "
                    f"missing={missing}, extra={extra}"
                )

        dates = pd.to_datetime(validated['date'], errors='coerce')
        if dates.isna().any():
            raise ValueError("demand_source.date must contain complete valid dates")
        validated['date'] = dates
        opening_dates = pd.to_datetime(inventory.get_dataframe()['date'], errors='coerce')
        if opening_dates.isna().any() or opening_dates.nunique() != 1:
            raise ValueError("inventory must contain one complete opening date")
        opening_date = opening_dates.iloc[0]
        stamps = validated['date'].to_numpy()
        plain_stamps = stamps.dtype.kind == 'M'
        for period in range(n_periods):
            expected_date = opening_date + (period + 1) * period_offset
            rows = row_order[bounds[period]:bounds[period + 1]]
            if (
                plain_stamps
                and (stamps[rows] == stamps[rows[0]]).all()
                and validated['date'].iloc[rows[0]] == expected_date
            ):
                continue
            period_dates = validated.loc[validated['period'] == period, 'date']
            if period_dates.nunique() != 1 or period_dates.iloc[0] != expected_date:
                actual = sorted(str(value) for value in period_dates.unique())
                raise ValueError(
                    f"demand_source period {period} must use date {expected_date}; got {actual}"
                )
        return validated

    def _resolve_demand_source(self, demand_source):
        """Convert demand_source to callable(period) -> demand_df."""
        if 'period' not in demand_source.columns:
            raise ValueError("demand_source DataFrame must contain a 'period' column")
        # DataFrame: filter by 'period' column
        def demand_fn(period):
            return demand_source[demand_source['period'] == period]
        return demand_fn

    # ---- Canonical order audit ----

    def _attach_order_audit(self, event_df: pd.DataFrame) -> pd.DataFrame:
        """Attach requested-versus-feasible order quantities to event rows."""
        event_df = event_df.copy()
        event_df["decision_inventory_position"] = event_df["unique_id"].map(
            self._captured_decision_positions
        )
        if self._captured_callback_order_audits:
            callback_audit = pd.concat(
                self._captured_callback_order_audits, ignore_index=True
            ).groupby('unique_id', as_index=False, sort=False).agg({
                'requested_order_quantity': 'sum',
                'callback_adjusted_order_quantity': 'sum',
                'callback_adjustment_units': 'sum',
            })
            event_df = event_df.merge(
                callback_audit, on='unique_id', how='left', validate='one_to_one'
            )
        else:
            event_df['requested_order_quantity'] = event_df['order_quantity']
            event_df['callback_adjusted_order_quantity'] = event_df['order_quantity']
            event_df['callback_adjustment_units'] = 0.0
        if not self._captured_order_audits:
            event_df['constrained_order_quantity'] = event_df['order_quantity']
            event_df['constraint_adjustment_units'] = 0.0
            event_df['constraint_binding_flag'] = False
            event_df['capacity_violation_flag'] = False
            event_df['binding_constraints'] = ""
            return event_df
        audit = pd.concat(self._captured_order_audits, ignore_index=True)
        audit = audit.groupby('unique_id', as_index=False, sort=False).agg({
            'constrained_order_quantity': 'sum',
            'constraint_adjustment_units': 'sum',
            'constraint_binding_flag': 'any',
            'capacity_violation_flag': 'any',
            'binding_constraints': lambda values: "|".join(
                value for value in values.astype(str) if value
            ),
        })
        return event_df.merge(
            audit,
            on='unique_id',
            how='left',
            validate='one_to_one',
        )

    # ---- Multi-policy comparison ----

    def run_comparison(
        self,
        policies: List[BasePolicy],
        demand_source: Union[pd.DataFrame, Callable],
        inventory: InventoryStateDataFrame,
        n_periods: int,
        *,
        period_frequency: str,
        initial_decision: str = "none",
        warmup_periods: int,
        scoring_periods: int,
        settlement_periods: int,
        order_during_settlement: bool,
        demand_source_name: str,
        random_seed: Optional[int],
        labels: Optional[List[str]] = None,
        policy_schedules: Optional[List[Optional[Mapping[int, BasePolicy]]]] = None,
        order_constraints: Optional[OrderingConstraints] = None,
        callbacks: Optional[Sequence[SimulationCallback]] = None,
        supply: Optional[SupplyModel] = None,
        processes: Optional[Sequence[InventoryProcess]] = None,
    ) -> 'ComparisonResult':
        """Simulate several policies on the same experiment.

        The demand is built once and shared; each policy starts from its own copy of
        ``inventory``. Constraints, callbacks, supply and processes apply to every
        branch (callbacks and processes are reset between branches), and random
        supplier lead times are drawn once and shared, so branches differ only by
        the policy. Arguments not listed below are those of ``run``.

        Args:
            policies: Fitted policies.
            labels: Names for the results; defaults to the policies' names.
            policy_schedules: One ``policy_schedule`` (or ``None``) per policy.

        Returns:
            A ``ComparisonResult`` keyed by label.
        """
        return self._run_comparison(
            policies, demand_source, inventory, n_periods,
            period_frequency=period_frequency,
            initial_decision=initial_decision,
            warmup_periods=warmup_periods,
            scoring_periods=scoring_periods,
            settlement_periods=settlement_periods,
            order_during_settlement=order_during_settlement,
            demand_source_name=demand_source_name,
            random_seed=random_seed,
            labels=labels,
            policy_schedules=policy_schedules,
            order_constraints=order_constraints,
            callbacks=callbacks,
            branch_run_options={"supply": supply, "processes": processes},
        )

    def _run_comparison(
        self, policies, demand_source, inventory, n_periods, *,
        period_frequency, initial_decision, warmup_periods, scoring_periods,
        settlement_periods, order_during_settlement, demand_source_name,
        random_seed, labels, policy_schedules, order_constraints, callbacks,
        branch_run_options: dict,
    ) -> 'ComparisonResult':
        """Run isolated branches; subclasses forward their own run inputs."""
        if not isinstance(policies, list) or not policies:
            raise ValueError("policies must be a non-empty list")
        if labels is None:
            labels = self._deduplicate_labels([p.policy_name for p in policies])
        else:
            if not isinstance(labels, list):
                raise TypeError("labels must be a list of non-empty unique strings")
            if len(labels) != len(policies):
                raise ValueError(
                    f"labels length ({len(labels)}) != policies length ({len(policies)})"
                )
        if any(
            not isinstance(label, str)
            or not label.strip()
            or label != label.strip()
            for label in labels
        ):
            raise ValueError(
                "labels must contain non-empty strings without surrounding whitespace"
            )
        if len(labels) != len(set(labels)):
            raise ValueError("labels must be unique")
        if policy_schedules is None:
            policy_schedules = [None] * len(policies)
        elif len(policy_schedules) != len(policies):
            raise ValueError(
                f"policy_schedules length ({len(policy_schedules)}) != policies length "
                f"({len(policies)})"
            )

        self._log(f"[SimEngine] Comparing {len(policies)} policies: {labels}")

        original_demand_source_type = (
            "dataframe" if isinstance(demand_source, pd.DataFrame) else "callable"
        )
        shared_demand = self._materialize_demand_source(demand_source, n_periods)
        results = {}
        for i, (policy, label, schedule) in enumerate(
            zip(policies, labels, policy_schedules)
        ):
            self._log(f"[SimEngine] Running {i + 1}/{len(policies)}: {label}")
            inv_copy = copy.deepcopy(inventory)
            policy_copy = copy.deepcopy(policy)
            result = self.run(
                policy_copy,
                shared_demand,
                inv_copy,
                n_periods,
                period_frequency=period_frequency,
                initial_decision=initial_decision,
                warmup_periods=warmup_periods,
                scoring_periods=scoring_periods,
                settlement_periods=settlement_periods,
                order_during_settlement=order_during_settlement,
                demand_source_name=demand_source_name,
                random_seed=random_seed,
                policy_schedule=schedule,
                order_constraints=order_constraints,
                callbacks=callbacks,
                **branch_run_options,
            )
            result.run_manifest['demand_source']['type'] = original_demand_source_type
            result.run_manifest['demand_source']['materialized_once_for_comparison'] = True
            results[label] = result

        return ComparisonResult(results)

    @staticmethod
    def _deduplicate_labels(labels: List[str]) -> List[str]:
        """Append _1, _2, etc. to duplicate labels."""
        seen = {}
        result = []
        for label in labels:
            if label in seen:
                seen[label] += 1
                result.append(f"{label}_{seen[label]}")
            else:
                seen[label] = 0
                result.append(label)
        return result
