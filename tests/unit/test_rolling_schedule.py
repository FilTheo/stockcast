"""Rolling targets (0.2.0): ``rolling_schedule``.

The builder must give exactly the ``policy_schedule`` a caller would assemble
by hand, and fail closed on every alignment error the hand-written loop could
hide: an origin off the calendar or between decisions, a missing row, a
missing target, a decision without an origin.
"""

from __future__ import annotations

import copy
import pickle

import numpy as np
import pandas as pd
import pytest

from stockcast import (
    ConstraintResult,
    InventoryStateDataFrame,
    OrderDecision,
    OrderingConstraint,
    OrderMultiple,
    OrderUpToPolicy,
    ReorderPointPolicy,
    SimulationEngine,
    SingleOrderPolicy,
    rolling_schedule,
)
from stockcast.policies import rolling_schedule as policies_rolling_schedule

ORIGIN = pd.Timestamp("2024-01-01")  # a Monday, so W-MON dates stay on the calendar
WEEK = pd.tseries.frequencies.to_offset("W-MON")
DAY = pd.Timedelta(days=1)
SKUS = ["a", "b"]


def state(skus=SKUS, on_hand=0.0, opening=ORIGIN):
    return InventoryStateDataFrame(skus, max_lead_time=2, allow_backorders=False) \
        .initialize_from_observed(
            pd.DataFrame({"unique_id": skus, "on_hand": on_hand}), opening_date=opening,
        )


def order_up_to(service_level=0.9, **kwargs):
    settings = dict(lead_time=2, review_period=1, freq="W-MON",
                    service_level=service_level, allow_backorders=False)
    settings.update(kwargs)
    return OrderUpToPolicy(**settings)


def weekly_demand(skus, n, value=10.0):
    return pd.DataFrame([
        (sku, p, ORIGIN + (p + 1) * WEEK, value + i + p % 3)
        for p in range(n) for i, sku in enumerate(skus)
    ], columns=["unique_id", "period", "date", "y"])


def weekly_targets(skus=SKUS, n_origins=5, horizon=3):
    """One row per SKU and weekly origin, with two levels and extra labels."""
    rows = []
    for t in range(n_origins):
        origin = ORIGIN + t * WEEK
        for i, sku in enumerate(skus):
            rows.append({
                "unique_id": sku, "cutoff": origin, "date": origin + horizon * WEEK,
                "q_80": 25.0 + 2 * t + i, "q_90": 30.0 + 3 * t + i, "model": "m",
            })
    return pd.DataFrame(rows)


def hand_schedule(targets, column, level=0.9):
    """The loop a caller writes without the builder."""
    schedule = {}
    for t, rows in targets.groupby("cutoff"):
        period = (t - ORIGIN) // pd.Timedelta(weeks=1)
        schedule[period] = order_up_to(level).fit(
            rows[["unique_id", "date", column]].reset_index(drop=True),
            target_column=column, forecast_origin=t,
        )
    return schedule


def run(schedule, demand, inventory=None, **kwargs):
    first = min(schedule)
    return SimulationEngine().run(
        policy=schedule[first], policy_schedule=schedule,
        demand_source=demand, inventory=inventory or state(), **kwargs,
    )


def events(result):
    return result.to_event_frame()


# ---------------------------------------------------------------------------
# The builder equals the hand-assembled schedule
# ---------------------------------------------------------------------------

def test_builder_is_exported_from_both_namespaces():
    assert rolling_schedule is policies_rolling_schedule


def test_two_skus_five_weekly_origins_equal_hand_assembled_run():
    targets = weekly_targets()
    built = rolling_schedule(order_up_to(), targets, state(), target_column="q_90")
    assert list(built) == [0, 1, 2, 3, 4]
    hand = hand_schedule(targets, "q_90")
    demand = weekly_demand(SKUS, 5)
    built_events, hand_events = events(run(built, demand)), events(run(hand, demand))
    pd.testing.assert_frame_equal(built_events, hand_events, check_exact=True)
    # The levels really change per decision (the test would not see a reused one).
    assert built_events.loc[built_events["unique_id"] == "a", "target_level"].tolist() == [
        30.0, 33.0, 36.0, 39.0, 42.0,
    ]
    # Each snapshot keeps only the columns fit reads: no other level or label.
    assert list(built[2].forecast_df_.columns) == ["unique_id", "date", "q_90"]
    assert built[2].get_target_metadata()["forecast_origin"] == (ORIGIN + 2 * WEEK).isoformat()


def test_policy_given_is_not_changed_and_extra_skus_are_ignored():
    policy = order_up_to()
    targets = weekly_targets(skus=["a", "b", "zz"])
    built = rolling_schedule(policy, targets, state(), target_column="q_90")
    assert not policy.fitted_
    assert all(set(p.target_levels_["unique_id"]) == {"a", "b"} for p in built.values())


def test_unsorted_table_gives_the_same_schedule():
    targets = weekly_targets()
    shuffled = targets.sample(frac=1.0, random_state=3)
    demand = weekly_demand(SKUS, 5)
    left = events(run(rolling_schedule(order_up_to(), targets, state(), target_column="q_90"), demand))
    right = events(run(rolling_schedule(order_up_to(), shuffled, state(), target_column="q_90"), demand))
    pd.testing.assert_frame_equal(left, right, check_exact=True)


