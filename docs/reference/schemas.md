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

`event_type` is either `period` or `initial_decision`. A period event has a
non-negative `demand_period`; the time-zero initial-decision event has no demand
period. `run_window` is `warmup`, `scoring`, or `settlement`.

## Callback audit frame

`CALLBACK_AUDIT_COLUMNS` defines the stable callback-audit columns:
`callback_position`, `callback_module`, `callback_class`, `phase`, `period`,
`date`, `run_window`, `initial_decision`, `unique_id`, `before_value`,
`after_value`, `quantity_delta`, `order_quantity`, `reason`, `source`,
`received_date`, and `lot_evidence`.

## Run manifest

`run_manifest` has stable top-level sections: `run_id`, `created_at_utc`,
`demand_source`, `package`, `policy`, `opening_inventory`, `run_settings`, and
`dependencies`. Nested descriptive values may grow in a patch release. A source
commit may be unavailable in an installed artifact; missing provenance should
be interpreted honestly rather than filled in.

New-engine period events additionally include `decision_inventory_position`, the
pre-demand inventory position presented to the policy, or missing when no
decision occurred. Existing canonical accounting fields retain their meanings.
