"""Pre-release stress suite: independent oracles, ledger invariants, and science.

Run separately from the fast unit suite:

    env MPLCONFIGDIR=/tmp/stockcast-mpl PYTHONPATH=src \
        .venv/bin/python -m pytest -q tests/stress

The suite deliberately avoids the engine's pipeline arrays. Its oracle keeps a
delivery calendar keyed by zero-based demand period, FIFO lots as plain lists,
and re-implements each policy rule and quantity constraint from its documented
contract. Everything is compared against the public event ledger.
"""

from __future__ import annotations

import copy
import math
import random
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

import stockcast as sc
from stockcast.core import ShelfLifeEngine
from stockcast.evaluation import (
    InventoryEvaluator,
    fill_rate,
    holding_cost,
    total_cost,
    validate_event_frame,
    waste_cost,
)

ORIGIN = pd.Timestamp("2026-03-02")
DAY = pd.Timedelta(days=1)
TOL = 1e-9


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def demand_frame(matrix: np.ndarray, skus: list, origin: pd.Timestamp = ORIGIN) -> pd.DataFrame:
    """Demand in the engine's calendar: period p is dated origin + (p + 1)."""
    n_periods = matrix.shape[0]
    return pd.DataFrame({
        "unique_id": np.tile(skus, n_periods),
        "period": np.repeat(np.arange(n_periods), len(skus)),
        "date": np.repeat(pd.date_range(origin + DAY, periods=n_periods, freq="D"), len(skus)),
        "y": matrix.reshape(-1).astype(float),
    })


def opening_state(skus, *, max_lead, backorders, on_hand, backlog=None, pipeline=None,
                  origin=ORIGIN):
    state = sc.InventoryStateDataFrame(
        list(skus), max_lead_time=max_lead, allow_backorders=backorders,
    ).initialize_zero(start_date=origin)
    state.data["on_hand"] = np.asarray(on_hand, dtype=float)
    if backlog is not None:
        state.data["backorders"] = np.asarray(backlog, dtype=float)
    if pipeline is not None:
        state.data["in_transit"] = [np.asarray(row, dtype=float) for row in pipeline]
    return state


def fit_out(policy, skus, targets, *, horizon, origin=ORIGIN):
    return policy.fit(
        pd.DataFrame({"unique_id": skus, "S": targets, "end": origin + horizon * DAY}),
        forecast_origin=origin, forecast_frequency="D", target_column="S",
        target_end_date_column="end", protection_horizon=horizon,
        target_source="external_direct", target_probability=policy.service_level,
    )


def fit_reorder(policy, skus, s_values, S_values=None, *, origin=ORIGIN):
    """Periodic reorder-point policy: s covers the L + R protection window."""
    horizon = policy.lead_time + policy.schedule.every
    frame = pd.DataFrame({"unique_id": skus, "s": s_values, "s_end": origin + horizon * DAY})
    kwargs = {}
    if policy.policy_type == "sS":
        frame["S"] = S_values
        kwargs = dict(order_up_to_column="S")
    return policy.fit(
        frame, forecast_origin=origin, forecast_frequency="D", reorder_point_column="s",
        reorder_end_date_column="s_end", target_probability=policy.service_level,
        reorder_horizon=horizon, target_source="external_direct", **kwargs,
    )


class StandingOrderPolicy(sc.BasePolicy):
    """A user-written policy: fixed per-SKU quantities on an arbitrary schedule."""

    def __init__(self, lead_time, quantities, *, schedule, allow_backorders):
        super().__init__(lead_time, allow_backorders=allow_backorders, schedule=schedule)
        self.quantities = dict(quantities)

    def fit(self, *args, **kwargs):
        self.fitted_ = True
        return self

    def predict(self, inventory_state_df, *, current_period, **kwargs):
        state = inventory_state_df.get_dataframe()
        assert (state["latest_incoming_demand"] == 0).all(), "demand leaked into decision"
        return sc.OrderDecision(pd.DataFrame({
            "unique_id": state["unique_id"],
            "order_quantity": state["unique_id"].map(self.quantities).astype(float),
            "order_period": current_period,
            "expected_delivery_period": current_period + self.lead_time,
        }), lead_time=self.lead_time)


def run_engine(engine, policy, demand, state, windows, *, constraints=None, callbacks=None,
               opening_lots=None, policy_schedule=None, name="stress"):
    warmup, scoring, settlement, during = windows
    kwargs = dict(
        period_frequency="D", warmup_periods=warmup, scoring_periods=scoring,
        settlement_periods=settlement, order_during_settlement=during,
        demand_source_name=name, random_seed=None, order_constraints=constraints,
        callbacks=callbacks, policy_schedule=policy_schedule,
    )
    if opening_lots is not None:
        kwargs["opening_lots"] = opening_lots
    n_periods = warmup + scoring + settlement
    return engine.run(policy, demand, state, n_periods, **kwargs)


# ---------------------------------------------------------------------------
# Independent oracle
# ---------------------------------------------------------------------------

