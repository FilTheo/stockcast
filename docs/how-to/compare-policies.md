# Compare forecasts and policies

A good comparison changes one thing and keeps everything else fixed: the
demand, the opening stock, the lead time, the costs, the scoring window.
`run_comparison` does the bookkeeping. This recipe compares two ordering rules
across three SKUs and picks the cheaper one.

## 1. One shared scenario

Three teas with different demand rates, twelve weeks of daily Poisson demand,
and opening stock worth five days of sales:

```python
import numpy as np
import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.policies import OrderUpToPolicy, ReorderPointPolicy

opening = pd.Timestamp("2026-01-05")
rates = {"tea_250g": 6.0, "coffee_1kg": 3.0, "matcha_50g": 1.5}
skus = list(rates)
L, R, n = 2, 4, 84

rng = np.random.default_rng(5)
dates = pd.date_range(opening + pd.Timedelta(days=1), periods=n, freq="D")
demand = pd.DataFrame([
    {"unique_id": sku, "period": p, "date": d, "y": float(rng.poisson(rate))}
    for sku, rate in rates.items() for p, d in enumerate(dates)
])

inventory = InventoryStateDataFrame(skus, max_lead_time=L, allow_backorders=False)
inventory.initialize_from_observed(
    pd.DataFrame({"unique_id": skus, "on_hand": [5 * r for r in rates.values()]}),
    on_hand_column="on_hand", start_date=opening,
)
```

## 2. Two policies from the same forecast

Both policies use the same forecast (Poisson demand at each SKU's rate); they
differ in how they use it. $(R, S)$ reviews every 4 days and covers
$L + R = 6$ days. $(s, S)$ checks every day, so its reorder point covers
$L + 1 = 3$ days, and orders up to $s$ plus a week of demand:

```python
paths = np.random.default_rng(0)


def window_quantile(rate, horizon, probability=0.95):
    return float(np.quantile(paths.poisson(rate, size=(10_000, horizon)).sum(axis=1),
                             probability))


rs_targets = pd.DataFrame({
    "unique_id": skus,
    "S": [window_quantile(r, L + R) for r in rates.values()],
    "end": opening + pd.Timedelta(days=L + R),
})
order_up_to = OrderUpToPolicy(lead_time=L, review_period=R, service_level=0.95,
                              allow_backorders=False).fit(
    rs_targets, target_column="S", target_probability=0.95, protection_horizon=L + R,
    target_source="external_direct", forecast_origin=opening, forecast_frequency="D",
    target_end_date_column="end",
)

ss_targets = pd.DataFrame({
    "unique_id": skus,
    "s": [window_quantile(r, L + 1) for r in rates.values()],
    "end": opening + pd.Timedelta(days=L + 1),
})
ss_targets["S"] = ss_targets["s"] + [7 * r for r in rates.values()]
reorder_point = ReorderPointPolicy(lead_time=L, review_period=1, policy_type="sS",
                                   service_level=0.95, allow_backorders=False).fit(
    ss_targets, forecast_origin=opening, forecast_frequency="D",
    reorder_point_column="s", order_up_to_column="S", reorder_end_date_column="end",
    reorder_horizon=L + 1, target_source="external_direct", target_probability=0.95,
)
```

## 3. Run them side by side

Two weeks of warm-up let both policies settle before scoring starts:

```python
comparison = SimulationEngine().run_comparison(
    policies=[order_up_to, reorder_point],
    labels=["(R, S) every 4 days", "(s, S) daily check"],
    demand_source=demand, inventory=inventory, n_periods=n, period_frequency="D",
    warmup_periods=14, scoring_periods=70, settlement_periods=0,
    order_during_settlement=False, demand_source_name="three_teas", random_seed=5,
)
```

## 4. Score with the same costs

```python
from stockcast.evaluation import (
    InventoryEvaluator, avg_on_hand, fill_rate, order_event_count, total_cost,
)

costs = {
    "holding_cost_per_unit_period": 0.02,
    "shortage_cost_per_unit": 2.00,
    "order_cost_per_sku_line": 5.00,
    "order_cost_per_unit": 0.0,
    "cost_components": ["holding", "shortage", "ordering"],
}


def score(groupby):
    return pd.concat({
        label: InventoryEvaluator().fit(comparison[label], window="scoring").evaluate(
            [fill_rate, avg_on_hand, order_event_count, total_cost],
            groupby=groupby, context=costs)
        for label in comparison
    }).droplevel(1)


score([]).round(3)
```

```text
                     fill_rate  avg_on_hand  order_event_count  total_cost
(R, S) every 4 days        1.0       13.314                 17      310.92
(s, S) daily check         1.0       19.238                 26      220.80
```

```python
score(["unique_id"])[["unique_id", "fill_rate", "total_cost"]].round(3)
```

```text
                      unique_id  fill_rate  total_cost
(R, S) every 4 days    tea_250g        1.0      114.30
(R, S) every 4 days  coffee_1kg        1.0      101.80
(R, S) every 4 days  matcha_50g        1.0       94.82
(s, S) daily check     tea_250g        1.0       87.92
(s, S) daily check   coffee_1kg        1.0       69.98
(s, S) daily check   matcha_50g        1.0       62.90
```

## Reading the result

Both rules served every sale. The difference is in how they spend money:

| | Order lines | Ordering cost | Holding cost | Total |
|---|---:|---:|---:|---:|
| $(R, S)$ every 4 days | 51 | 255.00 | 55.92 | 310.92 |
| $(s, S)$ daily check | 28 | 140.00 | 80.80 | 220.80 |

$(R, S)$ orders every SKU at every review, 51 order lines at €5 each. $(s, S)$
orders a SKU only when it runs low, and then orders a week's worth, so it
places about half as many lines and carries a little more stock. With a €5
fee per line, that trade wins for every SKU. Lower the fee, or raise the cost
of holding stock, and the answer can flip; rerun `score` with new rates to
find out. (The numbers come from `sku_order_line_count`, `ordering_cost`, and
`holding_cost`, evaluated the same way.)

## Variations

- **Two forecasts, one policy.** Fit the same policy class on targets from two
  forecasting models and compare them the same way. That measures forecasts by
  what they do to stock and cost, not only by accuracy.
- **Rolling refits.** Pass `policy_schedules=[...]` with one snapshot
  dictionary per policy ([Refresh targets as forecasts roll](rolling-targets.md)).
- **Many demand paths.** Loop over several seeds and average the metrics. One
  path is one possible future; several give you a distribution.
- **Plots.** `plot_summary_comparison(comparison)` and
  `plot_comparison_dashboard(comparison)` draw the comparison
  ([Plots](../user-guide/visualization.md)).

[Notebook 06](../notebooks/06_fair_forecast_and_policy_comparisons.ipynb)
walks through forecast and policy comparisons on five SKUs with smooth
forecasts.
