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
from stockcast.evaluation import InventoryEvaluator, fill_rate, total_cost
from stockcast.policies import OrderUpToPolicy

# The setting: a store orders coffee every Monday, deliveries take 2 weeks,
# and a customer who finds an empty shelf buys elsewhere.
lead_time = 2              # weeks
review_period = 1          # weeks
holding_cost = 0.2         # per pack per week on the shelf
lost_sale_cost = 1.0       # per pack of demand not served
today = pd.Timestamp("2026-03-23")   # a Monday

# Past sales: the last 12 weeks.
past_sales = pd.Series([21, 18, 25, 19, 23, 30, 17, 22, 26, 20, 24, 19])

# What each order must cover: the lead time plus the review period (3 weeks),
# at the service level the costs imply (the critical ratio, 0.83).
coverage = lead_time + review_period
service_level = lost_sale_cost / (lost_sale_cost + holding_cost)

# The forecast: the 83% quantile of demand over the next 3 weeks.
# Here from past 3-week totals; in practice, from your forecasting model.
three_week_totals = past_sales.rolling(coverage).sum()
forecast = three_week_totals.quantile(service_level)

# The link: the forecast, made today, becomes the policy's order-up-to level.
target = pd.DataFrame({"unique_id": ["coffee"], "order_up_to": [forecast]})
policy = OrderUpToPolicy(
    lead_time=lead_time,
    review_period=review_period,
    service_level=service_level,
    allow_backorders=False,
)
policy.fit(
    target,
    target_column="order_up_to",
    forecast_origin=today,
    forecast_frequency="W-MON",
)

# The shelf today: 40 packs.
shelf = InventoryStateDataFrame.from_observed(
    pd.DataFrame({"unique_id": ["coffee"], "on_hand": [40]}), start_date=today,
)

# Demand over the next 8 weeks: the sales the store will face.
demand = pd.DataFrame({
    "unique_id": "coffee",
    "period": range(8),
    "date": pd.date_range(today + pd.Timedelta(weeks=1), periods=8, freq="W-MON"),
    "y": [23, 28, 18, 21, 25, 22, 31, 20],
})

# Simulate: order every Monday, receive two weeks later, sell from the shelf.
result = SimulationEngine().run(policy=policy, demand_source=demand, inventory=shelf)

# Score the decisions: service and cost.
evaluator = InventoryEvaluator()
evaluator.fit(result, window="scoring")
scores = evaluator.evaluate(
    [fill_rate, total_cost],
    groupby=[],
    context={
        "cost_components": ["holding", "shortage"],
        "holding_cost_per_unit_period": holding_cost,
        "shortage_cost_per_unit": lost_sale_cost,
    },
)
scores = scores.round(2)
assert scores.loc[0, "fill_rate"] == 0.9
assert scores.loc[0, "total_cost"] == 30.2
print(scores)
assert stockcast.__version__ == importlib.metadata.version("stockcast")
