"""Installed-artifact imports and README quick-start smoke.

The quick-start below is the README's; keep the two in step (the README block
itself is also executed by tests/docs/test_docs_examples.py).
"""

import stockcast  # noqa: F401
import stockcast.core  # noqa: F401
import stockcast.evaluation  # noqa: F401
import stockcast.policies  # noqa: F401
import stockcast.utils  # noqa: F401
import stockcast.visualization  # noqa: F401

import numpy as np
import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.evaluation import InventoryEvaluator, avg_on_hand, fill_rate
from stockcast.policies import OrderUpToPolicy
from stockcast.utils import DemandGenerator

sku, opening = "tea_250g", pd.Timestamp("2026-01-05")
lead_time, review_period = 2, 4                 # deliveries take 2 days; order every 4
horizon = lead_time + review_period             # each order must cover 6 days

# Eight weeks of daily demand, and 30 packs on the shelf to start with.
demand = DemandGenerator([sku], start_date=opening + pd.Timedelta(days=1),
                         period_frequency="D", seed=3,
                         negative_demand_handling="clip_zero").seasonal(
    n_periods=56, base=6.0, amplitude=2.0, season_length=7, std=2.0)
inventory = InventoryStateDataFrame([sku], max_lead_time=lead_time, allow_backorders=False)
inventory.initialize_from_observed(pd.DataFrame({"unique_id": [sku], "on_hand": [30.0]}),
                                   on_hand_column="on_hand", start_date=opening)

# Your forecasting model's sample paths -> the 95% quantile of 6-day total demand.
paths = np.random.default_rng(42).poisson(6.0, size=(10_000, horizon))
target = pd.DataFrame({"unique_id": [sku],
                       "target": [np.quantile(paths.sum(axis=1), 0.95)],
                       "end": [opening + pd.Timedelta(days=horizon)]})

# Order up to that target every 4 days.
policy = OrderUpToPolicy(lead_time=lead_time, review_period=review_period,
                         service_level=0.95, allow_backorders=False).fit(
    target, target_column="target", target_probability=0.95,
    protection_horizon=horizon, target_source="external_direct",
    forecast_origin=opening, forecast_frequency="D", target_end_date_column="end")

# Play eight weeks forward, then measure what happened.
result = SimulationEngine().run(
    policy=policy, demand_source=demand, inventory=inventory, n_periods=56,
    period_frequency="D", warmup_periods=0, scoring_periods=56, settlement_periods=0,
    order_during_settlement=False, demand_source_name="tea_shop", random_seed=3)

score = InventoryEvaluator().fit(result, window="scoring").evaluate(
    [fill_rate, avg_on_hand], groupby=[]).round(2)
assert score.loc[0, "fill_rate"] == 1.0
assert score.loc[0, "avg_on_hand"] == 18.71
print(score)
