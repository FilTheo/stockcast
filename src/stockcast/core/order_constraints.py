"""Composable operational constraints for order decisions."""

from __future__ import annotations

import json
import math
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from stockcast.core.data_structures import (
    InventoryStateDataFrame,
    OrderDecision,
    _identifier_sample,
    _require_identifiers,
)


@dataclass(frozen=True)
class ConstraintContext:
    """State visible to an ordering constraint at one decision opportunity."""

    inventory: InventoryStateDataFrame
    policy: object | None
    decision_period: int


@dataclass(frozen=True)
class ConstraintResult:
    """One constraint's validated decision and audit rows."""

    order: OrderDecision
    audit: pd.DataFrame


class OrderingConstraint(ABC):
    """Extension interface for one operational ordering constraint."""

    name = "custom_constraint"

    def reset(self, context: ConstraintContext) -> None:
        """Reset run-local state. Stateless constraints need no action."""

    @abstractmethod
    def apply(self, order: OrderDecision, context: ConstraintContext) -> ConstraintResult:
        """Apply this constraint and return a decision plus audit."""

    def validate(self, order: OrderDecision, context: ConstraintContext) -> None:
        """Validate a final composed decision. Custom constraints may override this."""
        _validated_order_frame(order, context)

    def to_manifest(self) -> dict:
        return {"class": type(self).__name__, "name": self.name}


def _validated_order_frame(order: OrderDecision, context: ConstraintContext) -> pd.DataFrame:
    if not isinstance(order, OrderDecision):
        raise TypeError("constraint result order must be an OrderDecision")
    frame = order.get_dataframe()
    inventory_frame = context.inventory.get_dataframe()
    expected = _require_identifiers(
        inventory_frame,
        context.inventory.sku_column,
        "inventory_state",
        unique=True,
    )
    actual = _require_identifiers(frame, order.sku_column, "order decision", unique=True)
    unknown = actual - expected
    if unknown:
        raise ValueError(
            f"constraint result contains unknown SKUs: {_identifier_sample(unknown)}"
        )
    quantity = pd.to_numeric(frame["order_quantity"], errors="coerce")
    if quantity.isna().any() or not np.isfinite(quantity.to_numpy(dtype=float)).all():
        raise ValueError("constraint result order_quantity must contain finite numbers")
    if (quantity < 0).any():
        raise ValueError("constraint result order_quantity must be non-negative")
    prepared = frame.copy()
    prepared["order_quantity"] = quantity.astype(float)
    missing = expected - actual
    if missing:
        missing_skus = [
            sku
            for sku in inventory_frame[context.inventory.sku_column].tolist()
            if sku in missing
        ]
        zero_rows = prepared.iloc[:0].reindex(range(len(missing_skus))).copy()
        zero_rows[order.sku_column] = missing_skus
        zero_rows["order_quantity"] = 0.0
        zero_rows["order_period"] = context.decision_period
        if order.lead_time is not None:
            zero_rows["expected_delivery_period"] = (
                context.decision_period + order.lead_time
            )
        prepared = pd.concat([prepared, zero_rows], ignore_index=True)
    return prepared


def _make_order(template: OrderDecision, frame: pd.DataFrame) -> OrderDecision:
    return OrderDecision(
        frame,
        sku_column=template.sku_column,
        lead_time=template.lead_time,
        review_period=template.review_period,
    )


def _audit(
    sku_values: pd.Series,
    requested: pd.Series,
    constrained: pd.Series,
    *,
    name: str,
    capacity: bool,
) -> pd.DataFrame:
    changed = ~np.isclose(requested, constrained, rtol=0.0, atol=1e-9)
    return pd.DataFrame({
        "unique_id": sku_values.to_list(),
        "requested_order_quantity": requested.to_numpy(dtype=float),
        "constrained_order_quantity": constrained.to_numpy(dtype=float),
        "constraint_adjustment_units": (
            constrained.to_numpy(dtype=float) - requested.to_numpy(dtype=float)
        ),
        "constraint_binding_flag": changed,
        "capacity_violation_flag": changed if capacity else np.zeros(len(changed), dtype=bool),
        "binding_constraints": [name if flag else "" for flag in changed],
    })


