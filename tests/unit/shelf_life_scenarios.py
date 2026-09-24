"""Deterministic shelf-life run scenarios shared by the migration tests.

Each scenario is a ``(label, kwargs_factory)`` pair. ``kwargs_factory()``
returns fresh ``engine.run`` keyword arguments (policy, demand, state, opening
lots, options) so an engine can be run on exactly the same inputs twice.
"""

from __future__ import annotations

import copy
import random

import numpy as np
import pandas as pd

from stockcast.core import (
    InventoryStateDataFrame,
    OrderingConstraints,
    OrderMultiple,
    PeriodicSchedule,
    ScheduledInventoryAdjustment,
    ScheduledOrderMultiplier,
    Supplier,
    SupplyModel,
)
from stockcast.policies import OrderUpToPolicy

ORIGIN = pd.Timestamp("2026-03-02")
DAY = pd.Timedelta(days=1)


def demand_frame(matrix: np.ndarray, skus: list) -> pd.DataFrame:
    n_periods = matrix.shape[0]
    return pd.DataFrame({
        "unique_id": np.tile(np.asarray(skus, dtype=object), n_periods),
        "period": np.repeat(np.arange(n_periods), len(skus)),
        "date": np.repeat(pd.date_range(ORIGIN + DAY, periods=n_periods, freq="D"), len(skus)),
        "y": matrix.reshape(-1).astype(float),
    })


def state_frame(skus, *, max_lead, backorders, on_hand, pipeline=None):
    state = InventoryStateDataFrame(
        list(skus), max_lead_time=max_lead, allow_backorders=backorders,
    ).initialize_zero(start_date=ORIGIN)
    state.data["on_hand"] = np.asarray(on_hand, dtype=float)
    if pipeline is not None:
        state.data["in_transit"] = [np.asarray(row, dtype=float) for row in pipeline]
    return state


def order_up_to(skus, targets, *, lead, every, backorders):
    horizon = lead + every
    policy = OrderUpToPolicy(
        lead_time=lead, schedule=PeriodicSchedule(every), service_level=0.9,
        allow_backorders=backorders,
    )
    return policy.fit(
        pd.DataFrame({"unique_id": skus, "S": targets, "end": ORIGIN + horizon * DAY}),
        forecast_origin=ORIGIN, forecast_frequency="D", target_column="S",
        target_end_date_column="end", protection_horizon=horizon,
        target_source="external_direct", target_probability=0.9,
    )


def split_lots(skus, on_hand, ages, rng):
    """Split each SKU's opening stock over the given lot ages (days)."""
    rows = []
    for sku, quantity in zip(skus, on_hand):
        if quantity <= 0:
            continue
        chosen = rng.sample(ages, k=min(len(ages), rng.choice([1, 2, 3])))
        weights = [rng.randint(1, 4) for _ in chosen]
        parts = [quantity * weight / sum(weights) for weight in weights]
        parts[-1] = quantity - sum(parts[:-1])
        for age, part in zip(chosen, parts):
            if part > 0:
                rows.append({
                    "unique_id": sku,
                    "received_date": ORIGIN - age * DAY,
                    "quantity": float(part),
                })
    return pd.DataFrame(rows, columns=["unique_id", "received_date", "quantity"])


def random_scenario(seed: int):
    """Return ``(shelf_life_days, run_kwargs)`` for one seeded scenario."""
    rng = random.Random(1000 + seed)
    n_skus = rng.choice([1, 2, 4])
    skus = [f"S{seed}-{index}" for index in range(n_skus)]
    backorders = rng.random() < 0.5
    lead = rng.choice([0, 1, 2, 3])
    max_lead = lead + rng.choice([0, 1])
    every = rng.choice([1, 2, 3])
    shelf = rng.choice([1, 2, 3, 5])
    n_periods = rng.choice([10, 16])
    warmup = rng.choice([0, 2])
    settlement = rng.choice([0, 2])
    scale = rng.choice([1.0, 1.0, 1e7])
    matrix = np.random.default_rng(seed).poisson(rng.choice([2, 6]), size=(n_periods, n_skus))
    matrix = matrix * scale
    on_hand = [float(rng.randint(0, 20)) * scale for _ in skus]
    pipeline = (
        [[float(rng.randint(0, 5)) * scale for _ in range(max_lead)] for _ in skus]
        if max_lead else None
    )
    targets = [float(rng.randint(5, 30)) * scale + rng.choice([0.0, 0.5]) for _ in skus]
    handling = rng.choice(["reject", "reject", "expire_before_initial_decision"])
    ages = list(range(shelf))
    if handling == "expire_before_initial_decision":
        ages = ages + [shelf, shelf + 2]
    lots = split_lots(skus, on_hand, ages, rng)
    options = {
        "opening_lots": lots,
        "opening_expiry_handling": handling,
        "warmup": warmup,
        "settlement": settlement,
        "during": rng.random() < 0.5,
    }
    if rng.random() < 0.3:
        options["order_constraints"] = OrderingConstraints([
            OrderMultiple(float(rng.choice([2, 4])) * scale, mode="adjust"),
        ])
    callbacks = []
    if rng.random() < 0.4:
        callbacks.append(ScheduledOrderMultiplier(pd.DataFrame({
            "unique_id": skus[:1], "period": 2, "multiplier": 1.5,
            "reason": "promotion", "source": "scenario",
        })))
    if rng.random() < 0.5:
        # A FIFO removal (always feasible at zero) and a dated addition that
        # may fail closed under backlog: both outcomes are compared.
        callbacks.append(ScheduledInventoryAdjustment(pd.DataFrame({
            "unique_id": [skus[-1], skus[0]],
            "period": [3, 5],
            "quantity_delta": [-0.0, 2.0 * scale],
            "received_date": [pd.NaT, ORIGIN + 5 * DAY],
            "reason": ["count", "found"],
            "source": ["scenario", "scenario"],
        })))
    if callbacks:
        options["callbacks"] = callbacks
    if lead >= 1 and rng.random() < 0.25:
        options["supply"] = SupplyModel([
            Supplier("main", lead_time=lead),
        ])
    state = state_frame(skus, max_lead=max_lead, backorders=backorders,
                        on_hand=on_hand, pipeline=pipeline)
    policy = order_up_to(skus, targets, lead=lead, every=every, backorders=backorders)
    return shelf, dict(policy=policy, demand=demand_frame(matrix, skus), state=state, **options)


