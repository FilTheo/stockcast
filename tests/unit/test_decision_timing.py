"""Independent before-demand calendar oracles and schedule/season contracts."""

import numpy as np
import pandas as pd
import pytest

from stockcast import (
    BasePolicy,
    DecisionSchedule,
    ExplicitSchedule,
    InventoryStateDataFrame,
    OneTimeSchedule,
    OrderUpToPolicy,
    PeriodicSchedule,
    SimulationEngine,
    SingleOrderPolicy,
    newsvendor_critical_fractile,
)
from stockcast.core import ShelfLifeEngine
from stockcast.evaluation import validate_event_frame

ORIGIN = pd.Timestamp("2026-01-01")


def run(
    policy, demand, *, engine=None, stock=3.0, pipeline=None, backlog=0.0, **kwargs
):
    state = InventoryStateDataFrame(
        ["A"],
        max_lead_time=max(2, policy.lead_time),
        allow_backorders=policy.allow_backorders,
    ).initialize_zero(start_date=ORIGIN)
    state.data["on_hand"] = stock
    state.data["backorders"] = backlog
    if pipeline is not None:
        state.data.at[0, "in_transit"] = np.array(pipeline, dtype=float)
    return (engine or SimulationEngine()).run(
        policy,
        pd.DataFrame(
            {
                "unique_id": ["A"] * len(demand),
                "y": demand,
                "period": range(len(demand)),
                "date": pd.date_range(
                    ORIGIN + pd.Timedelta(days=1), periods=len(demand)
                ),
            }
        ),
        state,
        len(demand),
        period_frequency="D",
        warmup_periods=0,
        scoring_periods=len(demand) - 2,
        settlement_periods=2,
        order_during_settlement=kwargs.pop("order_during_settlement", False),
        demand_source_name="timing_oracle",
        random_seed=None,
        **kwargs,
    )


def target_policy(
    lead=0,
    review=1,
    *,
    backorders=False,
    schedule=None,
    horizon=None,
    origin=ORIGIN,
    target=23.0,
):
    schedule = schedule or PeriodicSchedule(review)
    horizon = horizon or lead + review
    return OrderUpToPolicy(lead, schedule=schedule, allow_backorders=backorders).fit(
        pd.DataFrame(
            {
                "unique_id": ["A"],
                "S": [target],
                "end": [origin + pd.Timedelta(days=horizon)],
            }
        ),
        forecast_origin=origin,
        forecast_frequency="D",
        target_column="S",
        target_end_date_column="end",
        protection_horizon=horizon,
        target_source="external_direct",
    )


@pytest.mark.parametrize("lead", [0, 1, 2])
@pytest.mark.parametrize("review", [1, 2, 3])
@pytest.mark.parametrize("backorders", [False, True])
def test_before_demand_matches_independent_calendar(lead, review, backorders):
    demands = [9.0, 30.0, 2.0, 0.0, 17.0, 1.0, 22.0, 4.0]
    result = run(target_policy(lead, review, backorders=backorders), demands)
    stock, backlog, calendar = 3.0, 0.0, {}
    expected = []
    for t, demand in enumerate(demands):
        received = calendar.pop(t, 0.0)
        cleared = min(backlog, received)
        backlog -= cleared
        stock += received - cleared
        q = 0.0
        if t % review == 0 and t < len(demands) - 2:
            q = max(0.0, 23.0 - stock - sum(calendar.values()) + backlog)
            if lead == 0:
                immediate_clear = min(backlog, q)
                backlog -= immediate_clear
                cleared += immediate_clear
                stock += q - immediate_clear
                received += q
            else:
                calendar[t + lead] = calendar.get(t + lead, 0.0) + q
        served = min(stock, demand)
        shortage = demand - served
        stock -= served
        if backorders:
            backlog += shortage
        expected.append(
            [
                received,
                cleared,
                q,
                served,
                shortage,
                stock,
                backlog,
                sum(calendar.values()),
            ]
        )
    events = validate_event_frame(result.to_event_frame())
    np.testing.assert_allclose(
        events[
            [
                "received_units",
                "backorders_fulfilled",
                "order_quantity",
                "fulfilled_units",
                "shortage_units",
                "ending_on_hand",
                "backorders_end",
                "on_order_end",
            ]
        ],
        expected,
    )
    assert not events.decision_flag.iloc[-2:].any()
    assert result.inventory.data.on_hand.iloc[0] == events.ending_on_hand.iloc[-1]
    assert result.history.on_hand.iloc[-1] == events.ending_on_hand.iloc[-1]


