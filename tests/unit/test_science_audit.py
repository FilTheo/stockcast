"""Independent science audit (0.1.0 pre-release): pins verified behavior.

The reference model and identities below are written from the documented
contracts (knowledge 20/30/40/60, docs timing, accounting and metrics pages)
with numpy/pandas only; they do not reuse engine code. They add checks that
``validate_event_frame`` does not perform on its own: row-to-row continuity,
old-backlog priority, greedy fulfilment, the pre-demand decision position, and
reconciliation of ledger totals with the order and process-flow frames.
"""

import itertools
import warnings
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from stockcast.core import (
    DeliveryOutcome,
    Flow,
    InventoryAdjustmentResult,
    InventoryProcess,
    InventoryStateDataFrame,
    MaximumOrderQuantity,
    MinimumOrderQuantity,
    OrderingConstraints,
    OrderMultiple,
    ProcessFlows,
    ScheduledOrderMultiplier,
    ShelfLife,
    ShelfSpaceLimit,
    SimulationCallback,
    SimulationEngine,
    Supplier,
    SupplierShares,
    SupplyModel,
)
from stockcast.evaluation import (
    InventoryEvaluator,
    backlog_cost,
    backlog_unit_periods,
    cycle_service_level,
    demand_period_service_level,
    fill_rate,
    holding_cost,
    inventory_turns,
    ordering_cost,
    purchase_cost,
    salvage_credit,
    shortage_cost,
    sku_order_quantity_variance,
    total_cost,
    validate_event_frame,
)
from stockcast.policies import (
    OrderUpToPolicy,
    ReorderPointPolicy,
    SingleOrderPolicy,
    newsvendor_critical_fractile,
)
from stockcast.core import OrderLines
from stockcast.utils import place_order_lines, update_inventory_with_orders

ORIGIN = pd.Timestamp("2026-03-02")
DAY = pd.Timedelta(days=1)


def demand_frame(matrix, skus):
    matrix = np.asarray(matrix, dtype=float)
    n_periods = matrix.shape[0]
    return pd.DataFrame({
        "unique_id": np.tile(skus, n_periods),
        "period": np.repeat(np.arange(n_periods), len(skus)),
        "date": np.repeat(pd.date_range(ORIGIN + DAY, periods=n_periods, freq="D"), len(skus)),
        "y": matrix.reshape(-1),
    })


def opening(skus, *, max_lead, backorders, on_hand, backlog=None, pipeline=None):
    state = InventoryStateDataFrame(
        list(skus), max_lead_time=max_lead, allow_backorders=backorders,
    ).initialize_zero(opening_date=ORIGIN)
    state.data["on_hand"] = np.asarray(on_hand, dtype=float)
    if backlog is not None:
        state.data["backorders"] = np.asarray(backlog, dtype=float)
    if pipeline is not None:
        state.data["in_transit"] = [np.asarray(row, dtype=float) for row in pipeline]
    return state


def run(policy, demand, state, n_periods, *, warmup=0, settlement=0, during=False, **kwargs):
    return SimulationEngine().run(
        policy, demand, state, n_periods, freq="D", warmup_periods=warmup,
        scoring_periods=n_periods - warmup - settlement, settlement_periods=settlement,
        order_during_settlement=during, demand_source_name="audit", random_seed=None, **kwargs,
    )


def order_up_to(lead, review, targets, backorders, skus):
    horizon = lead + review
    return OrderUpToPolicy(lead, review, freq="D", allow_backorders=backorders, date_column="end").fit(
        pd.DataFrame({"unique_id": list(skus), "S": targets, "end": ORIGIN + horizon * DAY}),
        forecast_origin=ORIGIN, target_column="S",
        protection_horizon=horizon,
    )


# ---------------------------------------------------------------------------
# Textbook reference model: receive (old backlog first) -> decide -> demand.
# ---------------------------------------------------------------------------

def reference(*, demand, lead, backorders, on_hand, backlog, pipeline, decide, rule):
    demand = np.asarray(demand, dtype=float)
    rows = []
    for sku in range(demand.shape[1]):
        stock, owed = float(on_hand[sku]), float(backlog[sku])
        calendar = {slot: float(quantity) for slot, quantity in enumerate(pipeline[sku])}
        for t in range(demand.shape[0]):
            start = (stock, owed, sum(calendar.values()))
            received = calendar.pop(t, 0.0)
            order, position = 0.0, np.nan
            if decide(t):
                position = stock + received + sum(calendar.values()) - owed
                order = float(rule(sku, position))
                if lead == 0:
                    received += order
                else:
                    calendar[t + lead] = calendar.get(t + lead, 0.0) + order
            served_backlog = min(owed, received) if backorders else 0.0
            available = stock + received - served_backlog
            owed -= served_backlog
            fulfilled = min(demand[t, sku], available)
            shortage = demand[t, sku] - fulfilled
            stock = available - fulfilled
            if backorders:
                owed += shortage
            rows.append({
                "sku": sku, "starting_on_hand": start[0], "starting_backorders": start[1],
                "starting_on_order": start[2], "received_units": received,
                "backorders_fulfilled": served_backlog, "fulfilled_units": fulfilled,
                "shortage_units": shortage, "lost_sales_units": 0.0 if backorders else shortage,
                "ending_on_hand": stock, "backorders_end": owed,
                "on_order_end": sum(calendar.values()), "order_quantity": order,
                "decision_inventory_position": position,
            })
    return pd.DataFrame(rows)


