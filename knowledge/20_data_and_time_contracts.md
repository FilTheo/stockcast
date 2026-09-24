# 20 — Data and time contracts

## 20.1 Inventory state schema

`InventoryStateDataFrame` is the authoritative live-state object. It keeps one
row per SKU and uses `unique_id` by default as the identifier column.

| Field | Meaning |
|---|---|
| `on_hand` | usable physical units after the latest transition |
| `backorders` | unmet demand carried forward in backorder mode |
| `in_transit` | per-SKU array; index `0` is the next receipt slot (accounting view; the order-level view is in 97) |
| `period`, `date` | current discrete-period coordinate and timestamp |
| `is_review_period` | whether the current period permits a policy decision |
| `target_level`, `safety_stock` | policy diagnostics copied into state/events |
| `latest_order` | order quantity placed at the current decision |
| `latest_received` | due pipeline receipts plus accepted zero-lead receipts this period |
| `latest_incoming_demand` | demand presented this period |
| `latest_fulfilled` | current demand served from stock |
| `latest_shortage` | current demand not served immediately |
| `latest_backorders_fulfilled` | old backlog cleared by receipts |

The constructor may default transient “latest” flow columns to zero. It does
not infer the scientific opening state. Before simulation, readiness validation
requires one common integer period, one common date, valid pipeline arrays of
the configured maximum lead time, finite nonnegative quantities, and mode
consistency.

## 20.2 Identifiers

Identifiers are deliberately not coerced. All IDs in one object must be
hashable, nonblank, unique, and of one exact Python type. Joins require exact
SKU-set agreement where completeness is scientifically material. This prevents
silent collisions such as integer `1` and string `"1"`, or a missing SKU being
mistaken for zero demand.

## 20.3 Explicit initialization

There are two accepted starting patterns:

- `initialize_zero(...)` declares an intentionally empty system: zero on-hand,
  zero backlog, and an empty pipeline.
- `initialize_from_observed(...)` requires an exact SKU set, complete finite
  nonnegative observed on-hand values, and an explicit date. It starts backlog
  and pipeline at zero.

There are no forecast-based or historical-data initializer methods. Earlier
heuristic initialization was removed; callers must not derive an undocumented
opening stock level from forecasts or history. A future initializer requires a
new explicit contract with complete inputs and provenance.

## 20.4 Time model

Simulation uses integer periods plus a pandas-compatible forward frequency.
For a run starting from state date `D0`, demand period `0` occurs at
`D0 + 1 * frequency`, period `1` at `D0 + 2 * frequency`, and so on. Demand
must cover every SKU for every requested period with the exact expected dates.

The engine opens the demand epoch before deciding. Decision schedules use the
zero-based demand coordinate; event/state/callback periods retain the opening
period plus one convention. `policy_schedule` keys use demand coordinates and
snapshot origins use the last observed date (one offset before current demand).

Lead time `L >= 0`. A positive order at state period `t` arrives before demand
at `t+L`, stored in pipeline slot `L-1` after today's receipt shift. For `L=0`,
accepted units immediately count as receipts and clear old backlog before
current demand. Empty pipeline arrays (`max_lead_time=0`) are valid.

## 20.5 Demand input

The engine accepts either a complete demand DataFrame or a callable demand
source. A callable is materialized exactly once before mutation begins. The
preflight contract requires:

- periods exactly `0..n_periods-1`;
- one row for every SKU-period pair;
- exact calendar dates;
- finite nonnegative demand;
- no duplicate SKU-period observations;
- explicit source name and generation/provenance fields where applicable.

`DemandGenerator` supports constant, normal, seasonal, trend, and historical
normal-moment generation in batch or callable form. Scalar settings may apply
to all SKUs; mappings must cover the exact SKU set. Negative generated demand
is either rejected or clipped to zero according to the explicit
`negative_demand_handling` mode. The implementation default is rejection; do
not rely on an older docstring suggesting unconditional clipping.

## 20.6 One demand transition

`advance_period` resets current flow fields, advances period/date, receives
pipeline slot zero, shifts the pipeline, and clears old backlog before adding
remaining receipts to stock. The engine can then request and apply an order.
`fulfill_demand` validates and serves the complete current-date SKU vector
without advancing time or receiving again. It records fulfillment, shortage,
and backlog increments (or lost sales).

`process_demand` remains a convenience composition of those two phases without
an intervening decision. Manual before-demand loops must use the separate
phases. No phase guesses missing demand or dates.

## 20.7 Order decision contract

`OrderDecision` contains one row per explicitly returned SKU and requires a
finite nonnegative `order_quantity`. It carries the policy's `lead_time` and
`review_period`, plus timing and diagnostic columns such as order period,
expected delivery, target, and safety stock.

When an order is applied:

- unknown SKUs are rejected;
- omitted known SKUs are normalized to zero orders;
- every positive order must have order period equal to the current state period;
- expected delivery must equal order period plus lead time;
- the state pipeline must be long enough for that lead time;
- positive-lead quantity is added to pipeline index `L - 1`;
- zero-lead quantity is received immediately, with backlog clearance recorded;
- `latest_order` accumulates, allowing multiple supplier/order events in one
  decision period when the low-level inventory operation is invoked more than
  once;
- positive-lead placement leaves on-hand unchanged; zero-lead placement adds usable receipts.

In 0.1.0, `SimulationEngine` makes one policy prediction and places one
composed `OrderDecision` per enabled decision opportunity. Order callbacks may
adjust that decision but cannot create additional supplier-specific decisions.
The low-level accumulation rule is retained as a state primitive. Since
2026-09-24 an optional, additive order-level layer
([97](97_open_orders_and_suppliers.md)) adds supplier identity, per-line due
periods, seeded random lead times and partial deliveries: `OrderLines` with
`place_order_lines`, and `SimulationEngine(..., supply=SupplyModel(...))`.
The supply stage runs after constraints and splits the accepted per-SKU
quantity; the contract above is unchanged when it is not used.

## 20.8 Balance equations

For each canonical SKU-period row:

```text
demand = fulfilled_current_demand + shortage

ending_on_hand
  = starting_on_hand
  + received
  - old_backorders_fulfilled
  - current_demand_fulfilled
  - expired
  + explicit_inventory_adjustment
  + process_inflow - process_outflow   (only when the columns exist; see 98.3)

ending_backorders
  = starting_backorders
  + new_backorder_increment
  - old_backorders_fulfilled

ending_pipeline
  = starting_pipeline - receipts + orders_placed
  (per SKU; with supply the supplier lines sum to orders_placed within tolerance)

ending_inventory_position
  = ending_on_hand + ending_pipeline - ending_backorders
```

These are executable validation rules, not explanatory approximations.
