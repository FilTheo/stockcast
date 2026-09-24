# 93 — Stockcast 0.1 public API contract

Status: the 2026-08-25 freeze is amended by the owner-approved 2026-09-23
pre-release timing migration in knowledge 95, and by the owner-approved
2026-09-24 replacement of `ContinuousReviewPolicy` with `ReorderPointPolicy`
(knowledge 40.5; exports in `test_public_api_contract.py`). This is the agent-facing release contract. Public documentation must
explain this contract from the active source, tests, and examples; it must not
create a broader promise.

## 93.1 Compatibility promise

For the `0.1.x` series, Stockcast will not intentionally remove, rename, or
reinterpret a public import, required call argument, output field, metric, or
scientific behavior defined here. Backward-compatible additions and fixes to
demonstrably incorrect behavior are allowed, but must be documented and tested.
A breaking redesign requires a later release line and an explicit migration
decision.

Private helpers, underscore-prefixed names, and direct imports from
implementation modules are not part of this promise.

## 93.2 Public import surface

The exact exports of the following namespaces are public and are locked by
`tests/unit/test_public_api_contract.py`:

- `stockcast`: the concise main workflow and supported extension objects in its
  `__all__`;
- `stockcast.core`: specialist state, engine, constraint, callback, and
  shelf-life objects in its `__all__`;
- `stockcast.policies`: built-in policies and periodic-target provider objects;
- `stockcast.evaluation`: evaluator, event validation/schema constant, and all
  exported metric functions/classes;
- `stockcast.utils`: demand generation and supported manual-loop primitives;
- `stockcast.visualization`: all six exported plotting functions.

The top-level namespace is the normal starting point. The specialist namespaces
above are endorsed public imports, not implementation details. For example,
`ShelfLifeEngine` is imported from `stockcast.core`, evaluation from
`stockcast.evaluation`, and plots from `stockcast.visualization`.

## 93.3 Supported extensions

The following are first-class 0.1.0 extension workflows:

- custom `BasePolicy` subclasses implement `fit(...)`, set `fitted_`, and
  return an `OrderDecision` from `predict(...)`; policy code requests an order
  and never mutates engine-owned inventory;
- custom `PeriodicReviewTargetProvider` objects return validated
  `PeriodicReviewTargets` with serializable metadata;
- custom `OrderingConstraint` objects transform a requested decision through
  the typed constraint context/result contract; and
- custom `SimulationCallback` objects use only `on_after_demand(...)` and
  `on_after_prediction(...)`, returning the appropriate typed adjustment
  result. They never receive live mutable engine state or finalized events.

The removed `after_step` hook, forecast/history opening-state heuristics, and
the ambiguous `backorder_units_end` metric are not public APIs.

## 93.4 Durable run outputs

`SimulationResult.to_event_frame(window=...)` is the authoritative accounting
output for a run. It returns the canonical event ledger: one row per SKU and
period, including first-period decisions. Historical opening-decision rows
remain recognizable by the evaluator. Every
column in `stockcast.evaluation.CANONICAL_EVENT_COLUMNS` is required, with its
current name and scientific meaning stable throughout `0.1.x`. Additive new
columns are permitted; existing columns must not be silently renamed,
repurposed, or removed.

The ledger's validated demand, physical-stock, backlog, pipeline, inventory
position, callback, and constraint identities remain part of the public
contract. Metrics and downstream analyses should use this ledger, not
caller-owned history snapshots.

`SimulationResult.to_callback_audit_frame()` is also durable output. Its
columns are defined by `stockcast.core.CALLBACK_AUDIT_COLUMNS` and may be used to
inspect accepted typed intervention effects.

`SimulationResult.run_manifest` is the durable reproducibility receipt. The
top-level sections in `stockcast.core.RUN_MANIFEST_REQUIRED_SECTIONS` are stable:
`run_id`, `created_at_utc`, `demand_source`, `package`, `policy`,
`opening_inventory`, `run_settings`, and `dependencies`. Their documented core
meaning remains stable; nested descriptive fields may be added. Provenance may
honestly be unavailable—for example, a wheel need not contain a source commit.

`SimulationResult.summary()` remains a compact scoring-window convenience
summary, not a replacement for the ledger and manifest.

## 93.5 Required scientific behavior

The public API preserves the contracts in 20, 30, 40, 50, and 60: explicit
opening state/demand/timing/provenance; nonnegative lead time and before-demand decisions; engine-owned timing
and mutation; fail-closed target validation; no heuristic uncertainty; no
summation of marginal forecast quantiles; validated event balances; and
explicit evaluation windows, grouping, and costs.

## 93.6 Evidence and completion

The freeze is enforced by public-export checks in
`tests/unit/test_public_api_contract.py`, output-schema checks in
`tests/unit/test_inventory_evaluation.py`, and the relevant behavior tests
across `tests/unit/`. Run:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  /home/filtheo/inventory/.venv/bin/python -m pytest \
  -p no:cacheprovider -q -o addopts='' tests/unit
```

The source suite is contract evidence only. Before publication, separately
repeat the public-import, example, and output checks from a clean installed
wheel and source distribution.

## 93.7 Additive order-level API (2026-09-24)

Owner-approved backward-compatible addition ([97](97_open_orders_and_suppliers.md)):

- exports appended to `stockcast`: `OrderLines`, `Supplier`,
  `SupplierAllocation`, `SupplierShares`, `SupplyModel`;
- exports appended to `stockcast.core`: those five plus `AllocationContext`
  and `ORDER_FRAME_COLUMNS`;
- exports appended to `stockcast.utils`: `place_order_lines`;
- new optional keyword `supply=None` on `SimulationEngine.run`,
  `run_comparison` and the `ShelfLifeEngine` equivalents;
- new state methods `with_open_orders`, `open_orders`, `scheduled_receipts`;
- new result method `SimulationResult.to_order_frame()`, whose columns are
  fixed by `ORDER_FRAME_COLUMNS`.

No existing name, argument, output column or behavior changed. A run with
`supply=None` produces the same event ledger, history, final state, callback
audit, run settings and manifest as before. `supply` adds
`run_settings["supply"]`. Declared opening orders add
`opening_inventory["open_orders"]`.

## 93.8 Additive inventory-process API (2026-09-24)

Owner-approved backward-compatible addition ([98](98_inventory_processes.md)):

- exports appended to `stockcast.core` only: `Flow`, `InventoryProcess`,
  `PROCESS_FLOW_COLUMNS`, `ProcessContext`, `ProcessFlows`, `ShelfLife`,
  `StockChange`;
- new optional keyword `processes=None` on `SimulationEngine.run`,
  `run_comparison` and the `ShelfLifeEngine` equivalents;
- new result method `SimulationResult.to_process_flow_frame()` with columns
  fixed by `PROCESS_FLOW_COLUMNS`;
- optional event-ledger columns `process_inflow_units` and
  `process_outflow_units`, present together only for runs with general
  process flows and included in `validate_event_frame`'s physical balance;
- `run_settings["processes"]` when `processes=` is used.

No existing name, argument, output column or behavior changed.
`ShelfLifeEngine` keeps its signature, attributes (`ledger`,
`expired_this_period`, `shelf_life_days`) and outputs. Its private lifecycle
hook overrides were removed; they were never public.
