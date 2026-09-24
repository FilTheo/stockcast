# 50 — Constraints and shelf life

## 50.1 Constraint pipeline

`OrderingConstraints` transforms a policy's requested order before it reaches
the inventory pipeline:

```text
requested OrderDecision
  -> constraint 1
  -> constraint 2
  -> ...
  -> cross-validate final order against every constraint
  -> accepted OrderDecision
```

Constraints are named and ordered explicitly. The same set in a different order
can produce a different answer, so application order is part of the run
manifest. Each constraint emits an audit contribution. A final validation pass
detects a later constraint that invalidated an earlier one.

Each numeric rule accepts either one scalar or an exact per-SKU map. Constraint
mode is explicit:

- `raise`: reject a violating request;
- `adjust`: transform it and record the adjustment.

Sparse decisions are normalized to zero for known omitted SKUs. Unknown SKUs
are rejected.

Constraints act on each SKU's total. An optional supply stage
([97](97_open_orders_and_suppliers.md)) splits the accepted total across
suppliers afterwards; supplier capacity or MOQ is not a constraint yet.

## 50.2 Built-in constraints

| Constraint | Rule in adjustment mode |
|---|---|
| `MinimumOrderQuantity` | a positive quantity below the minimum is raised to the minimum |
| `OrderMultiple` | a positive quantity is rounded upward to the next allowed multiple |
| `MaximumOrderQuantity` | quantity above the maximum is clipped |
| `ShelfSpaceLimit` | quantity is clipped to available capacity |

Shelf-space availability uses inventory already owned or committed:

```text
available = capacity - (on_hand + sum(in_transit))
```

Maximum-order and shelf-space adjustments also set capacity/constraint audit
flags used by evaluation metrics.

## 50.3 Constraint counters

The tracked order path records at least requested quantity, net constraint
adjustment, constrained quantity, final order quantity, violation flags, event
count, SKU-line count, and squared positive order quantity. These support
additive aggregation and order-size variance without reconstructing decisions
from mutable policy state.

## 50.4 Shelf-life model

`FIFOLotLedger` tracks dated lots per SKU and maintains a balance equal to
on-hand state. It consumes oldest lots first and expires a lot when:

```text
current_date - received_date >= shelf_life_days
```

Thus a lot received on day `D` with shelf life `S` can serve demand on
`D ... D+S-1` and expires at the start of `D+S`, before that day's demand.

Opening lots are explicit and their total must equal opening on-hand for every
SKU. Opening-expiry handling is one of:

- `reject`;
- `expire_before_initial_decision`;
- `preprocessed`.

The engine records a fingerprint of the opening lot configuration.

## 50.5 Shelf-life engine integration

`ShelfLifeEngine` extends normal simulation through private engine-owned phases:

1. reset the ledger at run start;
2. before demand, expire old lots, reduce state on-hand accordingly, and
   register the current pipeline receipt as a new lot;
3. run the standard demand transition;
4. after demand, consume FIFO quantities for current demand fulfillment and old
   backlog fulfillment;
5. apply typed physical callback requests, adding explicitly dated lots or
   consuming FIFO lots; and
6. assert after each accepted batch that lot balances equal ending on-hand.

With `supply=`, all of a SKU's deliveries received in one period (pipeline or
immediate supplier lines) still form one lot dated that period; lots carry no
supplier identity.

Expiry therefore appears as a distinct event flow and participates in the
on-hand balance equation. It is not silently folded into demand or shortage.

## 50.6 Interaction cautions

- Shelf-space constraints use aggregate on-hand and pipeline, not remaining
  shelf life by lot.
- An order multiple applied after capacity clipping could exceed capacity; the
  final all-rule validation must catch such ordering conflicts. Verified
  2026-09-24: `OrderMultiple` plus `ShelfSpaceLimit` raises in either order
  whenever space binds. "Whole cases within space" needs a custom
  `OrderingConstraint`; the stress suite's `PackedShelfSpace` is one example.
- `ShelfLifeEngine.run_comparison` forwards `opening_lots` and
  `opening_expiry_handling` to every branch through the private
  `SimulationEngine._run_comparison(..., branch_run_options=...)`. Before
  2026-09-24 the inherited method could not pass lots and raised `TypeError`.
- Engine and event-validation balance identities allow
  `1e-9 + 1e-12 * sum(|flows in the row|)`. A fixed `1e-9` was below one
  floating-point ulp near `1e7` and aborted valid gram-scale shelf-life runs.
  The FIFO insufficient-stock check scales the same way. Rare lot-dust
  mismatches remain possible near `1e9` units per SKU-period.
- Backlog fulfillment consumes received lots before current demand, matching
  the base state transition.
- Physical callbacks run after demand and affect subsequent decisions. Positive shelf-life
  additions require a nonfuture, unexpired `received_date`; removals consume
  FIFO lots. Every accepted effect enters the event ledger before validation.
