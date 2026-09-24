"""The NumPy period kernel must reproduce the DataFrame period path exactly.

``SimulationEngine`` keeps live state as arrays and falls back to the
per-period pandas path for any state the arrays cannot represent. Forcing the
fallback for a whole run gives the reference implementation. Every scenario
here runs both ways and compares all run outputs value for value, dtype for
dtype, and column order included, or compares the exact exception.
"""

from __future__ import annotations

import copy
import random

import numpy as np
import pandas as pd
import pytest

import stockcast as sc
from stockcast.core import ShelfLifeEngine, SimulationEngine

ORIGIN = pd.Timestamp("2026-03-02")
DAY = pd.Timedelta(days=1)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _plain(frame: pd.DataFrame) -> pd.DataFrame:
    """Make object columns holding pipeline arrays comparable by value."""
    frame = frame.copy()
    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].map(
                lambda value: ("array", value.dtype.str, tuple(value.tolist()))
                if isinstance(value, np.ndarray) else value
            )
    return frame


def _assert_same_frame(actual: pd.DataFrame, expected: pd.DataFrame, label: str) -> None:
    assert list(actual.columns) == list(expected.columns), label
    assert list(map(str, actual.dtypes)) == list(map(str, expected.dtypes)), label
    pd.testing.assert_index_equal(actual.index, expected.index, exact=True, obj=label)
    pd.testing.assert_frame_equal(_plain(actual), _plain(expected), check_exact=True, obj=label)


def _manifest(result) -> dict:
    manifest = copy.deepcopy(result.run_manifest)
    manifest.pop("run_id")
    manifest.pop("created_at_utc")
    return manifest


def _outputs(result) -> dict:
    return {
        "events": result.to_event_frame(),
        "history": result.history,
        "final": result.inventory.get_dataframe(),
        "final_history": result.inventory.get_history(),
        "callback_audit": result.to_callback_audit_frame(),
    }


def _run_both(monkeypatch, run):
    """Run ``run()`` on the array kernel and on the forced DataFrame path."""
    outcomes = []
    for arrays in (True, False):
        with monkeypatch.context() as patch:
            if not arrays:
                patch.setattr(SimulationEngine, "_array_hooks_supported", lambda self: False)
            try:
                outcomes.append(("ok", run()))
            except Exception as exc:  # noqa: BLE001 -- compared below
                outcomes.append(("error", exc))
    (kind, fast), (reference_kind, reference) = outcomes
    assert kind == reference_kind, (fast, reference)
    if kind == "error":
        assert type(fast) is type(reference)
        assert str(fast) == str(reference)
        return None
    fast_outputs, reference_outputs = _outputs(fast), _outputs(reference)
    for name in fast_outputs:
        _assert_same_frame(fast_outputs[name], reference_outputs[name], name)
    assert (fast.inventory.has_stockout, fast.inventory.has_backorder) == (
        reference.inventory.has_stockout, reference.inventory.has_backorder
    )
    assert fast.run_settings == reference.run_settings
    assert _manifest(fast) == _manifest(reference)
    return fast


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _demand(matrix: np.ndarray, skus: list) -> pd.DataFrame:
    n_periods = matrix.shape[0]
    return pd.DataFrame({
        "unique_id": np.tile(np.asarray(skus, dtype=object), n_periods),
        "period": np.repeat(np.arange(n_periods), len(skus)),
        "date": np.repeat(pd.date_range(ORIGIN + DAY, periods=n_periods, freq="D"), len(skus)),
        "y": matrix.reshape(-1).astype(float),
    })


def _state(skus, *, max_lead, backorders, on_hand, pipeline=None):
    state = sc.InventoryStateDataFrame(
        list(skus), max_lead_time=max_lead, allow_backorders=backorders,
    ).initialize_zero(start_date=ORIGIN)
    state.data["on_hand"] = np.asarray(on_hand, dtype=float)
    if pipeline is not None:
        state.data["in_transit"] = [np.asarray(row, dtype=float) for row in pipeline]
    return state