def test_schedule_semantics_and_custom_policy_has_no_fake_review_or_probability():
    assert PeriodicSchedule(3).next_decision_period(0) == 3
    assert PeriodicSchedule(3, start=2).next_decision_period(0) == 2
    assert OneTimeSchedule(0).next_decision_period(0) is None
    assert ExplicitSchedule([5, 0, 2]).periods == (0, 2, 5)
    assert ExplicitSchedule([0, 2]).next_decision_period(2) is None
    p = BasePolicy(0, schedule=OneTimeSchedule(0), allow_backorders=False)
    assert p.review_period is None and p.service_level is None
    for make in [
        lambda: PeriodicSchedule(0),
        lambda: OneTimeSchedule(True),
        lambda: ExplicitSchedule([0, 0]),
    ]:
        with pytest.raises(ValueError):
            make()


def test_one_time_target_and_newsvendor_economics():
    alpha = newsvendor_critical_fractile(
        selling_price=10, purchase_cost=4, salvage_value=2
    )
    assert alpha == 0.75
    policy = SingleOrderPolicy(
        0, selling_horizon=3, service_level=alpha, allow_backorders=False
    ).fit(
        pd.DataFrame(
            {"unique_id": ["A"], "q75": [12.0], "end": [ORIGIN + pd.Timedelta(days=3)]}
        ),
        forecast_origin=ORIGIN,
        forecast_frequency="D",
        target_column="q75",
        target_probability=alpha,
        target_end_date_column="end",
        target_source="external_direct",
    )
    events = run(policy, [4.0, 5.0, 6.0, 0.0, 0.0], stock=0.0).to_event_frame()
    assert events.order_quantity.tolist() == [12.0, 0.0, 0.0, 0.0, 0.0]
    assert events.fulfilled_units.sum() == 12
    assert events.lost_sales_units.sum() == 3
    assert events.on_order_end.eq(0).all()
    with pytest.raises(ValueError, match="outside the selling season"):
        run(policy, [4.0, 5.0, 6.0, 1.0, 0.0])
    with pytest.raises(ValueError, match="pipeline"):
        run(policy, [4.0, 5.0, 6.0, 0.0, 0.0], pipeline=[0.0, 5.0])


def test_positive_lead_season_delivers_before_season_and_observes_end():
    policy = SingleOrderPolicy(1, selling_horizon=2, allow_backorders=False).fit(
        pd.DataFrame(
            {"unique_id": ["A"], "S": [12.0], "end": [ORIGIN + pd.Timedelta(days=3)]}
        ),
        forecast_origin=ORIGIN,
        forecast_frequency="D",
        target_column="S",
        target_end_date_column="end",
        target_source="external_direct",
    )
    events = run(policy, [0.0, 4.0, 5.0, 0.0, 0.0], stock=0.0).to_event_frame()
    assert events.received_units.tolist() == [0.0, 12.0, 0.0, 0.0, 0.0]
    assert events.ending_on_hand.iloc[-1] == 3


def test_irregular_windows_require_matching_refitted_targets():
    schedule = ExplicitSchedule([0, 2, 5])
    policy = target_policy(schedule=schedule, horizon=2)
    with pytest.raises(ValueError, match="horizon 3"):
        run(policy, [1.0] * 8)
    snapshots = {
        2: target_policy(
            schedule=schedule, horizon=3, origin=ORIGIN + pd.Timedelta(days=2)
        ),
        5: target_policy(
            schedule=schedule, horizon=1, origin=ORIGIN + pd.Timedelta(days=5)
        ),
    }
    events = run(policy, [1.0] * 8, policy_schedule=snapshots).to_event_frame()
    assert events.loc[events.decision_flag, "demand_period"].tolist() == [0, 2, 5]
    bad = target_policy(
        schedule=schedule, horizon=3, origin=ORIGIN + pd.Timedelta(days=3)
    )
    with pytest.raises(ValueError, match="decision information"):
        run(policy, [1.0] * 8, policy_schedule={2: bad})


def test_zero_lead_shelf_life_and_backlog_clearance_accounting():
    policy = target_policy(0, target=10.0, backorders=True)
    engine = ShelfLifeEngine(shelf_life_days=1)
    lots = pd.DataFrame(columns=["unique_id", "quantity", "received_date"])
    result = run(
        policy,
        [2.0, 2.0, 2.0, 0.0, 0.0],
        engine=engine,
        stock=0.0,
        backlog=4.0,
        opening_lots=lots,
    )
    events = validate_event_frame(result.to_event_frame())
    assert events.backorders_fulfilled.iloc[0] == 4
    assert events.received_units.iloc[0] == 14
    assert events.expired_units.iloc[1] == 8
    assert engine.ledger.balances().iloc[0] == result.inventory.data.on_hand.iloc[0]


