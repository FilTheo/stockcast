"""Small validation helpers for policy target inputs."""

import math
import re
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from stockcast.core.data_structures import (
    _require_forward_frequency,
    _require_identifiers,
    _standard_frequency,
)


_QUANTILE_COLUMN = re.compile(r"^(?:up|q|p)_?(\d+(?:\.\d+)?)$", re.IGNORECASE)


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
    target_probability: Optional[float],
    target_column: str,
) -> float:
    """Validate probability metadata and recognizable column labels.

    ``target_probability=None`` means the policy's ``service_level``.
    """
    if target_probability is None:
        target_probability = service_level
    probability = validate_probability(target_probability, "target_probability")
    if not math.isclose(probability, float(service_level), rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(
            f"target_probability {probability} does not match policy service_level "
            f"{service_level}"
        )

    match = _QUANTILE_COLUMN.match(str(target_column))
    if match:
        digits = match.group(1)
        percent = float(digits)
        if percent > 1:
            percent /= 100.0
        readings = [percent]
        if "." not in digits and len(digits) >= 3:
            # "q975", "q025" and "q995" name the 97.5%, 2.5% and 99.5% quantiles.
            readings.append(float(digits) / 10 ** len(digits))
        # Only readings that are probabilities can name the column.
        readings = [reading for reading in readings if 0 < reading < 1] or readings
        if not any(
            math.isclose(reading, probability, rel_tol=0.0, abs_tol=1e-12)
            for reading in readings
        ):
            denoted = " or ".join(str(reading) for reading in readings)
            raise ValueError(
                f"target column '{target_column}' denotes probability "
                f"{denoted}, not {probability}"
            )
    return probability


def validate_protection_horizon(value: int, expected: int, name: str = "protection_horizon") -> int:
    """Require the caller's horizon metadata to match the policy horizon."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be an integer >= 1")
    if value != expected:
        raise ValueError(f"{name} must equal the policy horizon {expected}, got {value}")
    return value


def validate_forecast_origin(forecast_origin) -> pd.Timestamp:
    """Return a forecast information origin as a valid timestamp."""
    try:
        origin = pd.Timestamp(forecast_origin)
    except (TypeError, ValueError) as exc:
        raise ValueError("forecast_origin must be a valid timestamp") from exc
    if pd.isna(origin):
        raise ValueError("forecast_origin must be a valid timestamp")
    return origin


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


DEFAULT_DATE_COLUMN = "date"


def read_date_column(frame: pd.DataFrame, date_column: str, frame_name: str) -> Optional[pd.Series]:
    """Return ``frame[date_column]`` as valid timestamps, or None when absent.

    ``date_column`` is the date of the last demand period each row covers. The
    default column is read only if it exists; a column named explicitly must
    exist.
    """
    if not isinstance(date_column, str) or not date_column:
        raise ValueError("date_column must be a non-empty column name")
    if date_column not in frame.columns:
        if date_column == DEFAULT_DATE_COLUMN:
            return None
        raise ValueError(f"date column '{date_column}' not found in {frame_name}")
    dates = pd.to_datetime(frame[date_column], errors="coerce")
    if dates.isna().any():
        raise ValueError(f"{frame_name}.{date_column} must contain valid dates")
    return dates


def _missing_origin(horizon: int) -> ValueError:
    return ValueError(
        "give forecast_origin, or a date column (date_column=...): the target "
        f"window ends {horizon} periods after the forecast origin, on the date of "
        "its last period"
    )


def resolve_target_window(
    target_df: pd.DataFrame,
    *,
    forecast_origin,
    date_column: str,
    forecast_offset,
    horizon: int,
) -> tuple:
    """Return ``(origin, end_date, origin_column)`` of a one-row target's window.

    The date column holds the window's last period, so the two dates are tied
    by ``end = origin + horizon`` periods and either one is enough. When both
    are given they must agree. ``origin_column`` names the column the origin
    was derived from, or is None when it was given.
    """
    dates = read_date_column(target_df, date_column, "target_df")
    if forecast_origin is not None:
        origin = validate_forecast_origin(forecast_origin)
        expected = origin + horizon * forecast_offset
        if dates is not None and not (dates == expected).all():
            actual = sorted(str(value) for value in dates.unique())
            raise ValueError(
                f"target_df.{date_column} must equal {expected} for horizon "
                f"{horizon}; got {actual}"
            )
        return origin, expected, None
    if dates is None:
        raise _missing_origin(horizon)
    if dates.nunique() != 1:
        actual = sorted(str(value) for value in dates.unique())
        raise ValueError(
            f"target_df.{date_column} must hold one end date for every SKU; "
            f"got {actual}"
        )
    end_date = pd.Timestamp(dates.iloc[0])
    origin = end_date - horizon * forecast_offset
    if origin + horizon * forecast_offset != end_date:
        raise ValueError(
            f"target_df.{date_column} date {end_date} is not on the "
            f"{forecast_offset.freqstr} period grid"
        )
    return origin, end_date, date_column


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
    date_column: str,
    forecast_origin: Optional[pd.Timestamp],
    forecast_offset,
) -> tuple:
    """Validate consecutive marginal mean/std forecasts for an explicit model.

    Returns ``(forecasts_by_sku, origin, offset, origin_column)``. Each row's
    date is its own period, ``origin + fh`` periods, so:

    - with ``forecast_origin=None`` the origin is read from the dates
      (``date - fh`` periods) and ``origin_column`` names the date column;
    - without an ``fh`` column, ``fh`` is read from the dates and a given
      origin (dates alone cannot tell which step comes first);
    - with ``forecast_offset=None`` the period length is read from the dates
      (``infer_step_frequency``).

    Every date is checked against ``origin + fh`` periods.
    """
    if not isinstance(forecast_df, pd.DataFrame) or forecast_df.empty:
        raise ValueError("forecast_df must be a non-empty pandas DataFrame")
    required = [sku_column, mean_column, std_column]
    missing = [column for column in required if column not in forecast_df.columns]
    if missing:
        raise ValueError(f"forecast_df is missing required columns: {missing}")
    _require_identifiers(forecast_df, sku_column, 'forecast_df', unique=False)
    dates = read_date_column(forecast_df, date_column, "forecast_df")
    has_fh = "fh" in forecast_df.columns
    if forecast_origin is None and dates is None:
        raise _missing_origin(horizon)
    if not has_fh and dates is None:
        raise ValueError(
            "forecast_df needs an fh column, or a date column (date_column=...), "
            "to place each step"
        )
    if not has_fh and forecast_origin is None:
        raise ValueError(
            "forecast_df needs an fh column, or give forecast_origin: dates alone "
            "cannot tell which step comes first"
        )

    prepared = forecast_df.copy()
    for column in (mean_column, std_column):
        values = pd.to_numeric(prepared[column], errors="coerce")
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"forecast_df column '{column}' must contain finite values")
        if (values < 0).any():
            raise ValueError(f"forecast_df column '{column}' must be non-negative")
        prepared[column] = values.astype(float)
    if dates is not None:
        prepared[date_column] = dates
    if forecast_offset is None:
        if dates is None:
            raise ValueError(
                "freq is required: forecast_df has no date column to read the period "
                'length from; create the policy with freq, for example '
                'OrderUpToPolicy(..., freq="D")'
            )
        forecast_offset = infer_step_frequency(prepared, sku_column, date_column)

    if has_fh:
        fh = pd.to_numeric(prepared["fh"], errors="coerce")
        if fh.isna().any() or not np.isfinite(fh.to_numpy(dtype=float)).all():
            raise ValueError("forecast_df.fh must contain finite integers")
        if (fh < 1).any() or not np.equal(fh, np.floor(fh)).all():
            raise ValueError("forecast_df.fh must contain positive consecutive integers")
        prepared["fh"] = fh.astype(int)
    else:
        prepared["fh"] = _steps_from_origin(dates, forecast_origin, forecast_offset)
    if prepared.duplicated([sku_column, "fh"]).any():
        raise ValueError("forecast_df contains duplicate SKU-horizon rows")

    origin_column = None
    if forecast_origin is None:
        first = prepared.iloc[0]
        forecast_origin = first[date_column] - int(first["fh"]) * forecast_offset
        if forecast_origin + int(first["fh"]) * forecast_offset != first[date_column]:
            raise ValueError(
                f"forecast date {first[date_column]} is not on the "
                f"{forecast_offset.freqstr} period grid"
            )
        origin_column = date_column
    if dates is not None:
        expected_dates = prepared["fh"].map(
            lambda fh_value: forecast_origin + int(fh_value) * forecast_offset
        )
        if not (prepared[date_column] == expected_dates).all():
            bad = prepared.loc[
                prepared[date_column] != expected_dates,
                [sku_column, "fh", date_column],
            ].iloc[0]
            expected = forecast_origin + int(bad["fh"]) * forecast_offset
            raise ValueError(
                f"forecast date for SKU {bad[sku_column]} fh={int(bad['fh'])} must be "
                f"{expected}, got {bad[date_column]}"
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
    return by_sku, forecast_origin, forecast_offset, origin_column


def _steps_from_origin(dates: pd.Series, origin: pd.Timestamp, offset) -> pd.Series:
    """Number each date's period after ``origin`` exactly (``date = origin + fh`` periods)."""
    last = dates.max()
    grid, step, stamp = {}, 0, origin
    while stamp < last:
        step += 1
        stamp = origin + step * offset
        grid[stamp] = step
    steps = dates.map(grid)
    if steps.isna().any():
        bad = dates[steps.isna()].iloc[0]
        raise ValueError(
            f"forecast date {bad} is not a {offset.freqstr} period after the forecast "
            f"origin {origin}"
        )
    return steps.astype(int)


def infer_step_frequency(forecast_df: pd.DataFrame, sku_column: str, date_column: str):
    """Read the period length from per-step forecast dates, or raise.

    Every SKU needs at least three evenly spaced dates on one standard
    calendar (``_standard_frequency``), the same for all SKUs. Daily periods
    also need a Saturday or Sunday among the dates: consecutive weekdays fit
    business days as well. Anything else, such as business days (dates that
    skip a weekend), is refused rather than guessed.
    """
    ask = 'create the policy with freq, for example OrderUpToPolicy(..., freq="D")'
    found = set()
    for sku, rows in forecast_df.groupby(sku_column, sort=False):
        offset, problem = _standard_frequency(rows[date_column])
        if problem == "few":
            reason = f"SKU {sku!r} has fewer than three forecast dates"
        elif problem == "irregular":
            reason = f"the forecast dates of SKU {sku!r} are not evenly spaced"
        elif problem is not None:
            reason = (
                f"the forecast dates of SKU {sku!r} follow {problem!r}, which is not "
                "a calendar Stockcast reads from dates (daily, weekly, monthly, "
                "quarterly or yearly)"
            )
        elif (
            type(offset) is pd.offsets.Day
            and offset.n == 1
            and not (pd.to_datetime(rows[date_column]).dt.dayofweek >= 5).any()
        ):
            # Consecutive weekdays fit business days too; a weekend date (any
            # seven consecutive days include one) shows the periods are days.
            reason = (
                f"the forecast dates of SKU {sku!r} fit both daily and business-day "
                "periods (none falls on a Saturday or Sunday)"
            )
        else:
            found.add(offset.freqstr)
            continue
        raise ValueError(f"freq is required: {reason}, so the period length cannot be read; {ask}")
    if len(found) != 1:
        raise ValueError(
            "freq is required: the SKUs' forecast dates follow different frequencies "
            f"{sorted(found)}; {ask}"
        )
    return _require_forward_frequency(found.pop(), "freq")


def schedule_protection_horizon(
    schedule, lead_time: int, declared: Optional[int], name: str,
) -> int:
    """Return the fit-time protection horizon implied by a decision schedule.

    A periodic schedule with interval ``R`` fixes ``H = L + R``; ``declared``
    may be ``None`` and is otherwise checked against it. Other schedules
    require an explicit horizon, which is checked against the next
    opportunity at every decision before the run starts.
    """
    from stockcast.core.decision_schedule import PeriodicSchedule

    if isinstance(schedule, PeriodicSchedule):
        expected = lead_time + schedule.every
        if declared is None:
            return expected
    else:
        if declared is None:
            raise ValueError(
                f"{name} is required for a nonperiodic schedule; it is checked "
                "against each decision's window before the run"
            )
        expected = declared
    return validate_protection_horizon(declared, expected, name)


def validate_schedule_coverage(
    schedule,
    *,
    lead_time: int,
    period: int,
    horizon: int,
    forecast_origin,
    target_end_date,
    information_date,
    offset,
    label: str,
) -> None:
    """Check an irregular decision's target window: ``H = (u - t) + L``.

    If the policy does not order at ``t``, the next order is placed at the next
    opportunity ``u`` and becomes usable before demand ``u + L``. The position
    at ``t`` is therefore exposed to demand ``t .. u + L - 1``. A terminal
    decision (no next opportunity) uses its explicitly declared horizon.
    Periodic schedules are skipped: their fixed targets may be reused.
    """
    from stockcast.core.decision_schedule import PeriodicSchedule

    if isinstance(schedule, PeriodicSchedule):
        return
    next_period = schedule.next_decision_period(period)
    if next_period is not None:
        if not isinstance(next_period, int) or isinstance(next_period, bool) or next_period <= period:
            raise ValueError("next decision must be an integer strictly after this period")
        if not schedule.should_decide(next_period):
            raise ValueError("next decision must be an eligible opportunity")
        validate_protection_horizon(horizon, next_period - period + lead_time, label)
    if pd.Timestamp(forecast_origin) != information_date:
        raise ValueError("nonperiodic targets require the exact decision information origin")
    if pd.Timestamp(target_end_date) != information_date + horizon * offset:
        raise ValueError("target end date does not match decision coverage")
