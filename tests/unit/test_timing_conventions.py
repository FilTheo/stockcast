"""Other timing conventions map exactly onto the before-demand engine.

Each test writes a convention from the literature as an independent loop and
checks that Stockcast, set as ``docs/how-to/timing-conventions.md`` says,
places the same orders and holds the same stock.
"""

import numpy as np
import pandas as pd
import pytest

from stockcast.core import InventoryStateDataFrame, PeriodicSchedule, ShelfLife, SimulationEngine
from stockcast.policies import OrderUpToPolicy

SKU = "A"
OPENING = pd.Timestamp("2026-01-04")
DAY = pd.Timedelta(days=1)
N = 40


def demand_frame(demand):
    return pd.DataFrame({
        "unique_id": SKU, "date": OPENING + DAY * np.arange(1, len(demand) + 1), "y": demand,
    })


def opening_state(on_hand, backlog):
    return InventoryStateDataFrame.from_observed(
        pd.DataFrame({"unique_id": [SKU], "date": [OPENING], "on_hand": [float(on_hand)]}),
        allow_backorders=backlog,
    )


def level_policy(lead_time, review, level, *, backlog, start=0, origin=OPENING):
    horizon = lead_time + review
    return OrderUpToPolicy(
        lead_time=lead_time, schedule=PeriodicSchedule(every=review, start=start), freq="D",
        allow_backorders=backlog,
    ).fit(
        pd.DataFrame({"unique_id": [SKU], "target": [float(level)],
                      "date": [origin + horizon * DAY]}),
        target_column="target",
    )


def run(policy, demand, on_hand, backlog, **kwargs):
    return SimulationEngine().run(
        policy=policy, demand_source=demand_frame(demand),
        inventory=opening_state(on_hand, backlog), **kwargs,
    ).to_event_frame()


def order_after_demand(demand, L, R, level, on_hand, *, backlog, receipt_before_demand=True):
    """End-of-period order-up-to: Q[t] placed after demand at t, due in period t + L."""
    orders, stock, pipeline, back = np.zeros(len(demand)), [], {}, 0.0
    for t, d in enumerate(demand):
        if receipt_before_demand:
            on_hand += pipeline.pop(t, 0.0)
        served = min(on_hand, back)
        on_hand, back = on_hand - served, back - served
        met = min(on_hand, d)
        on_hand -= met
        if backlog:
            back += d - met
        if not receipt_before_demand:
            on_hand += pipeline.pop(t, 0.0)
        stock.append(on_hand)
        if t % R == 0:
            orders[t] = max(0.0, level - (on_hand + sum(pipeline.values()) - back))
            if orders[t] > 0:
                pipeline[t + L] = orders[t]
    return orders, np.array(stock)


@pytest.mark.parametrize("backlog", [True, False])
@pytest.mark.parametrize("R", [1, 3, 7])
@pytest.mark.parametrize("L", [1, 2, 3, 5])
def test_end_of_period_order_is_lead_time_minus_one_a_period_later(L, R, backlog):
    """Case 2: order after demand, due at the start of t + L."""
    rng = np.random.default_rng(100 * L + 10 * R + backlog)
    demand = rng.poisson(5, N).astype(float)
    level, on_hand = 5.0 * (L + R) + 4, 12.0
    source_orders, source_stock = order_after_demand(demand, L, R, level, on_hand, backlog=backlog)

    events = run(level_policy(L - 1, R, level, backlog=backlog, start=1), demand, on_hand, backlog)

    orders = events["order_quantity"].to_numpy(dtype=float)
    assert orders[0] == 0.0
    np.testing.assert_array_equal(orders[1:], source_orders[:-1])
    np.testing.assert_array_equal(events["ending_on_hand"].to_numpy(dtype=float), source_stock)


@pytest.mark.parametrize("R", [1, 3])
@pytest.mark.parametrize("lead", [0, 1, 4])
def test_arrival_at_start_of_t_plus_l_plus_one_is_lead_time_l(lead, R):
    """Case 3: order at the end of t arriving at the start of t + l + 1."""
    demand = np.random.default_rng(lead + 7 * R).poisson(4, N).astype(float)
    level = 4.0 * (lead + R) + 3
    source_orders, source_stock = order_after_demand(demand, lead + 1, R, level, 10.0, backlog=False)

    events = run(level_policy(lead, R, level, backlog=False, start=1), demand, 10.0, False)

    np.testing.assert_array_equal(events["order_quantity"].to_numpy(dtype=float)[1:],
                                  source_orders[:-1])
    np.testing.assert_array_equal(events["ending_on_hand"].to_numpy(dtype=float), source_stock)


@pytest.mark.parametrize("L", [1, 2, 4])
def test_lost_sales_receipt_after_demand_is_lead_time_l(L):
    """A receipt landing after the period's demand first sells a period later."""
    demand = np.random.default_rng(L).poisson(5, N).astype(float)
    level = 5.0 * (L + 1) + 4
    source_orders, _ = order_after_demand(
        demand, L, 1, level, 12.0, backlog=False, receipt_before_demand=False,
    )

    events = run(level_policy(L, 1, level, backlog=False, start=1), demand, 12.0, False)

    np.testing.assert_array_equal(events["order_quantity"].to_numpy(dtype=float)[1:],
                                  source_orders[:-1])


@pytest.mark.parametrize("L", [1, 2, 4])
def test_decide_before_demand_base_stock_matches_the_classic_model(L):
    """Case 1: order, receive, demand; with S every period q[n+1] = y[n]."""
    demand = np.random.default_rng(3 * L).poisson(5, N).astype(float)
    level = 5.0 * (L + 1) + 3

    events = run(level_policy(L, 1, level, backlog=False), demand, level, False)

    fulfilled = demand - events["shortage_units"].to_numpy(dtype=float)
    orders = events["order_quantity"].to_numpy(dtype=float)
    assert orders[0] == 0.0
    np.testing.assert_array_equal(orders[1:], fulfilled[:-1])


def test_zero_lead_time_serves_the_period_it_is_ordered_for():
    """Case 5: a level set before the period is on the shelf for its demand."""
    rng = np.random.default_rng(1)
    demand = rng.poisson(4, N).astype(float)
    levels = np.maximum(0, np.round(4 + 2 * rng.standard_normal(N)))
    snapshots = {
        t: level_policy(0, 1, levels[t], backlog=False, origin=OPENING + t * DAY)
        for t in range(1, N)
    }
    no_lots = pd.DataFrame({
        "unique_id": pd.Series(dtype=object),
        "received_date": pd.Series(dtype="datetime64[ns]"),
        "quantity": pd.Series(dtype=float),
    })

    events = run(
        level_policy(0, 1, levels[0], backlog=False), demand, 0.0, False,
        policy_schedule=snapshots, processes=[ShelfLife(1, no_lots)],
    )

    np.testing.assert_array_equal(events["order_quantity"].to_numpy(dtype=float), levels)
    np.testing.assert_array_equal(events["ending_on_hand"].to_numpy(dtype=float),
                                  np.maximum(levels - demand, 0))
    np.testing.assert_array_equal(events["shortage_units"].to_numpy(dtype=float),
                                  np.maximum(demand - levels, 0))
