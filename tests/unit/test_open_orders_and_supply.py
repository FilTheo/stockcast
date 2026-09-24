"""Order-level pipeline (open orders) and the optional supplier stage.

The per-SKU ``in_transit`` pipeline remains the accounting view. These tests
check that the open-order book stays in lockstep with it, that the default
engine is expressible through the order-level API with identical results,
and that suppliers, random lead times and partial deliveries keep every
event-ledger identity.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest

import stockcast as sc
from stockcast.core import (
    ORDER_FRAME_COLUMNS,
    AllocationContext,
    ShelfLifeEngine,
    SimulationEngine,
)
from stockcast.evaluation import validate_event_frame
from stockcast.utils import place_order_lines, update_inventory_with_orders

ORIGIN = pd.Timestamp("2026-03-02")
DAY = pd.Timedelta(days=1)
SKUS = ["beans", "milk"]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _state(*, max_lead=6, backorders=True, on_hand=(10.0, 8.0), pipeline=None):
    state = sc.InventoryStateDataFrame(
        list(SKUS), max_lead_time=max_lead, allow_backorders=backorders,
    ).initialize_zero(start_date=ORIGIN)
    state.data["on_hand"] = np.asarray(on_hand, dtype=float)
    if pipeline is not None:
        state.data["in_transit"] = [np.asarray(row, dtype=float) for row in pipeline]
    return state


def _demand(n_periods, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "unique_id": np.tile(SKUS, n_periods),
        "period": np.repeat(np.arange(n_periods), len(SKUS)),
        "date": np.repeat(pd.date_range(ORIGIN + DAY, periods=n_periods, freq="D"), len(SKUS)),
        "y": rng.poisson(5, len(SKUS) * n_periods).astype(float),
    })


def _policy(lead=2, *, every=1, backorders=True, targets=(30.0, 25.0)):
    horizon = lead + every
    policy = sc.OrderUpToPolicy(
        lead_time=lead, review_period=every, service_level=0.9, allow_backorders=backorders,
    )
    return policy.fit(
        pd.DataFrame({"unique_id": SKUS, "S": list(targets), "end": ORIGIN + horizon * DAY}),
        forecast_origin=ORIGIN, forecast_frequency="D", target_column="S",
        target_end_date_column="end", protection_horizon=horizon,
        target_source="external_direct", target_probability=0.9,
    )


def _run(state, policy=None, *, n_periods=20, engine=None, **options):
    engine = engine or SimulationEngine()
    return engine.run(
        policy or _policy(), _demand(n_periods), state, n_periods, period_frequency="D",
        warmup_periods=0, scoring_periods=n_periods, settlement_periods=0,
        order_during_settlement=False, demand_source_name="open_orders_test",
        random_seed=None, **options,
    )


def _plain(frame):
    frame = frame.copy()
    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].map(
                lambda value: ("array", value.dtype.str, tuple(value.tolist()))
                if isinstance(value, np.ndarray) else value
            )
    return frame


def _assert_same(actual, expected):
    assert list(actual.columns) == list(expected.columns)
    assert list(map(str, actual.dtypes)) == list(map(str, expected.dtypes))
    pd.testing.assert_frame_equal(_plain(actual), _plain(expected), check_exact=True)


def _assert_same_run(actual, expected):
    _assert_same(actual.to_event_frame(), expected.to_event_frame())
    _assert_same(actual.history, expected.history)
    _assert_same(actual.inventory.get_dataframe(), expected.inventory.get_dataframe())
    _assert_same(actual.to_callback_audit_frame(), expected.to_callback_audit_frame())


def _two_suppliers(seed=11, *, partial=True):
    return sc.SupplyModel(
        [
            sc.Supplier("local", lead_time=0),
            sc.Supplier(
                "import",
                lead_time={2: 0.5, 3: 0.3, 4: 0.2},
                partial_deliveries=[(0, 0.7), (1, 0.3)] if partial else None,
            ),
        ],
        allocation=sc.SupplierShares({"local": 0.4, "import": 0.6}),
        random_seed=seed,
    )


def _assert_order_frame_reconciles(result):
    """Deliveries add up to the event ledger's receipts and final pipeline."""
    events = result.to_event_frame()
    orders = result.to_order_frame()
    assert tuple(orders.columns) == ORDER_FRAME_COLUMNS
    received = orders[orders["status"] == "received"]
    # Received deliveries per SKU and period equal received_units (the
    # opening pipeline included).
    by_period = received.groupby(["unique_id", "due_period"])["delivery_quantity"].sum()
    event_receipts = events.set_index(["unique_id", "period"])["received_units"]
    event_receipts = event_receipts[event_receipts > 0]
    event_receipts.index = event_receipts.index.set_levels(
        event_receipts.index.levels[1].astype(int), level=1
    )
    np.testing.assert_allclose(
        by_period.reindex(event_receipts.index).to_numpy(), event_receipts.to_numpy(),
        rtol=1e-12, atol=1e-9,
    )
    assert np.isclose(by_period.sum(), events["received_units"].sum())
    last = events[events["period"] == events["period"].max()].set_index("unique_id")
    still_open = orders[orders["status"] == "open"].groupby("unique_id")["delivery_quantity"].sum()
    np.testing.assert_allclose(
        still_open.reindex(last.index).fillna(0.0).to_numpy(),
        last["on_order_end"].to_numpy(), rtol=1e-12, atol=1e-9,
    )
    placed = orders[orders["source"] == "placed"]
    assert np.isclose(placed["delivery_quantity"].sum(), events["order_quantity"].sum())


