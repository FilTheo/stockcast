"""Engine-private NumPy period state and one-shot result assembly.

``SimulationEngine`` keeps live inventory as arrays between its public
boundaries. Policies, constraints and callbacks still receive
``InventoryStateDataFrame`` or DataFrame objects built from these arrays, and
the history and canonical event ledger are assembled once, after the run.

Exactness contract: every frame built here must equal, value for value and
dtype for dtype, the frame the per-period pandas path produces. A state that
cannot be represented exactly (for example a non-float quantity column) is
left in DataFrame form, and the engine runs that period on the pandas path.
"""

from __future__ import annotations

import copy
from typing import Callable, Dict, List, Mapping, Optional

import numpy as np
import pandas as pd

from stockcast.core.data_structures import (
    InventoryStateDataFrame,
    _DeferredHistory,
    _uniform_float_pipelines,
)

LATEST_COLUMNS = (
    "latest_order",
    "latest_received",
    "latest_fulfilled",
    "latest_backorders_fulfilled",
    "latest_incoming_demand",
    "latest_shortage",
)
_FLOAT_COLUMNS = ("on_hand", "backorders") + LATEST_COLUMNS
_SPECIAL_COLUMNS = frozenset(
    _FLOAT_COLUMNS + ("target_level", "period", "date", "is_review_period", "in_transit")
)
_FLOAT64 = np.dtype("float64")
_BOOL = np.dtype("bool")
_PERIOD_DTYPES = (np.dtype("int64"), _FLOAT64)
_EVENT_DATE_DTYPE = np.dtype("datetime64[ns]")

_scalar_dtype_cache: Dict[type, object] = {}
# ``Series.map(...).fillna(0).astype(int)`` in the per-period event builder.
_LINE_COUNT_DTYPE = pd.Series([0.0]).astype(int).dtype


def scalar_column_dtype(value) -> object:
    """Return the dtype pandas gives a column assigned from one scalar."""
    key = type(value)
    if key not in _scalar_dtype_cache:
        probe = pd.DataFrame(index=pd.RangeIndex(1))
        probe["value"] = value
        _scalar_dtype_cache[key] = probe["value"].dtype
    return _scalar_dtype_cache[key]


def _numeric_constant(series: pd.Series) -> pd.Series:
    """Match ``pd.to_numeric(..., errors='coerce')`` as used on event columns."""
    return pd.to_numeric(series, errors="coerce")


def _repeat_values(values: list, n: int, dtype) -> np.ndarray:
    return np.repeat(np.asarray(values, dtype=dtype), n)


def _column_from_objects(values: np.ndarray, dtype):
    if dtype == np.dtype(object):
        return values
    return pd.array(values, dtype=dtype)