def _order_up_to(skus, targets, *, lead, schedule, backorders, origin=ORIGIN):
    horizon = lead + (schedule.every if isinstance(schedule, sc.PeriodicSchedule) else 1)
    policy = sc.OrderUpToPolicy(
        lead_time=lead, schedule=schedule, service_level=0.9, allow_backorders=backorders,
    )
    return policy.fit(
        pd.DataFrame({"unique_id": skus, "S": targets, "end": origin + horizon * DAY}),
        forecast_origin=origin, forecast_frequency="D", target_column="S",
        target_end_date_column="end", protection_horizon=horizon,
        target_source="external_direct", target_probability=0.9,
    )


def _run(engine, policy, demand, state, *, warmup=0, settlement=0, during=False, **options):
    n_periods = int(demand["period"].max()) + 1
    return engine.run(
        policy, demand, state, n_periods, period_frequency="D",
        warmup_periods=warmup, scoring_periods=n_periods - warmup - settlement,
        settlement_periods=settlement, order_during_settlement=during,
        demand_source_name="kernel_equivalence", random_seed=None, **options,
    )


class ListedPolicy(sc.BasePolicy):
    """A user policy with sparse, integer-valued orders and integer targets."""

    def __init__(self, lead_time, quantities, *, schedule, allow_backorders):
        super().__init__(lead_time, allow_backorders=allow_backorders, schedule=schedule)
        self.quantities = dict(quantities)

    def fit(self, *args, **kwargs):
        self.fitted_ = True
        return self

    def predict(self, inventory_state_df, *, current_period, **kwargs):
        state = inventory_state_df.get_dataframe()
        ordered = state[state["unique_id"].isin(list(self.quantities))]
        return sc.OrderDecision(pd.DataFrame({
            "unique_id": ordered["unique_id"],
            "order_quantity": ordered["unique_id"].map(self.quantities).astype("int64"),
            "target_level": np.int64(50),
            "order_period": current_period,
            "expected_delivery_period": current_period + self.lead_time,
        }), lead_time=self.lead_time)


class HistoryReadingPolicy(sc.BasePolicy):
    """Orders last period's total demand; reads the state's history."""

    def __init__(self, lead_time, *, allow_backorders):
        super().__init__(lead_time, review_period=1, allow_backorders=allow_backorders)
        self.seen = []

    def fit(self, *args, **kwargs):
        self.fitted_ = True
        return self

    def predict(self, inventory_state_df, *, current_period, **kwargs):
        history = inventory_state_df.get_history()
        self.seen.append(len(history))
        state = inventory_state_df.get_dataframe()
        last = (
            history.groupby("unique_id", sort=False)["latest_incoming_demand"].last()
            if len(history) else pd.Series(dtype=float)
        )
        return sc.OrderDecision(pd.DataFrame({
            "unique_id": state["unique_id"],
            "order_quantity": state["unique_id"].map(last).fillna(5.0),
            "order_period": current_period,
            "expected_delivery_period": current_period + self.lead_time,
        }), lead_time=self.lead_time)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def _random_scenario(seed: int):
    rng = random.Random(seed)
    n_skus = rng.choice([1, 3, 6])
    skus = [f"SKU-{seed}-{index}" for index in range(n_skus)]
    backorders = rng.random() < 0.5
    lead = rng.choice([0, 1, 2, 3])
    max_lead = lead + rng.choice([0, 0, 2])
    review = rng.choice([1, 1, 2, 3, 7])
    n_periods = rng.choice([9, 14, 20])
    warmup = rng.choice([0, 2])
    settlement = rng.choice([0, 3])
    during = rng.random() < 0.5
    matrix = np.random.default_rng(seed).poisson(rng.choice([2, 8]), size=(n_periods, n_skus))
    on_hand = [float(rng.randint(0, 20)) for _ in skus]
    pipeline = (
        [[float(rng.randint(0, 6)) for _ in range(max_lead)] for _ in skus]
        if max_lead else None
    )
    schedule = sc.PeriodicSchedule(review, start=rng.choice([0, review - 1]))
    targets = [float(rng.randint(5, 40)) + rng.choice([0.0, 0.25]) for _ in skus]
    policy = _order_up_to(skus, targets, lead=lead, schedule=schedule, backorders=backorders)
    options = {}
    if rng.random() < 0.4:
        options["order_constraints"] = sc.OrderingConstraints([
            sc.MinimumOrderQuantity(float(rng.choice([3, 10])), mode="adjust"),
            sc.OrderMultiple(float(rng.choice([2, 5])), mode="adjust"),
        ])
    callbacks = []
    if rng.random() < 0.4:
        callbacks.append(sc.ScheduledOrderMultiplier(pd.DataFrame({
            "unique_id": skus[:1], "period": 1 + review, "multiplier": 1.5,
            "reason": "promotion", "source": "test",
        })))
    if rng.random() < 0.4:
        callbacks.append(sc.ScheduledInventoryAdjustment(pd.DataFrame({
            "unique_id": skus[-1:], "period": 3, "quantity_delta": -0.0,
            "reason": "count", "source": "test",
        })))
    if callbacks:
        options["callbacks"] = callbacks
    state = _state(skus, max_lead=max_lead, backorders=backorders, on_hand=on_hand,
                   pipeline=pipeline)
    return (policy, _demand(matrix, skus), state,
            dict(warmup=warmup, settlement=settlement, during=during, **options))


