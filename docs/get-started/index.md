# Get started

Welcome! This section takes you from installing Stockcast to understanding
every part of a simulation, and the reasoning behind its design.

<div class="grid cards sc-grid-2" markdown>

-   **1. Install**

    ---

    One `pip install`. NumPy, pandas, and Matplotlib are the only dependencies.

    [:octicons-arrow-right-24: Installation](installation.md)

-   **2. Quickstart**

    ---

    Build a complete forecast → order → simulate → evaluate workflow for one
    product in about ten minutes.

    [:octicons-arrow-right-24: Quickstart](quickstart.md)

-   **3. Learn the basics**

    ---

    Nine short pages, one idea each: state, demand, targets, policies, the
    engine, the ledger, evaluation, comparisons, and going to production.

    [:octicons-arrow-right-24: Learn the basics](../learn/index.md)

-   **4. Philosophy**

    ---

    Why Stockcast is built the way it is: the forecasting–inventory gap, one
    explicit clock, checked accounting, composable parts, and the literature
    behind every choice.

    [:octicons-arrow-right-24: Read the philosophy](philosophy.md)

</div>

## What you need to know first

You do not need a background in inventory theory. The docs explain each idea
when it first appears, with a small worked example. A little pandas helps,
because Stockcast's inputs and outputs are DataFrames.

## The words you will meet most

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
