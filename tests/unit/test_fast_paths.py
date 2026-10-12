"""Speed paths (0.2.0) give exactly what the code they bypass gives.

Each fast path is compared with the general code it replaces: values, dtypes,
index and column order, or the exact error. Whole runs are covered by
``test_array_kernel_equivalence.py`` and by the golden-output comparison
recorded in knowledge 80.
"""

from __future__ import annotations

import pandas as pd
import pytest

from stockcast.core import InventoryStateDataFrame, SimulationEngine
from stockcast.policies import OrderUpToPolicy, ReorderPointPolicy

OPENING = pd.Timestamp("2026-03-02")


def _state(ids, on_hand, sku_column="unique_id"):
    return InventoryStateDataFrame.from_observed(
        pd.DataFrame({sku_column: ids, "on_hand": on_hand}),
        opening_date=OPENING, max_lead_time=2, allow_backorders=False,
        sku_column=sku_column,
    )


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
