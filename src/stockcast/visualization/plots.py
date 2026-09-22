"""
Plotting functions for inventory simulation results.

All functions return matplotlib Axes (or arrays of Axes) and do NOT call plt.show().
"""

from typing import Any, Hashable, List, Optional, Tuple, Union

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.axes import Axes


def _prepare_event_frame(result, sku=None) -> pd.DataFrame:
    """Filter and aggregate a SimulationResult event frame."""
    if hasattr(result, 'to_event_frame'):
        h = result.to_event_frame()
    else:
        h = result.history.copy()
        h = h.rename(
            columns={
                'on_hand': 'ending_on_hand',
                'latest_incoming_demand': 'demand',
                'latest_order': 'order_quantity',
                'latest_shortage': 'shortage_units',
            }
        )
    if sku is not None:
        if pd.api.types.is_scalar(sku):
            sku = [sku]
        h = h[h['unique_id'].isin(sku)]

    n_skus = h['unique_id'].nunique()
    if n_skus > 1 and sku is None:
        # Aggregate across SKUs per period
        numeric_cols = [
            'ending_on_hand',
            'demand',
            'order_quantity',
            'shortage_units',
            'target_level',
            'safety_stock',
        ]
        cols_present = [c for c in numeric_cols if c in h.columns]
        h = h.groupby('period')[cols_present].sum().reset_index()
    return h


def _get_ax(ax, figsize: Tuple[float, float]):
    """Create figure/axes if not provided."""
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    return ax


def plot_inventory(
    result: Any,
    sku: Optional[Union[Hashable, List[Hashable]]] = None,
    ax: Optional[Axes] = None,
    figsize: Tuple[float, float] = (12, 5),
) -> Axes:
    """
    Plot on_hand inventory over time with stockout highlighting.

    Args:
        result: SimulationResult
        sku: Optional SKU id or list of SKU ids to filter. None = aggregate all.
        ax: Optional matplotlib Axes.
        figsize: Figure size if creating new figure.

    Returns:
        matplotlib Axes
    """
    ax = _get_ax(ax, figsize)
    h = _prepare_event_frame(result, sku)

    ax.plot(h['period'], h['ending_on_hand'], label='On Hand', color='steelblue', linewidth=1.5)
    ax.fill_between(h['period'], 0, h['ending_on_hand'], alpha=0.15, color='steelblue')

    # Highlight stockout periods
    stockout_periods = h[h['shortage_units'].fillna(0.0) > 0]['period'] if 'shortage_units' in h.columns else h[h['ending_on_hand'] == 0]['period']
    for p in stockout_periods:
        ax.axvspan(p - 0.5, p + 0.5, alpha=0.2, color='red', linewidth=0)

    ax.set_xlabel('Period')
    ax.set_ylabel('Inventory')
    ax.set_title(f'Inventory Level: {result.policy_name}')
    ax.legend()
    ax.grid(True, alpha=0.3)
    return ax


def plot_demand_vs_orders(
    result: Any,
    sku: Optional[Union[Hashable, List[Hashable]]] = None,
    ax: Optional[Axes] = None,
    figsize: Tuple[float, float] = (12, 5),
) -> Axes:
    """
    Plot demand and orders over time.

    Args:
        result: SimulationResult
        sku: Optional SKU id or list of SKU ids to filter. None = aggregate all.
        ax: Optional matplotlib Axes.
        figsize: Figure size if creating new figure.

    Returns:
        matplotlib Axes
    """
    ax = _get_ax(ax, figsize)
    h = _prepare_event_frame(result, sku)

    ax.plot(h['period'], h['demand'], label='Demand', color='tab:blue', linewidth=1.2)
    ax.plot(h['period'], h['order_quantity'], label='Orders', color='tab:orange', linewidth=1.2, alpha=0.8)

    ax.set_xlabel('Period')
    ax.set_ylabel('Quantity')
    ax.set_title(f'Demand vs Orders: {result.policy_name}')
    ax.legend()
    ax.grid(True, alpha=0.3)
    return ax


def plot_comparison(
    comparison_result: Any,
    metric: str = 'on_hand',
    sku: Optional[Union[Hashable, List[Hashable]]] = None,
    ax: Optional[Axes] = None,
    figsize: Tuple[float, float] = (12, 6),
) -> Axes:
    """
    Overlay a metric across multiple policies from a ComparisonResult.

    Args:
        comparison_result: ComparisonResult
        metric: Column name from history to plot (default: 'on_hand').
        sku: Optional SKU id or list of SKU ids to filter. None = aggregate all.
        ax: Optional matplotlib Axes.
        figsize: Figure size if creating new figure.

    Returns:
        matplotlib Axes
    """
    ax = _get_ax(ax, figsize)

    for label, result in comparison_result.results.items():
        h = _prepare_event_frame(result, sku)
        metric_column = {
            'on_hand': 'ending_on_hand',
            'latest_incoming_demand': 'demand',
            'latest_order': 'order_quantity',
            'latest_shortage': 'shortage_units',
        }.get(metric, metric)
        ax.plot(h['period'], h[metric_column], label=label, linewidth=1.2)

    ax.set_xlabel('Period')
    ax.set_ylabel(metric.replace('_', ' ').title())
    ax.set_title(f'Policy Comparison: {metric.replace("_", " ").title()}')
    ax.legend()
    ax.grid(True, alpha=0.3)
    return ax


