"""Inventory metrics with explicit aggregation and cost contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import numpy as np
import pandas as pd


class BaseInventoryMetric(ABC):
    """Base class for inventory metrics."""

    name: str

    @abstractmethod
    def compute(self, event_frame: pd.DataFrame, context: dict) -> float:
        """Compute the metric from an event-frame slice."""


def _require_columns(event_frame: pd.DataFrame, columns) -> None:
    missing = [column for column in columns if column not in event_frame.columns]
    if missing:
        raise ValueError(f"event_frame is missing required columns: {missing}")


def _numeric_series(event_frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a complete finite numeric event column."""
    _require_columns(event_frame, [column])
    values = pd.to_numeric(event_frame[column], errors="coerce")
    if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError(f"event_frame.{column} must be complete and finite")
    return values.astype(float)


def _boolean_series(event_frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a complete boolean event column without truthy coercion."""
    _require_columns(event_frame, [column])
    values = event_frame[column]
    if values.isna().any() or not values.map(
        lambda value: isinstance(value, (bool, np.bool_))
    ).all():
        raise ValueError(f"event_frame.{column} must be complete and boolean")
    return values.astype(bool)


def _period_events(event_frame: pd.DataFrame) -> pd.DataFrame:
    """Exclude time-zero decision events from period-state metrics."""
    if "event_type" not in event_frame.columns:
        return event_frame
    return event_frame[event_frame["event_type"] == "period"]


def _terminal_rows(event_frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(event_frame, ["unique_id", "period"])
    period_events = _period_events(event_frame)
    if period_events.empty:
        return period_events
    return (
        period_events.sort_values(["unique_id", "period"])
        .groupby("unique_id", sort=False, as_index=False)
        .tail(1)
    )


def _sum(event_frame: pd.DataFrame, column: str) -> float:
    return float(_numeric_series(event_frame, column).sum())


def _mean(event_frame: pd.DataFrame, column: str) -> float:
    if event_frame.empty:
        return np.nan
    return float(_numeric_series(event_frame, column).mean())


def _rate_series(
    event_frame: pd.DataFrame,
    context: dict,
    key: str,
) -> pd.Series:
    """Resolve a complete, finite, non-negative row or scalar cost rate."""
    if key in event_frame.columns:
        values = pd.to_numeric(event_frame[key], errors="coerce")
        if values.isna().any():
            raise ValueError(f"event_frame.{key} must be complete and numeric")
    elif key in context:
        try:
            value = float(context[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"context['{key}'] must be a finite non-negative number") from exc
        values = pd.Series(value, index=event_frame.index, dtype=float)
    else:
        raise ValueError(
            f"cost rate '{key}' is required as an event column or evaluator context value"
        )
    array = values.to_numpy(dtype=float)
    if not np.isfinite(array).all() or (array < 0).any():
        raise ValueError(f"cost rate '{key}' must be finite and non-negative")
    return values.astype(float)


def _context_scalar(context: dict, key: str) -> float:
    if key not in context:
        raise ValueError(f"evaluator context must explicitly provide '{key}'")
    try:
        value = float(context[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"context['{key}'] must be a finite non-negative number") from exc
    if not np.isfinite(value) or value < 0:
        raise ValueError(f"context['{key}'] must be a finite non-negative number")
    return value


def demand_units(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    return _sum(_period_events(event_frame), "demand")


def fulfilled_units(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    return _sum(_period_events(event_frame), "fulfilled_units")


def shortage_units(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    return _sum(_period_events(event_frame), "shortage_units")


def lost_sales_units(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    return _sum(_period_events(event_frame), "lost_sales_units")


def backlog_unit_periods(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Sum ending backlog over SKU-period rows (backlog exposure)."""
    return _sum(_period_events(event_frame), "backorders_end")


def terminal_backlog_units(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Sum backlog at the final scored period for each SKU."""
    return _sum(_terminal_rows(event_frame), "backorders_end")


def terminal_pipeline_units(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Sum outstanding pipeline at the final scored period for each SKU."""
    return _sum(_terminal_rows(event_frame), "on_order_end")


def order_units(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    return _sum(event_frame, "order_quantity")


def sku_order_line_count(event_frame: pd.DataFrame, context: Optional[dict] = None) -> int:
    """Count directly recorded positive SKU order lines."""
    return int(_sum(event_frame, "sku_order_line_count"))


def order_event_count(event_frame: pd.DataFrame, context: Optional[dict] = None) -> int:
    """Count directly recorded order decisions with positive quantities."""
    return int(_sum(event_frame, "order_event_count"))


def sku_order_quantity_variance(
    event_frame: pd.DataFrame,
    context: Optional[dict] = None,
) -> float:
    """Population variance of directly observed positive SKU order-line sizes."""
    count = _sum(event_frame, "sku_order_line_count")
    if count <= 0:
        return np.nan
    quantity_sum = _sum(event_frame, "order_quantity")
    squared_sum = _sum(event_frame, "order_line_quantity_squared_sum")
    variance = squared_sum / count - (quantity_sum / count) ** 2
    return float(max(0.0, variance))


def capacity_violation_count(event_frame: pd.DataFrame, context: Optional[dict] = None) -> int:
    """Count requested SKU order lines that exceeded a configured capacity."""
    return int(_boolean_series(event_frame, "capacity_violation_flag").sum())


def capacity_violation_rate(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Share of positive requested SKU order lines exceeding capacity."""
    flags = _boolean_series(event_frame, "capacity_violation_flag")
    requested = _numeric_series(event_frame, "requested_order_quantity") > 0
    if not requested.any():
        return 0.0
    return float(flags[requested].mean())


def fill_rate(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    total_demand = demand_units(event_frame, context)
    if total_demand <= 0:
        return 1.0
    return fulfilled_units(event_frame, context) / total_demand


def demand_period_service_level(
    event_frame: pd.DataFrame,
    context: Optional[dict] = None,
) -> float:
    """Share of positive-demand SKU-period rows without shortage."""
    period_events = _period_events(event_frame)
    demand = _numeric_series(period_events, "demand")
    shortage = _numeric_series(period_events, "shortage_units")
    eligible = demand > 0
    if not eligible.any():
        return 1.0
    return float((shortage[eligible] <= 0).mean())


def cycle_service_level(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Fraction of receipt-to-receipt SKU cycles with no shortage.

    A receipt starts a new cycle. The opening-stock cycle and terminal partial
    cycle are included only when ``context['include_partial_cycles']`` is true.
    Requiring that choice prevents an implicit partial-cycle convention.
    """
    context = context or {}
    if "include_partial_cycles" not in context or not isinstance(
        context["include_partial_cycles"], bool
    ):
        raise ValueError("cycle_service_level requires boolean include_partial_cycles")
    include_partial = context["include_partial_cycles"]
    period_events = _period_events(event_frame)
    _require_columns(
        period_events,
        ["unique_id", "period", "received_units", "shortage_units"],
    )
    cycle_outcomes = []
    for _, rows in period_events.groupby("unique_id", sort=False):
        rows = rows.sort_values("period").copy()
        receipts = _numeric_series(rows, "received_units")
        shortages = _numeric_series(rows, "shortage_units")
        rows["_cycle"] = (receipts > 0).cumsum()
        rows["_shortage"] = shortages
        last_cycle = rows["_cycle"].max()
        for cycle_id, cycle_rows in rows.groupby("_cycle", sort=False):
            if not include_partial and cycle_id in {0, last_cycle}:
                continue
            cycle_outcomes.append(bool((cycle_rows["_shortage"] <= 0).all()))
    if not cycle_outcomes:
        return np.nan
    return float(np.mean(cycle_outcomes))


def sku_period_stockout_rate(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    period_events = _period_events(event_frame)
    if period_events.empty:
        return 0.0
    return float(_boolean_series(period_events, "stockout_flag").mean())


def stockout_period_rate(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Fraction of calendar periods with a stockout in any SKU in the slice."""
    period_events = _period_events(event_frame)
    _require_columns(period_events, ["period"])
    if period_events.empty:
        return 0.0
    period_events = period_events.copy()
    period_events["_stockout"] = _boolean_series(period_events, "stockout_flag")
    period_column = "demand_period" if "demand_period" in period_events.columns else "period"
    return float(period_events.groupby(period_column)["_stockout"].any().mean())


def backorder_period_rate(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Fraction of calendar periods ending with backlog in any SKU in the slice."""
    period_events = _period_events(event_frame)
    _require_columns(period_events, ["period"])
    if period_events.empty:
        return 0.0
    period_events = period_events.copy()
    period_events["_backorder"] = _boolean_series(period_events, "backorder_flag")
    period_column = "demand_period" if "demand_period" in period_events.columns else "period"
    return float(period_events.groupby(period_column)["_backorder"].any().mean())


def avg_on_hand(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    return _mean(_period_events(event_frame), "ending_on_hand")


def avg_inventory_position(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    return _mean(_period_events(event_frame), "inventory_position_end")


def avg_on_order(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    return _mean(_period_events(event_frame), "on_order_end")


def ending_on_hand_variance(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Population variance of total ending stock across calendar periods."""
    period_events = _period_events(event_frame)
    _require_columns(period_events, ["period"])
    if period_events.empty:
        return np.nan
    period_events = period_events.copy()
    period_events["_ending_on_hand"] = _numeric_series(period_events, "ending_on_hand")
    period_column = "demand_period" if "demand_period" in period_events.columns else "period"
    totals = period_events.groupby(period_column)["_ending_on_hand"].sum()
    return float(totals.var(ddof=0))


def peak_ending_on_hand(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Peak total ending stock across calendar periods."""
    period_events = _period_events(event_frame)
    _require_columns(period_events, ["period"])
    if period_events.empty:
        return np.nan
    period_events = period_events.copy()
    period_events["_ending_on_hand"] = _numeric_series(period_events, "ending_on_hand")
    period_column = "demand_period" if "demand_period" in period_events.columns else "period"
    totals = period_events.groupby(period_column)["_ending_on_hand"].sum()
    return float(totals.max())


def inventory_turns(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Annualized fulfilled-unit throughput divided by average ending stock."""
    context = context or {}
    periods_per_year = _context_scalar(context, "periods_per_year")
    if periods_per_year <= 0:
        raise ValueError("context['periods_per_year'] must be > 0")
    period_events = _period_events(event_frame)
    _require_columns(period_events, ["period"])
    n_periods = period_events["period"].nunique()
    period_column = "demand_period" if "demand_period" in period_events.columns else "period"
    period_events = period_events.copy()
    period_events["_ending_on_hand"] = _numeric_series(period_events, "ending_on_hand")
    average_inventory = float(
        period_events.groupby(period_column)["_ending_on_hand"].sum().mean()
    )
    if n_periods == 0 or np.isnan(average_inventory) or average_inventory <= 0:
        return np.nan
    annual_throughput = fulfilled_units(period_events, context) / n_periods * periods_per_year
    return annual_throughput / average_inventory


def holding_cost(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    context = context or {}
    period_events = _period_events(event_frame)
    rates = _rate_series(period_events, context, "holding_cost_per_unit_period")
    return float((_numeric_series(period_events, "ending_on_hand") * rates).sum())


def shortage_cost(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    context = context or {}
    period_events = _period_events(event_frame)
    rates = _rate_series(period_events, context, "shortage_cost_per_unit")
    return float((_numeric_series(period_events, "shortage_units") * rates).sum())


def backlog_cost(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    context = context or {}
    period_events = _period_events(event_frame)
    rates = _rate_series(period_events, context, "backlog_cost_per_unit_period")
    return float((_numeric_series(period_events, "backorders_end") * rates).sum())


def ordering_cost(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Return SKU-level fixed line cost plus variable unit cost.

    Stockcast does not model a shared/global order-event fixed cost. Each positive
    SKU order line incurs its own explicit ``order_cost_per_sku_line`` rate, so
    pooled cost is the sum of SKU-level costs and is independent of row order.
    """
    context = context or {}
    quantity = _numeric_series(event_frame, "order_quantity")
    line_rates = _rate_series(event_frame, context, "order_cost_per_sku_line")
    unit_rates = _rate_series(event_frame, context, "order_cost_per_unit")
    line_counts = _numeric_series(event_frame, "sku_order_line_count")
    line_cost = (line_counts * line_rates).sum()
    variable_cost = (quantity * unit_rates).sum()
    return float(line_cost + variable_cost)


def purchase_cost(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    context = context or {}
    rates = _rate_series(event_frame, context, "purchase_cost_per_unit")
    return float((_numeric_series(event_frame, "order_quantity") * rates).sum())


def waste_cost(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    context = context or {}
    period_events = _period_events(event_frame)
    rates = _rate_series(period_events, context, "waste_cost_per_unit")
    return float((_numeric_series(period_events, "expired_units") * rates).sum())


def terminal_backlog_cost(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    context = context or {}
    terminal = _terminal_rows(event_frame)
    rates = _rate_series(terminal, context, "terminal_backlog_cost_per_unit")
    return float((_numeric_series(terminal, "backorders_end") * rates).sum())


def terminal_pipeline_cost(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    context = context or {}
    terminal = _terminal_rows(event_frame)
    rates = _rate_series(terminal, context, "terminal_pipeline_cost_per_unit")
    return float((_numeric_series(terminal, "on_order_end") * rates).sum())


def salvage_credit(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    context = context or {}
    terminal = _terminal_rows(event_frame)
    on_hand_rates = _rate_series(terminal, context, "on_hand_salvage_per_unit")
    pipeline_rates = _rate_series(terminal, context, "pipeline_salvage_per_unit")
    return float(
        (_numeric_series(terminal, "ending_on_hand") * on_hand_rates).sum()
        + (_numeric_series(terminal, "on_order_end") * pipeline_rates).sum()
    )


_COST_COMPONENTS = {
    "holding": holding_cost,
    "shortage": shortage_cost,
    "backlog": backlog_cost,
    "ordering": ordering_cost,
    "purchase": purchase_cost,
    "waste": waste_cost,
    "terminal_backlog": terminal_backlog_cost,
    "terminal_pipeline": terminal_pipeline_cost,
    "salvage": salvage_credit,
}


def total_cost(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    """Sum only explicitly activated cost components.

    ``context['cost_components']`` is mandatory. Each component then requires
    every rate in its own contract, including explicit zeros. Salvage is a
    credit and is subtracted; all other components are added.
    """
    context = context or {}
    components = context.get("cost_components")
    if not isinstance(components, (list, tuple)) or not components:
        raise ValueError("total_cost requires a non-empty cost_components list")
    if len(set(components)) != len(components):
        raise ValueError("cost_components must not contain duplicates")
    unknown = sorted(set(components) - set(_COST_COMPONENTS))
    if unknown:
        raise ValueError(f"unknown cost_components: {unknown}")
    total = 0.0
    for component in components:
        value = _COST_COMPONENTS[component](event_frame, context)
        total = total - value if component == "salvage" else total + value
    return float(total)


def cost_per_demand_unit(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    total_demand = demand_units(event_frame, context)
    if total_demand <= 0:
        return np.nan
    return total_cost(event_frame, context) / total_demand


def cost_per_fulfilled_unit(event_frame: pd.DataFrame, context: Optional[dict] = None) -> float:
    fulfilled = fulfilled_units(event_frame, context)
    if fulfilled <= 0:
        return np.nan
    return total_cost(event_frame, context) / fulfilled


class CoverageMetric(BaseInventoryMetric):
    """Mean SKU-period inventory coverage in periods.

    Multi-SKU slices require
    ``context['coverage_aggregation']='mean_of_sku_period_ratios'`` so pooled
    coverage is never an implicit choice.
    """

    def __init__(self, mode: str = "forward") -> None:
        if mode not in {"forward", "trailing"}:
            raise ValueError("mode must be 'forward' or 'trailing'")
        self.mode = mode
        self.name = f"coverage_{mode}"

    def compute(self, event_frame: pd.DataFrame, context: dict) -> float:
        period_events = _period_events(event_frame)
        if period_events.empty:
            return np.nan
        _require_columns(period_events, ["unique_id", "ending_on_hand", "demand"])
        if period_events["unique_id"].nunique() > 1 and context.get(
            "coverage_aggregation"
        ) != "mean_of_sku_period_ratios":
            raise ValueError(
                "multi-SKU coverage requires "
                "coverage_aggregation='mean_of_sku_period_ratios'"
            )

        inventory = _numeric_series(period_events, "ending_on_hand")
        if self.mode == "forward":
            if "expected_demand_rate" in period_events.columns:
                demand_rate = _numeric_series(period_events, "expected_demand_rate")
                if (demand_rate < 0).any():
                    raise ValueError("expected_demand_rate must be non-negative")
            elif "forward_demand_rate" in context:
                rate = _context_scalar(context, "forward_demand_rate")
                demand_rate = pd.Series(rate, index=period_events.index)
            else:
                raise ValueError(
                    "forward coverage requires expected_demand_rate or forward_demand_rate"
                )
        else:
            period_events = period_events.copy()
            period_events["_demand"] = _numeric_series(period_events, "demand")
            realized_by_sku = period_events.groupby("unique_id")["_demand"].transform("mean")
            demand_rate = realized_by_sku.astype(float)

        ratios = inventory / demand_rate.replace(0.0, np.nan)
        return float(ratios.replace([np.inf, -np.inf], np.nan).mean())
