# Examples

Twenty-four runnable notebooks, from a first simulation to multi-supplier,
perishable, and production workflows, with real forecasts from
[smooth](https://openforecast.org/smooth-py/). Each page
here is the executed notebook; use the download button to run it yourself.

!!! tip "Running the notebooks"

    Clone the repository, install Stockcast, then
    `pip install jupyterlab "smooth>=1.0.7"` and open `examples/notebooks`. Notebook
    04e uses smooth's simulated intervals, so its simulated targets can differ
    slightly between runs.

## Foundations

Start here if you are new. These follow the same ideas as the
[Walkthrough](../learn/index.md), with more pictures.

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
| [04 · Weekly forecast to order](../notebooks/04_forecast_to_inventory_integration.ipynb) | a one-week smooth forecast becomes a weekly order: one step, then a loop through time, then the simulator |
| [04a · Forecasting from sales](../notebooks/04a_forecasting_from_sales.ipynb) | how stockouts hide demand from a forecaster that learns from sales, and how to refit the forecast inside a run |
| [04b · Daily newsvendor](../notebooks/04b_daily_newsvendor.ipynb) | price, cost, and salvage choose one daily purchase |
| [04c · Cumulative protection target](../notebooks/04c_cumulative_protection_target.ipynb) | lead time turns a forecast into a multi-day protection target |
| [04d · Rolling cumulative targets](../notebooks/04d_rolling_cumulative_targets.ipynb) | dated forecast snapshots refitted at every review |
| [04e · Cumulative target methods](../notebooks/04e_cumulative_target_methods.ipynb) | independent-normal, approximate, and simulated targets on the same replay |
| [04f · Scheduled forecast simulation](../notebooks/04f_scheduled_forecast_simulation.ipynb) | an irregular schedule with a forecast for each coverage window |

## Extending Stockcast

Custom rules and richer operations, one building block at a time.

| Notebook | You will learn |
|---|---|
| [05 · Write your own ordering rule](../notebooks/05_custom_policies.ipynb) | write an $(s, Q)$ rule yourself, tune it on costs, and extend it |
| [05b · How often to check stock](../notebooks/05b_reorder_points_and_review_frequency.ipynb) | where to put the reorder point and how often to check stock when checks happen at set times, from one SKU to 100 |
| [05c · Real-world rules and disruptions](../notebooks/05c_extension_points.ipynb) | order days, whole-case and chiller-space rules, a cancelled supplier day and a stock count, added one at a time on the same demand |
| [05d · Several suppliers and late deliveries](../notebooks/05d_open_orders_and_suppliers.ipynb) | buy from a local roaster and a cheaper importer: random delivery times, deliveries in two parts, and a rule for who gets each order |
| [05e · Inventory processes: expiry, discards and returns](../notebooks/05e_inventory_processes.ipynb) | the built-in `ShelfLife` process, a custom outflow (`after_demand`) and inflow (`before_demand`), and the stock balance that ties every flow together |
| [05f · Supplier delivery outcomes: delays, shortfalls and caps](../notebooks/05f_unreliable_supplier.ipynb) | `DeliveryOutcome` for delayed and partial deliveries, an `OrderingConstraint` that caps orders, and a `SupplierAllocation` that sends the excess to a second supplier |
| [05g · Reorder levels per item](../notebooks/05g_reorder_point_sources.ipynb) | the ways `ReorderPointPolicy.fit` accepts `s` and `S` (one pair, per-item values, a dated quantile forecast, a custom provider) and what per-item levels change |

## Experiments and operations

Complete studies and a production pattern.

| Notebook | You will learn |
|---|---|
| [06 · Controlled comparisons with `run_comparison`](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb) | targets from an external forecaster, one `run_comparison` with five branches, and the controlled contrasts it gives: forecast probability, forecast model, and policy rule |
| [07 · Shelf life on real demand (M5)](../notebooks/07_m5_fifo_perishable_scenario.ipynb) | `ShelfLife` with dated opening deliveries on 20 M5 items, forecast and history targets across three shelf lives, a custom waste-rate metric and the cost components |
| [08 · Callbacks: changing orders and stock during a run](../notebooks/08_callbacks_and_audit.ipynb) | `on_after_prediction` and `on_after_demand` callbacks, the audit table, why callback order matters, and how constraints apply after callbacks |
| [09 · A full experiment: forecasts, policies and extensions](../notebooks/09_full_operational_experiment.ipynb) | three forecasts refitted at every review and passed as a `policy_schedule` on 30 M5 items; supplier rules, a callback, an unreliable supplier and a shelf life added one at a time and measured; then `run_comparison` of `(R,S)` and `(s,S)` with each forecast, priced with the evaluator |
| [10 · A production job: plan, close and event triggers](../notebooks/10_production_daily_close.ipynb) | a live job built up step by step: one day with every table (restore with `from_observed` and `with_open_orders`, receive, refit, `predict`, supplier rules, `fulfill_demand`), pure `plan` and `close` functions, a store with idempotent phases and an order outbox, an event stream that triggers the phases, a shadow backtest with `SimulationEngine` for monitoring, and a morning rebuilt from a stock count and a late delivery |