@pytest.mark.parametrize("seed", range(24))
def test_random_scenarios_match_dataframe_path(monkeypatch, seed):
    policy, demand, state, options = _random_scenario(seed)
    _run_both(monkeypatch, lambda: _run(
        SimulationEngine(), copy.deepcopy(policy), demand, state, **copy.deepcopy(options)
    ))


@pytest.mark.parametrize("backorders", [False, True])
@pytest.mark.parametrize("lead", [0, 2])
def test_shelf_life_matches_dataframe_path(monkeypatch, backorders, lead):
    skus = ["milk", "bread", "eggs"]
    matrix = np.random.default_rng(7 + lead).poisson(6, size=(25, 3))
    policy = _order_up_to(skus, [20.0, 14.0, 30.0], lead=lead,
                          schedule=sc.PeriodicSchedule(1), backorders=backorders)
    state = _state(skus, max_lead=max(lead, 1), backorders=backorders, on_hand=[8.0, 0.0, 12.0])
    lots = pd.DataFrame({
        "unique_id": ["milk", "milk", "eggs"],
        "received_date": [ORIGIN - 2 * DAY, ORIGIN, ORIGIN - DAY],
        "quantity": [5.0, 3.0, 12.0],
    })
    adjustment = sc.ScheduledInventoryAdjustment(pd.DataFrame({
        "unique_id": ["bread", "milk"],
        "period": [4, 6],
        "quantity_delta": [4.0, -1.0],
        "received_date": [ORIGIN + 3 * DAY, pd.NaT],
        "reason": ["found", "damaged"],
        "source": ["test", "test"],
    }))
    _run_both(monkeypatch, lambda: _run(
        ShelfLifeEngine(shelf_life_days=3), copy.deepcopy(policy), _demand(matrix, skus),
        state, opening_lots=lots, callbacks=[adjustment],
    ))


