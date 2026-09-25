# Suppliers and open orders

By default every accepted order is one delivery, arriving exactly $L$ periods
later. Real supply is richer: several suppliers, lead times that vary, orders
delivered in parts. The **supply model** describes all of this, and the
**order frame** tells you which order is where.

Everything here is optional. Runs without a supply model behave exactly as
described in the rest of the guide.

## Two views of the pipeline

| View | What you see | Where |
|---|---|---|
| Per SKU | `in_transit[k]`: units arriving $k + 1$ periods from now | the state, the ledger (`on_order_end`, `received_units`) |
| Per order | each open order line: supplier, order period, due period, remaining quantity | `state.open_orders()`, `result.to_order_frame()` |

The two always agree: for every SKU and future period, the open deliveries due
then add up to the matching `in_transit` slot. The accounting (inventory
position, ledger identities) uses the per-SKU view, so the order view adds
detail without changing any number.

## A supply model

```python
from stockcast.core import Supplier, SupplierShares, SupplyModel

supply = SupplyModel(
    suppliers=[
        Supplier("local_packer", lead_time=1),
        Supplier(
            "importer",
            lead_time={3: 0.6, 4: 0.3, 6: 0.1},        # random lead time
            partial_deliveries=[(0, 0.7), (2, 0.3)],  # 70% on arrival, 30% two periods later
        ),
    ],
    allocation=SupplierShares({"local_packer": 0.3, "importer": 0.7}),
    random_seed=2026,
)
supply.max_delivery_offset
```

```text
8
```

The pieces:

**`Supplier(supplier_id, lead_time, *, partial_deliveries=None, delivery=None)`**

:   `lead_time` is a fixed integer $\ge 0$, or a distribution
    `{lead_time: probability}` over integers with probabilities summing to 1.
    `partial_deliveries` splits each order into parts
    `[(delay, share), ...]`: a part with delay $d$ arrives $d$ periods after
    the lead time. `delivery` adds a
    [`DeliveryOutcome`](unreliable-deliveries.md) for late or short deliveries.

**`SupplierShares(shares, *, by_sku=None)`**

:   Splits each SKU's accepted order by fixed fractions, for all SKUs or per
    SKU through `by_sku`. The last positive share receives the rounding
    remainder, so the parts always add up to the order. With a single
    supplier the allocation can be omitted.

**`random_seed`**

:   Required whenever a lead time is random. Lead times are drawn once per
    run: one value per decision period, SKU, and supplier, with a separate
    random stream per supplier. Every branch of `run_comparison` sees the same
    draws, so comparisons stay paired.

**Pipeline length**

:   The state's `max_lead_time` must cover `supply.max_delivery_offset`, the
    longest lead time plus the longest partial-delivery delay (here
    $6 + 2 = 8$).

## Run with suppliers

??? example "Setup: the tea shop from Learn the basics"

    ```python
    --8<-- "learn-setup.py"
    ```

```python
import warnings

from stockcast.core import InventoryStateDataFrame, SimulationEngine

inventory_8 = InventoryStateDataFrame(
    [sku], max_lead_time=supply.max_delivery_offset, allow_backorders=False,
).initialize_from_observed(
    pd.DataFrame({"unique_id": [sku], "on_hand": [30.0]}),
    on_hand_column="on_hand", start_date=opening_date,
)

with warnings.catch_warnings():
    warnings.simplefilter("ignore", UserWarning)    # see "Lead-time assumption" below
    result = SimulationEngine().run(
        policy=policy, demand_source=demand, inventory=inventory_8,
        supply=supply, **run_settings,
    )

orders = result.to_order_frame()
orders[["order_id", "supplier_id", "order_date", "due_date", "lead_time",
        "ordered_quantity", "delivery_quantity", "status"]].head(6)
```

```text
   order_id   supplier_id order_date   due_date  lead_time  ordered_quantity  delivery_quantity    status
0         0  local_packer 2026-01-06 2026-01-07        1.0               4.8               4.80  received
1         1      importer 2026-01-06 2026-01-09        3.0              11.2               7.84  received
2         1      importer 2026-01-06 2026-01-11        5.0              11.2               3.36  received
3         2  local_packer 2026-01-10 2026-01-11        1.0               8.1               8.10  received
4         3      importer 2026-01-10 2026-01-14        4.0              18.9              13.23  received
5         3      importer 2026-01-10 2026-01-16        6.0              18.9               5.67  received
```

The first order of 16 packs was split 30/70: 4.8 packs from the local packer
the next day, and 11.2 from the importer, of which 70% arrived after 3 days
and the rest 2 days later.

The pipeline in the engine becomes:

```text
policy → order callbacks → constraints → supply: allocation, lead time, split → pipeline or immediate receipt
```

Policies, callbacks, and constraints are unchanged: they still see one order
quantity per SKU.

## The order frame

`result.to_order_frame()` has one row per scheduled delivery, covering the
opening pipeline and every order placed during the run:

| Column | Meaning |
|---|---|
| `order_id` | the order line; deliveries of one line share it |
| `unique_id`, `supplier_id` | SKU and supplier (`None` when unknown) |
| `source` | `"opening"` or `"placed"` |
| `order_period`, `order_date` | when the line was ordered |
| `due_period`, `due_date` | when this delivery is received, before that period's demand |
| `lead_time` | realised `due_period - order_period` |
| `ordered_quantity`, `delivery_quantity` | size of the whole line and of this delivery |
| `status` | `"received"` or `"open"` at the end of the run |

Per SKU and period, received deliveries add up to the ledger's
`received_units`, and open ones to the final `on_order_end`. A few lines of
pandas give supplier-level views:

