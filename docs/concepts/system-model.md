# System model and timing

Stockcast separates the decision workflow into components with distinct owners.

```text
forecast-derived target -> fitted policy -> requested order
    -> callbacks -> constraints -> lead-time pipeline
    -> receipts and demand -> validated event ledger -> evaluation
```

## Who owns what

- **Your application** supplies demand, opening state, costs, timing choices,
  and forecast-derived information.
- **A policy** turns validated state and targets into a requested
  `OrderDecision`; it does not mutate inventory.
- **The engine** owns run preflight, state transitions, callback application,
  constraints, canonical events, and validation.
- **The event ledger** is the accounting record consumed by evaluation and
  plotting.

## A simulated period

For ordinary period events, the engine receives due pipeline stock, clears
backorders when applicable, processes demand, applies typed inventory
adjustments, evaluates an eligible policy, applies order callbacks and
constraints, and places the accepted order into the pipeline. An order does
not become on-hand inventory when it is placed. Lead time is a positive
integer, so same-period replenishment is not available.

`ContinuousReviewPolicy` retains its familiar name, but is evaluated once per
simulated period—not continuously in physical time.

## Engine run or caller-owned loop?

Use `SimulationEngine` for a complete, validated run with a manifest,
callback/constraint orchestration, and evaluator-ready event rows. The manual
primitives `process_demand` and `update_inventory_with_orders` are also public
for a caller-owned loop, but the caller then owns loop control, recording,
provenance, and any noncanonical output. Notebook 03 demonstrates both paths.
