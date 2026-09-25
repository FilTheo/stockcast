# The engine owns the stock

*A deep dive from our [Philosophy](../../get-started/philosophy.md).*

**Decision.** Only `SimulationEngine` changes inventory. Policies, callbacks,
constraints, suppliers, and processes each **propose** something (an order, an
adjustment, a delivery split, a flow), and the engine validates and applies
the proposals in a fixed order.

## Why

**Every policy is judged by the same rules.** A policy receives a copy of the
state and returns an `OrderDecision`. It cannot receive stock early, skip a
lost sale, or forget a backorder. Two policies compared on the same demand
differ only in their decisions, never in their bookkeeping.

**Extensions compose safely.** Because each extension only proposes, several
can be combined (a case-pack rule, a holiday callback, a random supplier,
shelf life) without knowing about each other. The engine applies them in the
documented order and checks the result.

**Nothing is lost between steps.** The engine records each stage of an order
(requested, after callbacks, after constraints, accepted) and each physical
flow, so every change has a place in the ledger.

**Your objects stay yours.** The engine copies the opening state and the
policy before a run. After `run`, your `inventory` and `policy` are exactly as
you left them, ready for the next experiment.

## What it means for you

- Write policies as pure decision rules: read the state, return an order.
- Model interventions with callbacks, rules with constraints, supply with a
  supply model, and physical mechanisms with processes, each on its own.
- For step-by-step control you can still drive the state yourself with the
  public primitives ([Run your own simulation loop](../../how-to/manual-loop.md));
  the engine is the convenient, fully checked path.