# ---------------------------------------------------------------------------
# State: attribution of in_transit and declared open orders
# ---------------------------------------------------------------------------

def test_in_transit_pipeline_is_shown_as_opening_orders_without_invented_facts():
    state = _state(pipeline=[[3, 0, 4, 0, 0, 0], [0, 5, 0, 0, 0, 0]])
    orders = state.open_orders()
    assert orders["unique_id"].tolist() == ["beans", "beans", "milk"]
    assert orders["due_period"].tolist() == [1, 3, 2]
    assert orders["remaining_quantity"].tolist() == [3.0, 4.0, 5.0]
    assert orders["source"].eq("opening").all()
    assert orders["supplier_id"].isna().all()
    assert orders["order_period"].isna().all()
    # The state itself is unchanged: in_transit remains the only record.
    assert state._open_orders is None
    receipts = state.scheduled_receipts()
    assert receipts["quantity"].sum() == 12.0


def test_declared_open_orders_set_an_empty_pipeline_and_carry_order_facts():
    state = _state().with_open_orders(pd.DataFrame({
        "unique_id": ["beans", "beans", "milk"],
        "supplier_id": ["roaster", "importer", "roaster"],
        "order_period": [-1, -3, np.nan],
        "order_id": ["PO-1", "PO-2", "PO-3"],
        "due_period": [1, 3, 2],
        "quantity": [3.0, 4.0, 5.0],
        "ordered_quantity": [3.0, 10.0, 5.0],
    }))
    assert [row.tolist() for row in state.data["in_transit"]] == [
        [3.0, 0.0, 4.0, 0.0, 0.0, 0.0], [0.0, 5.0, 0.0, 0.0, 0.0, 0.0],
    ]
    orders = state.open_orders()
    assert orders["supplier_id"].tolist() == ["roaster", "importer", "roaster"]
    assert orders.loc[1, "ordered_quantity"] == 10.0
    assert orders.loc[1, "remaining_quantity"] == 4.0
    assert orders.loc[1, "order_period"] == -3.0
    assert np.isnan(orders.loc[2, "order_period"])


def test_declared_partial_deliveries_group_into_one_order_line():
    state = _state().with_open_orders(pd.DataFrame({
        "unique_id": ["beans", "beans"],
        "supplier_id": ["importer", "importer"],
        "order_id": ["PO-9", "PO-9"],
        "due_period": [2, 5],
        "quantity": [6.0, 4.0],
    }))
    orders = state.open_orders()
    assert len(orders) == 1
    assert orders.loc[0, ["remaining_quantity", "ordered_quantity"]].tolist() == [10.0, 10.0]
    assert orders.loc[0, ["due_period", "final_due_period"]].tolist() == [2, 5]
    assert len(state.scheduled_receipts()) == 2


def test_open_orders_can_attribute_but_not_change_an_existing_pipeline():
    pipeline = [[3, 0, 4, 0, 0, 0], [0, 5, 0, 0, 0, 0]]
    matching = pd.DataFrame({
        "unique_id": ["beans", "beans", "milk"],
        "supplier_id": ["a", "b", "a"],
        "due_period": [1, 3, 2],
        "quantity": [3.0, 4.0, 5.0],
    })
    state = _state(pipeline=pipeline).with_open_orders(matching)
    assert state.open_orders()["supplier_id"].tolist() == ["a", "b", "a"]
    assert [row.tolist() for row in state.data["in_transit"]] == [
        [3.0, 0.0, 4.0, 0.0, 0.0, 0.0], [0.0, 5.0, 0.0, 0.0, 0.0, 0.0],
    ]
    conflicting = matching.assign(quantity=[3.0, 4.0, 6.0])
    with pytest.raises(ValueError, match="do not match in_transit"):
        _state(pipeline=pipeline).with_open_orders(conflicting)


