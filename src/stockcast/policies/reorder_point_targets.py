"""Your own rule for reorder points: the ``ReorderPointTargetProvider`` extension point."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from stockcast.core.data_structures import _require_identifiers


@dataclass(frozen=True)
class ReorderPointTargets:
    """The result of a target provider.

    Attributes:
        frame: One row per SKU with the SKU column and ``reorder_point``, plus
            ``order_up_to_level`` for an ``(s,S)`` policy.
        metadata: JSON-serialisable description, stored in the run manifest.
    """

    frame: pd.DataFrame
    metadata: dict = field(default_factory=dict)


class ReorderPointTargetProvider(ABC):
    """Base class for your own rule that sets ``s`` (and ``S``) per SKU.

    Pass an instance to ``ReorderPointPolicy.fit(data, target_provider=...)``.
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
    ) -> ReorderPointTargets:
        """Return one reorder point (and order-up-to level) per SKU.

        Args:
            target_data: The table passed to ``ReorderPointPolicy.fit``.
            sku_column: SKU column name.

        Returns:
            A ``ReorderPointTargets``. Stockcast then checks that every value is
            finite, ``s >= 0`` and, for ``(s,S)``, ``S >= s``.
        """

    def to_manifest(self) -> dict:
        """Describe the provider for the run manifest.
        """
        return {
            "provider_class": type(self).__name__,
            "target_source": self.target_source,
        }


def validate_reorder_point_targets(
    result: ReorderPointTargets,
    *,
    sku_column: str,
    order_up_to: bool,
) -> pd.DataFrame:
    """Centrally validate provider output.

    ``order_up_to`` says whether the policy is ``(s,S)`` (``order_up_to_level``
    required) or ``(s,Q)`` (``order_up_to_level`` must be absent).
    """
    if not isinstance(result, ReorderPointTargets):
        raise TypeError("target provider must return ReorderPointTargets")
    frame = result.frame
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("target provider returned an empty or invalid target frame")
    required = [sku_column, "reorder_point"]
    if order_up_to:
        required.append("order_up_to_level")
    elif "order_up_to_level" in frame.columns:
        raise ValueError(
            "an (s,Q) policy orders Q; the target provider must not return order_up_to_level"
        )
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"target provider output is missing required columns: {missing}")
    _require_identifiers(frame, sku_column, "target provider output", unique=True)
    prepared = frame[required].copy()
    for column in required[1:]:
        values = pd.to_numeric(prepared[column], errors="coerce")
        if values.isna().any() or not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"target provider output.{column} must contain finite numbers")
        prepared[column] = values.astype(float)
    if (prepared["reorder_point"] < 0).any():
        raise ValueError("reorder points must be non-negative")
    if order_up_to and (prepared["order_up_to_level"] < prepared["reorder_point"]).any():
        raise ValueError("order-up-to levels must be >= reorder points")
    if not isinstance(result.metadata, dict):
        raise TypeError("target provider metadata must be a dictionary")
    return prepared