class _PerSkuConstraint(OrderingConstraint):
    parameter_name = "values"

    def __init__(self, values, *, mode: str = "raise"):
        if mode not in {"raise", "adjust"}:
            raise ValueError("constraint mode must be 'raise' or 'adjust'")
        if isinstance(values, Mapping):
            if not values:
                raise ValueError(f"{self.parameter_name} mapping must not be empty")
            prepared = {}
            for sku, value in values.items():
                prepared[sku] = self._validate_value(value)
            self.values = prepared
        else:
            self.values = self._validate_value(values)
        self.mode = mode

    def _validate_value(self, value) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{self.parameter_name} must contain finite numbers") from exc
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"{self.parameter_name} must contain finite values >= 0")
        return numeric

    def _resolved(self, skus: list) -> np.ndarray:
        if isinstance(self.values, dict):
            missing = [sku for sku in skus if sku not in self.values]
            extra = [sku for sku in self.values if sku not in set(skus)]
            if missing or extra:
                raise ValueError(
                    f"{self.parameter_name} must contain exactly the inventory SKUs; "
                    f"missing={missing[:5]}, extra={extra[:5]}"
                )
            return np.asarray([self.values[sku] for sku in skus], dtype=float)
        return np.full(len(skus), self.values, dtype=float)

    def _violation(self, sku, detail: str) -> None:
        raise ValueError(f"order for SKU {sku} violates {self.name}: {detail}")

    def _composition_violation(self, sku, detail: str) -> None:
        raise ValueError(
            f"final composed order for SKU {sku} violates {self.name}: {detail}; "
            "the declared constraint sequence did not produce a jointly feasible quantity"
        )

    def to_manifest(self) -> dict:
        values = self.values
        if isinstance(values, dict):
            values = [
                {"unique_id": sku, "value": value}
                for sku, value in sorted(
                    values.items(), key=lambda item: (type(item[0]).__name__, repr(item[0]))
                )
            ]
        return {**super().to_manifest(), "mode": self.mode, self.parameter_name: values}


class MinimumOrderQuantity(_PerSkuConstraint):
    """Raise or adjust positive orders below a declared minimum."""

    name = "minimum_order_quantity"
    parameter_name = "minimum_quantities"

    def apply(self, order: OrderDecision, context: ConstraintContext) -> ConstraintResult:
        frame = _validated_order_frame(order, context)
        requested = frame["order_quantity"].copy()
        limits = self._resolved(frame[order.sku_column].tolist())
        for index, (quantity, limit) in enumerate(zip(requested, limits)):
            if 0 < quantity < limit:
                if self.mode == "raise":
                    self._violation(frame.iloc[index][order.sku_column], f"{quantity} < {limit}")
                frame.at[frame.index[index], "order_quantity"] = limit
        audit = _audit(
            frame[order.sku_column], requested, frame["order_quantity"],
            name=self.name, capacity=False,
        )
        return ConstraintResult(_make_order(order, frame), audit)

    def validate(self, order: OrderDecision, context: ConstraintContext) -> None:
        frame = _validated_order_frame(order, context)
        limits = self._resolved(frame[order.sku_column].tolist())
        for sku, quantity, limit in zip(
            frame[order.sku_column], frame["order_quantity"], limits
        ):
            if 0 < quantity < limit:
                self._composition_violation(sku, f"{quantity} < {limit}")


