# Get started

Install Stockcast, run the Quickstart, then follow Learn the basics.

- [Installation](installation.md): one `pip install`; NumPy, pandas, and
  Matplotlib are the only dependencies.
- [Quickstart](quickstart.md): a forecast → order → simulate → evaluate
  workflow for one product.
- [Learn the basics](../learn/index.md): nine short pages, from inventory
  state to a production daily job.
- [Philosophy](philosophy.md): why Stockcast is built the way it is, and the
  research behind it.

## Prerequisites

No background in inventory theory is needed; each idea is explained with a
small example when it first appears. Some pandas helps, because Stockcast's
inputs and outputs are DataFrames.

## Key terms

| Term | Meaning |
|---|---|
| **On hand** | Stock physically on the shelf, available to sell now. |
| **On order** (pipeline) | Stock ordered from a supplier that has not arrived yet. |
| **Backorders** | Demand you could not serve yet but still owe the customer. |
| **Inventory position** | On hand + on order − backorders. What you *will* have once everything arrives. |
| **Lead time** $L$ | Periods between placing an order and receiving it. |
| **Review period** $R$ | Periods between two ordering opportunities. |
| **Target** $S$ | The inventory position a policy orders up to. |

The full list, with symbols, is in [Notation and glossary](../user-guide/concepts/notation.md).
