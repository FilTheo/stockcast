"""Typed, ordered simulation callbacks with engine-owned application."""

from __future__ import annotations

import json
from dataclasses import dataclass

import numpy as np
import pandas as pd

from stockcast.core.data_structures import OrderDecision, _require_identifiers


class CallbackError(RuntimeError):
    """A callback failed or returned an invalid adjustment.

    The message names the callback, its position, the hook, the period, and the
    date; the original exception is chained as the cause.
    """


@dataclass(frozen=True)
class CallbackContext:
    """Read-only information given to a callback at one hook.

    Attributes:
        inventory: A copy of the state table at this moment.
        sku_column: Name of the SKU column.
        period: State period (opening period + demand period + 1).
        date: Date of the period.
        run_window: ``"warmup"``, ``"scoring"`` or ``"settlement"``.
        phase: ``"on_after_prediction"`` or ``"on_after_demand"``.
        initial_decision: Kept for compatibility; ``False`` in current runs.
    """

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
    """Signed on-hand changes proposed by ``on_after_demand``.

    Args:
        adjustments: One row per SKU with ``unique_id``, ``quantity_delta``
            (positive adds stock, negative removes it), ``reason`` and ``source``,
            and optionally ``received_date`` (needed for added stock under shelf
            life).
    """

    def __init__(self, adjustments: pd.DataFrame):
        if not isinstance(adjustments, pd.DataFrame):
            raise TypeError("InventoryAdjustmentResult requires a pandas DataFrame")
        self._adjustments = adjustments.copy(deep=True)

    def get_dataframe(self) -> pd.DataFrame:
        """Return a copy of the proposed adjustments.
        """
        return self._adjustments.copy(deep=True)


class OrderAdjustmentResult:
    """Order quantities proposed by ``on_after_prediction``.

    Args:
        adjustments: One row per SKU to change, with ``unique_id``,
            ``order_quantity`` (the new absolute quantity, >= 0), ``reason`` and
            ``source``. SKUs not listed keep their quantity.
    """

    def __init__(self, adjustments: pd.DataFrame):
        if not isinstance(adjustments, pd.DataFrame):
            raise TypeError("OrderAdjustmentResult requires a pandas DataFrame")
        self._adjustments = adjustments.copy(deep=True)

    def get_dataframe(self) -> pd.DataFrame:
        """Return a copy of the proposed quantities.
        """
        return self._adjustments.copy(deep=True)


class SimulationCallback:
    """Base class for planned interventions in a run, in the spirit of Keras callbacks.

    Override one or both hooks. Return ``None`` to change nothing. The engine
    validates each proposal, applies it, and records it in
    ``SimulationResult.to_callback_audit_frame()``. Callbacks receive copies of
    the state; they never change it directly.

    - ``on_after_prediction`` runs after the policy proposes an order and before
      ordering constraints. It can set new order quantities.
    - ``on_after_demand`` runs after demand is served. It can add or remove
      on-hand stock, which affects later decisions.

    Example:
        ```python
        class DoubleOrdersOn(SimulationCallback):
            def __init__(self, date):
                self.date = pd.Timestamp(date)

            def on_after_prediction(self, decision, context):
                if context.date != self.date:
                    return None
                orders = decision.get_dataframe()
                return OrderAdjustmentResult(pd.DataFrame({
                    "unique_id": orders["unique_id"],
                    "order_quantity": 2 * orders["order_quantity"],
                    "reason": "promotion", "source": "marketing",
                }))
        ```
    """

    def reset(self, context: CallbackContext) -> None:
        """Reset run-local state. Called before every run and comparison branch.

        Args:
            context: Context of the opening state.
        """

    def on_after_demand(
        self, context: CallbackContext
    ) -> InventoryAdjustmentResult | None:
        """Propose on-hand adjustments after demand is served.

        Args:
            context: The state after demand, and the period's date and window.

        Returns:
            An ``InventoryAdjustmentResult``, or ``None`` for no change.
        """
        return None

    def on_after_prediction(
        self, decision: OrderDecision, context: CallbackContext
    ) -> OrderAdjustmentResult | None:
        """Propose order quantities after the policy's prediction.

        Args:
            decision: A copy of the current ``OrderDecision``.
            context: The state before demand, and the period's date and window.

        Returns:
            An ``OrderAdjustmentResult``, or ``None`` for no change.
        """
        return None

    def get_config(self) -> dict:
        """Settings recorded in the run manifest.

        Returns:
            A JSON-serialisable dict (empty by default).
        """
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
    """Set the order to a fixed quantity on scheduled dates.

    Args:
        schedule: One row per SKU and date (or state period), with columns
            ``unique_id``, ``date`` and/or ``period``, and non-blank ``reason`` and
            ``source`` strings recorded in the audit; plus ``order_quantity`` (>= 0).
    """

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
    """Multiply the policy's order on scheduled dates.

    Args:
        schedule: One row per SKU and date (or state period), with columns
            ``unique_id``, ``date`` and/or ``period``, and non-blank ``reason`` and
            ``source`` strings recorded in the audit; plus ``multiplier`` (>= 0).
    """

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
    """Set the order to zero on scheduled dates, for example supplier holidays.

    Args:
        schedule: One row per SKU and date (or state period), with columns
            ``unique_id``, ``date`` and/or ``period``, and non-blank ``reason`` and
            ``source`` strings recorded in the audit.
    """

    def on_after_prediction(self, decision, context):
        matching = self._matching(context)
        if matching.empty:
            return None
        matching["order_quantity"] = 0.0
        return OrderAdjustmentResult(
            matching[["unique_id", "order_quantity", "reason", "source"]]
        )


class ScheduledInventoryAdjustment(_ScheduledCallback):
    """Add or remove on-hand stock after demand on scheduled dates.

    Useful for stock counts, damages, and one-off corrections. Removals cannot
    exceed on-hand stock, and stock cannot be added while the SKU has backorders.

    Args:
        schedule: One row per SKU and date (or state period), with columns
            ``unique_id``, ``date`` and/or ``period``, and non-blank ``reason`` and
            ``source`` strings recorded in the audit; plus a signed ``quantity_delta`` and, for stock added
            under shelf life, an optional ``received_date``.
    """

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