```python
placed = orders[orders["source"] == "placed"]
placed.groupby("supplier_id").agg(
    deliveries=("delivery_quantity", "size"),
    units=("delivery_quantity", "sum"),
    mean_lead_time=("lead_time", "mean"),
).round(2)
```

```text
              deliveries   units  mean_lead_time
supplier_id
importer              28  232.89            4.43
local_packer          14   99.81            1.00
```

## Same results, more detail

A single supplier with the policy's lead time gives exactly the same ledger as
a run without a supply model:

```python
plain = SimulationEngine().run(policy=policy, demand_source=demand,
                               inventory=inventory, **run_settings)
one_supplier = SimulationEngine().run(
    policy=policy, demand_source=demand, inventory=inventory,
    supply=SupplyModel([Supplier("wholesaler", lead_time=lead_time)]), **run_settings,
)
plain.to_event_frame().equals(one_supplier.to_event_frame())
```

```text
True
```

So you can move to the order view at any time without changing results; the
order frame then also names the supplier.

## Lead-time assumption

A policy sizes its target for its own `lead_time`: an order-up-to policy
covers $H = L + R$ periods. Suppliers may deliver at other times: a different
or random lead time, partial deliveries, or delays. Stockcast simulates
exactly the target you supplied against exactly the supply you declared.
That is often the very experiment you want: *how does my plan hold up if the
importer is slower than I assumed?*

The first time in a run that a delivery arrives at a time other than the
policy's lead time, Stockcast issues a friendly `UserWarning` so the
difference never goes unnoticed. Results are not changed. If you want targets
that account for random lead times, compute them outside Stockcast over the
random protection window (for example, the distribution of demand over
$L + R$ with $L$ random), and pass them in as usual. Once you have made that
choice, you can silence the warning:

```python
warnings.filterwarnings("ignore", message="supplier deliveries arrive at times other")
```

## Choosing a supplier per order

A `SupplierAllocation` decides how each accepted order is split. Subclass it
and implement `allocate(orders, context)`; return one row per SKU and supplier
with an `order_quantity`. `context` is an `AllocationContext` with copies of
the state and its open orders, the period and date, the supplier ids, and
`decision`, the accepted `OrderDecision` including any extra columns your
policy wrote. That lets the policy pick the supplier:

```python
from stockcast.core import OrderDecision, SupplierAllocation
from stockcast.policies import OrderUpToPolicy


class ExpediteWhenLow(OrderUpToPolicy):
    """Use the express supplier when the shelf is nearly empty."""

    def predict(self, inventory_state_df, *, current_period, **kwargs):
        decision = super().predict(inventory_state_df, current_period=current_period, **kwargs)
        frame = decision.get_dataframe()
        on_hand = inventory_state_df.get_dataframe().set_index("unique_id")["on_hand"]
        low = frame["unique_id"].map(on_hand) < 15
        frame["supplier_id"] = low.map({True: "express", False: "regular"})
        return OrderDecision(frame, lead_time=decision.lead_time,
                             review_period=decision.review_period)


class FollowPolicy(SupplierAllocation):
    def allocate(self, orders, context):
        chosen = context.decision.set_index(context.sku_column)["supplier_id"]
        return orders.assign(supplier_id=orders[context.sku_column].map(chosen))
```

The engine checks the allocation: known suppliers, no duplicates, no negative
quantities, and parts that add up to the accepted order.

## Supplier capacity

Capacity needs no special object; choose the behaviour you mean:

- **The excess is not ordered.** Write an [`OrderingConstraint`](constraints.md)
  that caps the total. Constraints run before the supply stage, and the ledger
  records every cut.
- **The excess goes to another supplier.** Write a `SupplierAllocation` that
  gives each supplier at most its capacity and passes the rest on.

[Notebook 05f](../notebooks/05f_unreliable_supplier.ipynb) builds both.

## Open orders in a manual loop

For a caller-owned loop, `OrderLines` holds explicit deliveries and
`stockcast.utils.place_order_lines` places them on a state. Rows with the same
`order_line` are parts of one line; a delivery due in the current period is
received immediately:

```python
from stockcast.core import OrderLines
from stockcast.utils import place_order_lines

state = inventory_8.advance_period(period_frequency="D", is_review_period=True)
now = int(state.get_dataframe()["period"].iloc[0])
lines = OrderLines(pd.DataFrame({
    "unique_id":      [sku, sku, sku],
    "supplier_id":    ["local_packer", "importer", "importer"],
    "order_quantity": [5.0, 8.0, 4.0],
    "order_period":   [now, now, now],
    "due_period":     [now + 1, now + 3, now + 5],
    "order_line":     [0, 1, 1],
}))
state = place_order_lines(state, lines)
state.scheduled_receipts()
```

```text
   order_id unique_id   supplier_id  due_period  quantity
0         0  tea_250g  local_packer           2       5.0
1         1  tea_250g      importer           4       8.0
2         1  tea_250g      importer           6       4.0
```

See [Run your own simulation loop](../how-to/manual-loop.md).

## What the ledger records

- `order_quantity` is the accepted quantity, `received_units` the sum of all
  deliveries received, `on_order_end` the sum of open deliveries.
- With a supply model, `sku_order_line_count` counts supplier order lines: an
  order split between two suppliers counts two. `ordering_cost` then charges
  its fixed cost per supplier line, which is usually what a delivery fee
  means. For a fixed cost per decision, use `order_event_count`.
- `run_settings["supply"]` in the manifest records every supplier, lead-time
  distribution, allocation, and the seed.

Every supplier delivers to the same stocking point; there is no multi-echelon
network. `SupplierAllocation` and the order frame are the natural extension
points for that kind of work.

**Notebooks:** [05d: open orders and suppliers](../notebooks/05d_open_orders_and_suppliers.ipynb) ·
[05f: unreliable supplier](../notebooks/05f_unreliable_supplier.ipynb)
