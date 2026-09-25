# Callbacks

Real operations have exceptions: a supplier closes for a holiday, a buyer
doubles an order before a promotion, a stock count finds missing units.
Callbacks let you add such events to a simulation without changing the policy
or the engine. Like callbacks in Keras, they are called at fixed moments of
each period, and every change they make is written to an audit table.

## Two moments

| Hook | When | What it may change | Returns |
|---|---|---|---|
| `on_after_prediction(decision, context)` | after the policy proposes an order, before constraints | the order quantity per SKU (absolute values) | `OrderAdjustmentResult` or `None` |
| `on_after_demand(context)` | after demand is served, before the period is recorded | on-hand stock per SKU (signed changes) | `InventoryAdjustmentResult` or `None` |

A callback proposes; the engine validates and applies. An order change passes
through the ordering constraints afterwards. A stock change affects the next
decisions and is recorded in the ledger as `inventory_adjustment_units`. It
cannot undo today's lost sales, because those customers have already left.

Both hooks receive a `CallbackContext` with a **copy** of the state
(`inventory`), the `period` and `date`, the `run_window`, and the `phase`.

## Built-in scheduled callbacks

For planned, dated events, write a small table and use a built-in callback.
Rows match on `date` or `period` (the state period, see
[Timing](concepts/timing.md#two-period-counters)), and every row carries a
`reason` and a `source` for the audit trail.

| Callback | Hook | Extra column | Effect |
|---|---|---|---|
| `ScheduledOrderOverride` | after prediction | `order_quantity` | set the order to this value |
| `ScheduledOrderMultiplier` | after prediction | `multiplier` | multiply the proposed order |
| `ScheduledOrderHold` | after prediction | – | set the order to zero |
| `ScheduledInventoryAdjustment` | after demand | `quantity_delta` (and optional `received_date`) | add or remove stock |

??? example "Setup: the tea shop from Learn the basics"

    ```python
    --8<-- "learn-setup.py"
    ```

```python
from stockcast.core import (
    ScheduledInventoryAdjustment, ScheduledOrderMultiplier, SimulationEngine,
)

promotion = ScheduledOrderMultiplier(pd.DataFrame({
    "unique_id": [sku],
    "date": [pd.Timestamp("2026-01-22")],
    "multiplier": [1.5],
    "reason": ["stock up before the tea festival"],
    "source": ["marketing calendar"],
}))
stock_count = ScheduledInventoryAdjustment(pd.DataFrame({
    "unique_id": [sku],
    "date": [pd.Timestamp("2026-01-25")],
    "quantity_delta": [-3.0],
    "reason": ["damaged packs found in count"],
    "source": ["weekly stock count"],
}))

result = SimulationEngine().run(
    policy=policy, demand_source=demand, inventory=inventory,
    callbacks=[promotion, stock_count], **run_settings,
)
result.to_callback_audit_frame()[["phase", "date", "before_value", "after_value", "reason"]]
```

```text
               phase       date  before_value  after_value                             reason
0  on_after_prediction 2026-01-22          17.0         25.5  stock up before the tea festival
1      on_after_demand 2026-01-25          28.5         25.5      damaged packs found in count
```

The audit table records each accepted effect: which callback, when, the value
before and after, and why. On 22 January the policy asked for 17 packs and the
promotion turned that into 25.5; on 25 January the count removed 3 packs from
the shelf.

## Write your own callback

Subclass `SimulationCallback` and implement the hook you need. Return `None`
when there is nothing to change. This callback expedites orders when the shelf
is nearly empty:

```python
from stockcast.core import OrderAdjustmentResult, SimulationCallback


class TopUpWhenLow(SimulationCallback):
    """Add `extra` units to any order placed while on-hand stock is below `floor`."""

    def __init__(self, floor, extra):
        self.floor, self.extra = floor, extra

    def on_after_prediction(self, decision, context):
        orders = decision.get_dataframe()
        stock = context.inventory.set_index(context.sku_column)["on_hand"]
        low = orders["unique_id"].map(stock) < self.floor
        if not low.any():
            return None
        return OrderAdjustmentResult(pd.DataFrame({
            "unique_id": orders.loc[low, "unique_id"],
            "order_quantity": orders.loc[low, "order_quantity"] + self.extra,
            "reason": "shelf below floor",
            "source": "store manager rule",
        }))

    def get_config(self):
        return {"floor": self.floor, "extra": self.extra}


result = SimulationEngine().run(
    policy=policy, demand_source=demand, inventory=inventory,
    callbacks=[TopUpWhenLow(floor=18, extra=6)], **run_settings,
)
result.to_callback_audit_frame()[["date", "before_value", "after_value", "reason"]]
```

```text
        date  before_value  after_value             reason
0 2026-01-18          32.0         38.0  shelf below floor
1 2026-01-30          29.0         35.0  shelf below floor
2 2026-02-23          29.0         35.0  shelf below floor
```

On three review days the shelf was below 18 packs, and each of those orders
got 6 extra packs.

### Rules the engine applies

- **Order adjustments** name SKUs present in the decision and give finite,
  non-negative absolute quantities, each with a non-blank `reason` and
  `source`.
- **Stock adjustments** give a signed `quantity_delta` per SKU. A removal
  cannot exceed on-hand stock, and stock cannot be added while the SKU has
  backorders. With shelf life, added stock needs a `received_date` so its age
  is known.
- Callbacks run in list order; each sees the result of the ones before it.
- `reset(context)` runs before every run (and every comparison branch), so
  keep run-local state there. `get_config()` returns JSON-serialisable
  settings for the run manifest.
- An error inside a callback stops the run with a `CallbackError` naming the
  callback, the hook, the period, and the date.

!!! tip "Callback, constraint, or process?"

    - A **one-off or dated exception** to an order or to stock: a callback.
    - A **rule every order must satisfy**: an [ordering constraint](constraints.md).
    - A **recurring physical mechanism** (expiry, inspections, returns): an
      [inventory process](processes.md).

**Notebooks:** [08: callbacks and audit](../notebooks/08_callbacks_and_audit.ipynb) ·
[05c: extension points](../notebooks/05c_extension_points.ipynb)
