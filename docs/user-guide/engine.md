# The simulation engine

`SimulationEngine` runs the clock. It validates every input, plays the
system forward one period at a time, applies every change to stock, and
returns a `SimulationResult` with the complete record of what happened.

## `run`

```py
result = SimulationEngine(verbose=0).run(
    policy, demand_source, inventory, n_periods,
    *, period_frequency, warmup_periods, scoring_periods, settlement_periods,
    order_during_settlement, demand_source_name, random_seed,
    policy_schedule=None, order_constraints=None, callbacks=None,
    supply=None, processes=None,
)
```

| Argument | Meaning |
|---|---|
| `policy` | A fitted policy. |
| `demand_source` | A [demand table](demand.md), or a function `period -> DataFrame`. |
| `inventory` | The opening `InventoryStateDataFrame`. |
| `n_periods` | Number of demand periods to simulate. |
| `period_frequency` | Length of one period, a pandas frequency such as `"D"`. |
| `warmup_periods`, `scoring_periods`, `settlement_periods` | Consecutive windows that add up to `n_periods`; scoring must be at least 1. |
| `order_during_settlement` | Whether the policy may still order in the settlement window. |
| `demand_source_name` | A label for the demand, stored in the manifest. |
| `random_seed` | The seed behind the demand, or `None` if no randomness is involved. |
| `policy_schedule` | `{decision_period: fitted_policy}`: fresh targets at later decisions. |
| `order_constraints` | An [`OrderingConstraints`](constraints.md) sequence. |
| `callbacks` | A list of [callbacks](callbacks.md), applied in order. |
| `supply` | A [`SupplyModel`](suppliers.md): suppliers, lead times, split deliveries. |
| `processes` | A list of [inventory processes](processes.md), such as `ShelfLife`. |

The first block of arguments describes the experiment and is always required.
The optional ones switch on extra building blocks; leave them out and the run
uses none of that code.

## Before the first period

`run` checks the whole experiment up front, so a run either starts on solid
ground or explains what is missing:

- the policy is fitted, and the state's `max_lead_time` covers the policy's
  lead time (and the supply model's longest delivery);
- the demand table is a complete, dated grid ([details](demand.md#what-the-engine-checks));
- the policy's forecast origin and frequency match the simulation calendar,
  and every scheduled decision has a target for its own window;
- every `policy_schedule` snapshot has the same configuration as `policy` and
  the right forecast origin;
- window lengths add up to `n_periods`.

The engine then copies the state and the policy. Your objects are never
changed, so you can reuse them for the next run.

## During each period

Each period follows [receive, decide, then meet demand](concepts/timing.md).
After each period, the engine writes one ledger row per SKU and checks its
[balance identities](concepts/accounting.md). The core loop runs on NumPy
arrays, so thousands of SKUs over long horizons simulate quickly.

## Run windows

![Run windows](../assets/figures/run-windows.svg)

| Window | Purpose | Orders allowed |
|---|---|---|
| Warm-up | Let the system move away from the opening state | yes |
| Scoring | The periods your metrics describe | yes |
| Settlement | Let late orders arrive and tails play out | only if `order_during_settlement=True` |

Every window moves stock; only the scoring window counts by default. Each
ledger row carries its window in `run_window`.

## The result

??? example "Setup: the tea shop from Learn the basics"

    ```python
    --8<-- "learn-setup.py"
    ```

| Member | Returns |
|---|---|
| `to_event_frame(window=None)` | The [event ledger](concepts/accounting.md), optionally one window (`"warmup"`, `"scoring"`, `"settlement"`, `"all"`) |
| `to_order_frame()` | One row per scheduled delivery: supplier, order and due periods, status |
| `to_callback_audit_frame()` | One row per accepted callback effect, with reason and source |
| `to_process_flow_frame()` | One row per inventory-process flow |
| `summary()` | A dict of headline numbers for the scoring window |
| `run_manifest` | The record of inputs, settings, and versions |
| `inventory` | The final state |
| `history` | State snapshots after each period |

```python
{key: round(float(value), 3) for key, value in result.summary().items()}
```

```text
{'fill_rate': 1.0, 'demand_period_service_level': 1.0, 'mean_ending_on_hand_per_sku_period': 18.607, 'stockout_periods': 0.0, 'total_order_units': 333.0, 'order_event_count': 14.0, 'sku_order_line_count': 14.0}
```

### The run manifest

Every result carries a manifest, a JSON-friendly dictionary with eight
sections:

```python
list(result.run_manifest)
```

```text
['run_id', 'created_at_utc', 'demand_source', 'package', 'policy', 'opening_inventory', 'run_settings', 'dependencies']
```

| Section | Records |
|---|---|
| `run_id`, `created_at_utc` | A unique id and timestamp |
| `demand_source` | Name, fingerprint, row count, seed, and generator settings of the demand |
| `package`, `dependencies` | Stockcast version (and source commit when available); Python, NumPy, pandas, and Matplotlib versions |
| `policy` | Policy class, configuration, and target metadata |
| `opening_inventory` | A fingerprint of the opening state and open orders |
| `run_settings` | Frequency, windows, schedule, constraints, callbacks, supply, processes, timing convention |

Store the manifest next to the ledger and any result can be traced back to
its inputs.

## `run_comparison`

`run_comparison` runs several fitted policies on the same experiment:

```python
comparison = SimulationEngine().run_comparison(
    policies=[tea_policy(0.80), tea_policy(0.95)],
    labels=["80%", "95%"],
    demand_source=demand,
    inventory=inventory,
    **run_settings,
)
comparison.summary()[["fill_rate", "mean_ending_on_hand_per_sku_period"]]
```

```text
        fill_rate  mean_ending_on_hand_per_sku_period
policy
80%      0.994083                           13.964286
95%      1.000000                           18.607143
```

- The demand is built once and shared by every branch.
- Each branch starts from its own copy of the opening state.
- Constraints, callbacks, suppliers, and processes apply to every branch;
  callbacks and processes are reset between branches, and random supplier
  lead times are drawn once and shared.
- `policy_schedules=[...]` gives each branch its own refits.

`comparison[label]` is a normal `SimulationResult`; iterate over `comparison`
for the labels.

## `ShelfLifeEngine`

`ShelfLifeEngine(shelf_life_days)` is a `SimulationEngine` with FIFO shelf
life built in. Its `run` and `run_comparison` take the same arguments plus
`opening_lots`. It gives the same results as
`SimulationEngine().run(..., processes=[ShelfLife(...)])`. See
[Shelf life and inventory processes](processes.md).

## Logging

`SimulationEngine(verbose=1)` prints start, end, and milestones;
`verbose=2` prints a line for every period.

**Go deeper:** [Learn step 5](../learn/05-engine.md) ·
[API: engine](../reference/engine.md) ·
[Notebook 02](../notebooks/02_first_engine_simulation.ipynb) ·
[Notebook 03: your own loop](../notebooks/03_component_loop.ipynb)
