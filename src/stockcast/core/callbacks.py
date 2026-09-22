"""Typed, ordered simulation callbacks with engine-owned application."""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from stockcast.core.data_structures import OrderDecision, _require_identifiers


class CallbackError(RuntimeError):
    """A callback failed or returned an invalid intervention."""


@dataclass(frozen=True)
class CallbackContext:
    """Defensive run state supplied at one precisely named callback phase."""

    inventory: pd.DataFrame
    sku_column: str
    period: int
    date: pd.Timestamp
    run_window: str
    phase: str
    initial_decision: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "inventory", self.inventory.copy(deep=True))
        object.__setattr__(self, "date", pd.Timestamp(self.date))


class InventoryAdjustmentResult:
    """Sparse, signed on-hand adjustments proposed by a callback."""

    def __init__(self, adjustments: pd.DataFrame):
        if not isinstance(adjustments, pd.DataFrame):
            raise TypeError("InventoryAdjustmentResult requires a pandas DataFrame")
        self._adjustments = adjustments.copy(deep=True)

    def get_dataframe(self) -> pd.DataFrame:
        return self._adjustments.copy(deep=True)


class OrderAdjustmentResult:
    """Sparse absolute order quantities proposed by a callback."""

    def __init__(self, adjustments: pd.DataFrame):
        if not isinstance(adjustments, pd.DataFrame):
            raise TypeError("OrderAdjustmentResult requires a pandas DataFrame")
        self._adjustments = adjustments.copy(deep=True)

    def get_dataframe(self) -> pd.DataFrame:
        return self._adjustments.copy(deep=True)


class SimulationCallback:
    """Keras-like base class for typed simulation interventions."""

    def reset(self, context: CallbackContext) -> None:
        """Reset run-local callback state before a simulation starts."""

    def on_after_demand(
        self, context: CallbackContext
    ) -> InventoryAdjustmentResult | None:
        """Propose on-hand adjustments after demand and before ordering."""
        return None

    def on_after_prediction(
        self, decision: OrderDecision, context: CallbackContext
    ) -> OrderAdjustmentResult | None:
        """Propose absolute order quantities before operational constraints."""
        return None

    def get_config(self) -> dict:
        """Return JSON-serializable constructor configuration."""
        return {}


def _json_value(value):
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    missing = pd.isna(value)
    if isinstance(missing, (bool, np.bool_)) and missing:
        return None
    return value


class _ScheduledCallback(SimulationCallback):
    value_column: str | None = None

    def __init__(self, schedule: pd.DataFrame):
        if not isinstance(schedule, pd.DataFrame) or schedule.empty:
            raise ValueError("schedule must be a non-empty pandas DataFrame")
        required = {"unique_id", "reason", "source"}
        if self.value_column is not None:
            required.add(self.value_column)
        missing = sorted(required - set(schedule.columns))
        if missing:
            raise ValueError(f"schedule is missing required columns: {missing}")
        if "period" not in schedule.columns and "date" not in schedule.columns:
            raise ValueError("schedule requires period, date, or both")
        allowed = required | {"period", "date"}
        extra = sorted(set(schedule.columns) - allowed)
        if extra:
            raise ValueError(f"schedule contains unsupported columns: {extra}")
        prepared = schedule.copy(deep=True)
        _require_identifiers(prepared, "unique_id", "callback schedule", unique=False)
        for column in ("reason", "source"):
            if (
                prepared[column].isna().any()
                or ~prepared[column].map(lambda value: isinstance(value, str)).all()
                or prepared[column].str.strip().eq("").any()
            ):
                raise ValueError(f"schedule.{column} must contain nonblank strings")
        if "period" in prepared:
            periods = pd.to_numeric(prepared["period"], errors="coerce")
            if (
                periods.isna().any()
                or not np.isfinite(periods.to_numpy(dtype=float)).all()
                or not np.equal(periods, np.floor(periods)).all()
                or (periods < 0).any()
            ):
                raise ValueError("schedule.period must contain finite integers >= 0")
            prepared["period"] = periods.astype(int)
        if "date" in prepared:
            dates = pd.to_datetime(prepared["date"], errors="coerce")
            if dates.isna().any():
                raise ValueError("schedule.date must contain valid timestamps")
            prepared["date"] = dates
        if self.value_column is not None:
            values = pd.to_numeric(prepared[self.value_column], errors="coerce")
            if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
                raise ValueError(f"schedule.{self.value_column} must contain finite numbers")
            prepared[self.value_column] = values.astype(float)
        coordinate_columns = [c for c in ("period", "date") if c in prepared]
        if prepared.duplicated(["unique_id", *coordinate_columns]).any():
            raise ValueError("schedule contains duplicate SKU-coordinate rows")
        self.schedule = prepared

    def _matching(self, context: CallbackContext) -> pd.DataFrame:
        selected = pd.Series(True, index=self.schedule.index)
        if "period" in self.schedule:
            selected &= self.schedule["period"].eq(context.period)
        if "date" in self.schedule:
            selected &= self.schedule["date"].eq(context.date)
        return self.schedule.loc[selected].copy()

    def get_config(self) -> dict:
        records = [
            {key: _json_value(value) for key, value in row.items()}
            for row in self.schedule.to_dict(orient="records")
        ]
        config = {"schedule": records}
        json.dumps(config)
        return config