def assert_matches_reference(events, expected, skus):
    events = events.assign(sku=events["unique_id"].map({s: i for i, s in enumerate(skus)}))
    events = events.sort_values(["sku", "demand_period"]).reset_index(drop=True)
    for column in expected.columns.drop(["sku", "decision_inventory_position"]):
        np.testing.assert_allclose(events[column], expected[column], atol=1e-9, err_msg=column)
    decided = expected["decision_inventory_position"].notna()
    np.testing.assert_allclose(
        events.loc[decided, "decision_inventory_position"],
        expected.loc[decided, "decision_inventory_position"], atol=1e-9,
    )


def assert_ledger_identities(result, opening_state):
    """Identities that must hold on any run (expiry is the only pre-demand outflow)."""
    events = result.to_event_frame()
    validate_event_frame(events)
    ev = events.sort_values(["unique_id", "period"])
    by_sku = ev.groupby("unique_id", sort=False)
    for start, end in [("starting_on_hand", "ending_on_hand"),
                       ("starting_backorders", "backorders_end"),
                       ("starting_on_order", "on_order_end")]:
        previous = by_sku[end].shift(1)
        known = previous.notna()
        np.testing.assert_allclose(ev.loc[known, start], previous[known], atol=1e-9, err_msg=start)
    state = opening_state.get_dataframe().set_index("unique_id")
    first = by_sku.head(1).set_index("unique_id")
    np.testing.assert_allclose(first["starting_on_hand"], state.loc[first.index, "on_hand"])
    np.testing.assert_allclose(first["starting_backorders"], state.loc[first.index, "backorders"])
    np.testing.assert_allclose(
        first["starting_on_order"], state.loc[first.index, "in_transit"].map(np.sum), atol=1e-9)
    backorder_rows = ev["allow_backorders"].astype(bool)
    priority = np.minimum(ev["starting_backorders"], ev["received_units"])
    np.testing.assert_allclose(
        ev.loc[backorder_rows, "backorders_fulfilled"], priority[backorder_rows], atol=1e-9)
    assert (ev.loc[~backorder_rows, "backorders_fulfilled"] == 0).all()
    available = (ev["starting_on_hand"] + ev["received_units"]
                 - ev["backorders_fulfilled"] - ev["expired_units"])
    np.testing.assert_allclose(
        ev["fulfilled_units"], np.minimum(ev["demand"], available.clip(lower=0)), atol=1e-6)
    assert not ((ev["ending_on_hand"] > 1e-9) & (ev["backorders_end"] > 1e-9)).any()
    # With one delivery per order (no supply model) every receipt starts a cycle.
    if "supply" not in result.run_settings:
        np.testing.assert_array_equal(ev["order_arrival_flag"], ev["received_units"] > 0)
    shortfall = ev["supplier_shortfall_units"] if "supplier_shortfall_units" in ev else 0.0
    pre_demand_position = (ev["starting_on_hand"] + ev["starting_on_order"]
                           - ev["starting_backorders"] - shortfall - ev["expired_units"])
    decided = ev["decision_flag"].astype(bool)
    np.testing.assert_allclose(
        ev.loc[decided, "decision_inventory_position"], pre_demand_position[decided], atol=1e-6)
    last = by_sku.tail(1).set_index("unique_id")
    totals = by_sku[["order_quantity", "received_units"]].sum()
    lost_in_supply = by_sku["supplier_shortfall_units"].sum() if "supplier_shortfall_units" in ev else 0.0
    np.testing.assert_allclose(
        first["starting_on_order"] + totals["order_quantity"] - totals["received_units"] - lost_in_supply,
        last.loc[first.index, "on_order_end"], atol=1e-6)
    orders = result.to_order_frame()
    if len(orders):
        quantity = "received_quantity" if "received_quantity" in orders else "delivery_quantity"
        received = orders[orders["status"].isin(["received", "disrupted"])]
        received = received.groupby(["unique_id", "due_period"])[quantity].sum()
        ledger = ev.set_index(["unique_id", "period"])["received_units"]
        joined = pd.concat([received.rename("orders"), ledger.rename("ledger")], axis=1).fillna(0.0)
        np.testing.assert_allclose(joined["orders"], joined["ledger"], atol=1e-6)
        still_open = orders[orders["status"] == "open"].groupby("unique_id")["delivery_quantity"].sum()
        np.testing.assert_allclose(
            still_open.reindex(last.index).fillna(0.0), last["on_order_end"], atol=1e-6)
    flows = result.to_process_flow_frame()
    if len(flows):
        index = ev.set_index(["unique_id", "period"]).index
        expired = flows[flows["category"] == "expiry"].groupby(["unique_id", "period"])["quantity"].sum()
        np.testing.assert_allclose(
            expired.reindex(index).fillna(0.0), ev["expired_units"], atol=1e-6)
        if "process_inflow_units" in ev:
            general = flows[flows["category"] == "general"]
            for direction in ("inflow", "outflow"):
                total = general[general["direction"] == direction].groupby(
                    ["unique_id", "period"])["quantity"].sum()
                np.testing.assert_allclose(
                    total.reindex(index).fillna(0.0), ev[f"process_{direction}_units"], atol=1e-6)
    return events


