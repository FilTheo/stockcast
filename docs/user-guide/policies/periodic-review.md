# Periodic review $(R, s, S)$

The $(R, s, S)$ policy combines a fixed ordering calendar with a trigger
level. Every $R$ periods it checks the inventory position; if the position is
at or below $s$, it orders up to $S$. Otherwise it skips the order.

## The rule

At every decision period $t$ of the schedule:

$$
q_t = \begin{cases}
\max(0,\; S - \mathit{IP}_t) & \text{if } \mathit{IP}_t \le s, \\
0 & \text{otherwise.}
\end{cases}
$$

With $s = S$ it behaves like order-up-to $(R, S)$; lowering $s$ skips small
orders, which pays off when each order carries a fixed cost. The $(R, s, S)$
policy is a classic of periodic inventory control (Silver, Pyke and Thomas,
2017), and good $(s, S)$ pairs are usually found together, by optimisation or
simulation.

## Targets come from a provider

Where $s$ and $S$ come from varies a lot between organisations: a table from a
planning system, fixed business rules, a formula on a forecast. So
`PeriodicReviewPolicy` receives them from a **target provider**, a small object
with one method, `provide`, that returns one $(s, S)$ pair per SKU.

| Provider | Use it when |
|---|---|
| `ColumnPeriodicReviewTargets(reorder_point_column=..., order_up_to_column=...)` | your table already has $s$ and $S$ columns |
| `FixedPeriodicReviewTargets(reorder_point=..., order_up_to_level=...)` | fixed values, for all SKUs or per SKU (a dict) |
| a subclass of `PeriodicReviewTargetProvider` | you compute $s$ and $S$ with your own rule |

Stockcast validates every provider's output in the same way: one row per SKU,
finite values, $s \ge 0$, and $S \ge s$.

## In Stockcast

```python
import pandas as pd

from stockcast.core import InventoryStateDataFrame
from stockcast.policies import (
    ColumnPeriodicReviewTargets, FixedPeriodicReviewTargets, PeriodicReviewPolicy,
)

opening_date = pd.Timestamp("2026-01-05")
skus = pd.DataFrame({"unique_id": ["tea_250g", "coffee_1kg"]})

policy = PeriodicReviewPolicy(
    lead_time=2, review_period=7, allow_backorders=False,
).fit(
    skus,
    target_provider=FixedPeriodicReviewTargets(
        reorder_point={"tea_250g": 25.0, "coffee_1kg": 10.0},
        order_up_to_level={"tea_250g": 60.0, "coffee_1kg": 30.0},
    ),
    information_origin=opening_date,
    information_frequency="D",
)

state = InventoryStateDataFrame(
    ["tea_250g", "coffee_1kg"], max_lead_time=2, allow_backorders=False,
).initialize_from_observed(
    pd.DataFrame({"unique_id": ["tea_250g", "coffee_1kg"], "on_hand": [22.0, 14.0]}),
    on_hand_column="on_hand", start_date=opening_date,
)
policy.predict(state, current_period=0).get_dataframe()[
    ["unique_id", "inventory_position", "reorder_point", "target_level", "order_quantity"]]
```

```text
    unique_id  inventory_position  reorder_point  target_level  order_quantity
0    tea_250g                22.0           25.0          60.0            38.0
1  coffee_1kg                14.0           10.0          30.0             0.0
```

Tea is below its reorder point and orders up to 60; coffee is above its
reorder point and waits.

`ColumnPeriodicReviewTargets` reads the same values from columns of the table
you pass to `fit`:

```python
table = pd.DataFrame({"unique_id": ["tea_250g", "coffee_1kg"],
                      "s": [25.0, 10.0], "S": [60.0, 30.0]})
from_columns = PeriodicReviewPolicy(lead_time=2, review_period=7,
                                    allow_backorders=False).fit(
    table,
    target_provider=ColumnPeriodicReviewTargets(reorder_point_column="s",
                                                order_up_to_column="S"),
    information_origin=opening_date, information_frequency="D",
)
```

## Write a provider

A provider receives the table passed to `fit` and returns
`PeriodicReviewTargets(frame, metadata)`. Here $s$ and $S$ are set as days of
cover of a forecast daily rate:

```python
from stockcast.policies import PeriodicReviewTargetProvider, PeriodicReviewTargets


class DaysOfCoverTargets(PeriodicReviewTargetProvider):
    """s = reorder_days x rate, S = order_up_to_days x rate."""

    def __init__(self, reorder_days, order_up_to_days):
        self.reorder_days, self.order_up_to_days = reorder_days, order_up_to_days

    def provide(self, target_data, *, sku_column):
        rate = target_data["daily_rate"]
        frame = pd.DataFrame({
            sku_column: target_data[sku_column],
            "reorder_point": self.reorder_days * rate,
            "order_up_to_level": self.order_up_to_days * rate,
        })
        return PeriodicReviewTargets(frame=frame, metadata=self.to_manifest())

    def to_manifest(self):
        return {**super().to_manifest(), "reorder_days": self.reorder_days,
                "order_up_to_days": self.order_up_to_days}


rates = pd.DataFrame({"unique_id": ["tea_250g", "coffee_1kg"], "daily_rate": [6.0, 2.0]})
cover = PeriodicReviewPolicy(lead_time=2, review_period=7, allow_backorders=False).fit(
    rates, target_provider=DaysOfCoverTargets(4, 10),
    information_origin=opening_date, information_frequency="D",
)
cover.get_parameters()
```

```text
    unique_id  reorder_point  order_up_to_level
0    tea_250g           24.0               60.0
1  coffee_1kg            8.0               20.0
```

The provider's `to_manifest()` output is stored in the run manifest, so every
result records how its targets were made. Set the class attribute
`target_source = "external_direct"` when the provider only passes along
values computed elsewhere; the default is `"custom_provider"`.

**Notebooks:** [02b: decision schedules](../../notebooks/02b_decision_schedules.ipynb) ·
[08: callbacks and audit](../../notebooks/08_callbacks_and_audit.ipynb)
