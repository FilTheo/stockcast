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

from stockcast.core.data_structures import InventoryStateDataFrame, OrderDecision


class BasePolicy:
    """
    Base class for all inventory policies (like nn.Module in PyTorch).

    All inventory policies inherit from this class and implement:
        - fit(): Calculate policy parameters from forecast data
        - predict(): Calculate order quantities from current inventory

    Provides common attributes (lead_time, review_period, service_level,
    allow_backorders) and the fitted_ flag.

    Example (custom policy):
        class SimpleMultiplierPolicy(BasePolicy):
            def __init__(self, lead_time, review_period, multiplier, **kwargs):
                super().__init__(lead_time, review_period, **kwargs)
                self.multiplier = multiplier

            def fit(self, forecast_df, **kwargs):
                self.forecast_df_ = forecast_df.copy()
                self.fitted_ = True
                return self

            def predict(self, inventory_state_df, **kwargs):
                # Custom ordering logic
                ...
                return OrderDecision(result_df, lead_time=self.lead_time,
                                     review_period=self.review_period)
    """

    def __init__(self,
                 lead_time: int,
                 review_period: int,
                 service_level: Optional[float],
                 allow_backorders: bool):
        """
        Initialize base policy with common parameters.

        Args:
            lead_time: Lead time in periods (L)
            review_period: Review period in periods (R)
            service_level: Explicit target service level, or ``None`` only for
                custom policies that do not use a probabilistic target
            allow_backorders: Whether to allow backorders or treat as lost sales
        """
        if not isinstance(lead_time, int) or isinstance(lead_time, bool) or lead_time < 1:
            raise ValueError(
                "lead_time must be an integer >= 1; same-period replenishment "
                "is not implemented"
            )
        if not isinstance(review_period, int) or isinstance(review_period, bool) or review_period < 1:
            raise ValueError("review_period must be an integer >= 1")
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
        """
        Calculate policy parameters from forecast data.

        Must be overridden by subclasses.

        Args:
            forecast_df: DataFrame with forecast data
            **kwargs: Additional policy-specific parameters

        Returns:
            self (for method chaining)
        """
        raise NotImplementedError("Subclasses must implement fit()")

    def predict(self,
                inventory_state_df: Union[pd.DataFrame, InventoryStateDataFrame],
                **kwargs: object) -> Union[OrderDecision, pd.DataFrame]:
        """
        Calculate order quantities from current inventory state.

        Must be overridden by subclasses.

        Args:
            inventory_state_df: Current inventory state
            **kwargs: Additional policy-specific parameters

        Returns:
            OrderDecision object or DataFrame with order quantities
        """
        raise NotImplementedError("Subclasses must implement predict()")

    def __repr__(self) -> str:
        status = "fitted" if self.fitted_ else "not fitted"
        return (f"{self.policy_name}(L={self.lead_time}, R={self.review_period}, "
                f"SL={self.service_level}, {status})")
