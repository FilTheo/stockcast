# Evaluation and metrics API

Evaluate canonical event rows with an explicit window and aggregation grain.
Pass `groupby=[]` for a pooled result; otherwise name the retained dimensions.
Metrics consume period rows except where their documented terminal or
order-event grain says otherwise.

## Metric families

| Family | Public functions |
|---|---|
| Demand and service | `demand_units`, `fulfilled_units`, `shortage_units`, `lost_sales_units`, `fill_rate`, `demand_period_service_level`, `cycle_service_level` |
| Backlog and stockouts | `backlog_unit_periods`, `terminal_backlog_units`, `backorder_period_rate`, `sku_period_stockout_rate`, `stockout_period_rate` |
| Orders and capacity | `order_units`, `sku_order_line_count`, `order_event_count`, `sku_order_quantity_variance`, `capacity_violation_count`, `capacity_violation_rate` |
| Inventory | `avg_on_hand`, `avg_inventory_position`, `avg_on_order`, `ending_on_hand_variance`, `peak_ending_on_hand`, `inventory_turns` |
| Costs | `holding_cost`, `shortage_cost`, `backlog_cost`, `ordering_cost`, `purchase_cost`, `waste_cost`, `terminal_backlog_cost`, `terminal_pipeline_cost`, `salvage_credit`, `total_cost`, `cost_per_demand_unit`, `cost_per_fulfilled_unit` |

Cost metrics use only explicitly activated `cost_components`, and each active
rate must be provided in the evaluator context or event frame. `CoverageMetric`
requires a forward or trailing demand-rate choice.

::: stockcast.evaluation
    options:
      show_root_heading: false
      members: true
