# Inventory state

`InventoryStateDataFrame` is the snapshot of an inventory system at one moment:
for every SKU, what is on the shelf, what is on its way, and what is owed. It
is the starting point of every simulation and the object policies look at when
they decide.

## What the state holds

One row per SKU. The columns that matter most:

| Column | Meaning |
|---|---|
| `unique_id` | The SKU (the name is configurable with `sku_column`). |
| `on_hand` | Units on the shelf. |
| `in_transit` | A NumPy array of length `max_lead_time`. Slot $k$ holds the units arriving $k + 1$ periods from now. |
| `backorders` | Demand owed to customers. |
| `period`, `date` | The state's position in time. All SKUs share one period and one date. |
| `latest_*` | Flows of the most recent period (`latest_received`, `latest_fulfilled`, `latest_shortage`, …), reset at the start of each period. |
| `target_level`, `safety_stock` | Informational fields written by policies. |

From these, `inventory_position()` adds `total_in_transit` and

$$
\mathit{IP} = \text{on\_hand} + \sum_k \text{in\_transit}[k] - \text{backorders}.
$$

## Three ways to set the opening state

=== "Observed on-hand stock"

    The common case: you counted the shelf; nothing is on order or owed.

    ```python
    import numpy as np
    import pandas as pd

    from stockcast.core import InventoryStateDataFrame

    opening_date = pd.Timestamp("2026-01-05")
    counts = pd.DataFrame({"unique_id": ["tea_250g", "coffee_1kg"],
                           "on_hand": [30.0, 12.0]})

    state = InventoryStateDataFrame(
        ["tea_250g", "coffee_1kg"], max_lead_time=2, allow_backorders=False,
    ).initialize_from_observed(counts, on_hand_column="on_hand",
                               start_date=opening_date)
    ```

=== "Empty"

    A new product, or a what-if that starts from nothing:

    ```python
    empty = InventoryStateDataFrame(
        ["tea_250g"], max_lead_time=2, allow_backorders=False,
    ).initialize_zero(start_date=opening_date)
    ```

=== "A full state table"

    When you already know the pipeline and the backorders, pass a complete
    table:

    ```python
    full = InventoryStateDataFrame(pd.DataFrame({
        "unique_id": ["tea_250g", "coffee_1kg"],
        "on_hand": [30.0, 0.0],
        "backorders": [0.0, 4.0],
        "in_transit": [np.array([0.0, 12.0]), np.array([10.0, 0.0])],
        "safety_stock": [0.0, 0.0],
        "period": [0, 0],
        "date": [opening_date, opening_date],
    }), max_lead_time=2, allow_backorders=True)

    full.inventory_position()[["unique_id", "on_hand", "in_transit",
                               "backorders", "inventory_position"]]
    ```

    ```text
        unique_id  on_hand   in_transit  backorders  inventory_position
    0    tea_250g     30.0  [0.0, 12.0]         0.0                42.0
    1  coffee_1kg      0.0  [10.0, 0.0]         4.0                 6.0
    ```

The opening state is always a fact you supply. The state rejects missing or
contradictory values (a SKU with both stock and backorders, backorders in a
lost-sales state, negative stock, a pipeline of the wrong length) as soon as
it is used.

### Constructor arguments

| Argument | Meaning |
|---|---|
| `data` | A list of SKUs, or a DataFrame with a SKU column |
| `max_lead_time` | Length of the pipeline. Must be at least the longest lead time (and, with suppliers, the longest delivery offset) |
| `sku_column` | Name of the SKU column, default `"unique_id"` |
| `allow_backorders` | `True` (backorders) or `False` (lost sales). During a simulation the engine applies the policy's setting |
| `start_date` | Opening date, if not supplied later by an initializer |

## Open orders

The `in_transit` array says *how much* arrives *when*. Often you also know
*which orders* make it up: supplier, order number, partial deliveries. Declare
them with `with_open_orders`, one row per scheduled delivery:

```python
state = InventoryStateDataFrame(
    ["tea_250g"], max_lead_time=3, allow_backorders=False,
).initialize_zero(start_date=opening_date).with_open_orders(pd.DataFrame({
    "unique_id":    ["tea_250g", "tea_250g", "tea_250g"],
    "supplier_id":  ["roaster", "importer", "importer"],
    "order_id":     ["PO-17", "PO-12", "PO-12"],   # PO-12 arrives in two parts
    "order_period": [-1, -2, -2],
    "due_period":   [1, 2, 3],
    "quantity":     [20.0, 30.0, 10.0],
}))

state.open_orders()
```

```text
   order_id unique_id supplier_id   source  order_period  ordered_quantity  remaining_quantity  due_period  final_due_period
0         0  tea_250g     roaster  opening          -1.0              20.0                20.0           1                 1
1         1  tea_250g    importer  opening          -2.0              40.0                40.0           2                 3
```

`due_period` counts state periods: the opening state is period 0, so
`due_period=1` arrives before the first demand. `scheduled_receipts()` lists
the individual deliveries, and the pipeline is filled in from them:

```python
state.get_dataframe()["in_transit"].iloc[0]
```

```text
array([20., 30., 10.])
```

Required columns are the SKU, `due_period`, and `quantity`; `supplier_id`,
`order_period`, `order_id`, and `ordered_quantity` are optional. If the state
already has a pipeline, the orders must add up to it exactly: they then
explain the existing quantities rather than adding new ones.
[Suppliers and open orders](suppliers.md) shows how open orders flow through a
simulation.

## Stepping the state by hand

The engine moves the state forward for you. For a caller-owned loop, the same
steps are public:

| Method | Does |
|---|---|
| `advance_period(period_frequency=..., is_review_period=...)` | Move to the next period, receive due deliveries, serve old backorders |
| `stockcast.utils.update_inventory_with_orders(state, decision)` | Place an `OrderDecision` (zero lead time: received now) |
| `stockcast.utils.place_order_lines(state, lines)` | Place order lines with explicit due periods |
| `fulfill_demand(demand_df)` | Serve the current period's demand |

See [Run your own simulation loop](../how-to/manual-loop.md).

## Reading the state

| Method | Returns |
|---|---|
| `get_dataframe()` | A copy of the current state table |
| `inventory_position()` | The state plus `total_in_transit` and `inventory_position` |
| `open_orders()` | One row per open order line |
| `scheduled_receipts()` | One row per open delivery |
| `get_history()` | Snapshots of past periods recorded on this object |

**Go deeper:** [Learn step 1](../learn/01-inventory-state.md) ·
[API: state and orders](../reference/state.md) ·
[Notebook 01](../notebooks/01_introduction_to_inventory_flow.ipynb)
