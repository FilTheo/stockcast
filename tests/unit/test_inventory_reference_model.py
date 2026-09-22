"""Independent scalar inventory oracle for contracts 20, 30, 40 and 60.

Deliberately uses a delivery-date dictionary, rather than the engine's pipeline
array, and checks every period against the public event ledger.
"""

import numpy as np
import pandas as pd
import pytest

from pyforia import InventoryStateDataFrame, OrderUpToPolicy, SimulationEngine
from pyforia.evaluation import fill_rate, total_cost


@pytest.mark.parametrize("backorders", [False, True])
@pytest.mark.parametrize("lead,review", [(1, 1), (2, 3), (4, 2)])
@pytest.mark.parametrize("opening_decision", [False, True])
def test_inventory_matches_delivery_calendar_oracle(backorders, lead, review, opening_decision):
    origin = pd.Timestamp("2026-01-01")
    demand = np.random.default_rng(142).integers(0, 18, size=24).astype(float)
    target, opening = 23.0, 3.0
    inventory = InventoryStateDataFrame(
        ["A"], max_lead_time=lead, allow_backorders=backorders,
    ).initialize_from_observed(
        pd.DataFrame({"unique_id": ["A"], "stock": [opening]}),
        on_hand_column="stock", start_date=origin,
    )
    policy = OrderUpToPolicy(
        lead_time=lead, review_period=review, service_level=0.95,
        allow_backorders=backorders,
    ).fit(
        pd.DataFrame({"unique_id": ["A"], "target": [target],
                      "end": [origin + pd.Timedelta(days=lead + review)]}),
        forecast_origin=origin, forecast_frequency="D", target_column="target",
        target_end_date_column="end", target_probability=0.95,
        protection_horizon=lead + review, target_source="external_direct",
    )
    result = SimulationEngine().run(
        policy=policy, inventory=inventory,
        demand_source=pd.DataFrame({
            "unique_id": ["A"] * len(demand), "y": demand,
            "period": range(len(demand)),
            "date": pd.date_range(origin + pd.Timedelta(days=1), periods=len(demand)),
        }),
        n_periods=len(demand), period_frequency="D",
        initial_decision="before_first_demand" if opening_decision else "none",
        warmup_periods=0, scoring_periods=20, settlement_periods=4,
        order_during_settlement=False, demand_source_name="independent_reference",
        random_seed=142,
    )
    on_hand, backlog, deliveries = opening, 0.0, {}
    if opening_decision:
        deliveries[lead] = target - opening
    expected = []
    for day, requested in enumerate(demand, start=1):
        received = deliveries.pop(day, 0.0)
        cleared = min(backlog, received)
        backlog -= cleared
        on_hand += received - cleared
        served = min(on_hand, requested)
        on_hand -= served
        shortage = requested - served
        if backorders:
            backlog += shortage
        quantity = 0.0
        if day <= 20 and day % review == 0:
            quantity = max(0.0, target - (on_hand + sum(deliveries.values()) - backlog))
            deliveries[day + lead] = deliveries.get(day + lead, 0.0) + quantity
        expected.append([received, served, shortage, cleared, on_hand, backlog,
                         sum(deliveries.values()), quantity])
    columns = ["received_units", "fulfilled_units", "shortage_units",
               "backorders_fulfilled", "ending_on_hand", "backorders_end",
               "on_order_end", "order_quantity"]
    events = result.to_event_frame(window="all")
    periods = events.loc[events.event_type.eq("period")]
    np.testing.assert_allclose(periods[columns].to_numpy(float), expected, rtol=0, atol=1e-9)
    np.testing.assert_allclose(periods.lost_sales_units, 0 if backorders else periods.shortage_units)
    assert inventory.get_dataframe().on_hand.iloc[0] == opening
    scoring = periods.iloc[:20]
    assert fill_rate(scoring) == pytest.approx(sum(row[1] for row in expected[:20]) / demand[:20].sum())
    # Explicit SKU-line charges, ending-stock holding, immediate shortages.
    context = {"cost_components": ["holding", "shortage", "ordering"],
               "holding_cost_per_unit_period": 0.2, "shortage_cost_per_unit": 3.0,
               "order_cost_per_sku_line": 2.0, "order_cost_per_unit": 0.1}
    cost = sum(0.2 * row[4] + 3 * row[2] + 2 * (row[7] > 0) + 0.1 * row[7]
               for row in expected[:20])
    assert total_cost(scoring, context) == pytest.approx(cost)
