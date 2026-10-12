"""Speed paths (0.2.0) give exactly what the code they bypass gives.

Each fast path is compared with the general code it replaces: values, dtypes,
index and column order, or the exact error. Whole runs are covered by
``test_array_kernel_equivalence.py`` and by the golden-output comparison
recorded in knowledge 80.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockcast.core import InventoryStateDataFrame, OrderDecision, SimulationEngine
from stockcast.policies import OrderUpToPolicy, ReorderPointPolicy

OPENING = pd.Timestamp("2026-03-02")


def _assert_same_frame(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    assert list(map(str, actual.dtypes)) == list(map(str, expected.dtypes))
    pd.testing.assert_index_equal(actual.index, expected.index, exact=True)
    pd.testing.assert_frame_equal(actual, expected, check_exact=True)


def _order_up_to(ids, levels, sku_column="unique_id"):
    policy = OrderUpToPolicy(
        lead_time=2, review_period=1, freq="D", allow_backorders=False,
        sku_column=sku_column,
    )
    return policy.fit(
        pd.DataFrame({sku_column: ids, "S": levels}),
        target_column="S",
        forecast_origin=OPENING,
        protection_horizon=3,
    )


def _state(ids, on_hand, sku_column="unique_id"):
    return InventoryStateDataFrame.from_observed(
        pd.DataFrame({sku_column: ids, "on_hand": on_hand}),
        opening_date=OPENING, max_lead_time=2, allow_backorders=False,
        sku_column=sku_column,
    )


def _both_paths(policy, state, monkeypatch, **kwargs):
    """predict with the index lookup, then with the merge it replaces."""
    def outcome():
        try:
            return policy.predict(state, current_period=4, **kwargs).get_dataframe()
        except Exception as error:  # the exact error must match too
            return f"{type(error).__name__}: {error}"

    fast = outcome()
    with monkeypatch.context() as patch:
        patch.setattr(OrderUpToPolicy, "_orders_by_lookup", lambda *args: None)
        reference = outcome()
    return fast, reference


@pytest.mark.parametrize(
    ("ids", "sku_column"),
    [
        (["tea", "coffee", "milk"], "unique_id"),
        ([30, 10, 20], "unique_id"),
        ([("tea", "shop1"), ("tea", "shop2"), ("milk", "shop1")], "unique_id"),
        (["tea", "coffee", "milk"], "product"),
    ],
)
@pytest.mark.parametrize("plain_frame", [False, True])
def test_order_up_to_lookup_equals_the_merge(ids, sku_column, plain_frame, monkeypatch):
    policy = _order_up_to(list(reversed(ids)), [14.5, 0.0, 9.25], sku_column)
    state = _state(ids, [3.0, 20.0, 0.0], sku_column)
    if plain_frame:
        state = state.inventory_position()[[sku_column, "inventory_position"]]
        state.index = [7, 3, 5]  # the merge path renumbers rows; so must the lookup
    fast, reference = _both_paths(policy, state, monkeypatch)
    _assert_same_frame(fast, reference)


@pytest.mark.parametrize("plain_frame", [False, True])
def test_order_up_to_lookup_raises_the_merge_error_for_missing_levels(plain_frame, monkeypatch):
    policy = _order_up_to(["tea"], [5.0])
    state = _state(["tea", "coffee", "milk"], [1.0, 2.0, 3.0])
    if plain_frame:
        state = state.inventory_position()
    fast, reference = _both_paths(policy, state, monkeypatch)
    assert fast == reference == "ValueError: No target_level was fitted for SKUs: ['coffee', 'milk']"


def test_order_up_to_lookup_skips_ids_of_another_dtype(monkeypatch):
    policy = _order_up_to(["1", "2"], [5.0, 6.0])
    state = _state([1, 2], [1.0, 2.0])
    fast, reference = _both_paths(policy, state, monkeypatch)
    assert fast == reference  # both take the merge; pandas decides the outcome


def _record_flags(policy_class, monkeypatch):
    seen = []
    original = policy_class.predict

    def recording(self, inventory_state_df, **kwargs):
        seen.append(inventory_state_df._ready_checked)
        return original(self, inventory_state_df, **kwargs)

    monkeypatch.setattr(policy_class, "predict", recording)
    return seen


def _run(policy, *, arrays=True):
    demand = pd.DataFrame({
        "unique_id": ["tea"] * 4,
        "date": pd.date_range(OPENING + pd.Timedelta(days=1), periods=4, freq="D"),
        "y": [4.0, 6.0, 5.0, 7.0],
    })
    with pytest.MonkeyPatch.context() as patch:
        if not arrays:
            patch.setattr(SimulationEngine, "_array_hooks_supported", lambda self: False)
        return SimulationEngine().run(policy, demand, _state(["tea"], [10.0]))


def test_only_built_in_policies_on_array_states_skip_the_state_check(monkeypatch):
    class OwnOrderUpTo(OrderUpToPolicy):
        pass

    def fitted(policy_class):
        policy = policy_class(lead_time=2, review_period=1, freq="D", allow_backorders=False)
        return policy.fit(pd.DataFrame({"unique_id": ["tea"], "S": [12.0]}), target_column="S",
                          forecast_origin=OPENING, protection_horizon=3)

    seen = _record_flags(OrderUpToPolicy, monkeypatch)
    _run(fitted(OrderUpToPolicy))
    assert seen == [True] * 4
    seen.clear()
    _run(fitted(OwnOrderUpTo))  # a subclass may change its copy: full check
    assert seen == [False] * 4
    seen.clear()
    _run(fitted(OrderUpToPolicy), arrays=False)  # DataFrame path: full check
    assert seen == [False] * 4

    rop = ReorderPointPolicy(lead_time=2, review_period=1, freq="D", allow_backorders=False)
    rop.fit(reorder_point=5.0, order_up_to_level=12.0)
    seen = _record_flags(ReorderPointPolicy, monkeypatch)
    _run(rop)
    assert seen == [True] * 4


def test_states_outside_the_engine_are_always_checked():
    state = _state(["tea"], [10.0])
    assert state._ready_checked is False
    state.data.loc[0, "on_hand"] = -1.0
    with pytest.raises(ValueError, match="on_hand must be non-negative"):
        state.inventory_position()


def test_checked_order_tables_still_reject_invalid_quantities():
    frame = pd.DataFrame({
        "unique_id": ["tea"], "order_quantity": [np.inf], "target_level": [1.0],
        "inventory_position": [0.0], "reorder_point": [np.nan], "order_period": [0],
        "expected_delivery_period": [1],
    })
    with pytest.raises(ValueError) as fast:
        OrderDecision._from_checked(frame, "unique_id", 1)
    with pytest.raises(ValueError) as reference:
        OrderDecision(frame, lead_time=1)
    assert str(fast.value) == str(reference.value)


@pytest.mark.parametrize(
    ("ids", "quantities"),
    [
        (["tea", "coffee", "milk"], [3.5, 0.0, 1e-3]),
        ([("tea", "s1"), ("tea", "s2"), ("milk", "s1")], [0.0, 7.0, 2.0]),
        (list(pd.date_range("2026-01-01", periods=3)), [1.0, 0.0, 3.0]),
        ([3, 1, 2], np.array([4, 0, 6], dtype="int64")),
    ],
)
def test_order_line_counts_equal_row_iteration(ids, quantities):
    orders = OrderDecision(pd.DataFrame({"unique_id": ids, "order_quantity": quantities}),
                           lead_time=1)
    engine = SimulationEngine()
    engine._active_order_constraints = None
    engine._active_supply = None
    engine._begin_order_capture()
    engine._constrain_and_count(None, orders)

    # The row loop the counting replaced.
    frame = orders.data
    positive = frame["order_quantity"].fillna(0.0) > 0
    counts, squared_sums = {}, {}
    for sku, quantity in frame.loc[positive, ["unique_id", "order_quantity"]].itertuples(
        index=False, name=None
    ):
        quantity = float(quantity)
        counts[sku] = counts.get(sku, 0) + 1
        squared_sums[sku] = squared_sums.get(sku, 0.0) + quantity ** 2
    assert engine._captured_sku_order_line_counts == counts
    assert engine._captured_order_line_quantity_squared_sums == squared_sums
    assert [type(key) for key in engine._captured_sku_order_line_counts] == [type(key) for key in counts]
    assert engine._captured_order_event_count == 1


@pytest.mark.parametrize("rows", ["state order", "other order", "one sku left out"])
def test_callback_trail_equals_the_lookup(rows):
    state = _state(["tea", "coffee", "milk"], [1.0, 2.0, 3.0])
    ids, quantities = ["tea", "coffee", "milk"], [4.0, -0.0, 2.5]
    if rows == "other order":
        ids, quantities = ids[::-1], quantities[::-1]
    elif rows == "one sku left out":
        ids, quantities = ids[:2], quantities[:2]
    raw = OrderDecision(pd.DataFrame({"unique_id": ids, "order_quantity": quantities}),
                        lead_time=1)
    engine = SimulationEngine()
    engine._begin_order_capture()
    engine._capture_callback_order_trail(raw, raw, state)

    # The lookup the fast path replaces.
    state_ids = state.data["unique_id"].tolist()
    requested = pd.Series(state_ids).map(raw.data.set_index("unique_id")["order_quantity"]).fillna(0.0)
    adjusted = requested.copy()
    expected = pd.DataFrame({
        "unique_id": state_ids,
        "requested_order_quantity": requested,
        "callback_adjusted_order_quantity": adjusted,
        "callback_adjustment_units": adjusted - requested,
    })
    (trail,) = engine._captured_callback_order_audits
    _assert_same_frame(trail, expected)
    # -0.0 stays -0.0, as the lookup keeps it.
    assert np.signbit(trail["requested_order_quantity"].to_numpy()).tolist() == (
        np.signbit(expected["requested_order_quantity"].to_numpy()).tolist()
    )


def test_the_run_log_keeps_arrays_only(monkeypatch):
    from stockcast.core import _array_state

    logged = []
    original = _array_state.RunLog.event_frame

    def inspecting(self):
        logged.extend(self.records)
        return original(self)

    monkeypatch.setattr(_array_state.RunLog, "event_frame", inspecting)
    policy = OrderUpToPolicy(lead_time=2, review_period=1, freq="D", allow_backorders=False)
    policy.fit(pd.DataFrame({"unique_id": ["tea"], "S": [12.0]}), target_column="S",
               forecast_origin=OPENING, protection_horizon=3)
    events = _run(policy).to_event_frame()

    # Logged states hold no open-order book (the live state keeps it), and
    # each decision's order-line counts are arrays in state row order.
    assert len(logged) == 4
    assert all(record.start.book is None and record.final.book is None for record in logged)
    ordered = [record for record in logged if record.line_counts is not None]
    assert ordered and all(isinstance(record.line_counts, np.ndarray) for record in ordered)
    assert events["sku_order_line_count"].tolist() == [
        0 if record.line_counts is None else int(record.line_counts[0]) for record in logged
    ]
