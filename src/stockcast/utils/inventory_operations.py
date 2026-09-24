"""
Inventory operations utilities for multi-SKU inventory management.

This module provides functions for updating and manipulating inventory states
in a multi-SKU DataFrame-based system.
"""

import pandas as pd
import numpy as np
from stockcast.core._open_orders import _OpenOrderBook
from stockcast.core.data_structures import (
    InventoryStateDataFrame,
    OrderDecision,
    OrderLines,
    _identifier_sample,
    _require_identifiers,
)


def update_inventory_with_orders(
    inventory_state: InventoryStateDataFrame,
    orders: OrderDecision,
    policy: object | None = None,
) -> InventoryStateDataFrame:
    """
    Update inventory state by placing orders.

    This function updates multiple columns to track the order placement:
        - 'in_transit': Orders added to array at appropriate period offset
        - 'latest_order': Records the order quantity placed
        - 'target_level': Stores the target level (S) from the policy
        - 'allow_backorders': Transferred from policy (if provided)

    Positive-lead-time orders enter the future pipeline. Zero-lead-time orders
    are received immediately, clear prior backlog first, and can serve demand
    when this operation is called before fulfillment.

    Inventory Management Logic:
        - Lead time is automatically inferred from orders.lead_time (set by policy)
        - Backorder mode is automatically transferred from policy.allow_backorders
        - Positive-lead-time orders enter pipeline index lead_time - 1; zero-lead orders are received.
        - 'latest_order' and 'target_level' are updated for tracking
        - The 'inventory_position' increases immediately: IP = on_hand + sum(in_transit) - backorders
        - Physical on-hand increases at receipt, immediately for zero lead time

    Args:
        inventory_state: Current multi-SKU inventory state
        orders: Order decisions to execute (must include 'order_quantity', 'target_level', 'lead_time')
        policy: Optional policy object whose explicit allow_backorders setting is
            transferred to the returned state

    Returns:
        New InventoryStateDataFrame with updated 'in_transit', 'latest_order', 'target_level',
        and 'allow_backorders'

    Raises:
        ValueError: If orders.lead_time is None or if max_lead_time is insufficient

    Example:
        from stockcast import InventoryStateDataFrame, OrderDecision
        from stockcast.utils import update_inventory_with_orders

        # Current inventory state
        inventory = InventoryStateDataFrame(inventory_df, max_lead_time=14)

        # Generate orders from policy (automatically includes lead_time)
        orders = policy.predict(inventory, current_period=10)

        # Place orders (lead_time and allow_backorders inferred automatically)
        new_inventory = update_inventory_with_orders(inventory, orders, policy=policy)
        # → in_transit updated, latest_order and target_level recorded
        # → allow_backorders transferred from policy
    """
    allow_backorders, lead_time = _validate_order_decision(inventory_state, orders, policy)
    return _apply_orders(inventory_state, orders, allow_backorders, lead_time=lead_time)


def _validate_order_decision(
    inventory_state: InventoryStateDataFrame,
    orders: OrderDecision,
    policy: object | None,
) -> tuple[bool, int]:
    """Validate a per-SKU order decision against the state.

    Returns the backorder mode to apply and the decision's lead time.
    """
    inventory_state._validate_ready_state()

    # Infer lead_time from orders (set by policy)
    lead_time = orders.lead_time
    if lead_time is None:
        raise ValueError(
            "OrderDecision must have lead_time set. "
            "This should be automatically set by the policy's predict() method."
        )
    if not isinstance(lead_time, int) or isinstance(lead_time, bool) or lead_time < 0:
        raise ValueError("orders.lead_time must be an integer >= 0")

    # Preserve the state's explicit setting unless an explicit policy is supplied.
    allow_backorders = inventory_state.allow_backorders
    if policy is not None and hasattr(policy, 'allow_backorders'):
        allow_backorders = policy.allow_backorders

    # Validate that inventory_state has sufficient max_lead_time
    if inventory_state.max_lead_time < lead_time:
        raise ValueError(
            f"InventoryStateDataFrame.max_lead_time ({inventory_state.max_lead_time}) "
            f"must be >= orders.lead_time ({lead_time}). "
            f"Create InventoryStateDataFrame with max_lead_time >= {lead_time}."
        )
    # Get underlying DataFrames
    inv_df = inventory_state.get_dataframe()
    order_df = orders.get_dataframe()
    order_skus = _require_identifiers(
        order_df,
        inventory_state.sku_column,
        'orders',
        unique=True,
    )
    inventory_skus = _require_identifiers(
        inv_df,
        inventory_state.sku_column,
        'inventory_state',
        unique=True,
    )
    unknown_skus = order_skus - inventory_skus
    if unknown_skus:
        sample = _identifier_sample(unknown_skus)
        raise ValueError(f"orders contain unknown SKUs: {sample}")
    positive_orders = order_df['order_quantity'] > 0
    if positive_orders.any():
        timing = order_df.loc[
            positive_orders,
            ['order_period', 'expected_delivery_period'],
        ].apply(pd.to_numeric, errors='coerce')
        if timing.isna().any().any():
            raise ValueError("positive orders require order_period and expected_delivery_period")
        current_period = int(inv_df['period'].iloc[0])
        if not (timing['order_period'] == current_period).all():
            raise ValueError("positive orders.order_period must equal the inventory period")
        if not (
            timing['expected_delivery_period'] == timing['order_period'] + lead_time
        ).all():
            raise ValueError(
                "positive orders.expected_delivery_period must equal order_period + lead_time"
            )
    return allow_backorders, lead_time


