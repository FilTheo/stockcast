<p align="center">
  <img src="https://raw.githubusercontent.com/FilTheo/stockcast/main/docs/assets/logo.png" width="170" alt="Stockcast logo">
</p>

<h1 align="center">Stockcast</h1>

<p align="center">
  <b>Turn forecasts into inventory decisions you can simulate, inspect, and evaluate.</b>
</p>

<p align="center">
  <a href="https://github.com/FilTheo/stockcast/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License: Apache 2.0"></a>
  <a href="https://github.com/FilTheo/stockcast/actions/workflows/docs.yml"><img src="https://github.com/FilTheo/stockcast/actions/workflows/docs.yml/badge.svg" alt="Documentation build"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+">
</p>

<p align="center">
  <a href="https://filtheo.github.io/stockcast/">Documentation</a> ·
  <a href="https://filtheo.github.io/stockcast/get-started/quickstart/">Quickstart</a> ·
  <a href="https://filtheo.github.io/stockcast/get-started/philosophy/">Philosophy</a> ·
  <a href="https://filtheo.github.io/stockcast/tutorials/">Examples</a> ·
  <a href="https://filtheo.github.io/stockcast/reference/">API</a>
</p>

A forecast is not a decision. Its value comes from the downstream decisions it
improves, and ultimately from the operational performance those decisions
deliver.

Stockcast brings this idea to inventory management: it is the layer
between the forecast and the replenishment decision. It maps forecasts from
**any model** into orders, simulates their execution against realised demand,
and evaluates the resulting impact against business metrics such as cost,
service, and waste. It is built for **researchers**
who judge forecasts by the decisions they drive, and for **engineers** who run
those decisions every day, with the same objects.

It is inspired by PyTorch and assembled like Lego: policies, schedules,
constraints, callbacks, suppliers, physical processes, and metrics are bricks
that snap onto one engine with explicit timing and checked accounting. Use the
built-in bricks, reshape any of them by subclassing, and build any inventory
system you need.

## Install

```bash
pip install stockcast
```

Stockcast needs Python 3.10+ and only NumPy, pandas, and Matplotlib.

## Quickstart

A tea shop sells about six packs a day, orders every 4 days, and waits 2 days
for deliveries. Turn a forecast into an ordering policy and replay four weeks
of sales:

```python
import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.evaluation import InventoryEvaluator, avg_on_hand, fill_rate
from stockcast.policies import OrderUpToPolicy

sku = "tea_250g"
opening = pd.Timestamp("2026-01-05")

# 1. Four weeks of daily sales from the till, starting the day after opening.
sales = [6, 7, 8, 6, 2, 3, 8, 6, 8, 4, 9, 5, 6, 5,
         2, 10, 3, 5, 7, 9, 8, 10, 4, 9, 5, 6, 2, 3]
demand = pd.DataFrame({
    "unique_id": sku,
    "period": range(28),
    "date": pd.date_range("2026-01-06", periods=28),
    "y": sales,
})

# 2. The shelf on the opening day: 30 packs, nothing on order yet.
inventory = InventoryStateDataFrame([sku], max_lead_time=2)
inventory.initialize_from_observed(
    pd.DataFrame({"unique_id": [sku], "on_hand": [30.0]}),
    on_hand_column="on_hand",
    start_date=opening,
)

# 3. Your forecast: a 95% chance that the next 6 days sell at most 46 packs.
#    Six days, because an order takes 2 days to arrive and the next one is 4 days away.
target = pd.DataFrame({
    "unique_id": [sku],
    "target": [46.0],
    "end": [opening + pd.Timedelta(days=6)],
})

# 4. The policy: every 4 days, order enough to bring stock up to that target.
policy = OrderUpToPolicy(
    lead_time=2,
    review_period=4,
    service_level=0.95,
    allow_backorders=False,           # a customer who finds no tea leaves
).fit(
    target,
    target_column="target",
    target_probability=0.95,          # the target is a 95% quantile...
    protection_horizon=6,             # ...of total demand over 6 days
    target_end_date_column="end",
    target_source="external_direct",  # computed by your model, not by Stockcast
    forecast_origin=opening,
    forecast_frequency="D",
)

# 5. Replay the four weeks with that policy, then measure what happened.
result = SimulationEngine().run(
    policy=policy,
    demand_source=demand,
    inventory=inventory,
    n_periods=28,
    period_frequency="D",
    warmup_periods=0, scoring_periods=28, settlement_periods=0,  # score all 28 days
    order_during_settlement=False,
    demand_source_name="till_sales",
    random_seed=None,                 # nothing random here
)

print(InventoryEvaluator().fit(result, window="scoring").evaluate(
    [fill_rate, avg_on_hand], groupby=[]).round(2))
```

