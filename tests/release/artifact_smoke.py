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

import numpy as np
import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.evaluation import InventoryEvaluator, avg_on_hand, fill_rate
from stockcast.policies import OrderUpToPolicy

# Eight weeks of daily tea sales.
days = pd.date_range("2026-02-02", periods=56, freq="D")
demand = pd.DataFrame({
    "unique_id": "tea",
    "date": days,
    "y": np.random.default_rng(3).poisson(6, len(days)),
})

# The forecast, from any model: a day's demand is 10 packs or fewer, with
# 95% probability.
forecast = pd.DataFrame({
    "unique_id": ["tea"],
    "date": [days[0]],   # the day it forecasts
    "q95": [10],         # the 95% quantile
})

# The shelf today.
shelf = InventoryStateDataFrame.from_observed(pd.DataFrame({
    "unique_id": ["tea"],
    "date": [pd.Timestamp("2026-02-01")],
    "on_hand": [30],
}))

policy = OrderUpToPolicy(
    lead_time=0,             # delivered before the shop opens
    review_period=1,         # order every morning
    freq="D",                # one period is one day
    service_level=0.95,      # the probability the forecast quantile stands for
    allow_backorders=False,  # a missed sale is lost
)
policy.fit(forecast, target_column="q95")

result = SimulationEngine().run(policy=policy, demand_source=demand, inventory=shelf)
scores = InventoryEvaluator().fit(result).evaluate([fill_rate, avg_on_hand]).round(2)
assert scores.loc[0, "fill_rate"] == 0.99
assert scores.loc[0, "avg_on_hand"] == 4.75
print(scores)
assert stockcast.__version__ == importlib.metadata.version("stockcast")
