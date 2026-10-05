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

A forecast is not a decision. Its real value depends on the downstream choices
it improves, and ultimately on the operational performance those choices
deliver.

Stockcast brings this idea to inventory management: it is the layer between
the forecast and the replenishment decision. It maps forecasts from **any
model** into orders, simulates their execution against historical or simulated
demand, and evaluates the resulting impact against business metrics such as
cost, service, and waste. Happy with a policy? Put it into production with the
same objects.

Inspired by PyTorch-style libraries, it is built from Lego-like pieces that
snap onto one engine. Each piece is a class you can subclass and adjust.

Stockcast is developed and maintained by
[Filotas Theodosiou](https://filtheo.github.io/) at the Predictive AI and
Digital Shift (PADS) research group, VIVES University of Applied Sciences.

## Install

```bash
pip install stockcast
```

Stockcast needs Python 3.10+ and only NumPy, pandas, and Matplotlib.

## Quickstart

A tea shop sells about six packs a day and orders every morning. Turn its
forecast into orders and play out eight weeks:

```python
import numpy as np
import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.evaluation import InventoryEvaluator, avg_on_hand, fill_rate
from stockcast.policies import OrderUpToPolicy

# Eight weeks of daily tea sales.
days = pd.date_range("2026-02-02", periods=56, freq="D")
demand = pd.DataFrame({
    "unique_id": "tea",
    "date": days,
    "y": np.random.default_rng(3).poisson(6, len(days)),
})

# The forecast, from any model: a day's demand is 10 packs or fewer, with
# 95% probability.
forecast = pd.DataFrame({
    "unique_id": ["tea"],
    "date": [days[0]],   # the day it forecasts
    "q95": [10],         # the 95% quantile
})

# The shelf today.
shelf = InventoryStateDataFrame.from_observed(pd.DataFrame({
    "unique_id": ["tea"],
    "date": [pd.Timestamp("2026-02-01")],
    "on_hand": [30],
}))

policy = OrderUpToPolicy(
    lead_time=0,             # delivered before the shop opens
    review_period=1,         # order every morning
    freq="D",                # one period is one day
    service_level=0.95,      # the probability the forecast quantile stands for
    allow_backorders=False,  # a missed sale is lost
)
policy.fit(forecast, target_column="q95")

result = SimulationEngine().run(policy=policy, demand_source=demand, inventory=shelf)
InventoryEvaluator().fit(result).evaluate([fill_rate, avg_on_hand]).round(2)
```

```text
   fill_rate  avg_on_hand
0       0.99         4.75
```

`result.to_event_frame()` holds the full record: one checked row per SKU and
day, with every receipt, order, sale, and shortage. The
[Quickstart](https://filtheo.github.io/stockcast/get-started/quickstart/)
builds the forecast from past sales, and
[Walkthrough step 3](https://filtheo.github.io/stockcast/learn/03-forecast-targets/)
shows orders that must cover a lead time.

## Core principles

Stockcast is built on seven principles. Our
[Philosophy](https://filtheo.github.io/stockcast/get-started/philosophy/)
explains each one, with the research behind it.

1. **The interface between forecasting and inventory is the target.** An
   order needs the distribution of total demand over the window it covers.
   Stockcast asks for exactly that, from any model, and adds no hidden safety
   stock.
2. **One explicit clock.** Every period runs in the same order: receive,
   decide, then meet demand. So lead time and the window `H = L + R` mean the
   same thing in every experiment.
3. **Accounting before optimisation.** Only the engine changes the stock, and
   every row of the event table adds up. A missing input stops the run before
   the first period.
4. **Small parts, one base class each.** Policies, schedules, constraints,
   suppliers, callbacks, and physical processes each have one base class. The
   built-in parts use it, and so can yours.
5. **Easy, scalable evaluation.** A comparison is one call: every option runs
   on the same demand, opening stock, and random draws. Every result carries a
   manifest, so anyone can rerun it.
6. **Fast inside, readable outside.** DataFrames in and out, NumPy arrays
   inside. A year of daily order-up-to decisions for 1,000 SKUs takes about 10
   to 12 seconds on a laptop.
7. **From research to production.** The parts you backtest are the parts that
   place real orders, run each period by your scheduler or event stream.

## Research and production

**Research: backtest and compare.** For researchers and analysts who measure
forecasts by the decisions they lead to. Compare policies, forecasting models,
lead times, suppliers, and shelf-life rules on identical demand.
[Compare scenarios](https://filtheo.github.io/stockcast/learn/08-compare/).

![Candidates A and B run on the same demand and starting stock, then you compare the results](https://raw.githubusercontent.com/FilTheo/stockcast/main/docs/assets/diagrams/compare.svg)

**Production: run the chosen policy every period.** For engineers who put the
chosen policy behind a periodic job: load stock, refresh the forecast, compute
orders with the supplier's rules, and save the state.
[From backtest to production](https://filtheo.github.io/stockcast/learn/09-production/).

![The production loop: sales and deliveries update the stock, a fresh forecast and your policy give the orders to send](https://raw.githubusercontent.com/FilTheo/stockcast/main/docs/assets/diagrams/production-loop.svg)

Stockcast does not forecast, and it does not search for the best policy for
you. It gives you the building blocks to do both: any forecasting model can
feed it, and any optimiser can wrap it. Version 0.1 models one stocking point
with any number of SKUs and suppliers, in discrete periods.

## Learn more

- [Walkthrough](https://filtheo.github.io/stockcast/learn/): nine short
  steps, from inventory state to a production job.
- [Guide](https://filtheo.github.io/stockcast/user-guide/): the theory and
  options of every building block, plus task recipes.
- [Examples](https://filtheo.github.io/stockcast/tutorials/): 24 runnable
  notebooks, from a first simulation to multi-supplier, perishable, and
  production workflows. Good starting points:
  [first simulation](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/02_first_engine_simulation.ipynb),
  [forecast to order](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/04_forecast_to_inventory_integration.ipynb),
  [fair comparisons](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/06_fair_forecast_and_policy_comparisons.ipynb).
- [API reference](https://filtheo.github.io/stockcast/reference/): every public
  class and function.
- [Release notes](https://filtheo.github.io/stockcast/release-notes/): what
  changed in each version.

## Citation

If you use Stockcast in your work, please cite the version you used. The
[`CITATION.cff`](https://github.com/FilTheo/stockcast/blob/main/CITATION.cff)
file has the details, and GitHub's *Cite this repository* button formats them:

```text
Theodosiou, F. Stockcast: forecast-driven inventory decisions, simulation, and
evaluation in Python. Version 0.1.0. https://github.com/FilTheo/stockcast
```

## Contributing and support

Questions, bugs, and ideas are welcome on
[GitHub Issues](https://github.com/FilTheo/stockcast/issues). For a bug,
include a small example and the run manifest (`result.run_manifest`), which
records the versions and settings behind a result. See the
[contributing guide](https://filtheo.github.io/stockcast/contributing/) for
the development setup.

## About

Stockcast is maintained by our team at the Predictive AI and Digital Shift
(PADS) research group, VIVES University of Applied Sciences.
[Read more](https://filtheo.github.io/).

## License

[Apache License 2.0](https://github.com/FilTheo/stockcast/blob/main/LICENSE).
