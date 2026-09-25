# Guide

The guide explains each part of Stockcast in depth: what it does, the
theory behind it, how to configure it, and how it connects to the rest. Pages
follow the same pattern: the idea, the math, the code, and pointers to
notebooks where you can see it at work.

If you are new, the [Learn the basics](../learn/index.md) series is a gentler
first pass through the same ideas, from the first simulation to a production
daily job.

## I want to…

Find the building block for your job, the page that explains it, and a
notebook that uses it.

| I want to… | Use | Read | See it in |
|---|---|---|---|
| start from my current stock and open orders | `InventoryStateDataFrame`, `with_open_orders` | [Inventory state](inventory-state.md) | [01](../notebooks/01_introduction_to_inventory_flow.ipynb), [05d](../notebooks/05d_open_orders_and_suppliers.ipynb) |
| feed in sales history or simulated demand | demand table, `DemandGenerator` | [Demand and calendars](demand.md) | [02](../notebooks/02_first_engine_simulation.ipynb) |
| turn my forecast into a stock target | target table + `fit` | [Forecast targets](forecast-targets.md), [Connect any forecasting model](../how-to/connect-a-forecaster.md) | [04c](../notebooks/04c_cumulative_protection_target.ipynb), [04e](../notebooks/04e_cumulative_target_methods.ipynb) |
| order up to a level at every review | `OrderUpToPolicy` | [Order-up-to](policies/order-up-to.md) | [04](../notebooks/04_forecast_to_inventory_integration.ipynb), [06](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb) |
| order only when stock falls low | `ReorderPointPolicy`, `PeriodicReviewPolicy` | [Reorder point](policies/reorder-point.md), [Periodic review](policies/periodic-review.md) | [05b](../notebooks/05b_reorder_points_and_review_frequency.ipynb), [08](../notebooks/08_callbacks_and_audit.ipynb) |
| buy once for a season | `SingleOrderPolicy`, `newsvendor_critical_fractile` | [Single order](policies/single-order.md) | [04b](../notebooks/04b_daily_newsvendor.ipynb) |
| use my own ordering rule | subclass `BasePolicy` | [Write your own policy](policies/custom-policies.md) | [05](../notebooks/05_custom_policies.ipynb) |
| order on specific days only | `PeriodicSchedule`, `ExplicitSchedule`, `DecisionSchedule` | [Decision schedules](decision-schedules.md) | [02b](../notebooks/02b_decision_schedules.ipynb), [04f](../notebooks/04f_scheduled_forecast_simulation.ipynb) |
| refresh targets as new forecasts arrive | `policy_schedule` | [Refresh targets as forecasts roll](../how-to/rolling-targets.md) | [04d](../notebooks/04d_rolling_cumulative_targets.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| respect minimums, case sizes, or capacity | `OrderingConstraints` and friends | [Ordering constraints](constraints.md) | [05c](../notebooks/05c_extension_points.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| add holidays, promotions, or stock corrections | `SimulationCallback`, scheduled callbacks | [Callbacks](callbacks.md) | [08](../notebooks/08_callbacks_and_audit.ipynb), [05c](../notebooks/05c_extension_points.ipynb) |
| use several suppliers or random lead times | `SupplyModel`, `Supplier`, `SupplierAllocation` | [Suppliers and open orders](suppliers.md) | [05d](../notebooks/05d_open_orders_and_suppliers.ipynb) |
| model late or short deliveries | `DeliveryOutcome` | [Unreliable deliveries](unreliable-deliveries.md) | [05f](../notebooks/05f_unreliable_supplier.ipynb) |
| model products that expire, inspections, returns | `ShelfLife`, `ShelfLifeEngine`, `InventoryProcess` | [Shelf life and processes](processes.md) | [05e](../notebooks/05e_inventory_processes.ipynb), [07](../notebooks/07_m5_fifo_perishable_scenario.ipynb) |
| simulate a policy over time | `SimulationEngine.run` | [The simulation engine](engine.md) | [02](../notebooks/02_first_engine_simulation.ipynb) |
| compare forecasts or policies fairly | `run_comparison` | [Compare forecasts and policies](../how-to/compare-policies.md) | [06](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| measure service, stock, and cost | `InventoryEvaluator`, metrics | [Evaluation and metrics](metrics.md), [Put costs on a run](../how-to/costs.md) | [07](../notebooks/07_m5_fifo_perishable_scenario.ipynb), [09](../notebooks/09_full_operational_experiment.ipynb) |
| compute real orders every day | `advance_period` → `predict` → constraints → `fulfill_demand` | [Use Stockcast in a daily job](../how-to/production.md), [Learn step 9](../learn/09-production.md) | [10](../notebooks/10_production_daily_close.ipynb) |

## Core ideas

Read these first. Everything else builds on them.

<div class="grid cards" markdown>

-   **[How Stockcast fits together](concepts/architecture.md)**

    The objects, who owns what, and how they combine into one run.

-   **[Notation and glossary](concepts/notation.md)**

    Every symbol and term used across the docs, in one place.

-   **[Timing: receive, decide, demand](concepts/timing.md)**

    The order of events in a period, and where $H = L + R$ comes from.

-   **[Stock accounting](concepts/accounting.md)**

    The balance equations every ledger row satisfies.

</div>

## Building blocks

| Page | Covers | Main objects |
|---|---|---|
| [Inventory state](inventory-state.md) | Opening stock, the pipeline, open orders | `InventoryStateDataFrame` |
| [Demand and calendars](demand.md) | Demand tables, frequencies, generators | `DemandGenerator` |
| [Decision schedules](decision-schedules.md) | When a policy may order | `PeriodicSchedule`, `OneTimeSchedule`, `ExplicitSchedule` |
| [Forecast targets](forecast-targets.md) | From forecast to stock target | target tables, `policy_schedule` |
| [Policies](policies/index.md) | Ordering rules and how to choose one | `OrderUpToPolicy`, `ReorderPointPolicy`, `PeriodicReviewPolicy`, `SingleOrderPolicy`, `BasePolicy` |
| [The simulation engine](engine.md) | Running and comparing simulations | `SimulationEngine`, `SimulationResult` |
| [Ordering constraints](constraints.md) | Minimums, case packs, maximums, shelf space | `OrderingConstraints` and friends |
| [Callbacks](callbacks.md) | Planned interventions with an audit trail | `SimulationCallback`, scheduled callbacks |
| [Suppliers and open orders](suppliers.md) | Several suppliers, random lead times, split deliveries | `SupplyModel`, `Supplier`, `OrderLines` |
| [Unreliable deliveries](unreliable-deliveries.md) | Late, short, or missing deliveries | `DeliveryOutcome` |
| [Shelf life and inventory processes](processes.md) | Expiry, inspections, returns | `ShelfLife`, `ShelfLifeEngine`, `InventoryProcess` |
| [Evaluation and metrics](metrics.md) | Service, stock, and cost metrics | `InventoryEvaluator`, metric functions |
| [Plots](visualization.md) | Ready-made charts of a run | `plot_inventory`, dashboards |

## Recipes

Short answers to specific jobs, with runnable code:
[connect any forecasting model](../how-to/connect-a-forecaster.md),
[targets from sample paths](../how-to/targets-from-sample-paths.md),
[rolling targets](../how-to/rolling-targets.md),
[compare forecasts and policies](../how-to/compare-policies.md),
[costs](../how-to/costs.md),
[your own simulation loop](../how-to/manual-loop.md),
[a daily production job](../how-to/production.md), and
[upgrading from demand-first timing](../how-to/timing-migration.md).
See [all recipes](../how-to/index.md).

## Design decisions

The reasoning behind the design lives in the
[Philosophy](../get-started/philosophy.md), with six short deep dives:
[decisions before demand](design/decide-before-demand.md),
[targets for a whole window](design/cumulative-targets.md),
[explicit inputs](design/explicit-inputs.md),
[engine-owned stock](design/engine-owns-state.md),
[the ledger as the record](design/ledger.md), and
[small parts you combine](design/composition.md).
