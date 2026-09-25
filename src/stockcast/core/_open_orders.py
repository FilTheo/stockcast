"""Order-level record of stock on order (engine and state private).

``InventoryStateDataFrame.in_transit`` stores, per SKU, the quantity due at
each future period offset. That view is the accounting authority and is left
exactly as it was. The open-order book is kept in lockstep with it and records
*which orders* make up those quantities: order line identity, supplier,
order period, ordered quantity and each scheduled delivery.

Invariant (validated): for every SKU and pipeline slot ``i``, the remaining
quantity of the book's deliveries due at ``period + 1 + i`` equals
``in_transit[i]``. A delivery is received exactly at its due period, so a
delivery due in the past cannot exist.

The book is immutable by convention: every transition returns a new object.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

_FIELDS = (
    "order_id", "sku", "supplier", "order_period", "ordered", "due", "quantity", "source",
    "scheduled",
)
_DTYPES = {
    "order_id": np.dtype("int64"),
    "sku": np.dtype(object),
    "supplier": np.dtype(object),
    "order_period": np.dtype("float64"),
    "ordered": np.dtype("float64"),
    "due": np.dtype("int64"),
    "quantity": np.dtype("float64"),
    "source": np.dtype(object),
    # Due period set when the delivery was scheduled; differs from ``due``
    # only after a supplier ``DeliveryOutcome`` delayed it.
    "scheduled": np.dtype("int64"),
}

OPENING_SOURCE = "opening"
PLACED_SOURCE = "placed"
# Part of a delivery that a supplier DeliveryOutcome moved to a later period.
DELAYED_SOURCE = "delayed"


def _objects(values) -> pd.Series:
    """Values as an object Series, so identifiers and None are kept as given
    (pandas 3 would otherwise infer a string dtype and turn None into NaN)."""
    return pd.Series(_object_array(values), dtype=object)


def _object_array(values) -> np.ndarray:
    """1-D object array holding exactly the given values (tuples stay scalars)."""
    if isinstance(values, np.ndarray) and values.dtype == object and values.ndim == 1:
        return values
    values = list(values)
    array = np.empty(len(values), dtype=object)
    if not any(issubclass(kind, (tuple, list, np.ndarray)) for kind in set(map(type, values))):
        array[:] = values
        return array
    for position, value in enumerate(values):
        array[position] = value
    return array


class _Deliveries:
    """Parallel arrays, one entry per scheduled delivery of an order line."""

    __slots__ = _FIELDS

    def __init__(self, **arrays):
        for name in _FIELDS:
            setattr(self, name, arrays[name])

    @classmethod
    def empty(cls) -> "_Deliveries":
        return cls(**{name: np.empty(0, dtype=dtype) for name, dtype in _DTYPES.items()})

    @classmethod
    def build(cls, *, order_id, sku, supplier, order_period, ordered, due, quantity,
              source, scheduled=None) -> "_Deliveries":
        due = np.asarray(due, dtype=np.int64)
        return cls(
            order_id=np.asarray(order_id, dtype=np.int64),
            sku=_object_array(sku),
            supplier=_object_array(supplier),
            order_period=np.asarray(order_period, dtype=np.float64),
            ordered=np.asarray(ordered, dtype=np.float64),
            due=due,
            quantity=np.asarray(quantity, dtype=np.float64),
            source=_object_array(source),
            scheduled=due if scheduled is None else np.asarray(scheduled, dtype=np.int64),
        )

    def __len__(self) -> int:
        return len(self.order_id)

    def select(self, mask: np.ndarray) -> "_Deliveries":
        return _Deliveries(**{name: getattr(self, name)[mask] for name in _FIELDS})

    def concat(self, other: "_Deliveries") -> "_Deliveries":
        if not len(other):
            return self
        if not len(self):
            return other
        return _Deliveries(**{
            name: np.concatenate([getattr(self, name), getattr(other, name)])
            for name in _FIELDS
        })

    @staticmethod
    def concat_all(parts: Sequence["_Deliveries"]) -> "_Deliveries":
        parts = [part for part in parts if len(part)]
        if not parts:
            return _Deliveries.empty()
        if len(parts) == 1:
            return parts[0]
        return _Deliveries(**{
            name: np.concatenate([getattr(part, name) for part in parts]) for name in _FIELDS
        })


class _OpenOrderBook:
    """Open deliveries plus the deliveries placed since the latest advance."""

    __slots__ = ("open", "placed", "next_id", "declared", "checked")

    def __init__(self, open: _Deliveries, placed: _Deliveries, next_id: int, declared: bool,
                 checked: bool):
        self.open = open
        self.placed = placed
        self.next_id = next_id
        # True when the caller declared the opening orders explicitly
        # (``with_open_orders``); False when derived from ``in_transit``.
        self.declared = declared
        # True when the caller opted into the order-level API (declared
        # orders or ``place_order_lines``): state validation then checks the
        # book against in_transit. A book the engine attributes to a plain
        # in_transit pipeline is checked once, on the final state of a run,
        # so code that only knows in_transit behaves exactly as before.
        self.checked = checked

    def _with(self, open: _Deliveries, placed: _Deliveries, next_id: int) -> "_OpenOrderBook":
        return _OpenOrderBook(open, placed, next_id, self.declared, self.checked)

    def as_checked(self) -> "_OpenOrderBook":
        if self.checked:
            return self
        return _OpenOrderBook(self.open, self.placed, self.next_id, self.declared, True)

    # Transitions never modify a book or its arrays in place, so copies
    # (for example the defensive state copy a policy receives) can share it.
    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    # ---- construction ----------------------------------------------------

    @classmethod
    def from_pipelines(cls, skus: list, pipelines: np.ndarray, period: int) -> "_OpenOrderBook":
        """Attribute each positive pipeline slot to one opening delivery.

        Nothing is known about these orders beyond SKU, quantity and due
        period, so order period and supplier stay missing (never invented).
        """
        rows, slots = np.nonzero(pipelines > 0)
        count = len(rows)
        quantity = pipelines[rows, slots].astype(np.float64)
        deliveries = _Deliveries.build(
            order_id=np.arange(count),
            sku=[skus[row] for row in rows],
            supplier=[None] * count,
            order_period=np.full(count, np.nan),
            ordered=quantity,
            due=period + 1 + slots,
            quantity=quantity,
            source=[OPENING_SOURCE] * count,
        )
        return cls(deliveries, _Deliveries.empty(), count, declared=False, checked=False)

    @classmethod
    def declared_opening(cls, deliveries: _Deliveries) -> "_OpenOrderBook":
        next_id = int(deliveries.order_id.max()) + 1 if len(deliveries) else 0
        return cls(deliveries, _Deliveries.empty(), next_id, declared=True, checked=True)

    # ---- transitions -----------------------------------------------------

    def advanced(self, new_period: int) -> "_OpenOrderBook":
        """Receive every delivery due at ``new_period``; reset placements."""
        keep = self.open.due != new_period
        return self._with(
            self.open.select(keep) if not keep.all() else self.open,
            _Deliveries.empty(),
            self.next_id,
        )

    def placed_lines(self, *, sku, supplier, order_period: int, ordered, due, quantity,
                     line) -> "_OpenOrderBook":
        """Record new order lines; ``line`` numbers them 0..k-1 in this call.

        A delivery due at ``order_period`` is received immediately, so it is
        recorded as placed but never enters the open book.
        """
        line = np.asarray(line, dtype=np.int64)
        count = len(line)
        if not count:
            return self
        deliveries = _Deliveries.build(
            order_id=self.next_id + line,
            sku=sku,
            supplier=supplier,
            order_period=np.full(count, float(order_period)),
            ordered=ordered,
            due=due,
            quantity=quantity,
            source=[PLACED_SOURCE] * count,
        )
        future = deliveries.due > order_period
        return self._with(
            self.open.concat(deliveries.select(future)) if future.any() else self.open,
            self.placed.concat(deliveries),
            self.next_id + int(line.max()) + 1,
        )

    def resolved(self, positions: np.ndarray, received: np.ndarray, delayed: np.ndarray,
                 delay: np.ndarray) -> tuple:
        """Apply supplier delivery outcomes to open deliveries due next.

        ``positions`` index ``self.open``. Each delivery keeps only its
        ``received`` quantity at its due period; a positive ``delayed``
        quantity becomes a new delivery of the same order line, due
        ``delay`` periods later, with source ``"delayed"``. Returns the new
        book and the rescheduled deliveries.
        """
        quantity = self.open.quantity.copy()
        quantity[positions] = received
        keep = np.ones(len(self.open), dtype=bool)
        keep[positions] = received > 0
        current = _Deliveries(**{
            name: (quantity if name == "quantity" else getattr(self.open, name))
            for name in _FIELDS
        }).select(keep)
        later = delayed > 0
        rescheduled = self.open.select(positions[later])
        rescheduled = _Deliveries(**{
            name: getattr(rescheduled, name) for name in _FIELDS
            if name not in ("due", "quantity", "source")
        }, due=rescheduled.due + delay[later], quantity=delayed[later],
            source=_object_array([DELAYED_SOURCE] * int(later.sum())))
        return self._with(current.concat(rescheduled), self.placed, self.next_id), rescheduled

    # ---- views -------------------------------------------------------------

    def pipeline_matrix(self, sku_index: pd.Index, period: int, max_lead_time: int) -> Optional[np.ndarray]:
        """Remaining quantity by SKU row and pipeline slot, or None if impossible.

        Quantities are accumulated in placement order, as the pipeline itself is.
        """
        matrix = np.zeros((len(sku_index), max_lead_time))
        if not len(self.open):
            return matrix
        rows = sku_index.get_indexer(pd.Index(self.open.sku, dtype=object))
        slots = self.open.due - period - 1
        if (rows < 0).any() or (slots < 0).any() or (slots >= max_lead_time).any():
            return None
        np.add.at(matrix, (rows, slots), self.open.quantity)
        return matrix

    def open_orders_frame(self, sku_column: str) -> pd.DataFrame:
        """One row per open order line."""
        deliveries = self.open
        columns = [
            "order_id", sku_column, "supplier_id", "source", "order_period",
            "ordered_quantity", "remaining_quantity", "due_period", "final_due_period",
        ]
        if not len(deliveries):
            return _empty_frame(columns, {
                "order_id": "int64", "order_period": "float64", "ordered_quantity": "float64",
                "remaining_quantity": "float64", "due_period": "int64",
                "final_due_period": "int64",
            })
        ids, first, inverse = np.unique(deliveries.order_id, return_index=True, return_inverse=True)
        remaining = np.zeros(len(ids))
        np.add.at(remaining, inverse, deliveries.quantity)
        due_first = np.full(len(ids), np.iinfo(np.int64).max)
        due_last = np.full(len(ids), np.iinfo(np.int64).min)
        np.minimum.at(due_first, inverse, deliveries.due)
        np.maximum.at(due_last, inverse, deliveries.due)
        return pd.DataFrame({
            "order_id": ids,
            sku_column: _objects(deliveries.sku[first]),
            "supplier_id": _objects(deliveries.supplier[first]),
            "source": _objects(deliveries.source[first]),
            "order_period": deliveries.order_period[first],
            "ordered_quantity": deliveries.ordered[first],
            "remaining_quantity": remaining,
            "due_period": due_first,
            "final_due_period": due_last,
        })[columns]

    def scheduled_receipts_frame(self, sku_column: str) -> pd.DataFrame:
        """One row per open scheduled delivery."""
        deliveries = self.open
        return pd.DataFrame({
            "order_id": deliveries.order_id,
            sku_column: _objects(deliveries.sku),
            "supplier_id": _objects(deliveries.supplier),
            "due_period": deliveries.due,
            "quantity": deliveries.quantity,
        })


def _empty_frame(columns, dtypes) -> pd.DataFrame:
    return pd.DataFrame({
        column: pd.Series(dtype=dtypes.get(column, object)) for column in columns
    })


def pipeline_mismatch(book: _OpenOrderBook, sku_index: pd.Index, period: int,
                      max_lead_time: int, pipelines: np.ndarray) -> Optional[str]:
    """Describe the first disagreement between book and pipeline, else None."""
    expected = book.pipeline_matrix(sku_index, period, max_lead_time)
    if expected is None:
        return (
            "open orders must belong to inventory SKUs and be due within "
            "period + 1 .. period + max_lead_time"
        )
    tolerance = 1e-9 + 1e-12 * (np.abs(expected) + np.abs(pipelines))
    bad = np.abs(expected - pipelines) > tolerance
    if bad.any():
        row, slot = map(int, np.argwhere(bad)[0])
        return (
            f"open orders do not match in_transit for SKU {sku_index[row]!r} at slot {slot}: "
            f"{expected[row, slot]} != {pipelines[row, slot]}"
        )
    return None


ORDER_FRAME_COLUMNS = (
    "order_id",
    "unique_id",
    "supplier_id",
    "source",
    "order_period",
    "order_date",
    "due_period",
    "due_date",
    "lead_time",
    "ordered_quantity",
    "delivery_quantity",
    "status",
)


DELIVERY_OUTCOME_COLUMNS = (
    "scheduled_due_period",
    "received_quantity",
    "delayed_quantity",
    "undelivered_quantity",
)


def build_order_frame(deliveries: _Deliveries, *, final_period: int, opening_period: int,
                      opening_date: pd.Timestamp, period_offset,
                      outcomes: Optional[dict] = None) -> pd.DataFrame:
    """One row per scheduled delivery of a run, with calendar dates and status.

    ``outcomes`` (runs with supplier delivery outcomes only) holds arrays
    ``received``, ``delayed``, ``undelivered`` and ``disrupted`` aligned with
    ``deliveries``; it appends ``DELIVERY_OUTCOME_COLUMNS``.
    """
    if not len(deliveries):
        columns = ORDER_FRAME_COLUMNS + (DELIVERY_OUTCOME_COLUMNS if outcomes is not None else ())
        return _empty_frame(columns, {
            "order_id": "int64", "order_period": "float64", "order_date": "datetime64[ns]",
            "due_period": "int64", "due_date": "datetime64[ns]", "lead_time": "float64",
            "ordered_quantity": "float64", "delivery_quantity": "float64",
            "scheduled_due_period": "int64", "received_quantity": "float64",
            "delayed_quantity": "float64", "undelivered_quantity": "float64",
        })
    periods = np.unique(np.concatenate([
        deliveries.due.astype(np.float64),
        deliveries.order_period[~np.isnan(deliveries.order_period)],
    ]))
    calendar = pd.DatetimeIndex([
        opening_date + int(period - opening_period) * period_offset for period in periods
    ]).to_numpy().astype("datetime64[ns]")

    def dates(values):
        known = ~np.isnan(values)
        result = np.full(len(values), np.datetime64("NaT", "ns"))
        result[known] = calendar[np.searchsorted(periods, values[known])]
        return result

    frame = pd.DataFrame({
        "order_id": deliveries.order_id,
        "unique_id": _objects(deliveries.sku),
        "supplier_id": _objects(deliveries.supplier),
        "source": _objects(deliveries.source),
        "order_period": deliveries.order_period,
        "order_date": dates(deliveries.order_period),
        "due_period": deliveries.due,
        "due_date": dates(deliveries.due.astype(np.float64)),
        "lead_time": deliveries.due - deliveries.order_period,
        "ordered_quantity": deliveries.ordered,
        "delivery_quantity": deliveries.quantity,
        "status": _objects(np.where(deliveries.due <= final_period, "received", "open")),
    })
    if outcomes is not None:
        frame["status"] = _objects(np.where(
            deliveries.due > final_period, "open",
            np.where(outcomes["disrupted"], "disrupted", "received"),
        ))
        frame["scheduled_due_period"] = deliveries.scheduled
        frame["received_quantity"] = outcomes["received"]
        frame["delayed_quantity"] = outcomes["delayed"]
        frame["undelivered_quantity"] = outcomes["undelivered"]
    return frame.sort_values(["order_id", "due_period"], kind="stable").reset_index(drop=True)