@pytest.mark.parametrize("unit", ["s", "ms", "us"])
def test_origins_at_another_timestamp_resolution_give_the_same_run(unit):
    # Parquet tables often hold datetime64[us] (pandas 2 and 3 keep the unit).
    targets = weekly_targets()
    coarse = targets.astype({"cutoff": f"datetime64[{unit}]", "date": f"datetime64[{unit}]"})
    demand = weekly_demand(SKUS, 5)
    expected = events(run(rolling_schedule(order_up_to(), targets, state(), target_column="q_90"), demand))
    actual = events(run(rolling_schedule(order_up_to(), coarse, state(), target_column="q_90"), demand))
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


def test_levels_in_run_comparison_equal_separate_runs():
    targets = weekly_targets()
    demand = weekly_demand(SKUS, 5)
    schedules = {
        f"{level:.0%}": rolling_schedule(
            order_up_to(level), targets, state(), target_column=column,
        )
        for level, column in [(0.8, "q_80"), (0.9, "q_90")]
    }
    comparison = SimulationEngine().run_comparison(
        policies={label: schedule[min(schedule)] for label, schedule in schedules.items()},
        policy_schedules=schedules,
        demand_source=demand, inventory=state(),
    )
    for label, schedule in schedules.items():
        alone = events(run(schedule, demand))
        pd.testing.assert_frame_equal(events(comparison[label]), alone, check_exact=True)
    assert not events(comparison["80%"])["target_level"].equals(
        events(comparison["90%"])["target_level"]
    )


def test_probability_label_is_checked_for_every_origin():
    with pytest.raises(ValueError, match=r"origin 2024-01-01 \(decision period 0\): .*q_90"):
        rolling_schedule(order_up_to(0.8), weekly_targets(), state(), target_column="q_90")


# ---------------------------------------------------------------------------
# Alignment errors
# ---------------------------------------------------------------------------

def test_removing_one_row_names_the_sku_and_decision_period():
    targets = weekly_targets()
    targets = targets[~((targets["unique_id"] == "b") & (targets["cutoff"] == ORIGIN + 2 * WEEK))]
    for mode in ("raise", "no_order"):
        with pytest.raises(ValueError, match=r"no row for 1 inventory SKU\(s\) at origin "
                                             r"2024-01-15 \(decision period 2\): \['b'\]"):
            rolling_schedule(order_up_to(), targets, state(), target_column="q_90",
                             missing_targets=mode)


def test_origin_off_the_calendar_raises():
    targets = weekly_targets()
    targets.loc[targets["cutoff"] == ORIGIN + 3 * WEEK, "cutoff"] += DAY  # a Tuesday
    with pytest.raises(ValueError, match=r"the 'cutoff' origin 2024-01-23 is not on the 'W-MON' calendar"):
        rolling_schedule(order_up_to(), targets, state(), target_column="q_90")


def test_origin_before_the_opening_raises():
    targets = weekly_targets()
    with pytest.raises(ValueError, match="origin 2024-01-01 .* before the inventory's opening date 2024-01-08"):
        rolling_schedule(order_up_to(), targets, state(opening=ORIGIN + WEEK),
                         target_column="q_90")


def test_daily_review_every_three_periods_accepts_only_decision_origins():
    opening = pd.Timestamp("2026-03-02")
    policy = OrderUpToPolicy(lead_time=1, review_period=3, freq="D",
                             service_level=None, allow_backorders=True)

    def table(periods):
        return pd.DataFrame([
            {"unique_id": sku, "cutoff": opening + t * DAY, "date": opening + (t + 4) * DAY,
             "S": 20.0 + t}
            for t in periods for sku in SKUS
        ])

    inventory = state(opening=opening)
    built = rolling_schedule(policy, table([0, 3, 6, 9]), inventory, target_column="S")
    assert list(built) == [0, 3, 6, 9]
    with pytest.raises(ValueError, match=r"origin 2026-03-03 \('cutoff'\) is decision period 1, "
                                         r"but the policy's schedule .* does not decide at period 1"):
        rolling_schedule(policy, table([0, 1, 3, 6]), inventory, target_column="S")
    with pytest.raises(ValueError, match=r"no origin for decision period 3 \(origin 2026-03-05\)"):
        rolling_schedule(policy, table([0, 6]), inventory, target_column="S")


def test_window_end_must_match_the_origin():
    targets = weekly_targets(horizon=2)  # dates one week short of H = 3
    with pytest.raises(ValueError, match=r"^origin 2024-01-01 \(decision period 0\): "):
        rolling_schedule(order_up_to(), targets, state(), target_column="q_90")


def test_duplicate_rows_raise_with_the_origin():
    targets = weekly_targets()
    targets = pd.concat([targets, targets.iloc[[4]]], ignore_index=True)
    with pytest.raises(ValueError, match=r"^origin 2024-01-15 \(decision period 2\): "):
        rolling_schedule(order_up_to(), targets, state(), target_column="q_90")


def test_invalid_origin_values_raise():
    targets = weekly_targets().astype({"cutoff": object})
    targets.loc[3, "cutoff"] = "not a date"
    with pytest.raises(ValueError, match=r"targets.cutoff must hold valid dates; the row for SKU 'b'"):
        rolling_schedule(order_up_to(), targets, state(), target_column="q_90")


