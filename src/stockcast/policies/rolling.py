"""Build a rolling policy schedule from one table of targets.

A rolling backtest refits the policy at every decision with the forecast made
at that decision's information date. ``rolling_schedule`` takes one long table
(one row per SKU and forecast origin) and returns the ``policy_schedule`` a
run needs, checking that every origin is exactly one decision of the policy's
schedule and that every inventory SKU has its target there.
"""

from __future__ import annotations

import copy
from typing import Dict

import numpy as np
import pandas as pd

from stockcast.core.base_policy import BasePolicy
from stockcast.core.data_structures import (
    InventoryStateDataFrame,
    _identifier_sample,
    _require_period_date,
)
from stockcast.policies.order_up_to import OrderUpToPolicy
from stockcast.policies.reorder_point import ReorderPointPolicy

MISSING_TARGET_MODES = ("raise", "no_order")

# fit arguments that name table columns holding the levels.
_FIT_COLUMN_ARGUMENTS = (
    "target_column", "mean_column", "std_column",
    "reorder_point_column", "order_up_to_column",
)
# Levels that are not read from table rows, so they have no origin per row.
_FIXED_LEVEL_ARGUMENTS = ("reorder_point", "order_up_to_level", "target_provider")


def _day(stamp: pd.Timestamp) -> str:
    """A date for messages: the day alone when there is no time of day."""
    return str(stamp.date()) if stamp == stamp.normalize() else str(stamp)


def _member_mask(values, members: set) -> np.ndarray:
    """Whether each value is in ``members`` (works for tuple SKU ids)."""
    return np.fromiter((value in members for value in values), dtype=bool, count=len(values))