def test_non_canonical_opening_state_matches_dataframe_path(monkeypatch):
    """Integer opening stock, a custom index and an extra column."""
    skus = ["A", "B"]
    state = sc.InventoryStateDataFrame(
        pd.DataFrame({
            "unique_id": skus,
            "on_hand": [7, 0],
            "safety_stock": [1, 2],
            "backorders": [0, 3],
            "period": [0, 0],
            "date": [ORIGIN, ORIGIN],
            "in_transit": [np.array([2.0, 0.0]), np.array([0.0, 1.0])],
        }, index=[10, 20]),
        max_lead_time=2, allow_backorders=True,
    )
    state.data["note"] = "kept until the first transition"
    policy = _order_up_to(skus, [12.0, 9.0], lead=2, schedule=sc.PeriodicSchedule(2),
                          backorders=True)
    matrix = np.random.default_rng(3).poisson(3, size=(12, 2))
    _run_both(monkeypatch, lambda: _run(SimulationEngine(), copy.deepcopy(policy),
                                        _demand(matrix, skus), state))


def test_integer_orders_targets_and_sku_ids_match_dataframe_path(monkeypatch):
    skus = [101, 205, 309]
    state = sc.InventoryStateDataFrame(skus, max_lead_time=1, allow_backorders=False)
    state.initialize_zero(start_date=ORIGIN)
    policy = ListedPolicy(1, {101: 4, 309: 7}, schedule=sc.ExplicitSchedule((0, 2, 5)),
                          allow_backorders=False)
    demand = pd.DataFrame({
        "unique_id": np.tile(skus, 8),
        "period": np.repeat(np.arange(8), 3),
        "date": np.repeat(pd.date_range(ORIGIN + DAY, periods=8), 3),
        "y": np.random.default_rng(5).poisson(2, size=24).astype(float),
    })
    _run_both(monkeypatch, lambda: _run(SimulationEngine(), copy.deepcopy(policy), demand, state))


def test_custom_sku_column_and_zero_max_lead_time_match_dataframe_path(monkeypatch):
    skus = ["x", "y"]
    state = sc.InventoryStateDataFrame(
        pd.DataFrame({"item": skus}), max_lead_time=0, sku_column="item",
        allow_backorders=True,
    ).initialize_zero(start_date=ORIGIN)
    policy = ListedPolicy(0, {"x": 3.5, "y": 1.0}, schedule=sc.PeriodicSchedule(1),
                          allow_backorders=True)
    policy.predict = lambda inventory_state_df, *, current_period, **kwargs: sc.OrderDecision(
        pd.DataFrame({
            "item": inventory_state_df.get_dataframe()["item"],
            "order_quantity": [3.5, 1.0],
            "order_period": current_period,
            "expected_delivery_period": current_period,
        }), sku_column="item", lead_time=0,
    )
    demand = pd.DataFrame({
        "item": np.tile(skus, 6),
        "period": np.repeat(np.arange(6), 2),
        "date": np.repeat(pd.date_range(ORIGIN + DAY, periods=6), 2),
        "y": [4.0, 0.0, 1.0, 2.0, 5.0, 1.0, 0.0, 0.0, 3.0, 3.0, 2.0, 2.0],
    })
    _run_both(monkeypatch, lambda: _run(SimulationEngine(), copy.deepcopy(policy), demand, state))


def test_policy_reading_history_sees_identical_history(monkeypatch):
    skus = ["A", "B"]
    matrix = np.random.default_rng(11).poisson(4, size=(10, 2))
    seen = []

    def run():
        policy = HistoryReadingPolicy(1, allow_backorders=False).fit()
        result = _run(SimulationEngine(), policy, _demand(matrix, skus),
                      _state(skus, max_lead=1, backorders=False, on_hand=[5.0, 5.0]))
        seen.append(policy.seen)
        return result

    _run_both(monkeypatch, run)
    # The engine predicts with a deep copy of the policy; lengths match anyway.
    assert seen[0] == seen[1]


def test_rolling_policy_schedule_matches_dataframe_path(monkeypatch):
    skus = ["A", "B"]
    matrix = np.random.default_rng(13).poisson(5, size=(21, 2))
    schedule = sc.PeriodicSchedule(7)
    base = _order_up_to(skus, [40.0, 30.0], lead=2, schedule=schedule, backorders=True)
    snapshots = {
        period: _order_up_to(skus, [40.0 + period, 30.0 - period / 7], lead=2,
                             schedule=schedule, backorders=True,
                             origin=ORIGIN + period * DAY)
        for period in (7, 14)
    }
    _run_both(monkeypatch, lambda: _run(
        SimulationEngine(), copy.deepcopy(base), _demand(matrix, skus),
        _state(skus, max_lead=2, backorders=True, on_hand=[10.0, 0.0]),
        policy_schedule=copy.deepcopy(snapshots),
    ))