# ---------------------------------------------------------------------------
# Missing targets
# ---------------------------------------------------------------------------

def test_nan_rows_raise_by_default_and_are_dropped_under_no_order():
    targets = weekly_targets()
    targets.loc[(targets["unique_id"] == "b") & (targets["cutoff"] == ORIGIN + WEEK), "q_90"] = np.nan
    with pytest.raises(ValueError, match=r"missing \(NaN\) for 1 SKU\(s\) at origin 2024-01-08 "
                                         r"\(decision period 1\): \['b'\]; pass missing_targets=\"no_order\""):
        rolling_schedule(order_up_to(), targets, state(), target_column="q_90")
    built = rolling_schedule(order_up_to(), targets, state(), target_column="q_90",
                             missing_targets="no_order")
    assert built[1].target_levels_["unique_id"].tolist() == ["a"]
    assert built[2].target_levels_["unique_id"].tolist() == ["a", "b"]


def test_an_origin_with_every_target_missing_raises():
    targets = weekly_targets()
    targets.loc[targets["cutoff"] == ORIGIN + WEEK, "q_90"] = np.nan
    with pytest.raises(ValueError, match=r"every SKU's target is missing at origin 2024-01-08"):
        rolling_schedule(order_up_to(), targets, state(), target_column="q_90",
                         missing_targets="no_order")


def test_mean_std_mode_and_a_nan_step_drops_the_sku_at_that_origin():
    policy = order_up_to(0.9)
    rows = []
    for t in range(3):
        origin = ORIGIN + t * WEEK
        for sku in SKUS:
            for fh in (1, 2, 3):
                rows.append({"unique_id": sku, "cutoff": origin, "fh": fh,
                             "date": origin + fh * WEEK, "mean": 10.0 + t, "std": 2.0})
    targets = pd.DataFrame(rows)
    built = rolling_schedule(policy, targets, state(), mean_column="mean", std_column="std")
    hand = {
        t: order_up_to(0.9).fit(
            rows.drop(columns="cutoff").reset_index(drop=True),
            mean_column="mean", std_column="std", forecast_origin=origin,
        )
        for t, (origin, rows) in enumerate(targets.groupby("cutoff"))
    }
    for t in built:
        pd.testing.assert_frame_equal(built[t].target_levels_, hand[t].target_levels_)
    assert list(built[1].forecast_df_.columns) == ["unique_id", "date", "fh", "mean", "std"]

    gap = (targets["unique_id"] == "a") & (targets["cutoff"] == ORIGIN + WEEK) & (targets["fh"] == 2)
    targets.loc[gap, "std"] = np.nan
    built = rolling_schedule(policy, targets, state(), mean_column="mean", std_column="std",
                             missing_targets="no_order")
    assert built[1].target_levels_["unique_id"].tolist() == ["b"]


def test_reorder_point_policy_in_column_mode():
    policy = ReorderPointPolicy(lead_time=2, review_period=1, freq="W-MON",
                                order_quantity=12.0, allow_backorders=False)
    targets = weekly_targets().rename(columns={"q_80": "s"})
    built = rolling_schedule(policy, targets, state(), reorder_point_column="s")
    hand = {
        t: ReorderPointPolicy(lead_time=2, review_period=1, freq="W-MON",
                              order_quantity=12.0, allow_backorders=False).fit(
            rows[["unique_id", "date", "s"]].reset_index(drop=True),
            reorder_point_column="s", forecast_origin=origin,
        )
        for t, (origin, rows) in enumerate(targets.groupby("cutoff"))
    }
    demand = weekly_demand(SKUS, 5)
    pd.testing.assert_frame_equal(events(run(built, demand)), events(run(hand, demand)),
                                  check_exact=True)


# ---------------------------------------------------------------------------
# Unsupported inputs
# ---------------------------------------------------------------------------

def test_unsupported_inputs_raise():
    targets = weekly_targets()
    with pytest.raises(ValueError, match="sets forecast_origin for every origin"):
        rolling_schedule(order_up_to(), targets, state(), target_column="q_90",
                         forecast_origin=ORIGIN)
    planner = ReorderPointPolicy(lead_time=2, review_period=1, freq="W-MON",
                                 order_quantity=12.0, allow_backorders=False)
    with pytest.raises(ValueError, match=r"reorder_point= gives levels without an origin"):
        rolling_schedule(planner, targets, state(), reorder_point=5.0)
    with pytest.raises(TypeError, match="OrderUpToPolicy or ReorderPointPolicy; got SingleOrderPolicy"):
        rolling_schedule(
            SingleOrderPolicy(lead_time=1, selling_horizon=3, freq="W-MON",
                              allow_backorders=False),
            targets, state(), target_column="q_90",
        )
    with pytest.raises(ValueError, match="freq is required"):
        rolling_schedule(order_up_to(freq=None), targets, state(), target_column="q_90")
    with pytest.raises(ValueError, match="missing_targets must be 'raise' or 'no_order'"):
        rolling_schedule(order_up_to(), targets, state(), target_column="q_90",
                         missing_targets="skip")
    with pytest.raises(ValueError, match="no origin column \\(origin_column\\) 'origin'"):
        rolling_schedule(order_up_to(), targets, state(), origin_column="origin",
                         target_column="q_90")
    with pytest.raises(ValueError, match="no target column 'q_99'"):
        rolling_schedule(order_up_to(), targets, state(), target_column="q_99")
    with pytest.raises(ValueError, match="name the target columns to fit"):
        rolling_schedule(order_up_to(), targets, state())
    assert copy.deepcopy(targets).equals(weekly_targets())  # the table is not changed


