"""Pluggable physical inventory processes with engine-owned accounting.

A process adds or removes physical on-hand stock at well-defined phases of a
simulation period, for example expiry, spoilage found on inspection, damage,
or customer returns. It declares its named flows up front; the engine applies
them, audits them, includes them in the stock-balance identities, and records
them in the run manifest.

Usage:
    class InspectionLoss(InventoryProcess):
        name = "inspection"
        flows = (Flow("damaged", "outflow"),)

        def after_demand(self, context):
            damaged = (0.05 * context.on_hand).round()
            return ProcessFlows({"damaged": damaged})

    result = SimulationEngine().run(..., processes=[InspectionLoss()])
    result.to_process_flow_frame()

Period sequence with processes (see knowledge/30):

    before_demand   the period opens at its demand date; flows act on stock
                    before due receipts, the order decision and demand
    on_receipt      pipeline arrivals, then any zero-lead-time receipts
    after_demand    demand has been served; flows act before callbacks

Processes only change on-hand stock. They never change the pipeline, backlog
or demand.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Optional

import numpy as np
import pandas as pd

from stockcast.core._array_state import ArrayState

PROCESS_FLOW_COLUMNS = (
    "unique_id",
    "period",
    "date",
    "demand_period",
    "run_window",
    "process",
    "flow",
    "direction",
    "category",
    "phase",
    "quantity",
)

FLOW_DIRECTIONS = ("inflow", "outflow")
FLOW_CATEGORIES = ("general", "expiry")
PROCESS_PHASES = ("before_demand", "on_receipt", "after_demand")
# Event-ledger columns carrying non-expiry process flows. They appear only in
# runs whose processes declare at least one ``general`` flow.
PROCESS_EVENT_COLUMNS = ("process_inflow_units", "process_outflow_units")


def _valid_name(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


@dataclass(frozen=True)
class Flow:
    """One named, signed physical flow that a process may report.

    ``direction`` is ``"inflow"`` (adds on-hand units) or ``"outflow"``
    (removes on-hand units). ``category="expiry"`` marks an outflow as
    expiry: it is recorded in the canonical ``expired_units`` ledger column
    and priced by ``waste_cost``. Every other flow is ``"general"`` and is
    recorded in ``process_inflow_units`` / ``process_outflow_units``.
    """

    name: str
    direction: str
    category: str = "general"

    def __post_init__(self) -> None:
        if not _valid_name(self.name):
            raise ValueError("Flow.name must be a non-empty string without surrounding whitespace")
        if self.direction not in FLOW_DIRECTIONS:
            raise ValueError("Flow.direction must be 'inflow' or 'outflow'")
        if self.category not in FLOW_CATEGORIES:
            raise ValueError("Flow.category must be 'general' or 'expiry'")
        if self.category == "expiry" and self.direction != "outflow":
            raise ValueError("an expiry flow must be an outflow")

    def to_manifest(self) -> dict:
        return {"name": self.name, "direction": self.direction, "category": self.category}


@dataclass(frozen=True)
class ProcessContext:
    """Read-only per-SKU view of the run at one process phase.

    Every quantity is a float ``pd.Series`` indexed by SKU identifier, in the
    state's SKU order. ``received``, ``demand``, ``fulfilled`` and
    ``backorders_fulfilled`` are ``None`` where the phase has no such value:

    - ``before_demand``: none of them (the period has not received or sold);
    - ``on_receipt``: ``received`` holds the units being received now;
    - ``after_demand`` and ``callback_adjustment``: all four hold this
      period's values.

    ``period`` and ``date`` identify the demand period being processed
    (the event-ledger coordinates); ``demand_period`` is its zero-based
    index. The context is a copy: changing it never changes engine state.
    """

    phase: str
    period: int
    demand_period: Optional[int]
    date: pd.Timestamp
    run_window: str
    unique_id: pd.Index
    on_hand: pd.Series
    backorders: pd.Series
    on_order: pd.Series
    received: Optional[pd.Series] = None
    demand: Optional[pd.Series] = None
    fulfilled: Optional[pd.Series] = None
    backorders_fulfilled: Optional[pd.Series] = None


@dataclass(frozen=True)
class StockChange:
    """One on-hand change made by something other than the notified process.

    ``quantity`` is signed: positive units were added, negative removed.
    ``origin`` is ``"process"`` (another process's flow; ``source`` is
    ``"<process>.<flow>"``) or ``"callback"`` (an accepted
    ``InventoryAdjustmentResult`` row; ``source`` is the callback class).
    ``received_date`` is the lot date given for an addition, else ``NaT``.
    """

    unique_id: object
    quantity: float
    received_date: pd.Timestamp
    origin: str
    source: str


class ProcessFlows:
    """Quantities a process reports for its declared flows at one phase.

    ``quantities`` maps a declared flow name to nonnegative per-SKU units,
    given as a mapping or a ``pd.Series`` indexed by SKU. Omitted SKUs and
    zero values mean no flow. The flow's direction comes from its ``Flow``
    declaration, never from the sign of the quantity.

    ``received_dates`` optionally gives inflows a lot date, per flow, as one
    timestamp or a per-SKU mapping. Processes that track lot ages (such as
    ``ShelfLife``) require it for every inflow they observe.
    """

    def __init__(
        self,
        quantities: Mapping[str, object],
        received_dates: Optional[Mapping[str, object]] = None,
    ):
        if not isinstance(quantities, Mapping):
            raise TypeError("ProcessFlows quantities must be a mapping of flow name to per-SKU units")
        self._quantities = {}
        for flow, values in quantities.items():
            if isinstance(values, pd.Series):
                values = values.copy(deep=True)
            elif isinstance(values, Mapping):
                values = dict(values)
            else:
                raise TypeError(
                    f"ProcessFlows quantities for flow {flow!r} must be a mapping or pandas Series"
                )
            self._quantities[flow] = values
        if received_dates is not None and not isinstance(received_dates, Mapping):
            raise TypeError("ProcessFlows received_dates must be a mapping of flow name to dates")
        self._received_dates = dict(received_dates or {})

    @property
    def quantities(self) -> dict:
        return {
            flow: values.copy(deep=True) if isinstance(values, pd.Series) else dict(values)
            for flow, values in self._quantities.items()
        }

    @property
    def received_dates(self) -> dict:
        return dict(self._received_dates)


class InventoryProcess:
    """Base class for a physical process that changes on-hand stock.

    Subclasses set ``name`` (unique within a run) and ``flows`` (a tuple of
    ``Flow``), then override only the hooks they need. The engine calls the
    exact instances passed to ``run`` and resets them before every run, so
    run-local state (a lot ledger, a random generator) belongs in ``reset``.

    Flow hooks return ``ProcessFlows`` or ``None``:
        before_demand(context)   start of period, before receipts and ordering
        after_demand(context)    after demand is served, before callbacks

    Observation hooks return ``None``:
        reset(context)           before the first period of each run
        on_receipt(context)      stock just received (``context.received``)
        check(context)           assert an internal mirror of on-hand stock;
                                 runs after demand and after each accepted
                                 callback adjustment batch

    Composition hooks, for processes that mirror stock (for example lots):
        validate_stock_change(change, context)  raise to reject a change
        on_stock_change(change, context)        mirror it; may return
                                                evidence text for the audit

    Processes run in list order; each sees the stock left by the ones before
    it. A process is told about every other process's flows and every
    callback adjustment, but not about its own flows.
    """

    name: str = ""
    flows: tuple = ()

    def reset(self, context: ProcessContext) -> None:
        """Reset run-local state before a simulation starts."""

    def before_demand(self, context: ProcessContext) -> Optional[ProcessFlows]:
        """Report flows at the start of the period."""
        return None

    def on_receipt(self, context: ProcessContext) -> None:
        """Observe units received this period (``context.received``)."""

    def after_demand(self, context: ProcessContext) -> Optional[ProcessFlows]:
        """Report flows after demand is served."""
        return None

    def validate_stock_change(self, change: StockChange, context: ProcessContext) -> None:
        """Raise to reject an on-hand change before anything is applied."""

    def on_stock_change(self, change: StockChange, context: ProcessContext) -> Optional[str]:
        """Mirror an on-hand change made by another process or a callback."""
        return None

    def check(self, context: ProcessContext) -> None:
        """Assert that internal state still agrees with ``context.on_hand``."""

    def get_config(self) -> dict:
        """Return JSON-serializable configuration for the run manifest."""
        return {}


_HOOKS = (
    "reset", "before_demand", "on_receipt", "after_demand",
    "validate_stock_change", "on_stock_change", "check",
)


def _enabled_hooks(process: InventoryProcess) -> list:
    return [
        hook for hook in _HOOKS
        if getattr(type(process), hook) is not getattr(InventoryProcess, hook)
    ]


def prepare_processes(processes, *, reserved=()) -> list:
    """Validate a ``processes=`` argument; return the ordered list."""
    if processes is None:
        return []
    if isinstance(processes, (str, bytes)) or not isinstance(processes, (list, tuple)):
        raise TypeError("processes must be a list of InventoryProcess objects")
    prepared = list(processes)
    names = set(reserved)
    for position, process in enumerate(prepared):
        if not isinstance(process, InventoryProcess):
            raise TypeError(
                "processes must contain only InventoryProcess objects; "
                f"got {type(process).__name__} at position {position}"
            )
        if not _valid_name(process.name):
            raise ValueError(
                f"process at position {position} ({type(process).__name__}) needs a "
                "non-empty name without surrounding whitespace"
            )
        if process.name in names:
            raise ValueError(f"process names must be unique within a run; duplicate {process.name!r}")
        names.add(process.name)
        flows = process.flows
        if not isinstance(flows, (list, tuple)) or not all(isinstance(flow, Flow) for flow in flows):
            raise TypeError(f"process {process.name!r} flows must be a tuple of Flow objects")
        flow_names = [flow.name for flow in flows]
        if len(flow_names) != len(set(flow_names)):
            raise ValueError(f"process {process.name!r} declares duplicate flow names")
    return prepared


def process_manifest(processes, *, first_position=0) -> list:
    """Manifest entries; ``get_config`` must return a JSON-serializable dict."""
    manifests = []
    for position, process in enumerate(processes, start=first_position):
        config = process.get_config()
        if not isinstance(config, dict):
            raise TypeError(f"process {process.name!r} get_config() must return a dictionary")
        json.dumps(config, allow_nan=False)
        manifests.append({
            "position": position,
            "name": process.name,
            "module": type(process).__module__,
            "class": type(process).__name__,
            "flows": [flow.to_manifest() for flow in process.flows],
            "enabled_hooks": _enabled_hooks(process),
            "config": config,
        })
    return manifests


# ---------------------------------------------------------------------------
# Engine-private application
# ---------------------------------------------------------------------------

def _state_view(state):
    """Return ``(sku_index, columns)`` for an ArrayState or DataFrame state."""
    if isinstance(state, ArrayState):
        index = state.schema.sku_index()
        values = {
            "on_hand": state.on_hand,
            "backorders": state.backorders,
            "on_order": state.on_order(),
        }
        for name, column in state.latest.items():
            values[name] = column
        return index, values
    data = state.data
    index = pd.Index(data[state.sku_column].reset_index(drop=True), name="unique_id")
    values = {
        "on_hand": data["on_hand"].to_numpy(dtype=float),
        "backorders": data["backorders"].to_numpy(dtype=float),
        "on_order": data["in_transit"].map(
            lambda value: float(value.sum()) if hasattr(value, "sum") else 0.0
        ).to_numpy(dtype=float),
    }
    for name in (
        "latest_received", "latest_incoming_demand", "latest_fulfilled",
        "latest_backorders_fulfilled",
    ):
        if name in data:
            values[name] = data[name].to_numpy(dtype=float)
    return index, values


def _series(index: pd.Index, values, name: str) -> pd.Series:
    return pd.Series(np.array(values, dtype=float, copy=True), index=index, name=name)


class ProcessRunner:
    """Applies, audits and records the flows of one run's processes."""

    def __init__(self, processes, *, opening_period: int):
        self.processes = list(processes)
        self.opening_period = int(opening_period)
        self.general_flows = any(
            flow.category == "general" for process in self.processes for flow in process.flows
        )
        self.flow_rows: list = []
        self.coordinates = None
        # Hooks a process does not override are skipped (no context built).
        self.with_hook = {
            hook: [process for process in self.processes if hook in _enabled_hooks(process)]
            for hook in _HOOKS
        }
        self.expired: dict = {}
        self.inflow: dict = {}
        self.outflow: dict = {}

    # ---- contexts -----------------------------------------------------------

    def context(self, state, phase: str, *, received=None, with_period_flows=False) -> ProcessContext:
        index, values = _state_view(state)
        coordinates = self.coordinates
        fields = dict(
            phase=phase,
            period=coordinates["period"],
            demand_period=coordinates["demand_period"],
            date=coordinates["date"],
            run_window=coordinates["run_window"],
            unique_id=index,
            on_hand=_series(index, values["on_hand"], "on_hand"),
            backorders=_series(index, values["backorders"], "backorders"),
            on_order=_series(index, values["on_order"], "on_order"),
        )
        if received is not None:
            fields["received"] = _series(index, received, "received")
        if with_period_flows:
            fields["received"] = _series(index, values["latest_received"], "received")
            fields["demand"] = _series(index, values["latest_incoming_demand"], "demand")
            fields["fulfilled"] = _series(index, values["latest_fulfilled"], "fulfilled")
            fields["backorders_fulfilled"] = _series(
                index, values["latest_backorders_fulfilled"], "backorders_fulfilled",
            )
        return ProcessContext(**fields)

    def reset(self, inventory, *, opening_period: int, opening_date) -> None:
        self.coordinates = {
            "period": int(opening_period),
            "demand_period": None,
            "date": pd.Timestamp(opening_date),
            "run_window": "opening",
        }
        if not self.with_hook["reset"]:
            return
        context = self.context(inventory, "reset")
        for process in self.with_hook["reset"]:
            process.reset(context)

    def begin_period(self, *, demand_period: int, date, run_window: str) -> None:
        self.coordinates = {
            "period": self.opening_period + demand_period + 1,
            "demand_period": int(demand_period),
            "date": pd.Timestamp(date),
            "run_window": run_window,
        }
        self.expired, self.inflow, self.outflow = {}, {}, {}

    # ---- phases ---------------------------------------------------------------

    def before_demand(self, state):
        return self._flow_phase(state, "before_demand", with_period_flows=False)

    def after_demand(self, state):
        state = self._flow_phase(state, "after_demand", with_period_flows=True)
        self.check(state, "after_demand")
        return state

    def receipt(self, state, received: np.ndarray) -> None:
        """Tell every process about units received now (if any)."""
        received = np.asarray(received, dtype=float)
        if not self.with_hook["on_receipt"] or not (received > 0).any():
            return
        context = self.context(state, "on_receipt", received=received)
        for process in self.with_hook["on_receipt"]:
            result = process.on_receipt(context)
            if result is not None:
                raise TypeError(f"process {process.name!r} on_receipt must return None")

    def order_receipt(self, before, after) -> None:
        """Receipts created by an order decision (zero lead time)."""
        if not self.with_hook["on_receipt"]:
            return
        before_index, before_values = _state_view(before)
        after_index, after_values = _state_view(after)
        prior = pd.Series(before_values["latest_received"], index=before_index)
        if not after_index.equals(before_index):
            prior = prior.reindex(after_index).fillna(0.0)
        delta = after_values["latest_received"] - prior.to_numpy(dtype=float)
        self.receipt(after, delta)

    def check(self, state, phase: str) -> None:
        if not self.with_hook["check"]:
            return
        context = self.context(state, phase, with_period_flows=True)
        for process in self.with_hook["check"]:
            process.check(context)

    # ---- callback adjustments ---------------------------------------------------

    def validate_change(self, change: StockChange, context: ProcessContext) -> None:
        for process in self.with_hook["validate_stock_change"]:
            process.validate_stock_change(change, context)

    def notify_change(self, change: StockChange, context: ProcessContext) -> str:
        evidence = {}
        for process in self.with_hook["on_stock_change"]:
            text = process.on_stock_change(change, context)
            if text:
                evidence[process.name] = str(text)
        if not evidence:
            return ""
        if len(evidence) == 1:
            return next(iter(evidence.values()))
        return json.dumps(evidence, sort_keys=True, separators=(",", ":"))

    # ---- flow application --------------------------------------------------------

    def _flow_phase(self, state, phase: str, *, with_period_flows: bool):
        for process in self.with_hook[phase]:
            context = self.context(state, phase, with_period_flows=with_period_flows)
            result = getattr(process, phase)(context)
            if result is None:
                continue
            if not isinstance(result, ProcessFlows):
                raise TypeError(f"process {process.name!r} {phase} must return ProcessFlows or None")
            state = self._apply(process, result, state, phase, context)
        return state

    def _apply(self, process, result: ProcessFlows, state, phase: str, context: ProcessContext):
        declared = {flow.name: flow for flow in process.flows}
        quantities = result.quantities
        received_dates = result.received_dates
        unknown = sorted(map(str, set(quantities) - set(declared)))
        if unknown:
            raise ValueError(f"process {process.name!r} reported undeclared flows: {unknown}")
        unknown = sorted(map(str, set(received_dates) - set(quantities)))
        if unknown:
            raise ValueError(
                f"process {process.name!r} gave received_dates for flows without quantities: {unknown}"
            )
        for flow in process.flows:
            if flow.name not in quantities:
                continue
            rows = self._validated_rows(process, flow, quantities[flow.name],
                                        received_dates.get(flow.name), context)
            if not rows:
                continue
            state = self._apply_flow(process, flow, rows, state, phase, context)
            # Later flows of the same result see the updated stock.
            context = self.context(state, phase, with_period_flows=phase == "after_demand")
        return state

    def _validated_rows(self, process, flow: Flow, values, dates, context: ProcessContext) -> list:
        label = f"process {process.name!r} flow {flow.name!r}"
        items = values.items() if isinstance(values, (pd.Series, Mapping)) else ()
        # Identifiers are never coerced: 1 and "1" are different SKUs.
        known = {(type(value), value) for value in context.unique_id.tolist()}
        rows = []
        seen = set()
        for sku, quantity in items:
            sku = sku.item() if isinstance(sku, np.generic) else sku
            if (type(sku), sku) not in known:
                raise ValueError(f"{label} contains unknown SKU {sku!r}")
            if (type(sku), sku) in seen:
                raise ValueError(f"{label} lists SKU {sku!r} more than once")
            seen.add((type(sku), sku))
            if isinstance(quantity, (bool, np.bool_)):
                raise ValueError(f"{label} quantities must be finite numbers >= 0")
            try:
                quantity = float(quantity)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label} quantities must be finite numbers >= 0") from exc
            if not np.isfinite(quantity) or quantity < 0:
                raise ValueError(f"{label} quantities must be finite numbers >= 0")
            if quantity == 0:
                continue
            received_date = pd.NaT
            if dates is not None:
                if flow.direction != "inflow":
                    raise ValueError(f"{label}: received_dates apply only to inflows")
                raw = dates.get(sku, pd.NaT) if isinstance(dates, Mapping) else dates
                received_date = pd.to_datetime(raw, errors="coerce")
                if pd.isna(received_date) and not pd.isna(raw):
                    raise ValueError(f"{label} received_dates must be valid timestamps")
                if not pd.isna(received_date) and received_date > context.date:
                    raise ValueError(f"{label} received_date cannot be in the future")
            on_hand = float(context.on_hand.loc[sku])
            if flow.direction == "outflow":
                # Same allowance as the lot-balance check; anything beyond it
                # is a process defect. The event balance still arbitrates.
                if quantity > on_hand + 1e-6 + 1e-5 * abs(on_hand):
                    raise ValueError(
                        f"{label} removes {quantity} units from SKU {sku!r} with "
                        f"{on_hand} on hand"
                    )
            elif float(context.backorders.loc[sku]) > 1e-9:
                raise ValueError(
                    f"{label} adds stock to SKU {sku!r} while it has backlog; "
                    "process inflows are not supported while a SKU has backlog"
                )
            rows.append((sku, quantity, received_date))
        return rows

    def _apply_flow(self, process, flow: Flow, rows: list, state, phase: str, context):
        sign = 1.0 if flow.direction == "inflow" else -1.0
        source = f"{process.name}.{flow.name}"
        validators = [other for other in self.with_hook["validate_stock_change"] if other is not process]
        observers = [other for other in self.with_hook["on_stock_change"] if other is not process]
        changes = [
            StockChange(sku, sign * quantity, received_date, "process", source)
            for sku, quantity, received_date in rows
        ]
        for other in validators:
            for change in changes:
                other.validate_stock_change(change, context)

        mapping = {sku: quantity for sku, quantity, _ in rows}
        if isinstance(state, ArrayState):
            delta = state.map_by_sku(mapping, 0.0)
            if flow.direction == "outflow":
                remaining = state.on_hand - delta
                state = state.with_on_hand(np.where(remaining < 0.0, 0.0, remaining))
            else:
                state = state.with_on_hand(state.on_hand + delta)
        else:
            delta = state.data[state.sku_column].map(mapping).fillna(0.0)
            if flow.direction == "outflow":
                state.data["on_hand"] = (state.data["on_hand"] - delta).clip(lower=0.0)
            else:
                state.data["on_hand"] = state.data["on_hand"] + delta

        for other in observers:
            for change in changes:
                other.on_stock_change(change, context)

        if flow.category == "expiry":
            totals = self.expired
        elif flow.direction == "inflow":
            totals = self.inflow
        else:
            totals = self.outflow
        coordinates = self.coordinates
        for sku, quantity, _ in rows:
            totals[sku] = totals.get(sku, 0.0) + quantity
            self.flow_rows.append((
                sku, coordinates["period"], coordinates["date"], coordinates["demand_period"],
                coordinates["run_window"], process.name, flow.name, flow.direction,
                flow.category, phase, quantity,
            ))
        return state

    # ---- outputs ------------------------------------------------------------------

    def merged_expired(self, engine_expired: Mapping) -> Mapping:
        if not self.expired:
            return engine_expired
        if not engine_expired:
            return self.expired
        merged = dict(engine_expired)
        for sku, quantity in self.expired.items():
            merged[sku] = merged.get(sku, 0.0) + quantity
        return merged

    def flow_frame(self) -> pd.DataFrame:
        frame = pd.DataFrame(self.flow_rows, columns=list(PROCESS_FLOW_COLUMNS))
        if len(frame):
            frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
            frame["quantity"] = frame["quantity"].astype(float)
        return frame
