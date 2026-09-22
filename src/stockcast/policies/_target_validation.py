"""Small validation helpers for policy target inputs."""

import math
import re
from typing import Iterable

import numpy as np
import pandas as pd

from stockcast.core.data_structures import (
    _require_forward_frequency,
    _require_identifiers,
)


_QUANTILE_COLUMN = re.compile(r"^(?:up|q|p)_?(\d+(?:\.\d+)?)$", re.IGNORECASE)
_INVALID_AGGREGATIONS = {
    "sum_marginal_quantiles",
    "sum_of_marginal_quantiles",
    "marginal_quantile_sum",
}


def validate_probability(value: float, name: str) -> float:
    """Return a finite probability strictly between zero and one."""
    try:
        probability = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number strictly between 0 and 1") from exc
    if not math.isfinite(probability) or not 0 < probability < 1:
        raise ValueError(f"{name} must be a finite number strictly between 0 and 1")
    return probability


def validate_target_probability(
    service_level: float,
    target_probability: float,
    target_column: str,
) -> float:
    """Validate explicit probability metadata and recognizable column labels."""
    probability = validate_probability(target_probability, "target_probability")
    if not math.isclose(probability, float(service_level), rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(
            f"target_probability {probability} does not match policy service_level "
            f"{service_level}"
        )

    match = _QUANTILE_COLUMN.match(str(target_column))
    if match:
        labelled_probability = float(match.group(1))
        if labelled_probability > 1:
            labelled_probability /= 100.0
        if not math.isclose(
            labelled_probability,
            probability,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                f"target column '{target_column}' denotes probability "
                f"{labelled_probability}, not {probability}"
            )
    return probability


def validate_aggregation_method(method: str, expected: str = None) -> str:
    """Require explicit provenance and reject marginal-quantile summation."""
    if not isinstance(method, str) or not method.strip():
        raise ValueError("aggregation_method must be a non-empty description")
    normalized = method.strip().lower()
    if normalized in _INVALID_AGGREGATIONS:
        raise ValueError(
            "marginal forecast quantiles cannot be summed into a protection-period "
            "quantile; supply a directly calculated target"
        )
    if expected is not None and normalized != expected:
        raise ValueError(f"aggregation_method must be '{expected}' for this input mode")
    return method.strip()


def validate_target_source(source: str, *, expected: str = "external_direct") -> str:
    """Validate where a supplied target came from, separately from calculation."""
    if not isinstance(source, str) or not source.strip():
        raise ValueError("target_source must be a non-empty string")
    normalized = source.strip().lower()
    if normalized != expected:
        raise ValueError(f"target_source must be '{expected}' for this input mode")
    return normalized


def validate_protection_horizon(value: int, expected: int, name: str = "protection_horizon") -> int:
    """Require the caller's horizon metadata to match the policy horizon."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be an integer >= 1")
    if value != expected:
        raise ValueError(f"{name} must equal the policy horizon {expected}, got {value}")
    return value


def validate_forecast_origin_and_frequency(forecast_origin, forecast_frequency: str):
    """Validate and normalize a forecast information origin and period frequency."""
    try:
        origin = pd.Timestamp(forecast_origin)
    except (TypeError, ValueError) as exc:
        raise ValueError("forecast_origin must be a valid timestamp") from exc
    if pd.isna(origin):
        raise ValueError("forecast_origin must be a valid timestamp")
    offset = _require_forward_frequency(
        forecast_frequency,
        "forecast_frequency",
    )
    return origin, offset


def prepare_inventory_positions(
    inventory_df: pd.DataFrame,
    sku_column: str,
    *,
    allow_components: bool,
) -> pd.DataFrame:
    """Return one finite inventory position per SKU without silent coercion."""
    if not isinstance(inventory_df, pd.DataFrame) or inventory_df.empty:
        raise ValueError("inventory_state_df must be a non-empty pandas DataFrame")
    _require_identifiers(
        inventory_df,
        sku_column,
        "inventory_state_df",
        unique=True,
    )
    prepared = inventory_df.copy()
    if "inventory_position" not in prepared.columns:
        components = ["on_hand", "on_order", "backorders"]
        if not allow_components or not all(column in prepared.columns for column in components):
            if allow_components:
                raise ValueError(
                    "inventory_state_df must have either 'inventory_position' column "
                    "or ['on_hand', 'on_order', 'backorders'] columns"
                )
            raise ValueError("inventory_state_df must contain inventory_position")
        for column in components:
            values = pd.to_numeric(prepared[column], errors="coerce")
            if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
                raise ValueError(f"inventory_state_df.{column} must contain finite numeric values")
            prepared[column] = values.astype(float)
        prepared["inventory_position"] = (
            prepared["on_hand"] + prepared["on_order"] - prepared["backorders"]
        )

    positions = pd.to_numeric(prepared["inventory_position"], errors="coerce")
    if positions.isna().any() or not np.isfinite(positions.to_numpy(dtype=float)).all():
        raise ValueError("inventory_state_df.inventory_position must contain finite numeric values")
    prepared["inventory_position"] = positions.astype(float)
    return prepared


def validate_target_end_dates(
    target_df: pd.DataFrame,
    target_end_date_column: str,
    forecast_origin: pd.Timestamp,
    forecast_offset,
    horizon: int,
) -> pd.Timestamp:
    """Require every direct target to identify the expected protection end date."""
    if not isinstance(target_end_date_column, str) or not target_end_date_column:
        raise ValueError("target_end_date_column is required for direct targets")
    if target_end_date_column not in target_df.columns:
        raise ValueError(
            f"target end-date column '{target_end_date_column}' not found in target_df"
        )
    dates = pd.to_datetime(target_df[target_end_date_column], errors="coerce")
    if dates.isna().any():
        raise ValueError(f"target_df.{target_end_date_column} must contain valid dates")
    expected = forecast_origin + horizon * forecast_offset
    if not (dates == expected).all():
        actual = sorted(str(value) for value in dates.unique())
        raise ValueError(
            f"target_df.{target_end_date_column} must equal {expected} for horizon "
            f"{horizon}; got {actual}"
        )
    return expected


def prepare_direct_targets(
    target_df: pd.DataFrame,
    sku_column: str,
    target_columns: Iterable[str],
) -> pd.DataFrame:
    """Validate one explicit policy-target row per SKU."""
    if not isinstance(target_df, pd.DataFrame) or target_df.empty:
        raise ValueError("target_df must be a non-empty pandas DataFrame")
    _require_identifiers(target_df, sku_column, 'target_df', unique=False)
    if target_df[sku_column].duplicated().any():
        raise ValueError("target_df must contain exactly one row per SKU")

    prepared = target_df.copy()
    for column in target_columns:
        if column not in prepared.columns:
            raise ValueError(
                f"target column '{column}' not found in target_df. "
                f"Available columns: {list(prepared.columns)}"
            )
        values = pd.to_numeric(prepared[column], errors="coerce")
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"target_df column '{column}' must contain finite values")
        if (values < 0).any():
            raise ValueError(f"target_df column '{column}' must be non-negative")
        prepared[column] = values.astype(float)
    return prepared


def prepare_independent_normal_forecasts(
    forecast_df: pd.DataFrame,
    sku_column: str,
    mean_column: str,
    std_column: str,
    horizon: int,
    forecast_date_column: str,
    forecast_origin: pd.Timestamp,
    forecast_offset,
) -> dict:
    """Validate consecutive marginal mean/std forecasts for an explicit model."""
    if not isinstance(forecast_df, pd.DataFrame) or forecast_df.empty:
        raise ValueError("forecast_df must be a non-empty pandas DataFrame")
    if not isinstance(forecast_date_column, str) or not forecast_date_column:
        raise ValueError("forecast_date_column is required for horizon forecasts")
    required = [sku_column, "fh", forecast_date_column, mean_column, std_column]
    missing = [column for column in required if column not in forecast_df.columns]
    if missing:
        raise ValueError(f"forecast_df is missing required columns: {missing}")
    _require_identifiers(forecast_df, sku_column, 'forecast_df', unique=False)

    prepared = forecast_df.copy()
    fh = pd.to_numeric(prepared["fh"], errors="coerce")
    if fh.isna().any() or not np.isfinite(fh.to_numpy(dtype=float)).all():
        raise ValueError("forecast_df.fh must contain finite integers")
    if (fh < 1).any() or not np.equal(fh, np.floor(fh)).all():
        raise ValueError("forecast_df.fh must contain positive consecutive integers")
    prepared["fh"] = fh.astype(int)
    if prepared.duplicated([sku_column, "fh"]).any():
        raise ValueError("forecast_df contains duplicate SKU-horizon rows")

    for column in (mean_column, std_column):
        values = pd.to_numeric(prepared[column], errors="coerce")
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"forecast_df column '{column}' must contain finite values")
        if (values < 0).any():
            raise ValueError(f"forecast_df column '{column}' must be non-negative")
        prepared[column] = values.astype(float)

    dates = pd.to_datetime(prepared[forecast_date_column], errors="coerce")
    if dates.isna().any():
        raise ValueError(f"forecast_df.{forecast_date_column} must contain valid dates")
    prepared[forecast_date_column] = dates
    expected_dates = prepared["fh"].map(
        lambda fh_value: forecast_origin + int(fh_value) * forecast_offset
    )
    if not (prepared[forecast_date_column] == expected_dates).all():
        bad = prepared.loc[
            prepared[forecast_date_column] != expected_dates,
            [sku_column, "fh", forecast_date_column],
        ].iloc[0]
        expected = forecast_origin + int(bad["fh"]) * forecast_offset
        raise ValueError(
            f"forecast date for SKU {bad[sku_column]} fh={int(bad['fh'])} must be "
            f"{expected}, got {bad[forecast_date_column]}"
        )

    expected_fh = list(range(1, horizon + 1))
    by_sku = {}
    for sku, rows in prepared.groupby(sku_column, sort=False):
        rows = rows.sort_values("fh")
        actual_fh = rows.loc[rows["fh"] <= horizon, "fh"].tolist()
        if actual_fh != expected_fh:
            raise ValueError(
                f"forecast_df for SKU {sku} must contain consecutive fh 1..{horizon}; "
                f"got {actual_fh}"
            )
        by_sku[sku] = rows[rows["fh"] <= horizon].copy()
    return by_sku
