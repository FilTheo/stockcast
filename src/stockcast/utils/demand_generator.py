"""
Demand generation utilities for multi-SKU inventory simulation.

This module provides DemandGenerator, which produces demand DataFrames
in the format expected by SimulationEngine and process_demand():
    - unique_id: SKU identifier
    - y: demand quantity (≥ 0)
    - period: simulation period index
    - date: date corresponding to the period

Each method returns the complete demand table for a run. Built-in
distributions cover common cases; ``sample`` accepts any user-defined
sampler.

Usage:
    gen = DemandGenerator(
        ['SKU_A', 'SKU_B'],
        start_date=pd.Timestamp('2025-01-01'),
        freq='D',
        random_seed=42,
        negative_demand_handling='clip_zero',
    )

    demand_df = gen.normal(n_periods=365, mean=100, std=20)
    result = engine.run(
        policy=policy, demand_source=demand_df, inventory=inv,
        random_seed=42,         # optional, recorded in the manifest
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
    (names set by ``sku_column``, ``demand_column``, ``period_column`` and
    ``date_column``).

    Parameters accept scalars (same for all SKUs) or dicts keyed by SKU
    for per-SKU configuration. ``sample`` accepts any sampler function in the
    same way: one for the whole panel, or a dict with one per SKU. Negative draws are rejected by default, or clipped to zero
    with a warning when ``negative_demand_handling='clip_zero'``.

    Args:
        skus: List of SKU identifiers.
        start_date: Explicit date for demand period 0. For ``SimulationEngine``
            this is one period after the inventory opening date.
        freq: Length of one period, a pandas frequency such as ``"D"``.
        random_seed: Explicit random seed, or ``None`` for intentionally unseeded data.
        negative_demand_handling: Explicitly reject or clip negative draws.
        sku_column: Name of the SKU column in the output (default ``"unique_id"``).
        date_column: Name of the date column in the output (default ``"date"``).
        period_column: Name of the period column in the output (default ``"period"``).
        demand_column: Name of the demand column in the output (default ``"y"``).

    Example:
        ```python
        gen = DemandGenerator(
            ['A', 'B', 'C'],
            start_date='2025-01-01',
            freq='D',
            random_seed=42,
            negative_demand_handling='clip_zero',
        )

        # Batch: full DataFrame
        df = gen.normal(n_periods=30, mean=100, std=20)

        # Any distribution: a sampler(rng, periods) -> one value per period
        df = gen.sample(30, lambda rng, periods: rng.poisson(100, periods.size))
        ```
    """

    def __init__(
        self,
        skus: Union[List[str], np.ndarray],
        *,
        start_date: pd.Timestamp,
        freq: str,
        random_seed: int | None,
        negative_demand_handling: Literal["raise", "clip_zero"] = "raise",
        sku_column: str = "unique_id",
        date_column: str = "date",
        period_column: str = "period",
        demand_column: str = "y",
    ):
        columns = {
            "sku_column": sku_column,
            "date_column": date_column,
            "period_column": period_column,
            "demand_column": demand_column,
        }
        for name, value in columns.items():
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty column name")
        if len(set(columns.values())) != len(columns):
            raise ValueError("sku_column, date_column, period_column and demand_column must differ")
        self.sku_column = sku_column
        self.date_column = date_column
        self.period_column = period_column
        self.demand_column = demand_column
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
        self.period_offset = _require_forward_frequency(freq, "freq")
        if random_seed is not None and (
            not isinstance(random_seed, int) or isinstance(random_seed, bool)
        ):
            raise ValueError("random_seed must be an integer or explicit None")
        if negative_demand_handling not in {"raise", "clip_zero"}:
            raise ValueError("negative_demand_handling must be 'raise' or 'clip_zero'")
        self.random_seed = random_seed
        self.negative_demand_handling = negative_demand_handling
        self.rng = np.random.default_rng(random_seed)

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
                    self.sku_column: sku,
                    self.demand_column: float(prepared[sku][period]),
                    self.period_column: period,
                    self.date_column: date,
                })
        return self._attach_generation_provenance(pd.DataFrame(records), raw_arrays)

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

    def normal_from_history(
        self,
        historical_df: pd.DataFrame,
        n_periods: int,
        demand_column: str = 'y',
        sku_column: str = 'unique_id',
    ) -> pd.DataFrame:
        """
        Normal demand with each SKU's historical mean and standard deviation.

        The same as ``normal`` with ``mean`` and ``std`` estimated per SKU from
        ``historical_df`` (sample standard deviation, ``ddof=1``). For any other
        distribution, for example resampling the history itself, use ``sample``.

        Args:
            historical_df: DataFrame with historical demand data.
            n_periods: Number of periods to generate.
            demand_column: Column name for demand values.
            sku_column: Column name for SKU identifiers.

        Returns:
            DataFrame with columns [unique_id, y, period, date].
        """
        self._validate_n_periods(n_periods)
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
        reproducible from ``random_seed``. ``periods`` holds the integer period
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

    def __repr__(self) -> str:
        return (
            f"DemandGenerator(skus={self.skus}, start_date={self.start_date}, "
            f"freq={self.period_offset.freqstr}, random_seed={self.random_seed})"
        )