def _apply_orders(
    inventory_state: InventoryStateDataFrame,
    orders: OrderDecision,
    allow_backorders: bool,
    *,
    lead_time: int | None = None,
    lines: pd.DataFrame | None = None,
) -> InventoryStateDataFrame:
    """Apply validated per-SKU order totals to a copy of the state.

    With ``lines=None`` each positive order is due ``lead_time`` periods after
    placement (the ``update_inventory_with_orders`` contract). Otherwise
    ``lines`` holds the deliveries behind the totals (columns ``sku``,
    ``supplier_id``, ``order_quantity``, ``due_period``, ``order_line``):
    deliveries due now are received immediately and the rest enter the
    pipeline slot of their due period. ``latest_order`` always accumulates
    the per-SKU totals of ``orders``.
    """
    inv_df = inventory_state.get_dataframe()
    order_df = orders.get_dataframe()
    current_period = int(inv_df['period'].iloc[0])
    # Merge order info into inventory DataFrame
    merged = inv_df.merge(
        order_df[[inventory_state.sku_column, 'order_quantity', 'target_level', 'order_period', 'expected_delivery_period']],
        on=inventory_state.sku_column,
        how='left'
    )

    # Missing SKU rows are explicit sparse no-order decisions.
    merged['order_quantity'] = merged['order_quantity'].fillna(0.0)
    # target_level from orders (if available) - otherwise keep existing value
    if 'target_level_x' in merged.columns:
        # Merge created target_level_x (from inventory) and target_level_y (from orders)
        # Use orders' target_level if available, otherwise keep existing
        merged['target_level'] = merged['target_level_y'].fillna(merged['target_level_x'])
        merged = merged.drop(columns=['target_level_x', 'target_level_y'])

    book = inventory_state._open_orders
    if lines is not None:
        # Explicit deliveries: immediate receipts and future pipeline slots.
        sku_column = inventory_state.sku_column
        immediate = lines[lines['due_period'] == current_period]
        future = lines[lines['due_period'] > current_period]
        received = merged[sku_column].map(
            immediate.groupby(sku_column, sort=False)['order_quantity'].sum()
        ).fillna(0.0).astype(float)
        if len(immediate):
            cleared = np.minimum(merged['backorders'], received) if allow_backorders else 0.0
            merged['backorders'] -= cleared
            merged['on_hand'] += received - cleared
            merged['latest_received'] += received
            merged['latest_backorders_fulfilled'] += cleared
        # New arrays for every SKU, as the per-SKU path creates them.
        pipelines = [pipeline.copy() for pipeline in merged['in_transit'].tolist()]
        if len(future):
            rows = pd.Index(merged[sku_column].tolist(), dtype=object).get_indexer(
                pd.Index(future[sku_column].tolist(), dtype=object)
            )
            slots = future['due_period'].to_numpy() - current_period - 1
            for row, slot, quantity in zip(rows, slots, future['order_quantity'].tolist()):
                pipelines[row][slot] += quantity
        merged['in_transit'] = pd.Series(pipelines, index=merged.index, dtype=object)
        if book is None:
            book = _OpenOrderBook.from_pipelines(
                inv_df[sku_column].tolist(),
                inventory_state._stacked_pipelines(),
                current_period,
            ).as_checked()
        placed = lines[lines['order_quantity'] > 0]
        line_codes, _ = pd.factorize(placed['order_line'], sort=False)
        ordered = placed.groupby('order_line', sort=False)['order_quantity'].transform('sum')
        book = book.placed_lines(
            sku=placed[sku_column].tolist(),
            supplier=placed['supplier_id'].tolist(),
            order_period=current_period,
            ordered=ordered.to_numpy(dtype=float),
            due=placed['due_period'].to_numpy(),
            quantity=placed['order_quantity'].to_numpy(dtype=float),
            line=line_codes,
        )
    elif lead_time == 0:
        # An accepted immediate order is both a purchase and a receipt. It
        # clears only existing backlog; current demand has not happened yet.
        received = merged['order_quantity']
        cleared = np.minimum(merged['backorders'], received) if allow_backorders else 0.0
        merged['backorders'] -= cleared
        merged['on_hand'] += received - cleared
        merged['latest_received'] += received
        merged['latest_backorders_fulfilled'] += cleared
    else:
        def add_to_pipeline(pipeline, quantity):
            pipeline = pipeline.copy()
            pipeline[lead_time - 1] += quantity
            return pipeline
        updated = pd.Series(
            [
                add_to_pipeline(pipeline, quantity)
                for pipeline, quantity in zip(
                    merged['in_transit'].tolist(),
                    merged['order_quantity'].astype(object).tolist(),
                )
            ],
            index=merged.index,
            dtype=object,
        )
        merged['in_transit'] = updated
    if lines is None and book is not None:
        placed = order_df[order_df['order_quantity'] > 0]
        count = len(placed)
        book = book.placed_lines(
            sku=placed[inventory_state.sku_column].tolist(),
            supplier=[None] * count,
            order_period=current_period,
            ordered=placed['order_quantity'].to_numpy(dtype=float),
            due=np.full(count, current_period + lead_time),
            quantity=placed['order_quantity'].to_numpy(dtype=float),
            line=np.arange(count),
        )

    # Accumulate every order line placed in the current period. This matters
    # when a calendar engine executes multiple supplier events before demand
    # advances and resets the period flow fields.
    merged['latest_order'] = merged['latest_order'] + merged['order_quantity']

    # Remove temporary columns used for calculation
    merged = merged.drop(columns=['order_quantity', 'order_period', 'expected_delivery_period'])

    # Return new InventoryStateDataFrame with updated values and preserve history
    return InventoryStateDataFrame(
        merged,
        sku_column=inventory_state.sku_column,
        max_lead_time=inventory_state.max_lead_time,
        allow_backorders=allow_backorders,  # Transfer allow_backorders from policy
        _history=inventory_state._history,  # Preserve accumulated history
        _open_orders=book,
    )


