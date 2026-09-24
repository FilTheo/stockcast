# Constraints, shelf life, and callbacks

These public extensions are composed by the engine and recorded in run outputs.

## Ordering constraints

`OrderingConstraints` applies constraints in the sequence supplied. The built
ins are `MinimumOrderQuantity`, `OrderMultiple`, `MaximumOrderQuantity`, and
`ShelfSpaceLimit`. Each can raise on an invalid requested order or apply its
declared adjustment mode. The event ledger records requested, callback-adjusted,
and constrained quantities plus binding information.

The final composed order is validated against every rule, not repaired. For
example, `OrderMultiple` together with `ShelfSpaceLimit` raises whenever shelf
space binds: clipping to the free space breaks the case multiple, and rounding
up to a case can exceed the space, in either order. For a rule such as "whole
cases that still fit", subclass `OrderingConstraint` and return a
`ConstraintResult` with its audit rows.

## Shelf life

Use `ShelfLifeEngine` with dated opening lots. It maintains FIFO consumption and
expiry evidence. Opening lot quantities must balance the opening on-hand state;
expiry is processed before demand. `ShelfLifeEngine.run_comparison(...)` takes
the same `opening_lots` and `opening_expiry_handling` arguments as `run`, and
every compared branch starts from those same lots. Notebook 07 uses a small, clearly declared
scenario—the M5 values there are demand observations, not evidence of real
inventory state, shelf life, costs, or replenishment behaviour.

## Typed callbacks

`SimulationCallback` has two public phases: `on_after_demand(...)` proposes a
signed on-hand adjustment after demand, affecting subsequent decisions, and `on_after_prediction(...)`
proposes absolute order quantities before constraints. Callbacks receive
defensive context, not live mutable engine state or finalized events. Their
accepted effects appear in `to_callback_audit_frame()`. See Notebook 08 and the
[core reference](../reference/core.md).

## Suppliers and open orders

An optional `SupplyModel` passed as `supply=` places each accepted order with
one or more suppliers, each with a fixed or random lead time and optional
partial deliveries. It runs after callbacks and constraints. See
[open orders and suppliers](suppliers-and-open-orders.md) and Notebook 05d.
