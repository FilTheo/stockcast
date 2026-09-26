"""
Demand generation utilities for multi-SKU inventory simulation.

This module provides DemandGenerator, which produces demand DataFrames
in the format expected by SimulationEngine and process_demand():
    - unique_id: SKU identifier
    - y: demand quantity (≥ 0)
    - period: simulation period index
    - date: date corresponding to the period

Supports both batch generation (full DataFrame) and callable generation
(period-by-period, for dynamic demand with SimulationEngine). Built-in
distributions cover common cases; ``sample``/``sample_fn`` accept any
user-defined sampler.

Usage:
    gen = DemandGenerator(
        ['SKU_A', 'SKU_B'],
        start_date=pd.Timestamp('2025-01-01'),
        period_frequency='D',
        seed=42,
        negative_demand_handling='clip_zero',
    )

    # Full DataFrame
    demand_df = gen.normal(n_periods=365, mean=100, std=20)

    # Callable for SimulationEngine
    demand_fn = gen.normal_fn(mean=100, std=20)
    result = engine.run(
        policy=policy,
        demand_source=demand_fn,
        inventory=inv,
        n_periods=365,
        period_frequency="D",
        initial_decision="none",
        warmup_periods=0,
        scoring_periods=365,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="synthetic_normal",
        random_seed=42,
    )
"""

from typing import Union, Dict, Callable, List, Literal
import warnings

import numpy as np
import pandas as pd

from stockcast.core.data_structures import (
    _require_forward_frequency,
    _require_identifiers,
)


