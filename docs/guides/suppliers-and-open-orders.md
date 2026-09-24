# Open orders and suppliers

Stockcast can describe stock on order at two levels of detail. Both are public,
both run on the same engine, and they give identical accounting whenever they
describe the same pipeline.

| Level | What you work with | Use it when |
|---|---|---|
| Per SKU (default) | `in_transit` arrays, one `lead_time` per policy, `OrderDecision` | one source of supply with a fixed lead time |
| Order level (optional) | open orders, `OrderLines`, `SupplyModel` | several suppliers, random lead times, orders that arrive out of sequence, partial deliveries, or you need to know *which* order is still open |

Nothing changes for existing code: runs without the order-level objects
produce the same event ledger, history, final state and manifest as before.
The order-level API is an addition you can use when you need it.

Notebook 05d builds a café-chain example one step at a time.

## How the two levels relate

`in_transit` holds, per SKU, the quantity due in each future period: slot `i`
is received before demand in period `period + 1 + i`. That per-SKU view is
still what the accounting uses: inventory position, the event ledger's
`starting_on_order`, `received_units` and `on_order_end`, and the balance
checks.

The order level records the orders that make up those quantities, and keeps
the two in step. For every SKU and slot, the remaining quantity of the open
deliveries due in that period equals `in_transit[i]`.

Every per-SKU concept has an order-level form:

| Per SKU | Order level |
|---|---|
| `in_transit = [3, 0, 4]` at period `p` | open deliveries of 3 due at `p+1` and 4 due at `p+3` |
| `update_inventory_with_orders(state, orders)` with lead time `L` | `place_order_lines(state, OrderLines(...))`, one line per positive SKU order, `due_period = order_period + L` |
| `engine.run(..., supply=None)` | `engine.run(..., supply=SupplyModel([Supplier("s", lead_time=policy.lead_time)]))` |

The last two rows give identical accounting, so you can move to the order
level without changing any result. `tests/unit/test_open_orders_and_supply.py`
checks this across lead times 0 to 3, both shortage modes, periodic review,
constraints, callbacks and shelf life, and requires identical event ledgers,
history and final state. The order frame then differs only in `supplier_id`.

## Open orders on a state

Every state can list its open orders. A pipeline given only as `in_transit`
appears as opening orders with unknown supplier and order period. Stockcast
does not invent them:

```python
state.open_orders()        # one row per open order line
state.scheduled_receipts() # one row per open delivery
```

`open_orders()` columns are `order_id`, the SKU column, `supplier_id`,
`source`, `order_period`, `ordered_quantity`, `remaining_quantity`,
`due_period` (next delivery) and `final_due_period`.

To declare the opening pipeline at order level, call `with_open_orders` after
initialization. Each row is one scheduled delivery:

```python
state = sc.InventoryStateDataFrame(skus, max_lead_time=8, allow_backorders=False)
state.initialize_from_observed(stock, on_hand_column="on_hand", start_date=opening)
state.with_open_orders(pd.DataFrame({
    "unique_id":   ["beans", "beans", "beans"],
    "supplier_id": ["roaster", "importer", "importer"],
    "order_id":    ["PO-17", "PO-12", "PO-12"],   # PO-12 arrives in two parts
    "order_period": [-1, -4, -4],
    "due_period":  [1, 2, 5],
    "quantity":    [20.0, 30.0, 10.0],
}))
```

Required columns are the SKU column, `due_period` and `quantity`. Optional
columns are `supplier_id`, `order_period`, `order_id` (groups the deliveries of
one order line) and `ordered_quantity`. The following are rejected:

- a due period outside `period + 1 .. period + max_lead_time`;
- an unknown SKU;
- a quantity that is not positive or not finite;
- an order period after the current period;
- grouped rows that disagree on SKU, supplier or order period.

If `in_transit` is still empty, it is filled from these rows. If it already
holds quantities, the rows must reproduce them exactly: they then only say
which orders the existing pipeline consists of. Once open orders are declared,
editing `in_transit` directly is rejected at the next validation, because the
two would disagree. Calling `initialize_zero` again starts from an empty
pipeline.

## Manual loops: `OrderLines` and `place_order_lines`

`OrderLines` holds one row per scheduled delivery. A SKU may appear several
times, and rows with the same `order_line` are parts of one order line:

```python
from stockcast.utils import place_order_lines

state = state.advance_period(period_frequency="D", is_review_period=True)
period = int(state.data["period"].iloc[0])
lines = sc.OrderLines(pd.DataFrame({
    "unique_id":      ["beans", "beans", "beans"],
    "supplier_id":    ["roaster", "importer", "importer"],
    "order_quantity": [15.0, 40.0, 20.0],
    "order_period":   [period] * 3,
    "due_period":     [period, period + 5, period + 7],
    "order_line":     [0, 1, 1],
}))
state = place_order_lines(state, lines)
state = state.fulfill_demand(demand_today)
```

