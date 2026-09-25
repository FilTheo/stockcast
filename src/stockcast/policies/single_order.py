"""One purchase for an explicitly dated selling season; targets remain external."""

import math

import pandas as pd

from stockcast.core.decision_schedule import OneTimeSchedule
from stockcast.policies._target_validation import validate_forecast_origin_and_frequency
from stockcast.policies.order_up_to import OrderUpToPolicy


def newsvendor_critical_fractile(*, selling_price, purchase_cost, salvage_value):
    """The newsvendor critical fractile ``(p - c) / (p - v)``.

    For one purchase with selling price ``p``, purchase cost ``c`` and salvage
    value ``v``, the profit-maximising quantity is the demand quantile at this
    probability. It assumes linear costs, lost sales, and no second purchase.

    Args:
        selling_price: ``p``.
        purchase_cost: ``c``.
        salvage_value: ``v`` for each unsold unit.

    Returns:
        The probability, between 0 and 1.

    Raises:
        ValueError: Unless ``p > c > v >= 0``.

    Example:
        ```python
        newsvendor_critical_fractile(selling_price=10, purchase_cost=4, salvage_value=2)
        # 0.75
        ```
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
    """Buy once for a selling season (newsvendor).

    The policy decides once, at ``decision_period``. The order arrives after
    ``lead_time`` periods, which is when the season starts; the season lasts
    ``selling_horizon`` periods. The target is a quantile of total season demand
    (for example at ``newsvendor_critical_fractile``), and the order is the
    target minus the stock already available.

    Demand outside the season must be zero, the run must cover the whole season,
    and any opening pipeline must arrive by the season's start.

    Args:
        lead_time: Periods from the order to the start of the season, >= 0.
        selling_horizon: Length of the season in periods, >= 1.
        decision_period: Demand period of the single decision (default 0).
        service_level: Probability the target represents, or ``None``.
        allow_backorders: ``True`` or ``False``.
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
        """Bind the season target.

        Args:
            target_df: One row per SKU.
            forecast_origin: Date of the last information used, normally the date
                before the decision.
            forecast_frequency: Period frequency, such as ``"D"``.
            target_column: Column with the season target.
            target_end_date_column: Last date of the season:
                ``forecast_origin + (lead_time + selling_horizon)`` periods.
            target_source: ``"external_direct"``.
            target_probability: Must equal ``service_level`` when one is set.
            sku_column: SKU column name.

        Returns:
            The fitted policy (``self``).
        """
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
        """Check the demand table: zero outside the season, and the whole season simulated.
        """
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
        """Order the season target minus available stock.

        Raises:
            ValueError: If opening pipeline would arrive after the season starts.
        """
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
