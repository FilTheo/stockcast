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

```python
import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.evaluation import InventoryEvaluator, avg_on_hand, fill_rate
from stockcast.policies import OrderUpToPolicy
from stockcast.utils import DemandGenerator


# Weekly sales of two products over 20 weeks: Poisson demand, 20 units a week.
def poisson(rng, periods):
    return rng.poisson(20, periods.size)


generator = DemandGenerator(
    ["coffee", "tea"], start_date="2026-01-05", freq="W-MON", random_seed=0,
)
sales = generator.sample(20, poisson)
sales.head(3)
#   unique_id     y  period       date
# 0    coffee  22.0       0 2026-01-05
# 1       tea  24.0       0 2026-01-05
# 2    coffee   9.0       1 2026-01-12

# Today is week 12: we know the past, the future is still to come.
today = pd.Timestamp("2026-03-23")
past = sales[sales["date"] <= today]
future = sales[sales["date"] > today]

# Forecast: the 95% quantile of next week's demand, for each product.
# Here from the last 12 weeks; any quantile forecasting model works.
forecast = past.groupby("unique_id", as_index=False)["y"].quantile(0.95)
forecast["date"] = today + pd.Timedelta(weeks=1)   # the week it forecasts

# The policy: order every Monday, delivered the same morning, so each order
# covers one week. It orders up to the forecast.
policy = OrderUpToPolicy(
    lead_time=0, review_period=1, freq="W-MON", service_level=0.95, allow_backorders=False,
)
policy.fit(forecast, target_column="y")

# The shelf today: 30 units of each product.
stock = pd.DataFrame({"unique_id": ["coffee", "tea"], "date": today, "on_hand": [30, 30]})
shelf = InventoryStateDataFrame.from_observed(stock)

# Simulate the next 8 weeks and score the decisions.
engine = SimulationEngine()
result = engine.run(policy, future, shelf)

evaluator = InventoryEvaluator()
evaluator.fit(result)
print(evaluator.evaluate([fill_rate, avg_on_hand]).round(2))
```

```text
   fill_rate  avg_on_hand
0       0.99         5.71
```

With a lead time, the forecast covers total demand over the lead time plus the
review period instead; the
[Quickstart](https://filtheo.github.io/stockcast/get-started/quickstart/)
shows how, step by step. `result.to_event_frame()` holds the full record: one
balanced row per SKU and period with every receipt, order, sale, and shortage.

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