@pytest.mark.parametrize(("rows", "message"), [
    ({"unique_id": ["tea"], "due_period": [1], "quantity": [1.0]}, "unknown SKUs"),
    ({"unique_id": ["beans"], "due_period": [0], "quantity": [1.0]}, "due_period must satisfy"),
    ({"unique_id": ["beans"], "due_period": [7], "quantity": [1.0]}, "due_period must satisfy"),
    ({"unique_id": ["beans"], "due_period": [1.5], "quantity": [1.0]}, "integer periods"),
    ({"unique_id": ["beans"], "due_period": [1], "quantity": [0.0]}, "must be > 0"),
    ({"unique_id": ["beans"], "due_period": [1], "quantity": [np.inf]}, "finite"),
    ({"unique_id": ["beans"], "due_period": [1], "quantity": [1.0], "order_period": [2]},
     "cannot be after"),
    ({"unique_id": ["beans"], "due_period": [1], "quantity": [1.0], "lead": [1]},
     "unsupported columns"),
    ({"unique_id": ["beans"], "quantity": [1.0]}, "missing required columns"),
    ({"unique_id": ["beans", "milk"], "order_id": [1, 1], "due_period": [1, 2],
      "quantity": [1.0, 1.0]}, "must agree"),
    ({"unique_id": ["beans", "beans"], "order_id": [1, 1], "due_period": [2, 2],
      "quantity": [1.0, 1.0]}, "same period"),
    ({"unique_id": ["beans"], "due_period": [1], "quantity": [5.0], "ordered_quantity": [4.0]},
     "ordered_quantity must be >="),
    ({"unique_id": ["beans"], "due_period": [1], "quantity": [1.0], "supplier_id": [" "]},
     "blank"),
])
def test_declared_open_orders_fail_closed(rows, message):
    with pytest.raises(ValueError, match=message):
        _state().with_open_orders(pd.DataFrame(rows))


def test_open_orders_require_an_initialized_state():
    state = sc.InventoryStateDataFrame(list(SKUS), max_lead_time=3, allow_backorders=True)
    with pytest.raises(ValueError, match="initialize"):
        state.with_open_orders(pd.DataFrame({"unique_id": ["beans"], "due_period": [1], "quantity": [1.0]}))


def test_editing_in_transit_after_declaring_orders_is_rejected():
    state = _state().with_open_orders(pd.DataFrame({
        "unique_id": ["beans"], "due_period": [2], "quantity": [4.0],
    }))
    state._validate_ready_state()
    state.data["in_transit"] = [np.array([0, 9.0, 0, 0, 0, 0]), np.zeros(6)]
    with pytest.raises(ValueError, match="must describe the same pipeline"):
        state._validate_ready_state()
    # Re-initializing declares a new, empty pipeline and drops the book.
    state.initialize_zero(start_date=ORIGIN)
    assert state._open_orders is None
    state._validate_ready_state()


# ---------------------------------------------------------------------------
# Manual primitives
# ---------------------------------------------------------------------------

def _decision(state, quantities, lead):
    period = int(state.data["period"].iloc[0])
    return sc.OrderDecision(
        pd.DataFrame({
            "unique_id": SKUS,
            "order_quantity": [float(value) for value in quantities],
            "target_level": [30.0, 25.0],
            "order_period": period,
            "expected_delivery_period": period + lead,
        }),
        lead_time=lead,
        review_period=1,
    )


def test_update_inventory_with_orders_keeps_legacy_states_unchanged():
    state = _state(pipeline=[[3, 0, 0, 0, 0, 0], [0, 0, 0, 0, 0, 0]])
    state = state.advance_period(period_frequency="D", is_review_period=True)
    after = update_inventory_with_orders(state, _decision(state, [5, 0], lead=2))
    assert after._open_orders is None
    assert after.data.loc[0, "in_transit"].tolist() == [0, 5.0, 0, 0, 0, 0]