class StateSchema:
    """Everything about a state frame except its per-period quantities.

    Two states share a schema object when their frames differ only in values
    the kernel tracks as arrays, which lets result assembly treat consecutive
    periods as one block.
    """

    __slots__ = (
        "columns", "index", "sku_column", "constants", "target_dtype",
        "period_dtype", "date_dtype", "pipeline_dtype", "max_lead_time",
        "n", "_sku_event", "_sku_list", "_sku_index", "_numeric", "_derived",
        "_demand_alignment",
    )

    def __init__(self, *, columns, index, sku_column, constants, target_dtype,
                 period_dtype, date_dtype, pipeline_dtype, max_lead_time):
        self.columns = columns
        self.index = index
        self.sku_column = sku_column
        self.constants = constants
        self.target_dtype = target_dtype
        self.period_dtype = period_dtype
        self.date_dtype = date_dtype
        self.pipeline_dtype = pipeline_dtype
        self.max_lead_time = max_lead_time
        self.n = len(index)
        self._sku_event = None
        self._sku_list = None
        self._sku_index = None
        self._numeric = {}
        self._derived = {}
        self._demand_alignment = None

    @property
    def sku(self) -> pd.Series:
        return self.constants[self.sku_column]

    def sku_event(self) -> pd.Series:
        """SKU column as the event ledger's ``unique_id`` column."""
        if self._sku_event is None:
            self._sku_event = self.sku.reset_index(drop=True).rename("unique_id")
        return self._sku_event

    def sku_list(self) -> list:
        if self._sku_list is None:
            self._sku_list = self.sku.tolist()
        return self._sku_list

    def sku_index(self) -> pd.Index:
        if self._sku_index is None:
            self._sku_index = pd.Index(self.sku_event())
        return self._sku_index

    def numeric(self, name: str) -> pd.Series:
        """Event-ledger view of a constant column (``pd.to_numeric``)."""
        if name not in self._numeric:
            self._numeric[name] = _numeric_constant(self.constants[name]).reset_index(drop=True)
        return self._numeric[name]

    def with_date_dtype(self, date_dtype) -> "StateSchema":
        """Schema after a period advance assigns a new scalar date."""
        if date_dtype == self.date_dtype and self.pipeline_dtype == _FLOAT64:
            return self
        pipeline_dtype = _FLOAT64 if self.max_lead_time else self.pipeline_dtype
        key = (date_dtype, pipeline_dtype)
        if key not in self._derived:
            self._derived[key] = StateSchema(
                columns=self.columns, index=self.index, sku_column=self.sku_column,
                constants=self.constants, target_dtype=self.target_dtype,
                period_dtype=self.period_dtype, date_dtype=date_dtype,
                pipeline_dtype=pipeline_dtype, max_lead_time=self.max_lead_time,
            )
        return self._derived[key]

    def after_order(self) -> "StateSchema":
        """Schema of the state ``update_inventory_with_orders`` returns.

        Its merge rebuilds a RangeIndex and re-appends ``target_level`` as
        the last column; a float order target keeps the column float64.
        """
        if "after_order" not in self._derived:
            columns = tuple(name for name in self.columns if name != "target_level")
            candidate = StateSchema(
                columns=columns + ("target_level",),
                index=pd.RangeIndex(self.n),
                sku_column=self.sku_column,
                constants={
                    name: series.reset_index(drop=True)
                    for name, series in self.constants.items()
                },
                target_dtype=_FLOAT64,
                period_dtype=self.period_dtype,
                date_dtype=self.date_dtype,
                pipeline_dtype=self.pipeline_dtype,
                max_lead_time=self.max_lead_time,
            )
            self._derived["after_order"] = self if candidate.same_as(self) else candidate
        return self._derived["after_order"]

    def same_as(self, other: "StateSchema") -> bool:
        if other is self:
            return True
        if (
            self.columns != other.columns
            or self.target_dtype != other.target_dtype
            or self.period_dtype != other.period_dtype
            or self.date_dtype != other.date_dtype
            or self.pipeline_dtype != other.pipeline_dtype
            or type(self.index) is not type(other.index)
            or self.index.dtype != other.index.dtype
            or not self.index.equals(other.index)
            or self.constants.keys() != other.constants.keys()
        ):
            return False
        for name, series in self.constants.items():
            candidate = other.constants[name]
            if candidate is not series and (
                candidate.dtype != series.dtype or not candidate.equals(series)
            ):
                return False
        return True


def _date_column(dates: List[pd.Timestamp], n: int, dtype) -> object:
    values = np.repeat(np.array([date.to_datetime64() for date in dates]), n)
    if values.dtype == dtype:
        return values
    return pd.Series(values).astype(dtype).array


_date_dtype_cache: Dict[tuple, object] = {}


def _assigned_date_dtype(date: pd.Timestamp):
    """dtype of a state date column assigned from one Timestamp scalar."""
    key = (date.unit, str(date.tz))
    if key not in _date_dtype_cache:
        probe = pd.DataFrame(index=pd.RangeIndex(1))
        probe["value"] = date
        _date_dtype_cache[key] = probe["value"].dtype
    return _date_dtype_cache[key]


