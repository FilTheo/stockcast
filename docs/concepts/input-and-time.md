# Inputs and time

Stockcast validates complete, explicit scenarios rather than completing missing
business information on a caller's behalf.

## Minimum scenario inputs

| Input | What to supply |
|---|---|
| SKU identifiers | One consistent, non-null identifier type across state, demand, targets, and decisions. |
| Opening state | Observed on-hand state, declared zero state, or a complete validated state frame. |
| Demand | One row per SKU and period with `unique_id`, `period`, `date`, and `y`. |
| Time | A forward pandas frequency, positive lead time, and policy review period where applicable. |
| Run windows | Explicit warm-up, scoring, and settlement lengths that sum to `n_periods`. |
| Costs | Explicit rates whenever a cost metric is requested. |

## Demand calendar

Demand input is a complete rectangular SKU-period grid. Dates must advance at
the declared frequency, periods are zero-based, and demand is finite and
non-negative. Missing rows, duplicates, unknown SKUs, and a guessed frequency
are errors rather than zero-filled data.

## Opening state

Use `InventoryStateDataFrame.initialize_from_observed(...)` for an observed
on-hand frame, or `initialize_zero(...)` for an explicitly zero scenario.
Opening backlog and pipeline require a complete state frame. A SKU cannot have
positive on-hand and positive backlog at the same time.

## Experiment windows

Warm-up, scoring, and settlement all advance demand and state. The scoring
window determines summary and evaluation defaults. Settlement can let existing
pipeline arrive after scoring; `order_during_settlement` explicitly controls
whether it creates new orders.
