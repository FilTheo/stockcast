"""
Base policy class for the DataFrame-based inventory management system.

This module provides the BasePolicy abstract class that all inventory policies
must inherit from. It follows the nn.Module pattern from PyTorch: users inherit
from BasePolicy and implement fit() and predict() for custom policies.

Example:
    class MyCustomPolicy(BasePolicy):
        def fit(self, forecast_df, **kwargs):
            # Calculate policy parameters from forecasts
            self.fitted_ = True
            return self

        def predict(self, inventory_state_df, **kwargs):
            # Calculate order quantities
            return OrderDecision(...)
"""

from typing import Optional, Union
import math
import pandas as pd

from stockcast.core.decision_schedule import DecisionSchedule, PeriodicSchedule
from stockcast.core.data_structures import InventoryStateDataFrame, OrderDecision


class BasePolicy:
    """Base class for every ordering policy, in the spirit of PyTorch's ``nn.Module``.

    A policy is configured with the operation (lead time, decision schedule,
    shortage rule), fitted on forecast information with ``fit``, and asked for
    orders with ``predict``. It reads a copy of the state and returns an
    ``OrderDecision``; it never changes stock. Subclasses implement ``fit`` (set
    ``self.fitted_ = True`` and return ``self``) and ``predict``.

    Example:
        ```python
        class DaysOfCover(BasePolicy):
            def __init__(self, days, **kwargs):
                super().__init__(**kwargs)
                self.days = days

            def fit(self, forecast_df, **kwargs):
                self.daily_mean_ = forecast_df.set_index("unique_id")["daily_mean"]
                self.fitted_ = True
                return self

            def predict(self, inventory_state_df, *, current_period, **kwargs):
                state = inventory_state_df.inventory_position()
                level = state["unique_id"].map(self.daily_mean_) * self.days
                orders = pd.DataFrame({
                    "unique_id": state["unique_id"],
                    "order_quantity": (level - state["inventory_position"]).clip(lower=0),
                    "order_period": current_period,
                    "expected_delivery_period": current_period + self.lead_time,
                })
                return OrderDecision(orders, lead_time=self.lead_time,
                                     review_period=self.review_period)
        ```
    """

    def __init__(self,
                 lead_time: int,
                 review_period: Optional[int] = None,
                 service_level: Optional[float] = None,
                 allow_backorders: bool = None,
                 *, schedule: Optional[DecisionSchedule] = None):
        """Configure the policy's operation.

        Args:
            lead_time: Periods from order to delivery, an integer >= 0. An order
                placed in period ``t`` arrives before demand in period ``t + L``.
            review_period: Periods between ordering opportunities, an integer >= 1.
                Shorthand for ``schedule=PeriodicSchedule(review_period)``.
            service_level: Probability that the policy's targets represent, in
                (0, 1), or ``None`` when targets are not quantiles.
            allow_backorders: ``True`` to keep unserved demand as backorders,
                ``False`` for lost sales. Required.
            schedule: A ``DecisionSchedule``. Give either this or ``review_period``
                (or both, if they agree).

        Raises:
            ValueError: If a value is out of range or the timing arguments disagree.
        """
        if not isinstance(lead_time, int) or isinstance(lead_time, bool) or lead_time < 0:
            raise ValueError("lead_time must be an integer >= 0")
        if review_period is not None and (
            not isinstance(review_period, int) or isinstance(review_period, bool) or review_period < 1
        ):
            raise ValueError("review_period must be an integer >= 1")
        if schedule is None:
            if review_period is None:
                raise ValueError("supply schedule or review_period")
            schedule = PeriodicSchedule(review_period)
        if not isinstance(schedule, DecisionSchedule):
            raise TypeError("schedule must be a DecisionSchedule")
        if review_period is not None and (
            not isinstance(schedule, PeriodicSchedule) or schedule.every != review_period
        ):
            raise ValueError("review_period must agree with the periodic schedule")
        self.schedule = schedule
        review_period = schedule.every if isinstance(schedule, PeriodicSchedule) else None
        normalized_service_level = None
        if service_level is not None:
            if isinstance(service_level, bool):
                raise ValueError("service_level must be None or finite and between 0 and 1")
            try:
                normalized_service_level = float(service_level)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "service_level must be None or finite and between 0 and 1"
                ) from exc
            if not math.isfinite(normalized_service_level) or not 0 < normalized_service_level < 1:
                raise ValueError("service_level must be None or finite and between 0 and 1")
        if not isinstance(allow_backorders, bool):
            raise ValueError("allow_backorders must be explicitly True or False")

        self.lead_time = lead_time
        self.review_period = review_period
        self.service_level = normalized_service_level
        self.allow_backorders = allow_backorders
        self.fitted_ = False
        self.policy_name = self.__class__.__name__

    def fit(self, forecast_df: pd.DataFrame, **kwargs: object) -> 'BasePolicy':
        """Bind forecast information to the policy. Subclasses must implement it.

        Args:
            forecast_df: Targets or forecasts, one or more rows per SKU.
            **kwargs: Policy-specific options.

        Returns:
            The fitted policy (``self``), with ``fitted_ = True``.
        """
        raise NotImplementedError("Subclasses must implement fit()")

    def predict(self,
                inventory_state_df: Union[pd.DataFrame, InventoryStateDataFrame],
                **kwargs: object) -> Union[OrderDecision, pd.DataFrame]:
        """Propose orders for the given state. Subclasses must implement it.

        Args:
            inventory_state_df: A copy of the state before demand.
            **kwargs: The engine passes ``current_period`` (the state period).

        Returns:
            An ``OrderDecision`` with one row per SKU and the policy's ``lead_time``.
        """
        raise NotImplementedError("Subclasses must implement predict()")

    def __repr__(self) -> str:
        status = "fitted" if self.fitted_ else "not fitted"
        return (f"{self.policy_name}(L={self.lead_time}, R={self.review_period}, "
                f"SL={self.service_level}, {status})")
