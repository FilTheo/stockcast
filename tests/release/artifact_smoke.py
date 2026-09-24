"""Installed-artifact imports and README quick-start smoke."""

import stockcast
import stockcast.core
import stockcast.evaluation
import stockcast.policies
import stockcast.utils
import stockcast.visualization
import pandas as pd


sku = "tea_250g"
origin = pd.Timestamp("2026-01-05")
inventory = stockcast.InventoryStateDataFrame(
    [sku], max_lead_time=1, allow_backorders=False
).initialize_from_observed(
    pd.DataFrame({"unique_id": [sku], "on_hand": [5.0]}),
    on_hand_column="on_hand",
    start_date=origin,
)
target = pd.DataFrame(
    {
        "unique_id": [sku],
        "target": [20.0],
        "target_end_date": [origin + pd.Timedelta(days=2)],
    }
)
policy = stockcast.OrderUpToPolicy(
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
demand = pd.DataFrame(
    {
        "unique_id": [sku] * 6,
        "period": range(6),
        "date": pd.date_range(origin + pd.Timedelta(days=1), periods=6, freq="D"),
        "y": [4.0] * 6,
    }
)
result = stockcast.SimulationEngine().run(
    policy=policy,
    demand_source=demand,
    inventory=inventory,
    n_periods=6,
    period_frequency="D",
    initial_decision="none",
    warmup_periods=0,
    scoring_periods=6,
    settlement_periods=0,
    order_during_settlement=False,
    demand_source_name="readme_example",
    random_seed=None,
)
summary = result.summary()
assert summary["fill_rate"] == 1.0
assert summary["total_order_units"] == 35.0
print({key: summary[key] for key in ("fill_rate", "total_order_units")})
