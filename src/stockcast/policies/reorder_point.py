"""Reorder-point ``(s,Q)`` and ``(s,S)`` rules driven by a decision schedule.

The rule decides *how much* to order; the ``DecisionSchedule`` decides *when*
the rule is consulted. Stockcast simulates discrete periods with demand
aggregated per period, so there is no true continuous review: a rule checked
every period is periodic review with ``R = 1``.

Protection window. Decisions occur before demand. If the rule does not order
at ``t`` and the next opportunity is ``u``, the next order becomes usable
before demand ``u + L``. The position at ``t`` is therefore exposed to demand
``t .. u + L - 1``, a window of ``H = (u - t) + L`` periods: ``L + R`` for a
periodic schedule and ``L + 1`` for every-period review. A demand quantile over
``H`` is a justified basis for ``s``; it is not a guarantee of realized cycle
service or fill rate, which also depend on undershoot, ``Q``, outstanding
orders, the shortage mode, and the demand process.
"""

from typing import Literal, Optional, Union

import numpy as np
import pandas as pd

from stockcast.core.base_policy import BasePolicy
from stockcast.core.data_structures import InventoryStateDataFrame, OrderDecision
from stockcast.core.decision_schedule import DecisionSchedule
from stockcast.policies._target_validation import (
    _QUANTILE_COLUMN,
    prepare_direct_targets,
    prepare_inventory_positions,
    schedule_protection_horizon,
    validate_forecast_origin_and_frequency,
    validate_schedule_coverage,
    validate_target_end_dates,
    validate_target_probability,
    validate_target_source,
)