# ---------------------------------------------------------------------------
# allow_target_reuse=False (S1)
# ---------------------------------------------------------------------------

def s1_policy(skus, target, origin=ORIGIN, **kwargs):
    """The S1 request's policy: a planner target over H = 3 weeks."""
    return order_up_to(None, **kwargs).fit(
        pd.DataFrame({"unique_id": skus, "S": target}),
        target_column="S", forecast_origin=origin,
    )


def s1_demand(skus, values):
    return pd.DataFrame([(s, p, ORIGIN + (p + 1) * WEEK, v)
                         for p, v in enumerate(values) for s in skus],
                        columns=["unique_id", "period", "date", "y"])


def _manifest(result):
    manifest = copy.deepcopy(result.run_manifest)
    manifest.pop("run_id")
    manifest.pop("created_at_utc")
    return manifest


def _assert_same_run(left, right, *, ignore_settings=()):
    pd.testing.assert_frame_equal(left.to_event_frame(), right.to_event_frame(), check_exact=True)
    pd.testing.assert_frame_equal(left.to_order_frame(), right.to_order_frame(), check_exact=True)
    pd.testing.assert_frame_equal(left.history, right.history, check_exact=True)
    pd.testing.assert_frame_equal(left.to_callback_audit_frame(), right.to_callback_audit_frame())
    pd.testing.assert_frame_equal(left.to_process_flow_frame(), right.to_process_flow_frame())
    left_manifest, right_manifest = _manifest(left), _manifest(right)
    for key in ignore_settings:
        left_manifest["run_settings"].pop(key, None)
        right_manifest["run_settings"].pop(key, None)
    assert left_manifest == right_manifest


def test_s1_static_target_raises_at_the_first_reused_decision():
    arguments = dict(policy=s1_policy(["a"], [30.0]),
                     demand_source=s1_demand(["a"], [10.0] * 15), inventory=state(["a"]))
    default = SimulationEngine().run(**arguments)
    assert default.to_event_frame()["decision_flag"].sum() == 15
    with pytest.raises(ValueError, match=r"allow_target_reuse=False: decision period 1 "
                                         r"\(information date 2024-01-08\) has no policy of its own "
                                         r".*rolling_schedule.*allow_target_reuse=True"):
        SimulationEngine().run(**arguments, allow_target_reuse=False)


def test_s1_partial_schedule_raises_at_the_first_uncovered_decision():
    snapshots = {1: s1_policy(["a"], [31.0], ORIGIN + WEEK),
                 3: s1_policy(["a"], [33.0], ORIGIN + 3 * WEEK)}
    arguments = dict(policy=s1_policy(["a"], [30.0]), policy_schedule=snapshots,
                     demand_source=s1_demand(["a"], [10.0] * 5), inventory=state(["a"]))
    default = SimulationEngine().run(**arguments)
    assert default.to_event_frame()["target_level"].tolist() == [30, 31, 31, 33, 33]
    with pytest.raises(ValueError, match=r"decision period 2 \(information date 2024-01-15\)"):
        SimulationEngine().run(**arguments, allow_target_reuse=False)


def test_complete_rolling_schedule_is_identical_with_and_without_the_guard():
    schedule = rolling_schedule(order_up_to(), weekly_targets(), state(), target_column="q_90")
    demand = weekly_demand(SKUS, 5)
    guarded = run(schedule, demand, allow_target_reuse=False)
    _assert_same_run(guarded, run(schedule, demand), ignore_settings=("allow_target_reuse",))
    assert guarded.run_settings["allow_target_reuse"] is False
    assert guarded.run_manifest["run_settings"]["allow_target_reuse"] is False


def test_default_and_explicit_true_are_the_0_1_run():
    arguments = dict(policy=s1_policy(SKUS, [30.0, 20.0]),
                     demand_source=s1_demand(SKUS, [10.0, 7.0, 12.0, 9.0]), inventory=state())
    default = SimulationEngine().run(**arguments)
    explicit = SimulationEngine().run(**arguments, allow_target_reuse=True)
    _assert_same_run(default, explicit)
    assert "allow_target_reuse" not in default.run_settings
    with pytest.raises(ValueError, match="allow_target_reuse must be True or False"):
        SimulationEngine().run(**arguments, allow_target_reuse="no")


