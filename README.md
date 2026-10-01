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
  <img src="https://img.shields.io/badge/Maintained%20by-PADS%20%C2%B7%20VIVES-red" alt="Maintained by PADS, VIVES">
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

Inspired by PyTorch-style libraries, it is built like Lego: policies,
schedules, constraints, callbacks, suppliers, physical processes, and metrics
are small parts that snap onto one engine with explicit timing and checked
accounting. Each part is a class you can subclass and adjust.

Stockcast is developed and maintained by
[Filotas Theodosiou](https://filtheo.github.io/) at the Predictive AI and
Digital Shift (PADS) research group, VIVES University of Applied Sciences.

## Install

```bash
pip install stockcast
```

Stockcast needs Python 3.10+ and only NumPy, pandas, and Matplotlib.

## Quickstart

A tea shop sells about six packs a day and orders every morning; deliveries
arrive before it opens. Forecast tomorrow's demand, turn the forecast into
orders, and simulate eight weeks:

```python
import numpy as np
import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.evaluation import InventoryEvaluator, avg_on_hand, fill_rate
from stockcast.policies import OrderUpToPolicy

sku, today = "tea_250g", pd.Timestamp("2026-02-01")

# Twelve weeks of daily tea sales, about six packs a day.
days = pd.date_range("2026-01-05", periods=84, freq="D")
sales = pd.DataFrame({
    "unique_id": sku,
    "date": days,
    "y": np.random.default_rng(3).poisson(6, len(days)),
})
past, future = sales[sales["date"] <= today], sales[sales["date"] > today]

# Forecast tomorrow from the last four weeks. Any model works; here, two
# common ways to state the uncertainty.
last_4_weeks = past["y"].tail(28)
forecast = pd.DataFrame({
    "unique_id": [sku],
    "date": [today + pd.Timedelta(days=1)],   # the day it forecasts
    "fh": [1],                                # one step ahead
    "mean": [last_4_weeks.mean()],            # a moving average ...
    "std": [last_4_weeks.std()],              # ... and its spread
    "q95": [last_4_weeks.quantile(0.95)],     # or a 95% quantile forecast
})

# Today's stock and the ordering policy.
shelf = InventoryStateDataFrame.from_observed(pd.DataFrame({
    "unique_id": [sku],
    "date": [today],     # the day the shelf was counted
    "on_hand": [30],     # packs on the shelf
}))
policy = OrderUpToPolicy(
    lead_time=0,             # delivered before the shop opens
    review_period=1,         # order every morning
    freq="D",                # one period is one day
    service_level=0.95,      # cover tomorrow's demand on 95% of days
    allow_backorders=False,  # a missed sale is lost
)

engine = SimulationEngine()

# From a mean and a spread, the policy computes the quantile...
policy.fit(forecast, mean_column="mean", std_column="std")
from_mean_std = engine.run(policy=policy, demand_source=future, inventory=shelf)

# ...or it takes a quantile forecast as it is.
policy.fit(forecast, target_column="q95")
from_quantile = engine.run(policy=policy, demand_source=future, inventory=shelf)

# Score the next eight weeks of each.
metrics = [fill_rate, avg_on_hand]
pd.concat({
    "mean + std": InventoryEvaluator().fit(from_mean_std).evaluate(metrics),
    "quantile": InventoryEvaluator().fit(from_quantile).evaluate(metrics),
}).droplevel(1).round(2)
```

```text
            fill_rate  avg_on_hand
mean + std       0.99         5.30
quantile         0.99         5.69
```

The same policy takes a mean and a spread or a quantile forecast, from any
model. `from_mean_std.to_event_frame()` holds the full record: one balanced
row per SKU and day with every receipt, order, sale, and shortage. With a lead
time, an order must cover the lead time plus the review period;
[Learn step 3](https://filtheo.github.io/stockcast/learn/03-forecast-targets/)
shows how.

## Research and production

The same objects run a backtest and a daily job, so the policy you evaluated is
the policy you run:

![How Stockcast works: your data and choices go in, Stockcast plays out each day, you get service, stock and cost](https://raw.githubusercontent.com/FilTheo/stockcast/main/docs/assets/diagrams/how-it-works.svg)

**Research: compare candidates on identical demand.**

![Candidates A and B run on the same demand and starting stock, then you compare the results](https://raw.githubusercontent.com/FilTheo/stockcast/main/docs/assets/diagrams/compare.svg)

**Production: run the chosen policy every day.**

![The daily loop: sales and deliveries, update the stock, fresh forecast, your policy, orders to send](https://raw.githubusercontent.com/FilTheo/stockcast/main/docs/assets/diagrams/production-loop.svg)

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
| Metrics | what success means | 39 service, stock, and cost metrics | any function |

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
- **Every unit accounted for.** Only the engine changes stock, and every event table
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
- [Examples](https://filtheo.github.io/stockcast/tutorials/): 23 runnable
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

## About

Stockcast is maintained by our team at the Predictive AI and Digital Shift
(PADS) research group, VIVES University of Applied Sciences.
[Read more](https://filtheo.github.io/).

## License

[Apache License 2.0](https://github.com/FilTheo/stockcast/blob/main/LICENSE).
