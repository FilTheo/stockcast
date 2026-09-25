"""Supplier delivery outcomes (``Supplier(..., delivery=DeliveryOutcome)``).

A ``DeliveryOutcome`` decides, when a supplier's delivery falls due, how much
arrives now, how much arrives later and how much never arrives. These tests
check that:

- a supplier without an outcome, or with the pass-through base class, gives
  the default run (the latter adds only a zero ``supplier_shortfall_units``
  column and the manifest entry);
- short, late and repeatedly delayed deliveries keep every ledger identity,
  reconcile with the order frame and give equal results on both engine paths;
- invalid outcomes fail closed;
- the lead-time warning fires only when deliveries arrive off the policy's
  lead time;
- a custom allocation can follow a supplier chosen by the policy.
"""

from __future__ import annotations

import copy
import warnings

import numpy as np
import pandas as pd
import pytest

import stockcast as sc
from stockcast.core import (
    ORDER_FRAME_COLUMNS,
    DeliveryOutcome,
    ShelfLifeEngine,
    SimulationEngine,
    Supplier,
    SupplierAllocation,
    SupplyModel,
)
from stockcast.evaluation import validate_event_frame

ORIGIN = pd.Timestamp("2026-03-02")
DAY = pd.Timedelta(days=1)
SKUS = ["beans", "milk"]
OUTCOME_COLUMNS = [
    "scheduled_due_period", "received_quantity", "delayed_quantity", "undelivered_quantity",
]


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


