# 30 — Execution flow

## 30.1 Inputs to a run

`SimulationEngine.run` combines:

- a ready `InventoryStateDataFrame`;
- one fitted policy, with an optional decision-date policy schedule;
- a complete demand frame or one callable source;
- total, warmup, scoring, and settlement period counts;
- optional ordering constraints;
- an explicit first decision opportunity (pre-run purchases belong in opening pipeline);
- optional ordered typed callbacks;
- engine-owned shelf-life behavior.

It returns a `SimulationResult` containing state history, final inventory,
canonical event rows, run settings, and a provenance manifest.

## 30.2 Preflight: fail before mutation

Before period execution, the engine checks:

1. the total period count and that warmup + scoring + settlement equals it;
2. that the scoring window contains at least one period;
3. demand source name, seed, type, and generation settings;
4. that the policy is fitted and compatible with state settings;
5. state readiness, frequency, backorder mode, and initial-decision timing;
6. validate callback objects and JSON-serializable configuration without
   resetting caller state;
7. forecast origin/frequency and scheduled-policy decision dates;
8. the complete demand grid after materializing a callable once;
9. reset copied constraint state and create private deep copies of mutable run
   inputs;
10. reset the exact caller-supplied callback instances only after the other
    non-mutating preflight checks succeed and immediately before simulation
    execution.

Policy schedules are strict. A scheduled policy must be the same class and have
the same lead time, decision schedule, service level, and shortage mode as the base
policy. Only its fitted target data may differ. Its forecast origin must equal
the preceding information cutoff date and use the simulation frequency.

## 30.3 Decision eligibility

A `DecisionSchedule` determines opportunities in zero-based demand coordinates.
`review_period=R` is shorthand for `PeriodicSchedule(R, start=0)`. The former
separate opening-order switch is rejected with migration guidance; first-period
decisions are ordinary period events. Existing pre-run purchases are explicit
opening pipeline. Settlement suppression also suppresses `decision_flag`.

## 30.4 Exact period sequence

```text
copy opening state
  -> expire unusable lots and register due receipts in the FIFO ledger
  -> advance_period (reset flows, advance, receive, clear old backlog)
  -> if schedule eligible and ordering enabled:
       select fitted snapshot -> predict on defensive pre-demand state
       -> order callbacks -> constraints -> accept order
       -> immediate receipt for L=0; register new FIFO lots
  -> fulfill_demand (no second advance or receipt)
  -> reconcile FIFO fulfillment
  -> on_after_demand physical callbacks
  -> complete history and canonical event; validate balances
```

Prediction never sees current demand. `on_after_prediction` precedes demand;
`on_after_demand` affects subsequent decisions. Shelf-life lots received with
L=0 have the current demand date and can serve current demand. Expiry remains
before receipts/decisions; old backlog retains priority over current demand.

## 30.5 Review and order path

On a decision period:

1. the selected fitted policy reads inventory position;
2. it returns a raw requested `OrderDecision` without changing state;
3. ordered callbacks propose validated absolute order quantities;
4. `_tracked_inventory_update` passes the callback-adjusted request through constraints in their
   declared order;
5. raw, callback-adjusted, constrained, and final quantities are captured;
6. event/line/order-size counters are updated;
7. the accepted order enters the positive-lead pipeline or is received immediately for zero lead;
8. diagnostics such as target and safety stock flow into state and events.

The engine performs this path once per enabled decision opportunity. The
current callback result adjusts one composed decision; it does not add a second
supplier-specific order. Multiple applications can still accumulate through
the lower-level inventory operation, but a typed multi-supplier engine surface
is outside 0.1.0.

`order_event_count` represents a system-level event and is stored once on the
first SKU event row so that summing rows remains correct. SKU order-line counts
remain attached to their respective SKUs.

## 30.6 Window semantics

Each period receives exactly one of `warmup`, `scoring`, or `settlement`. The
engine records the window on event rows. `SimulationResult.summary` and normal
result-based evaluation use different interfaces: `summary()` always uses
`scoring`, while `InventoryEvaluator.fit(simulation_result=...)` requires an
explicit window. Warmup advances state before scoring. Settlement observes tail
effects without entering scoring and disables new review decisions by default
unless `order_during_settlement=True`.

## 30.7 Comparison execution

`run_comparison` validates scenario labels first, then materializes the demand
source once. Each scenario receives deep-copied inventory, policy, and
constraints but the same realized demand path. The result manifest marks the
comparison context. This supports paired scenario analysis without accidental
demand resampling or shared mutable stock. Subclasses that need extra run
inputs (`ShelfLifeEngine`: opening lots) forward them to each branch through
the private `_run_comparison(..., branch_run_options=...)`.
The exact callback instances are reset after branch preflight and before each
branch executes; authoritative branch-specific effects remain in each result's
callback audit.

## 30.8 Output provenance

The run manifest records, where available:

- run UUID and timestamp;
- demand source/type, row count, content fingerprint, seed, and generation
  provenance;
- policy configuration, forecast-target metadata, and target fingerprint;
- opening inventory fingerprint;
- windows, frequency, shortage mode, and other run settings;
- dependency versions;
- package version, source commit, and dirty state.

The current legacy source-commit helper reads a `.git` checkout directly. Installed
wheels do not normally contain `.git`, so commit/dirty provenance can become
`None`. A future package must decide how build-time provenance is embedded; it
must not claim that the current mechanism solves wheel provenance.

## 30.9 State ownership and reproducibility

The caller's original state and fitted policy are not the run's working objects.
Deep copying isolates scenarios and permits post-run inspection. Reproducibility
still requires callers to persist inputs, explicit demand-generation settings,
target data, and package provenance. A seed alone is not a complete run record.
