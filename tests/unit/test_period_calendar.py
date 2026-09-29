"""Dates must sit on the period calendar, and a target must cover demand after the decision.

Stockcast dates period ``k`` as ``date + k`` periods. For an anchored
frequency pandas rolls a date that is off the anchor forward, even for
``k = 0``: a Sunday count with ``"W-MON"`` periods would put the first
decision's information date on period 0's own date. Such dates are rejected.
"""

import pandas as pd
import pytest

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.policies import OrderUpToPolicy, ReorderPointPolicy, SingleOrderPolicy
from stockcast.utils import DemandGenerator

DAY = pd.Timedelta(days=1)


def _stock(opening, skus=("a",), on_hand=5.0):
    return InventoryStateDataFrame.from_observed(pd.DataFrame({
        "unique_id": list(skus), "date": pd.Timestamp(opening), "on_hand": on_hand,
    }))


def _demand(first, freq, periods=4):
    return pd.DataFrame({
        "unique_id": "a", "date": pd.date_range(first, periods=periods, freq=freq), "y": 10.0,
    })


def _out(freq, *, origin=None, end=None, lead_time=0, review_period=1):
    table = pd.DataFrame({"unique_id": ["a"], "S": [12.0]})
    if end is not None:
        table["date"] = pd.Timestamp(end)
    return OrderUpToPolicy(
        lead_time=lead_time, review_period=review_period, freq=freq, allow_backorders=False,
    ).fit(table, target_column="S", forecast_origin=origin)


@pytest.mark.parametrize("count, freq, first, message", [
    ("2026-01-31", "MS", "2026-02-01", "2026-01-31 is not on the 'MS' calendar"),
    ("2026-03-22", "W-MON", "2026-03-23", "2026-03-22 is not on the 'W-MON' calendar"),
])
def test_an_opening_date_off_the_calendar_is_rejected(count, freq, first, message):
    # Before the fix the target below, which already knows period 0, was accepted.
    policy = _out(freq, origin=first)
    with pytest.raises(ValueError, match=f"inventory's opening date {message}"):
        SimulationEngine().run(policy, _demand(first, freq), _stock(count))
    with pytest.raises(ValueError, match="opening date .* calendar"):
        SimulationEngine().run(
            policy, _demand(first, freq).drop(columns="date").assign(period=range(4)),
            _stock(count),
        )


def test_the_look_ahead_check_holds_on_every_calendar():
    for count, freq, first in (
        ("2026-01-05", "D", "2026-01-06"),
        ("2026-01-01", "MS", "2026-02-01"),
        ("2026-03-16", "W-MON", "2026-03-23"),
    ):
        with pytest.raises(ValueError, match="must not be after decision information date"):
            SimulationEngine().run(_out(freq, origin=first), _demand(first, freq), _stock(count))
        ledger = SimulationEngine().run(
            _out(freq, origin=count), _demand(first, freq), _stock(count),
        ).to_event_frame()
        assert ledger["date"].iloc[0] == pd.Timestamp(first)


def test_a_forecast_origin_off_the_calendar_is_rejected_at_fit():
    with pytest.raises(ValueError, match="forecast_origin 2026-03-22 is not on the 'W-MON' calendar"):
        _out("W-MON", origin="2026-03-22")
    with pytest.raises(ValueError, match="forecast_origin 2026-01-31 is not on the 'MS' calendar"):
        ReorderPointPolicy(0, 1, freq="MS", allow_backorders=False).fit(
            reorder_point=2, order_up_to_level=8, forecast_origin="2026-01-31",
        )
    with pytest.raises(ValueError, match="forecast_origin 2026-03-22 is not on the 'W-MON' calendar"):
        SingleOrderPolicy(0, freq="W-MON", selling_horizon=1, allow_backorders=False).fit(
            pd.DataFrame({"unique_id": ["a"], "S": [3.0]}), target_column="S",
            forecast_origin="2026-03-22",
        )
    with pytest.raises(ValueError, match="forecast_origin 2026-03-22 is not on the 'W-MON' calendar"):
        OrderUpToPolicy(0, 1, freq="W-MON", service_level=0.9, allow_backorders=False).fit(
            pd.DataFrame({"unique_id": "a", "fh": [1], "m": [1.0], "s": [1.0]}),
            mean_column="m", std_column="s", forecast_origin="2026-03-22",
        )