class ArrayState:
    """Immutable-by-convention NumPy mirror of one ``InventoryStateDataFrame``.

    Transitions return new objects; arrays recorded in the run log are never
    modified afterwards.
    """

    __slots__ = (
        "schema", "on_hand", "backorders", "latest", "target", "pipeline",
        "period", "date", "is_review", "has_stockout", "has_backorder",
        "source", "_on_order",
    )

    def __init__(self, *, schema, on_hand, backorders, latest, target, pipeline,
                 period, date, is_review, has_stockout=False, has_backorder=False,
                 source=None):
        self.schema = schema
        self.on_hand = on_hand
        self.backorders = backorders
        self.latest = latest
        self.target = target
        self.pipeline = pipeline
        self.period = period
        self.date = date
        self.is_review = is_review
        self.has_stockout = has_stockout
        self.has_backorder = has_backorder
        # Only an opening state keeps its source frame; it is the exact
        # original for the reference path and for event rows.
        self.source = source
        self._on_order = None

    # ---- construction from DataFrame state ---------------------------------

    @classmethod
    def from_inventory(
        cls,
        inventory: InventoryStateDataFrame,
        *,
        opening: bool,
        hint: Optional[StateSchema] = None,
    ) -> Optional["ArrayState"]:
        """Mirror a state frame, or return None when it is not representable.

        An opening state only needs exact starting quantities; its flow
        columns and review flag are reset by the first advance. Any later
        state must already have float64 flow columns.
        """
        data = inventory.data
        sku_column = inventory.sku_column
        columns = tuple(data.columns)
        # Exactly the state schema: every transition rebuilds the state with
        # the constructor, which drops any other column.
        if len(columns) != len(_SPECIAL_COLUMNS) + 2 or set(columns) != _SPECIAL_COLUMNS | {
            sku_column, "safety_stock"
        }:
            return None
        checked = ("on_hand", "backorders") if opening else _FLOAT_COLUMNS
        if any(data[name].dtype != _FLOAT64 for name in checked):
            return None
        if not opening and data["is_review_period"].dtype != _BOOL:
            return None
        period = data["period"]
        if period.dtype not in _PERIOD_DTYPES:
            return None
        dates = data["date"]
        if dates.dtype.kind != "M" or not isinstance(dates.dtype, np.dtype):
            return None
        n = len(data)
        max_lead_time = inventory.max_lead_time
        if max_lead_time:
            pipeline = _uniform_float_pipelines(data["in_transit"], max_lead_time)
            if pipeline is None:
                return None
            pipeline_dtype = _FLOAT64
        else:
            items = data["in_transit"].tolist()
            dtypes = {item.dtype for item in items if type(item) is np.ndarray and item.shape == (0,)}
            if len(dtypes) != 1 or any(
                type(item) is not np.ndarray or item.shape != (0,) for item in items
            ):
                return None
            pipeline_dtype = dtypes.pop()
            pipeline = np.zeros((n, 0), dtype=pipeline_dtype)
        period_values = period.to_numpy()
        if n == 0 or (period_values != period_values[0]).any():
            return None
        review = data["is_review_period"].to_numpy()
        is_review = bool(review[0]) if not opening else False
        if not opening and bool(review.any()) != bool(review.all()):
            return None
        target = data["target_level"]
        constants = {
            name: data[name]
            for name in columns
            if name not in _SPECIAL_COLUMNS
        }
        if target.dtype.kind in "fiu" and isinstance(target.dtype, np.dtype):
            target_dtype = target.dtype
            target_values = target.to_numpy(copy=True)
        else:
            target_dtype = None
            target_values = None
            constants["target_level"] = target
        schema = StateSchema(
            columns=columns, index=data.index, sku_column=sku_column,
            constants=constants, target_dtype=target_dtype,
            period_dtype=period.dtype, date_dtype=dates.dtype,
            pipeline_dtype=pipeline_dtype, max_lead_time=max_lead_time,
        )
        if hint is not None and hint.same_as(schema):
            schema = hint
        latest = {
            name: data[name].to_numpy(copy=True) if not opening else None
            for name in LATEST_COLUMNS
        }
        return cls(
            schema=schema,
            on_hand=data["on_hand"].to_numpy(dtype=_FLOAT64, copy=True),
            backorders=data["backorders"].to_numpy(dtype=_FLOAT64, copy=True),
            latest=latest,
            target=target_values,
            pipeline=pipeline,
            period=period_values[0],
            date=pd.Timestamp(dates.iloc[0]),
            is_review=is_review,
            has_stockout=inventory.has_stockout,
            has_backorder=inventory.has_backorder,
            source=inventory if opening else None,
        )

    # ---- transitions ---------------------------------------------------------

    def ready(self, allow_backorders: bool) -> bool:
        """Cheap form of ``_validate_ready_state`` for the tracked arrays.

        Constant columns cannot change while a state is array-backed, so they
        keep the validity established before the run. False means "use the
        DataFrame path", which raises the exact validation error.
        """
        arrays = [self.on_hand, self.backorders, self.pipeline]
        arrays.extend(value for value in self.latest.values() if value is not None)
        for values in arrays:
            if not np.isfinite(values).all() or (values < 0).any():
                return False
        if not allow_backorders and (self.backorders > 0).any():
            return False
        return not ((self.on_hand > 0) & (self.backorders > 0)).any()

    def advanced(self, offset, *, is_review: bool, allow_backorders: bool) -> "ArrayState":
        """``InventoryStateDataFrame.advance_period`` on arrays."""
        n = self.schema.n
        if self.schema.max_lead_time:
            received = self.pipeline[:, 0].copy()
            pipeline = np.zeros_like(self.pipeline)
            pipeline[:, :-1] = self.pipeline[:, 1:]
        else:
            received = np.zeros(n)
            pipeline = self.pipeline
        if allow_backorders:
            cleared = np.minimum(self.backorders, received)
            backorders = self.backorders - cleared
            on_hand = self.on_hand + (received - cleared)
        else:
            cleared = np.zeros(n)
            backorders = self.backorders - 0.0
            on_hand = self.on_hand + (received - 0.0)
        latest = {name: np.zeros(n) for name in LATEST_COLUMNS}
        latest["latest_received"] = received
        latest["latest_backorders_fulfilled"] = cleared
        date = self.date + offset
        return ArrayState(
            schema=self.schema.with_date_dtype(_assigned_date_dtype(date)),
            on_hand=on_hand,
            backorders=backorders,
            latest=latest,
            target=self.target,
            pipeline=pipeline,
            period=self.period + self.schema.period_dtype.type(1),
            date=date,
            is_review=is_review,
        )

    def fulfilled(self, demand: np.ndarray, *, allow_backorders: bool) -> "ArrayState":
        """``InventoryStateDataFrame.fulfill_demand`` on arrays."""
        fulfilled = np.minimum(demand, self.on_hand)
        shortage = demand - fulfilled
        on_hand = self.on_hand - fulfilled
        backorders = self.backorders + shortage if allow_backorders else self.backorders
        latest = dict(self.latest)
        latest["latest_incoming_demand"] = demand
        latest["latest_fulfilled"] = fulfilled
        latest["latest_shortage"] = shortage
        return ArrayState(
            schema=self.schema,
            on_hand=on_hand,
            backorders=backorders,
            latest=latest,
            target=self.target,
            pipeline=self.pipeline,
            period=self.period,
            date=self.date,
            is_review=self.is_review,
            has_stockout=bool((shortage > 0).any()),
            has_backorder=bool((backorders > 0).any()),
        )

    def inventory_positions(self) -> dict:
        """``inventory_position()`` keyed by SKU, as the engine captures it."""
        positions = (self.on_hand + self.on_order()) - self.backorders
        return dict(zip(self.schema.sku_list(), positions.tolist()))

    def with_order(self, orders, *, allow_backorders: bool) -> Optional["ArrayState"]:
        """``update_inventory_with_orders`` on arrays, or None.

        None means a precondition is not plainly met (an unexpected dtype, an
        unknown SKU, inconsistent timing, ...). The caller then runs the real
        primitive, which either applies the order or raises its exact error.
        """
        schema = self.schema
        lead_time = orders.lead_time
        if (
            type(lead_time) is not int
            or not 0 <= lead_time <= schema.max_lead_time
            or orders.sku_column != schema.sku_column
            or schema.target_dtype is None
        ):
            return None
        frame = orders.data
        if schema.sku_column not in frame.columns:
            return None
        ids = frame[schema.sku_column]
        quantity = frame["order_quantity"]
        target = frame["target_level"]
        if (
            ids.dtype != schema.sku.dtype
            or quantity.dtype not in _PERIOD_DTYPES
            or target.dtype != _FLOAT64
        ):
            return None
        positions = schema.sku_index().get_indexer(ids)
        if (positions < 0).any():
            return None
        quantity = quantity.to_numpy(dtype=_FLOAT64)
        if np.isnan(quantity).any():
            return None
        positive = quantity > 0
        if positive.any():
            timing = []
            for name in ("order_period", "expected_delivery_period"):
                values = frame[name]
                if values.dtype.kind not in "fiu" or not isinstance(values.dtype, np.dtype):
                    return None
                timing.append(values.to_numpy(dtype=_FLOAT64)[positive])
            order_period, delivery = timing
            if (
                np.isnan(order_period).any()
                or np.isnan(delivery).any()
                or not (order_period == int(self.period)).all()
                or not (delivery == order_period + lead_time).all()
            ):
                return None
        n = schema.n
        # Left merge onto the state rows: absent SKUs order nothing and keep
        # their target (``fillna``).
        aligned = np.zeros(n)
        aligned[positions] = quantity
        order_target = np.full(n, np.nan)
        order_target[positions] = target.to_numpy()
        current_target = self.target.astype(_FLOAT64, copy=False)
        new_target = np.where(np.isnan(order_target), current_target, order_target)
        latest = dict(self.latest)
        latest["latest_order"] = self.latest["latest_order"] + aligned
        on_hand, backorders, pipeline = self.on_hand, self.backorders, self.pipeline
        if lead_time == 0:
            if allow_backorders:
                cleared = np.minimum(backorders, aligned)
                backorders = backorders - cleared
                on_hand = on_hand + (aligned - cleared)
                latest["latest_backorders_fulfilled"] = (
                    latest["latest_backorders_fulfilled"] + cleared
                )
            else:
                backorders = backorders - 0.0
                on_hand = on_hand + (aligned - 0.0)
                latest["latest_backorders_fulfilled"] = latest["latest_backorders_fulfilled"] + 0.0
            latest["latest_received"] = latest["latest_received"] + aligned
        else:
            pipeline = pipeline.copy()
            pipeline[:, lead_time - 1] += aligned
        return ArrayState(
            schema=schema.after_order(),
            on_hand=on_hand,
            backorders=backorders,
            latest=latest,
            target=new_target,
            pipeline=pipeline,
            period=self.period,
            date=self.date,
            is_review=self.is_review,
        )

    def with_on_hand(self, on_hand: np.ndarray) -> "ArrayState":
        return ArrayState(
            schema=self.schema,
            on_hand=on_hand,
            backorders=self.backorders,
            latest=self.latest,
            target=self.target,
            pipeline=self.pipeline,
            period=self.period,
            date=self.date,
            is_review=self.is_review,
            has_stockout=self.has_stockout,
            has_backorder=self.has_backorder,
            source=self.source,
        )

    def on_order(self) -> np.ndarray:
        if self._on_order is None:
            self._on_order = self.pipeline.sum(axis=1).astype(_FLOAT64, copy=False)
        return self._on_order

    # ---- DataFrame boundary --------------------------------------------------

    def frame(self, *, index=None) -> pd.DataFrame:
        """Build the state frame the pandas path would hold at this point."""
        schema = self.schema
        n = schema.n
        pipeline = self.pipeline.copy()
        in_transit = np.empty(n, dtype=object)
        for position, row in enumerate(pipeline):
            in_transit[position] = row
        columns = {}
        for name in schema.columns:
            if name == "on_hand":
                columns[name] = self.on_hand
            elif name == "backorders":
                columns[name] = self.backorders
            elif name in self.latest:
                columns[name] = self.latest[name]
            elif name == "target_level" and schema.target_dtype is not None:
                columns[name] = self.target
            elif name == "period":
                columns[name] = np.full(n, self.period, dtype=schema.period_dtype)
            elif name == "date":
                columns[name] = _date_column([self.date], n, schema.date_dtype)
            elif name == "is_review_period":
                columns[name] = np.full(n, self.is_review, dtype=_BOOL)
            elif name == "in_transit":
                columns[name] = in_transit
            else:
                columns[name] = schema.constants[name].array
        return pd.DataFrame(columns, index=schema.index if index is None else index)

    def to_inventory(
        self,
        history: List[pd.DataFrame],
        *,
        max_lead_time: int,
        allow_backorders: bool,
    ) -> InventoryStateDataFrame:
        """DataFrame-backed state equal to the one the pandas path holds."""
        if self.source is not None:
            # An opening state: its flow columns predate the first advance,
            # so start from the source frame. Only on_hand can have changed
            # (engine-owned expiry before the first advance).
            state = copy.deepcopy(self.source)
            state.data["on_hand"] = self.on_hand.copy()
            state._history = history
            return state
        return InventoryStateDataFrame._from_trusted(
            self.frame(),
            sku_column=self.schema.sku_column,
            max_lead_time=max_lead_time,
            allow_backorders=allow_backorders,
            history=history,
            start_date=self.date,
            has_stockout=self.has_stockout,
            has_backorder=self.has_backorder,
        )

    def map_by_sku(self, mapping: Mapping, fill: float) -> np.ndarray:
        """``state[sku].map(mapping).fillna(fill)`` as a float array."""
        if not mapping:
            return np.full(self.schema.n, fill)
        return self.schema.sku.map(mapping).fillna(fill).to_numpy(dtype=_FLOAT64)


