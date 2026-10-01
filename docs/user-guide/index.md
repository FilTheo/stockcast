# Guide

The guide covers each part of Stockcast in depth: what it does, the theory
behind it, and how to configure it. New users may prefer the
[Walkthrough](../learn/index.md) first.

- **Core ideas**: [architecture](concepts/architecture.md),
  [notation](concepts/notation.md), [timing](concepts/timing.md) and
  [other timing conventions](../how-to/timing-conventions.md), and
  [stock accounting](concepts/accounting.md).
- **Building blocks**: one page per component, from
  [inventory state](inventory-state.md) to [plots](visualization.md).
- **Recipes**: [short, task-focused pages](../how-to/index.md) with
  runnable code.

## I want to…

Find the building block for your job, the page that explains it, and a
notebook that uses it.

| I want to… | Use | Read | See it in |
|---|---|---|---|
| start from my current stock and open orders | `InventoryStateDataFrame`, `with_open_orders` | [Inventory state](inventory-state.md) | [01](../notebooks/01_introduction_to_inventory_flow.ipynb), [05d](../notebooks/05d_open_orders_and_suppliers.ipynb) |
| feed in sales history or simulated demand | demand table, `DemandGenerator` | [Demand and calendars](demand.md) | [02](../notebooks/02_first_engine_simulation.ipynb), [02c](../notebooks/02c_synthetic_demand.ipynb) |
| turn my forecast into a stock target | target table + `fit` | [Forecast targets](forecast-targets.md), [Connect any forecasting model](../how-to/connect-a-forecaster.md) | [04c](../notebooks/04c_cumulative_protection_target.ipynb), [04e](../notebooks/04e_cumulative_target_methods.ipynb) |
| order up to a level at every review | `OrderUpToPolicy` | [Order-up-to](policies/order-up-to.md) | [04](../notebooks/04_forecast_to_inventory_integration.ipynb), [06](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb) |
| order only when stock falls low | `ReorderPointPolicy` | [Reorder point](policies/reorder-point.md) | [05b](../notebooks/05b_reorder_points_and_review_frequency.ipynb), [05g](../notebooks/05g_reorder_point_sources.ipynb), [08](../notebooks/08_callbacks_and_audit.ipynb) |
| buy once for a season | `SingleOrderPolicy`, `newsvendor_critical_fractile` | [Single order](policies/single-order.md) | [04b](../notebooks/04b_daily_newsvendor.ipynb) |
| use my own ordering rule | subclass `BasePolicy` | [Write your own policy](policies/custom-policies.md) | [05](../notebooks/05_custom_policies.ipynb) |
| order on specific days only | `PeriodicSchedule`, `ExplicitSchedule`, `DecisionSchedule` | [Decision schedules](decision-schedules.md) | [02b](../notebooks/02b_decision_schedules.ipynb), [04f](../notebooks/04f_scheduled_forecast_simulation.ipynb) |
| use a lead time from a paper or another tool | `lead_time`, `PeriodicSchedule(start=...)` | [Work with any timing convention](../how-to/timing-conventions.md) | – |
| refresh targets as new forecasts arrive | `policy_schedule` | [Refresh targets as forecasts roll](../how-to/rolling-targets.md) | [04d](../notebooks/04d_rolling_cumulative_targets.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| respect minimums, case sizes, or capacity | `OrderingConstraints` and friends | [Ordering constraints](constraints.md) | [05c](../notebooks/05c_extension_points.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| add holidays, promotions, or stock corrections | `SimulationCallback`, scheduled callbacks | [Callbacks](callbacks.md) | [08](../notebooks/08_callbacks_and_audit.ipynb), [05c](../notebooks/05c_extension_points.ipynb) |
| use several suppliers or random lead times | `SupplyModel`, `Supplier`, `SupplierAllocation` | [Suppliers and open orders](suppliers.md) | [05d](../notebooks/05d_open_orders_and_suppliers.ipynb) |
| model late or short deliveries | `DeliveryOutcome` | [Unreliable deliveries](unreliable-deliveries.md) | [05f](../notebooks/05f_unreliable_supplier.ipynb) |
| model products that expire, inspections, returns | `ShelfLife`, `InventoryProcess` | [Shelf life and processes](processes.md) | [05e](../notebooks/05e_inventory_processes.ipynb), [07](../notebooks/07_m5_fifo_perishable_scenario.ipynb) |
| simulate a policy over time | `SimulationEngine.run` | [The simulation engine](engine.md) | [02](../notebooks/02_first_engine_simulation.ipynb) |
| compare forecasts or policies fairly | `run_comparison` | [Compare forecasts and policies](../how-to/compare-policies.md) | [06](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| measure service, stock, and cost | `InventoryEvaluator`, metrics | [Evaluation and metrics](metrics.md), [Put costs on a run](../how-to/costs.md) | [07](../notebooks/07_m5_fifo_perishable_scenario.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| compute real orders every day | `advance_period` → `predict` → constraints → `fulfill_demand` | [Use Stockcast in a daily job](../how-to/production.md), [Walkthrough step 9](../learn/09-production.md) | [10](../notebooks/10_production_daily_close.ipynb) |

The reasoning behind the design is in the
[Philosophy](../get-started/philosophy.md).
