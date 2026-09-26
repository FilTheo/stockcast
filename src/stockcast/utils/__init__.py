"""
Demand generation and the primitives for a caller-owned simulation loop.
"""

from .inventory_operations import update_inventory_with_orders, process_demand, place_order_lines
from .demand_generator import DemandGenerator

__all__ = [
    "update_inventory_with_orders",
    "process_demand",
    "DemandGenerator",
    "place_order_lines",
]
