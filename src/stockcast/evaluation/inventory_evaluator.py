"""
Simulation-first inventory evaluator.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

import pandas as pd

from stockcast.core.simulation_engine import SimulationResult

from .metrics import BaseInventoryMetric
from .event_validation import validate_event_frame


class InventoryEvaluator:
    """Compute metrics from a run's event ledger.

    ``fit`` selects and validates the ledger rows (by default the run's scoring
    window); ``evaluate`` computes metrics, pooled into one row by default or
    grouped by ledger columns such as ``["unique_id"]``.

    Example:
        ```python
        evaluator = InventoryEvaluator().fit(result)          # the scoring window
        evaluator.evaluate([fill_rate, avg_on_hand])           # one pooled row
        evaluator.evaluate([fill_rate], groupby=["unique_id"]) # one row per SKU
        ```
    """

    def __init__(self) -> None:
        self.event_frame_ = pd.DataFrame()

    def fit(
        self,
        simulation_result: Optional[SimulationResult] = None,
        event_frame: Optional[pd.DataFrame] = None,
        *,
        window: Optional[str] = None,
    ) -> "InventoryEvaluator":
        """Select and validate the ledger rows to evaluate.

        Pass either a ``SimulationResult`` or a ledger DataFrame. The rows are checked
        with ``validate_event_frame`` so metrics are computed from balanced books.

        Args:
            simulation_result: A result from ``SimulationEngine.run``.
            event_frame: A ledger DataFrame, for example one you saved or enriched
                with per-row cost rates.
            window: ``"scoring"`` (default, the window ``summary()`` uses),
                ``"warmup"``, ``"settlement"``, or ``"all"``, for a
                ``simulation_result`` and an ``event_frame`` alike.

        Returns:
            The fitted evaluator (``self``).

        Raises:
            ValueError: If both or neither inputs are given, the window is unknown,
                or the ledger fails validation.
        """
        if (simulation_result is None) == (event_frame is None):
            raise ValueError("Provide either simulation_result or event_frame")

        if window is None:
            # A saved ledger is scored on the same rows as its result.
            window = "scoring"
        if simulation_result is not None:
            self.event_frame_ = validate_event_frame(
                simulation_result.to_event_frame(window=window)
            )
        else:
            # Validation requires run_window, a canonical column.
            self.event_frame_ = validate_event_frame(event_frame)
            if window not in {"all", "warmup", "scoring", "settlement"}:
                raise ValueError(
                    "window must be 'all', 'warmup', 'scoring', or 'settlement'"
                )
            if window != "all":
                self.event_frame_ = self.event_frame_[
                    self.event_frame_["run_window"] == window
                ].copy()
        self.evaluation_window_ = window
        return self

    def evaluate(
        self,
        metrics: Iterable,
        groupby: Optional[Sequence[str]] = None,
        context: Optional[dict] = None,
    ) -> pd.DataFrame:
        """Compute metrics for each group of the fitted ledger.

        Args:
            metrics: Metric functions (``metric(event_frame, context)``) or objects
                with ``name`` and ``compute``, such as ``BaseInventoryMetric``
                subclasses. A function's ``__name__`` becomes its column name.
            groupby: Ledger columns to group by, such as ``["unique_id"]``;
                ``None`` or ``[]`` (default) gives one pooled row.
            context: Options and rates for the metrics, for example cost rates,
                ``cost_components``, ``include_partial_cycles``, or
                ``periods_per_year``.

        Returns:
            One row per group, with the group columns followed by one column per
            metric.

        Raises:
            ValueError: If the evaluator is not fitted.
        """
        if self.event_frame_.empty:
            raise ValueError("Evaluator is not fitted or event_frame is empty")

        context = context or {}
        metric_specs = [self._normalize_metric(metric) for metric in metrics]
        group_columns = [] if groupby is None else list(groupby)

        rows = []
        for keys, group in self._iter_groups(group_columns):
            row = self._group_key_to_row(group_columns, keys)
            for name, metric in metric_specs:
                row[name] = self._compute_metric(metric, group, context)
            rows.append(row)

        result = pd.DataFrame(rows)
        if group_columns:
            return result[group_columns + [name for name, _ in metric_specs]]
        return result[[name for name, _ in metric_specs]]

    def _iter_groups(self, group_columns: List[str]):
        if not group_columns:
            return [((), self.event_frame_)]
        # One column is grouped by its name: grouping by a one-element list
        # warns on pandas 1.5 and changes the key type between versions.
        keys = group_columns[0] if len(group_columns) == 1 else group_columns
        return self.event_frame_.groupby(keys, dropna=False, sort=False)

    @staticmethod
    def _group_key_to_row(group_columns: List[str], keys) -> dict:
        if not group_columns:
            return {}
        if len(group_columns) == 1 and not isinstance(keys, tuple):
            keys = (keys,)
        return dict(zip(group_columns, keys))

    @staticmethod
    def _normalize_metric(metric):
        if isinstance(metric, BaseInventoryMetric):
            return metric.name, metric
        if hasattr(metric, "name") and hasattr(metric, "compute"):
            return metric.name, metric
        if callable(metric):
            return getattr(metric, "__name__", "custom_metric"), metric
        raise TypeError(f"Unsupported metric type: {type(metric)}")

    @staticmethod
    def _compute_metric(metric, event_frame: pd.DataFrame, context: dict) -> float:
        if hasattr(metric, "compute"):
            return metric.compute(event_frame, context)
        return metric(event_frame, context)