@pytest.mark.parametrize("lead", [0, 1, 3])
def test_order_lines_reproduce_update_inventory_with_orders(lead):
    """One line per positive order, due order_period + L, is the per-SKU API."""
    state = _state(pipeline=[[3, 0, 0, 0, 0, 0], [0, 2, 0, 0, 0, 0]], on_hand=(0.0, 4.0))
    state.data["backorders"] = [2.0, 0.0]
    state = state.advance_period(period_frequency="D", is_review_period=True)
    period = int(state.data["period"].iloc[0])
    per_sku = update_inventory_with_orders(state, _decision(state, [5, 0], lead=lead))
    lines = sc.OrderLines(pd.DataFrame({
        "unique_id": ["beans"], "order_quantity": [5.0],
        "order_period": [period], "due_period": [period + lead],
    }))
    by_line = place_order_lines(state, lines)
    columns = [column for column in per_sku.data.columns if column != "target_level"]
    _assert_same(by_line.data[columns], per_sku.data[columns])


def test_place_order_lines_routes_suppliers_and_receives_immediate_lines_first():
    state = _state(pipeline=[[3, 0, 4, 0, 0, 0], [0, 5, 0, 0, 0, 0]], on_hand=(0.0, 8.0))
    state.data["backorders"] = [6.0, 0.0]
    state = state.advance_period(period_frequency="D", is_review_period=True)
    # The opening delivery of 3 clears half of the backlog of 6.
    assert state.data.loc[0, ["on_hand", "backorders"]].tolist() == [0.0, 3.0]
    lines = sc.OrderLines(pd.DataFrame({
        "unique_id": ["beans", "beans", "milk"],
        "supplier_id": ["local", "import", "local"],
        "order_quantity": [5.0, 7.0, 2.0],
        "order_period": [1, 1, 1],
        "due_period": [1, 4, 3],
    }))
    after = place_order_lines(state, lines)
    beans = after.data.iloc[0]
    assert beans["backorders"] == 0.0
    assert beans["on_hand"] == 2.0
    assert beans["latest_received"] == 3.0 + 5.0
    assert beans["latest_backorders_fulfilled"] == 3.0 + 3.0
    assert beans["latest_order"] == 12.0
    assert beans["in_transit"].tolist() == [0.0, 4.0, 7.0, 0.0, 0.0, 0.0]
    assert after.data.iloc[1]["in_transit"].tolist() == [5.0, 2.0, 0.0, 0.0, 0.0, 0.0]
    open_orders = after.open_orders()
    assert open_orders["supplier_id"].tolist() == [None, None, "import", "local"]
    assert open_orders["source"].tolist() == ["opening", "opening", "placed", "placed"]
    # After a period advance, due deliveries leave the book with the pipeline.
    later = after.fulfill_demand(pd.DataFrame({
        "unique_id": SKUS, "y": [0.0, 0.0], "date": after.data["date"].iloc[0],
    })).advance_period(period_frequency="D", is_review_period=False)
    later._validate_ready_state()
    assert later.open_orders()["due_period"].min() == 3


@pytest.mark.parametrize(("rows", "message"), [
    ({"unique_id": ["tea"], "order_quantity": [1.0], "order_period": [1], "due_period": [2]},
     "unknown SKUs"),
    ({"unique_id": ["beans"], "order_quantity": [1.0], "order_period": [0], "due_period": [2]},
     "order_period must equal"),
    ({"unique_id": ["beans"], "order_quantity": [1.0], "order_period": [1], "due_period": [8]},
     "max_lead_time"),
])
def test_place_order_lines_fail_closed_against_the_state(rows, message):
    state = _state().advance_period(period_frequency="D", is_review_period=True)
    with pytest.raises(ValueError, match=message):
        place_order_lines(state, sc.OrderLines(pd.DataFrame(rows)))


@pytest.mark.parametrize(("rows", "message"), [
    ({"unique_id": ["beans"], "order_quantity": [-1.0], "order_period": [1], "due_period": [2]},
     "non-negative"),
    ({"unique_id": ["beans"], "order_quantity": [1.0], "order_period": [2], "due_period": [1]},
     "due_period must be >="),
    ({"unique_id": ["beans"], "order_quantity": [1.0], "order_period": [1]}, "missing required"),
    ({"unique_id": ["beans", "milk"], "order_quantity": [1.0, 1.0], "order_period": [1, 1],
      "due_period": [2, 3], "order_line": ["x", "x"]}, "must agree"),
    ({"unique_id": ["beans"], "order_quantity": [1.0], "order_period": [1], "due_period": [2],
      "supplier_id": [["unhashable"]]}, "hashable"),
])
def test_order_lines_validate_their_own_rows(rows, message):
    with pytest.raises(ValueError, match=message):
        sc.OrderLines(pd.DataFrame(rows))


