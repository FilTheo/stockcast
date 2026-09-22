"""Traditional ``(s,Q)`` and ``(s,S)`` continuous-review policies.

Stockcast is a discrete-period simulator. These policies are evaluated once per
simulated period, not continuously in physical time.
"""

import pandas as pd
import numpy as np
from typing import Optional, Union, Literal

from stockcast.core.data_structures import InventoryStateDataFrame, OrderDecision
from stockcast.core.base_policy import BasePolicy
from stockcast.policies._target_validation import (
    prepare_direct_targets,
    prepare_inventory_positions,
    validate_forecast_origin_and_frequency,
    validate_protection_horizon,
    validate_target_end_dates,
    validate_target_probability,
    validate_target_source,
)


class ContinuousReviewPolicy(BasePolicy):
    """
    Traditional ``(s,Q)`` or ``(s,S)`` continuous-review inventory policy.

    The simulator evaluates the policy once per discrete simulated period
    (``review_period = 1``). "Continuous review" names the traditional policy
    family; it does not mean continuous physical-time observation.

    Policy Variants:
        - (s,Q): Order fixed quantity Q when IP ≤ s
        - (s,S): Order up to level S when IP ≤ s

    Usage:
        # Initialize with policy parameters
        policy = ContinuousReviewPolicy(
            lead_time=7,
            policy_type='sQ',
            service_level=0.95,
            order_quantity=100,
            order_quantity_source='contracted_case_quantity',
            allow_backorders=False,
        )

        # Fit explicit protection-period targets calculated outside Stockcast
        policy.fit(
            target_df,
            reorder_point_column='lead_time_q95',
            target_probability=0.95,
            reorder_horizon=7,
            target_source='external_direct',
            forecast_origin=decision_date,
            forecast_frequency='D',
            reorder_end_date_column='lead_time_end_date',
        )

        # Predict: Check inventory and calculate orders
        orders = policy.predict(
            inventory_state_df=current_inventory,
            current_period=decision_period,
        )
    """

    def __init__(self,
                 lead_time: int,
                 *,
                 policy_type: Literal['sQ', 'sS'],
                 service_level: float,
                 order_quantity: Optional[float] = None,
                 order_quantity_source: Optional[str] = None,
                 allow_backorders: bool):
        """
        Initialize Continuous Review policy with configuration parameters.

        Args:
            lead_time: Lead time in periods (L)
            policy_type: Explicit policy type: 'sQ' or 'sS'
            service_level: Explicit target service level
            order_quantity: Explicit fixed order quantity for an (s,Q) policy.
            order_quantity_source: Non-empty provenance for ``order_quantity``.
            allow_backorders: Explicitly choose backorders or lost sales
        """
        if service_level is None:
            raise ValueError("service_level is required for ContinuousReviewPolicy")
        # Continuous review: review_period=1 (check every period)
        super().__init__(lead_time, review_period=1, service_level=service_level,
                         allow_backorders=allow_backorders)
        self.policy_type = policy_type
        self.order_quantity = order_quantity
        self.order_quantity_source = order_quantity_source
        self.policy_name = f"Discrete-Period Review ({policy_type})"

        # Validate policy type
        if policy_type not in ['sQ', 'sS']:
            raise ValueError("policy_type must be 'sQ' or 'sS'")
        if policy_type == 'sQ':
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
            self.order_quantity = order_quantity
            self.order_quantity_source = order_quantity_source.strip()
        elif order_quantity is not None or order_quantity_source is not None:
            raise ValueError("order_quantity inputs apply only to an (s,Q) policy")

        # Will be set during fit()
        self.reorder_points_ = None
        self.order_quantities_ = None
        self.order_up_to_levels_ = None

    def fit(
        self,
        target_df: pd.DataFrame,
        *,
        forecast_origin,
        forecast_frequency: str,
        reorder_point_column: str,
        reorder_end_date_column: str,
        target_probability: float,
        reorder_horizon: int,
        target_source: str,
        sku_column: str = 'unique_id',
        order_up_to_column: Optional[str] = None,
        order_up_to_end_date_column: Optional[str] = None,
        order_up_to_horizon: Optional[int] = None,
        review_period_for_S: Optional[int] = None,
    ):
        """Fit explicit per-SKU reorder parameters.

        ``target_df`` must contain one row per SKU. Reorder points and, for an
        (s,S) policy, order-up-to levels must be protection-period targets
        calculated outside Stockcast. Their probability and source are mandatory
        and recorded on the policy.
        """
        origin, forecast_offset = validate_forecast_origin_and_frequency(
            forecast_origin,
            forecast_frequency,
        )
        validate_protection_horizon(reorder_horizon, self.lead_time, "reorder_horizon")
        probability = validate_target_probability(
            self.service_level,
            target_probability,
            reorder_point_column,
        )
        source = validate_target_source(target_source)

        target_columns = [reorder_point_column]
        if self.policy_type == 'sS':
            if not isinstance(review_period_for_S, int) or isinstance(review_period_for_S, bool):
                raise ValueError("review_period_for_S must be explicitly provided for an (s,S) policy")
            if review_period_for_S < 1:
                raise ValueError("review_period_for_S must be an integer >= 1")
            expected_order_up_to_horizon = self.lead_time + review_period_for_S
            validate_protection_horizon(
                order_up_to_horizon,
                expected_order_up_to_horizon,
                "order_up_to_horizon",
            )
            if order_up_to_column is None:
                raise ValueError("order_up_to_column is required for an (s,S) policy")
            validate_target_probability(
                self.service_level,
                target_probability,
                order_up_to_column,
            )
            target_columns.append(order_up_to_column)
            self.review_period_for_S_ = review_period_for_S
        else:
            unexpected = [
                order_up_to_column,
                order_up_to_horizon,
                review_period_for_S,
            ]
            if any(value is not None for value in unexpected):
                raise ValueError("order-up-to inputs apply only to an (s,S) policy")
            order_up_to_method = None

        prepared = prepare_direct_targets(target_df, sku_column, target_columns)
        reorder_end_date = validate_target_end_dates(
            prepared,
            reorder_end_date_column,
            origin,
            forecast_offset,
            self.lead_time,
        )
        self.reorder_points_ = prepared[[sku_column, reorder_point_column]].rename(
            columns={reorder_point_column: 'reorder_point'}
        )

        if self.policy_type == 'sQ':
            self.order_quantities_ = self.reorder_points_[[sku_column]].copy()
            self.order_quantities_['order_quantity'] = self.order_quantity
        else:
            order_up_to_end_date = validate_target_end_dates(
                prepared,
                order_up_to_end_date_column,
                origin,
                forecast_offset,
                self.lead_time + self.review_period_for_S_,
            )
            self.order_up_to_levels_ = prepared[[sku_column, order_up_to_column]].rename(
                columns={order_up_to_column: 'order_up_to_level'}
            )
            invalid_order_up_to = self.order_up_to_levels_.merge(
                self.reorder_points_, on=sku_column, how='inner'
            )
            if (invalid_order_up_to['order_up_to_level'] < invalid_order_up_to['reorder_point']).any():
                raise ValueError("order-up-to levels must be greater than or equal to reorder points")

        self.target_df_ = target_df.copy()
        self.sku_column_ = sku_column
        self.target_metadata_ = {
            'representation': 'direct_protection_period_targets',
            'target_probability': probability,
            'reorder_horizon': self.lead_time,
            'target_source': source,
            'order_quantity_source': self.order_quantity_source,
            'forecast_origin': origin.isoformat(),
            'forecast_frequency': forecast_offset.freqstr,
            'reorder_end_date': reorder_end_date.isoformat(),
        }
        if self.policy_type == 'sS':
            self.target_metadata_.update({
                'review_period_for_S': self.review_period_for_S_,
                'order_up_to_horizon': self.lead_time + self.review_period_for_S_,
                'order_up_to_end_date': order_up_to_end_date.isoformat(),
            })
        self.fitted_ = True
        return self

    def predict(self,
                inventory_state_df: Union[pd.DataFrame, InventoryStateDataFrame],
                sku_column: Optional[str] = None,
                *,
                current_period: int,
                return_dataframe: bool = False) -> Union[OrderDecision, pd.DataFrame]:
        """
        Check inventory positions and calculate order quantities for each SKU.

        For each SKU:
            - If IP ≤ s: Place order
            - (s,Q): Order quantity = Q
            - (s,S): Order quantity = max(0, S - IP)

        Args:
            inventory_state_df: InventoryStateDataFrame object or DataFrame with inventory data
            sku_column: Column name for SKU identifier (uses fit() column if None)
            current_period: Explicit decision period used for order and delivery timing.
            return_dataframe: If True, return plain DataFrame instead of OrderDecision object

        Returns:
            OrderDecision object (or DataFrame if return_dataframe=True) with columns:
                - unique_id, order_quantity, target_level (S for sS, NaN for sQ),
                - reorder_point, inventory_position, order_period, expected_delivery_period
        """
        if not self.fitted_:
            raise ValueError("Policy must be fitted before prediction. Call fit() first.")
        if not isinstance(current_period, int) or isinstance(current_period, bool) or current_period < 0:
            raise ValueError("current_period must be an integer >= 0")

        if sku_column is None:
            sku_column = self.sku_column_

        # Extract DataFrame if InventoryStateDataFrame object passed
        inventory_position_method = getattr(inventory_state_df, 'inventory_position', None)
        if isinstance(inventory_state_df, InventoryStateDataFrame) or callable(inventory_position_method):
            inventory_df = inventory_state_df.inventory_position()
        else:
            inventory_df = inventory_state_df.copy()
        inventory_df = prepare_inventory_positions(
            inventory_df,
            sku_column,
            allow_components=True,
        )

        # Merge reorder points with current inventory positions
        orders = inventory_df[[sku_column, 'inventory_position']].merge(
            self.reorder_points_,
            on=sku_column,
            how='left'
        )
        missing_reorder_points = orders[orders['reorder_point'].isna()][sku_column].tolist()
        if missing_reorder_points:
            raise ValueError(f"No reorder_point was fitted for SKUs: {missing_reorder_points[:5]}")

        # Merge policy-specific parameters
        if self.policy_type == 'sQ':
            orders = orders.merge(
                self.order_quantities_,
                on=sku_column,
                how='left'
            )
            missing_quantities = orders[orders['order_quantity'].isna()][sku_column].tolist()
            if missing_quantities:
                raise ValueError(f"No order_quantity was fitted for SKUs: {missing_quantities[:5]}")
            # Only order if IP <= s
            orders['should_order'] = orders['inventory_position'] <= orders['reorder_point']
            orders['order_quantity'] = np.where(
                orders['should_order'],
                orders['order_quantity'],
                0.0
            )
            orders['target_level'] = np.nan  # Not applicable for (s,Q)

        elif self.policy_type == 'sS':
            orders = orders.merge(
                self.order_up_to_levels_,
                on=sku_column,
                how='left'
            )
            missing_targets = orders[orders['order_up_to_level'].isna()][sku_column].tolist()
            if missing_targets:
                raise ValueError(f"No order_up_to_level was fitted for SKUs: {missing_targets[:5]}")
            # Only order if IP <= s
            orders['should_order'] = orders['inventory_position'] <= orders['reorder_point']
            # Order up to S
            orders['order_quantity'] = np.where(
                orders['should_order'],
                np.maximum(0, orders['order_up_to_level'] - orders['inventory_position']),
                0.0
            )
            orders['target_level'] = orders['order_up_to_level']

        # Add order timing information
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
                review_period=1  # Continuous review = check every period
            )

    def get_reorder_points(self) -> pd.DataFrame:
        """
        Get the calculated reorder points per SKU.

        Returns:
            DataFrame with reorder points (only available after fit())
        """
        if not self.fitted_:
            raise ValueError("Policy must be fitted first. Call fit() to calculate reorder points.")

        return self.reorder_points_.copy()

    def get_parameters(self) -> pd.DataFrame:
        """
        Get all calculated policy parameters per SKU.

        Returns:
            DataFrame with reorder points and Q (for sQ) or S (for sS)
        """
        if not self.fitted_:
            raise ValueError("Policy must be fitted first. Call fit() to calculate parameters.")

        if self.policy_type == 'sQ':
            return self.reorder_points_.merge(
                self.order_quantities_,
                on=self.sku_column_,
                how='left'
            )
        elif self.policy_type == 'sS':
            return self.reorder_points_.merge(
                self.order_up_to_levels_,
                on=self.sku_column_,
                how='left'
            )

    def get_target_metadata(self) -> dict:
        """Return a copy of target, horizon, and quantity provenance."""
        if not self.fitted_:
            raise ValueError("Policy must be fitted first. Call fit() to calculate parameters.")
        return self.target_metadata_.copy()

    def __repr__(self) -> str:
        """String representation of the policy."""
        fitted_status = "fitted" if self.fitted_ else "not fitted"
        backorder_mode = "backorders" if self.allow_backorders else "lost_sales"
        policy_desc = f"({self.policy_type})"

        return (f"ContinuousReviewPolicy{policy_desc}(lead_time={self.lead_time}, "
                f"service_level={self.service_level}, "
                f"allow_backorders={backorder_mode}, "
                f"status={fitted_status})")