class ReorderPointPolicy(BasePolicy):
    """Order when inventory position is at or below a reorder point ``s``.

    - ``(s,Q)``: order a fixed, explicitly sourced quantity ``Q``.
    - ``(s,S)``: order up to ``S``.

    Review timing comes from ``review_period`` or an explicit ``schedule``; use
    ``review_period=1`` for every-period review. ``s`` is a dated target over
    the schedule's protection window (see the module docstring).

    Two target modes:

    - quantile: set ``service_level`` and declare the same ``target_probability``
      at fit; ``s`` is the external demand quantile over the window.
    - planner: leave ``service_level=None``; ``s`` (and ``S``) are externally
      chosen policy parameters, for example jointly optimized ``(s,S)`` pairs.

    ``S`` is never interpreted as a quantile: in the literature ``s`` and ``S``
    are generally determined jointly. Stockcast only requires ``S >= s``.
    """

    def __init__(
        self,
        lead_time: int,
        review_period: Optional[int] = None,
        *,
        policy_type: Literal["sQ", "sS"],
        service_level: Optional[float] = None,
        order_quantity: Optional[float] = None,
        order_quantity_source: Optional[str] = None,
        allow_backorders: bool,
        schedule: Optional[DecisionSchedule] = None,
    ):
        super().__init__(
            lead_time, review_period, service_level, allow_backorders, schedule=schedule,
        )
        if policy_type not in ("sQ", "sS"):
            raise ValueError("policy_type must be 'sQ' or 'sS'")
        self.policy_type = policy_type
        self.policy_name = f"Reorder Point ({policy_type})"
        if policy_type == "sQ":
            if order_quantity is None:
                raise ValueError("order_quantity is required for an (s,Q) policy")
            try:
                order_quantity = float(order_quantity)
            except (TypeError, ValueError) as exc:
                raise ValueError("order_quantity must be a finite number > 0") from exc
            if not np.isfinite(order_quantity) or order_quantity <= 0:
                raise ValueError("order_quantity must be a finite number > 0")
            if not isinstance(order_quantity_source, str) or not order_quantity_source.strip():
                raise ValueError("order_quantity_source is required for an (s,Q) policy")
            order_quantity_source = order_quantity_source.strip()
        elif order_quantity is not None or order_quantity_source is not None:
            raise ValueError("order_quantity inputs apply only to an (s,Q) policy")
        self.order_quantity = order_quantity
        self.order_quantity_source = order_quantity_source
        self.reorder_points_ = None
        self.order_quantities_ = None
        self.order_up_to_levels_ = None

    def fit(
        self,
        target_df: pd.DataFrame,
        *,
        forecast_origin: pd.Timestamp,
        forecast_frequency: str,
        reorder_point_column: str,
        reorder_end_date_column: str,
        reorder_horizon: int,
        target_source: str,
        target_probability: Optional[float] = None,
        order_up_to_column: Optional[str] = None,
        sku_column: str = "unique_id",
    ) -> "ReorderPointPolicy":
        """Bind one external reorder point (and ``S`` for ``(s,S)``) per SKU.

        Args:
            target_df: One row per SKU.
            forecast_origin: Last observed demand date used to build ``s``.
            forecast_frequency: Explicit pandas frequency of one period.
            reorder_point_column: Column holding ``s``.
            reorder_end_date_column: Date of the last demand epoch ``s`` covers,
                ``forecast_origin + reorder_horizon`` periods.
            reorder_horizon: Protection window of ``s``. For a periodic
                schedule it must equal ``lead_time + review_period``; other
                schedules are checked at each decision against
                ``(next opportunity - decision) + lead_time``.
            target_source: Exactly ``"external_direct"``.
            target_probability: Required in quantile mode and must equal
                ``service_level``; must be omitted in planner mode.
            order_up_to_column: Column holding ``S`` for an ``(s,S)`` policy.
            sku_column: SKU identifier column.
        """
        origin, offset = validate_forecast_origin_and_frequency(forecast_origin, forecast_frequency)
        horizon = schedule_protection_horizon(
            self.schedule, self.lead_time, reorder_horizon, "reorder_horizon",
        )
        if self.service_level is None:
            if target_probability is not None:
                raise ValueError("set service_level when declaring target_probability")
            if _QUANTILE_COLUMN.match(str(reorder_point_column)):
                raise ValueError("quantile-labelled targets require explicit probability")
            probability = None
        else:
            probability = validate_target_probability(
                self.service_level, target_probability, reorder_point_column,
            )
        source = validate_target_source(target_source)

        columns = [reorder_point_column]
        if self.policy_type == "sS":
            if order_up_to_column is None:
                raise ValueError("order_up_to_column is required for an (s,S) policy")
            columns.append(order_up_to_column)
        elif order_up_to_column is not None:
            raise ValueError("order_up_to_column applies only to an (s,S) policy")
        prepared = prepare_direct_targets(target_df, sku_column, columns)
        end_date = validate_target_end_dates(
            prepared, reorder_end_date_column, origin, offset, horizon,
        )
        self.reorder_points_ = prepared[[sku_column, reorder_point_column]].rename(
            columns={reorder_point_column: "reorder_point"}
        )
        if self.policy_type == "sQ":
            self.order_quantities_ = self.reorder_points_[[sku_column]].copy()
            self.order_quantities_["order_quantity"] = self.order_quantity
        else:
            if (prepared[order_up_to_column] < prepared[reorder_point_column]).any():
                raise ValueError("order-up-to levels must be greater than or equal to reorder points")
            self.order_up_to_levels_ = prepared[[sku_column, order_up_to_column]].rename(
                columns={order_up_to_column: "order_up_to_level"}
            )

        self.target_df_ = target_df.copy()
        self.sku_column_ = sku_column
        self.target_metadata_ = {
            "representation": (
                "direct_reorder_point_target" if probability is not None
                else "external_reorder_point"
            ),
            "target_probability": probability,
            "reorder_horizon": horizon,
            "target_source": source,
            "order_quantity_source": self.order_quantity_source,
            "forecast_origin": origin.isoformat(),
            "forecast_frequency": offset.freqstr,
            "reorder_end_date": end_date.isoformat(),
        }
        if self.policy_type == "sS":
            self.target_metadata_["order_up_to_representation"] = "external_policy_level"
        self.fitted_ = True
        return self

    def validate_decision_window(self, period, information_date, offset):
        """Check, before a run, that ``s`` covers the decision's window.

        Args:
            period: Zero-based demand period of the decision.
            information_date: Date of the last demand known at the decision.
            offset: The simulation's period frequency.

        Raises:
            ValueError: If the reorder horizon or dates do not match.
        """
        validate_schedule_coverage(
            self.schedule,
            lead_time=self.lead_time,
            period=period,
            horizon=self.target_metadata_["reorder_horizon"],
            forecast_origin=self.target_metadata_["forecast_origin"],
            target_end_date=self.target_metadata_["reorder_end_date"],
            information_date=information_date,
            offset=offset,
            label="reorder_horizon",
        )

    def predict(
        self,
        inventory_state_df: Union[pd.DataFrame, InventoryStateDataFrame],
        sku_column: Optional[str] = None,
        *,
        current_period: int,
        return_dataframe: bool = False,
    ) -> Union[OrderDecision, pd.DataFrame]:
        """Order when ``inventory_position <= s``.

        ``(s,Q)`` orders ``Q``; ``(s,S)`` orders ``max(0, S - inventory_position)``.
        """
        if not self.fitted_:
            raise ValueError("Policy must be fitted before prediction. Call fit() first.")
        if not isinstance(current_period, int) or isinstance(current_period, bool) or current_period < 0:
            raise ValueError("current_period must be an integer >= 0")
        sku_column = sku_column or self.sku_column_
        position_method = getattr(inventory_state_df, "inventory_position", None)
        if isinstance(inventory_state_df, InventoryStateDataFrame) or callable(position_method):
            inventory_df = inventory_state_df.inventory_position()
        else:
            inventory_df = inventory_state_df.copy()
        inventory_df = prepare_inventory_positions(inventory_df, sku_column, allow_components=True)

        orders = inventory_df[[sku_column, "inventory_position"]].merge(
            self.reorder_points_, on=sku_column, how="left",
        )
        missing = orders.loc[orders["reorder_point"].isna(), sku_column].tolist()
        if missing:
            raise ValueError(f"No reorder_point was fitted for SKUs: {missing[:5]}")
        should_order = orders["inventory_position"] <= orders["reorder_point"]
        if self.policy_type == "sQ":
            orders = orders.merge(self.order_quantities_, on=sku_column, how="left")
            orders["order_quantity"] = np.where(should_order, orders["order_quantity"], 0.0)
            orders["target_level"] = np.nan
        else:
            orders = orders.merge(self.order_up_to_levels_, on=sku_column, how="left")
            orders["order_quantity"] = np.where(
                should_order,
                np.maximum(0.0, orders["order_up_to_level"] - orders["inventory_position"]),
                0.0,
            )
            orders["target_level"] = orders["order_up_to_level"]
        orders["order_period"] = current_period
        orders["expected_delivery_period"] = current_period + self.lead_time
        result = orders[[
            sku_column, "order_quantity", "target_level", "inventory_position",
            "reorder_point", "order_period", "expected_delivery_period",
        ]]
        if return_dataframe:
            return result
        return OrderDecision(
            result, sku_column=sku_column, lead_time=self.lead_time,
            review_period=self.review_period,
        )

    def get_reorder_points(self) -> pd.DataFrame:
        """Return the fitted reorder point ``s`` per SKU.

        Returns:
            A DataFrame with the SKU column and ``reorder_point``.

        Raises:
            ValueError: If the policy is not fitted.
        """
        if not self.fitted_:
            raise ValueError("Policy must be fitted first. Call fit() to set reorder points.")
        return self.reorder_points_.copy()

    def get_parameters(self) -> pd.DataFrame:
        """Return ``s`` with ``Q`` (for ``(s,Q)``) or ``S`` (for ``(s,S)``) per SKU."""
        if not self.fitted_:
            raise ValueError("Policy must be fitted first. Call fit() to set parameters.")
        other = self.order_quantities_ if self.policy_type == "sQ" else self.order_up_to_levels_
        return self.reorder_points_.merge(other, on=self.sku_column_, how="left")

    def get_target_metadata(self) -> dict:
        """Return a copy of target, window, and quantity provenance."""
        if not self.fitted_:
            raise ValueError("Policy must be fitted first. Call fit() to set parameters.")
        return self.target_metadata_.copy()

    def __repr__(self) -> str:
        status = "fitted" if self.fitted_ else "not fitted"
        mode = "backorders" if self.allow_backorders else "lost_sales"
        return (
            f"ReorderPointPolicy({self.policy_type}, lead_time={self.lead_time}, "
            f"schedule={self.schedule.to_manifest()}, service_level={self.service_level}, "
            f"allow_backorders={mode}, status={status})"
        )