# ============================================================================
# DEMAND
# ============================================================================

class DemandPath:
    """Validated demand aligned to the state SKU order, one row per period."""

    def __init__(self, demand_data: pd.DataFrame, sku_column: str, sku_index: pd.Index,
                 n_periods: int):
        self._data = demand_data
        periods = demand_data["period"].to_numpy()
        order = np.argsort(periods, kind="stable")
        first_rows = order[np.searchsorted(periods[order], np.arange(n_periods))]
        # The first row of each period in input order, as the pandas path
        # reads ``demand_df['date'].iloc[0]`` after filtering by period.
        self.dates = list(demand_data["date"].iloc[first_rows]) if n_periods else []
        positions = sku_index.get_indexer(demand_data[sku_column])
        matrix = np.full((n_periods, len(sku_index)), np.nan)
        if (positions >= 0).all():
            matrix[periods, positions] = demand_data["y"].to_numpy(dtype=_FLOAT64)
        # Preflight proves the grid complete; keep a defensive fallback.
        self.matrix = matrix if not np.isnan(matrix).any() else None
        self._sku_index = sku_index

    def aligned(self, schema: StateSchema) -> bool:
        """Whether matrix columns follow this schema's SKU row order."""
        cached = schema._demand_alignment
        if cached is None or cached[0] is not self:
            aligned = self.matrix is not None and schema.sku_index().equals(self._sku_index)
            cached = schema._demand_alignment = (self, aligned)
        return cached[1]

    def frame(self, period: int) -> pd.DataFrame:
        return self._data[self._data["period"] == period]

    def values(self, period: int) -> Optional[np.ndarray]:
        return None if self.matrix is None else self.matrix[period]


