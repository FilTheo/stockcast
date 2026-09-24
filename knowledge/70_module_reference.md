# 70 — Module reference

This page accounts for every Python module in the candidate reusable source.
Paths are relative to `src/stockcast`.

## 70.1 Package surface

### `__init__.py`

The staging `stockcast` top-level surface provisionally preserves the inherited
exports for the main state, policy, engine, result, and constraint types and
exports the approved callback types and built-ins. It
does not export shelf-life classes, evaluator, metrics, demand generation, or
plots from the top level. Treat this as migration evidence, not a frozen public
API.

## 70.2 Core

### `core/__init__.py`

Exports `InventoryStateDataFrame`, `OrderDecision`, `BasePolicy`,
`SimulationEngine`, `SimulationResult`, `ComparisonResult`, all constraint
types, callback types/built-ins, `FIFOLotLedger`, and `ShelfLifeEngine`.

### `core/_array_state.py`

Private NumPy period kernel for `SimulationEngine`: `ArrayState` (array
mirror of one state, with advance, order, fulfilment and boundary
materialization), `StateSchema` (column order, index, constants and dtypes),
`DemandPath` (validated demand aligned to state SKU order), and the `RunLog`
that assembles `result.history` and the event ledger once. Every frame it
builds must equal the per-period pandas result exactly; states it cannot
represent stay DataFrames. Not a public or extension surface. See
[30.10](30_execution_flow.md#3010-internal-state-representation).

### `core/_open_orders.py`

Private open-order book (`_OpenOrderBook`, `_Deliveries`) kept in lockstep
with `in_transit`, its pipeline-consistency check, and assembly of the public
order frame (`ORDER_FRAME_COLUMNS`, `build_order_frame`). See
[97](97_open_orders_and_suppliers.md).

### `core/supply.py`

Public optional supplier stage: `Supplier` (fixed or discrete random lead
time, partial deliveries), `SupplierAllocation` and `AllocationContext`,
`SupplierShares`, and `SupplyModel` (validation, manifest, seeded per-run
lead-time draws, allocation validation, delivery scheduling). See
[97.4](97_open_orders_and_suppliers.md#974-engine-supply-stage).

### `core/callbacks.py`

Defines the public callback base, defensive context, typed inventory/order
results, callback error, and four schedule-oriented built-ins. It owns built-in
schedule validation and serializable callback configuration; the engine owns
application and scientific validation.

### `core/base_policy.py`

Defines `BasePolicy`, the fitted-policy protocol implemented through concrete
inheritance. It validates lead time, review period, service level, and explicit
backorder mode; base `fit` and `predict` raise until implemented.

### `core/data_structures.py`

Defines `InventoryStateDataFrame`, `OrderDecision` and `OrderLines`, plus the
state's order-level methods `with_open_orders`, `open_orders` and
`scheduled_receipts`. It owns identifier
validation, opening-state initialization, state-readiness validation, inventory
position, the receipt/backlog/demand transition, pipeline representation, and
decision-row validation. The private `_from_trusted` constructor and
`_DeferredHistory` list serve the engine's array kernel only. See
[20](20_data_and_time_contracts.md).

### `core/order_constraints.py`

Defines `ConstraintContext`, `ConstraintResult`, abstract
`OrderingConstraint`, orchestrator `OrderingConstraints`, and built-ins
`MinimumOrderQuantity`, `OrderMultiple`, `MaximumOrderQuantity`, and
`ShelfSpaceLimit`. It owns ordered application, adjustments, audits, manifests,
and final cross-validation. See [50](50_constraints_and_shelf_life.md).

### `core/shelf_life.py`

Defines `FIFOLotLedger` and `ShelfLifeEngine`. It owns dated-lot validation,
FIFO consumption, expiry timing, opening-lot fingerprints, and private engine integration
with canonical events. See [50.4](50_constraints_and_shelf_life.md#504-shelf-life-model).

### `core/simulation_engine.py`

Defines `SimulationEngine`, typed callback integration,
`SimulationResult`, and `ComparisonResult` behavior. It owns run preflight,
demand materialization, policy schedules, period ordering, constraints,
canonical event construction, callback audit, flow assertions,
comparison isolation, and run manifests. The private `_PeriodRun` executes
periods on `core/_array_state.py` arrays and falls back to the pandas period
path (kept here) for any state without an exact array form. The removed
legacy `after_step` hook is not part of the staging extension surface. See
[30](30_execution_flow.md).

## 70.3 Policies

### `policies/__init__.py`

Exports `OrderUpToPolicy`, `ReorderPointPolicy`, `PeriodicReviewPolicy`,
and periodic target provider types.

### `policies/_target_validation.py`

Internal shared validator for probabilities, quantile-like labels, exact SKU
coverage, direct-target source, horizons, schedule-derived windows, origins,
frequencies, target dates, and independent-normal per-step forecast frames. It is the central defense
against scientifically incompatible target inputs.

### `policies/order_up_to.py`

Defines `OrderUpToPolicy` for `(R,S)`. Supports direct external cumulative
targets and the explicitly labeled independent-normal moment aggregation.
Protects horizon `lead_time + review_period`.

### `policies/reorder_point.py`

Defines `ReorderPointPolicy` for `(s,Q)` and `(s,S)` rules under any
`DecisionSchedule`. It validates the reorder-point window against the schedule,
`L+R` or `(u-t)+L` through `_target_validation.schedule_protection_horizon` and
`validate_schedule_coverage`. It supports quantile and planner modes, validates
`Q` provenance and `S >= s`, and emits orders when inventory position reaches
the reorder point. It replaced `policies/continuous_review.py` on 2026-09-24
(knowledge 40.5).

### `policies/periodic_review.py`

Defines `PeriodicReviewPolicy` for `(R,s,S)`. Delegates fitted target creation
to a provider, validates its output and provenance, and applies the threshold
rule during engine-selected review periods.

### `policies/periodic_targets.py`

Defines `PeriodicReviewTargetProvider`, its normalized
`PeriodicReviewTargets` result, `ColumnPeriodicReviewTargets`, and
`FixedPeriodicReviewTargets`. It separates how target tables are supplied from
how the periodic policy acts on them.

## 70.4 Utilities

### `utils/__init__.py`

Exports `update_inventory_with_orders`, `process_demand`,
`DemandGenerator`, and `place_order_lines`. Its old docstring mentions broad forecasting utilities that
are not present; use actual exports as authority.

### `utils/inventory_operations.py`

Contains the only standard order-to-pipeline mutation (`_apply_orders`, used by
`update_inventory_with_orders`, `place_order_lines` and the engine supply stage)
and a thin wrapper around the state demand transition. It validates order timing and lead-time capacity,
normalizes sparse decisions, and preserves physical on-hand at order placement.

### `utils/demand_generator.py`

Defines `DemandGenerator` and its constant, normal, seasonal, trend, and
historical-normal-moment sources. It owns generation seed/frequency, scalar or
exact mapping parameters, negative-demand policy, callable/batch shapes, and
generation provenance.

## 70.5 Evaluation

### `evaluation/__init__.py`

Exports `InventoryEvaluator`, canonical event validation, metric base/classes,
and the full functional metric surface. The ambiguous historical name
`backorder_units_end` is absent; use `backlog_unit_periods` or
`terminal_backlog_units` according to the intended aggregation.

### `evaluation/event_validation.py`

Defines `CANONICAL_EVENT_COLUMNS` and `validate_event_frame`. It validates event
keys, types, quantities, timing and shortage-mode flags, order equivalence, and
physical/state balance equations.

### `evaluation/inventory_evaluator.py`

Defines `InventoryEvaluator`. It accepts a result or event frame, requires
explicit window/grouping choices, validates before evaluation, and coordinates
built-in or caller-supplied metrics.

### `evaluation/metrics.py`

Defines `BaseInventoryMetric`, `CoverageMetric`, and functional metrics for
demand/service, shortage/backlog, ordering/capacity, inventory, costs, terminal
state, and coverage. Exact semantics and required contexts are summarized in
[60.4](60_events_evaluation_and_outputs.md#604-metric-families).

## 70.6 Visualization

### `visualization/__init__.py`

Exports six plotting entry points: `plot_inventory`,
`plot_demand_vs_orders`, `plot_comparison`, `plot_summary_comparison`,
`plot_simulation_dashboard`, and `plot_comparison_dashboard`.

### `visualization/plots.py`

Normalizes event/history inputs, aggregates multi-SKU rows where needed, and
implements single-run/comparison plots and dashboards. Functions return axes
and do not own display or scientific validation.

## 70.7 Cross-module change map

| If changing… | Inspect together |
|---|---|
| state fields or timing | `data_structures.py`, `simulation_engine.py`, `event_validation.py`, metrics, plots |
| order timing | policies, callbacks, `inventory_operations.py`, engine event builder, constraints, event validator |
| pipeline representation | `data_structures.py`, `_open_orders.py`, `_array_state.py`, `inventory_operations.py`, `supply.py`, shelf-life receipts |
| target semantics | `_target_validation.py`, affected policy, engine policy schedule/manifest |
| event schema | engine event builder, shelf-life hook, validator, evaluator, all metrics, plots |
| shortage mode | state transition, engine event semantics, validator, service/cost metrics |
| shelf life | lot ledger, private engine phase order, callbacks, event balance, waste metrics |
| provenance | demand generator, policy metadata, engine manifest, packaging/build metadata |

## Decision timing additions

- `core/decision_schedule.py`: eligibility, next opportunity and schedule provenance.
- `policies/single_order.py`: one-season external targets and economic probability helper.