# ---------------------------------------------------------------------------
# 1 + 3. Timing and policy rules against the reference model
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("family", ["out", "sQ", "sS"])
@pytest.mark.parametrize("backorders", [True, False])
@pytest.mark.parametrize("lead,review", [(0, 1), (0, 3), (1, 1), (2, 2), (3, 1)])
def test_policies_match_textbook_reference(family, backorders, lead, review):
    skus = ["a", "b"]
    rng = np.random.default_rng(100 * lead + 10 * review + backorders)
    demand = rng.poisson([4, 9], size=(12, 2)).astype(float) * (rng.random((12, 2)) > 0.25)
    max_lead = max(lead, 1) + 1
    on_hand = [0.0, 12.0] if backorders else [3.0, 12.0]
    backlog = [5.0, 0.0] if backorders else [0.0, 0.0]
    pipeline = [[2.0] + [0.0] * (max_lead - 1), [0.0] * (max_lead - 1) + [7.0]]
    horizon = lead + review
    if family == "out":
        targets = [20.0, 45.0]
        policy = order_up_to(lead, review, targets, backorders, skus)
        rule = lambda sku, ip: max(0.0, targets[sku] - ip)  # noqa: E731
    else:
        s, S = [6.0, 15.0], [18.0, 40.0]
        frame = pd.DataFrame({"unique_id": skus, "s": s, "S": S, "end": ORIGIN + horizon * DAY})
        if family == "sQ":
            policy = ReorderPointPolicy(lead, review, freq="D", policy_type="sQ",
                                        order_quantity=11.0, allow_backorders=backorders,
                                        date_column="end")
            rule = lambda sku, ip: 11.0 if ip <= s[sku] else 0.0  # noqa: E731
            extra = {}
        else:
            policy = ReorderPointPolicy(lead, review, freq="D", policy_type="sS",
                                        allow_backorders=backorders, date_column="end")
            rule = lambda sku, ip: max(0.0, S[sku] - ip) if ip <= s[sku] else 0.0  # noqa: E731
            extra = {"order_up_to_column": "S"}
        policy.fit(frame, forecast_origin=ORIGIN, reorder_point_column="s",
                   reorder_horizon=horizon, **extra)
    state = opening(skus, max_lead=max_lead, backorders=backorders, on_hand=on_hand,
                    backlog=backlog, pipeline=pipeline)
    result = run(policy, demand_frame(demand, skus), state, 12)
    events = assert_ledger_identities(result, state)
    expected = reference(demand=demand, lead=lead, backorders=backorders, on_hand=on_hand,
                         backlog=backlog, pipeline=pipeline, decide=lambda t: t % review == 0,
                         rule=rule)
    assert_matches_reference(events, expected, skus)
    if lead == 0:
        ordered = events[events["order_quantity"] > 0]
        assert (ordered["received_units"] >= ordered["order_quantity"]).all()


@pytest.mark.parametrize("lead,review", [(0, 1), (1, 2), (3, 5)])
def test_periodic_reorder_horizon_must_be_lead_plus_review(lead, review):
    def fit(horizon):
        return ReorderPointPolicy(
            lead, review, freq="D", policy_type="sQ", order_quantity=5.0,
            allow_backorders=True,
            date_column="end",
        ).fit(pd.DataFrame({"unique_id": ["a"], "s": [3.0], "end": ORIGIN + horizon * DAY}),
              forecast_origin=ORIGIN, reorder_point_column="s",
              reorder_horizon=horizon)

    fit(lead + review)
    for wrong in {lead, lead + review - 1, lead + review + 1} - {0}:
        with pytest.raises(ValueError):
            fit(wrong)


def test_independent_normal_target_and_critical_fractile():
    forecasts = pd.DataFrame({
        "unique_id": "a", "fh": range(1, 6), "date": [ORIGIN + h * DAY for h in range(1, 6)],
        "mu": [4, 5, 6, 5, 4.0], "sd": [1, 2, 1, 2, 1.0],
    })
    policy = OrderUpToPolicy(2, 3, freq="D", service_level=0.9, allow_backorders=True, date_column="date").fit(
        forecasts, forecast_origin=ORIGIN, mean_column="mu",
        std_column="sd",
        target_probability=0.9, protection_horizon=5,
    )
    expected = 24.0 + NormalDist().inv_cdf(0.9) * np.sqrt(11.0)
    assert policy.get_target_levels()["target_level"].iloc[0] == pytest.approx(expected)
    assert newsvendor_critical_fractile(
        selling_price=10, purchase_cost=4, salvage_value=2) == pytest.approx(0.75)
    # A negative salvage value is a disposal cost: (10 - 4) / (10 + 1).
    assert newsvendor_critical_fractile(
        selling_price=10, purchase_cost=4, salvage_value=-1) == pytest.approx(6 / 11)
    for p, c, v in [(4, 4, 1), (10, 2, 3), (10, 4, 4), (10, 4, float("-inf"))]:
        with pytest.raises(ValueError):
            newsvendor_critical_fractile(selling_price=p, purchase_cost=c, salvage_value=v)