@pytest.mark.parametrize("lead", [0, 1])
def test_decision_observes_receipts_but_never_current_demand_and_cannot_mutate_state(
    lead,
):
    from stockcast import OrderDecision, SimulationCallback

    class InspectPolicy(BasePolicy):
        def __init__(self):
            super().__init__(lead, schedule=OneTimeSchedule(), allow_backorders=False)
            self.fitted_ = True

        def predict(self, inventory, *, current_period):
            assert inventory.data.latest_incoming_demand.eq(0).all()
            assert inventory.data.latest_received.iloc[0] == 5
            frame = inventory.inventory_position()
            assert frame.inventory_position.iloc[0] == 8
            frame["order_quantity"] = 2.0
            frame["order_period"] = current_period
            frame["expected_delivery_period"] = current_period + self.lead_time
            # Even a misbehaving policy must not overwrite physical accounting.
            inventory.data["on_hand"] = 999.0
            return OrderDecision(frame, lead_time=self.lead_time)

    class PhaseRecorder(SimulationCallback):
        def reset(self, context):
            self.phases = []

        def on_after_prediction(self, decision, context):
            self.phases.append(context.phase)
            assert context.inventory.latest_incoming_demand.eq(0).all()

        def on_after_demand(self, context):
            self.phases.append(context.phase)

    recorder = PhaseRecorder()
    result = run(
        InspectPolicy(), [20.0, 0.0, 0.0], pipeline=[5.0, 0.0], callbacks=[recorder]
    )
    event = result.to_event_frame().iloc[0]
    assert event.decision_inventory_position == 8
    assert event.fulfilled_units == (10 if lead == 0 else 8)
    assert recorder.phases[:2] == ["on_after_prediction", "on_after_demand"]


def test_zero_lead_acceptance_uses_constraints_before_receipt_and_repeats_cleanly():
    from stockcast import MaximumOrderQuantity, OrderingConstraints

    policy = target_policy(0, target=23.0)
    engine = SimulationEngine()
    constraints = OrderingConstraints([MaximumOrderQuantity(5.0, mode="adjust")])
    first = run(
        policy,
        [9.0, 9.0, 0.0, 0.0],
        engine=engine,
        stock=0.0,
        order_constraints=constraints,
    )
    second = run(
        policy,
        [9.0, 9.0, 0.0, 0.0],
        engine=engine,
        stock=0.0,
        order_constraints=constraints,
    )
    pd.testing.assert_frame_equal(first.to_event_frame(), second.to_event_frame())
    event = first.to_event_frame().iloc[0]
    assert event.requested_order_quantity == 23
    assert event.order_quantity == event.received_units == 5
    assert event.shortage_units == 4
    assert event.on_order_end == 0


def test_delayed_one_time_schedule_uses_its_own_information_cutoff():
    policy = target_policy(
        schedule=OneTimeSchedule(2), horizon=1, origin=ORIGIN + pd.Timedelta(days=2)
    )
    events = run(policy, [0.0, 0.0, 1.0, 0.0, 0.0], stock=0.0).to_event_frame()
    assert events.decision_flag.tolist() == [False, False, True, False, False]
    assert events.order_quantity.sum() == 23


def test_zero_lead_reorder_point_protects_the_current_demand_epoch():
    from stockcast import ReorderPointPolicy

    policy = ReorderPointPolicy(
        0,
        review_period=1,
        policy_type="sQ",
        service_level=0.95,
        allow_backorders=False,
        order_quantity=10,
        order_quantity_source="explicit_case",
    )
    table = pd.DataFrame({"unique_id": ["A"], "s": [4.0], "end": [ORIGIN + pd.Timedelta(days=1)]})
    args = dict(
        forecast_origin=ORIGIN,
        forecast_frequency="D",
        reorder_point_column="s",
        reorder_end_date_column="end",
        target_probability=0.95,
        reorder_horizon=1,
        target_source="external_direct",
    )
    policy.fit(table, **args)
    # Stock 3 <= s=4 before demand, so the zero-lead order serves demand 5.
    events = run(policy, [5.0, 3.0, 9.0, 0.0], stock=3.0).to_event_frame()
    assert events.received_units.tolist()[:2] == [10.0, 0.0]
    assert events.lost_sales_units.tolist()[:2] == [0.0, 0.0]
    # The former lead-time-only window (0 periods) is no longer accepted.
    with pytest.raises(ValueError, match="reorder_horizon"):
        policy.fit(table.assign(end=ORIGIN), **{**args, "reorder_horizon": 0})


