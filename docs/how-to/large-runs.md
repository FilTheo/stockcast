# Run large panels

A run keeps one event table row and one history row per SKU and period, so
its memory grows with SKUs × periods. When your SKUs do not interact, you can
run them in chunks: each chunk is an ordinary run, and together the chunks
give exactly the results of one big run. This recipe shows when that holds
and how to check it.

## When chunks give the same results

Each SKU's books depend only on its own demand, stock, targets and orders, as
long as nothing in the run looks across SKUs. Chunking by SKU is exact when the
run has:

- no shared capacity: the built-in constraints (`MinimumOrderQuantity`,
  `OrderMultiple`, `MaximumOrderQuantity`, `ShelfSpaceLimit`) act on each SKU
  alone, but a custom constraint that caps the total order of all SKUs does
  not;
- no callbacks, since a callback sees the whole state;
- no random supplier lead times or delivery outcomes: their random draws are
  made for all of a run's SKUs together, so a chunk would get other draws;
- no supplier allocation or inventory process that reads other SKUs.

Give every chunk the same settings: the same calendar, policy configuration
and windows. A constraint with one value per SKU takes the values of the
chunk's SKUs only.

## Run each chunk

Here are six products with a new target at every daily decision, the shape
of a rolling backtest:

```python
import numpy as np
import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.evaluation import (
    InventoryEvaluator, avg_on_hand, fill_rate, order_event_count, order_variance,
    sku_order_line_count,
)
from stockcast.policies import OrderUpToPolicy, rolling_schedule

opening = pd.Timestamp("2026-01-05")
products = [f"product_{i}" for i in range(1, 7)]
n, lead_time, review_period = 56, 2, 1

rng = np.random.default_rng(3)
dates = pd.date_range(opening + pd.Timedelta(days=1), periods=n, freq="D")
demand = pd.DataFrame({
    "unique_id": np.repeat(products, n),
    "date": np.tile(dates, len(products)),
    "y": rng.poisson(4, len(products) * n).astype(float),
})
stock = pd.DataFrame({"unique_id": products, "date": opening, "on_hand": 12.0})

# One target per product and daily decision: a stand-in for your forecasts.
cutoffs = opening + pd.to_timedelta(np.arange(n), unit="D")
targets = pd.DataFrame({
    "unique_id": np.tile(products, n),
    "cutoff": np.repeat(cutoffs, len(products)),
    "q_90": rng.poisson(4 * (lead_time + review_period), len(products) * n) + 4.0,
})
targets["date"] = targets["cutoff"] + pd.Timedelta(days=lead_time + review_period)
```

One function runs any group of products. Everything it uses is filtered to
the chunk's products, and every setting is the same for each call:

```python
def run(chunk):
    inventory = InventoryStateDataFrame.from_observed(stock[stock["unique_id"].isin(chunk)])
    schedule = rolling_schedule(
        OrderUpToPolicy(lead_time=lead_time, review_period=review_period, freq="D",
                        service_level=0.9, allow_backorders=False),
        targets[targets["unique_id"].isin(chunk)], inventory,
        target_column="q_90",
    )
    return SimulationEngine().run(
        policy=schedule[min(schedule)], policy_schedule=schedule,
        demand_source=demand[demand["unique_id"].isin(chunk)], inventory=inventory,
        warmup_periods=7, allow_target_reuse=False,
    )


whole = run(products)
chunks = [products[:3], products[3:]]
events = pd.concat([run(chunk).to_event_frame() for chunk in chunks], ignore_index=True)
```

In a real study, save each chunk's event table and manifest as soon as its
run ends, so only one chunk is in memory at a time.

## Check the results

The stacked event table is an ordinary event table: fit the evaluator on it
as on a single run. Per product and pooled, the chunked metrics equal the
one-run metrics exactly:

```python
metrics = [fill_rate, avg_on_hand, order_variance, sku_order_line_count]
by_product = InventoryEvaluator().fit(whole).evaluate(metrics, groupby="unique_id")
by_product_chunked = InventoryEvaluator().fit(event_frame=events).evaluate(
    metrics, groupby="unique_id",
)
pd.testing.assert_frame_equal(by_product, by_product_chunked, check_exact=True)

pooled = InventoryEvaluator().fit(whole).evaluate(metrics)
pooled_chunked = InventoryEvaluator().fit(event_frame=events).evaluate(metrics)
pd.testing.assert_frame_equal(pooled, pooled_chunked, check_exact=True)
print(pooled_chunked.round(3))
```

```text
   fill_rate  avg_on_hand  order_variance  sku_order_line_count
0      0.938        5.786          15.777                   198
```

You can also evaluate each chunk on its own and combine the results, as long
as the combination matches the metric: add sums, and weight averages by the
rows or demand behind them.

## Order fees per decision

One count does not add up across chunks. `order_event_count` counts each
decision that ordered anything once for the whole portfolio, for a fee per
purchase order. Split the portfolio and each chunk counts its own decisions:

```python
print(order_event_count(whole.to_event_frame()), order_event_count(events))
```

```text
56 106
```

The one run ordered on all 56 days. The first chunk ordered on 51 of them
and the second on 55, so their counts add up to 106. For a fee per decision
across the whole portfolio, count the days on which any chunk ordered:

```python
print(events.loc[events["order_quantity"] > 0, "period"].nunique())
```

```text
56
```

For a fee per order line, use `sku_order_line_count`, which adds up across
chunks.