def test_invalid_callback_adjustment_raises_identically(monkeypatch):
    skus = ["A"]
    removal = sc.ScheduledInventoryAdjustment(pd.DataFrame({
        "unique_id": ["A"], "period": 2, "quantity_delta": -1000.0,
        "reason": "write-off", "source": "test",
    }))
    policy = _order_up_to(skus, [10.0], lead=1, schedule=sc.PeriodicSchedule(1),
                          backorders=False)
    matrix = np.ones((5, 1))
    _run_both(monkeypatch, lambda: _run(
        SimulationEngine(), copy.deepcopy(policy), _demand(matrix, skus),
        _state(skus, max_lead=1, backorders=False, on_hand=[4.0]), callbacks=[removal],
    ))


def test_array_path_is_used_and_falls_back_only_where_needed(monkeypatch):
    """Canonical periods run on arrays; a non-canonical opening only period 0."""
    from stockcast.core import simulation_engine

    recorded = []
    original = simulation_engine._PeriodRun._record_arrays

    def spy(self, *args, **kwargs):
        recorded.append(args[2])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(simulation_engine._PeriodRun, "_record_arrays", spy)
    skus = ["A", "B"]
    matrix = np.random.default_rng(1).poisson(3, size=(6, 2))
    policy = _order_up_to(skus, [12.0, 9.0], lead=1, schedule=sc.PeriodicSchedule(1),
                          backorders=True)
    _run(SimulationEngine(), policy, _demand(matrix, skus),
         _state(skus, max_lead=1, backorders=True, on_hand=[3.0, 0.0]))
    assert recorded == list(range(6))

    recorded.clear()
    state = _state(skus, max_lead=1, backorders=True, on_hand=[3.0, 0.0])
    state.data["on_hand"] = [3, 0]
    result = _run(SimulationEngine(), policy, _demand(matrix, skus), state)
    assert recorded == list(range(1, 6))
    assert result.to_event_frame()["starting_on_hand"].dtype == np.dtype("float64")


def test_run_comparison_matches_dataframe_path(monkeypatch):
    skus = ["A", "B", "C"]
    matrix = np.random.default_rng(17).poisson(4, size=(15, 3))
    weekly = _order_up_to(skus, [30.0, 25.0, 20.0], lead=2, schedule=sc.PeriodicSchedule(7),
                          backorders=True)
    daily = _order_up_to(skus, [12.0, 10.0, 9.0], lead=2, schedule=sc.PeriodicSchedule(1),
                         backorders=True)
    state = _state(skus, max_lead=2, backorders=True, on_hand=[10.0, 5.0, 0.0])
    outcomes = []
    for arrays in (True, False):
        with monkeypatch.context() as patch:
            if not arrays:
                patch.setattr(SimulationEngine, "_array_hooks_supported", lambda self: False)
            outcomes.append(SimulationEngine().run_comparison(
                [copy.deepcopy(weekly), copy.deepcopy(daily)], _demand(matrix, skus), state,
                15, period_frequency="D", warmup_periods=0, scoring_periods=15,
                settlement_periods=0, order_during_settlement=False,
                demand_source_name="kernel_equivalence", random_seed=None,
                labels=["weekly", "daily"],
            ))
    fast, reference = outcomes
    for label in ("weekly", "daily"):
        for name, frame in _outputs(fast[label]).items():
            _assert_same_frame(frame, _outputs(reference[label])[name], f"{label}.{name}")
    pd.testing.assert_frame_equal(fast.summary(), reference.summary(), check_exact=True)
