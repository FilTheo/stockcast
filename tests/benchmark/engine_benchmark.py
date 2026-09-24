"""Wall-clock benchmark for ``SimulationEngine`` and ``ShelfLifeEngine``.

Not a pytest module and not an acceptance gate: timings depend on the machine
and its load. Run from the repository root:

    env PYTHONPATH=src MPLCONFIGDIR=/tmp/stockcast-mpl \\
        .venv/bin/python tests/benchmark/engine_benchmark.py

Useful options:

    --skus 10 100 1000      SKU counts to time (default: 10 100 1000)
    --periods 365           simulated daily periods per run
    --scenarios weekly_out  subset of scenarios (see SCENARIOS)
    --seeds 32 --workers 4  also time many independent seeds in a process pool
    --json results.json     write the measurements

Each scenario is a complete, validated run with explicit targets, calendar,
windows and seeds, so it exercises the same preflight, event ledger and
manifest work as research code.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

import stockcast as sc
from stockcast.core import ShelfLifeEngine

ORIGIN = pd.Timestamp("2026-01-05")
DAY = pd.Timedelta(days=1)
MEAN_DEMAND = 10.0


def _demand(skus: list, n_periods: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    matrix = rng.poisson(MEAN_DEMAND, size=(n_periods, len(skus))).astype(float)
    return pd.DataFrame({
        "unique_id": np.tile(skus, n_periods),
        "period": np.repeat(np.arange(n_periods), len(skus)),
        "date": np.repeat(pd.date_range(ORIGIN + DAY, periods=n_periods, freq="D"), len(skus)),
        "y": matrix.reshape(-1),
    })


def _order_up_to(skus, *, lead_time, review, backorders):
    horizon = lead_time + review
    policy = sc.OrderUpToPolicy(
        lead_time=lead_time, review_period=review, service_level=0.9,
        allow_backorders=backorders,
    )
    targets = pd.DataFrame({
        "unique_id": skus,
        "S": MEAN_DEMAND * horizon + 2.0 * np.sqrt(MEAN_DEMAND * horizon),
        "end": ORIGIN + horizon * DAY,
    })
    return policy.fit(
        targets, forecast_origin=ORIGIN, forecast_frequency="D", target_column="S",
        target_end_date_column="end", protection_horizon=horizon,
        target_source="external_direct", target_probability=0.9,
    )


def _state(skus, *, max_lead_time, backorders, on_hand):
    state = sc.InventoryStateDataFrame(
        skus, max_lead_time=max_lead_time, allow_backorders=backorders,
    ).initialize_zero(start_date=ORIGIN)
    state.data["on_hand"] = float(on_hand)
    return state


def _run(engine, policy, demand, state, n_periods, **options):
    return engine.run(
        policy, demand, state, n_periods, period_frequency="D",
        warmup_periods=0, scoring_periods=n_periods, settlement_periods=0,
        order_during_settlement=False, demand_source_name="benchmark",
        random_seed=None, **options,
    )


def weekly_out(skus, n_periods, seed):
    """(R,S) weekly review, three-period lead time, backorders."""
    policy = _order_up_to(skus, lead_time=3, review=7, backorders=True)
    state = _state(skus, max_lead_time=3, backorders=True, on_hand=60)
    return _run(sc.SimulationEngine(), policy, _demand(skus, n_periods, seed), state, n_periods)


def daily_out(skus, n_periods, seed):
    """(R,S) daily review (a decision every period), lost sales."""
    policy = _order_up_to(skus, lead_time=2, review=1, backorders=False)
    state = _state(skus, max_lead_time=2, backorders=False, on_hand=30)
    return _run(sc.SimulationEngine(), policy, _demand(skus, n_periods, seed), state, n_periods)


def constrained_callbacks(skus, n_periods, seed):
    """Weekly (R,S) with MOQ/multiple constraints and a physical callback."""
    policy = _order_up_to(skus, lead_time=3, review=7, backorders=True)
    state = _state(skus, max_lead_time=3, backorders=True, on_hand=60)
    constraints = sc.OrderingConstraints([
        sc.MinimumOrderQuantity(20.0, mode="adjust"),
        sc.OrderMultiple(5.0, mode="adjust"),
    ])
    shrink = sc.ScheduledInventoryAdjustment(pd.DataFrame({
        "unique_id": skus,
        "period": 30,
        "quantity_delta": 0.0,
        "reason": "cycle count",
        "source": "benchmark",
    }))
    return _run(
        sc.SimulationEngine(), policy, _demand(skus, n_periods, seed), state, n_periods,
        order_constraints=constraints, callbacks=[shrink],
    )


def shelf_life(skus, n_periods, seed):
    """FIFO perishables: daily (R,S), one-period lead time, five-day life."""
    policy = _order_up_to(skus, lead_time=1, review=1, backorders=False)
    state = _state(skus, max_lead_time=1, backorders=False, on_hand=20)
    lots = pd.DataFrame({"unique_id": skus, "received_date": ORIGIN, "quantity": 20.0})
    return _run(
        ShelfLifeEngine(shelf_life_days=5), policy, _demand(skus, n_periods, seed), state,
        n_periods, opening_lots=lots,
    )


SCENARIOS = {
    "weekly_out": weekly_out,
    "daily_out": daily_out,
    "constrained_callbacks": constrained_callbacks,
    "shelf_life": shelf_life,
}


def _seed_task(args):
    scenario, n_skus, n_periods, seed = args
    skus = [f"SKU_{index:05d}" for index in range(n_skus)]
    result = SCENARIOS[scenario](skus, n_periods, seed)
    return result.summary()["fill_rate"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skus", type=int, nargs="+", default=[10, 100, 1000])
    parser.add_argument("--periods", type=int, default=365)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scenarios", nargs="+", choices=sorted(SCENARIOS), default=list(SCENARIOS))
    parser.add_argument("--seeds", type=int, default=0,
                        help="also time this many independent seeds per scenario")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--json", dest="json_path")
    args = parser.parse_args(argv)

    rows = []
    print(f"stockcast {sc.__file__}")
    print(f"python {platform.python_version()}, numpy {np.__version__}, pandas {pd.__version__}")
    print(f"{'scenario':<24}{'skus':>7}{'periods':>9}{'best s':>10}{'ms/period':>11}")
    for scenario in args.scenarios:
        for n_skus in args.skus:
            skus = [f"SKU_{index:05d}" for index in range(n_skus)]
            timings = []
            for repeat in range(args.repeats):
                started = time.perf_counter()
                SCENARIOS[scenario](skus, args.periods, repeat)
                timings.append(time.perf_counter() - started)
            best = min(timings)
            rows.append({
                "scenario": scenario, "skus": n_skus, "periods": args.periods,
                "best_seconds": best, "median_seconds": statistics.median(timings),
                "ms_per_period": 1000 * best / args.periods,
            })
            print(f"{scenario:<24}{n_skus:>7}{args.periods:>9}{best:>10.3f}"
                  f"{1000 * best / args.periods:>11.2f}", flush=True)

    if args.seeds:
        print(f"\n{args.seeds} independent seeds per scenario, {args.workers} worker processes")
        for scenario in args.scenarios:
            for n_skus in args.skus:
                tasks = [(scenario, n_skus, args.periods, seed) for seed in range(args.seeds)]
                started = time.perf_counter()
                with ProcessPoolExecutor(max_workers=args.workers) as pool:
                    list(pool.map(_seed_task, tasks))
                elapsed = time.perf_counter() - started
                rows.append({
                    "scenario": scenario, "skus": n_skus, "periods": args.periods,
                    "seeds": args.seeds, "workers": args.workers,
                    "batch_seconds": elapsed, "seeds_per_second": args.seeds / elapsed,
                })
                print(f"{scenario:<24}{n_skus:>7} skus: {elapsed:8.2f}s "
                      f"({args.seeds / elapsed:.2f} seeds/s)", flush=True)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump({
                "python": platform.python_version(),
                "numpy": np.__version__,
                "pandas": pd.__version__,
                "results": rows,
            }, handle, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
