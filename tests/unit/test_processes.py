"""Inventory processes: ShelfLife migration, user processes and composition.

The migration tests compare against ``LegacyShelfLifeEngine``, a frozen copy
of the hook-based engine that existed before processes. Every durable output
(event ledger, history, final state, callback audit, order frame, run
settings and manifest) must be identical.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd
import pytest
from legacy_shelf_life_engine import LegacyShelfLifeEngine
from shelf_life_scenarios import (
    DAY,
    MIGRATION_SEEDS,
    ORIGIN,
    assert_same_outputs,
    demand_frame,
    order_up_to,
    outputs,
    random_scenario,
    run_kwargs,
    state_frame,
)

from stockcast.core import (
    PROCESS_FLOW_COLUMNS,
    Flow,
    InventoryProcess,
    ProcessFlows,
    ScheduledInventoryAdjustment,
    ShelfLife,
    ShelfLifeEngine,
    SimulationEngine,
)
from stockcast.evaluation import validate_event_frame

SHELF_SETTINGS = (
    "processes", "shelf_life", "shelf_life_unit", "opening_lot_count",
    "opening_lots", "opening_expiry_handling", "opening_expired_units",
)


def _outcome(run):
    try:
        return "ok", outputs(run())
    except Exception as exc:  # noqa: BLE001 -- compared by the caller
        return "error", (type(exc).__name__, str(exc))


def _assert_same_outcome(actual, expected, label, **options):
    assert actual[0] == expected[0], (label, actual, expected)
    if actual[0] == "error":
        assert actual[1] == expected[1], label
    else:
        assert_same_outputs(actual[1], expected[1], label, **options)


def _paths(monkeypatch):
    """Run once on the array kernel and once on the forced pandas path."""
    for arrays in (True, False):
        with monkeypatch.context() as patch:
            if not arrays:
                patch.setattr(SimulationEngine, "_array_hooks_supported", lambda self: False)
            yield arrays


# ---------------------------------------------------------------------------
# ShelfLife migration: identical to the pre-process engine
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("seed", MIGRATION_SEEDS)
def test_shelf_life_engine_reproduces_the_legacy_engine(monkeypatch, seed):
    shelf, scenario = random_scenario(seed)
    for arrays in _paths(monkeypatch):
        expected = _outcome(lambda: LegacyShelfLifeEngine(shelf).run(**run_kwargs(scenario)))
        actual = _outcome(lambda: ShelfLifeEngine(shelf).run(**run_kwargs(scenario)))
        _assert_same_outcome(actual, expected, f"seed {seed}, arrays={arrays}")


@pytest.mark.parametrize("seed", MIGRATION_SEEDS)
def test_shelf_life_process_reproduces_the_legacy_engine(monkeypatch, seed):
    """The old simple case, expressed with the new ``processes=`` API."""
    shelf, scenario = random_scenario(seed)

    def with_process():
        kwargs = run_kwargs(scenario)
        lots = kwargs.pop("opening_lots")
        handling = kwargs.pop("opening_expiry_handling")
        return SimulationEngine().run(**kwargs, processes=[ShelfLife(shelf, lots, handling)])

    for arrays in _paths(monkeypatch):
        expected = _outcome(lambda: LegacyShelfLifeEngine(shelf).run(**run_kwargs(scenario)))
        actual = _outcome(with_process)
        _assert_same_outcome(
            actual, expected, f"seed {seed}, arrays={arrays}",
            ignore_manifest_keys=SHELF_SETTINGS,
        )


def test_shelf_life_engine_comparison_reproduces_the_legacy_engine():
    shelf, scenario = random_scenario(3)
    outcomes = []
    for engine in (LegacyShelfLifeEngine(shelf), ShelfLifeEngine(shelf)):
        kwargs = run_kwargs(scenario)
        policy = kwargs.pop("policy")
        comparison = engine.run_comparison(
            [policy, copy.deepcopy(policy)], labels=["a", "b"], **kwargs,
        )
        outcomes.append({label: outputs(comparison[label]) for label in ("a", "b")})
    for label in ("a", "b"):
        assert_same_outputs(outcomes[1][label], outcomes[0][label], label)


def test_shelf_life_engine_keeps_its_ledger_attributes_and_settings():
    shelf, scenario = random_scenario(5)
    engine = ShelfLifeEngine(shelf)
    result = engine.run(**run_kwargs(scenario))
    final = result.inventory.get_dataframe().set_index("unique_id")["on_hand"]
    balances = engine.ledger.balances().reindex(final.index).fillna(0.0)
    assert balances.to_numpy() == pytest.approx(final.to_numpy())
    assert isinstance(engine.expired_this_period, dict)
    # No general flows and no user processes: the ledger and settings keep
    # exactly their previous shape.
    assert "processes" not in result.run_settings
    assert "process_inflow_units" not in result.to_event_frame()
    assert result.run_settings["shelf_life"] == shelf


def test_shelf_life_flow_frame_reconciles_with_expired_units():
    shelf, scenario = random_scenario(1)
    result = ShelfLifeEngine(shelf).run(**run_kwargs(scenario))
    events = result.to_event_frame()
    flows = result.to_process_flow_frame()
    assert tuple(flows.columns) == PROCESS_FLOW_COLUMNS
    assert set(flows["process"]) <= {"shelf_life"}
    assert set(flows["category"]) <= {"expiry"}
    assert set(flows["phase"]) <= {"before_demand"}
    expired = flows.groupby(["unique_id", "period"])["quantity"].sum()
    ledger = events.set_index(["unique_id", "period"])["expired_units"]
    assert events["expired_units"].sum() > 0
    assert ledger[ledger > 0].sort_index().to_numpy() == pytest.approx(
        expired.sort_index().to_numpy()
    )


# ---------------------------------------------------------------------------
# User-written processes
# ---------------------------------------------------------------------------

SKUS = ["milk", "bread"]


class InspectionLoss(InventoryProcess):
    """Removes a fixed share of stock on inspection days, after demand."""

    name = "inspection"
    flows = (Flow("damaged", "outflow"),)

    def __init__(self, share=0.5, every=3):
        self.share = share
        self.every = every

    def after_demand(self, context):
        if context.demand_period % self.every != self.every - 1:
            return None
        return ProcessFlows({"damaged": (self.share * context.on_hand).round()})

    def get_config(self):
        return {"share": self.share, "every": self.every}


class CustomerReturns(InventoryProcess):
    """Returns units before demand on chosen days, as fresh stock."""

    name = "returns"
    flows = (Flow("returned", "inflow"),)

    def __init__(self, units):
        self.units = dict(units)

    def before_demand(self, context):
        units = self.units.get(context.demand_period)
        if not units:
            return None
        return ProcessFlows({"returned": units}, received_dates={"returned": context.date})


class Recorder(InventoryProcess):
    """Observes every hook; reports no flows."""

    name = "recorder"

    def reset(self, context):
        self.calls = [("reset", context.period, None)]
        self.changes = []

    def before_demand(self, context):
        assert context.received is None and context.fulfilled is None
        self.calls.append(("before_demand", context.period, float(context.on_hand.sum())))

    def on_receipt(self, context):
        self.calls.append(("on_receipt", context.period, float(context.received.sum())))

    def after_demand(self, context):
        self.calls.append(("after_demand", context.period, float(context.fulfilled.sum())))

    def on_stock_change(self, change, context):
        self.changes.append((change.origin, change.source, change.unique_id, change.quantity))

    def check(self, context):
        self.calls.append(("check", context.period, float(context.on_hand.sum())))


def _setup(*, lead=1, backorders=False, n_periods=12, on_hand=(20.0, 12.0), targets=(30.0, 20.0)):
    matrix = np.random.default_rng(4).poisson(5, size=(n_periods, len(SKUS))).astype(float)
    policy = order_up_to(SKUS, list(targets), lead=lead, every=1, backorders=backorders)
    state = state_frame(SKUS, max_lead=max(lead, 1), backorders=backorders, on_hand=list(on_hand))
    return policy, demand_frame(matrix, SKUS), state


def _run(engine, policy, demand, state, **options):
    n_periods = int(demand["period"].max()) + 1
    return engine.run(
        policy, demand, state, n_periods, period_frequency="D",
        warmup_periods=0, scoring_periods=n_periods, settlement_periods=0,
        order_during_settlement=False, demand_source_name="processes",
        random_seed=None, **options,
    )


def _fresh_lots(state):
    frame = state.get_dataframe()
    return pd.DataFrame({
        "unique_id": frame["unique_id"],
        "received_date": ORIGIN,
        "quantity": frame["on_hand"].astype(float),
    })


def test_run_without_processes_has_no_process_outputs():
    policy, demand, state = _setup()
    result = _run(SimulationEngine(), policy, demand, state)
    assert "processes" not in result.run_settings
    assert "process_inflow_units" not in result.to_event_frame()
    flows = result.to_process_flow_frame()
    assert flows.empty and tuple(flows.columns) == PROCESS_FLOW_COLUMNS


def test_user_outflow_enters_the_ledger_balance_and_manifest():
    policy, demand, state = _setup()
    result = _run(SimulationEngine(), policy, demand, state, processes=[InspectionLoss()])
    events = result.to_event_frame()
    assert list(events.columns[-2:]) == ["process_inflow_units", "process_outflow_units"]
    assert events["process_outflow_units"].sum() > 0
    assert events["process_inflow_units"].sum() == 0
    assert events["inventory_adjustment_units"].sum() == 0
    validate_event_frame(events)
    physical = (
        events["starting_on_hand"] + events["received_units"] - events["backorders_fulfilled"]
        - events["fulfilled_units"] - events["expired_units"]
        + events["inventory_adjustment_units"]
        + events["process_inflow_units"] - events["process_outflow_units"]
    )
    assert physical.to_numpy() == pytest.approx(events["ending_on_hand"].to_numpy())

    flows = result.to_process_flow_frame()
    assert set(flows["flow"]) == {"damaged"} and set(flows["phase"]) == {"after_demand"}
    by_row = flows.groupby(["unique_id", "period"])["quantity"].sum()
    ledger = events.set_index(["unique_id", "period"])["process_outflow_units"]
    assert ledger[ledger > 0].sort_index().to_numpy() == pytest.approx(by_row.sort_index().to_numpy())

    (entry,) = result.run_settings["processes"]
    assert entry["name"] == "inspection" and entry["class"] == "InspectionLoss"
    assert entry["flows"] == [{"name": "damaged", "direction": "outflow", "category": "general"}]
    assert entry["config"] == {"share": 0.5, "every": 3}
    assert entry["enabled_hooks"] == ["after_demand"]
    assert result.run_manifest["run_settings"]["processes"] == result.run_settings["processes"]
    json.dumps(result.run_manifest, default=str)


def test_before_demand_inflow_is_seen_by_the_policy_and_demand():
    policy, demand, state = _setup(on_hand=(0.0, 0.0), targets=(0.0, 0.0))
    returns = CustomerReturns({0: {"milk": 4.0}})
    result = _run(SimulationEngine(), policy, demand, state, processes=[returns])
    first = result.to_event_frame().query("demand_period == 0").set_index("unique_id")
    assert first.loc["milk", "process_inflow_units"] == 4.0
    assert first.loc["milk", "fulfilled_units"] == min(4.0, first.loc["milk", "demand"])
    assert first.loc["bread", "process_inflow_units"] == 0.0


def test_processes_run_identically_on_array_and_pandas_paths(monkeypatch):
    policy, demand, state = _setup(lead=0)
    adjustment = ScheduledInventoryAdjustment(pd.DataFrame({
        "unique_id": ["bread"], "period": [4], "quantity_delta": [-1.0],
        "reason": ["count"], "source": ["test"],
    }))
    outcomes = []
    for _ in _paths(monkeypatch):
        outcomes.append(_outcome(lambda: _run(
            SimulationEngine(), copy.deepcopy(policy), demand, state,
            callbacks=[adjustment],
            processes=[
                ShelfLife(2, _fresh_lots(state)),
                InspectionLoss(share=0.25, every=2),
                CustomerReturns({3: {"bread": 2.0}}),
            ],
        )))
    assert outcomes[0][0] == "ok", outcomes[0]
    _assert_same_outcome(outcomes[0], outcomes[1], "array vs pandas")


def test_shelf_life_mirrors_other_processes_flows():
    policy, demand, state = _setup()
    shelf = ShelfLife(3, _fresh_lots(state))
    inspection = InspectionLoss()
    returns = CustomerReturns({2: {"milk": 3.0}})
    result = _run(SimulationEngine(), policy, demand, state,
                  processes=[shelf, inspection, returns])
    events = result.to_event_frame()
    assert events["expired_units"].sum() > 0
    assert events["process_outflow_units"].sum() > 0
    assert events["process_inflow_units"].sum() == 3.0
    validate_event_frame(events)
    final = result.inventory.get_dataframe().set_index("unique_id")["on_hand"]
    balances = shelf.ledger.balances().reindex(final.index).fillna(0.0)
    assert balances.to_numpy() == pytest.approx(final.to_numpy())
    assert [entry["name"] for entry in result.run_settings["processes"]] == [
        "shelf_life", "inspection", "returns",
    ]


def test_shelf_life_rejects_an_inflow_without_a_lot_date():
    class UndatedReturns(CustomerReturns):
        def before_demand(self, context):
            units = self.units.get(context.demand_period)
            return ProcessFlows({"returned": units}) if units else None

    policy, demand, state = _setup()
    with pytest.raises(ValueError, match="received_date for inflow 'returns.returned'"):
        _run(SimulationEngine(), policy, demand, state, processes=[
            ShelfLife(3, _fresh_lots(state)), UndatedReturns({1: {"milk": 1.0}}),
        ])


def test_shelf_life_engine_combines_with_user_processes():
    policy, demand, state = _setup()
    engine = ShelfLifeEngine(shelf_life_days=3)
    result = _run(engine, policy, demand, state, opening_lots=_fresh_lots(state),
                  processes=[InspectionLoss()])
    events = result.to_event_frame()
    assert events["process_outflow_units"].sum() > 0
    validate_event_frame(events)
    assert [entry["name"] for entry in result.run_settings["processes"]] == [
        "shelf_life", "inspection",
    ]
    assert result.run_settings["shelf_life"] == 3
    final = result.inventory.get_dataframe().set_index("unique_id")["on_hand"]
    balances = engine.ledger.balances().reindex(final.index).fillna(0.0)
    assert balances.to_numpy() == pytest.approx(final.to_numpy())


def test_hooks_run_in_the_documented_order_and_changes_reach_other_processes():
    policy, demand, state = _setup(lead=0, n_periods=4)
    recorder = Recorder()
    adjustment = ScheduledInventoryAdjustment(pd.DataFrame({
        "unique_id": ["milk"], "period": [2], "quantity_delta": [-1.0],
        "reason": ["count"], "source": ["test"],
    }))
    result = _run(SimulationEngine(), policy, demand, state, callbacks=[adjustment],
                  processes=[InspectionLoss(share=0.5, every=2), recorder])
    phases = [call[0] for call in recorder.calls if call[1] == 1]
    # Zero lead time: the decision's immediate receipt follows before demand.
    assert phases == ["before_demand", "on_receipt", "after_demand", "check"]
    assert recorder.calls[0] == ("reset", 0, None)
    period_two = [call[0] for call in recorder.calls if call[1] == 2]
    assert period_two == ["before_demand", "on_receipt", "after_demand", "check", "check"]
    assert ("callback", "ScheduledInventoryAdjustment", "milk", -1.0) in recorder.changes
    assert any(origin == "process" and source == "inspection.damaged"
               for origin, source, _, _ in recorder.changes)
    audit = result.to_callback_audit_frame()
    assert audit["lot_evidence"].tolist() == [""]


def test_processes_apply_in_list_order():
    class TakeAll(InventoryProcess):
        name = "take_all"
        flows = (Flow("removed", "outflow"),)

        def after_demand(self, context):
            return ProcessFlows({"removed": context.on_hand})

    policy, demand, state = _setup()
    result = _run(SimulationEngine(), policy, demand, state,
                  processes=[TakeAll(), InspectionLoss(every=1)])
    flows = result.to_process_flow_frame()
    # InspectionLoss runs second and sees no stock left, so it reports nothing.
    assert set(flows["process"]) == {"take_all"}


def test_processes_are_reset_for_every_run_and_comparison_branch():
    class Counter(InventoryProcess):
        name = "counter"

        def reset(self, context):
            self.periods = 0

        def after_demand(self, context):
            self.periods += 1

    policy, demand, state = _setup(n_periods=5)
    counter = Counter()
    _run(SimulationEngine(), policy, demand, state, processes=[counter])
    _run(SimulationEngine(), policy, demand, state, processes=[counter])
    assert counter.periods == 5
    comparison = SimulationEngine().run_comparison(
        [policy, copy.deepcopy(policy)], demand, state, 5, period_frequency="D",
        warmup_periods=0, scoring_periods=5, settlement_periods=0,
        order_during_settlement=False, demand_source_name="processes",
        random_seed=None, labels=["a", "b"], processes=[counter],
    )
    assert counter.periods == 5
    assert "processes" in comparison["a"].run_settings


# ---------------------------------------------------------------------------
# Fail-closed validation
# ---------------------------------------------------------------------------

OUTFLOW_F = (Flow("f", "outflow"),)


def _flows_process(result_factory, *, flows=OUTFLOW_F, phase="after_demand"):
    class Custom(InventoryProcess):
        name = "custom"

    Custom.flows = flows
    setattr(Custom, phase, lambda self, context: result_factory(context))
    return Custom()


@pytest.mark.parametrize("factory, error, message", [
    (lambda c: {"f": {"milk": 1.0}}, TypeError, "must return ProcessFlows or None"),
    (lambda c: ProcessFlows({"g": {"milk": 1.0}}), ValueError, "undeclared flows"),
    (lambda c: ProcessFlows({"f": {"cheese": 1.0}}), ValueError, "unknown SKU 'cheese'"),
    (lambda c: ProcessFlows({"f": pd.Series([1.0, 1.0], index=["milk", "milk"])}),
     ValueError, "lists SKU 'milk' more than once"),
    (lambda c: ProcessFlows({"f": {"milk": -1.0}}), ValueError, "finite numbers >= 0"),
    (lambda c: ProcessFlows({"f": {"milk": np.nan}}), ValueError, "finite numbers >= 0"),
    (lambda c: ProcessFlows({"f": {"milk": True}}), ValueError, "finite numbers >= 0"),
    (lambda c: ProcessFlows({"f": {"milk": 1e6}}), ValueError, "removes 1000000.0 units"),
    (lambda c: ProcessFlows({"f": {"milk": 1.0}}, received_dates={"f": c.date}),
     ValueError, "received_dates apply only to inflows"),
])
def test_invalid_flows_fail_closed(factory, error, message):
    policy, demand, state = _setup()
    with pytest.raises(error, match=message):
        _run(SimulationEngine(), policy, demand, state, processes=[_flows_process(factory)])


def test_identifiers_are_not_coerced():
    skus = [1, 2]
    matrix = np.ones((3, 2))
    policy = order_up_to(skus, [5.0, 5.0], lead=1, every=1, backorders=False)
    state = state_frame(skus, max_lead=1, backorders=False, on_hand=[3.0, 3.0])
    process = _flows_process(lambda c: ProcessFlows({"f": {"1": 1.0}}))
    with pytest.raises(ValueError, match="unknown SKU '1'"):
        _run(SimulationEngine(), policy, demand_frame(matrix, skus), state, processes=[process])
    ok = _flows_process(lambda c: ProcessFlows({"f": pd.Series({np.int64(1): 1.0})}))
    result = _run(SimulationEngine(), policy, demand_frame(matrix, skus), state, processes=[ok])
    assert result.to_event_frame()["process_outflow_units"].sum() == 3.0


def test_inflow_is_rejected_while_a_sku_has_backlog():
    policy, demand, state = _setup(backorders=True, on_hand=(0.0, 0.0), targets=(0.0, 0.0))
    returns = CustomerReturns({2: {"milk": 1.0}})
    with pytest.raises(ValueError, match="while it has backlog"):
        _run(SimulationEngine(), policy, demand, state, processes=[returns])


def test_future_inflow_lot_date_is_rejected():
    process = _flows_process(
        lambda c: ProcessFlows({"f": {"milk": 1.0}}, received_dates={"f": c.date + DAY}),
        flows=(Flow("f", "inflow"),), phase="before_demand",
    )
    policy, demand, state = _setup()
    with pytest.raises(ValueError, match="cannot be in the future"):
        _run(SimulationEngine(), policy, demand, state, processes=[process])


@pytest.mark.parametrize("processes, error, message", [
    ("shelf", TypeError, "list of InventoryProcess"),
    ([object()], TypeError, "only InventoryProcess objects"),
    ([InventoryProcess()], ValueError, "needs a non-empty name"),
    ([InspectionLoss(), InspectionLoss()], ValueError, "duplicate 'inspection'"),
])
def test_invalid_process_lists_fail_before_the_run(processes, error, message):
    policy, demand, state = _setup()
    with pytest.raises(error, match=message):
        _run(SimulationEngine(), policy, demand, state, processes=processes)


def test_flow_declarations_are_validated():
    with pytest.raises(ValueError, match="expiry flow must be an outflow"):
        Flow("x", "inflow", category="expiry")
    with pytest.raises(ValueError, match="direction"):
        Flow("x", "out")
    with pytest.raises(ValueError, match="name"):
        Flow(" x", "outflow")

    class Duplicate(InventoryProcess):
        name = "dup"
        flows = (Flow("a", "outflow"), Flow("a", "inflow"))

    class BadConfig(InventoryProcess):
        name = "bad_config"

        def get_config(self):
            return {"value": float("nan")}

    policy, demand, state = _setup()
    with pytest.raises(ValueError, match="duplicate flow names"):
        _run(SimulationEngine(), policy, demand, state, processes=[Duplicate()])
    with pytest.raises(ValueError):
        _run(SimulationEngine(), policy, demand, state, processes=[BadConfig()])


def test_processes_cannot_be_combined_with_a_hook_overriding_engine():
    class HookEngine(SimulationEngine):
        def _after_demand_transition(self, inventory, period):
            return inventory

    policy, demand, state = _setup()
    with pytest.raises(ValueError, match="overrides the private lifecycle hooks"):
        _run(HookEngine(), policy, demand, state, processes=[InspectionLoss()])
    # Without processes such an engine runs as before.
    _run(HookEngine(), policy, demand, state)


def test_shelf_life_process_validates_its_inputs():
    with pytest.raises(ValueError, match="shelf_life_days"):
        ShelfLife(0, pd.DataFrame())
    with pytest.raises(ValueError, match="opening_expiry_handling"):
        ShelfLife(3, pd.DataFrame(), opening_expiry_handling="drop")
    with pytest.raises(TypeError, match="opening_lots must be a pandas DataFrame"):
        ShelfLife(3, [])
    policy, demand, state = _setup()
    lots = _fresh_lots(state)
    lots.loc[0, "quantity"] += 1.0
    with pytest.raises(ValueError, match="exactly equal opening on_hand"):
        _run(SimulationEngine(), policy, demand, state, processes=[ShelfLife(3, lots)])


# ---------------------------------------------------------------------------
# Event validation
# ---------------------------------------------------------------------------

def test_validate_event_frame_checks_process_columns():
    policy, demand, state = _setup()
    events = _run(SimulationEngine(), policy, demand, state,
                  processes=[InspectionLoss()]).to_event_frame()
    validate_event_frame(events)
    with pytest.raises(ValueError, match="both process_inflow_units and"):
        validate_event_frame(events.drop(columns="process_inflow_units"))
    tampered = events.copy()
    tampered.loc[tampered["process_outflow_units"] > 0, "process_outflow_units"] += 1.0
    with pytest.raises(ValueError, match="physical inventory balance"):
        validate_event_frame(tampered)
    negative = events.copy()
    negative.loc[negative.index[0], "process_inflow_units"] = -1.0
    with pytest.raises(ValueError, match="process_inflow_units must contain finite values >= 0"):
        validate_event_frame(negative)
    # Dropping both columns hides the flows, so the balance no longer holds.
    with pytest.raises(ValueError, match="physical inventory balance"):
        validate_event_frame(events.drop(columns=["process_inflow_units", "process_outflow_units"]))