class OrderMultiple(_PerSkuConstraint):
    """Raise or round positive orders up to a declared multiple."""

    name = "order_multiple"
    parameter_name = "multiples"

    def _validate_value(self, value) -> float:
        numeric = super()._validate_value(value)
        if numeric <= 0:
            raise ValueError("multiples must contain finite values > 0")
        return numeric

    def apply(self, order: OrderDecision, context: ConstraintContext) -> ConstraintResult:
        frame = _validated_order_frame(order, context)
        requested = frame["order_quantity"].copy()
        multiples = self._resolved(frame[order.sku_column].tolist())
        for index, (quantity, multiple) in enumerate(zip(requested, multiples)):
            ratio = quantity / multiple
            if quantity > 0 and not np.isclose(ratio, round(ratio), rtol=0.0, atol=1e-9):
                if self.mode == "raise":
                    self._violation(frame.iloc[index][order.sku_column], f"{quantity} is not a multiple of {multiple}")
                frame.at[frame.index[index], "order_quantity"] = math.ceil(
                    (quantity - 1e-12) / multiple
                ) * multiple
        audit = _audit(
            frame[order.sku_column], requested, frame["order_quantity"],
            name=self.name, capacity=False,
        )
        return ConstraintResult(_make_order(order, frame), audit)

    def validate(self, order: OrderDecision, context: ConstraintContext) -> None:
        frame = _validated_order_frame(order, context)
        multiples = self._resolved(frame[order.sku_column].tolist())
        for sku, quantity, multiple in zip(
            frame[order.sku_column], frame["order_quantity"], multiples
        ):
            ratio = quantity / multiple
            if quantity > 0 and not np.isclose(
                ratio, round(ratio), rtol=0.0, atol=1e-9
            ):
                self._composition_violation(
                    sku, f"{quantity} is not a multiple of {multiple}"
                )


class MaximumOrderQuantity(_PerSkuConstraint):
    """Raise or clip orders above a declared per-SKU maximum."""

    name = "maximum_order_quantity"
    parameter_name = "maximum_quantities"

    def apply(self, order: OrderDecision, context: ConstraintContext) -> ConstraintResult:
        frame = _validated_order_frame(order, context)
        requested = frame["order_quantity"].copy()
        limits = self._resolved(frame[order.sku_column].tolist())
        for index, (quantity, limit) in enumerate(zip(requested, limits)):
            if quantity > limit + 1e-9:
                if self.mode == "raise":
                    self._violation(frame.iloc[index][order.sku_column], f"{quantity} > {limit}")
                frame.at[frame.index[index], "order_quantity"] = limit
        audit = _audit(
            frame[order.sku_column], requested, frame["order_quantity"],
            name=self.name, capacity=True,
        )
        return ConstraintResult(_make_order(order, frame), audit)

    def validate(self, order: OrderDecision, context: ConstraintContext) -> None:
        frame = _validated_order_frame(order, context)
        limits = self._resolved(frame[order.sku_column].tolist())
        for sku, quantity, limit in zip(
            frame[order.sku_column], frame["order_quantity"], limits
        ):
            if quantity > limit + 1e-9:
                self._composition_violation(sku, f"{quantity} > {limit}")


class ShelfSpaceLimit(_PerSkuConstraint):
    """Limit each order using on-hand plus pipeline occupancy in unit space."""

    name = "shelf_space_limit"
    parameter_name = "capacity_units"

    @staticmethod
    def _pipeline_units(value) -> float:
        return float(value.sum()) if isinstance(value, np.ndarray) else 0.0

    def apply(self, order: OrderDecision, context: ConstraintContext) -> ConstraintResult:
        frame = _validated_order_frame(order, context)
        requested = frame["order_quantity"].copy()
        skus = frame[order.sku_column].tolist()
        limits = self._resolved(skus)
        inventory = context.inventory.get_dataframe().set_index(context.inventory.sku_column)
        for index, (sku, quantity, limit) in enumerate(zip(skus, requested, limits)):
            occupied = float(inventory.at[sku, "on_hand"]) + self._pipeline_units(
                inventory.at[sku, "in_transit"]
            )
            available = max(0.0, limit - occupied)
            if quantity > available + 1e-9:
                if self.mode == "raise":
                    self._violation(sku, f"{quantity} exceeds {available} available units")
                frame.at[frame.index[index], "order_quantity"] = available
        audit = _audit(
            frame[order.sku_column], requested, frame["order_quantity"],
            name=self.name, capacity=True,
        )
        return ConstraintResult(_make_order(order, frame), audit)

    def validate(self, order: OrderDecision, context: ConstraintContext) -> None:
        frame = _validated_order_frame(order, context)
        skus = frame[order.sku_column].tolist()
        limits = self._resolved(skus)
        inventory = context.inventory.get_dataframe().set_index(
            context.inventory.sku_column
        )
        for sku, quantity, limit in zip(skus, frame["order_quantity"], limits):
            occupied = float(inventory.at[sku, "on_hand"]) + self._pipeline_units(
                inventory.at[sku, "in_transit"]
            )
            available = max(0.0, limit - occupied)
            if quantity > available + 1e-9:
                self._composition_violation(
                    sku, f"{quantity} exceeds {available} available units"
                )


