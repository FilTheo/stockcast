"""
Demand generation and the primitives for a caller-owned simulation loop.
"""

from .inventory_operations import update_inventory_with_orders, place_order_lines
from .demand_generator import DemandGenerator

__all__ = [
    "update_inventory_with_orders",
    "DemandGenerator",
    "place_order_lines",
]
