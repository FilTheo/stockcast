"""
Inventory policy implementations.

All policies inherit from BasePolicy and implement fit/predict API.
"""

from stockcast.policies.single_order import (
    SingleOrderPolicy,
    newsvendor_critical_fractile,
)

from .order_up_to import OrderUpToPolicy
from .reorder_point import ReorderPointPolicy
from .reorder_point_targets import ReorderPointTargetProvider, ReorderPointTargets

__all__ = [
    "OrderUpToPolicy",
    "ReorderPointPolicy",
    "ReorderPointTargetProvider",
    "ReorderPointTargets",
    "SingleOrderPolicy",
    "newsvendor_critical_fractile",
]
