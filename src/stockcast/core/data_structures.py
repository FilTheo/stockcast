"""
Core data structures for the DataFrame-based multi-SKU inventory management system.

This module provides:
    - InventoryStateDataFrame: Multi-SKU inventory state container
    - OrderDecision: Multi-SKU order decision container

The supported staging import boundary is the ``stockcast`` package. Legacy
duplicate top-level source trees are not part of that boundary.
"""
import copy
from typing import Dict, Optional, Any, List, Union
import numpy as np
import pandas as pd

from stockcast.core._open_orders import (
    OPENING_SOURCE,
    _Deliveries,
    _OpenOrderBook,
    pipeline_mismatch,
)


# ============================================================================
# MULTI-SKU DATAFRAME-BASED STRUCTURES
# ============================================================================

def _require_finite_nonnegative(df: pd.DataFrame, columns: List[str], frame_name: str) -> None:
    """Validate finite, non-negative numeric values in selected columns."""
    for column in columns:
        if column not in df.columns:
            continue
        numbers = _plain_numbers(df[column])
        if numbers is not None:
            # Same checks, in the same order, without pandas round-trips.
            if np.isnan(numbers).any():
                raise ValueError(f"{frame_name}.{column} must not contain null or non-numeric values")
            if not np.isfinite(numbers).all():
                raise ValueError(f"{frame_name}.{column} must contain finite values")
            if (numbers < 0).any():
                raise ValueError(f"{frame_name}.{column} must be non-negative")
            continue
        values = pd.to_numeric(df[column], errors='coerce')
        if values.isna().any():
            raise ValueError(f"{frame_name}.{column} must not contain null or non-numeric values")
        if not np.isfinite(values.to_numpy(dtype=float)).all():
            raise ValueError(f"{frame_name}.{column} must contain finite values")
        if (values < 0).any():
            raise ValueError(f"{frame_name}.{column} must be non-negative")


def _plain_numbers(values: pd.Series) -> Optional[np.ndarray]:
    """Float view of a plain NumPy int/float column, else None."""
    dtype = values.dtype
    if isinstance(dtype, np.dtype) and dtype.kind in "fiu":
        return values.to_numpy(dtype=float)
    return None


def _plain_dates(values: pd.Series) -> Optional[np.ndarray]:
    """int64 view of a plain NumPy datetime64 column, else None.

    ``pd.to_datetime`` returns such a column unchanged, so validators can
    read it directly.
    """
    dtype = values.dtype
    if isinstance(dtype, np.dtype) and dtype.kind == "M":
        return values.to_numpy().view("i8")
    return None


def _one_valid_date(values: pd.Series) -> Optional[bool]:
    """Whether a plain datetime64 column holds one date and no NaT, else None."""
    stamps = _plain_dates(values)
    if stamps is None:
        return None
    return (
        len(stamps) > 0
        and not (stamps == np.iinfo(np.int64).min).any()
        and bool((stamps == stamps[0]).all())
    )


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
    if len(columns) == 1 and df[columns[0]].is_unique:
        return
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
    items = values.tolist()
    if any(isinstance(value, str) and not value.strip() for value in items):
        raise ValueError(f"{frame_name}.{column} must not contain blank strings")
    try:
        identifiers = set(items)
    except TypeError as exc:
        raise ValueError(f"{frame_name}.{column} identifiers must be hashable") from exc
    value_types = set(map(type, items))
    if len(value_types) > 1:
        names = sorted(value_type.__name__ for value_type in value_types)
        raise ValueError(
            f"{frame_name}.{column} must use one identifier type; got {names}"
        )
    # Missing values are rejected above, so distinct Python identifiers
    # cannot be pandas duplicates; only a collision needs the pandas count.
    if unique and len(identifiers) != len(items):
        _require_unique(df, [column], frame_name)
    return identifiers

def _is_missing(value) -> bool:
    """Whether a scalar is None or a pandas/NumPy missing value."""
    try:
        return value is None or bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _same_number(left: float, right: float) -> bool:
    return (np.isnan(left) and np.isnan(right)) or left == right


def _finite_numbers(frame: pd.DataFrame, column: str, frame_name: str) -> np.ndarray:
    """Float values of a complete finite numeric column."""
    values = pd.to_numeric(frame[column], errors='coerce').to_numpy(dtype=float)
    if np.isnan(values).any() or not np.isfinite(values).all():
        raise ValueError(f"{frame_name}.{column} must contain finite numbers")
    return values


