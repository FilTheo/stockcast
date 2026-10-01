# Evaluation and metrics

Metrics summarise a run's event table: how much demand was served, how much stock
was held, and what it cost. Each metric is a documented function of the event
event table.

## The evaluator

??? example "Setup: the tea shop from the Walkthrough"

    ```python
    --8<-- "learn-setup.py"
    ```

```python
from stockcast.evaluation import (
    InventoryEvaluator, avg_on_hand, cycle_service_level, demand_period_service_level,
    fill_rate, inventory_turns, order_event_count,
)

evaluator = InventoryEvaluator().fit(result)
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
| `fit(result, window="scoring")` | Which window: `"scoring"` (default), `"warmup"`, `"settlement"`, or `"all"`. You can also `fit(event_frame=...)` any saved event table, with the same windows and default. |
| `evaluate(metrics, groupby=None, context=None)` | Which metrics; the grain (one pooled row by default, or any event table columns); and the rates and options the metrics need. |

The evaluator validates the event table when you fit it, so every metric is
computed from balanced books.

## Notation

For a slice of the event table (a window, a group), sums run over its SKU-period
rows $(i, t)$. $D$ is demand, $F$ fulfilled units, $U$ shortage units, $r$
receipts, $q$ order quantity, $\mathit{OH}$, $P$, $B$ ending on-hand,
pipeline, and backorders.

## Service

| Metric | Definition |
|---|---|
| `fill_rate` | $\dfrac{\sum F}{\sum D}$: share of demand served from stock in its own period (1 if there is no demand) |
| `demand_period_service_level` | share of rows with $D > 0$ and $U = 0$ |
| `cycle_service_level` | share of replenishment cycles with no shortage. A cycle runs from the arrival of one order to the arrival of the next (rows with `order_arrival_flag`); an order split across suppliers or delivered in parts starts one cycle. An event table without that column (for example one you built yourself) uses every receipt. Requires `context["include_partial_cycles"]` to say whether the first and last, incomplete, cycles count |
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
| `order_event_count` | decisions with a positive order, counted once per decision for the whole portfolio (for a fee per decision; to count orders per SKU, use `sku_order_line_count`) |
| `sku_order_line_count` | positive SKU order lines (supplier lines with a supply model) |
| `sku_order_quantity_variance` | variance of positive order-line sizes |
| `capacity_violation_count`, `capacity_violation_rate` | order lines cut by a capacity constraint; as a count and as a share of positive requested lines |

## Stock

| Metric | Definition |
|---|---|
| `avg_on_hand`, `avg_on_order`, `avg_inventory_position` | mean of $\mathit{OH}$, $P$, $\mathit{IP}$ over SKU-period rows |
| `peak_ending_on_hand` | $\max_t \sum_i \mathit{OH}_{i,t}$: the largest total stock in any period |
| `ending_on_hand_variance` | variance over periods of $\sum_i \mathit{OH}_{i,t}$ |
| `inventory_turns` | $\dfrac{\sum (F + \text{backorders served}) / n \times \text{periods per year}}{\text{mean}_t \sum_i \mathit{OH}_{i,t}}$: units shipped, including backorders served late. Periods per year are read from the dates (daily 365, weekly 52, monthly 12, quarterly 4, yearly 1); for other period lengths pass `context["periods_per_year"]` |
| `CoverageMetric("forward")` | mean of $\mathit{OH} / \text{expected demand rate}$, from an `expected_demand_rate` column or `context["forward_demand_rate"]` |
| `CoverageMetric("trailing")` | mean of $\mathit{OH} / \text{average realised demand}$ per SKU |

The stock metrics use two grains. `avg_on_hand` is a mean over SKU-period
rows ("a typical SKU holds…"); `peak_ending_on_hand` and
`ending_on_hand_variance` look at the **portfolio total** per period ("the
warehouse holds…"). For more than one SKU, `CoverageMetric` is a mean over
SKU-period ratios, like `avg_on_hand`.

## Costs

Cost metrics multiply event table quantities by rates. A rate can be a number in
`context` or a column of the event table (for per-SKU or per-period rates).

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
| `TotalCost(holding=..., shortage=..., ...)` | sum of the components whose rates you pass (salvage subtracted) | the arguments themselves |
| `total_cost` | sum of the components in `cost_components` (salvage subtracted) | `cost_components` + each component's rates |
| `cost_per_demand_unit`, `cost_per_fulfilled_unit` | `total_cost` $/ \sum D$ or $/ \sum F$ | as for `total_cost` |

`TotalCost` is the short route: `TotalCost(holding=0.2, shortage=1.0)` adds
those two components. Its arguments are `holding`, `shortage`,
`backlog`, `order_per_line` with `order_per_unit`, `purchase`, `waste`,
`terminal_backlog`, `terminal_pipeline`, and `salvage_on_hand` with
`salvage_pipeline`. For per-row rates, or to use the component metrics
alongside, use `total_cost` with `context`.

`cost_components` names the parts of `total_cost`: any of `"holding"`,
`"shortage"`, `"backlog"`, `"ordering"`, `"purchase"`, `"waste"`,
`"terminal_backlog"`, `"terminal_pipeline"`, `"salvage"`. Each listed
component needs all of its rates, zeros included, so a total contains only
the components you list. Purchases are charged when ordered. Choose the window
and the terminal components that match your question; see
[Put costs on a run](../how-to/costs.md).

## Custom metrics

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

**See also:** [Walkthrough step 7](../learn/07-evaluate.md) ·
[API: metrics](../reference/metrics.md) ·
[Notebook 06](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb)