# ============================================================================
# RUN LOG AND RESULT ASSEMBLY
# ============================================================================

class FastRecord:
    """One array-backed period: its opening and completed states plus events."""

    __slots__ = (
        "demand_period", "run_window", "policy_name", "allow_backorders",
        "start", "final", "order_event_count", "line_counts", "squared_sums",
        "audit", "expired", "adjustments",
    )

    def __init__(self, **values):
        for name, value in values.items():
            setattr(self, name, value)

    def uid(self) -> pd.Series:
        if self.audit is not None:
            return self.audit["unique_id"]
        return self.start.schema.sku_event()


class FrameRecord:
    """One period executed on the pandas path."""

    __slots__ = ("event", "snapshot")

    def __init__(self, event: pd.DataFrame, snapshot: pd.DataFrame):
        self.event = event
        self.snapshot = snapshot


AUDIT_COLUMNS = (
    "decision_inventory_position",
    "requested_order_quantity",
    "callback_adjusted_order_quantity",
    "callback_adjustment_units",
    "constrained_order_quantity",
    "constraint_adjustment_units",
    "constraint_binding_flag",
    "capacity_violation_flag",
    "binding_constraints",
)


class RunLog:
    """Append-only record of completed periods."""

    def __init__(self):
        self.records: List[object] = []

    def __len__(self) -> int:
        return len(self.records)

    def history_view(self, upto: int) -> _DeferredHistory:
        """History list a state would carry after ``upto`` completed periods."""
        return _DeferredHistory(lambda: self.snapshot_frames(upto))

    def snapshot_frames(self, upto: int) -> List[pd.DataFrame]:
        frames = []
        for record in self.records[:upto]:
            if isinstance(record, FrameRecord):
                frames.append(record.snapshot.copy(deep=True))
            else:
                frames.append(record.final.frame())
        return frames

    # ---- history ----------------------------------------------------------

    def history(self) -> pd.DataFrame:
        pieces = []
        for kind, group in self._groups(lambda record: record.final.schema):
            if kind is None:
                pieces.extend(record.snapshot for record in group)
            else:
                pieces.append(_history_block(group, kind))
        if not pieces:
            return pd.DataFrame()
        return pd.concat(pieces, ignore_index=True)

    # ---- events -----------------------------------------------------------

    def event_frame(self) -> pd.DataFrame:
        pieces = []
        for kind, group in self._groups(_event_signature):
            if kind is None:
                pieces.extend(record.event for record in group)
            else:
                pieces.append(_event_block(group))
        return pd.concat(pieces, ignore_index=True)

    def _groups(self, key: Callable):
        """Runs of consecutive records sharing a key; frame records key None."""
        group: List[object] = []
        group_key = None
        for record in self.records:
            record_key = None if isinstance(record, FrameRecord) else key(record)
            if group and (
                record_key is None or group_key is None or not _same_key(record_key, group_key)
            ):
                yield group_key, group
                group = []
            group.append(record)
            group_key = record_key
        if group:
            yield group_key, group