@pytest.mark.parametrize("lead", [0, 2])
def test_single_order_buys_target_minus_stock_for_the_season(lead):
    policy = SingleOrderPolicy(lead, freq="D", selling_horizon=3, service_level=0.75,
                               allow_backorders=False, date_column="end").fit(
        pd.DataFrame({"unique_id": ["a"], "t": [20.0], "end": ORIGIN + (lead + 3) * DAY}),
        forecast_origin=ORIGIN, target_column="t",
        target_probability=0.75,
    )
    state = opening(["a"], max_lead=lead, backorders=False, on_hand=[4.0])
    demand = np.array([[0.0]] * lead + [[8.0], [9.0], [7.0]])
    events = assert_ledger_identities(run(policy, demand_frame(demand, ["a"]), state, lead + 3), state)
    assert events["order_quantity"].sum() == 16.0
    assert events["lost_sales_units"].sum() == 4.0


# ---------------------------------------------------------------------------
# 2. Random multi-SKU runs with every extension
# ---------------------------------------------------------------------------

class _RandomOutcome(DeliveryOutcome):
    def resolve(self, due, context):
        draw = context.rng.random(len(due))
        quantity = due["quantity"].to_numpy()
        out = due.copy()
        out["received_quantity"] = np.where(
            draw < 0.5, quantity, np.where(draw < 0.7, np.floor(0.6 * quantity), 0.0))
        out["delayed_quantity"] = np.where(draw >= 0.85, quantity, 0.0)
        out["delay_periods"] = np.where(draw >= 0.93, 2, 1)
        return out


class _ScrapAndReturns(InventoryProcess):
    name = "scrap_and_returns"
    flows = (Flow("scrap", "outflow"), Flow("returns", "inflow"))

    def __init__(self, dated):
        self.dated = dated

    def after_demand(self, context):
        returns = ((context.backorders <= 0) & (context.demand > 3)).astype(float)
        return ProcessFlows(
            {"scrap": np.floor(0.1 * context.on_hand), "returns": returns},
            received_dates={"returns": context.date} if self.dated else None,
        )


class _Shrink(SimulationCallback):
    def on_after_demand(self, context):
        stocked = context.inventory[context.inventory["on_hand"] >= 5]
        if stocked.empty:
            return None
        return InventoryAdjustmentResult(pd.DataFrame({
            "unique_id": stocked["unique_id"], "quantity_delta": -1.0,
            "reason": "shrink", "source": "audit",
        }))


def _extension_case(seed):
    rng = np.random.default_rng(seed)
    skus = [f"s{seed}_{i}" for i in range(int(rng.integers(1, 4)))]
    backorders = bool(rng.random() < 0.5)
    lead, review, n_periods = int(rng.integers(1, 4)), int(rng.integers(1, 4)), int(rng.integers(10, 16))
    demand = rng.poisson(rng.uniform(1, 10, len(skus)), size=(n_periods, len(skus))).astype(float)
    demand *= rng.random(demand.shape) > 0.3
    demand += np.where(rng.random(demand.shape) < 0.1, 0.25, 0.0)
    max_lead = lead + 6
    on_hand = [float(rng.integers(0, 20)) for _ in skus]
    backlog = [float(rng.integers(1, 5)) if backorders and stock == 0 else 0.0 for stock in on_hand]
    pipeline = [[float(rng.choice([0, 0, rng.integers(1, 9)])) for _ in range(max_lead)]
                for _ in skus]
    policy = order_up_to(lead, review, [float(rng.integers(10, 50)) for _ in skus], backorders, skus)
    kwargs = {}
    if rng.random() < 0.7:
        outcome = _RandomOutcome() if rng.random() < 0.6 else None
        kwargs["supply"] = SupplyModel(
            [Supplier("near", lead, delivery=outcome),
             Supplier("far", {lead: 0.5, lead + 1: 0.3, lead + 2: 0.2},
                      partial_deliveries=[(0, 0.6), (1, 0.4)], delivery=outcome)],
            allocation=SupplierShares({"near": 0.35, "far": 0.65}), random_seed=seed,
        )
    processes = []
    shelf = rng.random() < 0.5
    if shelf:
        life = int(rng.integers(2, 6))
        lots = pd.DataFrame(
            [(sku, ORIGIN - int(rng.integers(0, life)) * DAY, stock)
             for sku, stock in zip(skus, on_hand) if stock > 0],
            columns=["unique_id", "received_date", "quantity"])
        processes.append(ShelfLife(life, lots))
    if rng.random() < 0.5:
        processes.append(_ScrapAndReturns(dated=shelf))
    if processes:
        kwargs["processes"] = processes
    if rng.random() < 0.5:
        kwargs["order_constraints"] = OrderingConstraints([
            MinimumOrderQuantity(3.0, mode="adjust"), OrderMultiple(2.0, mode="adjust"),
            MaximumOrderQuantity(30.0, mode="adjust")])
    elif rng.random() < 0.5:
        kwargs["order_constraints"] = OrderingConstraints([ShelfSpaceLimit(35.0, mode="adjust")])
    if rng.random() < 0.5:
        schedule = pd.DataFrame({"unique_id": skus[0], "period": [2, 5], "multiplier": [1.5, 0.0],
                                 "reason": "promo", "source": "audit"})
        kwargs["callbacks"] = [ScheduledOrderMultiplier(schedule), _Shrink()]
    state = opening(skus, max_lead=max_lead, backorders=backorders, on_hand=on_hand,
                    backlog=backlog, pipeline=pipeline)
    windows = dict(warmup=int(rng.integers(0, 3)), settlement=int(rng.integers(0, 3)),
                   during=bool(seed % 2))
    return policy, demand_frame(demand, skus), state, n_periods, windows, kwargs