A delivery due in the current period is received immediately. It clears old
backlog first, as a zero-lead-time order does, and can serve that period's
demand. Later deliveries go into the `in_transit` slot of their due period.
`latest_order` adds up each SKU's total. `target_level` is not changed,
because order lines carry no policy target. Every positive row must be placed
in the current period and be due no later than `period + max_lead_time`.

## Engine runs: `SupplyModel`

With `supply=`, the engine adds one stage after order callbacks and
constraints:

```text
policy -> order callbacks -> constraints -> supply: allocation, lead time,
delivery split -> pipeline or immediate receipt
```

```python
supply = sc.SupplyModel(
    suppliers=[
        sc.Supplier("roaster", lead_time=1),
        sc.Supplier(
            "importer",
            lead_time={5: 0.6, 6: 0.25, 8: 0.15},   # random, integer periods
            partial_deliveries=[(0, 0.7), (2, 0.3)], # 70% on arrival, rest 2 periods later
        ),
    ],
    allocation=sc.SupplierShares({"roaster": 0.3, "importer": 0.7}),
    random_seed=2026,
)
result = engine.run(policy, demand, state, n_periods, ..., supply=supply)
result.to_order_frame()
```

- **Suppliers.** A `Supplier` has an id, a lead time and an optional split into
  partial deliveries. The lead time is either a fixed integer `>= 0` or a
  mapping of integer lead times to probabilities that sum to 1. Each delivery
  is received at the start of its due period, before demand, exactly like
  per-SKU receipts.
- **Allocation.** `SupplierShares` splits each SKU's accepted quantity by fixed
  fractions, either for all SKUs or per SKU through `by_sku`. The last
  positive share gets the remainder, so the lines add up to the order. With a
  single supplier, the allocation may be omitted.
- **Custom allocation.** Subclass `SupplierAllocation` and implement
  `allocate(orders, context)`. `context` is an `AllocationContext` with
  defensive copies of the state frame and its open orders, plus the period,
  date and supplier ids. Return one row per SKU and supplier with
  `order_quantity`. The engine rejects:
  - unknown suppliers;
  - SKUs without a positive order;
  - duplicate rows;
  - negative quantities;
  - totals that differ from the accepted order.
- **Randomness.** A random lead time requires an explicit `random_seed`. The
  engine draws every lead time once per run, one value per decision period,
  SKU and supplier, with a separate stream per supplier. Branches of
  `run_comparison` therefore see the same lead time for the same order slot,
  which keeps the comparison paired.
- **Capacity of the state.** `inventory.max_lead_time` must cover
  `supply.max_delivery_offset`, the longest lead time plus the longest
  partial-delivery delay. Otherwise the run is rejected before any state
  changes.

Policies, callbacks and constraints are unchanged. They still see one order
quantity per SKU. The policy's `lead_time` is the *planning* lead time used for
its target; the supply model decides when the goods actually arrive.

### What the event ledger records

The canonical event columns keep their meaning. Per SKU and period:

- `order_quantity` is the accepted quantity, equal to
  `constrained_order_quantity`;
- `received_units` is the sum of all deliveries received;
- `on_order_end` is the sum of open deliveries.

With `supply`, `sku_order_line_count` counts supplier order lines, so an
order split between two suppliers counts two lines.
`order_line_quantity_squared_sum` uses the line quantities. A partial delivery
does not create an extra line. Without `supply`, both columns are unchanged.

`ShelfLifeEngine` accepts `supply=` as well. All of a SKU's deliveries received
in the same period form one dated lot.

## The order frame

`SimulationResult.to_order_frame()` returns one row per scheduled delivery:
the opening pipeline and every order placed in the run. Columns
(`stockcast.core.ORDER_FRAME_COLUMNS`):

| Column | Meaning |
|---|---|
| `order_id` | order line; the deliveries of one line share it |
| `unique_id`, `supplier_id` | SKU and supplier (`None` when not known, e.g. without `supply`) |
| `source` | `opening` or `placed` |
| `order_period`, `order_date` | when the line was ordered (missing for opening orders without a declared order period) |
| `due_period`, `due_date` | when this delivery is received, before that period's demand |
| `lead_time` | realized `due_period - order_period` |
| `ordered_quantity` | quantity of the whole order line |
| `delivery_quantity` | quantity of this delivery |
| `status` | `received` or `open` at the end of the run |

Received deliveries add up to `received_units` per SKU and period, and open
deliveries add up to the final `on_order_end`. Use this frame for supplier-level
analysis: realized lead times, the share of volume per supplier, or how many
orders were still open at the end of the run. Supplier-specific costs are not
part of the built-in metrics; compute them from this frame with your own
declared prices.

## Current scope

- Deliveries are scheduled when an order is placed. Delays or cancellations
  after placement, supplier capacity and minimum order quantities per supplier
  are not modelled. Constraints apply to each SKU's total before allocation.
- Every supplier delivers to the same stocking point; there is no multi-echelon
  network. `SupplierAllocation` and the order frame are the extension points
  for that kind of work later.
- The manifest records `run_settings["supply"]`: suppliers, lead-time
  specifications, allocation class and configuration, and the seed. Declared
  opening orders add an `opening_inventory["open_orders"]` fingerprint.