def _same_key(left, right) -> bool:
    if isinstance(left, StateSchema):
        return left is right
    return left == right


def _concat_pieces(pieces: list, n: int):
    """Concatenate per-period column pieces that already share one dtype.

    A piece is a NumPy array or a pandas Series. Repeated identical pieces
    (a constant column) are tiled instead of concatenated.
    """
    first = pieces[0]
    if all(piece is first for piece in pieces):
        take = np.tile(np.arange(n), len(pieces))
        if isinstance(first, pd.Series):
            return first.array.take(take)
        return first[take]
    if all(isinstance(piece, np.ndarray) for piece in pieces):
        return np.concatenate(pieces)
    series = [piece if isinstance(piece, pd.Series) else pd.Series(piece) for piece in pieces]
    return pd.concat(series, ignore_index=True).array


def _history_block(records: List[FastRecord], schema: StateSchema) -> pd.DataFrame:
    n = schema.n
    k = len(records)
    finals = [record.final for record in records]
    columns = {}
    for name in schema.columns:
        if name == "on_hand":
            columns[name] = np.concatenate([state.on_hand for state in finals])
        elif name == "backorders":
            columns[name] = np.concatenate([state.backorders for state in finals])
        elif name in LATEST_COLUMNS:
            columns[name] = np.concatenate([state.latest[name] for state in finals])
        elif name == "target_level" and schema.target_dtype is not None:
            columns[name] = np.concatenate([state.target for state in finals])
        elif name == "period":
            columns[name] = _repeat_values([state.period for state in finals], n, schema.period_dtype)
        elif name == "date":
            columns[name] = _date_column([state.date for state in finals], n, schema.date_dtype)
        elif name == "is_review_period":
            columns[name] = _repeat_values([state.is_review for state in finals], n, _BOOL)
        elif name == "in_transit":
            stacked = np.concatenate([state.pipeline for state in finals])
            in_transit = np.empty(n * k, dtype=object)
            for position, row in enumerate(stacked):
                in_transit[position] = row
            columns[name] = in_transit
        else:
            columns[name] = _concat_pieces([schema.constants[name]] * k, n)
    return pd.DataFrame(columns)