class OrderingConstraints(OrderingConstraint):
    """Apply an explicit sequence of independent ordering constraints."""

    name = "ordering_constraints"

    def __init__(self, constraints: Sequence[OrderingConstraint]):
        if isinstance(constraints, (str, bytes)) or not isinstance(constraints, Sequence):
            raise TypeError("constraints must be a sequence of OrderingConstraint objects")
        if not constraints:
            raise ValueError("constraints must not be empty")
        if not all(isinstance(constraint, OrderingConstraint) for constraint in constraints):
            raise TypeError("every constraint must be an OrderingConstraint")
        names = [constraint.name for constraint in constraints]
        if any(
            not isinstance(name, str) or not name.strip() or "|" in name
            for name in names
        ):
            raise ValueError(
                "constraint names must be non-empty strings without the '|' delimiter"
            )
        if len(names) != len(set(names)):
            raise ValueError("constraint names must be unique within one composition")
        self.constraints = list(constraints)

    def reset(self, context: ConstraintContext) -> None:
        for constraint in self.constraints:
            constraint.reset(context)

    def apply(self, order: OrderDecision, context: ConstraintContext) -> ConstraintResult:
        original = _validated_order_frame(order, context)
        current = _make_order(order, original)
        audits = []
        for constraint in self.constraints:
            result = constraint.apply(current, context)
            current_frame = _validated_order_frame(result.order, context)
            if not isinstance(result.audit, pd.DataFrame):
                raise TypeError("constraint audit must be a pandas DataFrame")
            audits.append(result.audit.copy())
            current = _make_order(result.order, current_frame)

        self.validate(current, context)

        final = current.get_dataframe()
        binding_by_sku = {sku: [] for sku in original[order.sku_column]}
        capacity_by_sku = {sku: False for sku in binding_by_sku}
        for audit in audits:
            required = {"unique_id", "constraint_binding_flag", "capacity_violation_flag", "binding_constraints"}
            if not required.issubset(audit.columns):
                raise ValueError("constraint audit is missing canonical audit columns")
            for row in audit.itertuples(index=False):
                if bool(row.constraint_binding_flag) and row.binding_constraints:
                    binding_by_sku[row.unique_id].append(str(row.binding_constraints))
                capacity_by_sku[row.unique_id] |= bool(row.capacity_violation_flag)
        aggregate = _audit(
            original[order.sku_column],
            original["order_quantity"],
            final["order_quantity"],
            name=self.name,
            capacity=False,
        )
        aggregate["binding_constraints"] = [
            "|".join(binding_by_sku[sku]) for sku in aggregate["unique_id"]
        ]
        aggregate["constraint_binding_flag"] = (
            aggregate["binding_constraints"].str.len() > 0
        )
        aggregate["capacity_violation_flag"] = [
            capacity_by_sku[sku] for sku in aggregate["unique_id"]
        ]
        return ConstraintResult(current, aggregate)

    def validate(self, order: OrderDecision, context: ConstraintContext) -> None:
        _validated_order_frame(order, context)
        for constraint in self.constraints:
            constraint.validate(order, context)

    def to_manifest(self) -> dict:
        component_manifests = [constraint.to_manifest() for constraint in self.constraints]
        if not all(isinstance(manifest, dict) for manifest in component_manifests):
            raise TypeError("every constraint manifest must be a dictionary")
        manifest = {
            **super().to_manifest(),
            "application_order": [constraint.name for constraint in self.constraints],
            "constraints": component_manifests,
        }
        try:
            json.dumps(manifest)
        except (TypeError, ValueError) as exc:
            raise ValueError("ordering constraint manifest must be JSON-serializable") from exc
        return manifest
