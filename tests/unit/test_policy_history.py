"""A policy's view of the run so far: the history on the state given to predict.

These tests pin the behaviour the "refit inside the run" recipe relies on
(docs/how-to/rolling-targets.md, notebook 04a): at each decision the state
carries every completed period and nothing later, and under lost sales it
records demand, sales and lost sales separately.
"""

import math

import numpy as np
import pandas as pd
import pytest

from stockcast.core import (
    BasePolicy,
    InventoryStateDataFrame,
    MaximumOrderQuantity,
    OrderDecision,
    SimulationEngine,
)
from stockcast.policies import OrderUpToPolicy
from stockcast.utils import update_inventory_with_orders

SKU = "tea"
OPENING_DATE = pd.Timestamp("2026-01-01")
DEMAND = [5.0, 9.0, 4.0, 12.0, 6.0, 11.0, 7.0, 10.0]
DATES = pd.date_range(OPENING_DATE + pd.Timedelta(days=1), periods=len(DEMAND), freq="D")
PRE_RUN = [6.0, 7.0, 5.0]


def _demand():
    return pd.DataFrame({"unique_id": SKU, "period": range(len(DEMAND)), "date": DATES, "y": DEMAND})


def _opening(on_hand=8.0, max_lead_time=1):
    empty = InventoryStateDataFrame([SKU], max_lead_time=max_lead_time, allow_backorders=False)
    return empty.initialize_from_observed(
        pd.DataFrame({"unique_id": [SKU], "on_hand": [on_hand]}),
        on_hand_column="on_hand",
        opening_date=OPENING_DATE,
    )


def _order_up_to(target, *, origin):
    policy = OrderUpToPolicy(lead_time=0, review_period=1, freq="D", allow_backorders=False)
    policy.fit(
        pd.DataFrame({"unique_id": [SKU], "target": [target]}),
        target_column="target",
        forecast_origin=origin,
    )
    return policy


def _capturing_policy(captured, **kwargs):
    """A policy that orders up to 9 units and appends (decision date, history)
    to ``captured``.

    The engine predicts with a deep copy of the policy, so the list is reached
    through a closure, which the copy shares, not an instance attribute.
    """

    class CapturingPolicy(BasePolicy):
        def fit(self, *args, **kwargs):
            self.fitted_ = True
            return self

        def predict(self, inventory_state_df, *, current_period, **kwargs):
            date = pd.Timestamp(inventory_state_df.get_dataframe()["date"].iloc[0])
            captured.append((date, inventory_state_df.get_history()))
            position = inventory_state_df.inventory_position()
            orders = pd.DataFrame({
                "unique_id": position["unique_id"],
                "order_quantity": (9.0 - position["inventory_position"]).clip(lower=0.0),
                "order_period": current_period,
                "expected_delivery_period": current_period + self.lead_time,
            })
            return OrderDecision(orders, lead_time=self.lead_time)

    policy = CapturingPolicy(allow_backorders=False, **kwargs)
    policy.fit()
    return policy


class RefitFromHistory(BasePolicy):
    """Refits a one-period target at each decision from the run's own history."""

    COLUMNS = {"demand": "latest_incoming_demand", "sales": "latest_fulfilled"}

    def __init__(self, learn_from):
        super().__init__(lead_time=0, review_period=1, allow_backorders=False)
        self.learn_from = learn_from

    def fit(self, pre_run):
        self.pre_run = list(pre_run)
        self.fitted_ = True
        return self

    def target_policy(self, observations, origin):
        """A deterministic stand-in forecaster: 1.2 x the mean of the last three."""
        target = math.ceil(1.2 * np.mean(observations[-3:]))
        return _order_up_to(target, origin=origin)

    def predict(self, inventory_state_df, *, current_period, **kwargs):
        past = inventory_state_df.get_history()
        observed = list(past[self.COLUMNS[self.learn_from]]) if len(past) else []
        origin = pd.Timestamp(inventory_state_df.get_dataframe()["date"].iloc[0]) - pd.Timedelta(days=1)
        policy = self.target_policy(self.pre_run + observed, origin)
        return policy.predict(inventory_state_df, current_period=current_period)


def _run(policy, **kwargs):
    result = SimulationEngine().run(
        policy=policy, demand_source=_demand(), inventory=_opening(), freq="D", **kwargs,
    )
    events = result.to_event_frame()
    return events.loc[events["event_type"] == "period"].reset_index(drop=True)