def oracle(*, skus, demand, lead, backorders, on_hand, backlog, pipeline, decide, rule,
           constraints=(), shelf_life=None, lots=None, origin=ORIGIN):
    """Scalar calendar re-implementation of the documented before-demand contract.

    ``pipeline[i][k]`` arrives before demand period ``k``. ``constraints`` is a
    sequence of ``(kind, value)`` applied in order, each in adjust mode.
    """
    n_periods = demand.shape[0]
    stock = [float(v) for v in on_hand]
    back = [float(v) for v in backlog]
    calendar = [
        {k: float(q) for k, q in enumerate(row) if q > 0} for row in pipeline
    ]
    # FIFO: oldest receipt first, whatever order the opening lots were listed in.
    lot_book = [sorted(map(list, sku_lots), key=lambda lot: lot[0]) for sku_lots in (lots or [[] for _ in skus])]
    rows = []
    for t in range(n_periods):
        date = origin + (t + 1) * DAY
        is_decision = decide(t)
        for i, sku in enumerate(skus):
            row = dict(unique_id=sku, demand_period=t, starting_on_hand=stock[i],
                       starting_backorders=back[i],
                       starting_on_order=sum(calendar[i].values()))
            expired = 0.0
            if shelf_life is not None:
                keep = []
                for lot_date, qty in lot_book[i]:
                    if (date - lot_date).days >= shelf_life:
                        expired += qty
                    else:
                        keep.append([lot_date, qty])
                lot_book[i] = keep
                stock[i] -= expired
            received = calendar[i].pop(t, 0.0)
            if shelf_life is not None and received > 0:
                lot_book[i].append([date, received])
            cleared = min(back[i], received) if backorders else 0.0
            back[i] -= cleared
            stock[i] += received - cleared
            raw = final = 0.0
            decision_ip = np.nan
            if is_decision:
                decision_ip = stock[i] + sum(calendar[i].values()) - back[i]
                raw = final = float(rule(i, decision_ip))
                for kind, value in constraints:
                    if kind == "moq" and 0 < final < value:
                        final = value
                    elif kind == "multiple" and final > 0:
                        ratio = final / value
                        if not math.isclose(ratio, round(ratio), abs_tol=1e-9):
                            final = math.ceil((final - 1e-12) / value) * value
                    elif kind == "max":
                        final = min(final, value)
                    elif kind == "space":
                        final = min(final, max(0.0, value - stock[i] - sum(calendar[i].values())))
                if lead == 0:
                    if shelf_life is not None and final > 0:
                        lot_book[i].append([date, final])
                    immediate = min(back[i], final) if backorders else 0.0
                    back[i] -= immediate
                    cleared += immediate
                    stock[i] += final - immediate
                    received += final
                elif final > 0:
                    calendar[i][t + lead] = calendar[i].get(t + lead, 0.0) + final
            d = float(demand[t, i])
            served = min(stock[i], d)
            stock[i] -= served
            shortage = d - served
            if backorders:
                back[i] += shortage
            if shelf_life is not None:
                need = served + cleared
                for lot in lot_book[i]:
                    used = min(lot[1], need)
                    lot[1] -= used
                    need -= used
                lot_book[i] = [lot for lot in lot_book[i] if lot[1] > 1e-12]
                assert need < 1e-9
            row.update(expired_units=expired, received_units=received, demand=d,
                       fulfilled_units=served, backorders_fulfilled=cleared,
                       shortage_units=shortage, ending_on_hand=stock[i],
                       backorders_end=back[i], on_order_end=sum(calendar[i].values()),
                       order_quantity=final, requested_order_quantity=raw,
                       decision_flag=is_decision, decision_inventory_position=decision_ip)
            rows.append(row)
    return pd.DataFrame(rows)


ORACLE_COLUMNS = [
    "starting_on_hand", "starting_backorders", "starting_on_order", "expired_units",
    "received_units", "demand", "fulfilled_units", "backorders_fulfilled",
    "shortage_units", "ending_on_hand", "backorders_end", "on_order_end",
    "order_quantity", "requested_order_quantity",
]


def assert_matches_oracle(events: pd.DataFrame, expected: pd.DataFrame) -> None:
    actual = events.sort_values(["demand_period", "unique_id"]).reset_index(drop=True)
    expected = expected.sort_values(["demand_period", "unique_id"]).reset_index(drop=True)
    assert len(actual) == len(expected)
    assert actual["unique_id"].tolist() == expected["unique_id"].tolist()
    assert actual["decision_flag"].tolist() == expected["decision_flag"].tolist()
    for column in ORACLE_COLUMNS:
        np.testing.assert_allclose(
            actual[column].to_numpy(float), expected[column].to_numpy(float),
            rtol=0, atol=1e-7, err_msg=column,
        )
    np.testing.assert_allclose(
        actual["decision_inventory_position"].to_numpy(float),
        expected["decision_inventory_position"].to_numpy(float),
        rtol=0, atol=1e-7, err_msg="decision_inventory_position",
    )


# ---------------------------------------------------------------------------
# Ledger invariants shared by every scenario
# ---------------------------------------------------------------------------

def assert_ledger_invariants(result, opening: sc.InventoryStateDataFrame, lead: int) -> pd.DataFrame:
    events = validate_event_frame(result.to_event_frame())
    events = events.sort_values(["unique_id", "demand_period"]).reset_index(drop=True)
    state = opening.get_dataframe().set_index("unique_id")
    n_periods = result.n_periods
    for sku, rows in events.groupby("unique_id", sort=False):
        rows = rows.reset_index(drop=True)
        assert rows["demand_period"].tolist() == list(range(n_periods))
        assert rows["period"].tolist() == [int(state.at[sku, "period"]) + p + 1 for p in range(n_periods)]
        # Continuity: each period opens exactly where the previous one closed.
        for start, end in [("starting_on_hand", "ending_on_hand"),
                           ("starting_backorders", "backorders_end"),
                           ("starting_on_order", "on_order_end")]:
            np.testing.assert_allclose(rows[start].iloc[1:].to_numpy(), rows[end].iloc[:-1].to_numpy(), atol=TOL)
        # Opening row starts from the caller's opening state (before expiry).
        assert rows.at[0, "starting_backorders"] == pytest.approx(state.at[sku, "backorders"])
        assert rows.at[0, "starting_on_order"] == pytest.approx(float(np.sum(state.at[sku, "in_transit"])))
        # Delivery trace: every positive-lead order arrives exactly L periods later.
        pipeline = np.asarray(state.at[sku, "in_transit"], dtype=float)
        orders = rows["order_quantity"].to_numpy()
        for t in range(n_periods):
            due = pipeline[t] if t < len(pipeline) else 0.0
            if lead == 0:
                due += orders[t]
            elif t - lead >= 0:
                due += orders[t - lead]
            assert rows.at[t, "received_units"] == pytest.approx(due, abs=1e-7), (sku, t)
        # Orders are placed only at decision opportunities.
        assert (rows.loc[~rows["decision_flag"], "order_quantity"] == 0).all()
        # Physical, backlog and pipeline conservation over the whole run.
        final = result.inventory.get_dataframe().set_index("unique_id")
        assert final.at[sku, "on_hand"] == pytest.approx(rows["ending_on_hand"].iloc[-1])
        assert final.at[sku, "backorders"] == pytest.approx(rows["backorders_end"].iloc[-1])
        assert float(np.sum(final.at[sku, "in_transit"])) == pytest.approx(rows["on_order_end"].iloc[-1])
    # No SKU can hold stock and backlog at once, before or after demand.
    assert not ((events["ending_on_hand"] > TOL) & (events["backorders_end"] > TOL)).any()
    return events


