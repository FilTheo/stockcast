<span class="sc-step">Step 7 of 9</span>

# Evaluate a run

A run is only useful once you can say how well it went. `InventoryEvaluator`
reads the event ledger and computes metrics over the window and grouping you
choose.

??? example "Setup: the tea shop from steps 1 to 5"

    ```python
    --8<-- "learn-setup.py"
    ```

## Fit, then evaluate

The evaluator follows the same fit / apply rhythm as policies. `fit` selects
the ledger rows; `evaluate` computes the metrics:

```python
from stockcast.evaluation import (
    InventoryEvaluator, avg_on_hand, cycle_service_level, fill_rate, order_event_count,
)

evaluator = InventoryEvaluator().fit(result, window="scoring")
evaluator.evaluate(
    metrics=[fill_rate, avg_on_hand, order_event_count],
    groupby=[],           # [] = one pooled row; ["unique_id"] = one row per SKU
)
```

```text
   fill_rate  avg_on_hand  order_event_count
0        1.0    18.607143                 14
```

Two choices are always explicit:

- **`window`**: which part of the run you are describing (`"scoring"`,
  `"warmup"`, `"settlement"`, or `"all"`).
- **`groupby`**: the grain of the answer. `[]` pools everything,
  `["unique_id"]` gives one row per SKU, and any ledger column works.

## What the service metrics measure

"Service level" means different things to different people, so Stockcast gives
each meaning its own metric.

**Fill rate**: the share of demand served straight from the shelf.

$$
\text{fill rate} = \frac{\sum_t \text{fulfilled}_t}{\sum_t \text{demand}_t}
$$

**Demand-period service level**: the share of periods with demand in which
nothing was short.

$$
\frac{\#\{t : \text{demand}_t > 0,\ \text{shortage}_t = 0\}}{\#\{t : \text{demand}_t > 0\}}
$$

**Cycle service level**: the share of replenishment cycles (from one delivery
to the next) with no shortage at all. Because the first and last cycles of a
run are cut short, you choose whether to include them:

```python
evaluator.evaluate(
    metrics=[cycle_service_level],
    groupby=[],
    context={"include_partial_cycles": False},
)
```

```text
   cycle_service_level
0                  1.0
```

A target probability of 0.95 is a statement about the forecast. These metrics
measure what the whole system delivered. They are related, and they are not
the same number. Comparing them is one of the most useful things a simulation
can tell you.

## Put a price on it

Cost metrics multiply ledger quantities by rates you supply in `context`:

| Metric | Formula | Rate key(s) |
|---|---|---|
| `holding_cost` | $\sum_t h \cdot \text{on hand}_{\text{end},t}$ | `holding_cost_per_unit_period` |
| `shortage_cost` | $\sum_t p \cdot \text{shortage}_t$ | `shortage_cost_per_unit` |
| `ordering_cost` | $\sum_t (K \cdot \text{lines}_t + c_o \cdot q_t)$ | `order_cost_per_sku_line`, `order_cost_per_unit` |
| `purchase_cost` | $\sum_t c \cdot q_t$ | `purchase_cost_per_unit` |
| `total_cost` | sum of the components you list | `cost_components` + their rates |

```python
from stockcast.evaluation import holding_cost, shortage_cost, total_cost

costs = {
    "holding_cost_per_unit_period": 0.02,   # €0.02 per pack per day on the shelf
    "shortage_cost_per_unit": 2.00,         # lost margin per pack not sold
    "order_cost_per_sku_line": 5.00,        # delivery fee per order
    "order_cost_per_unit": 0.0,
    "cost_components": ["holding", "shortage", "ordering"],
}
evaluator.evaluate(
    metrics=[holding_cost, shortage_cost, total_cost],
    groupby=[],
    context=costs,
)
```

```text
   holding_cost  shortage_cost  total_cost
0         20.84            0.0       90.84
```

Every rate is yours to set, including zeros. A cost that you did not specify
is never filled in for you, so a total always means exactly the components
you listed.

## Your own metric

A metric is any function that takes the ledger slice and the context:

```python
def units_left_at_the_end(events, context=None):
    return events.sort_values("period")["ending_on_hand"].iloc[-1]

evaluator.evaluate(metrics=[fill_rate, units_left_at_the_end], groupby=["unique_id"])
```

```text
  unique_id  fill_rate  units_left_at_the_end
0  tea_250g        1.0                   25.0
```

!!! summary "Recap"

    - `InventoryEvaluator().fit(result, window=...).evaluate(metrics, groupby, context)`.
    - Fill rate, demand-period service level, and cycle service level answer
      three different questions.
    - Costs use rates you supply; custom metrics are plain functions.

**Go deeper:** [Evaluation and metrics](../user-guide/metrics.md) ·
[Put costs on a run](../how-to/costs.md) ·
[Notebook 06](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb)

[Next: Compare scenarios :octicons-arrow-right-24:](08-compare.md){ .md-button }
