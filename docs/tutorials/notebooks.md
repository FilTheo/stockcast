# Notebook collection

The notebooks are the full runnable examples for Stockcast 0.1. Each one uses
small, inspectable inputs and makes its assumptions visible. Run them after
installing Stockcast and any notebook-specific optional packages.

| Notebook | Question it answers |
|---|---|
| [01 — Inventory flow](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/01_introduction_to_inventory_flow.ipynb) | How do demand, an order, and the lead-time pipeline change state? |
| [02 — First engine simulation](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/02_first_engine_simulation.ipynb) | What does a complete validated run produce? |
| [02b — Decision schedules](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/02b_decision_schedules.ipynb) | How do periodic, delayed, one-time, and irregular opportunities differ, and when is `review_period` enough? |
| [03 — Component loop](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/03_component_loop.ipynb) | When should a caller own the loop, and what does the engine add? |
| [04 — Weekly forecast to order](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/04_forecast_to_inventory_integration.ipynb) | How does a one-week Smooth forecast become a weekly order using either policy API? |
| [04b — Daily newsvendor](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/04b_daily_newsvendor.ipynb) | How do price, cost, salvage, and an empirical demand forecast select one daily purchase? |
| [04c — Cumulative protection](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/04c_cumulative_protection_target.ipynb) | How does lead time turn a forecast into a multi-day protection target? |
| [04d — Rolling cumulative targets](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/04d_rolling_cumulative_targets.ipynb) | How are later dated forecast snapshots supplied to the engine? |
| [04e — Cumulative target methods](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/04e_cumulative_target_methods.ipynb) | How do independent-normal, external approximate, and simulated cumulative targets affect the same inventory replay? |
| [04f — Scheduled forecast simulation](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/04f_scheduled_forecast_simulation.ipynb) | How do an irregular decision schedule, dated Smooth targets, order-up-to policy, and the simulator work together? |
| [05 — Custom policies](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/05_custom_policies.ipynb) | How can a familiar `(s,Q)` rule be written, tuned against declared costs, and extended? |
| [05b — Reorder points and review frequency](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/05b_reorder_points_and_review_frequency.ipynb) | How do review timing, a dated reorder point, and order sizing affect service and cost? Starts with one SKU, then builds a 100-SKU comparison step by step. |
| [05c — Extension points](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/05c_extension_points.ipynb) | How can one dairy run grow from a custom order calendar to whole-case capacity rules and dated exceptions, while the event ledger shows each effect? |
| [05d — Open orders and suppliers](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/05d_open_orders_and_suppliers.ipynb) | How does the per-SKU pipeline map to named orders, and how can a café chain compare a roaster, an importer, split deliveries, and supplier allocation step by step? |
| [05e — Inventory processes](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/05e_inventory_processes.ipynb) | How does `ShelfLifeEngine` map to a `ShelfLife` process, and how can a user-written inspection or returns process be combined with shelf life while every unit stays in the audited stock balance? |
| [06 — Fair comparisons](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/06_fair_forecast_and_policy_comparisons.ipynb) | How do cumulative forecast targets and ordering rules separately affect service and cost on a shared five-SKU replay? Includes a declared per-SKU service screen. |
| [07 — FIFO shelf life](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/07_m5_fifo_perishable_scenario.ipynb) | How do dated lots, FIFO use, and expiry affect a scenario, and what changes when a hypothetical inspection process is added? |
| [08 — Callbacks and audit](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/08_callbacks_and_audit.ipynb) | How do typed interventions remain validated and auditable? |
| [09 — Full operational experiment](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/09_full_operational_experiment.ipynb) | How can several explicit operational assumptions be studied together? |
| [10 — Production daily close](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/10_production_daily_close.ipynb) | What is one way to place Stockcast in a production-oriented batch workflow? |

Notebook 10 is an integration pattern, not a prescribed production
architecture. Stockcast is modular; a real deployment can use a different
orchestration, approval, storage, and monitoring arrangement.

Note: notebooks 04e and 09 use Smooth's simulated interval, which has no
public seed parameter, so their simulated targets can vary slightly between
runs.

Notebook 04b uses an empirical predictive distribution estimated from artificial
historical demand. It checks the newsvendor quantity against forecast expected
profit and evaluates a held-out day without using it to choose the order.
