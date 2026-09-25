# Put costs on a run

Costs put service and stock on one scale. Stockcast computes costs from the
ledger with rates that you provide, so every euro in a result can be traced to
a quantity and a rate.

??? example "Setup: the tea shop from Learn the basics"

    ```python
    --8<-- "learn-setup.py"
    ```

## Rates for the whole run

Pass rates in the evaluator's `context`, and list the parts of `total_cost` in
`cost_components`:

```python
from stockcast.evaluation import (
    InventoryEvaluator, holding_cost, ordering_cost, purchase_cost, total_cost,
)

context = {
    "holding_cost_per_unit_period": 0.02,   # per unit, per period on the shelf
    "shortage_cost_per_unit": 2.00,         # per unit short
    "order_cost_per_sku_line": 5.00,        # fixed fee per order line
    "order_cost_per_unit": 0.00,            # variable handling per unit ordered
    "purchase_cost_per_unit": 1.50,         # what the stock itself costs
    "cost_components": ["holding", "shortage", "ordering", "purchase"],
}
InventoryEvaluator().fit(result, window="scoring").evaluate(
    [holding_cost, ordering_cost, purchase_cost, total_cost], groupby=[], context=context,
)
```

```text
   holding_cost  ordering_cost  purchase_cost  total_cost
0         20.84           70.0          499.5      590.34
```

Every component you list needs all of its rates, zeros included. A rate you
did not give is never assumed, so the total means exactly what you wrote.

## Rates per SKU or per period

A rate can also be a **column of the ledger**. Add it, then fit the evaluator
on the enriched table:

```python
unit_prices = {"tea_250g": 1.50}          # one entry per SKU

events = result.to_event_frame()
events["purchase_cost_per_unit"] = events["unique_id"].map(unit_prices)
events["holding_cost_per_unit_period"] = 0.01 * events["purchase_cost_per_unit"]

InventoryEvaluator().fit(event_frame=events, window="scoring").evaluate(
    [holding_cost, purchase_cost], groupby=["unique_id"],
)
```

```text
  unique_id  holding_cost  purchase_cost
0  tea_250g        15.63          499.5
```

A column takes precedence over a context value with the same name. The same
approach works for rates that change over time: build the column from `date`.

## Match rates to the period length

Rates are **per period**. If your holding cost is quoted per year and a period
is a day, divide:

$$
h_{\text{period}} = \frac{h_{\text{year}}}{\text{periods per year}}
= \frac{0.20 \times €1.50}{365} \approx €0.00082 \text{ per pack per day.}
$$

When you compare runs with different period lengths (daily against weekly
review, say), convert every rate to its own period length first.
[Notebook 05b](../notebooks/05b_reorder_points_and_review_frequency.ipynb)
does this for a review-frequency study.

## Choose the window

Costs are charged in the period where their quantity appears:

| Cost | Charged in the period… |
|---|---|
| purchase, ordering | the order is **placed** |
| holding | stock is on the shelf at the end of the period |
| shortage | demand goes unserved |
| backlog | backorders are open at the end of the period |
| waste | stock expires |

Evaluating the `"scoring"` window therefore counts orders placed in scoring,
even if they arrive later. For a finite-horizon comparison (a season, a
planning horizon), decide how to treat what is left at the end:

| Component | Charges or credits |
|---|---|
| `terminal_backlog` | backorders still open at the end, at `terminal_backlog_cost_per_unit` |
| `terminal_pipeline` | orders still on their way, at `terminal_pipeline_cost_per_unit` |
| `salvage` | a **credit** for leftover stock and pipeline, at `on_hand_salvage_per_unit` and `pipeline_salvage_per_unit` |

```python
season_end = dict(context,
    on_hand_salvage_per_unit=0.50,
    pipeline_salvage_per_unit=0.50,
    cost_components=["holding", "shortage", "ordering", "purchase", "salvage"],
)
InventoryEvaluator().fit(result, window="scoring").evaluate(
    [total_cost], groupby=[], context=season_end,
)
```

```text
   total_cost
0      577.84
```

The 25 packs left on the shelf are credited at €0.50 each.

## Costs per unit

`cost_per_demand_unit` and `cost_per_fulfilled_unit` divide `total_cost` by
total demand or by units served: handy for comparing SKUs of very different
volumes.

**Go deeper:** [Evaluation and metrics](../user-guide/metrics.md#costs) ·
[Compare forecasts and policies](compare-policies.md)
