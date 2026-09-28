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
from stockcast.utils import DemandGenerator


# Weekly sales of two products over 20 weeks: Poisson demand, 20 units a week.
def poisson(rng, periods):
    return rng.poisson(20, periods.size)


generator = DemandGenerator(
    ["coffee", "tea"], start_date="2026-01-05", freq="W-MON", random_seed=0,
)
sales = generator.sample(20, poisson)
sales.head(3)
#   unique_id     y  period       date
# 0    coffee  22.0       0 2026-01-05
# 1       tea  24.0       0 2026-01-05
# 2    coffee   9.0       1 2026-01-12

# Today is week 12: we know the past, the future is still to come.
today = pd.Timestamp("2026-03-23")
past = sales[sales["date"] <= today]
future = sales[sales["date"] > today]

# Forecast: the 95% quantile of next week's demand, for each product.
# Here from the last 12 weeks; any quantile forecasting model works.
forecast = past.groupby("unique_id", as_index=False)["y"].quantile(0.95)
forecast["date"] = today + pd.Timedelta(weeks=1)   # the week it forecasts

# The policy: order every Monday, delivered the same morning, so each order
# covers one week. It orders up to the forecast.
policy = OrderUpToPolicy(
    lead_time=0, review_period=1, freq="W-MON", service_level=0.95, allow_backorders=False,
)
policy.fit(forecast, target_column="y")

# The shelf today: 30 units of each product.
stock = pd.DataFrame({"unique_id": ["coffee", "tea"], "date": today, "on_hand": [30, 30]})
shelf = InventoryStateDataFrame.from_observed(stock)

# Simulate the next 8 weeks and score the decisions.
engine = SimulationEngine()
result = engine.run(policy, future, shelf)

evaluator = InventoryEvaluator()
evaluator.fit(result)
scores = evaluator.evaluate([fill_rate, avg_on_hand]).round(2)
assert scores.loc[0, "fill_rate"] == 0.99
assert scores.loc[0, "avg_on_hand"] == 5.71
print(scores)
assert stockcast.__version__ == importlib.metadata.version("stockcast")