# ---------------------------------------------------------------------------
# Engine: the default is expressible through the order-level API
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lead", [0, 1, 3])
@pytest.mark.parametrize("backorders", [True, False])
@pytest.mark.parametrize("every", [1, 3])
def test_one_supplier_supply_model_reproduces_the_default_engine(lead, backorders, every):
    pipeline = [[3, 0, 4, 0, 0, 0], [0, 5, 0, 0, 0, 0]]
    policy = _policy(lead, every=every, backorders=backorders)
    default = _run(_state(pipeline=pipeline, backorders=backorders), policy)
    mapped = _run(
        _state(pipeline=pipeline, backorders=backorders), policy,
        supply=sc.SupplyModel([sc.Supplier("main", lead_time=lead)]),
    )
    _assert_same_run(mapped, default)
    assert "supply" not in default.run_settings
    assert mapped.run_settings["supply"]["suppliers"] == [
        {"supplier_id": "main", "lead_time": {"fixed": lead}, "partial_deliveries": [[0, 1.0]]}
    ]
    placed_default = default.to_order_frame().drop(columns="supplier_id")
    placed_mapped = mapped.to_order_frame().drop(columns="supplier_id")
    _assert_same(placed_mapped, placed_default)
    assert mapped.to_order_frame().query("source == 'placed'")["supplier_id"].eq("main").all()


def test_supply_mapping_holds_with_constraints_callbacks_and_shelf_life():
    policy = _policy(2)
    constraints = sc.OrderingConstraints([
        sc.OrderMultiple(4.0, mode="adjust"), sc.ShelfSpaceLimit(60.0, mode="adjust"),
    ])
    hold = sc.ScheduledOrderHold(pd.DataFrame({
        "unique_id": ["beans"], "period": [3], "reason": ["supplier closed"], "source": ["test"],
    }))
    lots = pd.DataFrame({"unique_id": SKUS, "received_date": [ORIGIN, ORIGIN], "quantity": [10.0, 8.0]})
    options = dict(order_constraints=constraints, callbacks=[hold], opening_lots=lots)
    default = _run(_state(), policy, engine=ShelfLifeEngine(shelf_life_days=4), **options)
    hold.reset(None)
    mapped = _run(
        _state(), policy, engine=ShelfLifeEngine(shelf_life_days=4),
        supply=sc.SupplyModel([sc.Supplier("main", lead_time=2)]), **options,
    )
    _assert_same_run(mapped, default)
    # The scenario really exercises each stage.
    assert len(mapped.to_callback_audit_frame()) == 1
    assert mapped.to_event_frame()["constraint_binding_flag"].any()
    assert mapped.to_event_frame()["expired_units"].sum() > 0


def test_declared_open_orders_run_exactly_like_the_equivalent_in_transit():
    pipeline_state = _state(pipeline=[[3, 0, 4, 0, 0, 0], [0, 5, 0, 0, 0, 0]])
    declared_state = _state().with_open_orders(pd.DataFrame({
        "unique_id": ["beans", "beans", "milk"],
        "supplier_id": ["roaster", "importer", "roaster"],
        "due_period": [1, 3, 2],
        "quantity": [3.0, 4.0, 5.0],
    }))
    legacy = _run(pipeline_state)
    declared = _run(declared_state)
    _assert_same_run(declared, legacy)
    assert "open_orders" not in legacy.run_manifest["opening_inventory"]
    assert declared.run_manifest["opening_inventory"]["open_orders"]["rows"] == 3
    opening = declared.to_order_frame().query("source == 'opening'")
    assert opening["supplier_id"].tolist() == ["roaster", "importer", "roaster"]
    assert opening["status"].eq("received").all()


def test_default_run_order_frame_lists_opening_and_placed_orders():
    result = _run(_state(pipeline=[[3, 0, 4, 0, 0, 0], [0, 5, 0, 0, 0, 0]]), _policy(3))
    orders = result.to_order_frame()
    assert set(orders["source"]) == {"opening", "placed"}
    placed = orders[orders["source"] == "placed"]
    assert placed["lead_time"].eq(3.0).all()
    assert placed["supplier_id"].isna().all()
    assert (placed["due_date"] - placed["order_date"]).eq(3 * DAY).all()
    assert orders["status"].eq("open").sum() > 0
    _assert_order_frame_reconciles(result)
    # A policy input carries the same book: its open orders match in_transit.
    assert result.inventory.open_orders()["remaining_quantity"].sum() == pytest.approx(
        sum(float(row.sum()) for row in result.inventory.data["in_transit"])
    )