def test_base_policy_origin_must_be_exact_under_the_guard():
    from stockcast import PeriodicSchedule

    def policy(origin):
        return s1_policy(["a"], [30.0], origin, review_period=None,
                         schedule=PeriodicSchedule(every=3, start=1))

    arguments = dict(demand_source=s1_demand(["a"], [10.0] * 3), inventory=state(["a"]))
    # Default: a target fitted at the opening may serve a first decision a week later.
    SimulationEngine().run(policy=policy(ORIGIN), **arguments)
    with pytest.raises(ValueError, match=r"forecast_origin 2024-01-01 is not the information date "
                                         r"2024-01-08 of the first decision \(period 1\)"):
        SimulationEngine().run(policy=policy(ORIGIN), allow_target_reuse=False, **arguments)
    exact = SimulationEngine().run(policy=policy(ORIGIN + WEEK), allow_target_reuse=False,
                                   **arguments)
    assert exact.to_event_frame()["decision_flag"].sum() == 1


def test_undated_planner_levels_also_need_a_policy_per_decision():
    planner = ReorderPointPolicy(lead_time=2, review_period=1, order_quantity=12.0,
                                 allow_backorders=False).fit(reorder_point=5.0)
    arguments = dict(policy=planner, demand_source=s1_demand(["a"], [10.0] * 3),
                     inventory=state(["a"]), freq="W-MON")
    SimulationEngine().run(**arguments)
    with pytest.raises(ValueError, match="decision period 1 "):
        SimulationEngine().run(**arguments, allow_target_reuse=False)


def test_run_comparison_forwards_the_guard_to_every_branch():
    schedule = rolling_schedule(order_up_to(), weekly_targets(), state(), target_column="q_90")
    demand = weekly_demand(SKUS, 5)
    with pytest.raises(ValueError, match="allow_target_reuse=False: decision period 1"):
        SimulationEngine().run_comparison(
            {"rolling": schedule[0], "static": schedule[0]},
            policy_schedules={"rolling": schedule},
            demand_source=demand, inventory=state(), allow_target_reuse=False,
        )
    comparison = SimulationEngine().run_comparison(
        {"rolling": schedule[0]}, policy_schedules={"rolling": schedule},
        demand_source=demand, inventory=state(), allow_target_reuse=False,
    )
    assert comparison["rolling"].run_settings["allow_target_reuse"] is False
    pd.testing.assert_frame_equal(
        comparison["rolling"].to_event_frame(),
        run(schedule, demand, allow_target_reuse=False).to_event_frame(), check_exact=True,
    )
    plain = SimulationEngine().run_comparison(
        {"rolling": schedule[0]}, policy_schedules={"rolling": schedule},
        demand_source=demand, inventory=state(),
    )
    assert "allow_target_reuse" not in plain["rolling"].run_settings


# ---------------------------------------------------------------------------
# missing_targets="no_order" (S6)
# ---------------------------------------------------------------------------

AUDIT = [
    "decision_inventory_position", "requested_order_quantity",
    "callback_adjusted_order_quantity", "callback_adjustment_units",
]


def gap_schedule(gap_period=2, gap_sku="b", **policy_kwargs):
    """A full rolling schedule, and the same one without ``gap_sku`` at ``gap_period``."""
    targets = weekly_targets()
    full = rolling_schedule(order_up_to(**policy_kwargs), targets, state(on_hand=5.0),
                            target_column="q_90")
    gappy = targets.copy()
    gappy.loc[(gappy["unique_id"] == gap_sku)
              & (gappy["cutoff"] == ORIGIN + gap_period * WEEK), "q_90"] = np.nan
    with_gap = rolling_schedule(order_up_to(**policy_kwargs), gappy, state(on_hand=5.0),
                                target_column="q_90", missing_targets="no_order")
    return full, with_gap


def test_default_raises_exactly_as_in_0_1():
    _, with_gap = gap_schedule()
    with pytest.raises(ValueError) as caught:
        run(with_gap, weekly_demand(SKUS, 5), state(on_hand=5.0))
    assert str(caught.value) == (
        "policy_schedule period 2 has no fitted level for 1 inventory SKU(s): ['b']; "
        "fit it on every SKU of the inventory"
    )
    with pytest.raises(ValueError, match="missing_targets must be 'raise' or 'no_order'"):
        run(with_gap, weekly_demand(SKUS, 5), state(on_hand=5.0), missing_targets="skip")


def test_one_gap_equals_an_override_to_zero_except_target_and_audit():
    from stockcast import ScheduledOrderOverride

    full, with_gap = gap_schedule()
    demand = weekly_demand(SKUS, 5)
    gap = run(with_gap, demand, state(on_hand=5.0), missing_targets="no_order")
    override = run(full, demand, state(on_hand=5.0), callbacks=[ScheduledOrderOverride(
        pd.DataFrame({"unique_id": ["b"], "date": [ORIGIN + 3 * WEEK], "order_quantity": [0.0]}),
    )])
    assert len(override.to_callback_audit_frame()) == 1
    assert override.to_callback_audit_frame()["before_value"].iloc[0] > 0  # the override bites
    gap_events, override_events = gap.to_event_frame(), override.to_event_frame()
    assert list(gap_events.columns) == (
        list(override_events.columns[:list(override_events.columns).index("order_arrival_flag") + 1])
        + ["target_missing_flag"]
    )
    same = [c for c in override_events.columns if c not in AUDIT + ["target_level"]]
    pd.testing.assert_frame_equal(gap_events[same], override_events[same], check_exact=True)
    pd.testing.assert_frame_equal(gap.to_order_frame(), override.to_order_frame(), check_exact=True)
    differ = gap_events["target_level"].ne(override_events["target_level"])
    assert gap_events.loc[differ, ["unique_id", "demand_period"]].values.tolist() == [["b", 2]]
    assert np.isnan(gap_events.loc[differ, "target_level"]).all()
    assert gap_events["target_missing_flag"].tolist() == [
        sku == "b" and period == 2
        for period, sku in zip(gap_events["demand_period"], gap_events["unique_id"])
    ]
    assert gap.run_settings["missing_targets"] == {
        "mode": "no_order", "decisions_without_target": 1,
        "skus": [{"unique_id": "b", "decisions": 1}],
    }
    assert gap.run_manifest["run_settings"]["missing_targets"] == gap.run_settings["missing_targets"]
    assert "missing_targets" not in override.run_settings
    assert "target_missing_flag" not in override_events


