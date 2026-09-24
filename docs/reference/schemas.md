# Data and output schemas

## Input conventions

The standard demand columns are `unique_id`, `period`, `date`, and `y`.
Policies and state constructors document their specific required columns in the
[generated API reference](index.md). The same SKU identifier type must be used
throughout a scenario.

## Canonical event ledger

`CANONICAL_EVENT_COLUMNS` fixes all listed column names and meanings for 0.1.x.
New columns may be added; these columns are not silently removed, renamed, or
repurposed.

| Group | Columns |
|---|---|
| Identity and timing | `unique_id`, `event_type`, `demand_period`, `period`, `date`, `policy`, `run_window` |
| Run flags | `allow_backorders`, `is_review_period`, `decision_flag`, `stockout_flag`, `backorder_flag` |
| Starting state | `starting_on_hand`, `starting_backorders`, `starting_on_order` |
| Demand and stock flow | `received_units`, `demand`, `fulfilled_units`, `backorders_fulfilled`, `shortage_units`, `lost_sales_units`, `backorder_increment`, `expired_units`, `inventory_adjustment_units`, `ending_on_hand`, `backorders_end`, `on_order_end`, `inventory_position_end` |
| Decision and target | `order_quantity`, `order_event_count`, `sku_order_line_count`, `order_line_quantity_squared_sum`, `target_level`, `safety_stock` |
| Callback evidence | `requested_order_quantity`, `callback_adjustment_units`, `callback_adjusted_order_quantity` |
| Constraint evidence | `constrained_order_quantity`, `constraint_adjustment_units`, `constraint_binding_flag`, `capacity_violation_flag`, `binding_constraints` |

Runs whose [inventory processes](../guides/physical-processes.md) declare
general (non-expiry) flows add two optional columns, `process_inflow_units`
and `process_outflow_units`. They appear together, are nonnegative, and enter
the physical-inventory balance checked by `validate_event_frame`. Expiry
processes add into `expired_units`.

`event_type` is either `period` or `initial_decision`. A period event has a
non-negative `demand_period`; the time-zero initial-decision event has no demand
period. `run_window` is `warmup`, `scoring`, or `settlement`.

## Callback audit frame

`CALLBACK_AUDIT_COLUMNS` defines the stable callback-audit columns:
`callback_position`, `callback_module`, `callback_class`, `phase`, `period`,
`date`, `run_window`, `initial_decision`, `unique_id`, `before_value`,
`after_value`, `quantity_delta`, `order_quantity`, `reason`, `source`,
`received_date`, and `lot_evidence`.

## Order frame

`SimulationResult.to_order_frame()` returns one row per scheduled delivery of
the opening pipeline and of every order placed in a run. `ORDER_FRAME_COLUMNS`
lists the columns: `order_id`, `unique_id`, `supplier_id`, `source`,
`order_period`, `order_date`, `due_period`, `due_date`, `lead_time`,
`ordered_quantity`, `delivery_quantity`, and `status`. Received deliveries add
up to `received_units` per SKU and period; open ones add up to the final
`on_order_end`. See [open orders and suppliers](../guides/suppliers-and-open-orders.md).

## Process flow frame

`SimulationResult.to_process_flow_frame()` returns one row per nonzero process
flow, SKU and period. `PROCESS_FLOW_COLUMNS` lists the columns: `unique_id`,
`period`, `date`, `demand_period`, `run_window`, `process`, `flow`,
`direction`, `category`, `phase`, and `quantity`. Expiry rows add up to
`expired_units`; general inflows and outflows add up to `process_inflow_units`
and `process_outflow_units`. The frame is empty for a run without processes.

## Run manifest

`run_manifest` has stable top-level sections: `run_id`, `created_at_utc`,
`demand_source`, `package`, `policy`, `opening_inventory`, `run_settings`, and
`dependencies`. Nested descriptive values may grow in a patch release. A source
commit may be unavailable in an installed artifact; missing provenance should
be interpreted honestly rather than filled in. A run with `supply=` adds
`run_settings["supply"]`; declared opening orders add
`opening_inventory["open_orders"]`. A run with `processes=` adds
`run_settings["processes"]`. Runs without them are unchanged.

New-engine period events additionally include `decision_inventory_position`, the
pre-demand inventory position presented to the policy, or missing when no
decision occurred. Existing canonical accounting fields retain their meanings.