@pytest.mark.parametrize("periods,lead", [((0, 3, 10), 2), ((1, 2, 6), 0)])
def test_reorder_point_window_follows_the_next_opportunity(periods, lead):
    from stockcast import ReorderPointPolicy

    schedule = ExplicitSchedule(periods)
    # Decision at t with next opportunity u needs H = (u - t) + L.
    first, second = periods[0], periods[1]
    horizon = second - first + lead
    origin = ORIGIN + pd.Timedelta(days=first)
    policy = ReorderPointPolicy(
        lead, schedule=schedule, policy_type="sS", allow_backorders=True,
    )
    table = pd.DataFrame({
        "unique_id": ["A"], "s": [5.0], "S": [12.0],
        "end": [origin + pd.Timedelta(days=horizon)],
    })
    args = dict(
        forecast_origin=origin, forecast_frequency="D", reorder_point_column="s",
        order_up_to_column="S", reorder_end_date_column="end",
        target_source="external_direct",
    )
    policy.fit(table, reorder_horizon=horizon, **args)
    policy.validate_decision_window(first, origin, pd.offsets.Day())
    with pytest.raises(ValueError, match="reorder_horizon"):
        wrong = ReorderPointPolicy(lead, schedule=schedule, policy_type="sS", allow_backorders=True)
        wrong.fit(
            table.assign(end=origin + pd.Timedelta(days=horizon + 1)),
            reorder_horizon=horizon + 1, **args,
        ).validate_decision_window(first, origin, pd.offsets.Day())


def test_custom_schedule_interface_and_manifest():
    class OddOpportunities(DecisionSchedule):
        def should_decide(self, period):
            return period % 2 == 1

        def next_decision_period(self, period):
            return period + (2 if period % 2 else 1)

        def to_manifest(self):
            return {"type": "odd_epochs", "source": "declared_calendar"}

    policy = target_policy(
        schedule=OddOpportunities(), horizon=2, origin=ORIGIN + pd.Timedelta(days=1)
    )
    result = run(policy, [0.0, 4.0, 0.0], stock=0.0, order_during_settlement=True)
    assert result.run_settings["decision_schedule"]["type"] == "odd_epochs"
    assert result.to_event_frame().decision_flag.tolist() == [False, True, False]


def test_provider_policy_accepts_separate_nonperiodic_schedule_without_probability():
    from stockcast import FixedPeriodicReviewTargets, PeriodicReviewPolicy

    policy = PeriodicReviewPolicy(
        0, schedule=OneTimeSchedule(), allow_backorders=False
    ).fit(
        pd.DataFrame({"unique_id": ["A"]}),
        target_provider=FixedPeriodicReviewTargets(
            reorder_point=5.0, order_up_to_level=10.0
        ),
        information_origin=ORIGIN,
        information_frequency="D",
    )
    events = run(policy, [2.0, 3.0, 0.0, 0.0], stock=0.0).to_event_frame()
    assert events.order_quantity.tolist() == [10.0, 0.0, 0.0, 0.0]
    assert policy.review_period is None and policy.service_level is None


def test_demand_window_validation_cannot_change_scenario_or_leak_future_into_prediction():
    class MutatingValidator(OrderUpToPolicy):
        def validate_demand_window(self, demand, n_periods):
            self.future_demand = demand["y"].tolist()
            demand["y"] = 999.0

        def predict(self, inventory, **kwargs):
            assert not hasattr(self, "future_demand")
            return super().predict(inventory, **kwargs)

    policy = MutatingValidator(0, review_period=1, allow_backorders=False).fit(
        pd.DataFrame(
            {"unique_id": ["A"], "S": [10.0], "end": [ORIGIN + pd.Timedelta(days=1)]}
        ),
        forecast_origin=ORIGIN,
        forecast_frequency="D",
        target_column="S",
        target_end_date_column="end",
        protection_horizon=1,
        target_source="external_direct",
    )
    events = run(policy, [2.0, 3.0, 0.0, 0.0]).to_event_frame()
    assert events.demand.tolist() == [2.0, 3.0, 0.0, 0.0]
    assert not hasattr(policy, "future_demand")