@pytest.mark.parametrize("seed", [2, 3, 5, 8, 12, 21, 23, 29, 31, 35, 39, 44])
def test_random_extension_runs_keep_every_identity(seed):
    policy, demand, state, n_periods, windows, kwargs = _extension_case(seed)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # off-policy lead-time warning
        result = run(policy, demand, state, n_periods, **windows, **kwargs)
    events = assert_ledger_identities(result, state)
    assert sum(len(result.to_event_frame(w)) for w in ("warmup", "scoring", "settlement")) == len(events)


# ---------------------------------------------------------------------------
# 4. Metrics and costs: a hand-computed ledger
# ---------------------------------------------------------------------------

def test_hand_computed_ledger_metrics_and_costs():
    # L=1, R=1, S=10, backorders, opening 4, demand 6, 3, 8, 0, 5.
    policy = order_up_to(1, 1, [10.0], True, ["x"])
    state = opening(["x"], max_lead=1, backorders=True, on_hand=[4.0])
    result = run(policy, demand_frame([[6], [3], [8], [0], [5]], ["x"]), state, 5)
    events = result.to_event_frame()
    expected = {
        "order_quantity": [6, 6, 3, 8, 0], "received_units": [0, 6, 6, 3, 8],
        "backorders_fulfilled": [0, 2, 0, 1, 0], "fulfilled_units": [4, 3, 7, 0, 5],
        "shortage_units": [2, 0, 1, 0, 0], "ending_on_hand": [0, 1, 0, 2, 5],
        "backorders_end": [2, 0, 1, 0, 0], "on_order_end": [6, 6, 3, 8, 0],
    }
    for column, values in expected.items():
        assert events[column].tolist() == [float(v) for v in values], column
    context = {
        "include_partial_cycles": True, "periods_per_year": 365,
        "holding_cost_per_unit_period": 0.5, "shortage_cost_per_unit": 3.0,
        "backlog_cost_per_unit_period": 1.0, "order_cost_per_sku_line": 2.0,
        "order_cost_per_unit": 0.1, "purchase_cost_per_unit": 1.0,
        "on_hand_salvage_per_unit": 0.2, "pipeline_salvage_per_unit": 0.3,
        "cost_components": ["holding", "shortage", "backlog", "ordering", "purchase", "salvage"],
    }
    values = InventoryEvaluator().fit(result, window="scoring").evaluate(
        [fill_rate, demand_period_service_level, cycle_service_level, holding_cost,
         shortage_cost, backlog_cost, ordering_cost, purchase_cost, salvage_credit, total_cost,
         backlog_unit_periods, inventory_turns, sku_order_quantity_variance],
        groupby=[], context=context,
    ).iloc[0]
    ordering = 2.0 * 4 + 0.1 * 23
    assert values["fill_rate"] == pytest.approx(19 / 22)
    assert values["demand_period_service_level"] == pytest.approx(2 / 4)
    assert values["cycle_service_level"] == pytest.approx(3 / 5)
    assert values["holding_cost"] == pytest.approx(4.0)
    assert values["shortage_cost"] == pytest.approx(9.0)
    assert values["backlog_cost"] == pytest.approx(3.0)
    assert values["ordering_cost"] == pytest.approx(ordering)
    assert values["purchase_cost"] == pytest.approx(23.0)
    assert values["salvage_credit"] == pytest.approx(1.0)
    assert values["total_cost"] == pytest.approx(4 + 9 + 3 + ordering + 23 - 1)
    assert values["backlog_unit_periods"] == pytest.approx(3.0)
    # Throughput: 19 served on time + 3 backorders served later.
    assert values["inventory_turns"] == pytest.approx((22 / 5 * 365) / (8 / 5))
    assert values["sku_order_quantity_variance"] == pytest.approx(np.var([6, 6, 3, 8]))
    without_partial = InventoryEvaluator().fit(result, window="scoring").evaluate(
        [cycle_service_level], groupby=[], context={"include_partial_cycles": False}).iloc[0, 0]
    assert without_partial == pytest.approx(2 / 3)


# ---------------------------------------------------------------------------
# 5. Reproducibility
# ---------------------------------------------------------------------------

def test_same_seeds_give_identical_ledgers_with_random_supply():
    frames = []
    for _ in range(2):
        policy, demand, state, n_periods, windows, kwargs = _extension_case(31)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            frames.append(run(policy, demand, state, n_periods, **windows, **kwargs))
    pd.testing.assert_frame_equal(frames[0].to_event_frame(), frames[1].to_event_frame())
    pd.testing.assert_frame_equal(frames[0].to_order_frame(), frames[1].to_order_frame())
    assert frames[0].run_manifest["run_settings"]["supply"]["random_seed"] == 31


def test_comparison_branches_share_one_materialized_demand_path():
    demand = demand_frame(np.random.default_rng(11).poisson(5, size=(15, 2)), ["a", "b"])
    calls = []

    def source(period):
        calls.append(period)
        return demand[demand["period"] == period].copy()

    state = opening(["a", "b"], max_lead=1, backorders=False, on_hand=[5.0, 5.0])
    comparison = SimulationEngine().run_comparison(
        [order_up_to(1, 2, [10.0, 10.0], False, ["a", "b"]),
         order_up_to(1, 2, [25.0, 25.0], False, ["a", "b"])],
        source, state, 15, freq="D", warmup_periods=0, scoring_periods=15,
        settlement_periods=0, order_during_settlement=False, demand_source_name="cmp",
        random_seed=11, labels=["low", "high"],
    )
    low, high = comparison["low"], comparison["high"]
    assert calls == list(range(15))
    np.testing.assert_array_equal(low.to_event_frame()["demand"], high.to_event_frame()["demand"])
    assert (low.run_manifest["demand_source"]["sha256"]
            == high.run_manifest["demand_source"]["sha256"])
    assert low.run_manifest["demand_source"]["random_seed"] == 11


