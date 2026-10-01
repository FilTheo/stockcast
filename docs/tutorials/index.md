# Examples

Twenty-three runnable notebooks, from a first simulation to multi-supplier,
perishable, and production workflows, with real forecasts from
[smooth](https://openforecast.org/smooth-py/). Each page
here is the executed notebook; use the download button to run it yourself.

!!! tip "Running the notebooks"

    Clone the repository, install Stockcast, then
    `pip install jupyterlab "smooth>=1.0.7"` and open `examples/notebooks`. Notebooks
    04e and 09 use smooth's simulated intervals, so their simulated targets can
    differ slightly between runs.

## Foundations

Start here if you are new. These follow the same ideas as
[Learn the basics](../learn/index.md), with more pictures.

| Notebook | You will learn |
|---|---|
| [01 · Inventory flow](../notebooks/01_introduction_to_inventory_flow.ipynb) | how demand, an order, and the lead-time pipeline change the state |
| [02 · First engine simulation](../notebooks/02_first_engine_simulation.ipynb) | what a complete, checked run produces: event table, windows, manifest, metrics |
| [02b · Decision schedules](../notebooks/02b_decision_schedules.ipynb) | periodic, delayed, one-time, and irregular ordering calendars |
| [02c · Synthetic demand](../notebooks/02c_synthetic_demand.ipynb) | demand from any distribution: one sampler for the panel or one per SKU |
| [03 · Your own loop](../notebooks/03_component_loop.ipynb) | the primitives behind the engine, and what the engine adds |

## Forecasts to orders

How forecasts become targets, and targets become orders.

| Notebook | You will learn |
|---|---|
| [04 · Weekly forecast to order](../notebooks/04_forecast_to_inventory_integration.ipynb) | a one-week smooth forecast becomes a weekly order, with two policy APIs |
| [04b · Daily newsvendor](../notebooks/04b_daily_newsvendor.ipynb) | price, cost, and salvage choose one daily purchase |
| [04c · Cumulative protection target](../notebooks/04c_cumulative_protection_target.ipynb) | lead time turns a forecast into a multi-day protection target |
| [04d · Rolling cumulative targets](../notebooks/04d_rolling_cumulative_targets.ipynb) | dated forecast snapshots refitted at every review |
| [04e · Cumulative target methods](../notebooks/04e_cumulative_target_methods.ipynb) | independent-normal, approximate, and simulated targets on the same replay |
| [04f · Scheduled forecast simulation](../notebooks/04f_scheduled_forecast_simulation.ipynb) | an irregular schedule with a forecast for each coverage window |

## Extending Stockcast

Custom rules and richer operations, one building block at a time.

| Notebook | You will learn |
|---|---|
| [05 · Custom policies](../notebooks/05_custom_policies.ipynb) | write an $(s, Q)$ rule yourself, tune it on costs, and extend it |
| [05b · Reorder points and review frequency](../notebooks/05b_reorder_points_and_review_frequency.ipynb) | review timing, dated reorder points, and order sizing, from one SKU to 100 |
| [05c · Extension points](../notebooks/05c_extension_points.ipynb) | a custom calendar, whole-case capacity rules, and dated exceptions in one run |
| [05d · Open orders and suppliers](../notebooks/05d_open_orders_and_suppliers.ipynb) | named orders, two suppliers, split deliveries, and allocation for a café chain |
| [05e · Inventory processes](../notebooks/05e_inventory_processes.ipynb) | shelf life as a process, plus an inspection and a dated return |
| [05f · Unreliable supplier](../notebooks/05f_unreliable_supplier.ipynb) | late and short deliveries, supplier capacity, and a backup supplier |
| [05g · Reorder points from any source](../notebooks/05g_reorder_point_sources.ipynb) | one reorder-point policy with levels from a house rule, a planning table, a forecast, and your own rule, compared on shared demand |

## Experiments and operations

Complete studies and a production pattern.

| Notebook | You will learn |
|---|---|
| [06 · Fair comparisons](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb) | how forecasts and ordering rules separately affect service and cost on five SKUs |
| [07 · FIFO shelf life on M5 demand](../notebooks/07_m5_fifo_perishable_scenario.ipynb) | follow dated demand through a target, order, FIFO lot, expiry, and scored outcome |
| [08 · Callbacks and audit](../notebooks/08_callbacks_and_audit.ipynb) | trace a policy request through typed interventions, final events, and audit records |
| [09 · Full operational experiment](../notebooks/09_full_operational_experiment.ipynb) | calibrate cumulative targets, replay a shared operation, and select by accepted all-in cost |
| [10 · Production daily close](../notebooks/10_production_daily_close.ipynb) | one way to run Stockcast as a daily planning and closing job |