N_RANDOM_SCENARIOS = 40
# A covering subset for the routine suite: zero lead time, backorders, gram
# scale, constraints, callbacks (including fail-closed ones), supply and
# opening write-offs. The full range was verified once against golden outputs
# captured before the migration.
MIGRATION_SEEDS = (0, 2, 3, 4, 6, 8, 9, 12, 19, 20, 22, 25, 27, 32, 38)


def run_kwargs(scenario: dict) -> dict:
    """Engine ``run`` keyword arguments for a scenario (deep-copied)."""
    scenario = copy.deepcopy(scenario)
    demand = scenario.pop("demand")
    n_periods = int(demand["period"].max()) + 1
    warmup = scenario.pop("warmup")
    settlement = scenario.pop("settlement")
    during = scenario.pop("during")
    return dict(
        policy=scenario.pop("policy"),
        demand_source=demand,
        inventory=scenario.pop("state"),
        n_periods=n_periods,
        period_frequency="D",
        warmup_periods=warmup,
        scoring_periods=n_periods - warmup - settlement,
        settlement_periods=settlement,
        order_during_settlement=during,
        demand_source_name="shelf_life_migration",
        random_seed=None,
        **scenario,
    )


def outputs(result) -> dict:
    """Every durable run output, with volatile manifest fields removed."""
    manifest = copy.deepcopy(result.run_manifest)
    manifest.pop("run_id")
    manifest.pop("created_at_utc")
    return {
        "events": result.to_event_frame(),
        "history": result.history,
        "final": result.inventory.get_dataframe(),
        "final_history": result.inventory.get_history(),
        "callback_audit": result.to_callback_audit_frame(),
        "order_frame": result.to_order_frame(),
        "run_settings": copy.deepcopy(result.run_settings),
        "manifest": manifest,
        "flags": (result.inventory.has_stockout, result.inventory.has_backorder),
    }


def plain(frame: pd.DataFrame) -> pd.DataFrame:
    """Make object columns holding pipeline arrays comparable by value."""
    frame = frame.copy()
    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].map(
                lambda value: ("array", value.dtype.str, tuple(value.tolist()))
                if isinstance(value, np.ndarray) else value
            )
    return frame


def assert_same_outputs(actual: dict, expected: dict, label: str, *, ignore_manifest_keys=()) -> None:
    for name in ("events", "history", "final", "final_history", "callback_audit", "order_frame"):
        left, right = actual[name], expected[name]
        where = f"{label}:{name}"
        assert list(left.columns) == list(right.columns), where
        assert list(map(str, left.dtypes)) == list(map(str, right.dtypes)), where
        pd.testing.assert_index_equal(left.index, right.index, exact=True, obj=where)
        pd.testing.assert_frame_equal(plain(left), plain(right), check_exact=True, obj=where)
    settings = {k: v for k, v in actual["run_settings"].items() if k not in ignore_manifest_keys}
    expected_settings = {
        k: v for k, v in expected["run_settings"].items() if k not in ignore_manifest_keys
    }
    assert settings == expected_settings, label
    manifest, expected_manifest = copy.deepcopy(actual["manifest"]), copy.deepcopy(expected["manifest"])
    for key in ignore_manifest_keys:
        manifest["run_settings"].pop(key, None)
        expected_manifest["run_settings"].pop(key, None)
    # Package source provenance (commit/dirty) legitimately changes between
    # the checkpoint and the working tree.
    for value in (manifest, expected_manifest):
        value["package"].pop("source", None)
    assert manifest == expected_manifest, label
    assert actual["flags"] == expected["flags"], label
