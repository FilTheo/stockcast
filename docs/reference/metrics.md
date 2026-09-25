# Metrics

Every metric has the signature `metric(event_frame, context=None) -> float` and is imported from `stockcast.evaluation`. The [metrics guide](../user-guide/metrics.md) gives every formula in one table.

## Service

::: stockcast.evaluation.fill_rate
    options:
      heading_level: 3

::: stockcast.evaluation.demand_period_service_level
    options:
      heading_level: 3

::: stockcast.evaluation.cycle_service_level
    options:
      heading_level: 3

::: stockcast.evaluation.sku_period_stockout_rate
    options:
      heading_level: 3

::: stockcast.evaluation.stockout_period_rate
    options:
      heading_level: 3

::: stockcast.evaluation.backorder_period_rate
    options:
      heading_level: 3

## Quantities

::: stockcast.evaluation.demand_units
    options:
      heading_level: 3

::: stockcast.evaluation.fulfilled_units
    options:
      heading_level: 3

::: stockcast.evaluation.shortage_units
    options:
      heading_level: 3

::: stockcast.evaluation.lost_sales_units
    options:
      heading_level: 3

::: stockcast.evaluation.order_units
    options:
      heading_level: 3

::: stockcast.evaluation.backlog_unit_periods
    options:
      heading_level: 3

::: stockcast.evaluation.terminal_backlog_units
    options:
      heading_level: 3

::: stockcast.evaluation.terminal_pipeline_units
    options:
      heading_level: 3

::: stockcast.evaluation.order_event_count
    options:
      heading_level: 3

::: stockcast.evaluation.sku_order_line_count
    options:
      heading_level: 3

::: stockcast.evaluation.sku_order_quantity_variance
    options:
      heading_level: 3

::: stockcast.evaluation.capacity_violation_count
    options:
      heading_level: 3

::: stockcast.evaluation.capacity_violation_rate
    options:
      heading_level: 3

## Stock

::: stockcast.evaluation.avg_on_hand
    options:
      heading_level: 3

::: stockcast.evaluation.avg_on_order
    options:
      heading_level: 3

::: stockcast.evaluation.avg_inventory_position
    options:
      heading_level: 3

::: stockcast.evaluation.peak_ending_on_hand
    options:
      heading_level: 3

::: stockcast.evaluation.ending_on_hand_variance
    options:
      heading_level: 3

::: stockcast.evaluation.inventory_turns
    options:
      heading_level: 3

## Costs

::: stockcast.evaluation.holding_cost
    options:
      heading_level: 3

::: stockcast.evaluation.shortage_cost
    options:
      heading_level: 3

::: stockcast.evaluation.backlog_cost
    options:
      heading_level: 3

::: stockcast.evaluation.ordering_cost
    options:
      heading_level: 3

::: stockcast.evaluation.purchase_cost
    options:
      heading_level: 3

::: stockcast.evaluation.waste_cost
    options:
      heading_level: 3

::: stockcast.evaluation.terminal_backlog_cost
    options:
      heading_level: 3

::: stockcast.evaluation.terminal_pipeline_cost
    options:
      heading_level: 3

::: stockcast.evaluation.salvage_credit
    options:
      heading_level: 3

::: stockcast.evaluation.total_cost
    options:
      heading_level: 3

::: stockcast.evaluation.cost_per_demand_unit
    options:
      heading_level: 3

::: stockcast.evaluation.cost_per_fulfilled_unit
    options:
      heading_level: 3