def test_target_stays_missing_until_the_next_decision_with_a_target():
    # Review every 2 weeks: the gap decision is followed by a period without one.
    policy = order_up_to(review_period=2)
    rows = []
    for t in (0, 2, 4):
        for i, sku in enumerate(SKUS):
            rows.append({"unique_id": sku, "cutoff": ORIGIN + t * WEEK,
                         "date": ORIGIN + (t + 4) * WEEK,
                         "q_90": np.nan if (sku, t) == ("a", 2) else 40.0 + t + i})
    schedule = rolling_schedule(policy, pd.DataFrame(rows), state(), target_column="q_90",
                                missing_targets="no_order")
    result = run(schedule, weekly_demand(SKUS, 6), missing_targets="no_order")
    a = result.to_event_frame().query("unique_id == 'a'")
    assert a["target_level"].isna().tolist() == [False, False, True, True, False, False]
    assert a["target_missing_flag"].tolist() == [False, False, True, False, False, False]
    assert (a.loc[a["target_missing_flag"], "order_quantity"] == 0).all()


def test_no_order_without_any_gap_records_zero_and_changes_nothing_else():
    full, _ = gap_schedule()
    demand = weekly_demand(SKUS, 5)
    plain = run(full, demand, state(on_hand=5.0))
    checked = run(full, demand, state(on_hand=5.0), missing_targets="no_order")
    events_checked = checked.to_event_frame()
    assert not events_checked.pop("target_missing_flag").any()
    pd.testing.assert_frame_equal(events_checked, plain.to_event_frame(), check_exact=True)
    assert checked.run_settings["missing_targets"] == {
        "mode": "no_order", "decisions_without_target": 0, "skus": [],
    }


def test_a_reused_snapshot_carries_its_gaps():
    base = s1_policy(["a"], [30.0])  # no level for 'b', reused at periods 0 and 1
    result = SimulationEngine().run(
        policy=base, policy_schedule={2: s1_policy(SKUS, [32.0, 22.0], ORIGIN + 2 * WEEK)},
        demand_source=s1_demand(SKUS, [10.0] * 4), inventory=state(),
        missing_targets="no_order",
    )
    events_b = result.to_event_frame().query("unique_id == 'b'")
    assert events_b["target_missing_flag"].tolist() == [True, True, False, False]
    assert events_b["target_level"].tolist()[2:] == [22.0, 22.0]
    assert result.run_settings["missing_targets"]["decisions_without_target"] == 2
    # ... while 'a' still orders on its own (reused) target.
    assert result.to_event_frame().query("unique_id == 'a'")["order_quantity"].sum() > 0


def test_unusable_no_order_runs_raise():
    demand = s1_demand(SKUS, [10.0] * 3)
    base = s1_policy(["a"], [30.0])
    # 'b' has a target at no decision of the run.
    with pytest.raises(ValueError, match=r"1 inventory SKU\(s\) have no target at any decision "
                                         r"of the run: \['b'\]"):
        SimulationEngine().run(policy=base, policy_schedule={1: s1_policy(["a"], [31.0], ORIGIN + WEEK)},
                               demand_source=demand, inventory=state(), missing_targets="no_order")
    # A decision without a target for any SKU.
    with pytest.raises(ValueError, match=r"decision period 1 \(information date 2024-01-08\) "
                                         r"has no target for any inventory SKU"):
        SimulationEngine().run(policy=s1_policy(SKUS, [30.0, 20.0]),
                               policy_schedule={1: s1_policy(["zz"], [31.0], ORIGIN + WEEK)},
                               demand_source=demand, inventory=state(), missing_targets="no_order")
    # A custom policy: Stockcast does not know its fitted SKUs.
    from stockcast import BasePolicy, OrderDecision

    class Custom(BasePolicy):
        def fit(self):
            self.fitted_ = True
            return self

        def predict(self, inventory_state_df, *, current_period, **kwargs):
            ids = inventory_state_df.get_dataframe()["unique_id"]
            return OrderDecision(pd.DataFrame({"unique_id": ids, "order_quantity": 0.0}),
                                 lead_time=self.lead_time)

    custom = Custom(lead_time=1, review_period=1, allow_backorders=False).fit()
    with pytest.raises(ValueError, match="needs to know which SKUs the policy has a target for.*Custom"):
        SimulationEngine().run(policy=custom, demand_source=demand, inventory=state(),
                               freq="W-MON", missing_targets="no_order")
    # One reorder point for every SKU covers any inventory: no gap, no error.
    uniform = ReorderPointPolicy(lead_time=1, review_period=1, order_quantity=5.0,
                                 allow_backorders=False).fit(reorder_point=3.0)
    result = SimulationEngine().run(policy=uniform, demand_source=demand, inventory=state(),
                                    freq="W-MON", missing_targets="no_order")
    assert result.run_settings["missing_targets"]["decisions_without_target"] == 0


