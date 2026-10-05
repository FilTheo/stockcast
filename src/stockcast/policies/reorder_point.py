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

import json
from collections.abc import Mapping
from typing import Literal, Optional, Union

import numpy as np
import pandas as pd

from stockcast.core.base_policy import BasePolicy
from stockcast.core.data_structures import (
    InventoryStateDataFrame,
    OrderDecision,
    _require_forward_frequency,
    _require_identifiers,
)
from stockcast.core.decision_schedule import DecisionSchedule
from stockcast.policies._target_validation import (
    _QUANTILE_COLUMN,
    _require_column_name,
    prepare_direct_targets,
    prepare_inventory_positions,
    resolve_target_window,
    schedule_protection_horizon,
    validate_forecast_origin,
    validate_schedule_coverage,
    validate_target_probability,
)
from stockcast.policies.reorder_point_targets import (
    ReorderPointTargetProvider,
    validate_reorder_point_targets,
)


class ReorderPointPolicy(BasePolicy):
    """Order when inventory position is at or below a reorder point ``s``.

    - ``(s,Q)``: order a fixed, explicit quantity ``Q``.
    - ``(s,S)``: order up to ``S``.

    ``policy_type`` follows from the inputs: giving ``order_quantity`` makes an
    ``(s,Q)`` policy, otherwise ``(s,S)``. Passing it states the choice and is
    checked.

    Review timing comes from ``review_period`` or an explicit ``schedule``; use
    ``review_period=1`` for every-period review.

    ``fit`` takes ``s`` (and ``S``) from one of three sources:

    - a forecast table (``reorder_point_column``): ``s`` is dated and covers the
      schedule's protection window, checked at every decision (see the module
      docstring). With ``service_level`` set, ``s`` is the demand quantile at
      that probability; with ``service_level=None`` it is a planner value.
    - fixed values (``reorder_point=``): one number for every SKU, or one per
      SKU, for example jointly optimized ``(s,S)`` pairs.
    - your own rule (``target_provider=``), a ``ReorderPointTargetProvider``.

    ``S`` is never interpreted as a quantile: in the literature ``s`` and ``S``
    are generally determined jointly. Stockcast only requires ``S >= s``.

    ``freq`` is the length of one period (a pandas frequency such as ``"D"``);
    ``lead_time`` and ``review_period`` count periods of this length. Forecast
    targets need it; fixed values and target providers may omit it.

    ``sku_column`` (default ``"unique_id"``) names the SKU column of the tables
    given to ``fit`` and ``predict``; ``date_column`` (default ``"date"``) the
    date column of a forecast table: the last period each row's ``s`` covers.
    The default date column is read only if it exists; a column named
    explicitly must exist.
    """

    def __init__(
        self,
        lead_time: int,
        review_period: Optional[int] = None,
        *,
        freq: Optional[str] = None,
        policy_type: Optional[Literal["sQ", "sS"]] = None,
        service_level: Optional[float] = None,
        order_quantity: Optional[float] = None,
        allow_backorders: bool,
        schedule: Optional[DecisionSchedule] = None,
        sku_column: str = "unique_id",
        date_column: str = "date",
    ):
        super().__init__(
            lead_time, review_period, service_level, allow_backorders, schedule=schedule,
        )
        self.freq = freq
        self._freq_offset = None if freq is None else _require_forward_frequency(freq, "freq")
        self.sku_column = _require_column_name(sku_column, "sku_column")
        self.date_column = _require_column_name(date_column, "date_column")
        if policy_type is None:
            policy_type = "sQ" if order_quantity is not None else "sS"
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
        elif order_quantity is not None:
            raise ValueError("order_quantity applies only to an (s,Q) policy")
        self.order_quantity = order_quantity
        self._uniform_levels = None
        self.reorder_points_ = None
        self.order_quantities_ = None
        self.order_up_to_levels_ = None

    def fit(
        self,
        target_df: Optional[pd.DataFrame] = None,
        *,
        reorder_point_column: Optional[str] = None,
        forecast_origin: Optional[pd.Timestamp] = None,
        reorder_horizon: Optional[int] = None,
        target_probability: Optional[float] = None,
        order_up_to_column: Optional[str] = None,
        reorder_point=None,
        order_up_to_level=None,
        target_provider: Optional[ReorderPointTargetProvider] = None,
    ) -> "ReorderPointPolicy":
        """Set the reorder point ``s`` (and ``S`` for ``(s,S)``) per SKU.

        Choose exactly one source:

        1. ``reorder_point_column`` (and ``order_up_to_column``): targets in
           ``target_df``, typically from a forecast. They are dated: give
           ``forecast_origin``, the policy's date column, or both; the window of ``s``
           ends ``reorder_horizon`` periods after the origin and is checked
           at every decision.
        2. ``reorder_point`` (and ``order_up_to_level``): fixed planning values,
           one number for every SKU, a ``{sku: value}`` dict, or a Series
           indexed by SKU.
        3. ``target_provider``: your own ``ReorderPointTargetProvider``, called
           with ``target_df``.

        Sources 2 and 3 are planning levels: they need ``service_level=None``
        and claim no window. Their dates are optional: ``forecast_origin``
        needs the policy's ``freq``, and the run then checks it as for source
        1; ``freq`` alone records the period length without an origin.

        Args:
            target_df: One row per SKU (sources 1 and 3; optional for 2).
            reorder_point_column: Column holding ``s``.
            forecast_origin: Last observed demand date used to build ``s``.
                Optional when the policy's date column dates the rows: the
                last period of the window of ``s``, ``forecast_origin +
                reorder_horizon`` periods (source 1 only). The origin follows
                from it, and is checked against it when also given.
            reorder_horizon: Protection window of ``s``. Defaults to
                ``lead_time + review_period`` for a periodic schedule (and must
                equal it if given); other schedules require it and check it at
                each decision against ``(next opportunity - decision) + lead_time``.
            target_probability: Quantile mode only. Defaults to
                ``service_level``; if given, it must equal it. Must be omitted
                in planner mode.
            order_up_to_column: Column holding ``S`` for an ``(s,S)`` policy.
            reorder_point: Fixed ``s``: a number, a dict, or a Series by SKU.
            order_up_to_level: Fixed ``S`` for an ``(s,S)`` policy, in the same
                forms.
            target_provider: A ``ReorderPointTargetProvider``.

        Returns:
            The fitted policy (``self``).
        """
        sources = [
            name for name, value in (
                ("reorder_point_column", reorder_point_column),
                ("reorder_point", reorder_point),
                ("target_provider", target_provider),
            ) if value is not None
        ]
        if len(sources) != 1:
            raise ValueError(
                "choose exactly one target source: reorder_point_column, "
                "reorder_point, or target_provider"
            )
        self._uniform_levels = None
        sku_column, date_column = self.sku_column, self.date_column
        if sources[0] == "reorder_point_column":
            if order_up_to_level is not None:
                raise ValueError(
                    "order_up_to_level goes with reorder_point; with "
                    "reorder_point_column use order_up_to_column"
                )
            return self._fit_columns(
                target_df,
                reorder_point_column=reorder_point_column,
                forecast_origin=forecast_origin,
                date_column=date_column,
                reorder_horizon=reorder_horizon,
                target_probability=target_probability,
                order_up_to_column=order_up_to_column,
                sku_column=sku_column,
            )
        window_arguments = {
            "date_column": None if date_column == "date" else date_column,
            "reorder_horizon": reorder_horizon,
            "target_probability": target_probability,
            "order_up_to_column": order_up_to_column,
        }
        given = sorted(name for name, value in window_arguments.items() if value is not None)
        if given:
            verb = "applies" if len(given) == 1 else "apply"
            raise ValueError(
                f"{', '.join(given)} {verb} only to reorder_point_column targets"
            )
        if self.service_level is not None:
            raise ValueError(
                "fixed values and target providers are planning levels without a "
                "probability; create the policy with service_level=None"
            )
        dates = self._planning_dates(forecast_origin)
        if sources[0] == "reorder_point":
            return self._fit_fixed(
                target_df, reorder_point, order_up_to_level, sku_column, dates,
            )
        if order_up_to_level is not None:
            raise ValueError("order_up_to_level goes with reorder_point, not target_provider")
        return self._fit_provider(target_df, target_provider, sku_column, dates)

    def _planning_dates(self, forecast_origin):
        """Optional dates of planning levels: an origin needs ``freq``."""
        if forecast_origin is None:
            return None, self._freq_offset
        if self._freq_offset is None:
            raise ValueError(
                "freq is required: forecast_origin dates the levels in periods; "
                'create the policy with freq, for example ReorderPointPolicy(..., freq="D")'
            )
        return validate_forecast_origin(forecast_origin, self._freq_offset), self._freq_offset

    @staticmethod
    def _fixed_values(value, name: str):
        """A number, or ``{sku: number}`` from a dict or a Series."""
        if isinstance(value, pd.Series):
            if value.index.has_duplicates:
                raise ValueError(f"{name} has duplicate SKUs")
            value = value.to_dict()
        if isinstance(value, Mapping):
            if not value:
                raise ValueError(f"{name} must name at least one SKU")
            numbers = {}
            for sku, item in value.items():
                numbers[sku] = ReorderPointPolicy._fixed_number(item, name)
            return numbers
        return ReorderPointPolicy._fixed_number(value, name)

    @staticmethod
    def _fixed_number(value, name: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a finite number >= 0")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a finite number >= 0") from exc
        if not np.isfinite(number) or number < 0:
            raise ValueError(f"{name} must be a finite number >= 0")
        return number

    def _fit_fixed(self, target_df, reorder_point, order_up_to_level, sku_column, dates):
        """Mode 2: fixed planning values."""
        s_values = self._fixed_values(reorder_point, "reorder_point")
        if self.policy_type == "sS":
            if order_up_to_level is None:
                raise ValueError("order_up_to_level is required for an (s,S) policy")
            S_values = self._fixed_values(order_up_to_level, "order_up_to_level")
        elif order_up_to_level is not None:
            raise ValueError("order_up_to_level applies only to an (s,S) policy")
        else:
            S_values = None
        per_sku = [v for v in (s_values, S_values) if isinstance(v, dict)]
        skus = None
        if per_sku:
            skus = list(per_sku[0])
            for values in per_sku[1:]:
                if set(values) != set(skus):
                    raise ValueError(
                        "reorder_point and order_up_to_level must name the same SKUs"
                    )
        if target_df is not None:
            if not isinstance(target_df, pd.DataFrame) or target_df.empty:
                raise ValueError("target_df must be a non-empty pandas DataFrame")
            table_skus = _require_identifiers(target_df, sku_column, "target_df", unique=True)
            if skus is not None and set(skus) != table_skus:
                raise ValueError("the SKUs of the values must be exactly the SKUs of target_df")
            skus = target_df[sku_column].tolist()
        if skus is None:
            # One value for every SKU: applied to the SKUs of each decision.
            self._uniform_levels = (s_values, S_values)
            self.reorder_points_ = None
            self.order_quantities_ = None
            self.order_up_to_levels_ = None
            self.target_df_ = None
        else:
            def column(values):
                return [values[sku] if isinstance(values, dict) else values for sku in skus]
            frame = pd.DataFrame({sku_column: skus, "reorder_point": column(s_values)})
            if S_values is not None:
                frame["order_up_to_level"] = column(S_values)
            self._set_levels(frame, sku_column)
            self.target_df_ = None if target_df is None else target_df.copy()
        if S_values is not None:
            pairs = (
                [(s_values, S_values)] if skus is None
                else list(zip(frame["reorder_point"], frame["order_up_to_level"]))
            )
            if any(S < s for s, S in pairs):
                raise ValueError("order-up-to levels must be greater than or equal to reorder points")
        self.sku_column_ = sku_column
        self._set_planning_metadata("fixed_policy_levels", "external_direct", dates)
        self.fitted_ = True
        return self

    def _fit_provider(self, target_df, target_provider, sku_column, dates):
        """Mode 3: your own rule."""
        if not isinstance(target_provider, ReorderPointTargetProvider):
            raise TypeError("target_provider must be a ReorderPointTargetProvider")
        if not isinstance(target_df, pd.DataFrame) or target_df.empty:
            raise ValueError("target_df must be a non-empty pandas DataFrame")
        if target_provider.target_source not in {"external_direct", "custom_provider"}:
            raise ValueError(
                "target provider target_source must be 'external_direct' or "
                "'custom_provider'"
            )
        provider_result = target_provider.provide(target_df.copy(), sku_column=sku_column)
        levels = validate_reorder_point_targets(
            provider_result, sku_column=sku_column, order_up_to=self.policy_type == "sS",
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
        self._set_levels(levels, sku_column)
        self.target_df_ = target_df.copy()
        self.sku_column_ = sku_column
        self._set_planning_metadata(
            "provider_policy_levels", target_provider.target_source, dates,
            provider=provider_manifest,
            provider_metadata=provider_result.metadata.copy(),
        )
        self.fitted_ = True
        return self

    def _set_levels(self, frame: pd.DataFrame, sku_column: str) -> None:
        """Store per-SKU ``s`` and ``Q`` or ``S`` from a validated frame."""
        self.reorder_points_ = frame[[sku_column, "reorder_point"]].astype(
            {"reorder_point": float}
        ).reset_index(drop=True)
        if self.policy_type == "sQ":
            self.order_quantities_ = self.reorder_points_[[sku_column]].copy()
            self.order_quantities_["order_quantity"] = self.order_quantity
            self.order_up_to_levels_ = None
        else:
            self.order_up_to_levels_ = frame[[sku_column, "order_up_to_level"]].astype(
                {"order_up_to_level": float}
            ).reset_index(drop=True)
            self.order_quantities_ = None

    def _set_planning_metadata(self, representation, source, dates, **extra) -> None:
        origin, offset = dates
        self._forecast_origin_column = None
        self.target_metadata_ = {
            "representation": representation,
            "target_probability": None,
            "target_source": source,
            "forecast_origin": None if origin is None else origin.isoformat(),
            "forecast_frequency": None if offset is None else offset.freqstr,
            **extra,
        }
        if self.policy_type == "sS":
            self.target_metadata_["order_up_to_representation"] = "external_policy_level"

    def _fit_columns(
        self,
        target_df,
        *,
        reorder_point_column,
        forecast_origin,
        date_column,
        reorder_horizon,
        target_probability,
        order_up_to_column,
        sku_column,
    ) -> "ReorderPointPolicy":
        """Mode 1: dated, window-checked targets from table columns."""
        offset = self._freq_offset
        if offset is None:
            raise ValueError(
                "freq is required: reorder_point_column targets have one date per "
                "SKU, so the period length cannot be read from them; create the "
                'policy with freq, for example ReorderPointPolicy(..., freq="D")'
            )
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

        columns = [reorder_point_column]
        if self.policy_type == "sS":
            if order_up_to_column is None:
                raise ValueError("order_up_to_column is required for an (s,S) policy")
            columns.append(order_up_to_column)
        elif order_up_to_column is not None:
            raise ValueError("order_up_to_column applies only to an (s,S) policy")
        prepared = prepare_direct_targets(target_df, sku_column, columns)
        origin, end_date, origin_column = resolve_target_window(
            prepared,
            forecast_origin=forecast_origin,
            date_column=date_column,
            forecast_offset=offset,
            horizon=horizon,
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
        # Where the origin came from, for error messages only.
        self._forecast_origin_column = origin_column
        self.target_metadata_ = {
            "representation": (
                "direct_reorder_point_target" if probability is not None
                else "external_reorder_point"
            ),
            "target_probability": probability,
            "reorder_horizon": horizon,
            "target_source": "external_direct",
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
        if "reorder_horizon" not in self.target_metadata_:
            return  # fixed values and provider levels claim no window
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
    ) -> OrderDecision:
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

        reorder_points, order_quantities, order_up_to_levels = self._levels_for(
            inventory_df[sku_column], sku_column,
        )
        orders = inventory_df[[sku_column, "inventory_position"]].merge(
            reorder_points, on=sku_column, how="left",
        )
        missing = orders.loc[orders["reorder_point"].isna(), sku_column].tolist()
        if missing:
            raise ValueError(f"No reorder_point was fitted for SKUs: {missing[:5]}")
        should_order = orders["inventory_position"] <= orders["reorder_point"]
        if self.policy_type == "sQ":
            orders = orders.merge(order_quantities, on=sku_column, how="left")
            orders["order_quantity"] = np.where(should_order, orders["order_quantity"], 0.0)
            orders["target_level"] = np.nan
        else:
            orders = orders.merge(order_up_to_levels, on=sku_column, how="left")
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
        return OrderDecision(
            result, sku_column=sku_column, lead_time=self.lead_time,
        )

    def _levels_for(self, skus: pd.Series, sku_column: str):
        """Fitted level frames; one-value-for-every-SKU levels cover ``skus``."""
        if self._uniform_levels is None:
            return self.reorder_points_, self.order_quantities_, self.order_up_to_levels_
        s_value, S_value = self._uniform_levels
        reorder_points = pd.DataFrame({sku_column: skus.tolist(), "reorder_point": s_value})
        quantities = levels = None
        if self.policy_type == "sQ":
            quantities = reorder_points[[sku_column]].copy()
            quantities["order_quantity"] = self.order_quantity
        else:
            levels = pd.DataFrame({sku_column: skus.tolist(), "order_up_to_level": S_value})
        return reorder_points, quantities, levels

    def _require_per_sku_levels(self) -> None:
        if self._uniform_levels is not None:
            s_value, S_value = self._uniform_levels
            raise ValueError(
                f"the reorder point is one value for every SKU (s={s_value}"
                + ("" if S_value is None else f", S={S_value}")
                + "); pass target_df to fit for a per-SKU table"
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
        self._require_per_sku_levels()
        return self.reorder_points_.copy()

    def get_parameters(self) -> pd.DataFrame:
        """Return ``s`` with ``Q`` (for ``(s,Q)``) or ``S`` (for ``(s,S)``) per SKU."""
        if not self.fitted_:
            raise ValueError("Policy must be fitted first. Call fit() to set parameters.")
        self._require_per_sku_levels()
        other = self.order_quantities_ if self.policy_type == "sQ" else self.order_up_to_levels_
        return self.reorder_points_.merge(other, on=self.sku_column_, how="left")

    def _fitted_levels(self):
        """``s`` with ``Q`` or ``S`` for the run manifest: per SKU, or one value for all."""
        if self._uniform_levels is not None:
            s_value, S_value = self._uniform_levels
            levels = {"reorder_point": float(s_value)}
            if self.policy_type == "sQ":
                levels["order_quantity"] = self.order_quantity
            else:
                levels["order_up_to_level"] = float(S_value)
            return levels
        other = self.order_quantities_ if self.policy_type == "sQ" else self.order_up_to_levels_
        return self.reorder_points_.merge(other, on=self.sku_column_, how="left")

    def get_target_metadata(self) -> dict:
        """Return a copy of target, window, and quantity provenance."""
        if not self.fitted_:
            raise ValueError("Policy must be fitted first. Call fit() to set parameters.")
        return self.target_metadata_.copy()

    def __repr__(self) -> str:
        status = "fitted" if self.fitted_ else "not fitted"
        return (
            f"ReorderPointPolicy({self.policy_type}, lead_time={self.lead_time}, "
            f"schedule={self.schedule.to_manifest()}, freq={self.freq!r}, "
            f"service_level={self.service_level}, "
            f"allow_backorders={self.allow_backorders}, status={status})"
        )
