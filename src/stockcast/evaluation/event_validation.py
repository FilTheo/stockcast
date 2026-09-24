"""Validation for the canonical inventory simulation event frame."""

from __future__ import annotations

import numpy as np
import pandas as pd

from stockcast.core.data_structures import _require_identifiers, _require_unique

CANONICAL_EVENT_COLUMNS = (
    "unique_id", "event_type", "demand_period", "period", "date", "policy",
    "allow_backorders", "is_review_period", "decision_flag",
    "starting_on_hand", "starting_backorders", "starting_on_order",
    "received_units", "demand", "fulfilled_units", "backorders_fulfilled",
    "shortage_units", "lost_sales_units", "backorder_increment",
    "ending_on_hand", "backorders_end", "on_order_end", "inventory_position_end",
    "order_quantity", "order_event_count", "sku_order_line_count",
    "order_line_quantity_squared_sum", "expired_units",
    "inventory_adjustment_units", "target_level", "safety_stock",
    "stockout_flag", "backorder_flag", "requested_order_quantity",
    "callback_adjustment_units", "callback_adjusted_order_quantity",
    "constrained_order_quantity", "constraint_adjustment_units",
    "constraint_binding_flag", "capacity_violation_flag", "binding_constraints",
    "run_window",
)

_NONNEGATIVE_FLOW_COLUMNS = (
    "starting_on_hand", "starting_backorders", "starting_on_order",
    "received_units", "demand", "fulfilled_units", "backorders_fulfilled",
    "shortage_units", "lost_sales_units", "backorder_increment",
    "ending_on_hand", "backorders_end", "on_order_end", "order_quantity",
    "order_event_count", "sku_order_line_count", "order_line_quantity_squared_sum",
    "expired_units", "requested_order_quantity", "constrained_order_quantity",
    "callback_adjusted_order_quantity",
)

_BOOLEAN_COLUMNS = (
    "allow_backorders", "is_review_period", "decision_flag", "stockout_flag",
    "backorder_flag", "constraint_binding_flag", "capacity_violation_flag",
)


def _require_close(frame: pd.DataFrame, expected, actual, name: str) -> None:
    # Absolute floor plus a rounding allowance scaled by the row's flows, so
    # valid ledgers in grams or millilitres (1e7+) are not rejected.
    magnitude = frame["_flow_magnitude"].to_numpy(dtype=float)
    difference = np.abs(np.asarray(expected, dtype=float) - np.asarray(actual, dtype=float))
    valid = difference <= 1e-9 + 1e-12 * magnitude
    if not valid.all():
        row = frame.loc[~valid].iloc[0]
        raise ValueError(
            f"event_frame violates {name} for SKU {row['unique_id']} "
            f"at period {row['period']}"
        )