def test_policy_editing_its_own_state_copy_behaves_as_before():
    """The engine's attributed book never adds checks to in_transit-only code."""

    class ScratchpadPolicy(sc.OrderUpToPolicy):
        def predict(self, inventory_state_df, **kwargs):
            inventory_state_df.data["in_transit"] = [
                np.zeros(inventory_state_df.max_lead_time) for _ in SKUS
            ]
            return super().predict(inventory_state_df, **kwargs)

    policy = ScratchpadPolicy(lead_time=2, review_period=1, service_level=0.9, allow_backorders=True)
    policy.fit(
        pd.DataFrame({"unique_id": SKUS, "S": [30.0, 25.0], "end": ORIGIN + 3 * DAY}),
        forecast_origin=ORIGIN, forecast_frequency="D", target_column="S",
        target_end_date_column="end", protection_horizon=3,
        target_source="external_direct", target_probability=0.9,
    )
    result = _run(_state(pipeline=[[3, 0, 4, 0, 0, 0], [0, 5, 0, 0, 0, 0]]), policy, n_periods=6)
    validate_event_frame(result.to_event_frame())


def test_policies_may_read_open_orders_at_decisions():
    seen = []

    class SupplierAwarePolicy(sc.OrderUpToPolicy):
        def predict(self, inventory_state_df, **kwargs):
            seen.append(inventory_state_df.open_orders())
            return super().predict(inventory_state_df, **kwargs)

    policy = SupplierAwarePolicy(lead_time=2, review_period=1, service_level=0.9, allow_backorders=True)
    policy.fit(
        pd.DataFrame({"unique_id": SKUS, "S": [30.0, 25.0], "end": ORIGIN + 3 * DAY}),
        forecast_origin=ORIGIN, forecast_frequency="D", target_column="S",
        target_end_date_column="end", protection_horizon=3,
        target_source="external_direct", target_probability=0.9,
    )
    _run(_state(), policy, n_periods=5, supply=_two_suppliers())
    assert len(seen) == 5
    assert set(seen[-1]["supplier_id"]) <= {"local", "import"}


# ---------------------------------------------------------------------------
# Engine: suppliers, random lead times, partial deliveries
# ---------------------------------------------------------------------------

def test_two_suppliers_keep_every_ledger_identity():
    result = _run(_state(pipeline=[[3, 0, 4, 0, 0, 0], [0, 5, 0, 0, 0, 0]]), supply=_two_suppliers())
    events = result.to_event_frame()
    validate_event_frame(events)
    _assert_order_frame_reconciles(result)
    placed = result.to_order_frame().query("source == 'placed'")
    assert set(placed["supplier_id"]) == {"local", "import"}
    # Two supplier lines per positive SKU decision.
    positive = events[events["order_quantity"] > 0]
    assert positive["sku_order_line_count"].eq(2).all()
    shares = placed.groupby("supplier_id")["delivery_quantity"].sum() / placed["delivery_quantity"].sum()
    assert shares["local"] == pytest.approx(0.4)
    # Local deliveries are immediate receipts (lead time 0).
    assert placed.loc[placed["supplier_id"] == "local", "lead_time"].eq(0.0).all()
    json.dumps(result.run_manifest["run_settings"]["supply"], allow_nan=False)


def test_random_lead_times_are_seeded_and_arrive_out_of_sequence():
    first = _run(_state(), n_periods=40, supply=_two_suppliers(seed=5, partial=False))
    again = _run(_state(), n_periods=40, supply=_two_suppliers(seed=5, partial=False))
    other = _run(_state(), n_periods=40, supply=_two_suppliers(seed=6, partial=False))
    _assert_same(first.to_order_frame(), again.to_order_frame())
    _assert_same_run(first, again)
    assert not first.to_order_frame().equals(other.to_order_frame())
    imported = first.to_order_frame().query("supplier_id == 'import'")
    assert set(imported["lead_time"]) == {2.0, 3.0, 4.0}
    by_order = imported.sort_values("order_period")
    # Some later order arrives before an earlier one.
    assert (np.diff(by_order["due_period"].to_numpy()) < 0).any()
    validate_event_frame(first.to_event_frame())
    _assert_order_frame_reconciles(first)


