# First simulation

This example makes one small, explicit daily scenario. It creates observed
opening stock, supplies a cumulative protection target calculated by an
external forecasting process, and evaluates the resulting event ledger.

```python
import pandas as pd

from stockcast import InventoryStateDataFrame, OrderUpToPolicy, SimulationEngine
from stockcast.evaluation import InventoryEvaluator, fill_rate

sku = "tea_250g"
origin = pd.Timestamp("2026-01-05")

inventory = InventoryStateDataFrame(
    [sku], max_lead_time=1, allow_backorders=False
).initialize_from_observed(
    pd.DataFrame({"unique_id": [sku], "on_hand": [5.0]}),
    on_hand_column="on_hand",
    start_date=origin,
)

# Cumulative two-day target calculated outside Stockcast.
target = pd.DataFrame({
    "unique_id": [sku],
    "target": [20.0],
    "target_end_date": [origin + pd.Timedelta(days=2)],
})
policy = OrderUpToPolicy(
    lead_time=1, review_period=1, service_level=0.95, allow_backorders=False
).fit(
    target,
    target_column="target",
    target_probability=0.95,
    protection_horizon=2,
    target_source="external_direct",
    forecast_origin=origin,
    forecast_frequency="D",
    target_end_date_column="target_end_date",
)

demand = pd.DataFrame({
    "unique_id": [sku] * 6,
    "period": range(6),
    "date": pd.date_range(origin + pd.Timedelta(days=1), periods=6, freq="D"),
    "y": [4.0] * 6,
})

result = SimulationEngine().run(
    policy=policy,
    demand_source=demand,
    inventory=inventory,
    n_periods=6,
    period_frequency="D",
    initial_decision="before_first_demand",
    warmup_periods=0,
    scoring_periods=6,
    settlement_periods=0,
    order_during_settlement=False,
    demand_source_name="first_simulation",
    random_seed=None,
)

events = result.to_event_frame(window="scoring")
print(events[["date", "demand", "order_quantity", "ending_on_hand"]].to_string(index=False))

score = InventoryEvaluator().fit(result, window="scoring").evaluate(
    metrics=[fill_rate], groupby=[]
)
print(score.to_string(index=False))
```

The target covers demand over `lead_time + review_period`; it is not a list of
marginal daily quantiles. The engine validates the demand calendar, owns
receipts and demand transitions, and writes the canonical event rows used for
evaluation.

Next, read [inputs and time](../concepts/input-and-time.md) or open Notebook
[02 — First engine simulation](https://github.com/FilTheo/stockcast/blob/main/examples/notebooks/02_first_engine_simulation.ipynb).
