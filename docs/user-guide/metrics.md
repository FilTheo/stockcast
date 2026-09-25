# Evaluation and metrics

Metrics turn a run's ledger into answers: how much demand was served, how
much stock was held, what it cost. Every metric in Stockcast is a small,
documented function of the event ledger, so you always know exactly what a
number means.

## The evaluator

??? example "Setup: the tea shop from Learn the basics"

    ```python
    --8<-- "learn-setup.py"
    ```

```python
from stockcast.evaluation import (
    InventoryEvaluator, avg_on_hand, cycle_service_level, demand_period_service_level,
    fill_rate, inventory_turns, order_event_count,
)

evaluator = InventoryEvaluator().fit(result, window="scoring")
evaluator.evaluate(
    metrics=[fill_rate, demand_period_service_level, cycle_service_level,
             avg_on_hand, inventory_turns, order_event_count],
    groupby=["unique_id"],
    context={"include_partial_cycles": False, "periods_per_year": 365},
).round(3)
```

```text
  unique_id  fill_rate  demand_period_service_level  cycle_service_level  avg_on_hand  inventory_turns  order_event_count
0  tea_250g        1.0                          1.0                  1.0       18.607          118.397                 14
```

| Step | Choice |
|---|---|
| `fit(result, window=...)` | Which window: `"scoring"`, `"warmup"`, `"settlement"`, or `"all"`. You can also `fit(event_frame=...)` any saved ledger. |
| `evaluate(metrics, groupby, context)` | Which metrics; the grain (`[]` for one pooled row, or any ledger columns); and the rates and options the metrics need. |

The evaluator validates the ledger when you fit it, so every metric is
computed from balanced books.

## Notation

For a slice of the ledger (a window, a group), sums run over its SKU-period
rows $(i, t)$. $D$ is demand, $F$ fulfilled units, $U$ shortage units, $r$
receipts, $q$ order quantity, $\mathit{OH}$, $P$, $B$ ending on-hand,
pipeline, and backorders.

## Service

| Metric | Definition |
|---|---|
| `fill_rate` | $\dfrac{\sum F}{\sum D}$: share of demand served from stock in its own period (1 if there is no demand) |
| `demand_period_service_level` | share of rows with $D > 0$ and $U = 0$ |
| `cycle_service_level` | share of replenishment cycles with no shortage. A cycle runs from one receipt to the next. Requires `context["include_partial_cycles"]` to say whether the first and last, incomplete, cycles count |
| `sku_period_stockout_rate` | share of SKU-period rows with a shortage |
| `stockout_period_rate` | share of periods in which **any** SKU in the slice was short |
| `backorder_period_rate` | share of periods in which any SKU ended with backorders |

With backorders, fill rate counts only demand served in its own period;
backorders served later are recorded separately in `backorders_fulfilled`.

## Quantities

| Metric | Definition |
|---|---|
| `demand_units`, `fulfilled_units`, `shortage_units`, `lost_sales_units` | $\sum D$, $\sum F$, $\sum U$, and lost sales |
| `order_units` | $\sum q$ |
| `backlog_unit_periods` | $\sum B$: backorder exposure, units × periods |
| `terminal_backlog_units`, `terminal_pipeline_units` | $B$ and $P$ in each SKU's last row |
| `order_event_count` | decisions with a positive order (counted once for the whole portfolio) |
| `sku_order_line_count` | positive SKU order lines (supplier lines with a supply model) |
| `sku_order_quantity_variance` | variance of positive order-line sizes |
| `capacity_violation_count`, `capacity_violation_rate` | order lines cut by a capacity constraint; as a count and as a share of positive requested lines |

## Stock