# ---------------------------------------------------------------------------
# 6. Edge cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,lead,review,target,demand,backorders,on_hand", [
    ("zero demand", 2, 3, [10.0], np.zeros((8, 1)), True, [0.0]),
    ("zero lead daily", 0, 1, [7.0], [[5], [9], [0], [7]], False, [0.0]),
    ("single period", 1, 1, [5.0], [[3]], True, [1.0]),
    ("single period zero lead", 0, 1, [5.0], [[8]], True, [1.0]),
    ("intermittent", 1, 2, [4.0], [[0], [0], [9], [0], [0], [0], [1], [0], [0], [12]], True, [0.0]),
    ("very large", 1, 1, [3e12, 5e9],
     np.random.default_rng(1).uniform(0, 2e12, (10, 2)).round() * [[1, 1e-3]], True, [0.0, 0.0]),
    ("very small", 1, 2, [3e-7], np.random.default_rng(2).uniform(0, 2e-7, (10, 1)), False, [1e-7]),
])
def test_edge_cases_match_reference(label, lead, review, target, demand, backorders, on_hand):
    demand = np.asarray(demand, dtype=float)
    skus = [f"s{i}" for i in range(demand.shape[1])]
    state = opening(skus, max_lead=lead, backorders=backorders, on_hand=on_hand)
    result = run(order_up_to(lead, review, target, backorders, skus),
                 demand_frame(demand, skus), state, demand.shape[0])
    events = assert_ledger_identities(result, state)
    expected = reference(
        demand=demand, lead=lead, backorders=backorders, on_hand=on_hand,
        backlog=[0.0] * len(skus), pipeline=[[0.0] * lead] * len(skus),
        decide=lambda t: t % review == 0, rule=lambda sku, ip: max(0.0, target[sku] - ip))
    assert_matches_reference(events, expected, skus)


@pytest.mark.parametrize("constraint", [
    MaximumOrderQuantity(5.0, mode="adjust"), ShelfSpaceLimit(8.0, mode="adjust"),
])
def test_capacity_binding_every_period_keeps_identities(constraint):
    state = opening(["a"], max_lead=1, backorders=True, on_hand=[0.0])
    result = run(order_up_to(1, 1, [100.0], True, ["a"]), demand_frame(np.full((10, 1), 20.0), ["a"]),
                 state, 10, order_constraints=OrderingConstraints([constraint]))
    events = assert_ledger_identities(result, state)
    assert events["capacity_violation_flag"].all()
    if isinstance(constraint, MaximumOrderQuantity):
        assert (events["order_quantity"] == 5.0).all()
    else:
        assert (events["ending_on_hand"] + events["on_order_end"] <= 8.0 + 1e-9).all()


def test_zero_lead_orders_never_enter_pipeline():
    for lead, review in itertools.product([0], [1, 2]):
        state = opening(["a"], max_lead=0, backorders=True, on_hand=[0.0])
        events = run(order_up_to(lead, review, [6.0], True, ["a"]),
                     demand_frame([[4], [8], [1], [0]], ["a"]), state, 4).to_event_frame()
        assert (events["on_order_end"] == 0).all()
        np.testing.assert_array_equal(events["received_units"], events["order_quantity"])


# ---------------------------------------------------------------------------
# Pre-release fixes: order arrivals, cycle service, validator, modes, labels
# ---------------------------------------------------------------------------

