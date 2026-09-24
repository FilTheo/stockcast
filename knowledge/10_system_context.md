# 10 — System context

## 10.1 What the current legacy system does

The reusable core is a discrete-period, multi-SKU inventory simulator. It joins
five responsibilities:

1. an explicit inventory state and demand transition;
2. fitted replenishment policies with forecast-target provenance;
3. optional order constraints and perishable-stock hooks;
4. a canonical period-by-SKU event ledger;
5. event-based operational, service, inventory, and cost evaluation.

Forecasting itself is outside the engine. A caller supplies direct cumulative
targets or an explicitly supported distributional representation. Business
adapters, notebooks, SPAR workflows, and M5 experiments are also outside the
candidate public core.

## 10.2 Dependency map

```text
caller / forecast system
        |
        | fitted targets + opening state + demand path
        v
policies --------------------------+
  |                                 |
  | OrderDecision                   |
  v                                 v
SimulationEngine --> callbacks --> constraints --> inventory_operations
  |       |                              |
  |       +--> inventory processes       v
  |                                  InventoryStateDataFrame
  v
canonical event rows --> event validation --> evaluator / metrics --> plots
```

The dependency direction matters. Policies calculate requested orders; they do
not mutate inventory. Constraints transform requested orders. The inventory
operation schedules accepted orders into the lead-time pipeline. The simulator
owns timing and produces the event record. Metrics consume validated events,
not policy internals.

## 10.3 Candidate public boundary

The following live source areas form the candidate reusable boundary:

| Area | Responsibility | Principal modules |
|---|---|---|
| Core | state, decisions, engine, constraints, perishability | `core/*` |
| Policies | `(R,S)`, `(s,Q)`, `(s,S)`, `(R,s,S)` and target validation | `policies/*` |
| Utilities | demand generation and inventory mutation primitives | `utils/*` |
| Evaluation | canonical validation, evaluator, metrics | `evaluation/*` |
| Visualization | event-ledger plots and dashboards | `visualization/*` |

This is an extraction candidate, not authorization to copy every file unchanged.
Package metadata, names, exports, documentation, and provenance still require a
fresh design.

## 10.4 Explicitly outside that boundary

- `companies/` and SPAR orderbook/application code;
- dataset-specific M5 and SPAR adapters;
- experiments, reports, historical audits, and generated artifacts;
- old notebooks and compatibility demonstrations;
- the current legacy `pyproject.toml`, requirements, build manifest, and release
  metadata;
- the current private Git history as a basis for a public repository.

These remain in the private main tree for evidence and archaeology.
They must not leak into a clean public extraction merely because tests or old
imports refer to them.

## 10.5 Core objects and ownership

| Object | Created by | Mutated by | Consumed by |
|---|---|---|---|
| `InventoryStateDataFrame` | caller/init method | engine-owned state transition and order operation | policy, engine, defensive callback context, event builder |
| `OrderDecision` | fitted policy | constraints, then order operation | engine and event builder |
| fitted policy | caller via `fit` | engine uses a deep copy | engine |
| demand frame | caller or `DemandGenerator` | materialized/validated, not incrementally regenerated | engine |
| canonical event rows | engine | engine attaches typed callback and constraint audit before validation | result/evaluator/plots |
| `SimulationResult` | engine | treated as run output | evaluator and caller |

The engine deep-copies starting state and policy for a run. Comparison runs also
materialize demand once and copy inputs so scenarios share the same demand path
without sharing mutable inventory.

## 10.6 Design invariants

- One row per SKU in live state and one row per SKU-period in events.
- Lead time is a nonnegative integer; periodic review intervals are positive integers.
- Demand dates follow one explicit forward pandas frequency.
- On-hand, pipeline, demand, orders, shortage, and backlog are finite and
  nonnegative.
- A SKU cannot simultaneously have positive on-hand and positive backlog.
- Lost-sales mode cannot carry backlog.
- Physical stock changes only through receipts, demand fulfillment, expiry, an
  explicit audited adjustment, or a declared inventory-process flow
  ([98](98_inventory_processes.md)).
- Positive-lead-time orders change pipeline; zero-lead-time orders are received before demand.
- Forecast target probability, horizon, origin, end date, source, and supported
  aggregation semantics must agree.
- Evaluation is based on a validated event ledger and explicit aggregation.

## 10.7 Where to verify claims

The central implementation paths are
[`data_structures.py`](../src/stockcast/core/data_structures.py),
[`simulation_engine.py`](../src/stockcast/core/simulation_engine.py),
and
[`event_validation.py`](../src/stockcast/evaluation/event_validation.py).
The [module reference](70_module_reference.md) maps every remaining file.