def test_partial_deliveries_split_each_line():
    supply = sc.SupplyModel([
        sc.Supplier("import", lead_time=2, partial_deliveries=[(0, 0.25), (2, 0.75)]),
    ])
    result = _run(_state(), _policy(4), supply=supply)
    placed = result.to_order_frame().query("source == 'placed'")
    per_line = placed.groupby("order_id")
    assert per_line.size().eq(2).all()
    assert per_line["lead_time"].apply(tuple).map(lambda leads: leads == (2.0, 4.0)).all()
    np.testing.assert_allclose(
        per_line["delivery_quantity"].sum().to_numpy(),
        per_line["ordered_quantity"].first().to_numpy(),
    )
    events = result.to_event_frame()
    assert events.loc[events["order_quantity"] > 0, "sku_order_line_count"].eq(1).all()
    validate_event_frame(events)
    _assert_order_frame_reconciles(result)


def test_comparison_branches_share_lead_time_draws():
    comparison = SimulationEngine().run_comparison(
        [_policy(2), _policy(2, targets=(40.0, 30.0))], _demand(30), _state(), 30,
        period_frequency="D", warmup_periods=0, scoring_periods=30, settlement_periods=0,
        order_during_settlement=False, demand_source_name="open_orders_test",
        random_seed=None, labels=["lean", "rich"], supply=_two_suppliers(seed=3, partial=False),
    )
    lean = comparison["lean"].to_order_frame().query("supplier_id == 'import'")
    rich = comparison["rich"].to_order_frame().query("supplier_id == 'import'")
    keys = ["unique_id", "order_period"]
    joined = lean.merge(rich, on=keys, suffixes=("_lean", "_rich"))
    assert len(joined) > 10
    assert joined["lead_time_lean"].equals(joined["lead_time_rich"])


def test_supply_runs_identically_on_the_array_and_dataframe_paths(monkeypatch):
    outcomes = []
    for arrays in (True, False):
        with monkeypatch.context() as patch:
            if not arrays:
                patch.setattr(SimulationEngine, "_array_hooks_supported", lambda self: False)
            outcomes.append(_run(
                _state(pipeline=[[3, 0, 4, 0, 0, 0], [0, 5, 0, 0, 0, 0]]),
                supply=_two_suppliers(),
            ))
    fast, reference = outcomes
    _assert_same_run(fast, reference)
    _assert_same(fast.to_order_frame(), reference.to_order_frame())


def test_shelf_life_turns_every_supplier_receipt_into_a_lot():
    lots = pd.DataFrame({"unique_id": SKUS, "received_date": [ORIGIN, ORIGIN], "quantity": [10.0, 8.0]})
    engine = ShelfLifeEngine(shelf_life_days=3)
    result = _run(_state(), engine=engine, supply=_two_suppliers(), opening_lots=lots)
    events = result.to_event_frame()
    validate_event_frame(events)
    assert events["expired_units"].sum() > 0
    final = result.inventory.data.set_index("unique_id")["on_hand"]
    assert engine.ledger.balances().reindex(final.index).to_numpy() == pytest.approx(final.to_numpy())


def test_custom_allocation_sees_open_orders_and_routes_orders():
    class BackupWhenPrimaryIsLoaded(sc.SupplierAllocation):
        """Send an order to the backup while the primary has too much open."""

        def __init__(self, limit):
            self.limit = limit
            self.calls = 0

        def reset(self):
            self.calls = 0

        def allocate(self, orders, context):
            assert isinstance(context, AllocationContext)
            self.calls += 1
            open_orders = context.open_orders
            loaded = open_orders[open_orders["supplier_id"] == "primary"].groupby(
                context.sku_column
            )["remaining_quantity"].sum()
            supplier = [
                "backup" if loaded.get(sku, 0.0) > self.limit else "primary"
                for sku in orders[context.sku_column]
            ]
            return orders.assign(supplier_id=supplier)[
                [context.sku_column, "supplier_id", "order_quantity"]
            ]

        def get_config(self):
            return {"limit": self.limit}

    supply = sc.SupplyModel(
        [sc.Supplier("primary", lead_time=4), sc.Supplier("backup", lead_time=1)],
        allocation=BackupWhenPrimaryIsLoaded(limit=15.0),
    )
    result = _run(_state(), supply=supply)
    placed = result.to_order_frame().query("source == 'placed'")
    assert set(placed["supplier_id"]) == {"primary", "backup"}
    assert result.run_settings["supply"]["allocation"]["config"] == {"limit": 15.0}
    # The caller's allocation object is not the run's working copy.
    assert supply.allocation.calls == 0
    validate_event_frame(result.to_event_frame())


