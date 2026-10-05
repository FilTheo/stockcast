# Release notes

## 0.1.0 (2026-10-05)

The first public release of Stockcast: DataFrame-first inventory decisions,
simulation, and evaluation for many SKUs at once.

The 0.1.x line keeps the documented import namespaces, public outputs,
metrics, and scientific behaviour stable. Backward-compatible additions and
corrections will be recorded here. A breaking redesign will come with a new
release line and a migration note.

**What's included**

- **Timing you can reason about.** Decisions are made
  [before demand](user-guide/concepts/timing.md), lead time may be
  zero, and a `DecisionSchedule` (periodic, one-time, explicit, or your own)
  says when a policy may order, separately from how much it orders.
- **Synthetic demand from any distribution.** `DemandGenerator` builds
  seeded, validated demand tables from built-in models or from your own
  sampler function, with one sampler for the whole panel or one per SKU. See
  [demand and calendars](user-guide/demand.md).
- **Policies.** Order-up-to policies, `ReorderPointPolicy` for `(s,Q)` and
  `(s,S)` rules on any schedule, with levels from fixed values, a forecast, or
  your own target provider, single-order (newsvendor)
  decisions, and `BasePolicy` for your own rules. Targets can come from any
  forecaster or from a planner.
- **Open orders and suppliers.** Declare an opening pipeline with
  `with_open_orders(...)`, and split orders across `Supplier` objects with
  fixed or random lead times, partial deliveries, and custom allocations. See
  [suppliers and open orders](user-guide/suppliers.md).
- **Unreliable deliveries.** A `DeliveryOutcome` decides how much of a due
  delivery arrives, is delayed, or never arrives. See
  [unreliable deliveries](user-guide/unreliable-deliveries.md).
- **Inventory processes.** Add physical stock flows such as damage or
  inspection loss at defined phases, with FIFO shelf life built in. See
  [shelf life and inventory processes](user-guide/processes.md).
- **Comparisons in one call.** `run_comparison` runs every option on the same
  demand, from its own copy of the same opening stock, with the same random
  supplier lead times. Every result carries a run manifest. See
  [compare forecasts and policies](how-to/compare-policies.md).
- **Operational rules.** Ordering constraints (minimum, multiple, maximum,
  shelf space) and callbacks for planned interventions, each with an audit
  table.
- **From backtest to production.** The same state, policy and constraint
  objects run a periodic plan-and-close job. See
  [use Stockcast in production](how-to/production.md).
- **Evidence you can check.** Every run produces a checked event table, a
  run manifest, callback audits, order and process-flow frames, and service
  and cost metrics.
- **Documentation.** A Quickstart, a nine-step *Walkthrough*, a Guide to
  every building block, recipes, 24 runnable example notebooks,
  and a full API reference. Every Python example in the docs is executed in
  the test suite.

Stockcast supports Python 3.10 to 3.13, NumPy 1.23 or later, pandas 1.5 or
later, and Matplotlib 3.6 or later.