```text
   fill_rate  avg_on_hand
0        1.0        19.86
```

`result.to_event_frame()` holds the full record: one balanced row per SKU and
day with every receipt, order, sale, and shortage. The
[Quickstart](https://filtheo.github.io/stockcast/get-started/quickstart/)
explains each step in more depth.

## Research and production

The same objects run a backtest and a daily job, so the policy you evaluated is
the policy you run:

```text
Research: backtest and compare
    forecast (any library) -> target -> policy -> SimulationEngine -> event ledger -> evaluation

Production: every day
    sales and deliveries -> inventory state -> refreshed forecast -> policy
        -> constraints and suppliers -> orders to send -> next day
```

## Building blocks

Every part is a small object with one job. Use the built-ins, or subclass the
base class and plug in your own.

| Part | What it decides | Built-ins | Base class |
|---|---|---|---|
| Policy | how much to order | order-up-to, reorder point (s,Q)/(s,S), (R,s,S), single order (newsvendor) | `BasePolicy` |
| Schedule | when ordering is allowed | periodic, one-time, explicit calendar | `DecisionSchedule` |
| Constraints | what can actually be ordered | minimum, case multiple, maximum, shelf space | `OrderingConstraint` |
| Callbacks | planned interventions, with an audit trail | order override, multiplier, hold, stock adjustment | `SimulationCallback` |
| Suppliers | who delivers, when, in how many parts | fixed or random lead times, split deliveries, shares | `SupplierAllocation`, `DeliveryOutcome` |
| Processes | physical flows besides sales | FIFO shelf life | `InventoryProcess` |
| Metrics | what success means | 37 service, stock, and cost metrics | any function |

Forecasts enter as a dated target for the protection window, so any forecasting
model and any uncertainty method works: sample paths, quantile forecasts,
cumulative intervals, or means and standard deviations.

## Why Stockcast

- **One clean interface to forecasting.** A policy needs the distribution of
  *total* demand over the window its order must cover; Stockcast asks for
  exactly that, from whatever model you use.
- **One explicit clock.** Every period is receive → decide → meet demand, so
  lead time and the protection window `H = L + R` mean the same thing in every
  experiment.
- **Every unit accounted for.** Only the engine changes stock, and every ledger
  row satisfies the stock, pipeline, and backorder balances.
- **Fair, fast, reproducible.** Comparisons share demand and random draws, the
  inner loop runs on NumPy, and every result carries a manifest of its inputs.

The reasoning behind each choice, with the literature it rests on, is in our
[Philosophy](https://filtheo.github.io/stockcast/get-started/philosophy/).

## Learn more

- [Learn the basics](https://filtheo.github.io/stockcast/learn/): nine short
  steps, from inventory state to a production daily job.
- [Guide](https://filtheo.github.io/stockcast/user-guide/): the theory and
  options of every building block, plus task recipes.
- [Examples](https://filtheo.github.io/stockcast/tutorials/): 22 runnable
  notebooks, from a first simulation to multi-supplier, perishable, and
  production workflows. Good starting points:
  [first simulation](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/02_first_engine_simulation.ipynb),
  [forecast to order](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/04_forecast_to_inventory_integration.ipynb),
  [fair comparisons](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/06_fair_forecast_and_policy_comparisons.ipynb).
- [API reference](https://filtheo.github.io/stockcast/reference/): every public
  class and function.

## Scope

Stockcast does not fit forecasting models, is not a black-box optimiser, and is
not an ERP. It executes and evaluates the decisions you configure, with every
input explicit. Version 0.1 models one stocking point with any number of SKUs
and suppliers, in discrete periods.

## Citation

If Stockcast helps your research, please cite it using the
[`CITATION.cff`](https://github.com/FilTheo/stockcast/blob/main/CITATION.cff)
file (GitHub's "Cite this repository" button).

## Contributing and support

Questions, bugs, and ideas are welcome on
[GitHub Issues](https://github.com/FilTheo/stockcast/issues). See the
[contributing guide](https://filtheo.github.io/stockcast/contributing/) for
the development setup.

## License

[Apache License 2.0](https://github.com/FilTheo/stockcast/blob/main/LICENSE).
