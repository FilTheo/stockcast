<span class="sc-step">Step 6 of 9</span>

# The event ledger

Every simulation writes an **event ledger**: one row per SKU and period that
records what the shop started with, what arrived, what was ordered, what was
sold, and what was left. It is the complete, checked record of the run.
Metrics, plots, and your own analysis all read from it.

## Get the ledger

We continue with the `result` from [step 5](05-engine.md).

??? example "Setup: the tea shop from steps 1 to 5"

    ```python
    --8<-- "learn-setup.py"
    ```

```python
events = result.to_event_frame()
events.shape
```

```text
(56, 43)
```

56 days × 1 SKU = 56 rows. The columns fall into a few groups:

| Group | Columns |
|---|---|
| When and what | `unique_id`, `date`, `period`, `demand_period`, `run_window` |
| Opening of the day | `starting_on_hand`, `starting_on_order`, `starting_backorders` |
| Flows during the day | `received_units`, `demand`, `fulfilled_units`, `shortage_units`, `lost_sales_units`, `backorder_increment`, `expired_units`, … |
| The decision | `decision_flag`, `decision_inventory_position`, `order_quantity`, `target_level` |
| Close of the day | `ending_on_hand`, `on_order_end`, `backorders_end`, `inventory_position_end` |
| How the order was shaped | `requested_order_quantity`, `callback_adjusted_order_quantity`, `constrained_order_quantity`, `binding_constraints`, … |

The full list, with meanings, is in [Output tables](../reference/schemas.md).

## Read one day

Here is Saturday 10 January, a review day:

```python
day = events.loc[events["date"] == "2026-01-10"].iloc[0]
day[["starting_on_hand", "starting_on_order", "received_units",
     "decision_inventory_position", "order_quantity", "demand",
     "fulfilled_units", "ending_on_hand", "on_order_end",
     "inventory_position_end"]]
```

```text
starting_on_hand               19.0
starting_on_order               0.0
received_units                  0.0
decision_inventory_position    19.0
order_quantity                 27.0
demand                          4.0
fulfilled_units                 4.0
ending_on_hand                 15.0
on_order_end                   27.0
inventory_position_end         42.0
```

Following the three moves:

1. **Receive.** Nothing was due. The shelf holds 19 packs.
2. **Decide.** The policy saw an inventory position of 19 and ordered
   $46 - 19 = 27$.
3. **Meet demand.** 4 packs sold, 15 left. The 27 packs are now on order, so
   the inventory position closes at $15 + 27 = 42$.

`decision_inventory_position` is the position the policy saw *before* demand.
It is empty on days without a decision.

## The books always balance

Each row satisfies a small set of accounting identities. For every SKU and
period:

$$
\begin{aligned}
\text{demand} &= \text{fulfilled} + \text{shortage} \\[2pt]
\text{on hand}_{\text{end}} &= \text{on hand}_{\text{start}} + \text{received}
  - \text{backorders served} - \text{fulfilled} - \text{expired} + \text{adjustments} \\[2pt]
\text{on order}_{\text{end}} &= \text{on order}_{\text{start}} + \text{ordered} - \text{received} \\[2pt]
\text{backorders}_{\text{end}} &= \text{backorders}_{\text{start}} + \text{new backorders}
  - \text{backorders served} \\[2pt]
\mathit{IP}_{\text{end}} &= \text{on hand}_{\text{end}} + \text{on order}_{\text{end}}
  - \text{backorders}_{\text{end}}
\end{aligned}
$$

The engine checks these on every row as it runs. You can check any ledger,
including one you saved or edited, with `validate_event_frame`:

```python
from stockcast.evaluation import validate_event_frame

checked = validate_event_frame(events)   # returns the ledger, or raises
len(checked)
```

```text
56
```

Because every row balances, you can add rows up in any grouping (by week,
by SKU, by store) and the totals stay consistent.

## Shortages in the ledger

With lost sales, unmet demand appears as `shortage_units` and
`lost_sales_units`. With backorders, it appears as `backorder_increment` and
carries forward in `backorders_end` until a delivery serves it.
`stockout_flag` marks every SKU-period with a shortage. Step 8 shows a
scenario where these columns come alive.

## More views of the same run

| Method | One row per | Use it for |
|---|---|---|
| `to_event_frame(window=...)` | SKU and period | Metrics and plots. The main record. |
| `to_order_frame()` | Scheduled delivery | Which order arrived when, and from which supplier |
| `to_callback_audit_frame()` | Callback effect | Every manual intervention and its reason |
| `to_process_flow_frame()` | Process flow | Expiry, inspections, returns |
| `summary()` | – | A quick dictionary of headline numbers |

!!! summary "Recap"

    - The ledger has one row per SKU and period, with every flow of units.
    - Its rows satisfy stock, pipeline, and backorder balance identities,
      checked as the engine runs.
    - Everything downstream (metrics, plots, your analysis) reads the ledger.

**Go deeper:** [Stock accounting](../user-guide/concepts/accounting.md) ·
[Output tables](../reference/schemas.md) ·
[The ledger is the record](../user-guide/design/ledger.md)

[Next: Evaluate a run :octicons-arrow-right-24:](07-evaluate.md){ .md-button }