def place_order_lines(
    inventory_state: InventoryStateDataFrame,
    order_lines: OrderLines,
    policy: object | None = None,
) -> InventoryStateDataFrame:
    """
    Place order lines with their own suppliers and due periods.

    This is the order-level counterpart of ``update_inventory_with_orders``.
    Each row of ``order_lines`` is one scheduled delivery. Deliveries due in
    the current period are received immediately (clearing old backlog first,
    as zero-lead-time orders are); later deliveries enter the ``in_transit``
    slot of their due period. ``latest_order`` accumulates each SKU's total.
    ``target_level`` is left unchanged because order lines carry no policy
    target.

    The state's open-order book records every line (see
    ``InventoryStateDataFrame.open_orders()``). A state whose pipeline was
    given only as ``in_transit`` gets that pipeline attributed to opening
    orders first.

    Mapping from the per-SKU API: ``update_inventory_with_orders`` with lead
    time ``L`` is the same as one order line per positive SKU order with
    ``due_period = order_period + L``.

    Args:
        inventory_state: Ready multi-SKU inventory state
        order_lines: Order lines; every positive row must have
            ``order_period`` equal to the state period and
            ``due_period <= period + max_lead_time``
        policy: Optional policy whose ``allow_backorders`` setting is
            transferred to the returned state

    Returns:
        New InventoryStateDataFrame with updated pipeline, receipts and book

    Example:
        lines = OrderLines(pd.DataFrame({
            'unique_id': ['beans', 'beans'],
            'supplier_id': ['local', 'import'],
            'order_quantity': [20.0, 60.0],
            'order_period': [5, 5],
            'due_period': [6, 12],
        }))
        state = place_order_lines(state, lines)
    """
    if not isinstance(order_lines, OrderLines):
        raise TypeError("order_lines must be an OrderLines instance")
    inventory_state._validate_ready_state()
    allow_backorders = inventory_state.allow_backorders
    if policy is not None and hasattr(policy, 'allow_backorders'):
        allow_backorders = policy.allow_backorders
    lines = _state_order_lines(inventory_state, order_lines)
    sku_column = inventory_state.sku_column
    positive = lines[lines['order_quantity'] > 0]
    totals = positive.groupby(sku_column, sort=False)['order_quantity'].sum()
    orders = OrderDecision(
        pd.DataFrame({
            sku_column: pd.Series(totals.index.tolist(), dtype=lines[sku_column].dtype),
            'order_quantity': totals.to_numpy(dtype=float),
            'target_level': np.nan,
        }),
        sku_column=sku_column,
    )
    return _apply_orders(inventory_state, orders, allow_backorders, lines=positive)