def _audit_dtypes(record: FastRecord):
    if record.audit is None:
        return _default_audit_dtypes()
    return tuple(record.audit[name].dtype for name in AUDIT_COLUMNS)


def _event_signature(record: FastRecord):
    """Per-period event dtypes that are not fixed by the canonical schema."""
    start, final = record.start.schema, record.final.schema
    target = (
        final.target_dtype
        if final.target_dtype is not None
        else final.numeric("target_level").dtype
    )
    return (
        record.uid().dtype,
        final.period_dtype,
        target,
        start.numeric("safety_stock").dtype,
        type(record.policy_name),
        _audit_dtypes(record),
        _FLOAT64 if record.expired is None else record.expired.dtype,
        _FLOAT64 if record.adjustments is None else record.adjustments.dtype,
    )


def _event_block(records: List[FastRecord]) -> pd.DataFrame:
    """Event rows for consecutive array-backed periods with equal dtypes.

    Mirrors ``_build_period_event_frame`` plus the engine's audit, expiry and
    adjustment columns, evaluated once for the whole block.
    """
    first = records[0]
    n = first.start.schema.n
    k = len(records)
    size = n * k
    starts = [record.start for record in records]
    finals = [record.final for record in records]

    def cat(values):
        return np.concatenate(values) if k > 1 else values[0]

    shortage = cat([state.latest["latest_shortage"] for state in finals])
    ending_on_hand = cat([state.on_hand for state in finals])
    backorders_end = cat([state.backorders for state in finals])
    on_order_end = cat([state.on_order() for state in finals])
    order_quantity = cat([state.latest["latest_order"] for state in finals])
    allow = np.repeat(np.array([record.allow_backorders for record in records], dtype=_BOOL), n)
    zeros = np.zeros(size)
    is_review = _repeat_values([state.is_review for state in finals], n, _BOOL)

    order_event_count = np.zeros(size, dtype=scalar_column_dtype(0))
    line_counts = np.zeros(size, dtype=_LINE_COUNT_DTYPE)
    squared_sums = np.zeros(size)
    for block_position, record in enumerate(records):
        offset = block_position * n
        order_event_count[offset] = record.order_event_count
        if record.line_counts:
            positions = record.start.schema.sku_index().get_indexer(list(record.line_counts))
            for position, count in zip(positions, record.line_counts.values()):
                if position >= 0:
                    line_counts[offset + position] = count
        if record.squared_sums:
            positions = record.start.schema.sku_index().get_indexer(list(record.squared_sums))
            for position, value in zip(positions, record.squared_sums.values()):
                if position >= 0:
                    squared_sums[offset + position] = value

    final_schema = finals[0].schema
    target_level = _concat_pieces(
        [
            state.target if state.schema.target_dtype is not None
            else state.schema.numeric("target_level")
            for state in finals
        ],
        n,
    )
    policy_names = np.repeat(np.array([record.policy_name for record in records], dtype=object), n)
    windows = np.repeat(np.array([record.run_window for record in records], dtype=object), n)

    columns = {
        "unique_id": _concat_pieces([record.uid() for record in records], n),
        "event_type": _column_from_objects(
            np.full(size, "period", dtype=object), scalar_column_dtype("period")
        ),
        "demand_period": pd.array(
            np.repeat(np.array([record.demand_period for record in records]), n),
            dtype="Int64",
        ),
        "period": _repeat_values([state.period for state in finals], n, final_schema.period_dtype),
        "date": pd.Series(
            _date_column([state.date for state in finals], n, final_schema.date_dtype)
        ).astype(_EVENT_DATE_DTYPE).array,
        "policy": _column_from_objects(policy_names, scalar_column_dtype(first.policy_name)),
        "allow_backorders": allow,
        "is_review_period": is_review,
        "decision_flag": is_review.copy(),
        "starting_on_hand": cat([state.on_hand for state in starts]),
        "starting_backorders": cat([state.backorders for state in starts]),
        "starting_on_order": cat([state.on_order() for state in starts]),
        "received_units": cat([state.latest["latest_received"] for state in finals]),
        "demand": cat([state.latest["latest_incoming_demand"] for state in finals]),
        "fulfilled_units": cat([state.latest["latest_fulfilled"] for state in finals]),
        "backorders_fulfilled": cat(
            [state.latest["latest_backorders_fulfilled"] for state in finals]
        ),
        "shortage_units": shortage,
        "lost_sales_units": np.where(allow, zeros, shortage),
        "backorder_increment": np.where(allow, shortage, zeros),
        "ending_on_hand": ending_on_hand,
        "backorders_end": backorders_end,
        "on_order_end": on_order_end,
        "inventory_position_end": (ending_on_hand + on_order_end) - backorders_end,
        "order_quantity": order_quantity,
        "order_event_count": order_event_count,
        "sku_order_line_count": line_counts,
        "order_line_quantity_squared_sum": squared_sums,
        "expired_units": _optional_pieces([record.expired for record in records], n),
        "inventory_adjustment_units": _optional_pieces(
            [record.adjustments for record in records], n
        ),
        "target_level": target_level,
        "safety_stock": _concat_pieces(
            [state.schema.numeric("safety_stock") for state in starts], n
        ),
        "stockout_flag": shortage > 0,
        "backorder_flag": backorders_end > 0,
        "run_window": _column_from_objects(windows, scalar_column_dtype(first.run_window)),
    }
    for name in AUDIT_COLUMNS:
        columns[name] = _concat_pieces(
            [
                record.audit[name] if record.audit is not None
                else audit_default(name, record.final.latest["latest_order"])
                for record in records
            ],
            n,
        )
    return pd.DataFrame(columns)


