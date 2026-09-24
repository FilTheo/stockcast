"""
Supplier-level ordering for ``SimulationEngine`` (optional, order-level API).

This module provides:
    - Supplier: one source of supply with a fixed or discrete random lead time
      and an optional partial-delivery split
    - SupplierAllocation: extension point that splits each SKU's accepted
      order quantity across suppliers
    - SupplierShares: fixed fractional split, globally or per SKU
    - SupplyModel: the suppliers, their allocation and an explicit random seed

The supply stage runs after a policy's order has passed order callbacks and
constraints, and before the order enters the pipeline:

    policy -> order callbacks -> constraints -> supply (allocation, lead times,
    delivery split) -> pipeline / immediate receipt

Policies, callbacks and constraints are unchanged: they still work on one
order quantity per SKU. The event ledger keeps its per-SKU columns; the
supplier-level detail is in ``SimulationResult.to_order_frame()``.

Mapping from the default engine: ``supply=None`` behaves exactly like
``SupplyModel([Supplier("supplier", lead_time=policy.lead_time)])``; both
place each positive SKU order as one line due ``lead_time`` periods later.

Usage:
    supply = SupplyModel(
        suppliers=[
            Supplier("local_roaster", lead_time=1),
            Supplier("importer", lead_time={4: 0.6, 5: 0.3, 7: 0.1},
                     partial_deliveries=[(0, 0.7), (2, 0.3)]),
        ],
        allocation=SupplierShares({"local_roaster": 0.3, "importer": 0.7}),
        random_seed=7,
    )
    result = engine.run(..., supply=supply)
    result.to_order_frame()
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from stockcast.core.data_structures import (
    _identifier_sample,
    _require_supplier_ids,
)


def _plain_integer(value) -> bool:
    return isinstance(value, (int, np.integer)) and not isinstance(value, (bool, np.bool_))


def _json_identifier(value):
    """Supplier/SKU identifier for a JSON manifest (repr for other types)."""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return value
    return repr(value)


class Supplier:
    """
    One source of supply.

    Args:
        supplier_id: Hashable, nonblank supplier identifier.
        lead_time: Periods from order placement to (first) delivery. Either a
            fixed integer >= 0, or a mapping ``{lead_time: probability}`` with
            integer lead times >= 0 and positive probabilities summing to 1.
            Random lead times are drawn with the ``SupplyModel`` seed.
        partial_deliveries: Optional sequence of ``(delay, fraction)`` pairs.
            Each order line is delivered in parts, ``delay`` periods after the
            (drawn) lead time, with the given fraction of the line quantity.
            Delays are strictly increasing integers >= 0 and fractions are
            positive and sum to 1. Default: one delivery of the full quantity.

    Example:
        Supplier("importer", lead_time={4: 0.6, 5: 0.3, 7: 0.1},
                 partial_deliveries=[(0, 0.7), (2, 0.3)])
        # 70% arrives after 4, 5 or 7 periods; the rest two periods later.
    """

    def __init__(self, supplier_id, lead_time, *, partial_deliveries=None):
        if supplier_id is None:
            raise ValueError("supplier_id must not be None")
        _require_supplier_ids([supplier_id], "Supplier")
        self.supplier_id = supplier_id
        if _plain_integer(lead_time):
            if lead_time < 0:
                raise ValueError("Supplier.lead_time must be an integer >= 0")
            self._lead_values = np.array([int(lead_time)], dtype=np.int64)
            self._lead_probabilities = np.array([1.0])
        elif isinstance(lead_time, Mapping):
            if not lead_time:
                raise ValueError("Supplier.lead_time distribution must not be empty")
            values, probabilities = [], []
            for value, probability in lead_time.items():
                if not _plain_integer(value) or value < 0:
                    raise ValueError("Supplier.lead_time distribution keys must be integers >= 0")
                if (
                    isinstance(probability, bool)
                    or not isinstance(probability, (int, float, np.integer, np.floating))
                    or not np.isfinite(probability)
                    or probability <= 0
                ):
                    raise ValueError(
                        "Supplier.lead_time probabilities must be finite numbers > 0"
                    )
                values.append(int(value))
                probabilities.append(float(probability))
            if abs(sum(probabilities) - 1.0) > 1e-9:
                raise ValueError("Supplier.lead_time probabilities must sum to 1")
            order = np.argsort(values)
            self._lead_values = np.asarray(values, dtype=np.int64)[order]
            self._lead_probabilities = np.asarray(probabilities)[order]
        else:
            raise TypeError(
                "Supplier.lead_time must be an integer or a mapping of integer "
                "lead time to probability"
            )
        if partial_deliveries is None:
            self._delays = np.array([0], dtype=np.int64)
            self._fractions = np.array([1.0])
        else:
            if isinstance(partial_deliveries, (str, bytes)) or not isinstance(
                partial_deliveries, Sequence
            ) or not partial_deliveries:
                raise ValueError(
                    "Supplier.partial_deliveries must be a non-empty sequence of "
                    "(delay, fraction) pairs"
                )
            delays, fractions = [], []
            for item in partial_deliveries:
                if not isinstance(item, Sequence) or len(item) != 2:
                    raise ValueError("each partial delivery must be a (delay, fraction) pair")
                delay, fraction = item
                if not _plain_integer(delay) or delay < 0:
                    raise ValueError("partial delivery delays must be integers >= 0")
                if (
                    isinstance(fraction, bool)
                    or not isinstance(fraction, (int, float, np.integer, np.floating))
                    or not np.isfinite(fraction)
                    or fraction <= 0
                ):
                    raise ValueError("partial delivery fractions must be finite numbers > 0")
                delays.append(int(delay))
                fractions.append(float(fraction))
            if any(later <= earlier for earlier, later in zip(delays, delays[1:])):
                raise ValueError("partial delivery delays must be strictly increasing")
            if abs(sum(fractions) - 1.0) > 1e-9:
                raise ValueError("partial delivery fractions must sum to 1")
            self._delays = np.asarray(delays, dtype=np.int64)
            self._fractions = np.asarray(fractions)

    @property
    def is_random(self) -> bool:
        """Whether the lead time is drawn from a distribution."""
        return len(self._lead_values) > 1

    @property
    def max_delivery_offset(self) -> int:
        """Longest possible time, in periods, from order to last delivery."""
        return int(self._lead_values.max() + self._delays.max())

    def to_manifest(self) -> dict:
        if self.is_random:
            lead_time = {
                "distribution": [
                    [int(value), float(probability)]
                    for value, probability in zip(self._lead_values, self._lead_probabilities)
                ]
            }
        else:
            lead_time = {"fixed": int(self._lead_values[0])}
        return {
            "supplier_id": _json_identifier(self.supplier_id),
            "lead_time": lead_time,
            "partial_deliveries": [
                [int(delay), float(fraction)]
                for delay, fraction in zip(self._delays, self._fractions)
            ],
        }

    def __repr__(self) -> str:
        if self.is_random:
            lead = dict(zip(self._lead_values.tolist(), self._lead_probabilities.tolist()))
        else:
            lead = int(self._lead_values[0])
        return f"Supplier({self.supplier_id!r}, lead_time={lead!r})"


@dataclass(frozen=True)
class AllocationContext:
    """Defensive information handed to ``SupplierAllocation.allocate``.

    Attributes:
        inventory: Copy of the state frame at the decision (before this order).
        open_orders: Copy of ``InventoryStateDataFrame.open_orders()``.
        sku_column: SKU identifier column of both frames.
        period: State period of the decision (the order period).
        date: Calendar date of the decision period.
        suppliers: Supplier identifiers of the ``SupplyModel``, in order.
    """

    inventory: pd.DataFrame
    open_orders: pd.DataFrame
    sku_column: str
    period: int
    date: pd.Timestamp
    suppliers: tuple


class SupplierAllocation:
    """
    Extension point: split each SKU's order quantity across suppliers.

    Subclasses implement ``allocate(orders, context)``. ``orders`` has one row
    per SKU with a positive accepted order (``context.sku_column`` and
    ``order_quantity``). Return a DataFrame with columns ``context.sku_column``,
    ``supplier_id`` and ``order_quantity``: at most one row per SKU and
    supplier, known suppliers only, quantities >= 0, and per-SKU totals equal
    to the requested quantity (within floating-point tolerance). The per-SKU
    total in the event ledger is always the accepted order quantity.

    Allocations never change inventory. ``reset()`` is called before every
    run and ``get_config()`` must return JSON-serializable settings.
    """

    name = "supplier_allocation"

    def allocate(self, orders: pd.DataFrame, context: AllocationContext) -> pd.DataFrame:
        raise NotImplementedError

    def validate(self, supplier_ids: Sequence, skus: Sequence) -> None:
        """Optional preflight check against the run's suppliers and SKUs."""

    def reset(self) -> None:
        """Reset any per-run state."""

    def get_config(self) -> dict:
        return {}


