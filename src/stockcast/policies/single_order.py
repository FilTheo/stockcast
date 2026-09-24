"""One purchase for an explicitly dated selling season; targets remain external."""

import math

import pandas as pd

from stockcast.core.decision_schedule import OneTimeSchedule
from stockcast.policies._target_validation import validate_forecast_origin_and_frequency
from stockcast.policies.order_up_to import OrderUpToPolicy


def newsvendor_critical_fractile(*, selling_price, purchase_cost, salvage_value):
    """Classical lost-sales fractile (p-c)/(p-v), requiring p > c > v >= 0.

    Assumes linear unit economics, no extra shortage/holding costs, and no
    replenishment during the season. This calculates a probability, not a
    demand distribution or constrained optimal order quantity.
    """
    values = (selling_price, purchase_cost, salvage_value)
    if any(isinstance(value, bool) for value in values):
        raise ValueError("costs must be finite numbers satisfying p > c > v >= 0")
    try:
        p, c, v = map(float, values)
    except (TypeError, ValueError) as exc:
        raise ValueError("costs must be finite numbers") from exc
    if not all(math.isfinite(value) for value in (p, c, v)) or not p > c > v >= 0:
        raise ValueError(
            "costs must satisfy selling_price > purchase_cost > salvage_value >= 0"
        )
    return (p - c) / (p - v)


class SingleOrderPolicy(OrderUpToPolicy):
    """Order once to an external season target, net of available inventory.

    The season starts when this decision's order arrives and lasts
    ``selling_horizon`` demand epochs. Supply zero demand before/after the
    season, and enough simulation periods to observe its end. Quantile targets
    declare service_level; planner targets need no fictional probability.
    """

    def __init__(
        self,
        lead_time: int,
        *,
        selling_horizon: int,
        decision_period: int = 0,
        service_level=None,
        allow_backorders: bool,
    ):
        if (
            not isinstance(selling_horizon, int)
            or isinstance(selling_horizon, bool)
            or selling_horizon < 1
        ):
            raise ValueError("selling_horizon must be an integer >= 1")
        super().__init__(
            lead_time,
            service_level=service_level,
            allow_backorders=allow_backorders,
            schedule=OneTimeSchedule(decision_period),
        )
        self.selling_horizon = selling_horizon
        self.policy_name = "Single seasonal order"

    def fit(
        self,
        target_df,
        *,
        forecast_origin,
        forecast_frequency,
        target_column,
        target_end_date_column,
        target_source,
        target_probability=None,
        sku_column="unique_id",
    ):
        origin, offset = validate_forecast_origin_and_frequency(
            forecast_origin, forecast_frequency
        )
        # The generic direct-target validator counts periods from the point
        # immediately preceding the first covered demand. Information was
        # available earlier, at origin; preserve that actual cutoff below.
        super().fit(
            target_df,
            forecast_origin=origin + self.lead_time * offset,
            forecast_frequency=forecast_frequency,
            target_column=target_column,
            target_end_date_column=target_end_date_column,
            target_source=target_source,
            target_probability=target_probability,
            protection_horizon=self.selling_horizon,
            sku_column=sku_column,
        )
        self.target_metadata_.update(
            representation="single_season_target",
            forecast_origin=origin.isoformat(),
            coverage_start_date=(origin + (self.lead_time + 1) * offset).isoformat(),
            selling_horizon=self.selling_horizon,
        )
        return self

    def validate_decision_window(self, period, information_date, offset):
        metadata = self.target_metadata_
        if pd.Timestamp(metadata["forecast_origin"]) != information_date:
            raise ValueError(
                "single-order target requires the exact decision information origin"
            )
        if (
            pd.Timestamp(metadata["target_end_date"])
            != information_date + (self.lead_time + self.selling_horizon) * offset
        ):
            raise ValueError("single-order target must cover the selling season")

    def validate_demand_window(self, demand, n_periods):
        first = self.schedule.period + self.lead_time
        end = first + self.selling_horizon
        if end > n_periods:
            raise ValueError("simulation must observe the complete selling season")
        outside = (demand["period"] < first) | (demand["period"] >= end)
        if (demand.loc[outside, "y"] != 0).any():
            raise ValueError(
                "SingleOrderPolicy requires zero demand outside the selling season"
            )

    def predict(self, inventory_state_df, **kwargs):
        # Late pipeline must not suppress this season's purchase. Reject rather
        # than silently counting goods that cannot be present at season start.
        frame = inventory_state_df.get_dataframe()
        if (
            frame["in_transit"]
            .map(lambda pipeline: sum(pipeline[self.lead_time :]) > 0)
            .any()
        ):
            raise ValueError(
                "single-order opening pipeline must arrive by season start"
            )
        return super().predict(inventory_state_df, **kwargs)