def test_a_callback_cannot_adjust_a_sku_at_its_gap_decision():
    from stockcast import CallbackError, OrderAdjustmentResult, ScheduledOrderOverride, SimulationCallback

    _, with_gap = gap_schedule()
    override = ScheduledOrderOverride(pd.DataFrame(
        {"unique_id": ["b"], "date": [ORIGIN + 3 * WEEK], "order_quantity": [4.0]},
    ))
    with pytest.raises(CallbackError) as caught:
        run(with_gap, weekly_demand(SKUS, 5), state(on_hand=5.0), missing_targets="no_order",
            callbacks=[override])
    assert str(caught.value.__cause__) == (
        "order adjustment targets SKUs absent from the predicted decision: ['b']; it has "
        "no target at this decision, and missing_targets='no_order' places no order for it"
    )

    class Adjust(SimulationCallback):
        """Sets the order of the given SKUs at the gap decision."""

        def __init__(self, skus):
            self.skus = skus

        def on_after_prediction(self, decision, context):
            if context.date != ORIGIN + 3 * WEEK:
                return None
            return OrderAdjustmentResult(
                pd.DataFrame({"unique_id": self.skus, "order_quantity": 1.0}))

    # A SKU unknown for another reason keeps the 0.1.0 message, word for word ...
    full, _ = gap_schedule()
    with pytest.raises(CallbackError) as caught:
        run(full, weekly_demand(SKUS, 5), state(on_hand=5.0), callbacks=[Adjust(["zz"])])
    assert str(caught.value.__cause__) == (
        "order adjustment targets SKUs absent from the predicted decision: ['zz']"
    )
    # ... and a mix names the SKU without a target.
    with pytest.raises(CallbackError) as caught:
        run(with_gap, weekly_demand(SKUS, 5), state(on_hand=5.0), missing_targets="no_order",
            callbacks=[Adjust(["b", "zz"])])
    assert str(caught.value.__cause__) == (
        "order adjustment targets SKUs absent from the predicted decision: ['b', 'zz']; "
        "['b'] has no target at this decision, and missing_targets='no_order' places no "
        "order for it"
    )


class _GiveB(OrderingConstraint):
    """A custom constraint that orders 5 units of 'b' at every decision."""

    name = "give_b"

    def apply(self, order, context):
        frame = order.get_dataframe()
        requested = frame["order_quantity"].astype(float)
        frame["order_quantity"] = np.where(frame[order.sku_column] == "b", 5.0, requested)
        changed = frame["order_quantity"] != requested
        audit = pd.DataFrame({
            "unique_id": frame[order.sku_column].tolist(),
            "requested_order_quantity": requested,
            "constrained_order_quantity": frame["order_quantity"],
            "constraint_adjustment_units": frame["order_quantity"] - requested,
            "constraint_binding_flag": changed,
            "capacity_violation_flag": False,
            "binding_constraints": np.where(changed, self.name, ""),
        })
        return ConstraintResult(
            OrderDecision(frame, sku_column=order.sku_column, lead_time=order.lead_time), audit,
        )


def test_a_constraint_that_orders_a_sku_without_a_target_stops_the_run():
    _, with_gap = gap_schedule()
    with pytest.raises(ValueError) as caught:
        run(with_gap, weekly_demand(SKUS, 5), state(on_hand=5.0), missing_targets="no_order",
            order_constraints=[_GiveB()])
    assert str(caught.value) == (
        "ordering constraints gave an order to 1 SKU(s) without a target at decision "
        "period 2 (information date 2024-01-15): ['b']; with missing_targets='no_order' a "
        "SKU without a target places no order, so constraints must leave its order at zero"
    )


def test_adjusting_constraints_leave_a_gap_sku_at_zero():
    from stockcast import MinimumOrderQuantity, OrderMultiple
    from stockcast.evaluation import validate_event_frame

    _, with_gap = gap_schedule()
    result = run(with_gap, weekly_demand(SKUS, 5), state(on_hand=5.0), missing_targets="no_order",
                 order_constraints=[MinimumOrderQuantity(6.0, mode="adjust"),
                                    OrderMultiple(5.0, mode="adjust")])
    events = validate_event_frame(result.to_event_frame())
    gap = events.query("unique_id == 'b' and demand_period == 2").iloc[0]
    assert gap["target_missing_flag"] and gap["order_quantity"] == 0
    # The constraints did act on the other orders.
    assert events.loc[events["decision_flag"] & ~events["target_missing_flag"],
                      "order_quantity"].mod(5).eq(0).all()
    assert events["constraint_binding_flag"].any()