def _integer_numbers(frame: pd.DataFrame, column: str, frame_name: str, *,
                     allow_missing: bool) -> np.ndarray:
    """Integer-valued numbers of a column (float array; NaN only if allowed)."""
    raw = frame[column]
    values = pd.to_numeric(raw, errors='coerce').to_numpy(dtype=float)
    missing = np.isnan(values)
    if (missing & raw.notna().to_numpy()).any() or (missing.any() and not allow_missing):
        raise ValueError(f"{frame_name}.{column} must contain integer periods")
    known = values[~missing]
    if not np.isfinite(known).all() or not np.equal(known, np.floor(known)).all():
        raise ValueError(f"{frame_name}.{column} must contain integer periods")
    return values if allow_missing else values.astype(np.int64)


def _require_supplier_ids(values: list, frame_name: str) -> None:
    """Supplier identifiers: hashable, nonblank, one Python type."""
    for value in values:
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"{frame_name}.supplier_id must not contain blank strings")
        try:
            hash(value)
        except TypeError as exc:
            raise ValueError(f"{frame_name}.supplier_id values must be hashable") from exc
    if len(set(map(type, values))) > 1:
        names = sorted({type(value).__name__ for value in values})
        raise ValueError(f"{frame_name}.supplier_id must use one identifier type; got {names}")


_FLOAT64 = np.dtype("float64")


def _uniform_float_pipelines(values: pd.Series, max_lead_time: int) -> Optional[np.ndarray]:
    """Stack valid float64 pipeline arrays, or return None for any other content.

    ``None`` never means invalid: callers then use their general per-row path,
    which applies the exact validation and error semantics.
    """
    shape = (max_lead_time,)
    items = values.tolist()
    if not items or not all(
        type(item) is np.ndarray and item.dtype == _FLOAT64 and item.shape == shape
        for item in items
    ):
        return None
    stacked = np.stack(items) if max_lead_time else np.zeros((len(items), 0))
    if not np.isfinite(stacked).all() or (stacked < 0).any():
        return None
    return stacked


class _DeferredHistory(list):
    """Engine-owned history list whose snapshot frames are built on first use.

    ``SimulationEngine`` hands states to policies and constraints (and policies
    receive deep copies). Materializing every earlier period for each copy
    would make a run quadratic, so the snapshots are produced only when the
    list is read or modified. A deep copy shares the immutable source.
    """

    __slots__ = ("_source",)

    def __init__(self, source=None):
        super().__init__()
        self._source = source

    def _load(self) -> "_DeferredHistory":
        source = self._source
        if source is not None:
            self._source = None
            list.extend(self, source())
        return self

    def __deepcopy__(self, memo):
        if self._source is not None:
            return _DeferredHistory(self._source)
        return [copy.deepcopy(item, memo) for item in list.__iter__(self)]

    def __copy__(self):
        return list(self._load())

    def __reduce_ex__(self, protocol):
        return (list, (list(self._load()),))


def _deferred_list_method(name):
    method = getattr(list, name)

    def load_then_call(self, *args, **kwargs):
        return method(self._load(), *args, **kwargs)

    load_then_call.__name__ = name
    return load_then_call


for _name in (
    "__len__", "__iter__", "__getitem__", "__setitem__", "__delitem__",
    "__contains__", "__reversed__", "__eq__", "__ne__", "__lt__", "__le__",
    "__gt__", "__ge__", "__add__", "__iadd__", "__mul__", "__imul__",
    "__rmul__", "__repr__", "append", "extend", "insert", "pop", "remove",
    "clear", "index", "count", "sort", "reverse", "copy",
):
    setattr(_DeferredHistory, _name, _deferred_list_method(_name))
