"""Periodic-review (R,s,S) policy with pluggable target providers."""

import json

import numpy as np
import pandas as pd

from stockcast.core.base_policy import BasePolicy
from stockcast.core.data_structures import InventoryStateDataFrame, OrderDecision
from stockcast.policies._target_validation import (
    prepare_inventory_positions,
    validate_forecast_origin_and_frequency,
)
from stockcast.policies.periodic_targets import (
    PeriodicReviewTargetProvider,
    validate_periodic_review_targets,
)


class PeriodicReviewPolicy(BasePolicy):
    """Periodic ``(R,s,S)`` policy checked every ``review_period`` periods.

    The policy executes the replenishment rule. A target provider supplies the
    already-calculated ``s`` and ``S`` values. Stockcast does not imply a forecast
    horizon or aggregation method for externally supplied targets.
    """

    def __init__(
        self,
        lead_time: int,
        review_period: int,
        *,
        service_level: float,
        allow_backorders: bool,
    ):
        if service_level is None:
            raise ValueError("service_level is required for PeriodicReviewPolicy")
        super().__init__(lead_time, review_period, service_level, allow_backorders)
        self.policy_name = "Periodic Review (R,s,S)"
        self.parameters_ = None

    def fit(
        self,
        target_data: pd.DataFrame,
        *,
        target_provider: PeriodicReviewTargetProvider,
        information_origin,
        information_frequency: str,
        sku_column: str = "unique_id",
    ) -> "PeriodicReviewPolicy":
        if not isinstance(target_data, pd.DataFrame) or target_data.empty:
            raise ValueError("target_data must be a non-empty pandas DataFrame")
        if not isinstance(target_provider, PeriodicReviewTargetProvider):
            raise TypeError("target_provider must be a PeriodicReviewTargetProvider")
        origin, offset = validate_forecast_origin_and_frequency(
            information_origin,
            information_frequency,
        )
        provider_result = target_provider.provide(
            target_data.copy(),
            sku_column=sku_column,
        )
        parameters = validate_periodic_review_targets(
            provider_result,
            sku_column=sku_column,
        )
        if target_provider.target_source not in {"external_direct", "custom_provider"}:
            raise ValueError(
                "target provider target_source must be 'external_direct' or "
                "'custom_provider'"
            )
        provider_manifest = target_provider.to_manifest()
        if not isinstance(provider_manifest, dict):
            raise TypeError("target provider manifest must be a dictionary")
        try:
            json.dumps({
                "provider": provider_manifest,
                "provider_metadata": provider_result.metadata,
            })
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "target provider manifest and metadata must be JSON-serializable"
            ) from exc

        self.parameters_ = parameters
        self.sku_column_ = sku_column
        self.target_df_ = parameters.copy()
        self.target_metadata_ = {
            "representation": "periodic_review_targets",
            "target_source": target_provider.target_source,
            "provider": provider_manifest,
            "provider_metadata": provider_result.metadata.copy(),
            "forecast_origin": origin.isoformat(),
            "forecast_frequency": offset.freqstr,
        }
        self.fitted_ = True
        return self

    def predict(
        self,
        inventory_state_df: pd.DataFrame | InventoryStateDataFrame,
        sku_column: str | None = None,
        *,
        current_period: int,
        return_dataframe: bool = False,
    ) -> OrderDecision | pd.DataFrame:
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
        inventory_df = prepare_inventory_positions(
            inventory_df,
            sku_column,
            allow_components=False,
        )

        orders = inventory_df[[sku_column, "inventory_position"]].merge(
            self.parameters_,
            on=sku_column,
            how="left",
        )
        missing = orders.loc[orders["reorder_point"].isna(), sku_column].tolist()
        if missing:
            raise ValueError(f"No periodic-review targets were fitted for SKUs: {missing[:5]}")
        should_order = orders["inventory_position"] <= orders["reorder_point"]
        orders["order_quantity"] = np.where(
            should_order,
            np.maximum(0.0, orders["order_up_to_level"] - orders["inventory_position"]),
            0.0,
        )
        orders["target_level"] = orders["order_up_to_level"]
        orders["order_period"] = current_period
        orders["expected_delivery_period"] = current_period + self.lead_time
        result = orders[[
            sku_column,
            "order_quantity",
            "target_level",
            "inventory_position",
            "reorder_point",
            "order_period",
            "expected_delivery_period",
        ]]
        if return_dataframe:
            return result
        return OrderDecision(
            result,
            sku_column=sku_column,
            lead_time=self.lead_time,
            review_period=self.review_period,
        )

    def get_parameters(self) -> pd.DataFrame:
        if not self.fitted_:
            raise ValueError("Policy must be fitted first")
        return self.parameters_.copy()

    def get_target_metadata(self) -> dict:
        if not self.fitted_:
            raise ValueError("Policy must be fitted first")
        return self.target_metadata_.copy()
