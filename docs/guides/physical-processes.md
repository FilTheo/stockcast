# Physical processes: shelf life and custom stock flows

Most perishable runs need only `ShelfLifeEngine`. This page covers that
simple path first, then **inventory processes**: an optional way to combine
shelf life with other physical flows, such as inspection discards or customer
returns, while keeping every unit in the audited stock balance.

## The simple path: `ShelfLifeEngine`

```python
from stockcast.core import ShelfLifeEngine

result = ShelfLifeEngine(shelf_life_days=3).run(
    policy=policy, demand_source=demand, inventory=inventory, n_periods=28,
    period_frequency="D", warmup_periods=0, scoring_periods=28,
    settlement_periods=0, order_during_settlement=False,
    demand_source_name="chilled demand", random_seed=None,
    opening_lots=opening_lots,          # unique_id, received_date, quantity
)
result.to_event_frame()["expired_units"]
```

A lot received on day `D` with shelf life `S` serves demand on `D ... D+S-1`
and expires at the start of `D+S`, before that day's receipts, ordering and
demand. Demand and cleared backlog consume the oldest lots first. Opening lot
quantities must equal opening on-hand for every SKU. `opening_expiry_handling`
is `"reject"` (default), `"expire_before_initial_decision"`, or
`"preprocessed"`.

## The same run as a process

`ShelfLifeEngine` runs one `ShelfLife` process on the standard engine. This
gives an identical event ledger:

```python
from stockcast import SimulationEngine
from stockcast.core import ShelfLife

result = SimulationEngine().run(
    ...,                                # the same arguments, without opening_lots
    processes=[ShelfLife(shelf_life_days=3, opening_lots=opening_lots)],
)
```

The two forms record their configuration differently. `ShelfLifeEngine` keeps
its `run_settings["shelf_life"]`, `opening_lots` and related keys. The process
form lists each process under `run_settings["processes"]`.

## Writing a process

A process subclasses `InventoryProcess`, declares a unique `name` and its
`flows`, and overrides the hooks it needs:

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

    def get_config(self):                # JSON-serializable, stored in the manifest
        return {"share": self.share}

result = SimulationEngine().run(
    ..., processes=[ShelfLife(3, opening_lots), MondayInspection(0.25)],
)
```

- **`Flow(name, direction, category="general")`**: `direction` is `"inflow"`
  or `"outflow"`. `category="expiry"` (outflows only) records the flow as
  expiry.
- **`ProcessFlows(quantities, received_dates=None)`**: `quantities` maps a
  declared flow name to nonnegative per-SKU units, given as a mapping or a
  `pandas.Series` indexed by SKU. The direction comes from the declaration,
  not from the sign. `received_dates` optionally gives an inflow its lot
  date, as one timestamp or a per-SKU mapping.
- **`context`** is a read-only `ProcessContext`: `phase`, `period`,
  `demand_period`, `date`, `run_window`, and float Series indexed by SKU for
  `on_hand`, `backorders` and `on_order`. `received`, `demand`, `fulfilled` and
  `backorders_fulfilled` are filled where the phase has them.

### Phases

| Hook | When | Returns |
|---|---|---|
| `reset(context)` | before the first period of each run | `None` |
| `before_demand(context)` | the period has opened at its demand date; before due receipts, the order decision and demand | `ProcessFlows` or `None` |
| `on_receipt(context)` | after pipeline receipts, and again after zero-lead-time receipts; `context.received` holds the units | `None` |
| `after_demand(context)` | demand has been served; before `on_after_demand` callbacks | `ProcessFlows` or `None` |
| `check(context)` | after `after_demand`, and after each accepted callback adjustment batch | `None` (raise if a mirror disagrees) |

Stock changed in `before_demand` is what the policy and current demand see.
Stock changed in `after_demand` affects the following periods.

### Rules the engine enforces

- Only declared flows, known SKUs (identifiers are not coerced), and finite
  nonnegative quantities are accepted.
- An outflow cannot exceed the SKU's on-hand stock.
- An inflow is rejected while the SKU has backlog. Process inflows never clear
  backlog.
- A `received_date` cannot be in the future.
- Processes run in list order. Each sees the stock left by the ones before it.
- Processes change **on-hand stock only**. They cannot change the pipeline,
  backlog or demand. A delivery that arrives short is modelled with
  `SupplyModel` partial deliveries. Modelling it as an outflow after receipt
  keeps the balance correct, but `received_units` then includes units that
  never usefully arrived.
- Processes cannot be combined with an engine subclass that overrides the
  engine's private lifecycle hooks.

### Combining processes

A process is told about every *other* process's flows and every callback
adjustment through `validate_stock_change(change, context)` (raise to reject)
and `on_stock_change(change, context)` (mirror the change). It is not told
about its own flows. `ShelfLife` uses these hooks to keep its lot ledger equal
to on-hand stock:

- removals consume the oldest lots;
- additions need an explicit, unexpired `received_date`. Shelf life never
  guesses a lot's age, so an undated inflow stops the run with an error.

Put `ShelfLife` first so that its expiry acts before other processes'
`before_demand` flows.

## Outputs

- **Event ledger:** expiry flows add into the canonical `expired_units`, so
  `waste_cost` still prices expiry only. When any process declares a general
  flow, the ledger gains `process_inflow_units` and `process_outflow_units`
  (both nonnegative). The physical identity becomes:

  ```text
  ending_on_hand = starting_on_hand + received - backorders_fulfilled - fulfilled
                   - expired + inventory_adjustment
                   + process_inflow - process_outflow
  ```

  The engine asserts it every period, and `validate_event_frame` checks it
  whenever the two columns are present. Runs without general flows, including
  every `ShelfLifeEngine` run without extra processes, keep exactly the
  previous columns.
- **Flow table:** `result.to_process_flow_frame()` has one row per nonzero
  flow, SKU and period (`PROCESS_FLOW_COLUMNS`), including its `phase`,
  `direction` and `category`. It is empty for a run without processes.
- **Manifest:** `run_settings["processes"]` lists each process's position,
  name, class, declared flows, overridden hooks and `get_config()`. It is
  absent when `processes=` is not used.

Stock corrections that happen once on a known date are better modelled as a
`ScheduledInventoryAdjustment` callback (Notebook 08). Use a process for a
recurring physical mechanism. Notebook 05e builds the examples above step by
step, and Notebook 07 applies them to the M5 perishable scenario.
