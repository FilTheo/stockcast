"""Locked public import surface for the Stockcast 0.1 release line."""

import stockcast
import stockcast.core as core
import stockcast.evaluation as evaluation
import stockcast.policies as policies
import stockcast.utils as utils
import stockcast.visualization as visualization


def test_public_export_sets_are_frozen_for_0_1():
    assert stockcast.__all__ == [
        "BasePolicy", "CallbackContext", "CallbackError",
        "ColumnPeriodicReviewTargets", "ComparisonResult", "ConstraintContext",
        "ConstraintResult", "FixedPeriodicReviewTargets",
        "InventoryAdjustmentResult", "InventoryStateDataFrame",
        "MaximumOrderQuantity", "MinimumOrderQuantity", "OrderAdjustmentResult",
        "OrderDecision", "OrderMultiple", "OrderUpToPolicy", "OrderingConstraint",
        "OrderingConstraints", "PeriodicReviewPolicy", "PeriodicReviewTargetProvider",
        "PeriodicReviewTargets", "ReorderPointPolicy", "ScheduledInventoryAdjustment", "ScheduledOrderHold",
        "ScheduledOrderMultiplier", "ScheduledOrderOverride", "ShelfSpaceLimit",
        "SimulationCallback", "SimulationEngine", "SimulationResult",
        "DecisionSchedule", "PeriodicSchedule", "OneTimeSchedule", "ExplicitSchedule",
        "SingleOrderPolicy", "newsvendor_critical_fractile",
        # Additive order-level API (knowledge 93.7).
        "OrderLines", "Supplier", "SupplierAllocation", "SupplierShares", "SupplyModel",
    ]
    assert core.__all__ == [
        "BasePolicy", "CALLBACK_AUDIT_COLUMNS", "CallbackContext", "CallbackError",
        "ComparisonResult", "ConstraintContext", "ConstraintResult", "FIFOLotLedger",
        "InventoryAdjustmentResult", "InventoryStateDataFrame", "MaximumOrderQuantity",
        "MinimumOrderQuantity", "OrderAdjustmentResult", "OrderDecision", "OrderMultiple",
        "OrderingConstraint", "OrderingConstraints", "ScheduledInventoryAdjustment",
        "ScheduledOrderHold", "ScheduledOrderMultiplier", "ScheduledOrderOverride",
        "RUN_MANIFEST_REQUIRED_SECTIONS", "ShelfLifeEngine", "ShelfSpaceLimit",
        "SimulationCallback", "SimulationEngine", "SimulationResult",
        "DecisionSchedule", "PeriodicSchedule", "OneTimeSchedule", "ExplicitSchedule",
        # Additive order-level API (knowledge 93.7).
        "AllocationContext", "ORDER_FRAME_COLUMNS", "OrderLines", "Supplier",
        "SupplierAllocation", "SupplierShares", "SupplyModel",
        # Additive inventory-process API (knowledge 93.8).
        "Flow", "InventoryProcess", "PROCESS_FLOW_COLUMNS", "ProcessContext",
        "ProcessFlows", "ShelfLife", "StockChange",
        # Additive supplier delivery outcomes (knowledge 93.9).
        "DeliveryContext", "DeliveryOutcome",
    ]
    assert policies.__all__ == [
        "OrderUpToPolicy", "ReorderPointPolicy", "PeriodicReviewPolicy",
        "PeriodicReviewTargetProvider", "PeriodicReviewTargets",
        "ColumnPeriodicReviewTargets", "FixedPeriodicReviewTargets",
        "SingleOrderPolicy", "newsvendor_critical_fractile",
    ]
    assert evaluation.__all__ == [
        "InventoryEvaluator", "CANONICAL_EVENT_COLUMNS", "validate_event_frame",
        "BaseInventoryMetric", "CoverageMetric", "avg_inventory_position",
        "avg_on_hand", "avg_on_order", "backlog_cost", "backlog_unit_periods",
        "backorder_period_rate", "capacity_violation_count", "capacity_violation_rate",
        "cost_per_demand_unit", "cost_per_fulfilled_unit", "cycle_service_level",
        "demand_period_service_level", "demand_units", "ending_on_hand_variance",
        "fill_rate", "fulfilled_units", "holding_cost", "inventory_turns",
        "lost_sales_units", "order_event_count", "order_units", "ordering_cost",
        "peak_ending_on_hand", "purchase_cost", "salvage_credit",
        "sku_order_line_count", "sku_order_quantity_variance",
        "sku_period_stockout_rate", "shortage_cost", "shortage_units",
        "stockout_period_rate", "terminal_backlog_cost", "terminal_backlog_units",
        "terminal_pipeline_cost", "terminal_pipeline_units", "total_cost", "waste_cost",
    ]
    assert utils.__all__ == [
        "update_inventory_with_orders", "process_demand", "DemandGenerator",
        "place_order_lines",
    ]
    assert visualization.__all__ == [
        "plot_inventory", "plot_demand_vs_orders", "plot_comparison",
        "plot_summary_comparison", "plot_simulation_dashboard",
        "plot_comparison_dashboard",
    ]


def test_public_output_schema_constants_are_available_from_public_namespaces():
    assert core.PROCESS_FLOW_COLUMNS == (
        "unique_id", "period", "date", "demand_period", "run_window", "process",
        "flow", "direction", "category", "phase", "quantity",
    )
    assert core.CALLBACK_AUDIT_COLUMNS == (
        "callback_position", "callback_module", "callback_class", "phase", "period",
        "date", "run_window", "initial_decision", "unique_id", "before_value",
        "after_value", "quantity_delta", "order_quantity", "reason", "source",
        "received_date", "lot_evidence",
    )
    assert core.ORDER_FRAME_COLUMNS == (
        "order_id", "unique_id", "supplier_id", "source", "order_period", "order_date",
        "due_period", "due_date", "lead_time", "ordered_quantity", "delivery_quantity",
        "status",
    )
    assert core.RUN_MANIFEST_REQUIRED_SECTIONS == (
        "run_id", "created_at_utc", "demand_source", "package", "policy",
        "opening_inventory", "run_settings", "dependencies",
    )