def test_demand_generator_first_date_must_be_on_the_calendar():
    with pytest.raises(ValueError, match="first_date 2026-01-06 is not on the 'W-MON' calendar"):
        DemandGenerator(["a"], first_date="2026-01-06", freq="W-MON", random_seed=0)
    with pytest.raises(ValueError, match="first_date 2026-01-31 is not on the 'MS' calendar"):
        DemandGenerator(["a"], first_date="2026-01-31", freq="MS", random_seed=0)
    dates = DemandGenerator(
        ["a"], first_date="2026-01-12", freq="W-MON", random_seed=0,
    ).normal(3, mean=5, std=1)["date"]
    assert dates.tolist() == list(pd.date_range("2026-01-12", periods=3, freq="W-MON"))


def test_manual_stepping_rejects_a_state_date_off_the_calendar():
    state = _stock("2026-03-22", on_hand=5.0)
    state.allow_backorders = False
    with pytest.raises(ValueError, match="state's date 2026-03-22 is not on the 'W-MON' calendar"):
        state.advance_period(freq="W-MON", is_review_period=False)
    assert state.advance_period(freq="D", is_review_period=False).get_dataframe()["date"].iloc[0] == (
        pd.Timestamp("2026-03-23")
    )


def test_a_target_dated_with_the_day_it_was_made_is_rejected():
    opening = pd.Timestamp("2026-01-05")
    demand = _demand(opening + DAY, "D", periods=10)
    made_today = _out("D", end=opening, lead_time=2, review_period=4)
    # The date is read as the window's last day, so the origin is six days earlier.
    assert made_today.get_target_metadata()["forecast_origin"] == "2025-12-30T00:00:00"
    with pytest.raises(ValueError, match=(
        r"target window ends 2026-01-05 00:00:00 \(the 'date' column\), on or before "
        r"the inventory's opening date 2026-01-05 00:00:00, so it covers none of the run's demand"
    )):
        SimulationEngine().run(made_today, demand, _stock(opening))
    # A stale target whose window still reaches past the decision stays valid.
    stale = _out("D", end=opening + DAY, lead_time=2, review_period=4)
    assert SimulationEngine().run(stale, demand, _stock(opening)).n_periods == 10


def test_a_fitted_level_may_be_reused_by_a_schedule_that_starts_later():
    from stockcast.core import PeriodicSchedule

    # The window of a level fitted at the opening ends before the first review
    # at period 3; a periodic schedule reuses it as a standing level.
    opening = pd.Timestamp("2026-01-05")
    policy = ReorderPointPolicy(
        0, schedule=PeriodicSchedule(1, start=3), freq="D", allow_backorders=False,
    ).fit(
        pd.DataFrame({"unique_id": ["a"], "s": [2.0], "S": [8.0], "date": [opening + DAY]}),
        reorder_point_column="s", order_up_to_column="S",
    )
    ledger = SimulationEngine().run(
        policy, _demand(opening + DAY, "D", periods=6), _stock(opening),
    ).to_event_frame()
    assert ledger["decision_flag"].tolist() == [False, False, False, True, True, True]


def test_a_stock_count_with_a_time_of_day_says_so():
    opening = pd.Timestamp("2026-01-05")
    with pytest.raises(ValueError, match=r"opening date 2026-01-05 18:30:00 has a time of day"):
        SimulationEngine().run(
            _out("D", origin=opening), _demand(opening + DAY, "D"),
            _stock("2026-01-05 18:30"),
        )
