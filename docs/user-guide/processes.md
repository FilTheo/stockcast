# Shelf life and inventory processes

Stock does not only leave through sales. Food expires, quality checks discard
units, customers return goods. Stockcast models these physical flows as
**inventory processes**: small objects that add or remove on-hand stock at
defined moments of each period, with every unit recorded in the ledger.

Most perishable studies need only shelf life, so we start there.

## Shelf life with FIFO

`ShelfLifeEngine` is a `SimulationEngine` that tracks stock in dated **lots**
and sells the oldest first (FIFO).

A lot received on date $d$ with shelf life $S$ days can be sold on dates
$d, \dots, d + S - 1$ and expires at the start of $d + S$, before that day's
receipts, decision, and demand:

$$
\text{lot expires on day } \tau \iff \tau - d \ge S .
$$

Shelf life is measured in **calendar days**, whatever the period length.
Demand and backorders consume the oldest lots first. Expired units appear in
the ledger as `expired_units` and are priced by the `waste_cost` metric.

??? example "Setup: the tea shop from Learn the basics"

    ```python
    --8<-- "learn-setup.py"
    ```

For this example, suppose each pack stays sellable for 6 days, and the 30
opening packs arrived two days before the run:

```python
from stockcast.core import ShelfLifeEngine

opening_lots = pd.DataFrame({
    "unique_id": [sku],
    "received_date": [opening_date - pd.Timedelta(days=2)],
    "quantity": [30.0],                  # must equal opening on-hand stock
})

fresh = ShelfLifeEngine(shelf_life_days=6).run(
    policy=policy, demand_source=demand, inventory=inventory,
    opening_lots=opening_lots, **run_settings,
)
fresh.to_event_frame()["expired_units"].sum()
```

```text
np.float64(18.0)
```

The 95% target that served every sale for a long-life product now throws
away 18 packs over eight weeks: the price of holding a buffer of perishable
stock.

Opening lots must add up to each SKU's opening on-hand stock.
`opening_expiry_handling` says what to do with opening lots already expired
at the start: `"reject"` (default), `"expire_before_initial_decision"`, or
`"preprocessed"`.

## The same model, as a process

`ShelfLifeEngine` runs one `ShelfLife` process on the standard engine. You can
write the same run as:

```python
from stockcast.core import ShelfLife, SimulationEngine

as_process = SimulationEngine().run(
    policy=policy, demand_source=demand, inventory=inventory,
    processes=[ShelfLife(shelf_life_days=6, opening_lots=opening_lots)],
    **run_settings,
)
as_process.to_event_frame().equals(fresh.to_event_frame())
```

```text
True
```

The process form lets you combine shelf life with other physical flows.

## Write a process

A process subclasses `InventoryProcess`, declares a unique `name` and the
`flows` it may report, and overrides the hooks it needs. This one models a
Monday quality check that discards 10% of the stock on the shelf:

```python
import numpy as np

from stockcast.core import Flow, InventoryProcess, ProcessFlows


class MondayInspection(InventoryProcess):
    """Each Monday a quality check discards a share of the stock on hand."""

    name = "inspection"
    flows = (Flow("discarded", "outflow"),)

    def __init__(self, share):
        self.share = share

    def after_demand(self, context):
        if context.date.day_name() != "Monday":
            return None
        return ProcessFlows({"discarded": np.floor(self.share * context.on_hand)})

    def get_config(self):
        return {"share": self.share}


inspected = SimulationEngine().run(
    policy=policy, demand_source=demand, inventory=inventory,
    processes=[ShelfLife(6, opening_lots), MondayInspection(0.10)],
    **run_settings,
)
inspected.to_event_frame()[["expired_units", "process_outflow_units"]].sum()
```

```text
expired_units            17.0
process_outflow_units    12.0
dtype: float64
```

The inspection's discards come from the oldest lots (FIFO), which is why one
fewer pack expires.

### Flows

`Flow(name, direction, category="general")`

:   `direction` is `"inflow"` (adds stock) or `"outflow"` (removes stock).
    `category="expiry"` (outflows only) records the flow as expiry, in
    `expired_units`. Every other flow is recorded in `process_inflow_units` or
    `process_outflow_units`.

`ProcessFlows(quantities, received_dates=None)`

:   Maps each declared flow name to non-negative per-SKU quantities (a dict or
    a Series indexed by SKU). The direction comes from the declaration, never
    from the sign. `received_dates` gives inflows a lot date, which
    `ShelfLife` needs to know the age of returned goods.

### Hooks

| Hook | When | Returns |
|---|---|---|
| `reset(context)` | before each run | – |
| `before_demand(context)` | the period has opened; before receipts, the decision, and demand | `ProcessFlows` or `None` |
| `on_receipt(context)` | after deliveries are received (`context.received`) | – |
| `after_demand(context)` | demand has been served; before `on_after_demand` callbacks | `ProcessFlows` or `None` |
| `check(context)` | after `after_demand` and after callback adjustments | raise if an internal mirror disagrees |

Stock removed in `before_demand` is gone before the policy decides and before
customers arrive. Stock changed in `after_demand` affects the following
periods. `context` is a read-only `ProcessContext` with the phase, period,
date, window, and per-SKU Series for `on_hand`, `backorders`, and `on_order`
(plus `received`, `demand`, `fulfilled` where the phase has them).

### Rules the engine enforces

- Only declared flows, known SKUs, and finite non-negative quantities are
  accepted.
- An outflow cannot exceed on-hand stock; an inflow is not accepted while the
  SKU has backorders.
- Processes change on-hand stock only, never the pipeline, backorders, or
  demand.
- Processes run in list order, each seeing the stock left by the ones before
  it. Put `ShelfLife` first so expiry happens before other `before_demand`
  flows.

### Processes that mirror stock

A process that keeps its own record of stock, like `ShelfLife`'s lots, is told
about every other process's flows and every callback adjustment through
`validate_stock_change(change, context)` (raise to reject) and
`on_stock_change(change, context)` (update the mirror). That is how the
inspection above consumed the oldest lots, and how a callback that adds stock
must say when it was received.

## Outputs

- **Ledger:** expiry adds into `expired_units`; general flows add the pair
  `process_inflow_units` and `process_outflow_units`, and the on-hand identity
  becomes
  $\mathit{OH}^{\text{end}} = \mathit{OH}^{\text{start}} + r - \text{served} - \text{expired} + \text{adjustment} + \text{inflow} - \text{outflow}$.
- **Flow table:** `result.to_process_flow_frame()` has one row per non-zero
  flow, SKU, and period, with the process, flow, direction, category, and
  phase:

```python
inspected.to_process_flow_frame()[["date", "process", "flow", "phase", "quantity"]].head(4)
```

```text
        date     process       flow          phase  quantity
0 2026-01-09  shelf_life    expired  before_demand       9.0
1 2026-01-12  inspection  discarded   after_demand       3.0
2 2026-01-26  shelf_life    expired  before_demand       3.0
3 2026-01-26  inspection  discarded   after_demand       1.0
```

- **Manifest:** `run_settings["processes"]` lists each process's name, class,
  flows, overridden hooks, and `get_config()`.

!!! tip "Process or callback?"

    A one-off correction on a known date (a stock count) is simplest as a
    [`ScheduledInventoryAdjustment`](callbacks.md) callback. Use a process for
    a **recurring physical mechanism**.

**Notebooks:** [05e: inventory processes](../notebooks/05e_inventory_processes.ipynb) ·
[07: FIFO shelf life on M5 demand](../notebooks/07_m5_fifo_perishable_scenario.ipynb)
