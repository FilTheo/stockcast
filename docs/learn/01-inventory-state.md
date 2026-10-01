<span class="sc-step">Step 1 of 9</span>

# Inventory state

Before anything can be decided, you need to know where you stand. In
Stockcast that is the job of `InventoryStateDataFrame`: one row per SKU,
holding the stock on the shelf, the stock on its way, and the demand still
owed.

## Inventory position

| Quantity | Column | Meaning |
|---|---|---|
| On hand | `on_hand` | Units on the shelf right now. |
| On order | `in_transit` | Units ordered but not yet delivered, arranged by arrival day. |
| Backorders | `backorders` | Demand already accepted but not yet served. |

Ordering decisions look at all three together, through the **inventory
position**:

$$
\mathit{IP} \;=\; \underbrace{\text{on hand}}_{\text{shelf}}
\;+\; \underbrace{\text{on order}}_{\text{pipeline}}
\;-\; \underbrace{\text{backorders}}_{\text{owed}}
$$

Think of it as the stock you *will* have once everything already ordered has
arrived and everyone you owe has been served. It is the quantity replenishment
policies control: on-hand stock tells you what you can sell today, inventory
position tells you whether you need to order.

## Create a state

```python
import pandas as pd

from stockcast.core import InventoryStateDataFrame

sku = "tea_250g"
opening_date = pd.Timestamp("2026-01-05")

inventory = InventoryStateDataFrame.from_observed(
    pd.DataFrame({
        "unique_id": [sku],     # one row per SKU
        "date": [opening_date], # the day the shelf was counted
        "on_hand": [30.0],
    }),
    max_lead_time=2,          # pipeline length (optional; shown here to see the slots)
    allow_backorders=False,   # unserved demand is lost, not owed
)

inventory.inventory_position()[["unique_id", "on_hand", "in_transit",
                                "backorders", "inventory_position"]]
```

```text
  unique_id  on_hand  in_transit  backorders  inventory_position
0  tea_250g     30.0  [0.0, 0.0]         0.0                30.0
```

Nothing is on order yet, so the position equals the 30 packs on the shelf.

## The pipeline

`in_transit` is a small array per SKU. **Slot $i$ holds the units that arrive
$i + 1$ periods from now**, before that period's demand. With
`max_lead_time=2` there are two slots: "arrives tomorrow" and "arrives the day
after". Leave `max_lead_time` out and Stockcast sizes the pipeline when it is
needed: a simulation makes room for the policy's lead time, and placing or
declaring an order makes room for its delivery.

Suppose the shop placed an order of 12 packs yesterday and it arrives the day
after tomorrow. You can declare that opening order explicitly:

```python
inventory_with_order = InventoryStateDataFrame.from_observed(
    pd.DataFrame({"unique_id": [sku], "date": [opening_date], "on_hand": [30.0]}),
    max_lead_time=2, allow_backorders=False,
).with_open_orders(pd.DataFrame({
    "unique_id": [sku],
    "due_period": [2],        # periods are counted from the opening state (period 0)
    "quantity": [12.0],
}))

inventory_with_order.inventory_position()[["on_hand", "in_transit", "inventory_position"]]
```

```text
   on_hand   in_transit  inventory_position
0     30.0  [0.0, 12.0]                42.0
```

The 12 packs sit in slot 1, "arriving in two periods". They are not on the
shelf yet, but they already count in the inventory position: $30 + 12 - 0 = 42$.

## Lost sales or backorders

`allow_backorders` chooses what happens when demand exceeds stock:

- `False`, **lost sales**: the customer leaves. Typical of shops.
- `True`, **backorders**: the customer waits and is served from the next
  delivery. Typical of business-to-business supply.

With backorders, owed demand lowers the inventory position, so the next order
automatically replaces it. Policies declare the same choice, and the state
and the policy must agree. Leave the state's `allow_backorders` unset and it
takes the policy's setting.

## Explicit inputs

The opening stock, the opening date, and the shortage rule are all declared
above; Stockcast does not assume any of them.

## Summary

- `InventoryStateDataFrame` holds on hand, the pipeline, and backorders for
  every SKU.
- Policies decide with the **inventory position**
  $\mathit{IP} = \text{on hand} + \text{on order} - \text{backorders}$.
- Pipeline slot $i$ arrives $i + 1$ periods from now.

**See also:** [Inventory state](../user-guide/inventory-state.md) ·
[The event table](../user-guide/concepts/accounting.md) ·
[Notebook 01](../notebooks/01_introduction_to_inventory_flow.ipynb)

[Next: Demand and time :octicons-arrow-right-24:](02-demand-and-time.md){ .md-button }
