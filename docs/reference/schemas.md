# Output tables

The tables a run produces, column by column. For 0.1.x, the listed columns
keep their names and meanings; new columns may be added.

## Demand input

| Column | Type | Meaning |
|---|---|---|
| `unique_id` | hashable | SKU identifier (the state's `sku_column`) |
| `period` | int | demand period, `0 … n_periods − 1` |
| `date` | timestamp | opening date $+ (\text{period} + 1)\,\Delta$ |
| `y` | float $\ge 0$ | units demanded |

## Event ledger

`SimulationResult.to_event_frame()`. One row per SKU and period. The required
columns are listed in `stockcast.evaluation.CANONICAL_EVENT_COLUMNS`.

### Identity and timing

| Column | Meaning |
|---|---|
| `unique_id` | SKU |
| `event_type` | `"period"` for runs of this engine (`"initial_decision"` is recognised in older ledgers) |
| `demand_period` | zero-based demand period, matching the demand table |
| `period` | state period (opening period + demand period + 1) |
| `date` | the period's date |
| `policy` | the policy's name |
| `run_window` | `"warmup"`, `"scoring"`, or `"settlement"` |

### Flags

| Column | Meaning |
|---|---|
| `allow_backorders` | shortage rule of the run |
| `is_review_period` | whether the schedule allowed a decision this period |
| `decision_flag` | whether a decision was made |
| `stockout_flag` | `shortage_units > 0` |
| `backorder_flag` | `backorders_end > 0` |

### Stock flows

| Column | Meaning |
|---|---|
| `starting_on_hand`, `starting_backorders`, `starting_on_order` | state at the start of the period |
| `received_units` | units received this period (pipeline and zero-lead-time) |
| `demand` | units demanded |
| `fulfilled_units` | this period's demand served from stock |
| `backorders_fulfilled` | older backorders served from this period's receipts |
| `shortage_units` | demand not served this period |
| `lost_sales_units` | shortage that left (lost-sales mode) |
| `backorder_increment` | shortage added to backorders (backorder mode) |
| `expired_units` | stock removed by expiry |
| `inventory_adjustment_units` | signed stock change from `on_after_demand` callbacks |
| `ending_on_hand`, `backorders_end`, `on_order_end` | state at the end of the period |
| `inventory_position_end` | `ending_on_hand + on_order_end - backorders_end` |

### Decision

| Column | Meaning |
|---|---|
| `order_quantity` | accepted order quantity |
| `order_event_count` | 1 on one row of a period in which any positive order was placed, else 0 (so sums count decisions) |
| `sku_order_line_count` | positive order lines for this SKU (supplier lines with a supply model) |
| `order_line_quantity_squared_sum` | sum of squared line quantities (for order-size variance) |
| `target_level`, `safety_stock` | policy diagnostics, when the policy provides them |
| `decision_inventory_position` | inventory position the policy saw before demand; missing without a decision |

### Order trail

| Column | Meaning |
|---|---|
| `requested_order_quantity` | what the policy proposed |
| `callback_adjustment_units` | change made by order callbacks |
| `callback_adjusted_order_quantity` | after callbacks |
| `constraint_adjustment_units` | change made by ordering constraints |
| `constrained_order_quantity` | after constraints (equals `order_quantity`) |
| `constraint_binding_flag` | whether any constraint changed the order |
| `capacity_violation_flag` | whether a capacity constraint cut the order |
| `binding_constraints` | names of the constraints that changed the order, joined by `|` |

### Optional columns

| Column(s) | Present when | Enters |
|---|---|---|
| `process_inflow_units`, `process_outflow_units` | a process declares general (non-expiry) flows | on-hand balance |
| `supplier_shortfall_units` | a supplier has a `DeliveryOutcome` | pipeline balance: units due that will never arrive |

`validate_event_frame` checks all balance identities, including these columns
when present. See [Stock accounting](../user-guide/concepts/accounting.md).

## Order frame

`SimulationResult.to_order_frame()`. One row per scheduled delivery.
`stockcast.core.ORDER_FRAME_COLUMNS`:

| Column | Meaning |
|---|---|
| `order_id` | order line; deliveries of one line share it |
| `unique_id`, `supplier_id` | SKU and supplier (`None` when unknown) |
| `source` | `"opening"` or `"placed"` |
| `order_period`, `order_date` | when the line was ordered (missing for opening orders without one) |
| `due_period`, `due_date` | when this delivery is received, before that period's demand |
| `lead_time` | realised `due_period - order_period` |
| `ordered_quantity` | quantity of the whole order line |
| `delivery_quantity` | quantity of this delivery |
| `status` | `"received"`, `"open"`, or (with delivery outcomes) `"disrupted"` |

Runs with a `DeliveryOutcome` add `scheduled_due_period`,
`received_quantity`, `delayed_quantity`, and `undelivered_quantity`. Received
deliveries add up to `received_units` per SKU and period; open ones to the
final `on_order_end`.

## Callback audit

`SimulationResult.to_callback_audit_frame()`. One row per accepted callback
effect. `stockcast.core.CALLBACK_AUDIT_COLUMNS`:

| Column | Meaning |
|---|---|
| `callback_position`, `callback_module`, `callback_class` | which callback |
| `phase` | `"on_after_prediction"` or `"on_after_demand"` |
| `period`, `date`, `run_window`, `initial_decision` | when |
| `unique_id` | SKU |
| `before_value`, `after_value`, `quantity_delta` | the order quantity (prediction phase) or on-hand stock (demand phase) before and after |
| `order_quantity` | the resulting order quantity (prediction phase) |
| `reason`, `source` | the explanation supplied by the callback |
| `received_date`, `lot_evidence` | lot information for stock added under shelf life |

## Process flows

`SimulationResult.to_process_flow_frame()`. One row per non-zero flow, SKU,
and period. `stockcast.core.PROCESS_FLOW_COLUMNS`: `unique_id`, `period`,
`date`, `demand_period`, `run_window`, `process`, `flow`, `direction`
(`inflow`/`outflow`), `category` (`general`/`expiry`), `phase`, `quantity`.
Expiry rows add up to `expired_units`; general flows to
`process_inflow_units` and `process_outflow_units`.

## Run manifest

`SimulationResult.run_manifest`. A JSON-friendly dict whose top-level sections
are listed in `stockcast.core.RUN_MANIFEST_REQUIRED_SECTIONS`:

| Section | Contents |
|---|---|
| `run_id`, `created_at_utc` | unique id and creation time |
| `demand_source` | name, type, SHA-256 fingerprint, row count, seed, generator settings |
| `package` | Stockcast version and, when available, the source commit |
| `policy` | class, configuration, schedule, target metadata, target fingerprint |
| `opening_inventory` | fingerprint of the opening state; open orders when declared |
| `run_settings` | frequency, windows, timing convention, schedule, policy updates, constraints, callbacks; `supply` and `processes` when used |
| `dependencies` | Python, NumPy, pandas, and Matplotlib versions |

Nested fields may grow in later releases. Provenance that is not available
(for example a source commit in an installed wheel) is left empty rather than
guessed.