def rolling_schedule(
    policy: BasePolicy,
    targets: pd.DataFrame,
    inventory: InventoryStateDataFrame,
    *,
    origin_column: str = "cutoff",
    missing_targets: str = "raise",
    **fit_kwargs,
) -> Dict[int, BasePolicy]:
    """Fit one policy per forecast origin of a table of targets.

    ``policy`` gives the configuration: a copy of it is fitted at every origin
    with that origin's rows and ``forecast_origin=origin``, so every check of
    ``fit`` applies to every snapshot. Origin ``o`` belongs to decision period
    ``t`` when ``o == opening date + t`` periods of the policy's ``freq``;
    ``t`` must be a decision period of the policy's schedule.

    Example:
        ```python
        schedule = rolling_schedule(
            OrderUpToPolicy(lead_time=2, review_period=1, freq="W-MON",
                            service_level=0.9, allow_backorders=False),
            targets,                  # unique_id, cutoff, date, q_90
            inventory,
            target_column="q_90",
        )
        first = min(schedule)
        result = SimulationEngine().run(
            policy=schedule[first], policy_schedule=schedule,
            demand_source=demand, inventory=inventory, allow_target_reuse=False,
        )
        ```

    Args:
        policy: An ``OrderUpToPolicy`` or a ``ReorderPointPolicy`` (fitted from
            columns) with ``freq``. It is not changed.
        targets: One row per SKU and origin (per SKU, origin and step ``fh``
            for mean/std forecasts), with the policy's SKU column, the origin
            column and the target columns. Rows of SKUs that are not in the
            inventory are ignored.
        inventory: The opening ``InventoryStateDataFrame`` of the run: it gives
            the opening date and the SKUs every origin must cover.
        origin_column: Column holding each row's forecast origin, the last
            date its forecast saw (default ``"cutoff"``).
        missing_targets: What a row whose target columns are NaN means: the
            forecast could not produce that target. ``"raise"`` (default)
            stops; ``"no_order"`` leaves the SKU out of that origin's policy,
            so a run with ``missing_targets="no_order"`` places no order for
            it there. A missing row always raises.
        **fit_kwargs: Arguments of the policy's ``fit``, such as
            ``target_column="q_90"``; not ``forecast_origin``, which comes from
            ``origin_column``.

    Returns:
        ``{decision_period: fitted_policy}``, sorted by period. Pass the first
        one as ``policy`` and the whole dict as ``policy_schedule``.

    Raises:
        ValueError: If an origin is off the calendar, before the opening date or
            not a decision period, a decision between the first and the last
            origin has no origin, an inventory SKU has no row at an origin, a
            target is missing under ``missing_targets="raise"``, or ``fit``
            rejects an origin's rows (the message names the origin).
        TypeError: If the policy or an input has the wrong type.
    """
    if type(policy) not in (OrderUpToPolicy, ReorderPointPolicy):
        raise TypeError(
            "rolling_schedule builds schedules of OrderUpToPolicy or ReorderPointPolicy; "
            f"got {type(policy).__name__}"
        )
    fixed = [name for name in _FIXED_LEVEL_ARGUMENTS if name in fit_kwargs]
    if fixed:
        raise ValueError(
            f"rolling_schedule reads the levels from table columns, one origin per row; "
            f"{', '.join(f'{name}=' for name in fixed)} gives levels without an origin "
            "per row: fit one policy with them instead"
        )
    if "forecast_origin" in fit_kwargs:
        raise ValueError(
            "rolling_schedule sets forecast_origin for every origin from the "
            f"'{origin_column}' column; remove forecast_origin"
        )
    if missing_targets not in MISSING_TARGET_MODES:
        raise ValueError(
            f"missing_targets must be 'raise' or 'no_order', got {missing_targets!r}"
        )
    if policy.freq is None:
        raise ValueError(
            "freq is required: rolling_schedule numbers each origin in periods of the "
            "policy's freq; create the policy with freq, for example "
            f'{type(policy).__name__}(..., freq="D")'
        )
    offset = policy._freq_offset
    if not isinstance(targets, pd.DataFrame):
        raise TypeError("targets must be a pandas DataFrame")
    if not isinstance(inventory, InventoryStateDataFrame):
        raise TypeError("inventory must be an InventoryStateDataFrame")

    sku_column = policy.sku_column
    fit_columns = list(dict.fromkeys(
        fit_kwargs[name] for name in _FIT_COLUMN_ARGUMENTS if fit_kwargs.get(name) is not None
    ))
    if not fit_columns:
        names = (
            "target_column=, or mean_column= and std_column="
            if isinstance(policy, OrderUpToPolicy) else "reorder_point_column="
        )
        raise ValueError(f"name the target columns to fit: pass {names}")
    for column, role in (
        [(origin_column, "origin column (origin_column)"), (sku_column, "SKU column (the policy's sku_column)")]
        + [(column, "target column") for column in fit_columns]
    ):
        if column not in targets.columns:
            raise ValueError(f"targets has no {role} '{column}'")

    opening_dates = pd.to_datetime(inventory.get_dataframe()["date"], errors="coerce")
    if opening_dates.isna().any() or opening_dates.nunique() != 1:
        raise ValueError("inventory must hold one opening date; initialize it first")
    opening = pd.Timestamp(opening_dates.iloc[0])
    _require_period_date(opening, offset, "the inventory's opening date")
    inventory_skus = inventory.get_dataframe()[inventory.sku_column].tolist()
    inventory_set = set(inventory_skus)

    # Only the columns fit reads: the snapshot stores its table and the run
    # fingerprints it, so other levels or labels in the table stay out.
    columns = [sku_column]
    if policy.date_column in targets.columns:
        columns.append(policy.date_column)
    if "fh" in targets.columns and fit_kwargs.get("mean_column") is not None:
        columns.append("fh")
    columns = list(dict.fromkeys(columns + fit_columns))

    # Rows of SKUs outside the inventory are ignored; the rest is cut into
    # one block per origin with one sort, not one scan per origin.
    keep = _member_mask(targets[sku_column].tolist(), inventory_set)
    if not keep.any():
        raise ValueError("targets has no rows for the inventory's SKUs")
    table = targets.loc[keep, columns].reset_index(drop=True)
    raw_origins = targets.loc[keep, origin_column].reset_index(drop=True)
    origins = pd.to_datetime(raw_origins, errors="coerce")
    if origins.isna().any():
        row = int(np.flatnonzero(origins.isna().to_numpy())[0])
        raise ValueError(
            f"targets.{origin_column} must hold valid dates; the row for SKU "
            f"{table[sku_column].iloc[row]!r} has {raw_origins.iloc[row]!r}"
        )
    codes, unique_origins = pd.factorize(origins, sort=True)
    order = np.argsort(codes, kind="stable")
    blocks = np.split(order, np.cumsum(np.bincount(codes, minlength=len(unique_origins)))[:-1])
    unique_origins = pd.DatetimeIndex(unique_origins)

    for origin in unique_origins:
        _require_period_date(origin, offset, f"the '{origin_column}' origin")
    if unique_origins[0] < opening:
        raise ValueError(
            f"origin {_day(unique_origins[0])} ('{origin_column}') is before the "
            f"inventory's opening date {_day(opening)}; the first decision (period 0) "
            f"has origin {_day(opening)}"
        )
    grid = pd.date_range(start=opening, end=unique_origins[-1], freq=offset)
    periods = grid.get_indexer(unique_origins)
    schedule_text = policy.schedule.to_manifest()
    for origin, period in zip(unique_origins, periods):
        if period < 0:
            # On the calendar, but not a whole number of periods after the
            # opening date (for example a different time of day).
            raise ValueError(
                f"origin {_day(origin)} ('{origin_column}') is not the opening date "
                f"{_day(opening)} plus a whole number of {offset.freqstr!r} periods"
            )
        if not policy.schedule.should_decide(int(period)):
            raise ValueError(
                f"origin {_day(origin)} ('{origin_column}') is decision period {period}, "
                f"but the policy's schedule {schedule_text} does not decide at period "
                f"{period}; every origin must be the information date of a decision"
            )
    covered = set(periods.tolist())
    for period in range(int(periods[0]), int(periods[-1]) + 1):
        if policy.schedule.should_decide(period) and period not in covered:
            raise ValueError(
                f"targets have no origin for decision period {period} (origin "
                f"{_day(opening + period * offset)}); every decision between the first "
                f"origin ({_day(unique_origins[0])}) and the last "
                f"({_day(unique_origins[-1])}) needs its rows"
            )

    schedule = {}
    for origin, period, rows in zip(unique_origins, periods, blocks):
        period = int(period)
        where = f"origin {_day(origin)} (decision period {period})"
        rows = table.iloc[np.sort(rows)]
        present = set(rows[sku_column].tolist())
        absent = [sku for sku in inventory_skus if sku not in present]
        if absent:
            raise ValueError(
                f"targets have no row for {len(absent)} inventory SKU(s) at {where}: "
                f"{_identifier_sample(absent)}; every inventory SKU needs its row at "
                "every origin. A target the forecast could not produce is a row with NaN "
                "target values"
            )
        gaps = rows[fit_columns].isna().any(axis=1).to_numpy()
        if gaps.any():
            gap_skus = set(rows.loc[gaps, sku_column].tolist())
            if missing_targets == "raise":
                raise ValueError(
                    f"targets are missing (NaN) for {len(gap_skus)} SKU(s) at {where}: "
                    f"{_identifier_sample(gap_skus)}; pass missing_targets=\"no_order\" to "
                    "place no order for them at that decision (and the same to the run)"
                )
            rows = rows[~_member_mask(rows[sku_column].tolist(), gap_skus)]
            if rows.empty:
                raise ValueError(
                    f"every SKU's target is missing at {where}, so that decision has "
                    "nothing to order with"
                )
        snapshot = copy.deepcopy(policy)
        try:
            snapshot.fit(rows.reset_index(drop=True), forecast_origin=origin, **fit_kwargs)
        except ValueError as exc:
            raise ValueError(f"{where}: {exc}") from exc
        schedule[period] = snapshot
    return dict(sorted(schedule.items()))
