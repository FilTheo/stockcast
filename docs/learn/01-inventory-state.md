<span class="sc-step">Step 1 of 9</span>

# Inventory state

Before anything can be decided, you need to know where you stand. In
Stockcast that is the job of `InventoryStateDataFrame`: one row per SKU,
holding the stock on the shelf, the stock on its way, and the demand still
owed.

## Three quantities, one position

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

## Create the tea shop's state

```python
import pandas as pd

from stockcast.core import InventoryStateDataFrame

sku = "tea_250g"
opening_date = pd.Timestamp("2026-01-05")

inventory = InventoryStateDataFrame(
    [sku],                    # the SKUs this state tracks
    max_lead_time=2,          # pipeline length: the longest lead time you need
    allow_backorders=False,   # unserved demand is lost, not owed
).initialize_from_observed(
    pd.DataFrame({"unique_id": [sku], "on_hand": [30.0]}),
    on_hand_column="on_hand",
    start_date=opening_date,
)

inventory.inventory_position()[["unique_id", "on_hand", "in_transit",
                                "backorders", "inventory_position"]]
```

```text
  unique_id  on_hand  in_transit  backorders  inventory_position
0  tea_250g     30.0  [0.0, 0.0]         0.0                30.0
```

Nothing is on order yet, so the position equals the 30 packs on the shelf.

## The pipeline, slot by slot

`in_transit` is a small array per SKU. **Slot $i$ holds the units that arrive
$i + 1$ periods from now**, before that period's demand. With
`max_lead_time=2` there are two slots: "arrives tomorrow" and "arrives the day
after".

Suppose the shop placed an order of 12 packs yesterday and it arrives the day
after tomorrow. You can declare that opening order explicitly:

```python
inventory_with_order = InventoryStateDataFrame(
    [sku], max_lead_time=2, allow_backorders=False,
).initialize_from_observed(
    pd.DataFrame({"unique_id": [sku], "on_hand": [30.0]}),
    on_hand_column="on_hand",
    start_date=opening_date,
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
automatically replaces it. Policies declare the same choice, and during a
simulation the engine runs the state with the policy's setting.

## Every value is explicit

Notice that we declared the opening stock, the opening date, and the shortage
rule. Stockcast never guesses them. A simulation that starts from assumed
stock would produce confident-looking but made-up numbers, so the state asks
you for the facts up front. See [Every input is explicit](../user-guide/design/explicit-inputs.md).

!!! summary "Recap"

    - `InventoryStateDataFrame` holds on hand, the pipeline, and backorders for
      every SKU.
    - Policies decide with the **inventory position**
      $\mathit{IP} = \text{on hand} + \text{on order} - \text{backorders}$.
    - Pipeline slot $i$ arrives $i + 1$ periods from now.

**Go deeper:** [Inventory state](../user-guide/inventory-state.md) ·
[Stock accounting](../user-guide/concepts/accounting.md) ·
[Notebook 01](../notebooks/01_introduction_to_inventory_flow.ipynb)

[Next: Demand and time :octicons-arrow-right-24:](02-demand-and-time.md){ .md-button }