class SupplierShares(SupplierAllocation):
    """
    Fixed fractional split of every order across suppliers.

    Args:
        shares: Mapping ``{supplier_id: fraction}``; fractions are >= 0 and sum
            to 1. Used for SKUs not listed in ``by_sku``.
        by_sku: Optional mapping ``{sku: {supplier_id: fraction}}`` overriding
            ``shares`` for specific SKUs.

    Suppliers with a zero share receive no order line. The last positive
    share receives the remainder, so line quantities add up to the order.

    Example:
        SupplierShares({"local_roaster": 0.3, "importer": 0.7},
                       by_sku={"decaf": {"local_roaster": 1.0}})
    """

    name = "supplier_shares"

    def __init__(self, shares: Mapping, *, by_sku: Optional[Mapping] = None):
        self.shares = self._validated(shares, "shares")
        if by_sku is not None and not isinstance(by_sku, Mapping):
            raise TypeError("SupplierShares.by_sku must be a mapping of SKU to shares")
        self.by_sku = {
            sku: self._validated(value, f"by_sku[{sku!r}]")
            for sku, value in (by_sku or {}).items()
        }

    @staticmethod
    def _validated(shares, label: str) -> dict:
        if not isinstance(shares, Mapping) or not shares:
            raise ValueError(f"SupplierShares.{label} must be a non-empty mapping")
        validated = {}
        for supplier_id, share in shares.items():
            if (
                isinstance(share, bool)
                or not isinstance(share, (int, float, np.integer, np.floating))
                or not np.isfinite(share)
                or share < 0
            ):
                raise ValueError(f"SupplierShares.{label} values must be finite numbers >= 0")
            validated[supplier_id] = float(share)
        if abs(sum(validated.values()) - 1.0) > 1e-9:
            raise ValueError(f"SupplierShares.{label} must sum to 1")
        return validated

    def validate(self, supplier_ids, skus) -> None:
        known = set(supplier_ids)
        for label, shares in [("shares", self.shares), *self.by_sku.items()]:
            unknown = set(shares) - known
            if unknown:
                raise ValueError(
                    f"SupplierShares {label!r} names unknown suppliers: "
                    f"{_identifier_sample(unknown)}"
                )
        unknown_skus = set(self.by_sku) - set(skus)
        if unknown_skus:
            raise ValueError(
                f"SupplierShares.by_sku names unknown SKUs: {_identifier_sample(unknown_skus)}"
            )

    def allocate(self, orders: pd.DataFrame, context: AllocationContext) -> pd.DataFrame:
        rows = []
        for sku, quantity in orders[[context.sku_column, "order_quantity"]].itertuples(
            index=False, name=None
        ):
            shares = [
                (supplier_id, share)
                for supplier_id, share in self.by_sku.get(sku, self.shares).items()
                if share > 0
            ]
            allocated = 0.0
            for position, (supplier_id, share) in enumerate(shares):
                if position == len(shares) - 1:
                    line = quantity - allocated
                else:
                    line = quantity * share
                    allocated += line
                rows.append((sku, supplier_id, line))
        return pd.DataFrame(rows, columns=[context.sku_column, "supplier_id", "order_quantity"])

    def get_config(self) -> dict:
        return {
            "shares": {str(_json_identifier(key)): value for key, value in self.shares.items()},
            "by_sku": {
                str(_json_identifier(sku)): {
                    str(_json_identifier(key)): value for key, value in shares.items()
                }
                for sku, shares in self.by_sku.items()
            },
        }


