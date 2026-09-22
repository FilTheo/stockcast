"""
Order-Up-To inventory policy implementation.

This module implements the (R,S) periodic review policy where inventory
is reviewed every R periods and orders are placed to bring inventory
up to the target level S.
"""

import pandas as pd
import numpy as np
import warnings
from statistics import NormalDist
from typing import Optional, Union

from stockcast.core.data_structures import InventoryStateDataFrame, OrderDecision
from stockcast.core.base_policy import BasePolicy
from stockcast.policies._target_validation import (
    prepare_direct_targets,
    prepare_independent_normal_forecasts,
    prepare_inventory_positions,
    validate_aggregation_method,
    validate_forecast_origin_and_frequency,
    validate_protection_horizon,
    validate_target_end_dates,
    validate_target_probability,
    validate_target_source,
)


class OrderUpToPolicy(BasePolicy):
    """
    Order-Up-To (R,S) inventory policy with fit/predict API.

    This policy reviews inventory every R periods and places orders to
    bring the inventory position up to the target level S.

    Usage:
        # Initialize with policy parameters
        policy = OrderUpToPolicy(
            lead_time=7,
            review_period=7,
            service_level=0.95,
            allow_backorders=False,
        )

        # Fit: accept a protection-period target calculated outside Stockcast
        policy.fit(
            target_df,
            target_column="protection_q95",
            target_probability=0.95,
            protection_horizon=14,
            target_source="external_direct",
            forecast_origin=decision_date,
            forecast_frequency="D",
            target_end_date_column="protection_end_date",
        )

        # Predict: Calculate order quantities from current inventory
        orders_df = policy.predict(
            inventory_state_df=current_inventory,
            current_period=decision_period,
        )
    """

    def __init__(self,
                 lead_time: int,
                 review_period: int,
                 *,
                 service_level: float,
                 allow_backorders: bool):
        """
        Initialize Order-Up-To policy with configuration parameters.

        Args:
            lead_time: Lead time in periods (L)
            review_period: Review period in periods (R)
            service_level: Explicit target service level
            allow_backorders: Explicitly choose backorders or lost sales
        """
        if service_level is None:
            raise ValueError("service_level is required for OrderUpToPolicy")
        super().__init__(lead_time, review_period, service_level, allow_backorders)
        self.policy_name = "Order-Up-To (R,S)"
        self.safety_factor = NormalDist().inv_cdf(self.service_level)

        # Will be set during fit()
        self.target_levels_ = None  # DataFrame with target level S per SKU
        self.fitted_ = False

    def fit(
        self,
        forecast_df: pd.DataFrame,
        *,
        forecast_origin: pd.Timestamp,
        forecast_frequency: str,
        target_column: Optional[str] = None,
        target_end_date_column: Optional[str] = None,
        target_probability: Optional[float] = None,
        protection_horizon: Optional[int] = None,
        aggregation_method: Optional[str] = None,
        target_source: Optional[str] = None,
        sku_column: str = 'unique_id',
        mean_column: Optional[str] = None,
        std_column: Optional[str] = None,
        forecast_date_column: Optional[str] = None,
    ) -> "OrderUpToPolicy":
        """
        Calculate target inventory levels from explicit scientific inputs.

        Exactly one input mode must be selected:

        1. Supply one externally calculated protection-period target per SKU
           with ``target_column``.
        2. Supply consecutive marginal means and standard deviations with
           ``mean_column`` and ``std_column`` and explicitly select
           ``aggregation_method="independent_normal"``.

        Marginal quantiles are not accepted because their sum is not generally
        the quantile of cumulative protection-period demand.

        Args:
            forecast_df: Target or forecast DataFrame.
            forecast_origin: Information-set date at which the forecast or
                direct target was created.
            forecast_frequency: Explicit pandas frequency for forecast horizons.
            target_column: Direct protection-period target column. The frame
                must contain exactly one row per SKU in this mode.
            target_end_date_column: Date represented by a direct target.
            target_probability: Probability represented by the target. It must
                equal the policy's ``service_level``.
            protection_horizon: Number of periods represented by the target.
                It must equal ``lead_time + review_period``.
            aggregation_method: Exactly ``"independent_normal"`` for mean/std
                mode. It is not a label for externally supplied targets.
            target_source: Exactly ``"external_direct"`` for direct-target mode.
            sku_column: SKU identifier column.
            mean_column: Marginal forecast mean column for independent-normal mode.
            std_column: Marginal forecast standard deviation column for
                independent-normal mode. It is required; Stockcast never invents it.
            forecast_date_column: Target date column for horizon forecasts.

        Returns:
            self (for method chaining)
        """
        protection_period = self.lead_time + self.review_period
        validate_protection_horizon(protection_horizon, protection_period)
        origin, forecast_offset = validate_forecast_origin_and_frequency(
            forecast_origin,
            forecast_frequency,
        )

        direct_mode = target_column is not None
        normal_mode = mean_column is not None or std_column is not None
        if direct_mode == normal_mode:
            raise ValueError(
                "select exactly one target mode: target_column, or both "
                "mean_column and std_column"
            )

        target_levels = []
        if direct_mode:
            probability = validate_target_probability(
                self.service_level,
                target_probability,
                target_column,
            )
            source = validate_target_source(target_source)
            if aggregation_method is not None:
                raise ValueError(
                    "aggregation_method does not apply to direct targets; use "
                    "target_source='external_direct'"
                )
            prepared_targets = prepare_direct_targets(
                forecast_df,
                sku_column,
                [target_column],
            )
            target_end_date = validate_target_end_dates(
                prepared_targets,
                target_end_date_column,
                origin,
                forecast_offset,
                protection_period,
            )
            for row in prepared_targets[[sku_column, target_column]].itertuples(index=False):
                target_levels.append({sku_column: row[0], 'target_level': row[1]})
            representation = "direct_protection_period_target"
            method = None
        else:
            if target_source is not None:
                raise ValueError("target_source applies only to direct-target mode")
            if mean_column is None or std_column is None:
                raise ValueError("mean_column and std_column are both required")
            probability = validate_target_probability(
                self.service_level,
                target_probability,
                "independent_normal_target",
            )
            method = validate_aggregation_method(
                aggregation_method,
                expected="independent_normal",
            )
            prepared_forecasts = prepare_independent_normal_forecasts(
                forecast_df,
                sku_column,
                mean_column,
                std_column,
                protection_period,
                forecast_date_column,
                origin,
                forecast_offset,
            )
            for sku, sku_forecasts in prepared_forecasts.items():
                with np.errstate(over="ignore", invalid="ignore"):
                    mean_demand = float(sku_forecasts[mean_column].sum())
                    std_demand = float(
                        np.sqrt(np.square(sku_forecasts[std_column]).sum())
                    )
                    target_level = mean_demand + self.safety_factor * std_demand
                if not np.isfinite([mean_demand, std_demand, target_level]).all():
                    warnings.warn(
                        f"independent-normal aggregation produced a non-finite target for SKU {sku}",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    raise ValueError(
                        f"independent-normal target is non-finite for SKU {sku}; "
                        "check the scale of forecast means and standard deviations"
                    )
                if target_level < 0:
                    raise ValueError(
                        f"independent-normal target is negative for SKU {sku}; "
                        "supply an externally calculated non-negative target"
                    )
                target_levels.append({sku_column: sku, 'target_level': target_level})
            representation = "independent_normal_marginals"
            source = "built_in_calculation"
            target_end_date = origin + protection_period * forecast_offset

        self.target_levels_ = pd.DataFrame(target_levels)
        self.fitted_ = True
        self.sku_column_ = sku_column

        # Store the exact inputs and scientific provenance for inspection.
        self.forecast_df_ = forecast_df.copy()
        self.target_column_ = target_column
        self.mean_column_ = mean_column
        self.std_column_ = std_column
        self.target_metadata_ = {
            'representation': representation,
            'target_probability': probability,
            'protection_horizon': protection_period,
            'target_source': source,
            'forecast_origin': origin.isoformat(),
            'forecast_frequency': forecast_offset.freqstr,
            'target_end_date': target_end_date.isoformat(),
        }
        if method is not None:
            self.target_metadata_["calculation_method"] = method

        return self

    def predict(self,
                inventory_state_df: Union[pd.DataFrame, InventoryStateDataFrame],
                sku_column: Optional[str] = None,
                *,
                current_period: int,
                return_dataframe: bool = False) -> Union[OrderDecision, pd.DataFrame]:
        """
        Calculate order quantities for each SKU.

        Order quantity = max(0, S - IP)
        where S = target level (from fit)
              IP = inventory position (from inventory_state_df)

        Args:
            inventory_state_df: InventoryStateDataFrame object or DataFrame with inventory data
                            Must have 'inventory_position' or ['on_hand', 'on_order', 'backorders']
            sku_column: Column name for SKU identifier (uses fit() column if None)
            current_period: Explicit decision period used for order and delivery timing.
            return_dataframe: If True, return plain DataFrame instead of OrderDecision object

        Returns:
            OrderDecision object (or DataFrame if return_dataframe=True) with columns:
                - unique_id, order_quantity, target_level, inventory_position,
                - reorder_point (NaN for this policy), order_period, expected_delivery_period
        """
        if not self.fitted_:
            raise ValueError("Policy must be fitted before prediction. Call fit() first.")
        if not isinstance(current_period, int) or isinstance(current_period, bool) or current_period < 0:
            raise ValueError("current_period must be an integer >= 0")

        if sku_column is None:
            sku_column = self.sku_column_

        # Always use pre-computed target levels from fit().
        # For updated targets (e.g., reforecasting), call fit() again with new forecasts.
        dynamic_target_levels = self.target_levels_

        # Extract DataFrame if InventoryStateDataFrame object passed
        # Use duck typing to handle autoreload issues in Jupyter
        inventory_position_method = getattr(inventory_state_df, 'inventory_position', None)
        if isinstance(inventory_state_df, InventoryStateDataFrame) or callable(inventory_position_method):
            # Use the built-in inventory_position() method
            inventory_df = inventory_state_df.inventory_position()
        else:            # Plain DataFrame - calculate inventory position if needed
            inventory_df = inventory_state_df.copy()
        inventory_df = prepare_inventory_positions(
            inventory_df,
            sku_column,
            allow_components=True,
        )

        # Merge target levels with current inventory positions
        orders = inventory_df[[sku_column, 'inventory_position']].merge(
            dynamic_target_levels,
            on=sku_column,
            how='left'
        )
        missing_targets = orders[orders['target_level'].isna()][sku_column].tolist()
        if missing_targets:
            raise ValueError(f"No target_level was fitted for SKUs: {missing_targets[:5]}")

        # Calculate order quantity: Q = max(0, S - IP)
        orders['order_quantity'] = np.maximum(
            0,
            orders['target_level'] - orders['inventory_position']
        )

        # Add additional columns for OrderDecision structure
        orders['reorder_point'] = np.nan  # Not applicable for periodic review policy

        orders['order_period'] = current_period
        orders['expected_delivery_period'] = current_period + self.lead_time

        # Select columns in standard order
        result_df = orders[[
            sku_column,
            'order_quantity',
            'target_level',
            'inventory_position',
            'reorder_point',
            'order_period',
            'expected_delivery_period'
        ]]

        # Return as OrderDecision object or plain DataFrame
        if return_dataframe:
            return result_df
        else:
            return OrderDecision(
                result_df,
                sku_column=sku_column,
                lead_time=self.lead_time,
                review_period=self.review_period
            )

    def get_target_levels(self) -> pd.DataFrame:
        """
        Get the calculated target levels per SKU.

        Returns:
            DataFrame with target levels (only available after fit())
        """
        if not self.fitted_:
            raise ValueError("Policy must be fitted first. Call fit() to calculate target levels.")

        return self.target_levels_.copy()

    def get_target_metadata(self) -> dict:
        """Return a copy of the target probability and aggregation provenance."""
        if not self.fitted_:
            raise ValueError("Policy must be fitted first. Call fit() to calculate target levels.")
        return self.target_metadata_.copy()

    def __repr__(self) -> str:
        """String representation of the policy."""
        fitted_status = "fitted" if self.fitted_ else "not fitted"
        backorder_mode = "backorders" if self.allow_backorders else "lost_sales"
        return (f"OrderUpToPolicy(lead_time={self.lead_time}, "
                f"review_period={self.review_period}, "
                f"service_level={self.service_level}, "
                f"allow_backorders={backorder_mode}, "
                f"status={fitted_status})")
