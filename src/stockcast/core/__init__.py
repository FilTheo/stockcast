"""
Core module for inventory management package.

Contains data structures and base policy class for the DataFrame-based
multi-SKU inventory management system.
"""

from stockcast.core.decision_schedule import (
    DecisionSchedule,
    ExplicitSchedule,
    OneTimeSchedule,
    PeriodicSchedule,
)

from .base_policy import BasePolicy
from .callbacks import (
    CallbackContext,
    CallbackError,
    InventoryAdjustmentResult,
    OrderAdjustmentResult,
    ScheduledInventoryAdjustment,
    ScheduledOrderHold,
    ScheduledOrderMultiplier,
    ScheduledOrderOverride,
    SimulationCallback,
)
from ._open_orders import ORDER_FRAME_COLUMNS
from .data_structures import InventoryStateDataFrame, OrderDecision, OrderLines
from .order_constraints import (
    ConstraintContext,
    ConstraintResult,
    MaximumOrderQuantity,
    MinimumOrderQuantity,
    OrderingConstraint,
    OrderingConstraints,
    OrderMultiple,
    ShelfSpaceLimit,
)
from .processes import (
    PROCESS_FLOW_COLUMNS,
    Flow,
    InventoryProcess,
    ProcessContext,
    ProcessFlows,
    StockChange,
)
from .shelf_life import FIFOLotLedger, ShelfLife, ShelfLifeEngine
from .simulation_engine import (
    CALLBACK_AUDIT_COLUMNS,
    RUN_MANIFEST_REQUIRED_SECTIONS,
    ComparisonResult,
    SimulationEngine,
    SimulationResult,
)
from .supply import (
    AllocationContext,
    Supplier,
    SupplierAllocation,
    SupplierShares,
    SupplyModel,
)

__all__ = [
    "BasePolicy",
    "CALLBACK_AUDIT_COLUMNS",
    "CallbackContext",
    "CallbackError",
    "ComparisonResult",
    "ConstraintContext",
    "ConstraintResult",
    "FIFOLotLedger",
    "InventoryAdjustmentResult",
    "InventoryStateDataFrame",
    "MaximumOrderQuantity",
    "MinimumOrderQuantity",
    "OrderAdjustmentResult",
    "OrderDecision",
    "OrderMultiple",
    "OrderingConstraint",
    "OrderingConstraints",
    "ScheduledInventoryAdjustment",
    "ScheduledOrderHold",
    "ScheduledOrderMultiplier",
    "ScheduledOrderOverride",
    "RUN_MANIFEST_REQUIRED_SECTIONS",
    "ShelfLifeEngine",
    "ShelfSpaceLimit",
    "SimulationCallback",
    "SimulationEngine",
    "SimulationResult",
    "DecisionSchedule",
    "PeriodicSchedule",
    "OneTimeSchedule",
    "ExplicitSchedule",
    "AllocationContext",
    "ORDER_FRAME_COLUMNS",
    "OrderLines",
    "Supplier",
    "SupplierAllocation",
    "SupplierShares",
    "SupplyModel",
    "Flow",
    "InventoryProcess",
    "PROCESS_FLOW_COLUMNS",
    "ProcessContext",
    "ProcessFlows",
    "ShelfLife",
    "StockChange",
]
