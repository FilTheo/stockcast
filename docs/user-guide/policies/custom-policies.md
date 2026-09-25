# Write your own policy

Any ordering rule can become a Stockcast policy: a heuristic from your
business, a rule from a paper, a machine-learning model that predicts order
quantities. Subclass `BasePolicy`, implement two methods, and your rule runs in
the engine, in comparisons, and in the ledger exactly like the built-in ones.

## The contract

```py
class MyPolicy(BasePolicy):
    def fit(self, forecast_df, **kwargs):             # bind data, return self
        ...
        self.fitted_ = True
        return self

    def predict(self, inventory_state_df, *, current_period, **kwargs):
        ...                                           # read the state, never change it
        return OrderDecision(orders, lead_time=self.lead_time,
                             review_period=self.review_period)
```

| Piece | What Stockcast expects |
|---|---|
| `__init__` | Call `super().__init__(lead_time, review_period=..., service_level=..., allow_backorders=..., schedule=...)`. This sets up the timing, the schedule, and the shortage rule. |
| `fit(...)` | Store what the policy needs, set `self.fitted_ = True`, return `self`. The engine only runs fitted policies. |
| `predict(state, *, current_period)` | Receives a **copy** of the `InventoryStateDataFrame` before demand. Returns an `OrderDecision`. |
| The `OrderDecision` | One row per SKU with `order_quantity` $\ge 0$. For positive orders, `order_period = current_period` and `expected_delivery_period = current_period + lead_time`. Its `lead_time` must equal the policy's. |

The engine decides *when* `predict` is called (the policy's schedule), and
applies callbacks, constraints, and suppliers afterwards. Your policy only
answers "how much, right now?"

## Example: days of cover

A common retail rule: keep enough stock for a number of days of forecast
demand.

$$
q_t = \max\bigl(0,\; k \cdot \hat\mu - \mathit{IP}_t\bigr)
$$

??? example "Setup: the tea shop from Learn the basics"

    ```python
    --8<-- "learn-setup.py"
    ```

```python
from stockcast.core import BasePolicy, OrderDecision, SimulationEngine


class DaysOfCover(BasePolicy):
    """Order up to `days` times the forecast daily demand."""

    def __init__(self, days, **kwargs):
        super().__init__(**kwargs)
        self.days = days
        self.policy_name = f"Days of cover ({days})"

    def fit(self, forecast_df, **kwargs):
        self.daily_mean_ = forecast_df.set_index("unique_id")["daily_mean"]
        self.fitted_ = True
        return self

    def predict(self, inventory_state_df, *, current_period, **kwargs):
        state = inventory_state_df.inventory_position()
        level = state["unique_id"].map(self.daily_mean_) * self.days
        orders = pd.DataFrame({
            "unique_id": state["unique_id"],
            "order_quantity": (level - state["inventory_position"]).clip(lower=0),
            "target_level": level,
            "inventory_position": state["inventory_position"],
            "order_period": current_period,
            "expected_delivery_period": current_period + self.lead_time,
        })
        return OrderDecision(orders, lead_time=self.lead_time,
                             review_period=self.review_period)


cover = DaysOfCover(
    days=7, lead_time=lead_time, review_period=review_period, allow_backorders=False,
).fit(pd.DataFrame({"unique_id": [sku], "daily_mean": [6.0]}))

comparison = SimulationEngine().run_comparison(
    policies=[cover, tea_policy(0.95)],
    labels=["7 days of cover", "95% quantile target"],
    demand_source=demand, inventory=inventory, **run_settings,
)
comparison.summary()[["fill_rate", "stockout_periods", "mean_ending_on_hand_per_sku_period"]]
```

```text
                     fill_rate  stockout_periods  mean_ending_on_hand_per_sku_period
policy
7 days of cover            1.0                 0                           14.750000
95% quantile target        1.0                 0                           18.607143
```

On this demand path, seven days of cover ($42$ packs) served every sale with
about four fewer packs on the shelf than the 95% quantile target ($46$). One
demand path is one story; the next step would be more paths, or costs, before
deciding. The comparison itself is fair: same demand, same opening stock,
same engine.

## Example: extend a built-in policy

You can also subclass a built-in policy and change one step. This variant of
$(R, S)$ never orders more than a supplier's daily limit:

```python
from stockcast.policies import OrderUpToPolicy


class CappedOrderUpTo(OrderUpToPolicy):
    """(R, S), but at most `cap` units per order."""

    def __init__(self, cap, **kwargs):
        super().__init__(**kwargs)
        self.cap = cap

    def predict(self, inventory_state_df, *, current_period, **kwargs):
        decision = super().predict(inventory_state_df, current_period=current_period, **kwargs)
        frame = decision.get_dataframe()
        frame["order_quantity"] = frame["order_quantity"].clip(upper=self.cap)
        return OrderDecision(frame, lead_time=decision.lead_time,
                             review_period=decision.review_period)
```

!!! tip "Policy logic or a constraint?"

    A limit that belongs to the *decision rule* fits in the policy. A limit
    that belongs to the *operation*, and should apply whatever the policy, is
    better as an [ordering constraint](../constraints.md): it works with every
    policy and is recorded separately in the ledger.

## Optional extras

| Method | Why you might add it |
|---|---|
| `get_target_metadata()` | Return a dict with `forecast_origin` and `forecast_frequency` so the engine checks your forecast dates like it does for built-in policies. |
| `validate_decision_window(period, information_date, offset)` | Check, before the run, that the policy's information covers each scheduled decision. Raise to stop the run. |
| `validate_demand_window(demand, n_periods)` | Check the demand table before the run (for example, a season's boundaries). |
| `policy_name` | A readable name used as the default label in comparisons and the ledger. |

**Notebooks:** [05: custom policies](../../notebooks/05_custom_policies.ipynb) ·
[05c: extension points](../../notebooks/05c_extension_points.ipynb)