def _supply_run(supplier_list, *, lead, review, target, demand, allocation=None, n_periods=14):
    state = opening(["a"], max_lead=6, backorders=False, on_hand=[6.0])
    supply = SupplyModel(supplier_list, allocation=allocation, random_seed=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        result = run(order_up_to(lead, review, [target], False, ["a"]),
                     demand_frame(np.full((n_periods, 1), demand), ["a"]), state, n_periods,
                     supply=supply)
    return result, assert_ledger_identities(result, state)


def _flagged(events):
    return set(events.loc[events["order_arrival_flag"], "demand_period"])


def _orders(events):
    return list(events.loc[events["order_quantity"] > 0, "demand_period"])


def test_partial_deliveries_start_one_cycle_per_order():
    result, events = _supply_run(
        [Supplier("s", 2, partial_deliveries=[(0, 0.5), (1, 0.5)])],
        lead=2, review=3, target=20.0, demand=4.0,
    )
    arrivals = {t + 2 for t in _orders(events) if t + 2 < 14}
    assert _flagged(events) == arrivals
    second_parts = {t + 3 for t in _orders(events) if t + 3 < 14}
    assert (events.set_index("demand_period").loc[sorted(second_parts), "received_units"] > 0).all()
    # Cycles from order arrivals, counted independently of the flag column.
    starts = events["demand_period"].isin(arrivals).cumsum()
    by_cycle = (events["shortage_units"] > 0).groupby(starts).any()
    expected = float((~by_cycle).mean())
    value = cycle_service_level(events, {"include_partial_cycles": True})
    assert value == pytest.approx(expected)
    assert len(by_cycle) == len(arrivals) + 1


def test_supplier_lines_of_one_decision_are_one_order():
    result, events = _supply_run(
        [Supplier("near", 1), Supplier("far", 3)], lead=1, review=4, target=20.0, demand=3.0,
        allocation=SupplierShares({"near": 0.5, "far": 0.5}),
    )
    assert _flagged(events) == {t + 1 for t in _orders(events) if t + 1 < 14}


class _LateOnce(DeliveryOutcome):
    """Every placed delivery arrives one period late; nothing is lost."""

    def resolve(self, due, context):
        out = due.copy()
        late = (due["source"] == "placed").to_numpy()
        out["received_quantity"] = np.where(late, 0.0, due["quantity"])
        out["delayed_quantity"] = np.where(late, due["quantity"], 0.0)
        out["delay_periods"] = 1
        return out


class _Cancelled(DeliveryOutcome):
    def resolve(self, due, context):
        return due.assign(received_quantity=0.0)


def test_delayed_and_cancelled_orders_arrive_when_stock_arrives():
    _, late = _supply_run([Supplier("s", 2, delivery=_LateOnce())],
                          lead=2, review=3, target=20.0, demand=4.0)
    assert _flagged(late) == {t + 3 for t in _orders(late) if t + 3 < 14}
    _, cancelled = _supply_run([Supplier("s", 2, delivery=_Cancelled())],
                               lead=2, review=3, target=20.0, demand=4.0)
    assert not cancelled["order_arrival_flag"].any()
    assert cancelled["supplier_shortfall_units"].sum() > 0


def test_arrival_flag_marks_opening_pipeline_and_zero_lead_receipts():
    state = opening(["a"], max_lead=2, backorders=True, on_hand=[0.0], pipeline=[[3.0, 4.0]])
    events = run(order_up_to(0, 2, [5.0], True, ["a"]),
                 demand_frame(np.full((6, 1), 2.0), ["a"]), state, 6).to_event_frame()
    np.testing.assert_array_equal(events["order_arrival_flag"], events["received_units"] > 0)
    assert events["order_arrival_flag"].iloc[:2].all()


def _ledger(backorders=False):
    state = opening(["a"], max_lead=1, backorders=backorders, on_hand=[5.0])
    return run(order_up_to(1, 1, [8.0], backorders, ["a"]),
               demand_frame([[3], [4], [6], [2]], ["a"]), state, 4, warmup=1).to_event_frame()


def test_validator_rejects_broken_chains_idle_stock_and_stock_with_backlog():
    events = _ledger()
    validate_event_frame(events)
    broken = events.copy()
    calm = events.index[(events["shortage_units"] == 0) & (events.index > 0)][0]
    for column in ("starting_on_hand", "ending_on_hand", "inventory_position_end"):
        broken.loc[calm, column] += 10.0
    with pytest.raises(ValueError, match="starting_on_hand continuity"):
        validate_event_frame(broken)

    idle = events.copy()
    idle.loc[1, ["fulfilled_units", "ending_on_hand", "inventory_position_end"]] += [-1.0, 1.0, 1.0]
    idle.loc[1, ["shortage_units", "lost_sales_units"]] += 1.0
    idle.loc[1, "stockout_flag"] = True
    with pytest.raises(ValueError, match="shortage while stock remained"):
        validate_event_frame(idle)

    owed = _ledger(backorders=True)
    owed.loc[0, ["starting_backorders", "backorders_end"]] += 2.0
    owed.loc[0, "inventory_position_end"] -= 2.0
    owed.loc[0, "backorder_flag"] = True
    with pytest.raises(ValueError, match="positive ending_on_hand and backorders_end"):
        validate_event_frame(owed)


def test_validator_accepts_window_slices_and_combined_policies():
    events = _ledger()
    validate_event_frame(events[events["run_window"] == "scoring"])
    validate_event_frame(events.iloc[[0, 2, 3]])            # non-consecutive rows are not chained
    other = events.assign(policy="another policy", ending_on_hand=events["ending_on_hand"])
    validate_event_frame(pd.concat([events, other], ignore_index=True))


def test_validator_checks_the_arrival_flag():
    events = _ledger()
    no_receipt = events.index[events["received_units"] == 0][0]
    tampered = events.copy()
    tampered.loc[no_receipt, "order_arrival_flag"] = True
    with pytest.raises(ValueError, match="order_arrival_flag may be set only"):
        validate_event_frame(tampered)
    with pytest.raises(ValueError, match="order_arrival_flag must contain boolean"):
        validate_event_frame(events.assign(order_arrival_flag=1))
    validate_event_frame(events.drop(columns="order_arrival_flag"))


def test_shortage_mode_conflict_is_rejected_and_unset_mode_follows_policy():
    demand = demand_frame(np.ones((3, 1)), ["a"])
    for state_mode, policy_mode in [(True, False), (False, True)]:
        state = opening(["a"], max_lead=1, backorders=state_mode, on_hand=[1.0])
        with pytest.raises(ValueError, match="conflicts with policy allow_backorders"):
            run(order_up_to(1, 1, [4.0], policy_mode, ["a"]), demand, state, 3)
        policy = order_up_to(1, 1, [4.0], policy_mode, ["a"])
        advanced = state.advance_period(freq="D", is_review_period=True)
        decision = policy.predict(advanced, current_period=int(advanced.data["period"].iloc[0]))
        with pytest.raises(ValueError, match="conflicts with inventory_state"):
            update_inventory_with_orders(advanced, decision, policy=policy)
    unset = InventoryStateDataFrame(["a"], max_lead_time=1).initialize_zero(opening_date=ORIGIN)
    result = run(order_up_to(1, 1, [4.0], True, ["a"]), demand, unset, 3)
    assert result.to_event_frame()["allow_backorders"].all()


@pytest.mark.parametrize("column,probability,accepted", [
    ("q975", 0.975, True), ("q975", 0.95, False), ("q995", 0.995, True),
    ("q025", 0.025, True), ("q025", 0.25, True), ("q025", 0.5, False), ("q005", 0.005, True),
    ("q95", 0.95, True), ("q5", 0.05, True), ("q5", 0.5, False), ("q97.5", 0.975, True),
    ("q100", 0.975, False),
])
def test_quantile_labels_are_read_as_probabilities(column, probability, accepted):
    def fit():
        OrderUpToPolicy(1, 1, freq="D", service_level=probability, allow_backorders=True, date_column="end").fit(
            pd.DataFrame({"unique_id": ["a"], column: [10.0], "end": ORIGIN + 2 * DAY}),
            forecast_origin=ORIGIN, target_column=column,
            protection_horizon=2,
            target_probability=probability)

    if accepted:
        fit()
    else:
        with pytest.raises(ValueError, match="denotes probability"):
            fit()


def test_quantile_label_mismatch_names_only_probability_readings():
    with pytest.raises(ValueError, match=r"'q975' denotes probability 0\.975, not 0\.95"):
        OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=True, date_column="end").fit(
            pd.DataFrame({"unique_id": ["a"], "q975": [10.0], "end": ORIGIN + 2 * DAY}),
            forecast_origin=ORIGIN, target_column="q975",
            protection_horizon=2,
            target_probability=0.95)