def plot_summary_comparison(
    comparison_result: Any,
    metrics: Optional[List[str]] = None,
    figsize: Tuple[float, float] = (10, 6),
) -> Axes:
    """
    Grouped bar chart comparing summary metrics across policies.

    Args:
        comparison_result: ComparisonResult
        metrics: List of metric names to plot. None = all numeric metrics.
        figsize: Figure size.

    Returns:
        matplotlib Axes
    """
    summary_df = comparison_result.summary()

    if metrics is not None:
        summary_df = summary_df[metrics]

    n_policies = len(summary_df)
    n_metrics = len(summary_df.columns)
    x = np.arange(n_metrics)
    width = 0.8 / n_policies

    fig, ax = plt.subplots(figsize=figsize)
    for i, (policy_name, row) in enumerate(summary_df.iterrows()):
        offset = (i - n_policies / 2 + 0.5) * width
        ax.bar(x + offset, row.values, width, label=policy_name)

    ax.set_xticks(x)
    ax.set_xticklabels([m.replace('_', ' ').title() for m in summary_df.columns], rotation=15)
    ax.set_title('Policy Comparison: Summary Metrics')
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')
    return ax


def plot_simulation_dashboard(
    result: Any,
    sku: Optional[Union[Hashable, List[Hashable]]] = None,
    figsize: Tuple[float, float] = (13, 10),
    show_target: bool = True,
    show_safety_stock: bool = True,
) -> np.ndarray:
    """
    Multi-panel dashboard for a single simulation run.

    Creates a synchronized 4-panel figure:
        1. Inventory position — on_hand with target level and safety stock bands
        2. Demand and order decisions — demand line with order bars at decision points
        3. Shortage per period — bar chart of unmet demand
        4. Cumulative shortage — running total showing impact over time

    Panels share the x-axis for easy cross-referencing.

    Args:
        result: SimulationResult
        sku: Optional SKU id or list to filter. None = aggregate all.
        figsize: Overall figure size.
        show_target: Show target inventory level (S) as horizontal reference.
        show_safety_stock: Show safety stock band if data is available.

    Returns:
        Array of matplotlib Axes (one per panel).
    """
    h = _prepare_event_frame(result, sku)
    periods = h['period']

    # Determine which panels to show (shortage panels only if shortages exist)
    has_shortage = 'shortage_units' in h.columns and h['shortage_units'].sum() > 0
    n_panels = 4 if has_shortage else 2
    height_ratios = [3, 2, 1.5, 1.5] if has_shortage else [3, 2]

    fig, axes = plt.subplots(
        n_panels, 1, figsize=figsize, sharex=True, layout='constrained',
        gridspec_kw={'height_ratios': height_ratios[:n_panels], 'hspace': 0.08},
    )
    if n_panels == 1:
        axes = np.array([axes])

    # ── Panel 1: Inventory position ──
    ax1 = axes[0]
    ax1.plot(periods, h['ending_on_hand'], color='#2c7fb8', linewidth=1.5, label='On Hand')
    ax1.fill_between(periods, 0, h['ending_on_hand'], alpha=0.1, color='#2c7fb8')

    # Target level reference line
    if show_target and 'target_level' in h.columns:
        target = h['target_level']
        if target.max() > 0:
            ax1.plot(periods, target, color='#636363', linewidth=1, linestyle='--',
                     label='Target Level (S)', alpha=0.7)

    # Safety stock band
    if show_safety_stock and 'safety_stock' in h.columns:
        ss = h['safety_stock']
        if ss.max() > 0:
            ax1.fill_between(periods, 0, ss, alpha=0.12, color='#fdae6b', label='Safety Stock')

    # Mark stockout periods on the x-axis
    stockout_mask = h['shortage_units'].fillna(0.0) > 0 if 'shortage_units' in h.columns else h['ending_on_hand'] == 0
    if stockout_mask.any():
        stockout_p = periods[stockout_mask]
        ax1.scatter(stockout_p, [0] * len(stockout_p), color='#e34a33',
                    marker='x', s=30, zorder=5, label='Stockout')

    ax1.set_ylabel('Units')
    ax1.set_title(f'Simulation Dashboard: {result.policy_name}', fontsize=12, fontweight='bold')
    ax1.legend(loc='upper right', fontsize=8)
    ax1.grid(True, alpha=0.2)

    # ── Panel 2: Demand line + order decision bars ──
    ax2 = axes[1]
    ax2.plot(periods, h['demand'], color='#2c7fb8', linewidth=1.2,
             label='Demand', alpha=0.9)

    # Orders as narrow bars at decision points (only where orders > 0)
    order_mask = h['order_quantity'] > 0
    if order_mask.any():
        order_periods = periods[order_mask]
        order_vals = h.loc[order_mask, 'order_quantity']
        bar_width = max(0.4, (periods.max() - periods.min()) / len(periods) * 0.6)
        ax2.bar(order_periods, order_vals, width=bar_width, color='#fdae6b',
                alpha=0.7, label='Order Placed', edgecolor='#e6550d', linewidth=0.5)

    ax2.set_ylabel('Quantity')
    ax2.legend(loc='upper right', fontsize=8)
    ax2.grid(True, alpha=0.2)

    if not has_shortage:
        ax2.set_xlabel('Period')
        return axes

    # ── Panel 3: Per-period shortage ──
    ax3 = axes[2]
    shortage = h['shortage_units']
    shortage_mask = shortage > 0
    if shortage_mask.any():
        bar_width = max(0.4, (periods.max() - periods.min()) / len(periods) * 0.6)
        ax3.bar(periods[shortage_mask], shortage[shortage_mask],
                width=bar_width, color='#e34a33', alpha=0.65, edgecolor='#b30000', linewidth=0.5)
    ax3.set_ylabel('Unmet Demand')
    ax3.grid(True, alpha=0.2)

    # ── Panel 4: Cumulative shortage ──
    ax4 = axes[3]
    cumulative = shortage.cumsum()
    ax4.plot(periods, cumulative, color='#e34a33', linewidth=1.3)
    ax4.fill_between(periods, 0, cumulative, alpha=0.1, color='#e34a33')
    ax4.set_ylabel('Cumulative')
    ax4.set_xlabel('Period')
    ax4.grid(True, alpha=0.2)

    return axes


