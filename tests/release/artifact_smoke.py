"""Installed-artifact imports and README quick-start smoke.

The quick-start below is the README's; keep the two in step (the README block
itself is also executed by tests/docs/test_docs_examples.py).
"""

import importlib.metadata

import stockcast
import stockcast.core  # noqa: F401
import stockcast.evaluation  # noqa: F401
import stockcast.policies  # noqa: F401
import stockcast.utils  # noqa: F401
import stockcast.visualization  # noqa: F401

import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.evaluation import InventoryEvaluator, avg_on_hand, fill_rate
from stockcast.policies import OrderUpToPolicy

sku = "tea_250g"
opening = pd.Timestamp("2026-01-05")

# 1. Four weeks of the shop's daily sales, starting the day after opening.
sales = [6, 7, 8, 6, 2, 3, 8, 6, 8, 4, 9, 5, 6, 5,
         2, 10, 3, 5, 7, 9, 8, 10, 4, 9, 5, 6, 2, 3]
demand = pd.DataFrame({
    "unique_id": sku,
    "period": range(28),
    "date": pd.date_range("2026-01-06", periods=28),
    "y": sales,
})

# 2. The shelf on the opening day: 30 packs, nothing on order yet.
inventory = InventoryStateDataFrame([sku], max_lead_time=2)
inventory.initialize_from_observed(
    pd.DataFrame({"unique_id": [sku], "on_hand": [30.0]}),
    on_hand_column="on_hand",
    start_date=opening,
)

# 3. Your forecast: a 95% chance that the next 6 days sell at most 46 packs.
#    Six days, because an order takes 2 days to arrive and the next one is 4 days away.
target = pd.DataFrame({
    "unique_id": [sku],
    "target": [46.0],
    "end": [opening + pd.Timedelta(days=6)],
})

# 4. The policy: every 4 days, order enough to bring stock up to that target.
policy = OrderUpToPolicy(
    lead_time=2,
    review_period=4,
    service_level=0.95,
    allow_backorders=False,           # a customer who finds no tea leaves
).fit(
    target,
    target_column="target",
    target_probability=0.95,          # the target is a 95% quantile...
    protection_horizon=6,             # ...of total demand over 6 days
    target_end_date_column="end",
    target_source="external_direct",  # computed by your model, not by Stockcast
    forecast_origin=opening,
    forecast_frequency="D",
)

# 5. Replay the four weeks with that policy, then measure what happened.
result = SimulationEngine().run(
    policy=policy,
    demand_source=demand,
    inventory=inventory,
    n_periods=28,
    period_frequency="D",
    warmup_periods=0, scoring_periods=28, settlement_periods=0,  # score all 28 days
    order_during_settlement=False,
    demand_source_name="tea_shop_sales",  # a label saved with the results
    random_seed=None,                 # nothing random here
)

score = InventoryEvaluator().fit(result, window="scoring").evaluate(
    [fill_rate, avg_on_hand], groupby=[]).round(2)
assert score.loc[0, "fill_rate"] == 1.0
assert score.loc[0, "avg_on_hand"] == 19.86
print(score)
assert stockcast.__version__ == importlib.metadata.version("stockcast")
