# Release notes

## 0.1.0

Initial public release line for Stockcast's DataFrame-first inventory decision,
simulation, and evaluation workflow.

The 0.1.x public contract freezes the documented import namespaces, public
outputs, metrics, and scientific behaviour. Future backward-compatible
additions and corrections will be recorded here. A breaking redesign requires
a later release line and an explicit migration note.

## Pre-release timing migration (2026-09-23)

Decisions now precede demand, lead time may be zero, and decision schedules are
separate from policy rules. This is an explicitly approved numerical behavior
change. See [migration details](concepts/decision-schedules.md). Older release
audits do not establish readiness of the migrated engine.

## Pre-release fixes (2026-09-24)

- `ShelfLifeEngine.run_comparison(...)` previously failed with a `TypeError`
  because it could not receive opening lots. It now accepts `opening_lots` and
  `opening_expiry_handling`, like `run`.
- Stock, backlog, and pipeline balance checks in the engine and in
  `validate_event_frame` used a fixed `1e-9` absolute tolerance. Valid runs
  with quantities around `1e7` (for example grams or millilitres) could fail
  on floating-point rounding. The tolerance now also allows `1e-12` times the
  magnitude of the flows in the row. Accounting results are unchanged.
- **Breaking, approved:** `ContinuousReviewPolicy` is replaced by
  `ReorderPointPolicy`. The old policy checked every period before demand but
  sized `s` over the lead time `L` only, and forced `s=0` when `L=0`. Under
  the current timing that under-protects. `ReorderPointPolicy(lead_time,
  review_period=... | schedule=..., policy_type="sQ"|"sS", ...)` takes its
  review timing from any `DecisionSchedule`.
  - Its `reorder_horizon` must equal `L+R` for periodic schedules (`L+1` for
    every-period review), or `(next opportunity - t) + L` for irregular
    schedules.
  - `service_level=None` selects planner mode for externally chosen `s`/`S`
    pairs.
  - `S` is a policy level with no probability or horizon of its own:
    `order_up_to_horizon`, `order_up_to_end_date_column` and
    `review_period_for_S` are removed.
  - Migration: construct with `review_period=1`, and recompute `s` over `L+1`
    periods and set `reorder_horizon=L+1`.

  Notebook 05b demonstrates the change.

## Order-level pipeline and suppliers (2026-09-24, additive)

Backward-compatible addition. Existing runs, primitives and outputs are
unchanged.

- `InventoryStateDataFrame.open_orders()` and `scheduled_receipts()` show the
  pipeline as open orders; `with_open_orders(...)` declares an opening
  pipeline with supplier, order period and partial deliveries.
- `OrderLines` and `stockcast.utils.place_order_lines` place order lines with
  their own supplier and due period in a manual loop.
- `SimulationEngine.run(..., supply=SupplyModel(...))` (also `run_comparison`
  and `ShelfLifeEngine`) splits accepted orders across `Supplier` objects with
  fixed or seeded random lead times and partial deliveries, using
  `SupplierShares` or a custom `SupplierAllocation`.
- `SimulationResult.to_order_frame()` lists every scheduled delivery
  (`ORDER_FRAME_COLUMNS`).

See [open orders and suppliers](guides/suppliers-and-open-orders.md) and
Notebook 05d.

## Inventory processes (2026-09-24, additive)

Backward-compatible addition. Existing runs, including `ShelfLifeEngine`
runs, produce the same event ledger, history, final state, callback audit,
run settings and manifest as before.

- `SimulationEngine.run(..., processes=[...])` (also `run_comparison`, and
  `ShelfLifeEngine` for extra processes) adds physical on-hand flows at
  defined phases: `before_demand`, `on_receipt` and `after_demand`.
  Processes subclass `InventoryProcess`, declare named `Flow`s and return
  `ProcessFlows`.
- `ShelfLife(shelf_life_days, opening_lots, ...)` is the FIFO shelf-life
  process. `ShelfLifeEngine` now runs it internally, and both forms give the
  same results.
- Ledgers from runs with general (non-expiry) process flows gain
  `process_inflow_units` and `process_outflow_units`, and these enter the
  physical-inventory identity in the engine and in `validate_event_frame`.
  Expiry flows add into `expired_units`.
- `SimulationResult.to_process_flow_frame()` lists every flow
  (`PROCESS_FLOW_COLUMNS`); `run_settings["processes"]` records each process.

See [physical processes](guides/physical-processes.md) and Notebook 05e.