def plot_comparison_dashboard(
    comparison_result: Any,
    sku: Optional[Union[Hashable, List[Hashable]]] = None,
    figsize: Tuple[float, float] = (13, 8),
) -> np.ndarray:
    """
    Side-by-side multi-metric comparison across policies.

    Creates a 3-panel figure:
        1. Inventory levels overlaid per policy
        2. Cumulative shortage per policy
        3. Fill rate evolution over time per policy

    Args:
        comparison_result: ComparisonResult
        sku: Optional SKU id or list to filter. None = aggregate all.
        figsize: Overall figure size.

    Returns:
        Array of matplotlib Axes.
    """
    fig, axes = plt.subplots(
        3, 1, figsize=figsize, sharex=True, layout='constrained',
        gridspec_kw={'height_ratios': [3, 2, 2], 'hspace': 0.08},
    )

    colors = plt.rcParams['axes.prop_cycle'].by_key()['color']

    for i, (label, result) in enumerate(comparison_result.results.items()):
        h = _prepare_event_frame(result, sku)
        periods = h['period']
        color = colors[i % len(colors)]

        # Panel 1: Inventory levels
        axes[0].plot(periods, h['ending_on_hand'], color=color, linewidth=1.3,
                     label=label, alpha=0.85)

        # Panel 2: Cumulative shortage
        if 'shortage_units' in h.columns:
            cum_shortage = h['shortage_units'].cumsum()
            axes[1].plot(periods, cum_shortage, color=color, linewidth=1.3,
                         label=label, alpha=0.85)

        # Panel 3: Rolling fill rate
        if 'shortage_units' in h.columns and 'demand' in h.columns:
            demand_cum = h['demand'].cumsum()
            shortage_cum = h['shortage_units'].cumsum()
            # Avoid division by zero in early periods
            fill_rate = np.where(demand_cum > 0, 1 - shortage_cum / demand_cum, 1.0)
            axes[2].plot(periods, fill_rate, color=color, linewidth=1.3,
                         label=label, alpha=0.85)

    axes[0].set_ylabel('On Hand')
    axes[0].set_title('Policy Comparison Dashboard', fontsize=12, fontweight='bold')
    axes[0].legend(loc='upper right', fontsize=8)
    axes[0].grid(True, alpha=0.2)

    axes[1].set_ylabel('Cumulative Shortage')
    axes[1].grid(True, alpha=0.2)

    axes[2].set_ylabel('Fill Rate')
    axes[2].set_xlabel('Period')
    axes[2].set_ylim(-0.05, 1.05)
    axes[2].axhline(y=1.0, color='#636363', linewidth=0.8, linestyle=':', alpha=0.5)
    axes[2].grid(True, alpha=0.2)

    return axes
