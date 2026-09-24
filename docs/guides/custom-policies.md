# Custom policies and target providers

Two extension points let you add inventory logic without changing engine-owned
state transitions.

## Custom policy

Subclass `BasePolicy`, implement `fit(...)`, mark the policy `fitted_`, and
return an `OrderDecision` from `predict(...)`. The policy may inspect the state
provided to it, but it requests an order; it must not mutate live inventory.
Notebook 05 is the executable starting point.

## Periodic-review target provider

`PeriodicReviewPolicy` delegates its target construction to a
`PeriodicReviewTargetProvider`. A custom provider returns `PeriodicReviewTargets`
with one `reorder_point` and `order_up_to_level` per SKU plus serializable
metadata. Stockcast validates its result centrally. Use
`ColumnPeriodicReviewTargets` when those two values already exist in named
columns, or `FixedPeriodicReviewTargets` for declared scalar/per-SKU scenarios.

## Decision schedule

Subclass `DecisionSchedule` to declare deterministic opportunities separately
from the order rule. Supply `should_decide(period)`, a strictly later
`next_decision_period(period)` or `None`, and `to_manifest()`.
The period is zero-based within the run; the engine still owns all physical
transitions. `BasePolicy` accepts a schedule without a review interval or
probability. Built-in periodic, one-time and explicit schedules are described
in [decision schedules](../concepts/decision-schedules.md).

`PeriodicReviewPolicy` also accepts a separate schedule and optional probability
for explicit provider targets. Its threshold rule remains `IP <= s`, then
`max(0, S-IP)`; the provider retains responsibility for scientific coverage.