# ---------------------------------------------------------------------------
# Fail-closed configuration
# ---------------------------------------------------------------------------

def test_supply_offsets_must_fit_the_state_pipeline_before_any_mutation():
    state = _state(max_lead=3)
    before = copy.deepcopy(state.data)
    supply = sc.SupplyModel([sc.Supplier("slow", lead_time=2, partial_deliveries=[(0, 0.5), (2, 0.5)])])
    with pytest.raises(ValueError, match="longest delivery offset \\(4\\)"):
        _run(state, _policy(2), supply=supply)
    _assert_same(state.data, before)


@pytest.mark.parametrize(("build", "error", "message"), [
    (lambda: sc.Supplier("a", lead_time=-1), ValueError, "integer >= 0"),
    (lambda: sc.Supplier("a", lead_time=1.5), TypeError, "integer or a mapping"),
    (lambda: sc.Supplier("a", lead_time={1: 0.5, 2: 0.4}), ValueError, "sum to 1"),
    (lambda: sc.Supplier("a", lead_time={1: 0.0, 2: 1.0}), ValueError, "> 0"),
    (lambda: sc.Supplier("a", lead_time=1, partial_deliveries=[(1, 0.5), (1, 0.5)]),
     ValueError, "strictly increasing"),
    (lambda: sc.Supplier("a", lead_time=1, partial_deliveries=[(0, 0.5)]), ValueError, "sum to 1"),
    (lambda: sc.Supplier(None, lead_time=1), ValueError, "must not be None"),
    (lambda: sc.SupplyModel([]), ValueError, "non-empty"),
    (lambda: sc.SupplyModel([sc.Supplier("a", 1), sc.Supplier("a", 2)],
                            allocation=sc.SupplierShares({"a": 1.0})), ValueError, "unique"),
    (lambda: sc.SupplyModel([sc.Supplier("a", 1), sc.Supplier("b", 2)]), ValueError,
     "requires an explicit allocation"),
    (lambda: sc.SupplyModel([sc.Supplier("a", {1: 0.5, 2: 0.5})]), ValueError, "random_seed"),
    (lambda: sc.SupplierShares({"a": 0.5}), ValueError, "sum to 1"),
    (lambda: sc.SupplierShares({"a": -0.5, "b": 1.5}), ValueError, ">= 0"),
])
def test_supply_configuration_fails_closed(build, error, message):
    with pytest.raises(error, match=message):
        build()


@pytest.mark.parametrize(("shares", "by_sku", "message"), [
    ({"a": 1.0, "ghost": 0.0}, None, "unknown suppliers"),
    ({"a": 1.0}, {"tea": {"a": 1.0}}, "unknown SKUs"),
])
def test_supplier_shares_are_checked_against_the_run(shares, by_sku, message):
    supply = sc.SupplyModel(
        [sc.Supplier("a", 1), sc.Supplier("b", 1)],
        allocation=sc.SupplierShares(shares, by_sku=by_sku),
    )
    with pytest.raises(ValueError, match=message):
        _run(_state(), supply=supply)


@pytest.mark.parametrize(("result", "message"), [
    (lambda orders, sku: orders.assign(supplier_id="a")[[sku, "supplier_id"]], "missing columns"),
    (lambda orders, sku: orders.assign(supplier_id="ghost")[[sku, "supplier_id", "order_quantity"]],
     "unknown suppliers"),
    (lambda orders, sku: orders.assign(supplier_id="a", order_quantity=orders["order_quantity"] / 2)[
        [sku, "supplier_id", "order_quantity"]], "full order quantity"),
    (lambda orders, sku: pd.concat([orders.assign(supplier_id="a")] * 2)[
        [sku, "supplier_id", "order_quantity"]], "at most one row"),
    (lambda orders, sku: orders.assign(supplier_id="a", extra=1), "unsupported columns"),
])
def test_custom_allocation_output_is_validated(result, message):
    class Broken(sc.SupplierAllocation):
        def allocate(self, orders, context):
            return result(orders, context.sku_column)

    supply = sc.SupplyModel([sc.Supplier("a", 1), sc.Supplier("b", 1)], allocation=Broken())
    with pytest.raises(ValueError, match=message):
        _run(_state(), supply=supply)


def test_supply_argument_type_is_checked():
    with pytest.raises(TypeError, match="SupplyModel"):
        _run(_state(), supply={"supplier": "a"})