class DemandGenerator:
    """Generates multi-SKU demand DataFrames for inventory simulation.

    All generation methods produce DataFrames with columns:
        [unique_id, y, period, date]

    Parameters accept scalars (same for all SKUs) or dicts keyed by SKU
    for per-SKU configuration. ``sample`` and ``sample_fn`` accept any
    sampler function in the same way: one for the whole panel, or a dict with
    one per SKU. Negative draws are rejected by default, or clipped to zero
    with a warning when ``negative_demand_handling='clip_zero'``.

    Args:
        skus: List of SKU identifiers.
        start_date: Explicit date for demand period 0. For ``SimulationEngine``
            this is one period after the inventory opening date.
        period_frequency: Explicit pandas frequency for one period.
        seed: Explicit random seed, or ``None`` for intentionally unseeded data.
        negative_demand_handling: Explicitly reject or clip negative draws.

    Example:
        ```python
        gen = DemandGenerator(
            ['A', 'B', 'C'],
            start_date='2025-01-01',
            period_frequency='D',
            seed=42,
            negative_demand_handling='clip_zero',
        )

        # Batch: full DataFrame
        df = gen.normal(n_periods=30, mean=100, std=20)

        # Any distribution: a sampler(rng, periods) -> one value per period
        df = gen.sample(30, lambda rng, periods: rng.poisson(100, periods.size))

        # Dynamic: callable for SimulationEngine
        fn = gen.normal_fn(mean=100, std=20)
        result = engine.run(
            policy=p,
            demand_source=fn,
            inventory=inv,
            n_periods=30,
            period_frequency="D",
            initial_decision="none",
            warmup_periods=0,
            scoring_periods=30,
            settlement_periods=0,
            order_during_settlement=False,
            demand_source_name="synthetic_normal",
            random_seed=42,
        )
        ```
    """

    def __init__(
        self,
        skus: Union[List[str], np.ndarray],
        *,
        start_date: pd.Timestamp,
        period_frequency: str,
        seed: int | None,
        negative_demand_handling: Literal["raise", "clip_zero"] = "raise",
    ):
        self.skus = list(skus)
        if not self.skus:
            raise ValueError("skus must be non-empty")
        _require_identifiers(
            pd.DataFrame({"unique_id": self.skus}),
            "unique_id",
            "skus",
            unique=True,
        )
        try:
            self.start_date = pd.Timestamp(start_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("start_date must be a valid timestamp") from exc
        if pd.isna(self.start_date):
            raise ValueError("start_date must be a valid timestamp")
        self.period_offset = _require_forward_frequency(
            period_frequency,
            "period_frequency",
        )
        if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
            raise ValueError("seed must be an integer or explicit None")
        if negative_demand_handling not in {"raise", "clip_zero"}:
            raise ValueError("negative_demand_handling must be 'raise' or 'clip_zero'")
        self.seed = seed
        self.negative_demand_handling = negative_demand_handling
        self.rng = np.random.default_rng(seed)

    # ========================================================================
    # INTERNAL HELPERS
    # ========================================================================

    def _per_sku(self, param, name: str) -> list:
        """Expand a single value or complete SKU dictionary to SKU order."""
        if isinstance(param, dict):
            expected = set(self.skus)
            supplied = set(param)
            if supplied != expected:
                raise ValueError(
                    f"{name} dictionary must contain exactly the generator SKUs; "
                    f"missing={sorted(expected - supplied)[:5]}, "
                    f"extra={sorted(supplied - expected)[:5]}"
                )
            return [param[sku] for sku in self.skus]
        return [param] * len(self.skus)

    def _resolve_param(self, param, name: str, *, nonnegative: bool = False):
        """Convert a finite scalar or complete SKU dictionary to a list."""
        raw_values = self._per_sku(param, name)
        try:
            values = np.asarray(raw_values, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must contain finite numeric values") from exc
        if not np.isfinite(values).all():
            raise ValueError(f"{name} must contain finite numeric values")
        if nonnegative and (values < 0).any():
            raise ValueError(f"{name} must contain non-negative values")
        return values.tolist()

    def _resolve_sampler(self, sampler) -> list:
        """Convert one sampler or a complete SKU dictionary of samplers to a list."""
        samplers = self._per_sku(sampler, "sampler")
        for sku, fn in zip(self.skus, samplers):
            if not callable(fn):
                raise TypeError(
                    f"sampler for SKU {sku!r} must be callable as "
                    f"sampler(rng, periods); got {type(fn).__name__}"
                )
        return samplers

    def _draw(self, sampler, sku, periods: np.ndarray) -> np.ndarray:
        """Call a sampler on the seeded stream and check its output."""
        values = np.asarray(sampler(self.rng, periods))
        if values.dtype.kind not in "iuf":
            raise ValueError(
                f"sampler for SKU {sku!r} must return numeric values; "
                f"got dtype {values.dtype}"
            )
        if values.shape != periods.shape:
            raise ValueError(
                f"sampler for SKU {sku!r} returned shape {values.shape}; "
                f"expected {periods.shape}, one value per period"
            )
        if not np.isfinite(values).all():
            raise ValueError(f"sampler for SKU {sku!r} returned non-finite values")
        return values.astype(float)

    @staticmethod
    def _validate_n_periods(n_periods: int) -> None:
        if not isinstance(n_periods, int) or isinstance(n_periods, bool) or n_periods < 1:
            raise ValueError("n_periods must be an integer >= 1")

    @staticmethod
    def _validate_period(period: int) -> None:
        if not isinstance(period, int) or isinstance(period, bool) or period < 0:
            raise ValueError("period must be an integer >= 0")

    def _handle_negative(self, values) -> np.ndarray:
        array = np.asarray(values, dtype=float)
        if not np.isfinite(array).all():
            raise ValueError("generated demand must be finite")
        if (array < 0).any():
            if self.negative_demand_handling == "raise":
                raise ValueError("generated demand contains negative values")
            negative = array[array < 0]
            warnings.warn(
                "generated demand contained "
                f"{negative.size} negative value(s), with minimum {negative.min():.6g}; "
                "they were clipped to zero because negative_demand_handling='clip_zero'",
                RuntimeWarning,
                stacklevel=3,
            )
            array = np.maximum(array, 0.0)
        return array

    def _attach_generation_provenance(self, frame: pd.DataFrame, raw_values) -> pd.DataFrame:
        arrays = [np.asarray(values, dtype=float).reshape(-1) for values in raw_values]
        combined = np.concatenate(arrays) if arrays else np.asarray([], dtype=float)
        negatives = combined[combined < 0]
        frame.attrs["stockcast_demand_provenance"] = {
            "negative_demand_handling": self.negative_demand_handling,
            "clipped_negative_count": int(negatives.size),
            "minimum_clipped_value": float(negatives.min()) if negatives.size else None,
        }
        return frame

    def _build_df(self, n_periods, demand_arrays):
        """
        Build standard demand DataFrame from per-SKU arrays.

        Args:
            n_periods: Number of periods.
            demand_arrays: dict of {sku: np.array of shape (n_periods,)}

        Returns:
            DataFrame with columns [unique_id, y, period, date].
        """
        self._validate_n_periods(n_periods)
        raw_arrays = [demand_arrays[sku] for sku in self.skus]
        prepared = {
            sku: self._handle_negative(demand_arrays[sku])
            for sku in self.skus
        }
        records = []
        for period in range(n_periods):
            date = self.start_date + period * self.period_offset
            for sku in self.skus:
                records.append({
                    'unique_id': sku,
                    'y': float(prepared[sku][period]),
                    'period': period,
                    'date': date,
                })
        return self._attach_generation_provenance(pd.DataFrame(records), raw_arrays)

    def _single_period_df(self, period, demands):
        """Build DataFrame for a single period from per-SKU demand values."""
        self._validate_period(period)
        prepared = self._handle_negative(demands)
        date = self.start_date + period * self.period_offset
        frame = pd.DataFrame({
            'unique_id': self.skus,
            'y': prepared.astype(float),
            'period': period,
            'date': date,
        })
        return self._attach_generation_provenance(frame, [demands])

    # ========================================================================
    # BATCH GENERATORS (return full DataFrame)
    # ========================================================================

    def constant(self, n_periods: int, value: Union[float, Dict[str, float]]) -> pd.DataFrame:
        """
        Generate constant demand.

        Args:
            n_periods: Number of periods to generate.
            value: Demand per period. Scalar or dict keyed by SKU.

        Returns:
            DataFrame with columns [unique_id, y, period, date].
        """
        self._validate_n_periods(n_periods)
        values = self._resolve_param(value, "value", nonnegative=True)
        arrays = {sku: np.full(n_periods, v) for sku, v in zip(self.skus, values)}
        return self._build_df(n_periods, arrays)

    def normal(
        self,
        n_periods: int,
        mean: Union[float, Dict[str, float]],
        std: Union[float, Dict[str, float]],
    ) -> pd.DataFrame:
        """
        Generate normally distributed demand.

        Args:
            n_periods: Number of periods to generate.
            mean: Mean demand. Scalar or dict keyed by SKU.
            std: Standard deviation. Scalar or dict keyed by SKU.

        Returns:
            DataFrame with columns [unique_id, y, period, date].
        """
        self._validate_n_periods(n_periods)
        means = self._resolve_param(mean, "mean", nonnegative=True)
        stds = self._resolve_param(std, "std", nonnegative=True)
        arrays = {
            sku: self.rng.normal(m, s, n_periods)
            for sku, m, s in zip(self.skus, means, stds)
        }
        return self._build_df(n_periods, arrays)

    def seasonal(
        self,
        n_periods: int,
        base: Union[float, Dict[str, float]],
        amplitude: Union[float, Dict[str, float]],
        season_length: int,
        std: Union[float, Dict[str, float]],
    ) -> pd.DataFrame:
        """
        Generate demand with sinusoidal seasonal pattern + noise.

        Formula: demand = base + amplitude * sin(2π * t / season_length) + noise(0, std)

        Args:
            n_periods: Number of periods to generate.
            base: Base demand level. Scalar or dict keyed by SKU.
            amplitude: Seasonal swing amplitude. Scalar or dict keyed by SKU.
            season_length: Explicit length of one seasonal cycle in periods.
            std: Explicit noise standard deviation.

        Returns:
            DataFrame with columns [unique_id, y, period, date].
        """
        self._validate_n_periods(n_periods)
        if not isinstance(season_length, int) or isinstance(season_length, bool) or season_length < 1:
            raise ValueError("season_length must be an integer >= 1")
        bases = self._resolve_param(base, "base", nonnegative=True)
        amplitudes = self._resolve_param(amplitude, "amplitude", nonnegative=True)
        stds = self._resolve_param(std, "std", nonnegative=True)

        t = np.arange(n_periods)
        seasonal_component = np.sin(2 * np.pi * t / season_length)

        arrays = {}
        for sku, b, a, s in zip(self.skus, bases, amplitudes, stds):
            noise = self.rng.normal(0, s, n_periods)
            arrays[sku] = b + a * seasonal_component + noise

        return self._build_df(n_periods, arrays)

    def trend(
        self,
        n_periods: int,
        initial: Union[float, Dict[str, float]],
        growth_rate: Union[float, Dict[str, float]],
        std: Union[float, Dict[str, float]],
    ) -> pd.DataFrame:
        """
        Generate demand with linear trend + noise.

        Formula: demand = initial + growth_rate * t + noise(0, std)

        Args:
            n_periods: Number of periods to generate.
            initial: Starting demand level. Scalar or dict keyed by SKU.
            growth_rate: Demand increase per period. Scalar or dict keyed by SKU.
            std: Explicit noise standard deviation.

        Returns:
            DataFrame with columns [unique_id, y, period, date].
        """
        self._validate_n_periods(n_periods)
        initials = self._resolve_param(initial, "initial", nonnegative=True)
        rates = self._resolve_param(growth_rate, "growth_rate")
        stds = self._resolve_param(std, "std", nonnegative=True)

        t = np.arange(n_periods)
        arrays = {}
        for sku, init, rate, s in zip(self.skus, initials, rates, stds):
            noise = self.rng.normal(0, s, n_periods)
            arrays[sku] = init + rate * t + noise

        return self._build_df(n_periods, arrays)

    def from_historical(
        self,
        historical_df: pd.DataFrame,
        n_periods: int,
        sampling_method: str,
        demand_column: str = 'y',
        sku_column: str = 'unique_id',
    ) -> pd.DataFrame:
        """
        Generate synthetic demand by sampling from per-SKU historical statistics.

        Computes mean and std of demand per SKU from historical data,
        then generates normally distributed demand with those parameters.

        Args:
            historical_df: DataFrame with historical demand data.
            n_periods: Number of periods to generate.
            sampling_method: How to sample from the history. Only
                ``"normal_moments"`` is supported: normal draws with each SKU's
                historical mean and sample standard deviation (``ddof=1``).
            demand_column: Column name for demand values.
            sku_column: Column name for SKU identifiers.

        Returns:
            DataFrame with columns [unique_id, y, period, date].
        """
        self._validate_n_periods(n_periods)
        if sampling_method != "normal_moments":
            raise ValueError("sampling_method must be 'normal_moments'")
        required = [sku_column, demand_column]
        missing = [column for column in required if column not in historical_df.columns]
        if missing:
            raise ValueError(f"historical_df is missing required columns: {missing}")
        values = pd.to_numeric(historical_df[demand_column], errors="coerce")
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError("historical demand must be complete and finite")
        if (values < 0).any():
            raise ValueError("historical demand must be non-negative")
        prepared = historical_df.copy()
        prepared[demand_column] = values.astype(float)
        unknown_skus = set(prepared[sku_column]) - set(self.skus)
        if unknown_skus:
            raise ValueError(f"historical_df contains unknown SKUs: {sorted(unknown_skus)[:5]}")
        arrays = {}
        for sku in self.skus:
            sku_data = prepared[prepared[sku_column] == sku][demand_column]
            if len(sku_data) < 2:
                raise ValueError(
                    f"historical_df requires at least two observations for SKU {sku}"
                )
            arrays[sku] = self.rng.normal(
                float(sku_data.mean()),
                float(sku_data.std(ddof=1)),
                n_periods,
            )
        return self._build_df(n_periods, arrays)

    def sample(
        self,
        n_periods: int,
        sampler: Union[Callable, Dict[str, Callable]],
    ) -> pd.DataFrame:
        """
        Generate demand from any sampler function.

        A sampler is a function ``sampler(rng, periods)`` that returns one
        demand value per entry of ``periods``. ``rng`` is the generator's
        seeded ``np.random.Generator``; draw from it so the panel is
        reproducible from ``seed``. ``periods`` holds the integer period
        indices, so demand can change over time.

        Pass one sampler to generate every SKU in the panel from it (each SKU
        gets its own draws), or a dict with one sampler per SKU.

        Args:
            n_periods: Number of periods to generate.
            sampler: A sampler for the whole panel, or a dict keyed by SKU.

        Returns:
            DataFrame with columns [unique_id, y, period, date].

        Example:
            ```python
            def poisson(rng, periods):
                return rng.poisson(6.0, periods.size)

            def busy_weekends(rng, periods):
                return rng.poisson(6.0 + 3.0 * (periods % 7 >= 5))

            panel = gen.sample(28, poisson)
            mixed = gen.sample(28, {"A": poisson, "B": busy_weekends})
            ```
        """
        self._validate_n_periods(n_periods)
        samplers = self._resolve_sampler(sampler)
        arrays = {
            sku: self._draw(fn, sku, np.arange(n_periods))
            for sku, fn in zip(self.skus, samplers)
        }
        return self._build_df(n_periods, arrays)

    # ========================================================================
    # CALLABLE GENERATORS (return callable(period) -> demand_df)
    # ========================================================================

    def constant_fn(self, value: Union[float, Dict[str, float]]) -> Callable:
        """Returns callable(period) -> demand_df with constant demand."""
        values = self._resolve_param(value, "value", nonnegative=True)
        def fn(period):
            return self._single_period_df(period, values)
        return fn

    def normal_fn(self, mean: Union[float, Dict[str, float]], std: Union[float, Dict[str, float]]) -> Callable:
        """Returns callable(period) -> demand_df with normally distributed demand."""
        means = self._resolve_param(mean, "mean", nonnegative=True)
        stds = self._resolve_param(std, "std", nonnegative=True)
        def fn(period):
            demands = [self.rng.normal(m, s) for m, s in zip(means, stds)]
            return self._single_period_df(period, demands)
        return fn

    def seasonal_fn(
        self,
        base: Union[float, Dict[str, float]],
        amplitude: Union[float, Dict[str, float]],
        season_length: int,
        std: Union[float, Dict[str, float]],
    ) -> Callable:
        """Returns callable(period) -> demand_df with seasonal demand."""
        if not isinstance(season_length, int) or isinstance(season_length, bool) or season_length < 1:
            raise ValueError("season_length must be an integer >= 1")
        bases = self._resolve_param(base, "base", nonnegative=True)
        amplitudes = self._resolve_param(amplitude, "amplitude", nonnegative=True)
        stds = self._resolve_param(std, "std", nonnegative=True)
        def fn(period):
            seasonal = np.sin(2 * np.pi * period / season_length)
            demands = [b + a * seasonal + self.rng.normal(0, s) for b, a, s in zip(bases, amplitudes, stds)]
            return self._single_period_df(period, demands)
        return fn

    def trend_fn(
        self,
        initial: Union[float, Dict[str, float]],
        growth_rate: Union[float, Dict[str, float]],
        std: Union[float, Dict[str, float]],
    ) -> Callable:
        """Returns callable(period) -> demand_df with trending demand."""
        initials = self._resolve_param(initial, "initial", nonnegative=True)
        rates = self._resolve_param(growth_rate, "growth_rate")
        stds = self._resolve_param(std, "std", nonnegative=True)
        def fn(period):
            demands = [init + rate * period + self.rng.normal(0, s) for init, rate, s in zip(initials, rates, stds)]
            return self._single_period_df(period, demands)
        return fn

    def from_historical_fn(
        self,
        historical_df: pd.DataFrame,
        sampling_method: str,
        demand_column: str = 'y',
        sku_column: str = 'unique_id',
    ) -> Callable:
        """Returns callable(period) -> demand_df sampling from historical distributions."""
        if sampling_method != "normal_moments":
            raise ValueError("sampling_method must be 'normal_moments'")
        required = [sku_column, demand_column]
        missing = [column for column in required if column not in historical_df.columns]
        if missing:
            raise ValueError(f"historical_df is missing required columns: {missing}")
        values = pd.to_numeric(historical_df[demand_column], errors="coerce")
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError("historical demand must be complete and finite")
        if (values < 0).any():
            raise ValueError("historical demand must be non-negative")
        prepared = historical_df.copy()
        prepared[demand_column] = values.astype(float)
        unknown_skus = set(prepared[sku_column]) - set(self.skus)
        if unknown_skus:
            raise ValueError(f"historical_df contains unknown SKUs: {sorted(unknown_skus)[:5]}")
        stats = {}
        for sku in self.skus:
            sku_data = prepared[prepared[sku_column] == sku][demand_column]
            if len(sku_data) < 2:
                raise ValueError(
                    f"historical_df requires at least two observations for SKU {sku}"
                )
            stats[sku] = (float(sku_data.mean()), float(sku_data.std(ddof=1)))
        def fn(period):
            demands = [self.rng.normal(stats[sku][0], stats[sku][1]) for sku in self.skus]
            return self._single_period_df(period, demands)
        return fn

    def sample_fn(self, sampler: Union[Callable, Dict[str, Callable]]) -> Callable:
        """Returns callable(period) -> demand_df drawing from any sampler.

        Each call passes ``periods=np.array([period])`` to the sampler of every
        SKU, so samplers that carry state across periods (for example
        autocorrelated demand) belong in ``sample``, which sees all periods at
        once. See ``sample`` for the sampler contract.
        """
        samplers = self._resolve_sampler(sampler)
        def fn(period):
            self._validate_period(period)
            demands = np.concatenate([
                self._draw(s, sku, np.array([period]))
                for sku, s in zip(self.skus, samplers)
            ])
            return self._single_period_df(period, demands)
        return fn

    def __repr__(self) -> str:
        return (
            f"DemandGenerator(skus={self.skus}, start_date={self.start_date}, "
            f"period_frequency={self.period_offset.freqstr}, seed={self.seed})"
        )