del _name


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
                 _history: Optional[List[pd.DataFrame]] = None,
                 _open_orders: Optional[_OpenOrderBook] = None):
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
            _open_orders: Internal parameter for transferring the open-order
                book between instances (see ``with_open_orders``)
        """
        if not isinstance(max_lead_time, int) or isinstance(max_lead_time, bool) or max_lead_time < 0:
            raise ValueError("max_lead_time must be an integer >= 0")
        if allow_backorders is not None and not isinstance(allow_backorders, bool):
            raise ValueError("allow_backorders must be True, False, or unset")
        if not isinstance(sku_column, str) or not sku_column.strip():
            raise ValueError("sku_column must be a non-empty string")

        self.sku_column = sku_column
        self.max_lead_time = max_lead_time
        self.allow_backorders = allow_backorders
        self._history = _history if _history is not None else []
        self._open_orders = _open_orders

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
            one_date = _one_valid_date(df_input['date'])
            if one_date is not None:
                if one_date:
                    inferred_date = df_input['date'].iloc[0]
            else:
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
        elif _plain_dates(self.data['date']) is None:
            self.data['date'] = pd.to_datetime(self.data['date'], errors='coerce')

        # Drop columns that are not part of the inventory state schema
        # This removes columns like 'y' (demand) from historical data
        columns_to_drop = [col for col in self.data.columns if col not in valid_inventory_columns]
        if columns_to_drop:
            self.data = self.data.drop(columns=columns_to_drop)

        # Initialize class-level attributes
        self.has_stockout = False
        self.has_backorder = False

    @classmethod
    def _from_trusted(
        cls,
        data: pd.DataFrame,
        *,
        sku_column: str,
        max_lead_time: int,
        allow_backorders: Optional[bool],
        history: List[pd.DataFrame],
        start_date: pd.Timestamp,
        has_stockout: bool = False,
        has_backorder: bool = False,
        open_orders: Optional[_OpenOrderBook] = None,
    ) -> 'InventoryStateDataFrame':
        """Wrap an engine-built complete state frame without re-validating it.

        The engine builds ``data`` from state it has already validated, so it
        already has every schema column and one period and date.
        """
        state = cls.__new__(cls)
        state.sku_column = sku_column
        state.max_lead_time = max_lead_time
        state.allow_backorders = allow_backorders
        state._history = history
        state._inferred_start_date = start_date
        state._source_data = data
        state.data = data
        state.has_stockout = has_stockout
        state.has_backorder = has_backorder
        state._open_orders = open_orders
        return state

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
        pipelines = _uniform_float_pipelines(result['in_transit'], self.max_lead_time)
        if pipelines is not None:
            # Row sums of a stacked float64 pipeline equal the per-array sums.
            result['total_in_transit'] = pipelines.sum(axis=1)
        else:
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
        backorders = _plain_numbers(self.data['backorders'])
        if backorders is None:
            backorders = pd.to_numeric(self.data['backorders'], errors='coerce')
        on_hand = _plain_numbers(self.data['on_hand'])
        if on_hand is None:
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
        one_date = _one_valid_date(self.data['date'])
        if one_date is None:
            dates = pd.to_datetime(self.data['date'], errors='coerce')
            one_date = not dates.isna().any() and dates.nunique() == 1
        if not one_date:
            raise ValueError("inventory_state.date must contain one complete opening date")
        pipelines = _uniform_float_pipelines(self.data['in_transit'], self.max_lead_time)
        if pipelines is None:
            self._validate_pipeline_arrays()
        if self._open_orders is not None and self._open_orders.checked:
            if pipelines is None:
                pipelines = self._stacked_pipelines()
            mismatch = pipeline_mismatch(
                self._open_orders,
                pd.Index(self.data[self.sku_column].tolist(), dtype=object),
                int(periods[0]),
                self.max_lead_time,
                pipelines,
            )
            if mismatch:
                raise ValueError(
                    f"inventory_state {mismatch}; in_transit and the declared open "
                    "orders must describe the same pipeline"
                )

    def _validate_pipeline_arrays(self) -> None:
        """Per-row ``in_transit`` validation for non-uniform pipeline content."""
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

    def _stacked_pipelines(self) -> np.ndarray:
        """Validated ``in_transit`` arrays as one float matrix (SKU rows)."""
        rows = [np.asarray(value, dtype=float) for value in self.data['in_transit']]
        if not self.max_lead_time:
            return np.zeros((len(rows), 0))
        return np.stack(rows)

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
        self._open_orders = None

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

    # ------------------------------------------------------------------
    # Order-level view of the pipeline
    # ------------------------------------------------------------------

    def with_open_orders(
        self,
        open_orders: pd.DataFrame,
        *,
        sku_column: Optional[str] = None,
    ) -> 'InventoryStateDataFrame':
        """Declare the opening pipeline as explicit open orders.

        ``in_transit`` stores only the quantity due at each future period.
        This method records the orders behind it: one row per scheduled
        delivery, with optional supplier, order period and order grouping.
        Call it after ``initialize_zero`` or ``initialize_from_observed``.

        Required columns:
            - SKU column (``sku_column``, default the state's SKU column)
            - ``due_period``: integer state period at which the delivery is
              received, before that period's demand. It must satisfy
              ``period < due_period <= period + max_lead_time``.
            - ``quantity``: finite quantity > 0 still to be delivered.

        Optional columns:
            - ``supplier_id``: hashable supplier identifier (missing = unknown)
            - ``order_period``: integer period the order was placed
              (``<= period``; missing = unknown, never inferred)
            - ``order_id``: groups rows that are scheduled deliveries of one
              order line (partial deliveries). Stockcast assigns its own
              integer ids in order of first appearance.
            - ``ordered_quantity``: original ordered quantity of the order
              line, ``>=`` its remaining deliveries (default: their sum)

        If ``in_transit`` is still empty it is set from these rows. If it
        already holds quantities, the rows must reproduce it exactly: the
        orders then only attribute the existing pipeline. A disagreement is
        rejected rather than silently resolved.

        Returns:
            self (for method chaining, like the initializers)
        """
        sku_column = sku_column or self.sku_column
        if not isinstance(open_orders, pd.DataFrame):
            raise TypeError("open_orders must be a pandas DataFrame")
        required = [sku_column, "due_period", "quantity"]
        optional = ["supplier_id", "order_period", "order_id", "ordered_quantity"]
        missing = [column for column in required if column not in open_orders.columns]
        if missing:
            raise ValueError(f"open_orders is missing required columns: {missing}")
        extra = sorted(set(open_orders.columns) - set(required) - set(optional))
        if extra:
            raise ValueError(f"open_orders contains unsupported columns: {extra}")
        periods = pd.to_numeric(self.data['period'], errors='coerce').to_numpy(dtype=float)
        if (
            not len(periods)
            or np.isnan(periods).any()
            or len(set(periods)) != 1
            or periods[0] != np.floor(periods[0])
        ):
            raise ValueError(
                "with_open_orders requires an initialized state with one integer period; "
                "call initialize_zero or initialize_from_observed first"
            )
        period = int(periods[0])
        if not all(
            isinstance(value, np.ndarray) and len(value) == self.max_lead_time
            for value in self.data['in_transit']
        ):
            raise ValueError(
                "with_open_orders requires initialized in_transit arrays; "
                "call initialize_zero or initialize_from_observed first"
            )
        self._validate_pipeline_arrays()
        state_skus = self.data[self.sku_column].tolist()
        frame = open_orders.reset_index(drop=True)
        count = len(frame)
        if count:
            order_skus = _require_identifiers(frame, sku_column, 'open_orders', unique=False)
            unknown = order_skus - set(state_skus)
            if unknown:
                raise ValueError(f"open_orders contains unknown SKUs: {_identifier_sample(unknown)}")
        quantity = _finite_numbers(frame, "quantity", "open_orders")
        if (quantity <= 0).any():
            raise ValueError("open_orders.quantity must be > 0")
        due = _integer_numbers(frame, "due_period", "open_orders", allow_missing=False)
        if ((due <= period) | (due > period + self.max_lead_time)).any():
            raise ValueError(
                "open_orders.due_period must satisfy "
                f"{period} < due_period <= {period + self.max_lead_time} "
                "(period < due_period <= period + max_lead_time)"
            )
        if "order_period" in frame.columns:
            order_period = _integer_numbers(frame, "order_period", "open_orders", allow_missing=True)
            known = ~np.isnan(order_period)
            if (order_period[known] > period).any():
                raise ValueError("open_orders.order_period cannot be after the state period")
        else:
            order_period = np.full(count, np.nan)
        if "supplier_id" in frame.columns:
            supplier = [
                None if _is_missing(value) else value for value in frame["supplier_id"].tolist()
            ]
            _require_supplier_ids([value for value in supplier if value is not None], "open_orders")
        else:
            supplier = [None] * count
        if "order_id" in frame.columns:
            labels = frame["order_id"].tolist()
            if any(_is_missing(value) for value in labels):
                raise ValueError("open_orders.order_id must not contain missing values")
            try:
                codes, uniques = pd.factorize(pd.Series(labels, dtype=object), sort=False)
            except TypeError as exc:
                raise ValueError("open_orders.order_id values must be hashable") from exc
            line = codes.astype(np.int64)
        else:
            line = np.arange(count, dtype=np.int64)
        skus = frame[sku_column].tolist() if count else []
        remaining = np.zeros(line.max() + 1 if count else 0)
        np.add.at(remaining, line, quantity)
        if "ordered_quantity" in frame.columns:
            declared = _finite_numbers(frame, "ordered_quantity", "open_orders")
        else:
            declared = remaining[line]
        for code in range(len(remaining)):
            rows = np.flatnonzero(line == code)
            first = rows[0]
            if any(
                skus[row] != skus[first]
                or type(skus[row]) is not type(skus[first])
                or supplier[row] != supplier[first]
                or not _same_number(order_period[row], order_period[first])
                or declared[row] != declared[first]
                for row in rows
            ):
                raise ValueError(
                    "rows sharing an order_id must agree on SKU, supplier_id, "
                    "order_period and ordered_quantity"
                )
            if len(set(due[rows].tolist())) != len(rows):
                raise ValueError("an order line cannot have two deliveries due in the same period")
            if declared[first] < remaining[code] - 1e-9 * max(1.0, remaining[code]):
                raise ValueError(
                    "open_orders.ordered_quantity must be >= the remaining quantity of its order"
                )
        deliveries = _Deliveries.build(
            order_id=line,
            sku=skus,
            supplier=supplier,
            order_period=order_period,
            ordered=declared,
            due=due,
            quantity=quantity,
            source=[OPENING_SOURCE] * count,
        )
        book = _OpenOrderBook.declared_opening(deliveries)
        sku_index = pd.Index(state_skus, dtype=object)
        declared_matrix = book.pipeline_matrix(sku_index, period, self.max_lead_time)
        current = self._stacked_pipelines()
        if (current != 0).any():
            mismatch = pipeline_mismatch(book, sku_index, period, self.max_lead_time, current)
            if mismatch:
                raise ValueError(
                    f"{mismatch}; with_open_orders can attribute an existing pipeline "
                    "but not change it. Start from an empty pipeline to replace it"
                )
        else:
            self.data['in_transit'] = [row.copy() for row in declared_matrix]
        self._open_orders = book
        return self

    def open_orders(self) -> pd.DataFrame:
        """Return one row per open order line.

        Columns: ``order_id``, SKU column, ``supplier_id``, ``source``,
        ``order_period``, ``ordered_quantity``, ``remaining_quantity``,
        ``due_period`` (next scheduled delivery) and ``final_due_period``.

        A state whose pipeline was given only as ``in_transit`` arrays shows
        one ``source="opening"`` row per positive pipeline slot, with unknown
        supplier and order period. Summing ``remaining_quantity`` by SKU and
        due period reproduces ``in_transit``.
        """
        return self._order_book().open_orders_frame(self.sku_column)

    def scheduled_receipts(self) -> pd.DataFrame:
        """Return one row per open scheduled delivery (order line and due period)."""
        return self._order_book().scheduled_receipts_frame(self.sku_column)

    def _order_book(self) -> _OpenOrderBook:
        """The declared book, or one attributed from ``in_transit``."""
        self._validate_pipeline_arrays()
        periods = pd.to_numeric(self.data['period'], errors='coerce').to_numpy(dtype=float)
        if not len(periods) or np.isnan(periods).any() or len(set(periods)) != 1:
            raise ValueError("inventory_state.period must be one complete integer period")
        if self._open_orders is not None:
            return self._open_orders
        return _OpenOrderBook.from_pipelines(
            self.data[self.sku_column].tolist(),
            self._stacked_pipelines(),
            int(periods[0]),
        )

    def advance_period(self, *, period_frequency: str, is_review_period: bool) -> 'InventoryStateDataFrame':
        """Advance the clock, reset flows, receive due stock and clear old backlog.

        No demand is observed here. Call ``fulfill_demand`` after the optional
        decision and order receipt to complete the period.
        """
        self._validate_ready_state()
        offset = _require_forward_frequency(period_frequency, "period_frequency")
        if not isinstance(is_review_period, bool):
            raise ValueError("is_review_period must be boolean")
        data = self.data.copy()
        for column in data.columns:
            if column.startswith("latest_"):
                data[column] = 0.0
        data["period"] = data["period"] + 1
        data["date"] = pd.Timestamp(data["date"].iloc[0]) + offset
        data["is_review_period"] = is_review_period
        received = data["in_transit"].map(lambda pipeline: float(pipeline[0]) if len(pipeline) else 0.0)
        data["in_transit"] = data["in_transit"].map(
            lambda pipeline: np.r_[pipeline[1:], 0.0] if len(pipeline) else pipeline.copy()
        )
        cleared = np.minimum(data["backorders"], received) if self.allow_backorders else 0.0
        data["backorders"] -= cleared
        data["on_hand"] += received - cleared
        data["latest_received"] = received
        data["latest_backorders_fulfilled"] = cleared
        book = self._open_orders
        if book is not None:
            book = book.advanced(int(data["period"].iloc[0]))
        return InventoryStateDataFrame(data, sku_column=self.sku_column,
                                       max_lead_time=self.max_lead_time,
                                       allow_backorders=self.allow_backorders,
                                       _history=self._history,
                                       _open_orders=book)

    def fulfill_demand(self, demand_df: pd.DataFrame, *, demand_column: str = "y",
                       date_column: str = "date", sku_column: Optional[str] = None
                       ) -> 'InventoryStateDataFrame':
        """Fulfill current-period demand without advancing time or receiving again."""
        self._validate_ready_state()
        sku_column = sku_column or self.sku_column
        actual = _require_identifiers(demand_df, sku_column, "demand_df", unique=True)
        expected = _require_identifiers(self.data, self.sku_column, "inventory_state", unique=True)
        if actual - expected:
            raise ValueError(f"demand_df contains unknown SKUs: {_identifier_sample(actual - expected)}")
        if expected - actual:
            raise ValueError(f"demand_df is missing inventory SKUs: {_identifier_sample(expected - actual)}")
        if demand_column not in demand_df:
            raise ValueError(f"demand_column '{demand_column}' not found in demand_df")
        _require_finite_nonnegative(demand_df, [demand_column], "demand_df")
        if not date_column or date_column not in demand_df:
            raise ValueError("demand_df must contain an explicit date column")
        dates = pd.to_datetime(demand_df[date_column], errors="coerce")
        if dates.isna().any() or dates.nunique() != 1:
            raise ValueError("demand_df must contain one complete date for the period")
        if dates.iloc[0] != pd.Timestamp(self.data["date"].iloc[0]):
            raise ValueError("demand date does not match expected next date/current opened period")
        data = self.data.copy()
        demand = data[self.sku_column].map(demand_df.set_index(sku_column)[demand_column]).astype(float)
        fulfilled = np.minimum(demand, data["on_hand"])
        shortage = demand - fulfilled
        data["on_hand"] -= fulfilled
        if self.allow_backorders:
            data["backorders"] += shortage
        data["latest_incoming_demand"] = demand
        data["latest_fulfilled"] = fulfilled
        data["latest_shortage"] = shortage
        result = InventoryStateDataFrame(data, sku_column=self.sku_column,
                                        max_lead_time=self.max_lead_time,
                                        allow_backorders=self.allow_backorders,
                                        _history=self._history,
                                        _open_orders=self._open_orders)
        result.has_stockout = bool((shortage > 0).any())
        result.has_backorder = bool((data["backorders"] > 0).any())
        result._history.append(result.data.copy())
        return result

    def process_demand(self, demand_df: pd.DataFrame, review_period: int,
                       period_frequency: str, demand_column: str = "y",
                       date_column: Optional[str] = "date", sku_column: Optional[str] = None
                       ) -> 'InventoryStateDataFrame':
        """Convenience transition: advance/receive then fulfill, without an order.

        For a manual before-demand decision loop use ``advance_period``,
        ``update_inventory_with_orders``, then ``fulfill_demand`` instead.
        """
        if not isinstance(review_period, int) or isinstance(review_period, bool) or review_period < 1:
            raise ValueError("review_period must be an integer >= 1")
        self._validate_ready_state()
        new_period = int(self.data["period"].iloc[0]) + 1
        advanced = self.advance_period(period_frequency=period_frequency,
                                       is_review_period=new_period % review_period == 0)
        return advanced.fulfill_demand(demand_df, demand_column=demand_column,
                                       date_column=date_column, sku_column=sku_column)


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


class OrderLines:
    """
    Supplier order lines with explicit delivery periods (order-level API).

    ``OrderDecision`` holds one order quantity per SKU and a single lead time.
    ``OrderLines`` is the order-level form used by ``place_order_lines`` and by
    ``SimulationEngine(..., supply=SupplyModel(...))``: one row per scheduled
    delivery, so one SKU can be ordered from several suppliers, with
    different lead times, and one order line can be delivered in parts.

    Columns:
        - unique_id (or ``sku_column``): SKU identifier (may repeat)
        - order_quantity: finite quantity >= 0 of this delivery
        - order_period: state period in which the order is placed
        - due_period: state period in which the delivery is received,
          before that period's demand (``due_period >= order_period``;
          equal means an immediate receipt, as with zero lead time)
        - supplier_id (optional): hashable supplier identifier
        - order_line (optional): rows with the same value are deliveries of
          one order line (partial deliveries); default one line per row

    Example:
        lines = OrderLines(pd.DataFrame({
            'unique_id':      ['beans', 'beans', 'beans'],
            'supplier_id':    ['local', 'import', 'import'],
            'order_quantity': [20.0,     60.0,     40.0],
            'order_period':   [5,        5,        5],
            'due_period':     [6,        12,       15],
            'order_line':     [0,        1,        1],
        }))
    """

    def __init__(self, data: pd.DataFrame, sku_column: str = 'unique_id'):
        if not isinstance(sku_column, str) or not sku_column.strip():
            raise ValueError("sku_column must be a non-empty string")
        if not isinstance(data, pd.DataFrame):
            raise TypeError("OrderLines data must be a pandas DataFrame")
        required = [sku_column, 'order_quantity', 'order_period', 'due_period']
        missing = [column for column in required if column not in data.columns]
        if missing:
            raise ValueError(f"OrderLines is missing required columns: {missing}")
        extra = sorted(set(data.columns) - set(required) - {'supplier_id', 'order_line'})
        if extra:
            raise ValueError(f"OrderLines contains unsupported columns: {extra}")
        self.sku_column = sku_column
        frame = data.reset_index(drop=True).copy()
        if len(frame):
            _require_identifiers(frame, sku_column, 'order_lines', unique=False)
        quantity = _finite_numbers(frame, 'order_quantity', 'order_lines')
        if (quantity < 0).any():
            raise ValueError("order_lines.order_quantity must be non-negative")
        frame['order_quantity'] = quantity
        frame['order_period'] = _integer_numbers(
            frame, 'order_period', 'order_lines', allow_missing=False
        )
        frame['due_period'] = _integer_numbers(frame, 'due_period', 'order_lines', allow_missing=False)
        if (frame['due_period'] < frame['order_period']).any():
            raise ValueError("order_lines.due_period must be >= order_period")
        if 'supplier_id' not in frame.columns:
            frame['supplier_id'] = pd.Series([None] * len(frame), dtype=object)
        supplier = [None if _is_missing(value) else value for value in frame['supplier_id'].tolist()]
        _require_supplier_ids([value for value in supplier if value is not None], 'order_lines')
        frame['supplier_id'] = pd.Series(supplier, dtype=object)
        if 'order_line' in frame.columns:
            labels = frame['order_line'].tolist()
            if any(_is_missing(value) for value in labels):
                raise ValueError("order_lines.order_line must not contain missing values")
            try:
                codes, _ = pd.factorize(pd.Series(labels, dtype=object), sort=False)
            except TypeError as exc:
                raise ValueError("order_lines.order_line values must be hashable") from exc
            frame['order_line'] = codes.astype(np.int64)
            for _, group in frame.groupby('order_line', sort=False):
                keys = group[[sku_column, 'order_period']].drop_duplicates()
                if (
                    len(keys) > 1
                    or len(set(map(type, group[sku_column]))) > 1
                    or len({repr(value) for value in group['supplier_id']}) > 1
                ):
                    raise ValueError(
                        "rows sharing an order_line must agree on SKU, supplier_id and order_period"
                    )
                if group['due_period'].duplicated().any():
                    raise ValueError(
                        "an order line cannot have two deliveries due in the same period"
                    )
        else:
            frame['order_line'] = np.arange(len(frame), dtype=np.int64)
        self.data = frame[[
            sku_column, 'supplier_id', 'order_quantity', 'order_period', 'due_period', 'order_line',
        ]]

    def get_dataframe(self) -> pd.DataFrame:
        """Return a copy of the validated order lines."""
        return self.data.copy()

    def total_order_quantity(self) -> float:
        """Sum of all delivery quantities."""
        return float(self.data['order_quantity'].sum())

    def __repr__(self) -> str:
        return (f"OrderLines(n_rows={len(self.data)}, "
                f"n_order_lines={self.data['order_line'].nunique()}, "
                f"total_quantity={self.total_order_quantity():.0f})")