# ---------------------------------------------------------------------------
# 1. Randomized oracle sweep across every built-in policy family
# ---------------------------------------------------------------------------

def _random_case(seed: int):
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    n_skus = rng.randint(1, 3)
    skus = [f"sku_{seed}_{i}" for i in range(n_skus)]
    family = ["out", "out", "sQ", "sS", "RsS", "standing"][seed % 6]
    lead = rng.randint(0, 4)
    backorders = rng.random() < 0.5
    max_lead = lead + rng.randint(0, 2)
    warmup, scoring, settlement = rng.randint(0, 3), rng.randint(6, 10), rng.randint(0, 3)
    windows = (warmup, scoring, settlement, rng.random() < 0.5)
    n_periods = warmup + scoring + settlement
    shelf_life = rng.choice([None, None, rng.randint(2, 6)])
    # Demand: intermittent integers with occasional fractional spikes.
    demand = nrng.poisson(nrng.uniform(1, 9, n_skus), size=(n_periods, n_skus)).astype(float)
    demand *= nrng.random((n_periods, n_skus)) > 0.2
    demand += np.where(nrng.random((n_periods, n_skus)) < 0.1, 0.5, 0.0)

    on_hand = [float(rng.randint(0, 25)) for _ in skus]
    backlog = [0.0] * n_skus
    if backorders:
        backlog = [float(rng.randint(1, 6)) if on_hand[i] == 0 else 0.0 for i in range(n_skus)]
    pipeline = [[float(rng.choice([0, 0, rng.randint(1, 12)])) for _ in range(max_lead)] for _ in skus]
    lots = None
    if shelf_life is not None:
        lots = []
        for qty in on_hand:
            if qty == 0:
                lots.append([])
                continue
            age_a, age_b = rng.randint(0, shelf_life - 1), rng.randint(0, shelf_life - 1)
            first = math.floor(qty / 2)
            sku_lots = [[ORIGIN - age_a * DAY, float(first)]] if first else []
            sku_lots.append([ORIGIN - age_b * DAY, qty - first])
            lots.append(sku_lots)

    constraints = []
    if rng.random() < 0.5:
        pack = float(rng.choice([2, 3, 6]))
        constraints = [("moq", float(rng.randint(1, 5))), ("multiple", pack),
                       ("max", pack * rng.randint(3, 8))]
    elif rng.random() < 0.3:
        constraints = [("space", float(rng.randint(15, 40)))]

    if family == "out":
        review, start = rng.randint(1, 4), rng.randint(0, 2)
        schedule = sc.PeriodicSchedule(review, start=start)
        targets = [float(rng.randint(5, 40)) for _ in skus]
        policy = fit_out(sc.OrderUpToPolicy(lead, schedule=schedule, allow_backorders=backorders),
                         skus, targets, horizon=lead + review)
        rule = lambda i, ip: max(0.0, targets[i] - ip)
    elif family in {"sQ", "sS"}:
        schedule = sc.PeriodicSchedule(rng.choice([1, 1, 2, 3]), start=rng.randint(0, 1))
        s_values = [float(rng.randint(0, 15)) for _ in skus]
        service = rng.choice([0.9, None])  # quantile mode or planner mode
        if family == "sQ":
            q = float(rng.randint(3, 20))
            policy = fit_reorder(sc.ReorderPointPolicy(
                lead, schedule=schedule, policy_type="sQ", service_level=service, order_quantity=q,
                order_quantity_source="case_pack", allow_backorders=backorders), skus, s_values)
            rule = lambda i, ip: q if ip <= s_values[i] else 0.0
        else:
            S_values = [s + rng.randint(0, 20) for s in s_values]
            policy = fit_reorder(sc.ReorderPointPolicy(
                lead, schedule=schedule, policy_type="sS", service_level=service,
                allow_backorders=backorders), skus, s_values, S_values)
            rule = lambda i, ip: max(0.0, S_values[i] - ip) if ip <= s_values[i] else 0.0
    elif family == "RsS":
        periods = sorted(rng.sample(range(n_periods), k=max(1, n_periods // 3)))
        schedule = sc.ExplicitSchedule(tuple(periods))
        s_values = {sku: float(rng.randint(0, 15)) for sku in skus}
        S_values = {sku: s_values[sku] + rng.randint(0, 20) for sku in skus}
        policy = sc.PeriodicReviewPolicy(lead, schedule=schedule, allow_backorders=backorders).fit(
            pd.DataFrame({"unique_id": skus}),
            target_provider=sc.FixedPeriodicReviewTargets(reorder_point=s_values, order_up_to_level=S_values),
            information_origin=ORIGIN, information_frequency="D",
        )
        rule = lambda i, ip: max(0.0, S_values[skus[i]] - ip) if ip <= s_values[skus[i]] else 0.0
    else:
        periods = sorted(rng.sample(range(n_periods), k=max(1, n_periods // 2)))
        schedule = sc.ExplicitSchedule(tuple(periods))
        quantities = {sku: float(rng.randint(0, 9)) for sku in skus}
        policy = StandingOrderPolicy(lead, quantities, schedule=schedule, allow_backorders=backorders).fit()
        rule = lambda i, ip: quantities[skus[i]]

    decide = lambda t: schedule.should_decide(t) and (t < warmup + scoring or windows[3])
    return dict(
        skus=skus, lead=lead, backorders=backorders, max_lead=max_lead, windows=windows,
        demand=demand, on_hand=on_hand, backlog=backlog, pipeline=pipeline, lots=lots,
        shelf_life=shelf_life, constraints=constraints, policy=policy, rule=rule,
        decide=decide, family=family,
    )


def _constraint_objects(spec):
    mapping = {
        "moq": lambda v: sc.MinimumOrderQuantity(v, mode="adjust"),
        "multiple": lambda v: sc.OrderMultiple(v, mode="adjust"),
        "max": lambda v: sc.MaximumOrderQuantity(v, mode="adjust"),
        "space": lambda v: sc.ShelfSpaceLimit(v, mode="adjust"),
    }
    if not spec:
        return None
    return sc.OrderingConstraints([mapping[kind](value) for kind, value in spec])


@pytest.mark.parametrize("seed", range(36))
def test_randomized_engine_matches_independent_oracle(seed):
    case = _random_case(seed)
    skus = case["skus"]
    state = opening_state(skus, max_lead=case["max_lead"], backorders=case["backorders"],
                          on_hand=case["on_hand"], backlog=case["backlog"], pipeline=case["pipeline"])
    caller_state = copy.deepcopy(state.get_dataframe())
    caller_policy = copy.deepcopy(case["policy"])
    opening_lots = None
    engine = sc.SimulationEngine()
    if case["shelf_life"] is not None:
        engine = ShelfLifeEngine(case["shelf_life"])
        opening_lots = pd.DataFrame(
            [(sku, date, qty) for sku, sku_lots in zip(skus, case["lots"]) for date, qty in sku_lots],
            columns=["unique_id", "received_date", "quantity"],
        )
    result = run_engine(engine, case["policy"], demand_frame(case["demand"], skus), state,
                        case["windows"], constraints=_constraint_objects(case["constraints"]),
                        opening_lots=opening_lots)
    events = assert_ledger_invariants(result, state, case["lead"])
    expected = oracle(
        skus=skus, demand=case["demand"], lead=case["lead"], backorders=case["backorders"],
        on_hand=case["on_hand"], backlog=case["backlog"], pipeline=case["pipeline"],
        decide=case["decide"], rule=case["rule"], constraints=case["constraints"],
        shelf_life=case["shelf_life"], lots=case["lots"],
    )
    assert_matches_oracle(events, expected)
    # The caller's objects are inputs, never run state.
    pd.testing.assert_frame_equal(state.get_dataframe(), caller_state)
    assert case["policy"].__dict__.keys() == caller_policy.__dict__.keys()
    # Window slicing partitions the ledger.
    warmup, scoring, settlement, _ = case["windows"]
    sizes = [len(result.to_event_frame(w)) for w in ("warmup", "scoring", "settlement")]
    assert sizes == [warmup * len(skus), scoring * len(skus), settlement * len(skus)]


# ---------------------------------------------------------------------------
# 2. Causality: decisions never depend on current or future demand
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("lead,review,backorders", [(0, 1, False), (0, 2, True), (2, 3, False), (3, 1, True)])
def test_decisions_are_invariant_to_current_and_future_demand(lead, review, backorders):
    skus = ["a", "b"]
    rng = np.random.default_rng(lead * 10 + review)
    base = rng.poisson(6, size=(12, 2)).astype(float)
    policy = fit_out(sc.OrderUpToPolicy(lead, review, allow_backorders=backorders),
                     skus, [25.0, 14.0], horizon=lead + review)
    runs = {}
    for cut in (4, 7):
        shocked = base.copy()
        shocked[cut:] = rng.poisson(30, size=shocked[cut:].shape)
        runs[cut] = run_engine(
            sc.SimulationEngine(), policy, demand_frame(shocked, skus),
            opening_state(skus, max_lead=lead, backorders=backorders, on_hand=[5.0, 5.0]),
            (0, 12, 0, False)).to_event_frame()
    baseline = run_engine(sc.SimulationEngine(), policy, demand_frame(base, skus),
                          opening_state(skus, max_lead=lead, backorders=backorders, on_hand=[5.0, 5.0]),
                          (0, 12, 0, False)).to_event_frame()
    for cut, events in runs.items():
        # Up to and including the decision *at* the shock period, orders match.
        keep = baseline["demand_period"] <= cut
        np.testing.assert_array_equal(
            events.loc[keep, "order_quantity"].to_numpy(), baseline.loc[keep, "order_quantity"].to_numpy()
        )
        np.testing.assert_array_equal(
            events.loc[keep, "decision_inventory_position"].to_numpy(),
            baseline.loc[keep, "decision_inventory_position"].to_numpy(),
        )


def test_callable_demand_is_materialized_once_and_runs_are_reproducible():
    calls = []
    matrix = np.random.default_rng(3).poisson(4, size=(9, 1)).astype(float)
    frame = demand_frame(matrix, ["a"])

    def demand_source(period):
        calls.append(period)
        return frame[frame["period"] == period].copy()

    policy = fit_out(sc.OrderUpToPolicy(1, 2, allow_backorders=True), ["a"], [12.0], horizon=3)
    state = opening_state(["a"], max_lead=1, backorders=True, on_hand=[3.0])
    first = run_engine(sc.SimulationEngine(), policy, demand_source, state, (1, 6, 2, False))
    assert calls == list(range(9))
    second = run_engine(sc.SimulationEngine(), policy, frame, state, (1, 6, 2, False))
    pd.testing.assert_frame_equal(first.to_event_frame(), second.to_event_frame())
    assert first.run_manifest["demand_source"]["sha256"] == second.run_manifest["demand_source"]["sha256"]


# ---------------------------------------------------------------------------
# 3. Scientific identities and statistical calibration
# ---------------------------------------------------------------------------

def _poisson_ppf(probability: float, mean: float) -> int:
    k, pmf = 0, math.exp(-mean)
    cdf = pmf
    while cdf < probability:
        k += 1
        pmf *= mean / k
        cdf += pmf
    return k


def _poisson_cdf(k: int, mean: float) -> float:
    return sum(math.exp(-mean) * mean ** j / math.factorial(j) for j in range(k + 1))


def test_zero_lead_daily_order_up_to_is_a_repeated_newsvendor():
    """L=0, R=1 lost sales: every epoch starts at S, so shortage = (D - S)+ exactly."""
    n_skus, n_periods, mean, alpha = 250, 16, 6.0, 0.9
    skus = [f"n{i}" for i in range(n_skus)]
    target = float(_poisson_ppf(alpha, mean))
    demand = np.random.default_rng(11).poisson(mean, size=(n_periods, n_skus)).astype(float)
    policy = fit_out(sc.OrderUpToPolicy(0, 1, service_level=alpha, allow_backorders=False),
                     skus, [target] * n_skus, horizon=1)
    result = run_engine(sc.SimulationEngine(), policy, demand_frame(demand, skus),
                        opening_state(skus, max_lead=0, backorders=False, on_hand=[0.0] * n_skus),
                        (0, n_periods, 0, False))
    events = assert_ledger_invariants(result, opening_state(skus, max_lead=0, backorders=False,
                                                           on_hand=[0.0] * n_skus), 0)
    events = events.sort_values(["demand_period", "unique_id"])
    np.testing.assert_allclose(events["shortage_units"], np.maximum(0.0, events["demand"] - target))
    np.testing.assert_allclose(events["starting_on_hand"] + events["received_units"], target)
    # Achieved per-epoch in-stock probability matches the target's exact CDF.
    achieved = float((events["shortage_units"] == 0).mean())
    expected = _poisson_cdf(int(target), mean)
    assert expected >= alpha
    assert abs(achieved - expected) < 4 * math.sqrt(expected * (1 - expected) / len(events))


@pytest.mark.parametrize("lead,review", [(0, 1), (0, 3), (1, 1), (2, 3), (4, 2)])
def test_order_up_to_protection_window_is_exactly_lead_plus_review(lead, review):
    """Backorders: net stock after demand t+L+R-1 equals S - D[t .. t+L+R-1].

    This deterministic identity is what makes ``protection_horizon = L + R`` the
    correct target window. The statistical check then confirms that a normal
    quantile at alpha gives an end-of-cycle no-backlog frequency near alpha.
    """
    n_skus, mu, sigma, alpha = 200, 10.0, 3.0, 0.9
    horizon = lead + review
    target = mu * horizon + NormalDist().inv_cdf(alpha) * sigma * math.sqrt(horizon)
    n_cycles = 5
    n_periods = review * n_cycles + lead
    skus = [f"r{i}" for i in range(n_skus)]
    demand = np.maximum(0.0, np.random.default_rng(horizon).normal(mu, sigma, size=(n_periods, n_skus)))
    policy = fit_out(sc.OrderUpToPolicy(lead, review, service_level=alpha, allow_backorders=True),
                     skus, [target] * n_skus, horizon=horizon)
    state = opening_state(skus, max_lead=lead, backorders=True, on_hand=[target] * n_skus)
    result = run_engine(sc.SimulationEngine(), policy, demand_frame(demand, skus), state,
                        (0, n_periods, 0, False))
    events = assert_ledger_invariants(result, state, lead)
    net = (events.assign(net=events["ending_on_hand"] - events["backorders_end"])
           .pivot(index="unique_id", columns="demand_period", values="net").loc[skus].to_numpy())
    covered = []
    for t in range(0, n_periods - horizon + 1, review):
        end = t + horizon - 1
        expected = target - demand[t:end + 1].sum(axis=0)
        np.testing.assert_allclose(net[:, end], expected, atol=1e-7)
        covered.extend((net[:, end] >= 0).tolist())
    rate = float(np.mean(covered))
    se = math.sqrt(alpha * (1 - alpha) / len(covered))
    assert abs(rate - alpha) < 4 * se + 0.01, rate


@pytest.mark.parametrize("lead", [0, 1, 3])
def test_any_policy_satisfies_the_lead_time_net_stock_identity(lead):
    """For backorders, NI(end of t+L) = IP after ordering at t - D[t..t+L]."""
    skus = ["x", "y", "z"]
    demand = np.random.default_rng(lead).poisson(5, size=(14, 3)).astype(float)
    schedule = sc.ExplicitSchedule((0, 2, 3, 7, 8))
    policy = StandingOrderPolicy(lead, {"x": 4.0, "y": 0.0, "z": 11.0}, schedule=schedule,
                                 allow_backorders=True).fit()
    state = opening_state(skus, max_lead=lead, backorders=True, on_hand=[6.0, 0.0, 2.0],
                          backlog=[0.0, 3.0, 0.0])
    events = run_engine(sc.SimulationEngine(), policy, demand_frame(demand, skus), state,
                        (0, 14, 0, False)).to_event_frame().sort_values(["unique_id", "demand_period"])
    for sku, rows in events.groupby("unique_id"):
        rows = rows.set_index("demand_period")
        for t in schedule.periods:
            ip_after = rows.at[t, "decision_inventory_position"] + rows.at[t, "order_quantity"]
            net = rows.at[t + lead, "ending_on_hand"] - rows.at[t + lead, "backorders_end"]
            assert net == pytest.approx(ip_after - rows.loc[t:t + lead, "demand"].sum())


@pytest.mark.parametrize("lead", [0, 2])
def test_irregular_schedule_window_is_next_opportunity_plus_lead(lead):
    """NI(end of u+L-1) = IP after ordering at t - D[t .. u+L-1], u = next opportunity.

    This is the identity behind ``H = (u - t) + L``: nothing ordered after ``t``
    can arrive before demand ``u + L``.
    """
    skus = ["x", "y"]
    periods = (0, 1, 4, 5, 9, 13)
    schedule = sc.ExplicitSchedule(periods)
    demand = np.random.default_rng(7 + lead).poisson(4, size=(16, 2)).astype(float)
    policy = StandingOrderPolicy(lead, {"x": 6.0, "y": 13.0}, schedule=schedule,
                                 allow_backorders=True).fit()
    state = opening_state(skus, max_lead=lead, backorders=True, on_hand=[4.0, 9.0])
    events = run_engine(sc.SimulationEngine(), policy, demand_frame(demand, skus), state,
                        (0, 16, 0, False)).to_event_frame()
    for sku, rows in events.groupby("unique_id"):
        rows = rows.set_index("demand_period")
        for t, u in zip(periods, periods[1:]):
            end = u + lead - 1
            ip_after = rows.at[t, "decision_inventory_position"] + rows.at[t, "order_quantity"]
            net = rows.at[end, "ending_on_hand"] - rows.at[end, "backorders_end"]
            assert net == pytest.approx(ip_after - rows.loc[t:end, "demand"].sum())


def test_every_period_reorder_point_needs_lead_plus_one_window():
    """Backorders, Poisson demand: s sized for L+1 protects; s sized for L does not.

    The L+1 quantile is the justified basis under before-demand review; it is
    not an exact service guarantee (undershoot and Q also matter).
    """
    n_skus, n_periods, mean, alpha, q = 150, 60, 5.0, 0.95, 15.0
    skus = [f"p{i}" for i in range(n_skus)]
    demand = np.random.default_rng(0).poisson(mean, size=(n_periods, n_skus)).astype(float)
    from stockcast.evaluation import cycle_service_level

    def service(lead, window):
        s = float(_poisson_ppf(alpha, mean * window)) if window else 0.0
        policy = sc.ReorderPointPolicy(lead, review_period=1, policy_type="sQ", order_quantity=q,
                                       order_quantity_source="case", allow_backorders=True)
        fit_reorder(policy, skus, [s] * n_skus)  # planner mode: s supplied directly
        state = opening_state(skus, max_lead=lead, backorders=True, on_hand=[s + q] * n_skus)
        result = run_engine(sc.SimulationEngine(), policy, demand_frame(demand, skus), state,
                            (10, n_periods - 10, 0, False))
        return cycle_service_level(result.to_event_frame("scoring"), {"include_partial_cycles": False})

    for lead in (0, 2):
        protected = service(lead, lead + 1)
        assert protected >= alpha - 0.02, (lead, protected)
        assert service(lead, lead) < protected - 0.1


def test_single_season_newsvendor_is_profit_maximizing_at_the_critical_fractile():
    """A retailer buys once for a 3-day event; realized profit peaks at the fractile."""
    price, cost, salvage = 12.0, 5.0, 1.0
    fractile = sc.newsvendor_critical_fractile(selling_price=price, purchase_cost=cost,
                                               salvage_value=salvage)
    assert fractile == pytest.approx(7 / 11)
    n_skus, lead, decision, season = 400, 2, 1, 3
    mu, sigma = 20.0, 6.0
    season_mu, season_sigma = mu * season, sigma * math.sqrt(season)
    optimum = season_mu + NormalDist().inv_cdf(fractile) * season_sigma
    skus = [f"e{i}" for i in range(n_skus)]
    n_periods = decision + lead + season + 1
    demand = np.zeros((n_periods, n_skus))
    first = decision + lead
    demand[first:first + season] = np.maximum(0.0, np.random.default_rng(5).normal(mu, sigma, (season, n_skus)))
    origin = ORIGIN + decision * DAY

    def profit(quantity):
        policy = sc.SingleOrderPolicy(lead, selling_horizon=season, decision_period=decision,
                                      service_level=fractile, allow_backorders=False)
        policy.fit(pd.DataFrame({"unique_id": skus, "q": quantity,
                                 "end": origin + (lead + season) * DAY}),
                   forecast_origin=origin, forecast_frequency="D", target_column="q",
                   target_end_date_column="end", target_source="external_direct",
                   target_probability=fractile)
        state = opening_state(skus, max_lead=lead, backorders=False, on_hand=[0.0] * n_skus)
        result = run_engine(sc.SimulationEngine(), policy, demand_frame(demand, skus), state,
                            (0, n_periods, 0, False))
        events = assert_ledger_invariants(result, state, lead)
        ordered = events.groupby("unique_id")["order_quantity"].sum()
        assert np.allclose(ordered, quantity)
        assert events.loc[events["order_quantity"] > 0, "demand_period"].eq(decision).all()
        leftover = events.loc[events["demand_period"] == n_periods - 1, "ending_on_hand"].sum()
        revenue = price * events["fulfilled_units"].sum()
        return (revenue - cost * events["order_quantity"].sum() + salvage * leftover) / n_skus, events

    best, events = profit(optimum)
    season_demand = demand.sum(axis=0)
    in_stock = float((season_demand <= optimum).mean())
    assert abs(in_stock - fractile) < 4 * math.sqrt(fractile * (1 - fractile) / n_skus)
    for factor in (0.8, 0.9, 1.1, 1.25):
        assert profit(optimum * factor)[0] < best


# ---------------------------------------------------------------------------
# 4. A retailer's end-to-end weekly replenishment workflow
# ---------------------------------------------------------------------------

class PackedShelfSpace(sc.OrderingConstraint):
    """User extension: round up to case packs, then drop whole cases that do not fit."""

    name = "packed_shelf_space"

    def __init__(self, pack, space):
        self.pack, self.space = dict(pack), dict(space)

    def apply(self, order, context):
        frame = order.get_dataframe()
        state = context.inventory.get_dataframe().set_index(context.inventory.sku_column)
        requested = frame["order_quantity"].to_numpy(dtype=float)
        final = []
        for sku, quantity in zip(frame[order.sku_column], requested):
            pack = self.pack[sku]
            free = self.space[sku] - state.at[sku, "on_hand"] - float(np.sum(state.at[sku, "in_transit"]))
            cases = min(math.ceil(quantity / pack - 1e-9), math.floor(max(0.0, free) / pack + 1e-9))
            final.append(float(max(0, cases) * pack))
        frame["order_quantity"] = final
        changed = ~np.isclose(requested, final)
        audit = pd.DataFrame({
            "unique_id": frame[order.sku_column], "requested_order_quantity": requested,
            "constrained_order_quantity": final, "constraint_adjustment_units": np.array(final) - requested,
            "constraint_binding_flag": changed, "capacity_violation_flag": changed,
            "binding_constraints": [self.name if flag else "" for flag in changed],
        })
        return sc.ConstraintResult(sc.OrderDecision(frame, lead_time=order.lead_time), audit)


def test_retailer_weekly_rolling_forecast_workflow_with_perishables():
    """Daily sales, weekly orders, 2-day lead time, 8-day shelf life, case packs,
    shelf space, a promotion uplift, and a shrink write-off. Forecasts are rolled
    at every order date from observed history only."""
    rng = np.random.default_rng(2026)
    skus = [f"fresh_{i:02d}" for i in range(12)]
    level = rng.uniform(4, 15, len(skus))
    weekday = np.array([0.8, 0.9, 1.0, 1.0, 1.2, 1.4, 0.7])
    history_days, lead, review, shelf = 56, 2, 7, 8
    warmup, scoring, settlement = 7, 28, 7
    n_periods = warmup + scoring + settlement
    horizon = lead + review
    total_days = history_days + n_periods
    dates = pd.date_range(ORIGIN - (history_days - 1) * DAY, periods=total_days, freq="D")
    means = level[None, :] * weekday[dates.dayofweek.to_numpy()][:, None]
    sales = rng.poisson(means).astype(float)
    history, future = sales[:history_days], sales[history_days:]
    observed = pd.DataFrame(sales, index=dates, columns=skus)

    def snapshot(decision_period):
        origin = ORIGIN + decision_period * DAY
        window = observed.loc[:origin].iloc[-28:]  # strictly up to the information date
        assert window.index.max() == origin
        by_weekday = window.groupby(window.index.dayofweek).mean()
        spread = window.std(ddof=1)
        rows = []
        for fh in range(1, horizon + 1):
            date = origin + fh * DAY
            for sku in skus:
                rows.append((sku, fh, date, by_weekday.at[date.dayofweek, sku], spread[sku]))
        frame = pd.DataFrame(rows, columns=["unique_id", "fh", "date", "mean", "std"])
        return sc.OrderUpToPolicy(lead, review, service_level=0.95, allow_backorders=False).fit(
            frame, forecast_origin=origin, forecast_frequency="D", mean_column="mean",
            std_column="std", forecast_date_column="date", aggregation_method="independent_normal",
            protection_horizon=horizon, target_probability=0.95,
        )

    decision_periods = [p for p in range(0, warmup + scoring, review)]
    schedule = {p: snapshot(p) for p in decision_periods}
    policy = schedule[0]
    opening_stock = np.round(level * 4)
    state = opening_state(skus, max_lead=lead, backorders=False, on_hand=opening_stock)
    lots = pd.DataFrame({"unique_id": skus, "received_date": ORIGIN - DAY, "quantity": opening_stock})
    pack = {sku: float(rng.choice([4, 6, 12])) for sku in skus}
    space = {sku: float(level[i] * 14) for i, sku in enumerate(skus)}
    constraints = sc.OrderingConstraints([PackedShelfSpace(pack, space)])
    promo_period = int(state.get_dataframe()["period"].iloc[0]) + 1 + 14
    callbacks = [
        sc.ScheduledOrderMultiplier(pd.DataFrame({
            "unique_id": skus[:4], "period": promo_period, "multiplier": 1.5,
            "reason": "promotion_uplift", "source": "category_plan",
        })),
        sc.ScheduledInventoryAdjustment(pd.DataFrame({
            "unique_id": [skus[5]], "period": [promo_period + 2], "quantity_delta": [-1.0],
            "reason": ["shrink"], "source": ["store_audit"],
        })),
    ]
    engine = ShelfLifeEngine(shelf)
    result = engine.run(
        policy, demand_frame(future, skus), state, n_periods, period_frequency="D",
        warmup_periods=warmup, scoring_periods=scoring, settlement_periods=settlement,
        order_during_settlement=False, demand_source_name="retailer_fresh", random_seed=2026,
        opening_lots=lots, policy_schedule=schedule, order_constraints=constraints,
        callbacks=callbacks,
    )
    events = assert_ledger_invariants(result, state, lead)
    assert result.run_settings["policy_update_periods"] == decision_periods
    decisions = events.loc[events["decision_flag"], "demand_period"].unique().tolist()
    assert decisions == decision_periods
    # Every accepted order respects case packs; shelf space is never exceeded at decision time.
    for sku, rows in events.groupby("unique_id"):
        quantities = rows["order_quantity"].to_numpy()
        assert np.allclose(np.round(quantities / pack[sku]) * pack[sku], quantities)
        assert (rows["decision_inventory_position"].fillna(0) + rows["order_quantity"] <= space[sku] + 1e-9).all()
    audit = result.to_callback_audit_frame()
    assert set(audit["reason"]) <= {"promotion_uplift", "shrink"}
    assert (events["inventory_adjustment_units"] <= 0).all()
    assert events["inventory_adjustment_units"].sum() == pytest.approx(-audit.loc[audit["reason"] == "shrink", "quantity_delta"].abs().sum())
    # Evaluation reproduces hand-computed business metrics from the ledger.
    scoring_events = result.to_event_frame("scoring")
    context = {
        "cost_components": ["holding", "shortage", "purchase", "waste"],
        "holding_cost_per_unit_period": 0.02, "shortage_cost_per_unit": 3.0,
        "purchase_cost_per_unit": 1.0, "waste_cost_per_unit": 1.5,
    }
    evaluated = InventoryEvaluator().fit(result, window="scoring").evaluate(
        [fill_rate, holding_cost, waste_cost, total_cost], groupby=[], context=context,
    ).iloc[0]
    assert evaluated["fill_rate"] == pytest.approx(scoring_events["fulfilled_units"].sum() / scoring_events["demand"].sum())
    assert evaluated["holding_cost"] == pytest.approx(0.02 * scoring_events["ending_on_hand"].sum())
    assert evaluated["waste_cost"] == pytest.approx(1.5 * scoring_events["expired_units"].sum())
    assert evaluated["total_cost"] == pytest.approx(
        0.02 * scoring_events["ending_on_hand"].sum() + 3.0 * scoring_events["shortage_units"].sum()
        + scoring_events["order_quantity"].sum() + 1.5 * scoring_events["expired_units"].sum()
    )
    assert result.summary()["fill_rate"] == pytest.approx(evaluated["fill_rate"])
    assert 0.8 < evaluated["fill_rate"] <= 1.0

    # Same scenario, two policies, one shared demand path and identical opening lots.
    fixed = fit_out(sc.OrderUpToPolicy(lead, review, allow_backorders=False), skus,
                    list(np.round(level * horizon * 1.2)), horizon=horizon)
    comparison = engine.run_comparison(
        [fixed, copy.deepcopy(fixed)], demand_frame(future, skus), state, n_periods,
        period_frequency="D", warmup_periods=warmup, scoring_periods=scoring,
        settlement_periods=settlement, order_during_settlement=False,
        demand_source_name="retailer_fresh", random_seed=2026, opening_lots=lots,
        labels=["static_A", "static_B"], order_constraints=constraints,
    )
    a, b = comparison["static_A"].to_event_frame(), comparison["static_B"].to_event_frame()
    pd.testing.assert_frame_equal(a, b)
    assert a["expired_units"].sum() > 0
    assert comparison["static_A"].run_manifest["demand_source"]["materialized_once_for_comparison"]


# ---------------------------------------------------------------------------
# 5. Fail-closed inputs a production integration will eventually send
# ---------------------------------------------------------------------------

def _good():
    skus = ["a", "b"]
    demand = demand_frame(np.full((4, 2), 3.0), skus)
    state = opening_state(skus, max_lead=1, backorders=False, on_hand=[5.0, 5.0])
    policy = fit_out(sc.OrderUpToPolicy(1, 1, allow_backorders=False), skus, [9.0, 9.0], horizon=2)
    return policy, demand, state


def _run_good(policy, demand, state, **overrides):
    kwargs = dict(period_frequency="D", warmup_periods=0, scoring_periods=4, settlement_periods=0,
                  order_during_settlement=False, demand_source_name="bad", random_seed=None)
    kwargs.update(overrides)
    n_periods = kwargs.pop("n_periods", 4)
    return sc.SimulationEngine().run(policy, demand, state, n_periods, **kwargs)


def _mutate_demand(fn):
    def apply(policy, demand, state):
        return _run_good(policy, fn(demand.copy()), state)
    return apply


BAD_INPUTS = {
    "demand date off by one period": _mutate_demand(lambda d: d.assign(date=d["date"] - DAY)),
    "missing SKU-period row": _mutate_demand(lambda d: d.iloc[1:]),
    "duplicate SKU-period row": _mutate_demand(lambda d: pd.concat([d, d.iloc[:1]])),
    "NaN demand": _mutate_demand(lambda d: d.assign(y=d["y"].where(d.index != 3))),
    "negative demand": _mutate_demand(lambda d: d.assign(y=-d["y"])),
    "infinite demand": _mutate_demand(lambda d: d.assign(y=np.inf)),
    "unknown SKU": _mutate_demand(lambda d: d.assign(unique_id=d["unique_id"].replace("b", "c"))),
    "period gap": _mutate_demand(lambda d: d.assign(period=d["period"] * 2)),
    "frequency mismatch": lambda p, d, s: _run_good(p, d, s, period_frequency="W"),
    "window sum mismatch": lambda p, d, s: _run_good(p, d, s, scoring_periods=3),
    "unfitted policy": lambda p, d, s: _run_good(sc.OrderUpToPolicy(1, 1, allow_backorders=False), d, s),
    "pipeline shorter than lead": lambda p, d, s: _run_good(
        fit_out(sc.OrderUpToPolicy(2, 1, allow_backorders=False), ["a", "b"], [9.0, 9.0], horizon=3), d, s),
    "forecast origin after first decision": lambda p, d, s: _run_good(
        fit_out(sc.OrderUpToPolicy(1, 1, allow_backorders=False), ["a", "b"], [9.0, 9.0], horizon=2,
                origin=ORIGIN + DAY), d, s),
    "retired initial decision": lambda p, d, s: _run_good(p, d, s, initial_decision="before_first_demand"),
    "stock and backlog together": lambda p, d, s: _run_good(
        p, d, opening_state(["a", "b"], max_lead=1, backorders=True, on_hand=[5.0, 5.0], backlog=[1.0, 0.0])),
    "backlog in lost-sales mode": lambda p, d, s: _run_good(
        p, d, opening_state(["a", "b"], max_lead=1, backorders=False, on_hand=[0.0, 5.0], backlog=[1.0, 0.0])),
    "negative opening stock": lambda p, d, s: _run_good(
        p, d, opening_state(["a", "b"], max_lead=1, backorders=False, on_hand=[-1.0, 5.0])),
    "missing target for a SKU": lambda p, d, s: _run_good(
        fit_out(sc.OrderUpToPolicy(1, 1, allow_backorders=False), ["a"], [9.0], horizon=2), d, s),
}


@pytest.mark.parametrize("name", sorted(BAD_INPUTS))
def test_invalid_production_inputs_fail_closed(name):
    policy, demand, state = _good()
    before = copy.deepcopy(state.get_dataframe())
    with pytest.raises((ValueError, TypeError)):
        BAD_INPUTS[name](policy, demand, state)
    pd.testing.assert_frame_equal(state.get_dataframe(), before)


@pytest.mark.parametrize("bad", [
    dict(protection_horizon=3),  # L + R is 2
    dict(end_shift=1),
    dict(target_column="q90", target_probability=0.95),
])
def test_target_metadata_that_misstates_the_window_is_rejected(bad):
    policy = sc.OrderUpToPolicy(1, 1, service_level=0.95, allow_backorders=False)
    horizon = bad.get("protection_horizon", 2)
    column = bad.get("target_column", "S")
    frame = pd.DataFrame({"unique_id": ["a"], column: [9.0],
                          "end": ORIGIN + (2 + bad.get("end_shift", 0)) * DAY})
    with pytest.raises(ValueError):
        policy.fit(frame, forecast_origin=ORIGIN, forecast_frequency="D", target_column=column,
                   target_end_date_column="end", protection_horizon=horizon,
                   target_probability=bad.get("target_probability", 0.95),
                   target_source="external_direct")


def test_summing_marginal_quantiles_is_refused():
    policy = sc.OrderUpToPolicy(1, 1, service_level=0.9, allow_backorders=False)
    frame = pd.DataFrame({"unique_id": ["a", "a"], "fh": [1, 2],
                          "date": [ORIGIN + DAY, ORIGIN + 2 * DAY], "mean": [3.0, 3.0], "std": [1.0, 1.0]})
    with pytest.raises(ValueError, match="cannot be summed"):
        policy.fit(frame, forecast_origin=ORIGIN, forecast_frequency="D", mean_column="mean",
                   std_column="std", forecast_date_column="date", protection_horizon=2,
                   target_probability=0.9, aggregation_method="sum_marginal_quantiles")