class _DatedReturns(InventoryProcess):
    name = "returns"
    flows = (Flow("returned", "inflow"),)

    def after_demand(self, context):
        dates = pd.Series(context.date, index=context.on_hand.index)
        return ProcessFlows({"returned": (context.backorders <= 0).astype(float)},
                            received_dates={"returned": dates})


def test_process_received_dates_accept_a_series():
    lots = pd.DataFrame({"unique_id": ["a"], "received_date": [ORIGIN], "quantity": [5.0]})
    state = opening(["a"], max_lead=1, backorders=False, on_hand=[5.0])
    result = run(order_up_to(1, 1, [8.0], False, ["a"]), demand_frame(np.full((5, 1), 2.0), ["a"]),
                 state, 5, processes=[ShelfLife(4, lots), _DatedReturns()])
    events = assert_ledger_identities(result, state)
    assert events["process_inflow_units"].sum() == 5.0
    with pytest.raises(ValueError, match="more than once"):
        ProcessFlows({"returned": {"a": 1.0}},
                     received_dates={"returned": pd.Series([ORIGIN, ORIGIN], index=["a", "a"])})




def test_order_lines_reject_a_conflicting_policy_mode():
    state = opening(["a"], max_lead=2, backorders=False, on_hand=[1.0])
    advanced = state.advance_period(freq="D", is_review_period=True)
    period = int(advanced.data["period"].iloc[0])
    lines = OrderLines(pd.DataFrame({"unique_id": ["a"], "supplier_id": ["s"],
                                     "order_quantity": [3.0], "order_period": [period],
                                     "due_period": [period + 1]}))
    with pytest.raises(ValueError, match="conflicts with inventory_state"):
        place_order_lines(advanced, lines, policy=order_up_to(1, 1, [4.0], True, ["a"]))
    placed = place_order_lines(advanced, lines, policy=order_up_to(1, 1, [4.0], False, ["a"]))
    assert placed.allow_backorders is False


def test_comparison_checks_shortage_modes_before_any_branch_runs():
    demand = demand_frame(np.full((6, 1), 3.0), ["a"])
    policies = [order_up_to(1, 1, [4.0], False, ["a"]), order_up_to(1, 1, [4.0], True, ["a"])]
    calls = []

    def source(period):
        calls.append(period)
        return demand[demand["period"] == period].copy()

    settings = dict(freq="D", warmup_periods=0, scoring_periods=6,
                    settlement_periods=0, order_during_settlement=False,
                    demand_source_name="modes", random_seed=None, labels=["lost", "owed"])
    explicit = opening(["a"], max_lead=1, backorders=False, on_hand=[0.0])
    with pytest.raises(ValueError, match="policy 'owed' allow_backorders=True"):
        SimulationEngine().run_comparison(policies, source, explicit, 6, **settings)
    assert calls == []                      # nothing ran, not even the demand source
    unset = InventoryStateDataFrame(["a"], max_lead_time=1).initialize_zero(opening_date=ORIGIN)
    comparison = SimulationEngine().run_comparison(policies, demand, unset, 6, **settings)
    lost, owed = comparison["lost"].to_event_frame(), comparison["owed"].to_event_frame()
    assert not lost["allow_backorders"].any() and owed["allow_backorders"].all()
    assert lost["lost_sales_units"].sum() > 0 and owed["backorder_increment"].sum() > 0