class SupplyModel:
    """
    Suppliers, their allocation and the random seed for one simulation.

    Args:
        suppliers: Non-empty sequence of ``Supplier`` objects with unique ids.
        allocation: ``SupplierAllocation`` deciding how each SKU order is
            split. Optional with one supplier (it then receives everything);
            required with several.
        random_seed: Integer seed for random lead times. Required when any
            supplier has a random lead time; may be ``None`` otherwise.

    Random lead times are drawn once per run for every (decision period,
    SKU, supplier) cell, from a stream per supplier. Runs of
    ``run_comparison`` therefore see the same lead-time realization for an
    order placed by the same SKU in the same period from the same supplier.

    The engine requires ``inventory.max_lead_time`` to cover the longest
    delivery offset of every supplier (see ``max_delivery_offset``).
    """

    def __init__(
        self,
        suppliers: Sequence[Supplier],
        *,
        allocation: Optional[SupplierAllocation] = None,
        random_seed: Optional[int] = None,
    ):
        if isinstance(suppliers, (str, bytes)) or not isinstance(suppliers, Sequence) or not suppliers:
            raise ValueError("SupplyModel.suppliers must be a non-empty sequence of Supplier objects")
        if not all(isinstance(supplier, Supplier) for supplier in suppliers):
            raise TypeError("SupplyModel.suppliers must contain only Supplier objects")
        ids = [supplier.supplier_id for supplier in suppliers]
        _require_supplier_ids(ids, "SupplyModel.suppliers")
        if len(set(ids)) != len(ids):
            raise ValueError("SupplyModel supplier ids must be unique")
        if allocation is None and len(suppliers) > 1:
            raise ValueError("SupplyModel with several suppliers requires an explicit allocation")
        if allocation is not None and not isinstance(allocation, SupplierAllocation):
            raise TypeError("SupplyModel.allocation must be a SupplierAllocation")
        if random_seed is not None and not _plain_integer(random_seed):
            raise ValueError("SupplyModel.random_seed must be an integer or None")
        if random_seed is None and any(supplier.is_random for supplier in suppliers):
            raise ValueError(
                "SupplyModel.random_seed must be an explicit integer when a supplier "
                "has a random lead time"
            )
        self.suppliers = tuple(suppliers)
        self.allocation = allocation
        self.random_seed = None if random_seed is None else int(random_seed)
        self._draws = None

    @property
    def supplier_ids(self) -> tuple:
        return tuple(supplier.supplier_id for supplier in self.suppliers)

    @property
    def max_delivery_offset(self) -> int:
        """Longest possible time from order to last delivery over all suppliers."""
        return max(supplier.max_delivery_offset for supplier in self.suppliers)

    def to_manifest(self) -> dict:
        allocation = None
        if self.allocation is not None:
            config = self.allocation.get_config()
            if not isinstance(config, dict):
                raise TypeError("SupplierAllocation.get_config() must return a dictionary")
            allocation = {
                "module": type(self.allocation).__module__,
                "class": type(self.allocation).__name__,
                "config": copy.deepcopy(config),
            }
        manifest = {
            "suppliers": [supplier.to_manifest() for supplier in self.suppliers],
            "allocation": allocation,
            "random_seed": self.random_seed,
            "lead_time_draws": "per supplier stream, one draw per decision period and SKU",
        }
        json.dumps(manifest, allow_nan=False)
        return manifest

    # ---- engine-private ----------------------------------------------------

    def _prepare(self, skus: Sequence, n_periods: int) -> None:
        """Validate against the run and draw every random lead time up front."""
        if self.allocation is not None:
            self.allocation.validate(self.supplier_ids, skus)
            self.allocation.reset()
        self._sku_positions = {sku: position for position, sku in enumerate(skus)}
        draws = []
        for position, supplier in enumerate(self.suppliers):
            if supplier.is_random:
                rng = np.random.default_rng([self.random_seed, position])
                draws.append(rng.choice(
                    supplier._lead_values,
                    size=(n_periods, len(skus)),
                    p=supplier._lead_probabilities / supplier._lead_probabilities.sum(),
                ))
            else:
                draws.append(None)
        self._draws = draws

    def _allocate(self, orders: pd.DataFrame, context: AllocationContext) -> pd.DataFrame:
        """Validated supplier lines for the positive per-SKU orders."""
        sku_column = context.sku_column
        if self.allocation is None:
            lines = orders[[sku_column, "order_quantity"]].copy()
            lines.insert(1, "supplier_id", pd.Series(
                [self.suppliers[0].supplier_id] * len(lines), index=lines.index, dtype=object,
            ))
            return lines.reset_index(drop=True)
        result = self.allocation.allocate(orders.copy(deep=True), context)
        if not isinstance(result, pd.DataFrame):
            raise TypeError("SupplierAllocation.allocate must return a pandas DataFrame")
        required = {sku_column, "supplier_id", "order_quantity"}
        missing = sorted(required - set(result.columns))
        extra = sorted(set(result.columns) - required)
        if missing:
            raise ValueError(f"supplier allocation is missing columns: {missing}")
        if extra:
            raise ValueError(f"supplier allocation contains unsupported columns: {extra}")
        lines = result[[sku_column, "supplier_id", "order_quantity"]].reset_index(drop=True)
        quantity = pd.to_numeric(lines["order_quantity"], errors="coerce")
        if (
            quantity.isna().any()
            or not np.isfinite(quantity.to_numpy(dtype=float)).all()
            or (quantity < 0).any()
        ):
            raise ValueError("supplier allocation.order_quantity must contain finite values >= 0")
        lines["order_quantity"] = quantity.astype(float)
        unknown_suppliers = set(lines["supplier_id"].tolist()) - set(self.supplier_ids)
        if unknown_suppliers:
            raise ValueError(
                f"supplier allocation names unknown suppliers: {_identifier_sample(unknown_suppliers)}"
            )
        ordered = orders.set_index(sku_column)["order_quantity"]
        unknown_skus = set(lines[sku_column].tolist()) - set(ordered.index.tolist())
        if unknown_skus:
            raise ValueError(
                "supplier allocation names SKUs without a positive order: "
                f"{_identifier_sample(unknown_skus)}"
            )
        if lines.duplicated([sku_column, "supplier_id"]).any():
            raise ValueError("supplier allocation must have at most one row per SKU and supplier")
        totals = lines.groupby(sku_column, sort=False)["order_quantity"].sum()
        allocated = ordered.index.to_series().map(totals).fillna(0.0).to_numpy(dtype=float)
        requested = ordered.to_numpy(dtype=float)
        if (np.abs(allocated - requested) > 1e-9 * np.maximum(1.0, np.abs(requested))).any():
            raise ValueError(
                "supplier allocation must split each SKU's full order quantity "
                "(per-SKU totals differ from the accepted order)"
            )
        return lines[lines["order_quantity"] > 0].reset_index(drop=True)

    def _deliveries(self, lines: pd.DataFrame, *, sku_column: str, demand_period: int,
                    order_period: int) -> pd.DataFrame:
        """Scheduled deliveries (``OrderLines`` rows) for allocated supplier lines."""
        by_id = {supplier.supplier_id: position for position, supplier in enumerate(self.suppliers)}
        rows = []
        for line, (sku, supplier_id, quantity) in enumerate(
            lines[[sku_column, "supplier_id", "order_quantity"]].itertuples(index=False, name=None)
        ):
            position = by_id[supplier_id]
            supplier = self.suppliers[position]
            draws = self._draws[position]
            if draws is None:
                lead_time = int(supplier._lead_values[0])
            else:
                lead_time = int(draws[demand_period, self._sku_positions[sku]])
            delivered = 0.0
            last = len(supplier._fractions) - 1
            for part, (delay, fraction) in enumerate(zip(supplier._delays, supplier._fractions)):
                if part == last:
                    part_quantity = quantity - delivered
                else:
                    part_quantity = quantity * fraction
                    delivered += part_quantity
                rows.append((
                    sku, supplier_id, part_quantity, order_period,
                    order_period + lead_time + int(delay), line,
                ))
        return pd.DataFrame(rows, columns=[
            sku_column, "supplier_id", "order_quantity", "order_period", "due_period", "order_line",
        ])

    def __repr__(self) -> str:
        return (
            f"SupplyModel(suppliers={list(self.supplier_ids)!r}, "
            f"allocation={type(self.allocation).__name__ if self.allocation else None}, "
            f"random_seed={self.random_seed})"
        )
