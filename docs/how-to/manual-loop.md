# Run your own simulation loop

`SimulationEngine` is the complete, checked way to run a simulation. Sometimes
you want the steps in your own hands: to plug Stockcast into another
simulator, to step through a period while teaching, or to drive a live
process one day at a time. The same building blocks the engine uses are
public.

## The three primitives

| Step | Call | Does |
|---|---|---|
| 1. Receive | `state.advance_period(period_frequency=..., is_review_period=...)` | moves to the next period, receives due deliveries, serves old backorders first |
| 2. Decide | `policy.predict(state, current_period=...)`, then `update_inventory_with_orders(state, decision, policy=policy)` | places the order: into the pipeline, or received now if $L = 0$ |
| 3. Meet demand | `state.fulfill_demand(demand_rows)` | serves this period's demand; lost sales or backorders |

Each call returns a **new** state, so earlier states stay available.

## A loop that matches the engine

??? example "Setup: the tea shop from Learn the basics"

    ```python
    --8<-- "learn-setup.py"
    ```

```python
from stockcast.utils import update_inventory_with_orders

state = inventory
ending_on_hand = []

for p in range(56):
    review = p % review_period == 0
    state = state.advance_period(period_frequency="D", is_review_period=review)   # 1
    if review:
        now = int(state.get_dataframe()["period"].iloc[0])
        decision = policy.predict(state, current_period=now)                      # 2
        state = update_inventory_with_orders(state, decision, policy=policy)
    state = state.fulfill_demand(demand[demand["period"] == p])                   # 3
    ending_on_hand.append(float(state.get_dataframe()["on_hand"].iloc[0]))

engine_on_hand = result.to_event_frame()["ending_on_hand"].tolist()
ending_on_hand == engine_on_hand
```

```text
True
```

The loop reproduces the engine's run exactly. Note that `current_period` is
the state's period (the opening state is period 0, so the first demand period
is state period 1).

## What the engine adds

Your loop is responsible for everything the engine otherwise does for you:

| The engine… | In your loop, you… |
|---|---|
| validates the demand calendar, targets, and settings before starting | check inputs yourself |
| copies the opening state and policy | keep track of which objects you change |
| writes and checks one ledger row per SKU and period | record what you need |
| applies callbacks, constraints, suppliers, and processes | apply them yourself, or leave them out |
| builds the run manifest | record your settings |

So: use the engine for experiments and comparisons, and the primitives when
you need control of each step.

## Order lines

`place_order_lines(state, OrderLines(...))` is the order-level version of step
2: explicit deliveries per supplier, with their own due periods and partial
deliveries. See [Suppliers and open orders](../user-guide/suppliers.md#open-orders-in-a-manual-loop).

[Notebook 03](../notebooks/03_component_loop.ipynb) builds this loop step by
step and compares it with the engine.
