# Examples

Twenty-one runnable notebooks, from a first simulation to multi-supplier,
perishable, and production workflows. They go further than the rest of the
docs: realistic scenarios, real forecasts with
[smooth](https://openforecast.org/smooth-py/), and plenty of plots. Each page
here is the executed notebook; use the download button to run it yourself.

!!! tip "Running the notebooks"

    Clone the repository, install Stockcast, then
    `pip install jupyterlab smooth` and open `examples/notebooks`. Notebooks
    04e and 09 use smooth's simulated intervals, so their simulated targets can
    differ slightly between runs.

Each example lists the building blocks it uses. If a notebook moves fast, follow
those links to the Guide page that explains the piece, then come back.

## Foundations

Start here if you are new. These follow the same ideas as
[Learn the basics](../learn/index.md), with more pictures.

| Notebook | You will learn | Building blocks used |
|---|---|---|
| [01 · Inventory flow](../notebooks/01_introduction_to_inventory_flow.ipynb) | how demand, an order, and the lead-time pipeline change the state | [state](../user-guide/inventory-state.md) · [order-up-to](../user-guide/policies/order-up-to.md) · [manual loop](../how-to/manual-loop.md) |
| [02 · First engine simulation](../notebooks/02_first_engine_simulation.ipynb) | what a complete, checked run produces: ledger, windows, manifest, metrics | [engine](../user-guide/engine.md) · [ledger](../user-guide/concepts/accounting.md) · [metrics](../user-guide/metrics.md) · [plots](../user-guide/visualization.md) |
| [02b · Decision schedules](../notebooks/02b_decision_schedules.ipynb) | periodic, delayed, one-time, and irregular ordering calendars | [schedules](../user-guide/decision-schedules.md) · [periodic review](../user-guide/policies/periodic-review.md) |
| [03 · Your own loop](../notebooks/03_component_loop.ipynb) | the primitives behind the engine, and what the engine adds | [manual loop](../how-to/manual-loop.md) · [engine](../user-guide/engine.md) · [metrics](../user-guide/metrics.md) |

## Forecasts to orders

How forecasts become targets, and targets become orders.

| Notebook | You will learn | Building blocks used |
|---|---|---|
| [04 · Weekly forecast to order](../notebooks/04_forecast_to_inventory_integration.ipynb) | a one-week smooth forecast becomes a weekly order, with two policy APIs | [forecasting models](../how-to/connect-a-forecaster.md) · [forecast targets](../user-guide/forecast-targets.md) · [order-up-to](../user-guide/policies/order-up-to.md) · [single order](../user-guide/policies/single-order.md) |
| [04b · Daily newsvendor](../notebooks/04b_daily_newsvendor.ipynb) | price, cost, and salvage choose one daily purchase | [single order](../user-guide/policies/single-order.md) · [forecast targets](../user-guide/forecast-targets.md) |
| [04c · Cumulative protection target](../notebooks/04c_cumulative_protection_target.ipynb) | lead time turns a forecast into a multi-day protection target | [forecast targets](../user-guide/forecast-targets.md) · [order-up-to](../user-guide/policies/order-up-to.md) · [metrics](../user-guide/metrics.md) |
| [04d · Rolling cumulative targets](../notebooks/04d_rolling_cumulative_targets.ipynb) | dated forecast snapshots refitted at every review | [rolling targets](../how-to/rolling-targets.md) · [forecast targets](../user-guide/forecast-targets.md) · [order-up-to](../user-guide/policies/order-up-to.md) |
| [04e · Cumulative target methods](../notebooks/04e_cumulative_target_methods.ipynb) | independent-normal, approximate, and simulated targets on the same replay | [forecast targets](../user-guide/forecast-targets.md) · [comparisons](../how-to/compare-policies.md) |
| [04f · Scheduled forecast simulation](../notebooks/04f_scheduled_forecast_simulation.ipynb) | an irregular schedule with a forecast for each coverage window | [schedules](../user-guide/decision-schedules.md) · [rolling targets](../how-to/rolling-targets.md) · [forecast targets](../user-guide/forecast-targets.md) |

## Extending Stockcast

Custom rules and richer operations, one building block at a time.

| Notebook | You will learn | Building blocks used |
|---|---|---|
| [05 · Custom policies](../notebooks/05_custom_policies.ipynb) | write an $(s, Q)$ rule yourself, tune it on costs, and extend it | [custom policy](../user-guide/policies/custom-policies.md) · [comparisons](../how-to/compare-policies.md) · [costs](../how-to/costs.md) |
| [05b · Reorder points and review frequency](../notebooks/05b_reorder_points_and_review_frequency.ipynb) | review timing, dated reorder points, and order sizing, from one SKU to 100 | [reorder point](../user-guide/policies/reorder-point.md) · [schedules](../user-guide/decision-schedules.md) · [comparisons](../how-to/compare-policies.md) · [costs](../how-to/costs.md) |
| [05c · Extension points](../notebooks/05c_extension_points.ipynb) | a custom calendar, whole-case capacity rules, and dated exceptions in one run | [schedules](../user-guide/decision-schedules.md) · [reorder point](../user-guide/policies/reorder-point.md) · [constraints](../user-guide/constraints.md) · [callbacks](../user-guide/callbacks.md) |
| [05d · Open orders and suppliers](../notebooks/05d_open_orders_and_suppliers.ipynb) | named orders, two suppliers, split deliveries, and allocation for a café chain | [suppliers](../user-guide/suppliers.md) · [state](../user-guide/inventory-state.md) · [manual loop](../how-to/manual-loop.md) · [comparisons](../how-to/compare-policies.md) |
| [05e · Inventory processes](../notebooks/05e_inventory_processes.ipynb) | shelf life as a process, plus an inspection and a dated return | [shelf life & processes](../user-guide/processes.md) · [callbacks](../user-guide/callbacks.md) |
| [05f · Unreliable supplier](../notebooks/05f_unreliable_supplier.ipynb) | late and short deliveries, supplier capacity, and a backup supplier | [unreliable deliveries](../user-guide/unreliable-deliveries.md) · [suppliers](../user-guide/suppliers.md) · [constraints](../user-guide/constraints.md) |

## Experiments and operations

Complete studies and a production pattern.

| Notebook | You will learn | Building blocks used |
|---|---|---|
| [06 · Fair comparisons](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb) | how forecasts and ordering rules separately affect service and cost on five SKUs | [forecast targets](../user-guide/forecast-targets.md) · [order-up-to](../user-guide/policies/order-up-to.md) · [reorder point](../user-guide/policies/reorder-point.md) · [comparisons](../how-to/compare-policies.md) · [costs](../how-to/costs.md) |
| [07 · FIFO shelf life on M5 demand](../notebooks/07_m5_fifo_perishable_scenario.ipynb) | follow dated demand through a target, order, FIFO lot, expiry, and scored outcome | [shelf life & processes](../user-guide/processes.md) · [forecast targets](../user-guide/forecast-targets.md) · [periodic review](../user-guide/policies/periodic-review.md) · [costs](../how-to/costs.md) |
| [08 · Callbacks and audit](../notebooks/08_callbacks_and_audit.ipynb) | trace a policy request through typed interventions, final events, and audit records | [callbacks](../user-guide/callbacks.md) · [constraints](../user-guide/constraints.md) · [periodic review](../user-guide/policies/periodic-review.md) · [costs](../how-to/costs.md) |
| [09 · Full operational experiment](../notebooks/09_full_operational_experiment.ipynb) | calibrate cumulative targets, replay a shared operation, and select by accepted all-in cost | [rolling targets](../how-to/rolling-targets.md) · [comparisons](../how-to/compare-policies.md) · [callbacks](../user-guide/callbacks.md) · [constraints](../user-guide/constraints.md) · [costs](../how-to/costs.md) |
| [10 · Production daily close](../notebooks/10_production_daily_close.ipynb) | one way to run Stockcast as a daily planning and closing job | [daily job](../how-to/production.md) · [manual loop](../how-to/manual-loop.md) · [rolling targets](../how-to/rolling-targets.md) |
