"""Target providers for periodic-review inventory policies."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from stockcast.core.data_structures import _require_identifiers


@dataclass(frozen=True)
class PeriodicReviewTargets:
    """The result of a target provider.

    Attributes:
        frame: One row per SKU with the SKU column, ``reorder_point`` and
            ``order_up_to_level``.
        metadata: JSON-serialisable description, stored in the run manifest.
    """

    frame: pd.DataFrame
    metadata: dict = field(default_factory=dict)


class PeriodicReviewTargetProvider(ABC):
    """Base class for objects that supply ``(s, S)`` to ``PeriodicReviewPolicy``.

    Implement ``provide``. Set the class attribute ``target_source`` to
    ``"external_direct"`` when the provider only passes along values computed
    elsewhere (default ``"custom_provider"``), and extend ``to_manifest`` with
    your settings.
    """

    target_source = "custom_provider"

    @abstractmethod
    def provide(
        self,
        target_data: pd.DataFrame,
        *,
        sku_column: str,
    ) -> PeriodicReviewTargets:
        """Return one reorder point and order-up-to level per SKU.

        Args:
            target_data: The table passed to ``PeriodicReviewPolicy.fit``.
            sku_column: SKU column name.

        Returns:
            A ``PeriodicReviewTargets``. Stockcast then checks that every value is
            finite, ``s >= 0`` and ``S >= s``.
        """

    def to_manifest(self) -> dict:
        """Describe the provider for the run manifest.
        """
        return {
            "provider_class": type(self).__name__,
            "target_source": self.target_source,
        }


class ColumnPeriodicReviewTargets(PeriodicReviewTargetProvider):
    """Read ``s`` and ``S`` from two columns of the fitted table.

    Args:
        reorder_point_column: Column with ``s``.
        order_up_to_column: Column with ``S``.
    """

    target_source = "external_direct"

    def __init__(self, *, reorder_point_column: str, order_up_to_column: str):
        for name, value in {
            "reorder_point_column": reorder_point_column,
            "order_up_to_column": order_up_to_column,
        }.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        self.reorder_point_column = reorder_point_column
        self.order_up_to_column = order_up_to_column

    def provide(self, target_data: pd.DataFrame, *, sku_column: str) -> PeriodicReviewTargets:
        missing = [
            column
            for column in (sku_column, self.reorder_point_column, self.order_up_to_column)
            if column not in target_data.columns
        ]
        if missing:
            raise ValueError(f"target_data is missing required columns: {missing}")
        frame = target_data[
            [sku_column, self.reorder_point_column, self.order_up_to_column]
        ].rename(columns={
            self.reorder_point_column: "reorder_point",
            self.order_up_to_column: "order_up_to_level",
        })
        return PeriodicReviewTargets(frame=frame, metadata=self.to_manifest())

    def to_manifest(self) -> dict:
        """Describe the provider and its column names.
        """
        return {
            **super().to_manifest(),
            "reorder_point_column": self.reorder_point_column,
            "order_up_to_column": self.order_up_to_column,
        }


class FixedPeriodicReviewTargets(PeriodicReviewTargetProvider):
    """Use fixed ``s`` and ``S`` values.

    Args:
        reorder_point: One value for all SKUs, or a dict with one value per SKU.
        order_up_to_level: One value for all SKUs, or a dict with one value per
            SKU.
    """

    target_source = "external_direct"

    def __init__(self, *, reorder_point, order_up_to_level):
        self.reorder_point = reorder_point
        self.order_up_to_level = order_up_to_level

    @staticmethod
    def _values(value, skus: list, name: str) -> list:
        if isinstance(value, Mapping):
            missing = [sku for sku in skus if sku not in value]
            extra = [sku for sku in value if sku not in set(skus)]
            if missing or extra:
                raise ValueError(
                    f"{name} mapping must contain exactly the target SKUs; "
                    f"missing={missing[:5]}, extra={extra[:5]}"
                )
            return [value[sku] for sku in skus]
        return [value] * len(skus)

    def provide(self, target_data: pd.DataFrame, *, sku_column: str) -> PeriodicReviewTargets:
        if not isinstance(target_data, pd.DataFrame) or target_data.empty:
            raise ValueError("target_data must be a non-empty pandas DataFrame")
        _require_identifiers(target_data, sku_column, "target_data", unique=True)
        skus = target_data[sku_column].tolist()
        frame = pd.DataFrame({
            sku_column: skus,
            "reorder_point": self._values(self.reorder_point, skus, "reorder_point"),
            "order_up_to_level": self._values(
                self.order_up_to_level,
                skus,
                "order_up_to_level",
            ),
        })
        return PeriodicReviewTargets(frame=frame, metadata=self.to_manifest())


def validate_periodic_review_targets(
    result: PeriodicReviewTargets,
    *,
    sku_column: str,
) -> pd.DataFrame:
    """Centrally validate built-in and custom provider output."""
    if not isinstance(result, PeriodicReviewTargets):
        raise TypeError("target provider must return PeriodicReviewTargets")
    frame = result.frame
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("target provider returned an empty or invalid target frame")
    required = [sku_column, "reorder_point", "order_up_to_level"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"target provider output is missing required columns: {missing}")
    _require_identifiers(frame, sku_column, "target provider output", unique=True)
    prepared = frame[required].copy()
    for column in ("reorder_point", "order_up_to_level"):
        values = pd.to_numeric(prepared[column], errors="coerce")
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"target provider output.{column} must contain finite numbers")
        prepared[column] = values.astype(float)
    if (prepared["reorder_point"] < 0).any():
        raise ValueError("reorder points must be non-negative")
    if (prepared["order_up_to_level"] < prepared["reorder_point"]).any():
        raise ValueError("order-up-to levels must be >= reorder points")
    if not isinstance(result.metadata, dict):
        raise TypeError("target provider metadata must be a dictionary")
    return prepared
