"""
Core data structures for the DataFrame-based multi-SKU inventory management system.

This module provides:
    - InventoryStateDataFrame: Multi-SKU inventory state container
    - OrderDecision: Multi-SKU order decision container

The supported staging import boundary is the ``stockcast`` package. Legacy
duplicate top-level source trees are not part of that boundary.
"""
from typing import Dict, Optional, Any, List, Union
import numpy as np
import pandas as pd


# ============================================================================
# MULTI-SKU DATAFRAME-BASED STRUCTURES
# ============================================================================

def _require_finite_nonnegative(df: pd.DataFrame, columns: List[str], frame_name: str) -> None:
    """Validate finite, non-negative numeric values in selected columns."""
    for column in columns:
        if column not in df.columns:
            continue
        values = pd.to_numeric(df[column], errors='coerce')
        if values.isna().any():
            raise ValueError(f"{frame_name}.{column} must not contain null or non-numeric values")
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"{frame_name}.{column} must contain finite values")
        if (values < 0).any():
            raise ValueError(f"{frame_name}.{column} must be non-negative")


def _require_forward_frequency(value: str, name: str):
    """Return a pandas offset that advances time strictly forward."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty pandas frequency")
    try:
        offset = pd.tseries.frequencies.to_offset(value)
    except ValueError as exc:
        raise ValueError(f"invalid {name} '{value}'") from exc

    reference = pd.Timestamp("2000-01-03")
    try:
        next_date = reference + offset
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {name} '{value}'") from exc
    if next_date <= reference:
        raise ValueError(f"{name} must advance time strictly forward")
    return offset


def _require_unique(df: pd.DataFrame, columns: List[str], frame_name: str) -> None:
    """Validate uniqueness for a set of key columns."""
    duplicates = int(df.duplicated(columns).sum())
    if duplicates:
        raise ValueError(f"{frame_name} contains {duplicates} duplicate rows for {columns}")


def _identifier_sample(values) -> list:
    """Return a deterministic, representation-preserving identifier sample."""
    return sorted(values, key=lambda value: (type(value).__name__, repr(value)))[:5]


def _require_identifiers(
    df: pd.DataFrame,
    column: str,
    frame_name: str,
    *,
    unique: bool,
) -> set:
    """Validate identifiers without coercing their values or types."""
    if column not in df.columns:
        raise ValueError(f"{frame_name} is missing identifier column '{column}'")
    values = df[column]
    if values.isna().any():
        raise ValueError(f"{frame_name}.{column} must not contain missing values")
    if values.map(lambda value: isinstance(value, str) and not value.strip()).any():
        raise ValueError(f"{frame_name}.{column} must not contain blank strings")
    try:
        for value in values:
            hash(value)
    except TypeError as exc:
        raise ValueError(f"{frame_name}.{column} identifiers must be hashable") from exc
    value_types = {type(value) for value in values}
    if len(value_types) > 1:
        names = sorted(value_type.__name__ for value_type in value_types)
        raise ValueError(
            f"{frame_name}.{column} must use one identifier type; got {names}"
        )
    if unique:
        _require_unique(df, [column], frame_name)
    return set(values.tolist())

class InventoryStateDataFrame:
    """
    Multi-SKU inventory state represented as a DataFrame.

    This class manages inventory positions for multiple SKUs simultaneously,
    designed for production use with forecasting pipelines.

    Columns:
        - unique_id: SKU identifier
        - on_hand: Physical inventory available (includes cycle stock + safety stock)
        - safety_stock: Safety buffer portion of inventory (informational)
        - target_level: Target inventory level from policy (S in Order-Up-To policy)
        - latest_order: Most recent order quantity placed
        - latest_received: Quantity delivered in the current period
        - latest_fulfilled: Current-period demand fulfilled from on-hand stock
        - latest_backorders_fulfilled: Prior backlog cleared by current receipts
        - latest_incoming_demand: Most recent demand quantity processed
        - backorders: Unfulfilled customer demand
        - period: Current time period
        - date: Date corresponding to the current period
        - in_transit: Array tracking orders in transit by period offset (for simulation)
        - is_review_period: Boolean flag indicating if current period is a review period

    Attributes:
        - has_stockout: Boolean flag indicating if ANY SKU had stockout in current period
        - has_backorder: Boolean flag indicating if ANY SKU has unfulfilled backorders

    All columns are included in the DataFrame. Missing scientific opening-state
    fields remain incomplete and simulation rejects them. Use
    ``initialize_zero`` or ``initialize_from_observed`` to declare zero backlog
    and pipeline explicitly. Current-period flow fields begin at zero, and
    ``is_review_period`` begins false.

    Example:
        # Create from existing inventory data
        inventory_df = pd.DataFrame({
            'unique_id': ['SKU_A', 'SKU_B', 'SKU_C'],
            'on_hand': [100, 250, 50],
            'safety_stock': [20, 50, 10],
            'backorders': [0, 0, 10],
            'period': [0, 0, 0]
        })

        inventory_state = InventoryStateDataFrame(inventory_df, max_lead_time=14)

        # Calculate inventory position for all SKUs
        ip_df = inventory_state.inventory_position()
    """

    def __init__(self,
                 data: Union[pd.DataFrame, List, np.ndarray, Dict],
                 max_lead_time: int,
                 sku_column: str = 'unique_id',
                 start_date: Optional[pd.Timestamp] = None,
                 allow_backorders: Optional[bool] = None,
                 _history: Optional[List[pd.DataFrame]] = None):
        """
        Initialize multi-SKU inventory state from DataFrame or SKU list.

        Args:
            data: Either:
                  - DataFrame with inventory data (must have sku_column)
                  - List/array/dict of unique SKU identifiers
            max_lead_time: Maximum lead time for tracking in-transit orders
            sku_column: Name of the SKU identifier column (default: 'unique_id')
            start_date: Optional explicit opening date. If omitted, one complete
                date may be retained from a state DataFrame. No current-date
                fallback is used.
            allow_backorders: Explicit backorder convention. It may remain
                unset until ``SimulationEngine`` supplies the policy setting.
            _history: Internal parameter for transferring history between instances
        """
        if not isinstance(max_lead_time, int) or isinstance(max_lead_time, bool) or max_lead_time < 1:
            raise ValueError("max_lead_time must be an integer >= 1")
        if allow_backorders is not None and not isinstance(allow_backorders, bool):
            raise ValueError("allow_backorders must be True, False, or unset")
        if not isinstance(sku_column, str) or not sku_column.strip():
            raise ValueError("sku_column must be a non-empty string")

        self.sku_column = sku_column
        self.max_lead_time = max_lead_time
        self.allow_backorders = allow_backorders
        self._history = _history if _history is not None else []

        # === STEP 1: Convert input to DataFrame ===
        if isinstance(data, pd.DataFrame):
            # Input is already a DataFrame
            df_input = data.copy()

            # Validate SKU column exists
            if sku_column not in df_input.columns:
                raise ValueError(f"SKU column '{sku_column}' not found in DataFrame")

        elif isinstance(data, dict):
            # Input is a dictionary - use keys or values as SKU IDs
            if sku_column in data:
                # Dictionary has sku_column as key with list of SKUs
                sku_ids = data[sku_column]
            else:
                # Use dictionary keys as SKU IDs
                sku_ids = list(data.keys())
            df_input = pd.DataFrame({sku_column: sku_ids})

        elif isinstance(data, (list, np.ndarray)):
            # Input is a list or array of SKU IDs
            df_input = pd.DataFrame({sku_column: data})

        else:
            raise TypeError(f"data must be DataFrame, list, array, or dict, got {type(data)}")

        if df_input.empty:
            raise ValueError(
                "inventory SKU universe must be non-empty; dynamic SKU addition "
                "is not supported in this release"
            )
        _require_identifiers(df_input, sku_column, 'inventory_state', unique=True)

        # Preserve an explicit opening date, or one complete date already in a
        # state frame. Missing or conflicting dates remain invalid until an
        # initializer supplies an explicit date.
        inferred_date = pd.NaT
        if start_date is not None:
            try:
                inferred_date = pd.Timestamp(start_date)
            except (TypeError, ValueError) as exc:
                raise ValueError("start_date must be a valid timestamp") from exc
            if pd.isna(inferred_date):
                raise ValueError("start_date must be a valid timestamp")
        elif 'date' in df_input.columns:
            valid_dates = pd.to_datetime(df_input['date'], errors='coerce')
            if valid_dates.notna().all() and valid_dates.nunique() == 1:
                inferred_date = valid_dates.iloc[0]

        # Store inferred date as instance attribute for preservation across initialization methods
        self._inferred_start_date = inferred_date

        # Create copy to avoid modifying original
        self._source_data = df_input.copy()
        self.data = df_input

        # Define all inventory state columns
        numeric_columns = [
            'on_hand',
            'safety_stock',
            'target_level',
            'latest_order',
            'latest_received',
            'latest_fulfilled',
            'latest_backorders_fulfilled',
            'latest_incoming_demand',
            'latest_shortage',
            'backorders',
            'period',
        ]
        boolean_columns = ['is_review_period']
        special_columns = ['in_transit', 'date']

        # Define all valid inventory columns
        valid_inventory_columns = [sku_column] + numeric_columns + boolean_columns + special_columns

        # Flow fields are zero before the first event. Scientific opening-state
        # fields remain missing until an initializer or complete input supplies
        # them; the simulation-ready validator rejects incomplete state.
        initial_flow_columns = {
            'latest_order',
            'latest_received',
            'latest_fulfilled',
            'latest_backorders_fulfilled',
            'latest_incoming_demand',
            'latest_shortage',
        }
        for col in numeric_columns:
            if col not in self.data.columns:
                if col in initial_flow_columns:
                    self.data[col] = 0.0
                else:
                    self.data[col] = np.nan

        # Add missing boolean columns with False defaults
        for col in boolean_columns:
            if col not in self.data.columns:
                self.data[col] = False

        # Add special columns
        if 'in_transit' not in self.data.columns:
            self.data['in_transit'] = [None for _ in range(len(self.data))]

        # Set date column with inferred date
        if 'date' not in self.data.columns:
            # Date column doesn't exist, create it with inferred date
            self.data['date'] = inferred_date
        else:
            self.data['date'] = pd.to_datetime(self.data['date'], errors='coerce')

        # Drop columns that are not part of the inventory state schema
        # This removes columns like 'y' (demand) from historical data
        columns_to_drop = [col for col in self.data.columns if col not in valid_inventory_columns]
        if columns_to_drop:
            self.data = self.data.drop(columns=columns_to_drop)

        # Initialize class-level attributes
        self.has_stockout = False
        self.has_backorder = False


    def inventory_position(self) -> pd.DataFrame:
        """
        Calculate inventory position (IP) for all SKUs.

        Inventory Position = on_hand + total_in_transit - backorders

        This represents the total inventory committed to satisfy demand,
        including both physical stock and outstanding orders in transit.

        Returns:
            DataFrame with all original columns plus 'inventory_position' column
        """
        self._validate_ready_state()
        result = self.data.copy()

        # Calculate total in_transit per SKU (sum of array)
        result['total_in_transit'] = result['in_transit'].apply(
            lambda x: np.sum(x) if isinstance(x, np.ndarray) else 0.0
        )

        # Calculate inventory position
        result['inventory_position'] = (
            result['on_hand'] +
            result['total_in_transit'] -
            result['backorders']
        )

        return result

    def get_dataframe(self) -> pd.DataFrame:
        """
        Get the underlying DataFrame (latest state only).

        Returns:
            Copy of the internal DataFrame with current period state
        """
        return self.data.copy()

    def get_history(self) -> pd.DataFrame:
        """
        Get the complete historical DataFrame with all periods stacked.

        Returns all accumulated historical states concatenated into a single DataFrame.
        Each row represents a SKU at a specific period. History accumulates automatically
        during process_demand() calls.

        Returns:
            DataFrame with all historical states stacked (unique_id × period combinations)
            Returns empty DataFrame if no history has been accumulated

        Example:
            # After running simulation for 3 periods
            latest = inventory.get_dataframe()  # 9 rows (current period only)
            history = inventory.get_history()   # 27 rows (9 SKUs × 3 periods)
        """
        if not self._history:
            return pd.DataFrame()
        return pd.concat(self._history, ignore_index=True)

    def clear_history(self) -> None:
        """
        Clear the accumulated history.

        Useful for resetting history tracking without creating a new instance.
        """
        self._history = []

    def _validate_ready_state(self) -> None:
        """Validate that state columns are usable for simulation or ordering."""
        if not isinstance(self.allow_backorders, bool):
            raise ValueError(
                "inventory_state.allow_backorders must be explicitly supplied "
                "or set by SimulationEngine from the policy"
            )
        _require_unique(self.data, [self.sku_column], 'inventory_state')
        _require_finite_nonnegative(
            self.data,
            [
                'on_hand',
                'safety_stock',
                'latest_order',
                'latest_received',
                'latest_fulfilled',
                'latest_backorders_fulfilled',
                'latest_incoming_demand',
                'latest_shortage',
                'backorders',
                'period',
            ],
            'inventory_state',
        )
        backorders = pd.to_numeric(self.data['backorders'], errors='coerce')
        on_hand = pd.to_numeric(self.data['on_hand'], errors='coerce')
        if not self.allow_backorders and (backorders > 0).any():
            raise ValueError(
                "inventory_state.backorders must be zero when allow_backorders=False"
            )
        if ((on_hand > 0) & (backorders > 0)).any():
            raise ValueError(
                "inventory_state cannot contain positive on_hand and backorders "
                "for the same SKU"
            )
        periods = self.data['period'].to_numpy(dtype=float)
        if not np.equal(periods, np.floor(periods)).all() or len(set(periods)) != 1:
            raise ValueError("inventory_state.period must be one complete integer period")
        dates = pd.to_datetime(self.data['date'], errors='coerce')
        if dates.isna().any() or dates.nunique() != 1:
            raise ValueError("inventory_state.date must contain one complete opening date")
        bad_in_transit = []
        for idx, value in self.data['in_transit'].items():
            if not isinstance(value, np.ndarray):
                bad_in_transit.append(idx)
                continue
            if len(value) != self.max_lead_time:
                bad_in_transit.append(idx)
                continue
            if not np.isfinite(value).all() or (value < 0).any():
                bad_in_transit.append(idx)
        if bad_in_transit:
            raise ValueError("inventory_state.in_transit must contain finite, non-negative arrays of length max_lead_time")

    def __repr__(self) -> str:
        """String representation showing number of SKUs and key statistics."""
        n_skus = len(self.data)
        total_on_hand = self.data['on_hand'].sum()
        total_safety_stock = self.data['safety_stock'].sum()
        total_backorders = self.data['backorders'].sum()

        return (f"InventoryStateDataFrame(n_skus={n_skus}, "
                f"total_on_hand={total_on_hand:.0f}, "
                f"total_safety_stock={total_safety_stock:.0f}, "
                f"total_backorders={total_backorders:.0f}, "
                f"has_stockout={self.has_stockout}, "
                f"has_backorder={self.has_backorder})")

    def initialize_zero(self, start_date: Optional[pd.Timestamp] = None) -> 'InventoryStateDataFrame':
        """
        Initialize all inventory levels to zero.

        Sets all numeric inventory columns (on_hand, safety_stock, backorders, target_level, latest_order) to 0
        and period to 0. Useful for starting fresh simulations with empty inventory.

        Args:
            start_date: Explicit opening date, or omit only when the constructor
                retained one complete state date

        Returns:
            self (for method chaining)

        Example:
            inventory = InventoryStateDataFrame(
                pd.DataFrame({'unique_id': ['SKU_A', 'SKU_B']}),
                max_lead_time=7,
            )
            inventory.initialize_zero(start_date=pd.Timestamp('2025-01-01'))
            # → on_hand=0, safety_stock=0, target_level=0, latest_order=0, backorders=0 for all SKUs
        """
        # Ensure we have only one row per SKU (prevents duplicates when historical data passed to __init__)
        unique_skus = self.data[[self.sku_column]].drop_duplicates().reset_index(drop=True)
        self.data = unique_skus

        self.data['on_hand'] = 0.0
        self.data['safety_stock'] = 0.0
        self.data['target_level'] = 0.0
        self.data['latest_order'] = 0.0
        self.data['latest_received'] = 0.0
        self.data['latest_fulfilled'] = 0.0
        self.data['latest_backorders_fulfilled'] = 0.0
        self.data['latest_incoming_demand'] = 0.0
        self.data['latest_shortage'] = 0.0
        self.data['backorders'] = 0.0
        self.data['period'] = 0.0
        self.data['date'] = start_date if start_date is not None else self._inferred_start_date
        self.data['is_review_period'] = False

        # Re-initialize in_transit arrays for each SKU
        self.data['in_transit'] = [np.zeros(self.max_lead_time) for _ in range(len(self.data))]

        # Reset class-level attributes
        self.has_stockout = False
        self.has_backorder = False

        return self

    def initialize_from_observed(
        self,
        opening_stock_df: pd.DataFrame,
        *,
        on_hand_column: str,
        start_date: pd.Timestamp,
        sku_column: Optional[str] = None,
    ) -> 'InventoryStateDataFrame':
        """Initialize from one explicit observed on-hand value per SKU."""
        sku_column = sku_column or self.sku_column
        if not isinstance(opening_stock_df, pd.DataFrame) or opening_stock_df.empty:
            raise ValueError("opening_stock_df must be a non-empty pandas DataFrame")
        missing_columns = [
            column
            for column in [sku_column, on_hand_column]
            if column not in opening_stock_df.columns
        ]
        if missing_columns:
            raise ValueError(f"opening_stock_df is missing columns: {missing_columns}")
        supplied_skus = _require_identifiers(
            opening_stock_df,
            sku_column,
            'opening_stock_df',
            unique=True,
        )
        _require_finite_nonnegative(opening_stock_df, [on_hand_column], 'opening_stock_df')
        expected_skus = _require_identifiers(
            self.data,
            self.sku_column,
            'inventory_state',
            unique=True,
        )
        if supplied_skus != expected_skus:
            raise ValueError(
                "opening_stock_df must contain exactly the inventory SKUs; "
                f"missing={_identifier_sample(expected_skus - supplied_skus)}, "
                f"extra={_identifier_sample(supplied_skus - expected_skus)}"
            )
        try:
            opening_date = pd.Timestamp(start_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("start_date must be a valid timestamp") from exc
        if pd.isna(opening_date):
            raise ValueError("start_date must be a valid timestamp")

        stock_by_sku = opening_stock_df.set_index(sku_column)[on_hand_column]
        self.initialize_zero(start_date=opening_date)
        self.data['on_hand'] = self.data[self.sku_column].map(stock_by_sku).astype(float)
        return self

    def process_demand(self,
                      demand_df: pd.DataFrame,
                      review_period: int,
                      period_frequency: str,
                      demand_column: str = 'y',
                      date_column: Optional[str] = 'date',
                      sku_column: Optional[str] = None) -> 'InventoryStateDataFrame':
        """
        Process incoming demand and advance the simulation by one period.

        TIMING NOTE: Period advancement happens at the START of this method.
        This ensures that when inventory_position() is called during review periods,
        the system is already at the new period, making order placement timing correct.

        This method:
        1. Advances period by 1 (FIRST - this is the period we're entering)
        2. Updates review period flag (is this period a review period?)
        3. Updates dates
        4. Processes deliveries from in_transit (orders arriving)
        5. Processes demand (satisfies from on_hand, tracks stockouts)
        6. Updates backorders if allowed (uses self.allow_backorders)
        7. Shifts in_transit arrays (time advancement)

        Args:
            demand_df: DataFrame with demand and one explicit period date
            review_period: Review period for determining is_review_period flag
            period_frequency: Explicit pandas frequency for one simulation period.
            demand_column: Column name containing demand values (default: 'y')
            date_column: Required date column name (default: 'date')
            sku_column: Column name for SKU identifier (uses self.sku_column if None)

        Returns:
            New InventoryStateDataFrame with updated state for period + 1

        Note:
            Backorder behavior is controlled by self.allow_backorders, which is set
            automatically by the policy via update_inventory_with_orders().

        Example:
            # Process demand for next period
            demand_df = pd.DataFrame({
                'unique_id': ['SKU_A', 'SKU_B'],
                'y': [50, 100],
                'date': [pd.Timestamp('2025-01-02'), pd.Timestamp('2025-01-02')]
            })

            new_inventory = inventory.process_demand(
                demand_df=demand_df,
                review_period=7,
                period_frequency="D",
            )
            # → period incremented FIRST, then inventory updated, stockouts tracked
            # → backorder behavior determined by self.allow_backorders
        """
        if sku_column is None:
            sku_column = self.sku_column

        if not isinstance(review_period, int) or isinstance(review_period, bool) or review_period < 1:
            raise ValueError("review_period must be an integer >= 1")
        period_offset = _require_forward_frequency(
            period_frequency,
            "period_frequency",
        )

        self._validate_ready_state()

        # Validate required columns
        if sku_column not in demand_df.columns:
            raise ValueError(f"sku_column '{sku_column}' not found in demand_df")
        if demand_column not in demand_df.columns:
            raise ValueError(f"demand_column '{demand_column}' not found in demand_df")
        demand_skus = _require_identifiers(
            demand_df,
            sku_column,
            'demand_df',
            unique=True,
        )
        inventory_skus = _require_identifiers(
            self.data,
            sku_column,
            'inventory_state',
            unique=True,
        )
        unknown_skus = demand_skus - inventory_skus
        if unknown_skus:
            sample = _identifier_sample(unknown_skus)
            raise ValueError(f"demand_df contains unknown SKUs: {sample}")
        missing_skus = inventory_skus - demand_skus
        if missing_skus:
            sample = _identifier_sample(missing_skus)
            raise ValueError(f"demand_df is missing inventory SKUs: {sample}")
        _require_finite_nonnegative(demand_df, [demand_column], 'demand_df')
        if not date_column or date_column not in demand_df.columns:
            raise ValueError("demand_df must contain an explicit date column")
        demand_dates = pd.to_datetime(demand_df[date_column], errors='coerce')
        if demand_dates.isna().any() or demand_dates.nunique() != 1:
            raise ValueError("demand_df must contain one complete date for the period")
        state_dates = pd.to_datetime(self.data['date'], errors='coerce')
        if state_dates.isna().any() or state_dates.nunique() != 1:
            raise ValueError("inventory state must contain one complete opening date")
        expected_date = state_dates.iloc[0] + period_offset
        demand_date = demand_dates.iloc[0]
        if demand_date != expected_date:
            raise ValueError(
                f"demand date {demand_date} does not match expected next date "
                f"{expected_date} for frequency '{period_frequency}'"
            )

        # Create a copy of current state
        new_data = self.data.copy()
        new_data['latest_order'] = 0.0

        # === STEP 1: Advance period FIRST ===
        # This is the period we're ENTERING, not the period we're leaving
        current_period = new_data['period'].iloc[0]
        new_period = current_period + 1
        new_data['period'] = new_period

        # === STEP 2: Update review period flag EARLY ===
        # Check if this new period is a review period
        new_data['is_review_period'] = (new_period % review_period == 0)

        # === STEP 3: Update date EARLY ===
        # Demand dates are required and were validated against the explicit frequency.
        demand_subset = demand_df[[sku_column, demand_column]].copy()
        demand_subset[date_column] = demand_dates.to_numpy()
        new_data['date'] = demand_date

        # === STEP 4: Process deliveries from in_transit ===
        def process_delivery(row):
            """Process deliveries for a single SKU.

            Arriving stock clears existing backorders first, then remainder
            goes to on_hand. This prevents backorders from permanently
            suppressing inventory position.
            """
            in_transit = row['in_transit'].copy()

            # Check if any orders are arriving (in_transit[0])
            arriving_qty = in_transit[0]

            # Clear backorders with arriving stock first
            backorders = row['backorders']
            if self.allow_backorders and arriving_qty > 0 and backorders > 0:
                cleared = min(arriving_qty, backorders)
                new_backorders = backorders - cleared
                new_on_hand = row['on_hand'] + (arriving_qty - cleared)
            else:
                cleared = 0.0
                new_backorders = backorders
                new_on_hand = row['on_hand'] + arriving_qty

            # Shift in_transit array left (advance time)
            in_transit = np.roll(in_transit, -1)
            in_transit[-1] = 0.0  # Last position awaits new order

            return pd.Series({
                'on_hand': new_on_hand,
                'in_transit': in_transit,
                'arriving_qty': arriving_qty,
                'backorders_fulfilled': cleared,
                'backorders': new_backorders,
            })

        delivery_updates = new_data.apply(process_delivery, axis=1)
        new_data['on_hand'] = delivery_updates['on_hand']
        new_data['in_transit'] = delivery_updates['in_transit']
        new_data['backorders'] = delivery_updates['backorders']
        new_data['latest_received'] = delivery_updates['arriving_qty']
        new_data['latest_backorders_fulfilled'] = delivery_updates['backorders_fulfilled']

        # === STEP 5: Merge demand data ===
        # demand_subset already created above, just merge the demand column
        new_data = new_data.merge(
            demand_subset[[sku_column, demand_column]],
            on=sku_column,
            how='left',
            suffixes=('', '_demand')
        )

        if new_data[demand_column].isna().any():
            raise AssertionError("validated demand grid became incomplete during merge")

        # Store incoming demand in latest_incoming_demand column
        new_data['latest_incoming_demand'] = new_data[demand_column]

        # === STEP 6: Process demand and calculate stockouts ===
        # Calculate satisfied demand and shortages
        new_data['satisfied_demand'] = new_data[[demand_column, 'on_hand']].min(axis=1)
        new_data['shortage'] = new_data[demand_column] - new_data['satisfied_demand']
        new_data['latest_fulfilled'] = new_data['satisfied_demand']

        # Track if ANY SKU had stockout this period (before updating backorders)
        had_stockout = (new_data['shortage'] > 0).any()

        # Update on_hand (subtract satisfied demand)
        new_data['on_hand'] = new_data['on_hand'] - new_data['satisfied_demand']

        # === STEP 7: Update backorders ===
        if self.allow_backorders:
            # Add new shortages to existing backorders
            new_data['backorders'] = new_data['backorders'] + new_data['shortage']
        else:
            # Lost sales - don't track backorders
            new_data['backorders'] = 0.0

        # === STEP 8: Save shortage and clean up temporary columns ===
        new_data['latest_shortage'] = new_data['shortage']
        new_data = new_data.drop(columns=[demand_column, 'satisfied_demand', 'shortage'])

        # === STEP 9: Create new inventory state and transfer history ===
        new_inventory = InventoryStateDataFrame(
            new_data,
            sku_column=sku_column,
            max_lead_time=self.max_lead_time,
            allow_backorders=self.allow_backorders,  # Preserve backorder setting
            _history=self._history  # Transfer accumulated history
        )

        # === STEP 10: Update class-level attributes ===
        # Set stockout flag (was there a shortage in this period?)
        new_inventory.has_stockout = had_stockout
        # Set backorder flag (are there any unfulfilled backorders currently?)
        new_inventory.has_backorder = (new_data['backorders'] > 0).any()
        new_inventory._history.append(new_inventory.data.copy())

        return new_inventory


class OrderDecision:
    """
    Multi-SKU order decision represented as a DataFrame.

    This class represents ordering decisions for multiple SKUs,
    typically generated by inventory policies (e.g., OrderUpToPolicy.predict()).

    Columns:
        - unique_id: SKU identifier
        - order_quantity: Amount to order for each SKU
        - target_level: Target inventory level (S in Order-Up-To policy)
        - inventory_position: Current inventory position when order was calculated
        - reorder_point: Reorder point (s in continuous review policies)
        - order_period: Period when order was placed
        - expected_delivery_period: Period when order is expected to arrive

    ``order_quantity`` is mandatory. Optional diagnostic columns are added with
    NaN when they are not applicable.

    Attributes:
        - lead_time: Lead time from the policy (L)
        - review_period: Review period from the policy (R)

    Example:
        # Typically created from policy output
        policy = OrderUpToPolicy(
            lead_time=7,
            review_period=7,
            service_level=0.95,
            allow_backorders=False,
        )
        policy.fit(forecast_df)

        # predict() automatically creates OrderDecision with policy parameters
        orders = policy.predict(inventory_state_df, current_period=decision_period)
        # orders.lead_time = 7, orders.review_period = 7

        # Access order quantities
        print(orders.get_dataframe()[['unique_id', 'order_quantity']])
    """

    def __init__(self,
                 data: pd.DataFrame,
                 sku_column: str = 'unique_id',
                 lead_time: Optional[int] = None,
                 review_period: Optional[int] = None):
        """
        Initialize multi-SKU order decisions from DataFrame.

        Args:
            data: DataFrame with order data (must include unique_id column)
            sku_column: Name of the SKU identifier column (default: 'unique_id')
            lead_time: Lead time from the policy (optional, for parameter propagation)
            review_period: Review period from the policy (optional, for parameter propagation)
        """
        if not isinstance(sku_column, str) or not sku_column.strip():
            raise ValueError("sku_column must be a non-empty string")
        self.sku_column = sku_column
        self.lead_time = lead_time
        self.review_period = review_period

        # Validate SKU column exists
        if sku_column not in data.columns:
            raise ValueError(f"SKU column '{sku_column}' not found in DataFrame")

        _require_identifiers(data, sku_column, 'orders', unique=True)

        if 'order_quantity' not in data.columns:
            raise ValueError("OrderDecision requires an explicit order_quantity column")

        # Create copy
        self.data = data.copy()

        # Define all order decision columns
        all_columns = [
            'order_quantity',
            'target_level',
            'inventory_position',
            'reorder_point',
            'order_period',
            'expected_delivery_period'
        ]

        # Add missing columns with NaN defaults
        for col in all_columns:
            if col not in self.data.columns:
                self.data[col] = np.nan

        _require_finite_nonnegative(self.data, ['order_quantity'], 'orders')

    def get_dataframe(self) -> pd.DataFrame:
        """
        Get the underlying order DataFrame.

        Returns:
            Copy of the internal DataFrame
        """
        return self.data.copy()

    def total_order_quantity(self) -> float:
        """
        Calculate total order quantity across all SKUs.

        Returns:
            Sum of all order quantities
        """
        return self.data['order_quantity'].sum()

    def skus_to_order(self) -> int:
        """
        Count how many SKUs have positive order quantities.

        Returns:
            Number of SKUs with order_quantity > 0
        """
        return (self.data['order_quantity'] > 0).sum()

    def __repr__(self) -> str:
        """String representation showing order summary."""
        n_skus = len(self.data)
        n_orders = self.skus_to_order()
        total_qty = self.total_order_quantity()

        return (f"OrderDecision(n_skus={n_skus}, "
                f"skus_with_orders={n_orders}, "
                f"total_quantity={total_qty:.0f}, "
                f"lead_time={self.lead_time}, "
                f"review_period={self.review_period})")