class ScheduledOrderOverride(_ScheduledCallback):
    """Set scheduled order quantities to explicit absolute values."""

    value_column = "order_quantity"

    def __init__(self, schedule: pd.DataFrame):
        super().__init__(schedule)
        if (self.schedule["order_quantity"] < 0).any():
            raise ValueError("schedule.order_quantity must be non-negative")

    def on_after_prediction(self, decision, context):
        matching = self._matching(context)
        if matching.empty:
            return None
        return OrderAdjustmentResult(
            matching[["unique_id", "order_quantity", "reason", "source"]]
        )


class ScheduledOrderMultiplier(_ScheduledCallback):
    """Multiply scheduled current predicted quantities."""

    value_column = "multiplier"

    def __init__(self, schedule: pd.DataFrame):
        super().__init__(schedule)
        if (self.schedule["multiplier"] < 0).any():
            raise ValueError("schedule.multiplier must be non-negative")

    def on_after_prediction(self, decision, context):
        matching = self._matching(context)
        if matching.empty:
            return None
        current = decision.get_dataframe()[[decision.sku_column, "order_quantity"]]
        current = current.rename(columns={decision.sku_column: "unique_id"})
        result = matching.merge(current, on="unique_id", how="left", validate="one_to_one")
        if result["order_quantity"].isna().any():
            raise ValueError("scheduled multiplier targets an SKU absent from the decision")
        result["order_quantity"] = result["order_quantity"] * result["multiplier"]
        return OrderAdjustmentResult(
            result[["unique_id", "order_quantity", "reason", "source"]]
        )


class ScheduledOrderHold(_ScheduledCallback):
    """Set scheduled current predicted quantities to zero."""

    def on_after_prediction(self, decision, context):
        matching = self._matching(context)
        if matching.empty:
            return None
        matching["order_quantity"] = 0.0
        return OrderAdjustmentResult(
            matching[["unique_id", "order_quantity", "reason", "source"]]
        )


class ScheduledInventoryAdjustment(_ScheduledCallback):
    """Apply scheduled signed on-hand unit adjustments after demand."""

    value_column = "quantity_delta"

    def __init__(self, schedule: pd.DataFrame):
        if isinstance(schedule, pd.DataFrame) and "received_date" in schedule:
            self._received_dates = schedule["received_date"].copy()
            schedule = schedule.drop(columns="received_date").copy()
            super().__init__(schedule)
            dates = pd.to_datetime(self._received_dates, errors="coerce")
            invalid = self._received_dates.notna() & dates.isna()
            if invalid.any():
                raise ValueError("schedule.received_date must contain valid timestamps or missing values")
            self.schedule["received_date"] = dates.to_numpy()
        else:
            super().__init__(schedule)

    def on_after_demand(self, context):
        matching = self._matching(context)
        if matching.empty:
            return None
        columns = ["unique_id", "quantity_delta", "reason", "source"]
        if "received_date" in matching:
            columns.append("received_date")
        return InventoryAdjustmentResult(matching[columns])
