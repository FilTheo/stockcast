---
hide:
  - navigation
  - toc
---

<div class="sc-hero" markdown>

![Stockcast logo](assets/logo.png)

# Stockcast

<p class="sc-tagline">Turn forecasts into inventory decisions you can simulate, inspect, and evaluate.</p>

[Get started](get-started/quickstart.md){ .md-button .md-button--primary }
[Learn the basics](learn/index.md){ .md-button }
[Our philosophy](get-started/philosophy.md){ .md-button }

</div>

Stockcast is a Python library for the step that comes **after** forecasting.
You bring a forecast from any model you like. Stockcast maps it into orders,
simulates their execution against historical or simulated demand, and
evaluates the resulting impact against business metrics such as cost, service,
and waste. And when you are happy with a policy, the same objects
compute your real orders every day.

It is built like PyTorch and assembled like Lego: a handful of bricks that
snap onto one engine. Swap the policy, the demand, the supplier, or the shelf-life rule, and
everything else stays exactly the same. The reasoning behind every design
choice, and the literature it rests on, is in our
[philosophy](get-started/philosophy.md).

## Find your way

The docs have four parts. Pick the one that matches what you want right now:

<div class="grid cards sc-grid-2" markdown>

-   :material-rocket-launch-outline:{ .lg .middle } **Get started**

    ---

    *"I'm new."* Install, run the ten-minute
    [Quickstart](get-started/quickstart.md), then follow
    [Learn the basics](learn/index.md): nine short steps from the first
    simulation to a production daily job.

    [:octicons-arrow-right-24: Start here](get-started/quickstart.md)

-   :material-book-open-variant:{ .lg .middle } **Guide**

    ---

    *"How does X work?"* or *"How do I do Y?"* The theory and options of every
    building block (timing, targets, policies, suppliers, shelf life,
    metrics), plus short **recipes** for everyday jobs.

    [:octicons-arrow-right-24: Open the guide](user-guide/index.md)

-   :material-notebook-outline:{ .lg .middle } **Examples**

    ---

    *"Show me a complete project."* Twenty-three runnable notebooks with real
    forecasts, multiple suppliers, perishable stock, and a production
    workflow.

    [:octicons-arrow-right-24: Browse examples](tutorials/index.md)

-   :material-api:{ .lg .middle } **API reference**

    ---

    *"What are the arguments of this function?"* Every public class and
    function, with parameters, return values, and examples.

    [:octicons-arrow-right-24: Look it up](reference/index.md)

</div>

**Suggested paths**

- **Researchers:** [Quickstart](get-started/quickstart.md) → [Learn steps 1–8](learn/index.md) →
  [Compare forecasts and policies](how-to/compare-policies.md) →
  [experiment examples](tutorials/index.md#experiments-and-operations)
- **Engineers:** [Quickstart](get-started/quickstart.md) → [Learn steps 1–9](learn/index.md) →
  [Use Stockcast in a daily job](how-to/production.md) →
  [Example 10: production daily close](notebooks/10_production_daily_close.ipynb)

## Two pipelines

**Research: backtest and compare.**

```mermaid
flowchart LR
    F["Your forecast<br/>(any library)"] --> T["Forecast target"]
    T --> P["Policy<br/>fit · predict"]
    S["Inventory state"] --> E
    D["Demand"] --> E
    P --> E["SimulationEngine"]
    E --> L["Event ledger"]
    L --> M["Inventory evaluation"]
```

**Production: run the chosen policy every day.**

```mermaid
flowchart LR
    N["Incoming sales<br/>and deliveries"] --> U["Update the state"]
    U --> R["Refresh the forecast<br/>and targets"]
    R --> P["Policy<br/>predict"]
    P --> C["Constraints<br/>and suppliers"]
    C --> O["Orders to send"]
    O -->|next day| N
```

## For researchers and for engineers

<div class="grid cards" markdown>

-   :material-flask-outline:{ .lg .middle } **Researchers and analysts**

    ---

    Measure forecasts by the decisions they lead to. Compare policies,
    forecasting models, lead times, suppliers, and shelf-life rules on
    identical demand, with every unit accounted for and every run
    reproducible.

    [:octicons-arrow-right-24: Compare scenarios](learn/08-compare.md)

-   :material-cog-outline:{ .lg .middle } **Engineers in production**

    ---

    Put the chosen policy behind a daily job: load stock, refresh the
    forecast, compute orders with the supplier's rules, and save the state.
    The daily job gives exactly the results of its backtest.

    [:octicons-arrow-right-24: From backtest to production](learn/09-production.md)

</div>

## Stockcast in one screen

```python
import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.evaluation import InventoryEvaluator, avg_on_hand, fill_rate
from stockcast.policies import OrderUpToPolicy
from stockcast.utils import DemandGenerator

sku, today = "tea_250g", pd.Timestamp("2026-02-01")

# Twelve weeks of daily tea sales, about six packs a day.
sales = DemandGenerator([sku], first_date="2026-01-05", freq="D", random_seed=3).sample(
    84, lambda rng, periods: rng.poisson(6, periods.size))
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

# 30 packs on the shelf today. Order every morning, delivered before opening,
# up to the 95% quantile of tomorrow's demand.
shelf = InventoryStateDataFrame.from_observed(
    pd.DataFrame({"unique_id": [sku], "date": [today], "on_hand": [30]}))
policy = OrderUpToPolicy(lead_time=0, review_period=1, freq="D",
                         service_level=0.95, allow_backorders=False)
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

The [Quickstart](get-started/quickstart.md) walks through each of these lines.

## What makes Stockcast different

<div class="grid" markdown>

!!! abstract "Forecasts become decisions"

    Forecast accuracy is one number. What a business feels is the stock on the
    shelf, the sales it missed, and the money tied up in inventory. Stockcast
    measures forecasts by the decisions they lead to.

!!! abstract "Composable, like PyTorch"

    Policies, schedules, constraints, suppliers, callbacks, and physical
    processes are separate objects with one job each. Combine the built-in ones
    or subclass a base class to add your own.

!!! abstract "Every unit is accounted for"

    Each simulated day produces a row in an event ledger. The engine checks
    that stock, backorders, and pipeline balance on every row, so your
    results rest on consistent accounting.

!!! abstract "Timing you can read"

    Receive, decide, then meet demand. The order of events is written down,
    drawn, and tested, so a lead time means the same thing in every experiment.
    Other conventions map onto it exactly:
    [work with any timing convention](how-to/timing-conventions.md).

</div>

## Works with your forecasting stack

Stockcast does not fit forecasting models. It accepts what any forecasting
library produces: quantiles of cumulative demand, mean and standard deviation,
or sample paths. The examples use [smooth](https://openforecast.org/smooth-py/),
from the same open-source ecosystem, and the same steps apply to statsforecast,
skforecast, Prophet, scikit-learn, deep learning models, or your own code. See
[Connect any forecasting model](how-to/connect-a-forecaster.md).
