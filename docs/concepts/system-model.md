# System model and timing

For the rationale, protection-horizon derivation, literature, and validation
boundaries, read [Engine design and scientific basis](engine-design.md).

Stockcast separates the decision workflow into components with distinct owners.

```text
forecast-derived target -> fitted policy -> requested order
    -> callbacks -> constraints -> [optional supply model]
    -> lead-time pipeline -> receipts and demand
    -> validated event ledger -> evaluation
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

Each demand epoch has the following order:

1. Expire unusable lots (with `ShelfLifeEngine` or a `ShelfLife` process) and
   apply other processes' `before_demand` flows.
2. Advance the clock, receive due pipeline stock, and clear old backlog first.
3. If the decision schedule permits, predict an order from pre-demand state.
4. Apply order callbacks and constraints, then accept the order.
5. Receive an accepted `lead_time=0` order immediately, clearing old backlog first.
6. Fulfill current demand, apply processes' `after_demand` flows, then typed
   `on_after_demand` adjustments.
7. Validate and record the completed period's accounting.

An order accepted at epoch `t` arrives before demand at `t+L`.
`L` is a nonnegative integer. Positive-lead-time orders enter the pipeline;
zero-lead-time orders increase receipts and usable stock immediately. Review
intervals remain positive. Under periodic review, the inventory-position target
covers demand at `t,...,t+L+R-1`: `H=L+R`. This describes coverage, not guaranteed
optimality, fill rate, or a universal lost-sales service formula.

The pipeline is kept per SKU (`in_transit`) and, in step with it, per open
order. By default each accepted order is one line due `L` periods later. An
optional `SupplyModel` splits it across suppliers with their own fixed or
random lead times and partial deliveries. See
[open orders and suppliers](../guides/suppliers-and-open-orders.md).

Decision schedules and fitted-policy updates are separate. See
[decision schedules and migration](decision-schedules.md) for periodic,
one-time, and irregular examples, coordinate conventions, and the breaking
migration from the former demand-first loop.

Stockcast does not simulate continuous review. `ReorderPointPolicy` applies
the `(s,Q)` or `(s,S)` rule at the opportunities of its schedule. Its reorder
point covers the same window `H=(u-t)+L`, so review every period gives `L+1`.
Notebook 05b shows how finer review steps approach the continuous-review
reorder point.

## Engine run or caller-owned loop?

Use `SimulationEngine` for a complete, validated run with a manifest,
callback/constraint orchestration, and evaluator-ready event rows. The manual
primitives `advance_period`, `update_inventory_with_orders` (or the order-level
`place_order_lines`), and `fulfill_demand` are also public
for a caller-owned loop, but the caller then owns loop control, recording,
provenance, and any noncanonical output. Notebook 03 demonstrates both paths.
