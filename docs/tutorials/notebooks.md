# Notebook collection

The notebooks are the full runnable examples for Pyforia 0.1. Each one uses
small, inspectable inputs and makes its assumptions visible. Run them after
installing Pyforia and any notebook-specific optional packages.

| Notebook | Question it answers |
|---|---|
| [01 — Inventory flow](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/01_introduction_to_inventory_flow.ipynb) | How do demand, an order, and the lead-time pipeline change state? |
| [02 — First engine simulation](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/02_first_engine_simulation.ipynb) | What does a complete validated run produce? |
| [03 — Component loop](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/03_component_loop.ipynb) | When should a caller own the loop, and what does the engine add? |
| [04 — Forecast integration](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/04_forecast_to_inventory_integration.ipynb) | How can a forecast become a valid inventory target? |
| [04b — Rolling updates](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/04b_rolling_forecast_updates.ipynb) | How are later forecast snapshots scheduled? |
| [04c — Cumulative targets](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/04c_cumulative_target_methods.ipynb) | How do independent-normal, external approximate, and external simulated targets enter the same inventory replay? |
| [05 — Custom policies](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/05_custom_policies.ipynb) | How can a supported policy extension be written? |
| [06 — Fair comparisons](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/06_fair_forecast_and_policy_comparisons.ipynb) | How can forecasts and policies be compared on the same demand path? |
| [07 — FIFO shelf life](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/07_m5_fifo_perishable_scenario.ipynb) | How do dated lots, FIFO use, and expiry affect a scenario? |
| [08 — Callbacks and audit](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/08_callbacks_and_audit.ipynb) | How do typed interventions remain validated and auditable? |
| [09 — Full operational experiment](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/09_full_operational_experiment.ipynb) | How can several explicit operational assumptions be studied together? |
| [10 — Production daily close](https://github.com/FilTheo/pyforia/blob/main/examples/notebooks/10_production_daily_close.ipynb) | What is one way to place Pyforia in a production-oriented batch workflow? |

Notebook 10 is an integration pattern, not a prescribed production
architecture. Pyforia is modular; a real deployment can use a different
orchestration, approval, storage, and monitoring arrangement.

Note: notebooks 04c and 09 use Smooth's simulated interval, which has no
public seed parameter, so their simulated targets can vary slightly between
runs.