def validate_event_frame(event_frame: pd.DataFrame) -> pd.DataFrame:
    """Return a defensive copy after strict structural and physical validation."""
    if not isinstance(event_frame, pd.DataFrame) or event_frame.empty:
        raise ValueError("event_frame must be a non-empty pandas DataFrame")
    missing = [column for column in CANONICAL_EVENT_COLUMNS if column not in event_frame]
    if missing:
        raise ValueError(f"event_frame is missing canonical columns: {missing}")

    frame = event_frame.copy()
    _require_identifiers(frame, "unique_id", "event_frame", unique=False)
    if frame["policy"].isna().any() or frame["policy"].astype(str).str.strip().eq("").any():
        raise ValueError("event_frame.policy must contain non-empty values")
    _require_unique(frame, ["unique_id", "event_type", "period", "policy"], "event_frame")

    event_types = set(frame["event_type"].dropna())
    unknown_types = sorted(event_types - {"period", "initial_decision"})
    if unknown_types or frame["event_type"].isna().any():
        raise ValueError(f"event_frame.event_type contains invalid values: {unknown_types}")
    unknown_windows = sorted(set(frame["run_window"].dropna()) - {"warmup", "scoring", "settlement"})
    if unknown_windows or frame["run_window"].isna().any():
        raise ValueError(f"event_frame.run_window contains invalid values: {unknown_windows}")
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any():
        raise ValueError("event_frame.date must contain valid timestamps")
    frame["date"] = dates

    periods = pd.to_numeric(frame["period"], errors="coerce")
    if periods.isna().any() or not np.isfinite(periods).all() or (periods < 0).any():
        raise ValueError("event_frame.period must contain finite integers >= 0")
    if not np.equal(periods, np.floor(periods)).all():
        raise ValueError("event_frame.period must contain finite integers >= 0")
    frame["period"] = periods.astype(int)

    period_rows = frame["event_type"].eq("period")
    demand_periods = pd.to_numeric(frame.loc[period_rows, "demand_period"], errors="coerce")
    if (
        demand_periods.isna().any()
        or not np.isfinite(demand_periods).all()
        or (demand_periods < 0).any()
        or not np.equal(demand_periods, np.floor(demand_periods)).all()
    ):
        raise ValueError("period events require finite integer demand_period values >= 0")
    if frame.loc[~period_rows, "demand_period"].notna().any():
        raise ValueError("initial_decision events must have a missing demand_period")

    for column in _NONNEGATIVE_FLOW_COLUMNS:
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f"event_frame.{column} must contain finite values >= 0")
        frame[column] = values.astype(float)
    for column in (
        "inventory_adjustment_units", "callback_adjustment_units",
        "constraint_adjustment_units", "inventory_position_end",
    ):
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.isna().any() or not np.isfinite(values).all():
            raise ValueError(f"event_frame.{column} must contain finite numeric values")
        frame[column] = values.astype(float)
    for column in ("target_level", "safety_stock"):
        values = pd.to_numeric(frame[column], errors="coerce")
        invalid = frame[column].notna() & (
            values.isna() | ~np.isfinite(values.to_numpy(dtype=float))
        )
        if invalid.any():
            raise ValueError(
                f"event_frame.{column} must contain finite numbers or explicit missing values"
            )
        if column == "safety_stock" and (values.dropna() < 0).any():
            raise ValueError("event_frame.safety_stock must be non-negative when supplied")
        frame[column] = values
    for column in ("order_event_count", "sku_order_line_count"):
        if not np.equal(frame[column], np.floor(frame[column])).all():
            raise ValueError(f"event_frame.{column} must contain integers")
    for column in _BOOLEAN_COLUMNS:
        if not frame[column].map(lambda value: isinstance(value, (bool, np.bool_))).all():
            raise ValueError(f"event_frame.{column} must contain boolean values")
    if not frame["binding_constraints"].map(lambda value: isinstance(value, str)).all():
        raise ValueError("event_frame.binding_constraints must contain strings")

    frame["_flow_magnitude"] = frame[
        list(_NONNEGATIVE_FLOW_COLUMNS) + ["inventory_adjustment_units", "callback_adjustment_units",
                                          "constraint_adjustment_units"]
    ].abs().sum(axis=1)
    _require_close(
        frame,
        frame["fulfilled_units"] + frame["shortage_units"],
        frame["demand"],
        "demand fulfillment balance",
    )
    _require_close(
        frame,
        frame["starting_on_hand"] + frame["received_units"]
        - frame["backorders_fulfilled"] - frame["fulfilled_units"]
        - frame["expired_units"] + frame["inventory_adjustment_units"],
        frame["ending_on_hand"],
        "physical inventory balance",
    )
    _require_close(
        frame,
        frame["starting_backorders"] + frame["backorder_increment"]
        - frame["backorders_fulfilled"],
        frame["backorders_end"],
        "backlog balance",
    )
    _require_close(
        frame,
        frame["starting_on_order"] - frame["received_units"] + frame["order_quantity"],
        frame["on_order_end"],
        "pipeline balance",
    )
    _require_close(
        frame,
        frame["ending_on_hand"] + frame["on_order_end"] - frame["backorders_end"],
        frame["inventory_position_end"],
        "inventory position identity",
    )
    _require_close(
        frame,
        frame["requested_order_quantity"] + frame["callback_adjustment_units"],
        frame["callback_adjusted_order_quantity"],
        "callback adjustment identity",
    )
    _require_close(
        frame,
        frame["callback_adjusted_order_quantity"] + frame["constraint_adjustment_units"],
        frame["constrained_order_quantity"],
        "constraint adjustment identity",
    )
    _require_close(
        frame,
        frame["constrained_order_quantity"],
        frame["order_quantity"],
        "constrained order identity",
    )
    adjusted = frame["constraint_adjustment_units"].abs() > 1e-9
    if (adjusted & ~frame["constraint_binding_flag"]).any():
        raise ValueError(
            "event_frame.constraint_binding_flag must identify every adjusted order"
        )
    names_present = frame["binding_constraints"].str.len() > 0
    if not names_present.eq(frame["constraint_binding_flag"]).all():
        raise ValueError(
            "event_frame.binding_constraints is inconsistent with constraint_binding_flag"
        )

    backorders = frame["allow_backorders"]
    if (frame.loc[backorders, "lost_sales_units"] > 1e-9).any():
        raise ValueError("backorder events cannot record lost_sales_units")
    _require_close(
        frame.loc[backorders],
        frame.loc[backorders, "shortage_units"],
        frame.loc[backorders, "backorder_increment"],
        "backorder shortage identity",
    )
    lost_sales = ~backorders
    if (frame.loc[lost_sales, ["backorder_increment", "backorders_end"]] > 1e-9).any().any():
        raise ValueError("lost-sales events cannot create or retain backorders")
    _require_close(
        frame.loc[lost_sales],
        frame.loc[lost_sales, "shortage_units"],
        frame.loc[lost_sales, "lost_sales_units"],
        "lost-sales shortage identity",
    )
    if not frame["stockout_flag"].eq(frame["shortage_units"] > 1e-9).all():
        raise ValueError("event_frame.stockout_flag is inconsistent with shortage_units")
    if not frame["backorder_flag"].eq(frame["backorders_end"] > 1e-9).all():
        raise ValueError("event_frame.backorder_flag is inconsistent with backorders_end")
    return frame.drop(columns="_flow_magnitude")