def _state_order_lines(inventory_state: InventoryStateDataFrame, order_lines: OrderLines) -> pd.DataFrame:
    """Validate order lines against a state; return them keyed by the state SKU column."""
    lines = order_lines.get_dataframe()
    sku_column = inventory_state.sku_column
    if order_lines.sku_column != sku_column:
        lines = lines.rename(columns={order_lines.sku_column: sku_column})
    inventory_skus = _require_identifiers(
        inventory_state.data, sku_column, 'inventory_state', unique=True,
    )
    if len(lines):
        unknown = set(lines[sku_column].tolist()) - inventory_skus
        if unknown:
            raise ValueError(f"order_lines contain unknown SKUs: {_identifier_sample(unknown)}")
    current_period = int(inventory_state.data['period'].iloc[0])
    positive = lines['order_quantity'] > 0
    if (lines.loc[positive, 'order_period'] != current_period).any():
        raise ValueError("positive order_lines.order_period must equal the inventory period")
    horizon = current_period + inventory_state.max_lead_time
    if (lines.loc[positive, 'due_period'] > horizon).any():
        raise ValueError(
            f"order_lines.due_period must be <= period + max_lead_time ({horizon}); "
            "create InventoryStateDataFrame with a larger max_lead_time"
        )
    return lines


def process_demand(
    inventory_state: InventoryStateDataFrame,
    demand_df: pd.DataFrame,
    review_period: int,
    period_frequency: str,
    demand_column: str = 'y',
    date_column: str = 'date'
) -> InventoryStateDataFrame:
    """
    Process incoming demand and advance inventory state by one period.

    This is a convenience wrapper around InventoryStateDataFrame.process_demand().
    It advances the simulation by:
        1. Processing deliveries (orders arriving from in_transit)
        2. Processing demand (satisfying from on_hand)
        3. Tracking stockouts and backorders (controlled by inventory_state.allow_backorders)
        4. Shifting in_transit arrays (time advancement)
        5. Updating review period flags
        6. Advancing dates

    Args:
        inventory_state: Current multi-SKU inventory state
        demand_df: DataFrame with demand data (must include unique_id and demand columns)
                  Optional: date column for date tracking
        review_period: Review period for determining when to place orders
        period_frequency: Explicit pandas frequency for one simulation period
        demand_column: Column name containing demand values (default: 'y')
        date_column: Column name containing dates (default: 'date')

    Returns:
        New InventoryStateDataFrame with updated state for period + 1

    Note:
        Backorder behavior is controlled by the state's explicit
        inventory_state.allow_backorders value. SimulationEngine obtains that
        value from the configured policy before processing demand.

    Example:
        from stockcast import InventoryStateDataFrame
        from stockcast.utils import process_demand

        # Current inventory state (period 0)
        inventory = InventoryStateDataFrame(inventory_df, max_lead_time=14)

        # Incoming demand for period 1
        demand = pd.DataFrame({
            'unique_id': ['SKU_A', 'SKU_B'],
            'y': [50, 100],
            'date': [pd.Timestamp('2025-01-02'), pd.Timestamp('2025-01-02')]
        })

        # Process demand and advance to period 1
        new_inventory = process_demand(
            inventory_state=inventory,
            demand_df=demand,
            review_period=7,
            period_frequency="D",
        )
        # → period=1, inventory updated, stockouts tracked
        # → backorder behavior determined by inventory_state.allow_backorders
    """
    return inventory_state.process_demand(
        demand_df=demand_df,
        review_period=review_period,
        period_frequency=period_frequency,
        demand_column=demand_column,
        date_column=date_column
    )