| Metric | Definition |
|---|---|
| `avg_on_hand`, `avg_on_order`, `avg_inventory_position` | mean of $\mathit{OH}$, $P$, $\mathit{IP}$ over SKU-period rows |
| `peak_ending_on_hand` | $\max_t \sum_i \mathit{OH}_{i,t}$: the largest total stock in any period |
| `ending_on_hand_variance` | variance over periods of $\sum_i \mathit{OH}_{i,t}$ |
| `inventory_turns` | $\dfrac{\sum F / n \times \text{periods per year}}{\text{mean}_t \sum_i \mathit{OH}_{i,t}}$; needs `context["periods_per_year"]` |
| `CoverageMetric("forward")` | mean of $\mathit{OH} / \text{expected demand rate}$, from an `expected_demand_rate` column or `context["forward_demand_rate"]` |
| `CoverageMetric("trailing")` | mean of $\mathit{OH} / \text{average realised demand}$ per SKU |

Two grains appear here on purpose. `avg_on_hand` is a mean over SKU-period
rows ("a typical SKU holds…"); `peak_ending_on_hand` and
`ending_on_hand_variance` look at the **portfolio total** per period ("the
warehouse holds…"). For more than one SKU, `CoverageMetric` asks you to confirm
the row-average grain with
`context["coverage_aggregation"] = "mean_of_sku_period_ratios"`.

## Costs

Cost metrics multiply ledger quantities by rates. A rate can be a number in
`context` or a column of the ledger (for per-SKU or per-period rates).

| Metric | Formula | Rate keys |
|---|---|---|
| `holding_cost` | $\sum h \cdot \mathit{OH}$ | `holding_cost_per_unit_period` |
| `shortage_cost` | $\sum p \cdot U$ | `shortage_cost_per_unit` |
| `backlog_cost` | $\sum b \cdot B$ | `backlog_cost_per_unit_period` |
| `ordering_cost` | $\sum (K \cdot \text{lines} + c_o \cdot q)$ | `order_cost_per_sku_line`, `order_cost_per_unit` |
| `purchase_cost` | $\sum c \cdot q$ | `purchase_cost_per_unit` |
| `waste_cost` | $\sum w \cdot \text{expired}$ | `waste_cost_per_unit` |
| `terminal_backlog_cost`, `terminal_pipeline_cost` | rate × final $B$ or $P$ per SKU | `terminal_backlog_cost_per_unit`, `terminal_pipeline_cost_per_unit` |
| `salvage_credit` | final $\mathit{OH}$ and $P$ × salvage rates | `on_hand_salvage_per_unit`, `pipeline_salvage_per_unit` |
| `total_cost` | sum of the components in `cost_components` (salvage subtracted) | `cost_components` + each component's rates |
| `cost_per_demand_unit`, `cost_per_fulfilled_unit` | `total_cost` $/ \sum D$ or $/ \sum F$ | as for `total_cost` |

`cost_components` names the parts of `total_cost`: any of `"holding"`,
`"shortage"`, `"backlog"`, `"ordering"`, `"purchase"`, `"waste"`,
`"terminal_backlog"`, `"terminal_pipeline"`, `"salvage"`. Each listed
component needs all of its rates, zeros included, so a total always means
exactly what you listed. Purchases are charged when ordered. Choose the window
and the terminal components that match your question; see
[Put costs on a run](../how-to/costs.md).

## Your own metrics

Any function `metric(events, context) -> float` is a metric; its name becomes
the column name. For a named, configurable metric, subclass
`BaseInventoryMetric`:

```python
from stockcast.evaluation import BaseInventoryMetric


class ShareOfDaysBelow(BaseInventoryMetric):
    """Share of periods ending with less than `level` units on the shelf."""

    def __init__(self, level):
        self.level = level
        self.name = f"days_below_{level}"

    def compute(self, event_frame, context):
        rows = event_frame[event_frame["event_type"] == "period"]
        return float((rows["ending_on_hand"] < self.level).mean())


evaluator.evaluate([fill_rate, ShareOfDaysBelow(10)], groupby=[])
```

```text
   fill_rate  days_below_10
0        1.0          0.125
```

**Go deeper:** [Learn step 7](../learn/07-evaluate.md) ·
[API: metrics](../reference/metrics.md) ·
[Notebook 06](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb)
