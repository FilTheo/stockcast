# Learn the basics

Nine short steps, one idea each. Every step uses the same small example, so
by the end you will know every object in a Stockcast run, how they fit
together, and how the policy you backtested runs in production.

**The running example.** A tea shop sells 250 g packs, about six a day. The
supplier delivers 2 days after an order and the shop orders every 4 days. It
opens with 30 packs on the shelf, and sales it cannot serve are lost.

| Step | You will learn | Key objects |
|---|---|---|
| [1. Inventory state](01-inventory-state.md) | On hand, on order, backorders, and inventory position | `InventoryStateDataFrame` |
| [2. Demand and time](02-demand-and-time.md) | The demand table, calendars, and what one period means | `DemandGenerator` |
| [3. Forecast targets](03-forecast-targets.md) | How a forecast becomes a stock target, and why it covers a whole window | target tables |
| [4. Policies](04-policies.md) | Turning a target into an order with `fit` and `predict` | `OrderUpToPolicy`, `OrderDecision` |
| [5. The engine](05-engine.md) | What happens inside one simulated day | `SimulationEngine` |
| [6. The event ledger](06-event-ledger.md) | Reading the record of every unit | `SimulationResult` |
| [7. Evaluate a run](07-evaluate.md) | Service, stock, and cost metrics | `InventoryEvaluator` |
| [8. Compare scenarios](08-compare.md) | Fair comparisons on identical demand | `run_comparison` |
| [9. From backtest to production](09-production.md) | Running the chosen policy every day, and why it matches the backtest | `plan` / `close` with the same objects |

Steps 1 to 8 build the **research pipeline**: simulate, evaluate, and compare.
Step 9 takes the chosen policy into the **production pipeline**: a daily job
on live stock and fresh forecasts.

Each page takes about five minutes. The code on every page runs as it is:
copy the blocks into a notebook in order and you get the same numbers.

!!! tip "Prefer notebooks?"

    [Notebook 01](../notebooks/01_introduction_to_inventory_flow.ipynb) and
    [Notebook 02](../notebooks/02_first_engine_simulation.ipynb) cover the same
    ground with more plots. The [examples](../tutorials/index.md) go much
    further.

[Start with step 1 :octicons-arrow-right-24:](01-inventory-state.md){ .md-button .md-button--primary }
