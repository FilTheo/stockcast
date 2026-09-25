# The ledger is the record

*A deep dive from our [Philosophy](../../get-started/philosophy.md).*

**Decision.** Every run produces one event ledger, a table with one row per
SKU and period that records every flow of units. Its rows satisfy the stock,
pipeline, and backorder balance identities, and every metric, plot, and
summary is computed from it.

## Why

**One source of truth.** Service, stock, and cost numbers all come from the
same table, so they are consistent with each other by construction. When two
numbers disagree with your intuition, the ledger shows the flows behind both.

**Balanced books catch mistakes.** Because each row must balance, a unit that
appears from nowhere or disappears without a reason is caught immediately,
with the SKU and period. That protects you from bugs in custom policies,
callbacks, and processes as much as in Stockcast itself.

**Additive by design.** Balanced SKU-period rows can be summed over any
grouping (weeks, stores, categories, suppliers) and the totals still balance.
Counts that describe the whole portfolio, such as `order_event_count`, are
stored so that sums stay correct.

**Stable and portable.** The ledger's columns and their meanings are part of
Stockcast's public contract: new columns may be added, and existing ones keep
their meaning. It is a plain DataFrame, ready for pandas, SQL, parquet, or a
dashboard.

## What it means for you

- Compute your own measures from `result.to_event_frame()`; they will agree
  with the built-in metrics.
- Store the ledger and the run manifest together to keep a complete record of
  an experiment.
- Use `validate_event_frame` on ledgers you save, transform, or receive.

See [Stock accounting](../concepts/accounting.md) for the identities and
[Output tables](../../reference/schemas.md) for every column.