@pytest.mark.parametrize("review_period", [1, 3])
@pytest.mark.parametrize("constrained", [False, True])
def test_predict_sees_every_completed_period_and_nothing_later(review_period, constrained):
    captured = []
    policy = _capturing_policy(captured, lead_time=1, review_period=review_period)
    events = _run(
        policy,
        order_constraints=[MaximumOrderQuantity(20.0)] if constrained else None,
    )

    decisions = list(range(0, len(DEMAND), review_period))
    assert [date for date, _ in captured] == list(DATES[decisions])
    for period, (date, history) in zip(decisions, captured):
        if period == 0:
            assert history.empty
            continue
        # One row per completed period, including periods without a decision,
        # and none for the period being decided.
        assert list(pd.to_datetime(history["date"])) == list(DATES[:period])
        assert (pd.to_datetime(history["date"]) < date).all()
        assert list(history["latest_incoming_demand"]) == DEMAND[:period]
        # The history rows are the completed periods as the event table records them.
        np.testing.assert_allclose(history["latest_fulfilled"], events["fulfilled_units"][:period])
        np.testing.assert_allclose(history["latest_shortage"], events["lost_sales_units"][:period])
        np.testing.assert_allclose(history["on_hand"], events["ending_on_hand"][:period])


def test_history_separates_demand_sales_and_lost_sales():
    captured = []
    policy = _capturing_policy(captured, lead_time=1, review_period=1)
    _run(policy)

    history = captured[-1][1]
    assert (history["latest_shortage"] > 0).any()
    np.testing.assert_allclose(
        history["latest_incoming_demand"],
        history["latest_fulfilled"] + history["latest_shortage"],
    )


def test_the_engine_predicts_with_its_own_copy_of_the_policy(monkeypatch):
    original_predict = RefitFromHistory.predict

    def counting_predict(self, inventory_state_df, **kwargs):
        self.calls.append(kwargs["current_period"])
        return original_predict(self, inventory_state_df, **kwargs)

    monkeypatch.setattr(RefitFromHistory, "predict", counting_predict)
    policy = RefitFromHistory("sales")
    policy.fit(PRE_RUN)
    policy.calls = []
    _run(policy)
    # What the policy changes about itself during the run stays on the engine's copy.
    assert policy.calls == []


def _manual_loop(learn_from):
    """The same refit rule, run with the state primitives."""
    state = _opening()
    observed = list(PRE_RUN)
    orders = []
    for period, (date, demand) in enumerate(zip(DATES, DEMAND)):
        state = state.advance_period(freq="D", is_review_period=True)
        current_period = int(state.get_dataframe()["period"].iloc[0])
        policy = RefitFromHistory(learn_from).target_policy(observed, date - pd.Timedelta(days=1))
        decision = policy.predict(state, current_period=current_period)
        state = update_inventory_with_orders(state, decision, policy=policy)
        state = state.fulfill_demand(pd.DataFrame({"unique_id": [SKU], "date": [date], "y": [demand]}))
        after = state.get_dataframe().iloc[0]
        orders.append(float(after["latest_order"]))
        observed.append(demand if learn_from == "demand" else float(after["latest_fulfilled"]))
    return orders


@pytest.mark.parametrize("learn_from", ["demand", "sales"])
def test_refit_inside_the_run_matches_a_manual_loop(learn_from):
    policy = RefitFromHistory(learn_from)
    policy.fit(PRE_RUN)
    events = _run(policy)
    assert list(events["order_quantity"]) == _manual_loop(learn_from)


def test_learning_from_sales_differs_after_a_stockout():
    learners = {}
    for learn_from in ("demand", "sales"):
        policy = RefitFromHistory(learn_from)
        policy.fit(PRE_RUN)
        learners[learn_from] = policy
    comparison = SimulationEngine().run_comparison(
        policies=learners, demand_source=_demand(), inventory=_opening(), freq="D",
    )
    orders = {}
    for label in learners:
        events = comparison[label].to_event_frame()
        events = events.loc[events["event_type"] == "period"].reset_index(drop=True)
        orders[label] = list(events["order_quantity"])
        # Each comparison branch gives the same orders as a single run.
        policy = RefitFromHistory(label)
        policy.fit(PRE_RUN)
        assert orders[label] == list(_run(policy)["order_quantity"])

    events = comparison["sales"].to_event_frame()
    events = events.loc[events["event_type"] == "period"].reset_index(drop=True)
    first_stockout = int(np.flatnonzero(events["lost_sales_units"] > 0)[0])
    # Identical until the first stockout is observed, different afterwards.
    assert orders["demand"][: first_stockout + 1] == orders["sales"][: first_stockout + 1]
    assert orders["demand"] != orders["sales"]
