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
    """
    Evaluate inventory simulations at the event-frame level.

    Aggregation grain is always explicit. Pass ``groupby=[]`` for one pooled
    result or name the dimensions to retain.
    """

    def __init__(self, default_groupby: Optional[Sequence[str]] = None) -> None:
        self.default_groupby = (
            None if default_groupby is None else list(default_groupby)
        )
        self.event_frame_ = pd.DataFrame()

    def fit(
        self,
        simulation_result: Optional[SimulationResult] = None,
        event_frame: Optional[pd.DataFrame] = None,
        *,
        window: Optional[str] = None,
    ) -> "InventoryEvaluator":
        if (simulation_result is None) == (event_frame is None):
            raise ValueError("Provide either simulation_result or event_frame")

        if simulation_result is not None:
            if window is None:
                raise ValueError(
                    "window is required with simulation_result: choose 'scoring' or 'all'"
                )
            self.event_frame_ = validate_event_frame(
                simulation_result.to_event_frame(window=window)
            )
        else:
            self.event_frame_ = validate_event_frame(event_frame)
            if window is not None:
                if "run_window" not in self.event_frame_.columns:
                    raise ValueError("event_frame does not contain run_window")
                if window not in {"all", "warmup", "scoring", "settlement"}:
                    raise ValueError(
                        "window must be 'all', 'warmup', 'scoring', or 'settlement'"
                    )
                if window != "all":
                    self.event_frame_ = self.event_frame_[
                        self.event_frame_["run_window"] == window
                    ].copy()
        self.evaluation_window_ = window or "provided_event_frame"
        return self

    def evaluate(
        self,
        metrics: Iterable,
        groupby: Optional[Sequence[str]] = None,
        context: Optional[dict] = None,
    ) -> pd.DataFrame:
        if self.event_frame_.empty:
            raise ValueError("Evaluator is not fitted or event_frame is empty")

        context = context or {}
        metric_specs = [self._normalize_metric(metric) for metric in metrics]
        if groupby is None and self.default_groupby is None:
            raise ValueError(
                "groupby must be explicit; use [] for a pooled result or provide dimensions"
            )
        group_columns = self.default_groupby if groupby is None else list(groupby)

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
        return self.event_frame_.groupby(group_columns, dropna=False, sort=False)

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