def test_run_comparison_forwards_missing_targets():
    full, with_gap = gap_schedule()
    demand = weekly_demand(SKUS, 5)
    comparison = SimulationEngine().run_comparison(
        {"full": full[0], "gap": with_gap[0]},
        policy_schedules={"full": full, "gap": with_gap},
        demand_source=demand, inventory=state(on_hand=5.0), missing_targets="no_order",
    )
    alone = run(with_gap, demand, state(on_hand=5.0), missing_targets="no_order")
    pd.testing.assert_frame_equal(comparison["gap"].to_event_frame(), alone.to_event_frame(),
                                  check_exact=True)
    assert comparison["full"].run_settings["missing_targets"]["decisions_without_target"] == 0


@pytest.mark.parametrize("setting", ["plain", "constraint", "supply", "int_target"])
def test_array_path_equals_dataframe_path(monkeypatch, setting):
    from stockcast import MinimumOrderQuantity, Supplier, SupplyModel

    _, with_gap = gap_schedule()
    demand = weekly_demand(SKUS, 5)
    inventory = state(on_hand=5.0)
    options = {}
    if setting == "constraint":
        options["order_constraints"] = [MinimumOrderQuantity(6.0, mode="adjust")]
    if setting == "supply":
        options["supply"] = SupplyModel([Supplier("only", lead_time=2)])
    if setting == "int_target":
        # A target column the arrays cannot hold: the gap is cleared on the frame.
        inventory.data["target_level"] = np.int64(0)

    def outputs():
        result = run(copy.deepcopy(with_gap), demand, copy.deepcopy(inventory),
                     missing_targets="no_order", **options)
        return result, _manifest(result)

    fast, fast_manifest = outputs()
    with monkeypatch.context() as patch:
        patch.setattr(SimulationEngine, "_array_hooks_supported", lambda self: False)
        reference, reference_manifest = outputs()
    pd.testing.assert_frame_equal(fast.to_event_frame(), reference.to_event_frame(), check_exact=True)
    pd.testing.assert_frame_equal(fast.history, reference.history, check_exact=True)
    pd.testing.assert_frame_equal(fast.to_order_frame(), reference.to_order_frame(), check_exact=True)
    assert fast_manifest == reference_manifest
    row = fast.to_event_frame().query("unique_id == 'b' and demand_period == 2").iloc[0]
    assert row["target_missing_flag"] and np.isnan(row["target_level"]) and row["order_quantity"] == 0


def test_validator_accepts_and_rejects_the_flag():
    from stockcast.evaluation import validate_event_frame

    _, with_gap = gap_schedule()
    valid = run(with_gap, weekly_demand(SKUS, 5), state(on_hand=5.0),
                missing_targets="no_order").to_event_frame()
    assert validate_event_frame(valid)["target_missing_flag"].sum() == 1
    flagged = valid.index[valid["target_missing_flag"]][0]
    other = valid.index[(valid["unique_id"] == "a") & (valid["demand_period"] == 2)][0]

    def broken(change):
        frame = valid.copy()
        change(frame)
        return frame

    def flag_other(frame):
        frame.loc[other, "target_missing_flag"] = True   # 'a' had a target and ordered

    def flag_with_target(frame):
        frame.loc[other, ["target_missing_flag", "target_level"]] = [True, np.nan]  # but ordered

    def not_a_decision(frame):
        frame.loc[flagged, "decision_flag"] = False

    def with_level(frame):
        frame.loc[flagged, "target_level"] = 30.0

    for change in (flag_other, flag_with_target, not_a_decision, with_level):
        with pytest.raises(ValueError, match="target_missing_flag may be set only on decision rows"):
            validate_event_frame(broken(change))
    as_numbers = broken(lambda frame: frame.__setitem__(
        "target_missing_flag", frame["target_missing_flag"].astype(int)))
    with pytest.raises(ValueError, match="target_missing_flag must contain boolean values"):
        validate_event_frame(as_numbers)


@pytest.mark.parametrize("kind", ["order_up_to", "reorder_point"])
def test_runs_leave_the_callers_policies_and_state_untouched(kind):
    # The 0.2.0 speed paths use validated snapshots without a second copy and
    # skip copies around built-in checks; none of that may reach the caller.
    targets = weekly_targets()
    if kind == "order_up_to":
        schedule = rolling_schedule(order_up_to(), targets, state(), target_column="q_90")
    else:
        schedule = rolling_schedule(
            ReorderPointPolicy(lead_time=2, review_period=1, freq="W-MON",
                               order_quantity=12.0, allow_backorders=False),
            targets.rename(columns={"q_80": "s"}), state(), reorder_point_column="s",
        )
    inventory = state()
    demand = weekly_demand(SKUS, 5)
    before = {period: pickle.dumps(policy) for period, policy in schedule.items()}
    inventory_before = pickle.dumps(inventory)

    run(schedule, demand, inventory=inventory)
    run(schedule, demand, inventory=inventory, allow_target_reuse=False,
        order_constraints=[OrderMultiple(5, mode="adjust")])
    SimulationEngine().run(policy=schedule[0], demand_source=demand, inventory=inventory)
    SimulationEngine().run_comparison(
        {"rolling": schedule[0], "static": schedule[0]}, demand, inventory,
        policy_schedules={"rolling": schedule},
    )

    assert {period: pickle.dumps(policy) for period, policy in schedule.items()} == before
    assert pickle.dumps(inventory) == inventory_before