def _optional_pieces(pieces: list, n: int):
    """Mapped per-SKU quantities; None stands for an empty mapping (zeros)."""
    arrays = [np.zeros(n) if piece is None else piece for piece in pieces]
    return np.concatenate(arrays) if len(arrays) > 1 else arrays[0]


def audit_default(name: str, order_quantity: np.ndarray):
    """Audit column of a period without an order decision."""
    n = len(order_quantity)
    if name in ("requested_order_quantity", "callback_adjusted_order_quantity",
                "constrained_order_quantity"):
        return order_quantity
    if name == "decision_inventory_position":
        return np.full(n, np.nan)
    if name in ("callback_adjustment_units", "constraint_adjustment_units"):
        return np.zeros(n)
    if name in ("constraint_binding_flag", "capacity_violation_flag"):
        return np.zeros(n, dtype=_BOOL)
    return pd.Series(np.full(n, "", dtype=object), dtype=scalar_column_dtype(""))


_DEFAULT_AUDIT_DTYPES = None


def _default_audit_dtypes():
    global _DEFAULT_AUDIT_DTYPES
    if _DEFAULT_AUDIT_DTYPES is None:
        _DEFAULT_AUDIT_DTYPES = tuple(
            audit_default(name, np.zeros(1)).dtype for name in AUDIT_COLUMNS
        )
    return _DEFAULT_AUDIT_DTYPES


def assert_flow_balance(record: FastRecord) -> None:
    """``_assert_event_flow_balance`` for one array-backed period."""
    start, final = record.start, record.final
    latest = final.latest
    n = final.schema.n
    received = latest["latest_received"]
    backorders_fulfilled = latest["latest_backorders_fulfilled"]
    fulfilled = latest["latest_fulfilled"]
    shortage = latest["latest_shortage"]
    expired = np.zeros(n) if record.expired is None else record.expired
    adjustment = np.zeros(n) if record.adjustments is None else record.adjustments
    increment = shortage if record.allow_backorders else np.zeros(n)
    starting_on_order = start.on_order()
    physical_terms = [start.on_hand, received, backorders_fulfilled, fulfilled, expired, adjustment]
    physical_expected = (
        start.on_hand + received - backorders_fulfilled - fulfilled - expired + adjustment
    )
    backlog_terms = [start.backorders, increment, backorders_fulfilled]
    backlog_expected = start.backorders + increment - backorders_fulfilled
    pipeline_terms = [starting_on_order, received, latest["latest_order"]]
    pipeline_expected = starting_on_order - received + latest["latest_order"]
    checks = [
        ("physical inventory", physical_expected, final.on_hand, physical_terms),
        ("backlog", backlog_expected, final.backorders, backlog_terms),
        ("pipeline", pipeline_expected, final.on_order(), pipeline_terms),
    ]
    for name, expected, actual, terms in checks:
        magnitude = 0
        for term in [*terms, actual]:
            magnitude = magnitude + np.abs(term)
        tolerance = 1e-9 + 1e-12 * magnitude
        valid = np.abs(expected - actual) <= tolerance
        if not valid.all():
            position = int(np.flatnonzero(~valid)[0])
            raise AssertionError(
                f"{name} flow balance failed for SKU {record.uid().iloc[position]} at "
                f"period {final.period}"
            )
