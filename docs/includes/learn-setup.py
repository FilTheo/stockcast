import numpy as np
import pandas as pd

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.policies import OrderUpToPolicy
from stockcast.utils import DemandGenerator

sku = "tea_250g"
opening_date = pd.Timestamp("2026-01-05")
lead_time, review_period = 2, 4
horizon = lead_time + review_period

demand = DemandGenerator(
    [sku], start_date=opening_date + pd.Timedelta(days=1), period_frequency="D",
    seed=3, negative_demand_handling="clip_zero",
).seasonal(n_periods=56, base=6.0, amplitude=2.0, season_length=7, std=2.0)
demand["y"] = demand["y"].round()

inventory = InventoryStateDataFrame(
    [sku], max_lead_time=lead_time, allow_backorders=False,
).initialize_from_observed(
    pd.DataFrame({"unique_id": [sku], "on_hand": [30.0]}),
    on_hand_column="on_hand", start_date=opening_date,
)

paths = np.random.default_rng(42).poisson(6.0, size=(10_000, horizon))


def tea_policy(probability):
    """Order-up-to policy whose target is the given quantile of 6-day demand."""
    target = pd.DataFrame({
        "unique_id": [sku],
        "target": [np.quantile(paths.sum(axis=1), probability)],
        "target_end_date": [opening_date + pd.Timedelta(days=horizon)],
    })
    return OrderUpToPolicy(
        lead_time=lead_time, review_period=review_period,
        service_level=probability, allow_backorders=False,
    ).fit(
        target, target_column="target", target_probability=probability,
        protection_horizon=horizon, target_source="external_direct",
        forecast_origin=opening_date, forecast_frequency="D",
        target_end_date_column="target_end_date",
    )


policy = tea_policy(0.95)
run_settings = dict(
    n_periods=56, period_frequency="D", warmup_periods=0, scoring_periods=56,
    settlement_periods=0, order_during_settlement=False,
    demand_source_name="tea_shop", random_seed=3,
)
result = SimulationEngine().run(
    policy=policy, demand_source=demand, inventory=inventory, **run_settings,
)