def _run(state=None, policy=None, *, n_periods=20, engine=None, **options):
    engine = engine or SimulationEngine()
    return engine.run(
        policy or _policy(), _demand(n_periods), state if state is not None else _state(),
        n_periods, period_frequency="D", warmup_periods=0, scoring_periods=n_periods,
        settlement_periods=0, order_during_settlement=False,
        demand_source_name="delivery_outcome_test", random_seed=None, **options,
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


class Shaky(DeliveryOutcome):
    """Seeded: 20% of deliveries slip ``delay`` periods, 20% arrive half full."""

    def __init__(self, delay=2):
        self.delay = delay

    def resolve(self, due, context):
        draw = context.rng.random(len(due))
        late = draw < 0.2
        short = (draw >= 0.2) & (draw < 0.4)
        out = due.copy()
        out["received_quantity"] = np.where(
            late, 0.0, np.where(short, 0.5 * due["quantity"], due["quantity"])
        )
        out["delayed_quantity"] = np.where(late, due["quantity"], 0.0)
        out["delay_periods"] = self.delay
        return out

    def get_config(self):
        return {"delay": self.delay}


class Returns(DeliveryOutcome):
    """Returns a fixed frame built by ``make(due)`` (for validation tests)."""

    def __init__(self, make):
        self.make = make

    def resolve(self, due, context):
        return self.make(due)


def _shaky_supply(seed=5, lead=2):
    return SupplyModel([Supplier("wholesaler", lead_time=lead, delivery=Shaky())],
                       random_seed=seed)


def _quiet(**kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return _run(**kwargs)


def _assert_reconciles(result):
    """The order frame adds up to the ledger's receipts, shortfall and pipeline."""
    events = validate_event_frame(result.to_event_frame())
    orders = result.to_order_frame()
    assert list(orders.columns) == list(ORDER_FRAME_COLUMNS) + OUTCOME_COLUMNS
    done = orders[orders["status"] != "open"]
    for column, ledger_column in [("received_quantity", "received_units"),
                                  ("undelivered_quantity", "supplier_shortfall_units")]:
        by_row = done.groupby(["unique_id", "due_period"])[column].sum()
        ledger = events.set_index(["unique_id", "period"])[ledger_column]
        joined = pd.concat([by_row.rename("orders"), ledger.rename("ledger")], axis=1).fillna(0.0)
        np.testing.assert_allclose(joined["orders"], joined["ledger"], atol=1e-9, err_msg=column)
    last = events[events["period"] == events["period"].max()].set_index("unique_id")
    open_rows = orders[orders["status"] == "open"].groupby("unique_id")["delivery_quantity"].sum()
    np.testing.assert_allclose(
        open_rows.reindex(last.index).fillna(0.0), last["on_order_end"], atol=1e-9,
    )
    # Every resolved row is split into received, delayed and undelivered.
    np.testing.assert_allclose(
        done["received_quantity"] + done["delayed_quantity"] + done["undelivered_quantity"],
        done["delivery_quantity"], atol=1e-9,
    )
    return events, orders


# ---------------------------------------------------------------------------
# Mapping: without an outcome, or with the pass-through outcome
# ---------------------------------------------------------------------------

def test_supplier_without_outcome_adds_no_column_and_no_order_frame_columns():
    result = _run(supply=SupplyModel([Supplier("wholesaler", lead_time=2)]))
    assert "supplier_shortfall_units" not in result.to_event_frame().columns
    assert list(result.to_order_frame().columns) == list(ORDER_FRAME_COLUMNS)
    assert "delivery" not in result.run_settings["supply"]["suppliers"][0]


@pytest.mark.parametrize("lead,every,backorders", [
    (1, 1, True), (2, 1, False), (3, 2, True), (2, 3, False),
])
@pytest.mark.parametrize("use_arrays", [True, False])
def test_pass_through_outcome_reproduces_the_default_run(monkeypatch, lead, every, backorders,
                                                         use_arrays):
    if not use_arrays:
        monkeypatch.setattr(SimulationEngine, "_array_hooks_supported", lambda self: False)
    policy = _policy(lead, every=every, backorders=backorders)
    state = _state(backorders=backorders, pipeline=[[4, 0, 3, 0, 0, 0], [0, 2, 0, 0, 0, 0]])
    default = _run(state, policy, n_periods=25)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        mapped = _run(state, policy, n_periods=25, supply=SupplyModel(
            [Supplier("wholesaler", lead_time=lead, delivery=DeliveryOutcome())]
        ))
    events = mapped.to_event_frame()
    assert (events["supplier_shortfall_units"] == 0.0).all()
    _assert_same(events.drop(columns="supplier_shortfall_units"), default.to_event_frame())
    _assert_same(mapped.history, default.history)
    _assert_same(mapped.inventory.get_dataframe(), default.inventory.get_dataframe())
    _assert_same(mapped.to_callback_audit_frame(), default.to_callback_audit_frame())
    orders = mapped.to_order_frame()
    expected = default.to_order_frame()
    base = orders[list(ORDER_FRAME_COLUMNS)].drop(columns="supplier_id")
    _assert_same(base, expected.drop(columns="supplier_id"))
    assert (orders["received_quantity"] == np.where(
        orders["status"] == "received", orders["delivery_quantity"], 0.0)).all()
    assert (orders["delayed_quantity"] == 0.0).all()
    assert (orders["undelivered_quantity"] == 0.0).all()
    assert (orders["scheduled_due_period"] == orders["due_period"]).all()
    assert mapped.run_settings["supply"]["suppliers"][0]["delivery"] == {
        "module": "stockcast.core.supply", "class": "DeliveryOutcome", "config": {},
    }


# ---------------------------------------------------------------------------
# Disrupted deliveries
# ---------------------------------------------------------------------------

def test_short_and_late_deliveries_keep_identities_and_reconcile():
    result = _quiet(n_periods=40, supply=_shaky_supply())
    events, orders = _assert_reconciles(result)
    assert events["supplier_shortfall_units"].sum() > 0
    assert (orders["due_period"] > orders["scheduled_due_period"]).any()
    assert set(orders["status"]) <= {"received", "open", "disrupted"}
    assert (orders["status"] == "disrupted").any()


def test_placed_rows_count_every_ordered_unit_once_even_with_delays():
    result = _quiet(n_periods=40, supply=_shaky_supply())
    orders = result.to_order_frame()
    assert (orders["source"] == "delayed").any()
    placed = orders[orders["source"] == "placed"]
    per_period = placed.groupby(["unique_id", "order_period"])["delivery_quantity"].sum()
    ledger = result.to_event_frame().set_index(["unique_id", "period"])["order_quantity"]
    np.testing.assert_allclose(per_period.to_numpy(), ledger.loc[per_period.index].to_numpy())
    np.testing.assert_allclose(placed["delivery_quantity"].sum(), ledger.sum())


def test_array_and_dataframe_paths_are_identical(monkeypatch):
    arrays = _quiet(n_periods=30, supply=_shaky_supply())
    monkeypatch.setattr(SimulationEngine, "_array_hooks_supported", lambda self: False)
    frames = _quiet(n_periods=30, supply=_shaky_supply())
    _assert_same(arrays.to_event_frame(), frames.to_event_frame())
    _assert_same(arrays.history, frames.history)
    _assert_same(arrays.to_order_frame(), frames.to_order_frame())


def test_outcomes_are_reproducible_under_the_seed():
    first = _quiet(n_periods=30, supply=_shaky_supply(seed=5))
    again = _quiet(n_periods=30, supply=_shaky_supply(seed=5))
    other = _quiet(n_periods=30, supply=_shaky_supply(seed=6))
    _assert_same(first.to_event_frame(), again.to_event_frame())
    _assert_same(first.to_order_frame(), again.to_order_frame())
    assert not first.to_event_frame()["received_units"].equals(
        other.to_event_frame()["received_units"]
    )


def test_undelivered_stock_leaves_the_pipeline_but_not_the_shelf():
    """Half of every delivery never arrives: the order is still charged in full."""
    half = Returns(lambda due: due.assign(received_quantity=0.5 * due["quantity"]))
    result = _run(n_periods=15, supply=SupplyModel([Supplier("w", lead_time=2, delivery=half)]))
    events, orders = _assert_reconciles(result)
    arrived = events[events["received_units"] > 0]
    np.testing.assert_allclose(arrived["supplier_shortfall_units"], arrived["received_units"])
    # The ordered quantity is what the retailer placed, not what arrived.
    assert events["order_quantity"].sum() == pytest.approx(
        orders.loc[orders["source"] == "placed", "delivery_quantity"].sum()
    )


def test_owed_remainder_arrives_later_and_repeated_delays_are_resolved_again():
    """Received half now, the other half one period later; it slips once more."""
    def make(due):
        first = due["due_period"] == due["scheduled_due_period"]
        slips = due["due_period"] - due["scheduled_due_period"]
        return due.assign(
            received_quantity=np.where(first, 0.5 * due["quantity"],
                                       np.where(slips >= 2, due["quantity"], 0.0)),
            delayed_quantity=np.where(first, 0.5 * due["quantity"],
                                      np.where(slips >= 2, 0.0, due["quantity"])),
            delay_periods=1,
        )
    result = _quiet(n_periods=20, supply=SupplyModel(
        [Supplier("w", lead_time=2, delivery=Returns(make))]
    ))
    events, orders = _assert_reconciles(result)
    assert events["supplier_shortfall_units"].sum() == 0.0
    lines = orders[orders["source"].isin(["placed", "delayed"])]
    received = lines.groupby("order_id")["received_quantity"].sum()
    ordered = lines.groupby("order_id")["ordered_quantity"].first()
    arrived = lines.groupby("order_id")["due_period"].max() <= events["period"].max()
    np.testing.assert_allclose(received[arrived], ordered[arrived])
    assert set((lines["due_period"] - lines["scheduled_due_period"]).unique()) == {0, 1, 2}
    # Delayed parts are follow-up rows; the original rows are the placed orders.
    slipped = lines["due_period"] > lines["scheduled_due_period"]
    assert (lines.loc[slipped, "source"] == "delayed").all()
    assert (lines.loc[~slipped, "source"] == "placed").all()


def test_declared_opening_orders_of_the_supplier_are_resolved_too():
    state = _state().with_open_orders(pd.DataFrame({
        "unique_id": ["beans"], "supplier_id": ["w"], "order_period": [-1],
        "quantity": [12.0], "due_period": [1],
    }))
    seen = []

    class Record(DeliveryOutcome):
        def resolve(self, due, context):
            seen.append(due.copy())
            return due.assign(received_quantity=0.0)

    result = _run(state, n_periods=3, supply=SupplyModel(
        [Supplier("w", lead_time=2, delivery=Record())]
    ))
    assert seen[0]["source"].tolist() == ["opening"]
    events = result.to_event_frame()
    first = events[(events["unique_id"] == "beans") & (events["demand_period"] == 0)]
    assert first["supplier_shortfall_units"].item() == 12.0
    assert first["received_units"].item() == 0.0


def test_context_is_defensive_and_carries_period_date_and_seeded_rng():
    contexts = []

    class Inspect(DeliveryOutcome):
        def resolve(self, due, context):
            contexts.append((context, due.copy()))
            context.inventory["on_hand"] = 1e9
            return None

    result = _run(n_periods=6, supply=SupplyModel(
        [Supplier("w", lead_time=2, delivery=Inspect())]
    ))
    context, due = contexts[0]
    assert (due["due_period"] == context.period).all()
    assert context.date == ORIGIN + context.period * DAY
    assert context.supplier_id == "w" and context.sku_column == "unique_id"
    assert context.rng is None  # no SupplyModel seed
    assert result.to_event_frame()["ending_on_hand"].max() < 1e6
    seeded = []

    class Draws(DeliveryOutcome):
        def resolve(self, due, context):
            seeded.append(context.rng.random())
            return None

    _run(n_periods=6, supply=SupplyModel([Supplier("w", lead_time=2, delivery=Draws())],
                                         random_seed=1))
    first = list(seeded)
    seeded.clear()
    _run(n_periods=6, supply=SupplyModel([Supplier("w", lead_time=2, delivery=Draws())],
                                         random_seed=1))
    assert seeded == first


def test_the_callers_outcome_object_is_not_used_as_run_state():
    outcome = Shaky()
    supply = SupplyModel([Supplier("w", lead_time=2, delivery=outcome)], random_seed=3)
    before = copy.deepcopy(outcome.__dict__)
    first = _quiet(n_periods=15, supply=supply)
    again = _quiet(n_periods=15, supply=supply)
    assert outcome.__dict__ == before
    _assert_same(first.to_event_frame(), again.to_event_frame())


def test_only_suppliers_with_an_outcome_are_resolved():
    class Nothing(DeliveryOutcome):
        def resolve(self, due, context):
            assert set(due["supplier_id"]) == {"flaky"}
            return due.assign(received_quantity=0.0)

    supply = SupplyModel(
        [Supplier("steady", lead_time=2), Supplier("flaky", lead_time=2, delivery=Nothing())],
        allocation=sc.SupplierShares({"steady": 0.6, "flaky": 0.4}),
    )
    events, orders = _assert_reconciles(_run(n_periods=12, supply=supply))
    done = orders[orders["status"] != "open"]
    assert (done.loc[done["supplier_id"] == "steady", "undelivered_quantity"] == 0).all()
    assert (done.loc[done["supplier_id"] == "flaky", "received_quantity"] == 0).all()
    # Per SKU and period: steady + flaky ordered quantities = the ledger's order.
    placed = orders[orders["source"] == "placed"]
    split = placed.pivot_table(index=["unique_id", "order_period"], columns="supplier_id",
                               values="delivery_quantity", aggfunc="sum").fillna(0.0)
    ledger = events.set_index(["unique_id", "period"])["order_quantity"]
    np.testing.assert_allclose(split["steady"] + split["flaky"],
                               ledger.loc[split.index].to_numpy())


def test_shelf_life_lots_receive_only_what_arrived():
    half = Returns(lambda due: due.assign(received_quantity=0.5 * due["quantity"]))
    result = _run(n_periods=15, engine=ShelfLifeEngine(shelf_life_days=4),
                  supply=SupplyModel([Supplier("w", lead_time=2, delivery=half)]),
                  opening_lots=pd.DataFrame({
                      "unique_id": SKUS, "received_date": [ORIGIN, ORIGIN],
                      "quantity": [10.0, 8.0],
                  }))
    events = validate_event_frame(result.to_event_frame())
    assert events["supplier_shortfall_units"].sum() > 0
    assert events["expired_units"].sum() >= 0


def test_run_comparison_accepts_outcomes():
    comparison = SimulationEngine().run_comparison(
        [_policy(), _policy(targets=(40.0, 35.0))], _demand(15), _state(), 15,
        labels=["low", "high"], period_frequency="D", warmup_periods=0,
        scoring_periods=15, settlement_periods=0, order_during_settlement=False,
        demand_source_name="delivery_outcome_test", random_seed=None,
        supply=SupplyModel([Supplier("w", lead_time=2, delivery=DeliveryOutcome())]),
    )
    for label in ("low", "high"):
        assert "supplier_shortfall_units" in comparison[label].to_event_frame()


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("make,message", [
    (lambda due: [1.0] * len(due), "must return a pandas DataFrame or None"),
    (lambda due: due, "must return a received_quantity column"),
    (lambda due: due.assign(received_quantity=-1.0), "finite values >= 0"),
    (lambda due: due.assign(received_quantity=np.nan), "finite values >= 0"),
    (lambda due: due.assign(received_quantity=due["quantity"] + 1), "must not exceed"),
    (lambda due: due.assign(received_quantity=0.0, delayed_quantity=due["quantity"]),
     "must return delay_periods"),
    (lambda due: due.assign(received_quantity=0.0, delayed_quantity=due["quantity"],
                            delay_periods=0), "integer >= 1"),
    (lambda due: due.assign(received_quantity=0.0, delayed_quantity=due["quantity"],
                            delay_periods=1.5), "integer >= 1"),
    (lambda due: due.assign(received_quantity=0.0).iloc[:0], "one row per due delivery"),
    (lambda due: due.assign(received_quantity=0.0).reset_index(drop=True).set_axis(
        np.arange(len(due)) + 100), "one row per due delivery"),
])
def test_invalid_outcomes_are_rejected(make, message):
    with pytest.raises((TypeError, ValueError), match=message):
        _run(n_periods=6, supply=SupplyModel([Supplier("w", lead_time=2, delivery=Returns(make))]))


def test_delays_beyond_the_pipeline_window_are_rejected():
    late = Returns(lambda due: due.assign(
        received_quantity=0.0, delayed_quantity=due["quantity"], delay_periods=6,
    ))
    with pytest.raises(ValueError, match="increase inventory max_lead_time"):
        _quiet(state=_state(max_lead=6), n_periods=6,
               supply=SupplyModel([Supplier("w", lead_time=2, delivery=late)]))


def test_outcomes_require_deliveries_after_the_order_period():
    for lead_time in (0, {0: 0.5, 2: 0.5}):
        supply = SupplyModel([Supplier("w", lead_time=lead_time, delivery=DeliveryOutcome())],
                             random_seed=1)
        with pytest.raises(ValueError, match="at least one period after the order"):
            _run(_state(), _policy(0), n_periods=4, supply=supply)


def test_supplier_delivery_must_be_an_outcome():
    with pytest.raises(TypeError, match="DeliveryOutcome instance or None"):
        Supplier("w", lead_time=2, delivery=lambda due, context: None)


# ---------------------------------------------------------------------------
# Lead-time warning
# ---------------------------------------------------------------------------

def test_no_warning_when_deliveries_arrive_on_the_policy_lead_time():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _run(supply=SupplyModel([Supplier("w", lead_time=2)]))
        short = Returns(lambda due: due.assign(received_quantity=0.8 * due["quantity"]))
        _run(supply=SupplyModel([Supplier("w", lead_time=2, delivery=short)]))


@pytest.mark.parametrize("supplier", [
    Supplier("w", lead_time=3),
    Supplier("w", lead_time={2: 0.5, 3: 0.5}),
    Supplier("w", lead_time=2, partial_deliveries=[(0, 0.5), (1, 0.5)]),
    Supplier("w", lead_time=2, delivery=Shaky()),
])
def test_warning_when_deliveries_arrive_off_the_policy_lead_time(supplier):
    with pytest.warns(UserWarning, match="policy's lead_time"):
        _run(n_periods=30, supply=SupplyModel([supplier], random_seed=4))


def test_warning_is_issued_once_per_run():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _run(n_periods=30, supply=SupplyModel([Supplier("w", lead_time=3)]))
    assert len([w for w in caught if "policy's lead_time" in str(w.message)]) == 1


# ---------------------------------------------------------------------------
# Policy-chosen supplier through AllocationContext.decision
# ---------------------------------------------------------------------------

class ExpeditingPolicy(sc.OrderUpToPolicy):
    """Order-up-to that names a supplier: the fast one when stock is low."""

    def predict(self, inventory_state_df, *, current_period, **kwargs):
        decision = super().predict(inventory_state_df, current_period=current_period, **kwargs)
        frame = decision.get_dataframe()
        on_hand = inventory_state_df.get_dataframe().set_index("unique_id")["on_hand"]
        frame["supplier_id"] = np.where(
            frame["unique_id"].map(on_hand).to_numpy() < 5, "express", "regular",
        )
        return sc.OrderDecision(frame, lead_time=decision.lead_time,
                                review_period=decision.review_period)


class FollowPolicy(SupplierAllocation):
    def allocate(self, orders, context):
        chosen = context.decision.set_index(context.sku_column)["supplier_id"]
        return orders.assign(supplier_id=orders[context.sku_column].map(chosen))


def test_allocation_can_follow_the_supplier_chosen_by_the_policy():
    policy = _policy()
    policy.__class__ = ExpeditingPolicy
    seen = []

    class Watch(FollowPolicy):
        def allocate(self, orders, context):
            seen.append(context.decision.copy())
            return super().allocate(orders, context)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        result = _run(policy=policy, n_periods=20, state=_state(on_hand=(2.0, 20.0)),
                      supply=SupplyModel([Supplier("regular", lead_time=2),
                                          Supplier("express", lead_time=1)],
                                         allocation=Watch()))
    assert "supplier_id" in seen[0].columns
    orders = result.to_order_frame()
    placed = orders[orders["source"] == "placed"]
    assert set(placed["supplier_id"]) == {"regular", "express"}
    assert (placed.loc[placed["supplier_id"] == "express", "lead_time"] == 1).all()
    validate_event_frame(result.to_event_frame())


def test_existing_allocations_see_the_decision_without_behaviour_change():
    shares = sc.SupplierShares({"a": 0.5, "b": 0.5})
    supply = SupplyModel([Supplier("a", lead_time=2), Supplier("b", lead_time=2)],
                         allocation=shares)
    result = _run(supply=supply)
    validate_event_frame(result.to_event_frame())
