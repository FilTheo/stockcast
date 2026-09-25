# Small parts you combine

*A deep dive from our [Philosophy](../../get-started/philosophy.md).*

**Decision.** Stockcast is a set of small objects with one job each (state,
demand, schedule, policy, callback, constraint, supply, process, metric) and
one base class per extension point. You describe an inventory system by
choosing and combining them, the way you build a model from PyTorch layers.

## Why

**Change one thing at a time.** Research and practice both ask "what if?":
what if the forecast improves, the supplier slows down, the shelf shrinks?
When each concern is its own object, a what-if is a one-argument change, and
everything else stays fixed. That is what makes comparisons fair.

**Simple things stay simple.** A first simulation needs a state, demand, and a
policy. Constraints, callbacks, suppliers, and processes are optional
arguments that switch on extra code only when you pass them. Runs that do not
use a feature go through exactly the same path as before it existed.

**One flexible extension point beats many options.** Rather than growing a
long list of arguments for every variant (every supplier rule, every expiry
rule), Stockcast offers a base class you can subclass: `SupplierAllocation`,
`DeliveryOutcome`, `InventoryProcess`, `OrderingConstraint`,
`SimulationCallback`, `DecisionSchedule`, `BasePolicy`,
`PeriodicReviewTargetProvider`, `BaseInventoryMetric`. The built-ins are
written with the same interfaces you use.

**Familiar shapes.** Policies follow scikit-learn's `fit` / `predict`,
callbacks follow Keras, the engine's role follows a PyTorch training loop, and
data is long-format pandas like most forecasting libraries. Learning one part
makes the next one predictable.

## What it means for you

- Start with the smallest run that answers your question, and add parts as the
  question grows.
- When a built-in object is almost right, subclass its base class rather than
  looking for an option.
- See all extension points side by side in
  [How Stockcast fits together](../concepts/architecture.md#extension-points),
  and at work in [Notebook 05c](../../notebooks/05c_extension_points.ipynb).
