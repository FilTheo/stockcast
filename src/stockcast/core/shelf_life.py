"""
FIFO shelf-life primitives for the DataFrame-based inventory system.

This module provides:
    - FIFOLotLedger: Tracks on-hand stock as dated lots, consumed oldest-first,
      with age-based expiry.
    - ShelfLife: the FIFO shelf-life ``InventoryProcess``. Expired stock
      leaves on_hand before receipts, ordering and demand, and is recorded in
      the event ledger's ``expired_units`` column.
    - ShelfLifeEngine: the original convenience engine. It runs one
      ``ShelfLife`` process and produces exactly the outputs it always has.

Expiry semantics: a lot received on day D with shelf life S serves demand on
days D through D+S-1 and expires at the start of day D+S, before that day's
demand is processed.

Usage (the simple engine):
    engine = ShelfLifeEngine(shelf_life_days=3)
    result = engine.run(
        policy=policy,
        demand_source=demand_df,
        inventory=inventory,
        n_periods=30,
        period_frequency="D",
        initial_decision="none",
        warmup_periods=0,
        scoring_periods=30,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="example_demand",
        random_seed=None,
        opening_lots=opening_lots,
    )
    result.to_event_frame()["expired_units"]

Usage (the same run as a process, combinable with other processes):
    result = SimulationEngine().run(
        ...,  # the same arguments, without opening_lots
        processes=[ShelfLife(shelf_life_days=3, opening_lots=opening_lots)],
    )
"""

import copy
import json
import warnings
from typing import Dict, List

import numpy as np
import pandas as pd

from stockcast.core.data_structures import (
    InventoryStateDataFrame,
    _identifier_sample,
    _require_identifiers,
)
from stockcast.core.processes import Flow, InventoryProcess, ProcessFlows
from stockcast.core.simulation_engine import SimulationEngine

OPENING_EXPIRY_HANDLING = ("reject", "expire_before_initial_decision", "preprocessed")


class FIFOLotLedger:
    """
    Dated-lot ledger for multi-SKU on-hand stock with FIFO consumption.

    The ledger mirrors on_hand: every unit on hand belongs to exactly one lot
    with a receipt date. Consumption removes from the oldest lot first;
    expiry removes lots whose age reached ``shelf_life_days``.
    """

    def __init__(self, shelf_life_days: int):
        if not isinstance(shelf_life_days, int) or isinstance(shelf_life_days, bool) or shelf_life_days < 1:
            raise ValueError("shelf_life_days must be an integer >= 1")
        self.shelf_life_days = int(shelf_life_days)
        self.lots_by_sku: Dict[object, List[dict]] = {}

    def seed_from_lots(
        self,
        inventory: InventoryStateDataFrame,
        opening_lots: pd.DataFrame,
    ) -> None:
        """Seed explicit opening lots and assert they equal opening on-hand."""
        required = ["unique_id", "received_date", "quantity"]
        if not isinstance(opening_lots, pd.DataFrame):
            raise TypeError("opening_lots must be a pandas DataFrame")
        missing = [column for column in required if column not in opening_lots.columns]
        if missing:
            raise ValueError(f"opening_lots is missing required columns: {missing}")
        prepared = opening_lots.copy()
        lot_skus = _require_identifiers(
            prepared,
            "unique_id",
            "opening_lots",
            unique=False,
        )
        quantities = pd.to_numeric(prepared["quantity"], errors="coerce")
        if quantities.isna().any() or not np.isfinite(quantities.to_numpy(dtype=float)).all():
            raise ValueError("opening_lots.quantity must contain finite values")
        if (quantities <= 0).any():
            raise ValueError("opening_lots.quantity must be > 0")
        prepared["quantity"] = quantities.astype(float)
        dates = pd.to_datetime(prepared["received_date"], errors="coerce")
        if dates.isna().any():
            raise ValueError("opening_lots.received_date must contain valid dates")
        prepared["received_date"] = dates

        state = inventory.get_dataframe()
        opening_dates = pd.to_datetime(state["date"], errors="coerce")
        if opening_dates.isna().any() or opening_dates.nunique() != 1:
            raise ValueError("inventory must contain one complete opening date")
        opening_date = opening_dates.iloc[0]
        if (prepared["received_date"] > opening_date).any():
            raise ValueError("opening lot dates cannot be after the inventory opening date")
        inventory_skus = _require_identifiers(
            state,
            inventory.sku_column,
            "inventory_state",
            unique=True,
        )
        unknown = _identifier_sample(lot_skus - inventory_skus)
        if unknown:
            raise ValueError(f"opening_lots contains unknown SKUs: {unknown[:5]}")

        totals = prepared.groupby("unique_id")["quantity"].sum()
        expected = state.set_index(inventory.sku_column)["on_hand"].astype(float)
        actual = expected.index.to_series().map(totals).fillna(0.0).to_numpy(dtype=float)
        if not np.allclose(actual, expected.to_numpy(dtype=float), atol=1e-9):
            raise ValueError("opening lot quantities must exactly equal opening on_hand per SKU")

        self.lots_by_sku = {sku: [] for sku in state[inventory.sku_column]}
        for row in prepared.sort_values("received_date").itertuples(index=False):
            self.lots_by_sku[row.unique_id].append({
                "date": pd.Timestamp(row.received_date),
                "qty": float(row.quantity),
            })

    def receive(self, unique_id, qty: float, date: pd.Timestamp) -> None:
        if isinstance(qty, (bool, np.bool_)):
            raise ValueError("lot receipt quantity must be a finite number >= 0")
        try:
            quantity = float(qty)
        except (TypeError, ValueError) as exc:
            raise ValueError("lot receipt quantity must be a finite number >= 0") from exc
        if not np.isfinite(quantity) or quantity < 0:
            raise ValueError("lot receipt quantity must be a finite number >= 0")
        received_date = pd.Timestamp(date)
        if pd.isna(received_date):
            raise ValueError("lot receipt date must be a valid timestamp")
        if quantity == 0:
            warnings.warn(
                "zero-quantity lot receipt was skipped because it does not create stock",
                RuntimeWarning,
                stacklevel=2,
            )
            return
        self.lots_by_sku.setdefault(unique_id, []).append(
            {"date": received_date, "qty": quantity}
        )

    def expire(self, current_date: pd.Timestamp) -> Dict[object, float]:
        """Remove lots aged >= shelf_life_days; return expired qty per SKU."""
        expired: Dict[object, float] = {}
        current_date = pd.Timestamp(current_date)
        for unique_id, lots in self.lots_by_sku.items():
            kept = []
            expired_qty = 0.0
            for lot in lots:
                age_days = (current_date - pd.Timestamp(lot["date"])).days
                if age_days >= self.shelf_life_days:
                    expired_qty += float(lot["qty"])
                else:
                    kept.append(lot)
            self.lots_by_sku[unique_id] = kept
            if expired_qty > 0:
                expired[unique_id] = expired_qty
        return expired

    def consume(self, unique_id, qty: float) -> list[dict]:
        """Consume qty from the oldest lots first."""
        remaining = float(qty)
        lots = self.lots_by_sku.setdefault(unique_id, [])
        kept = []
        consumed = []
        for lot in lots:
            lot_qty = float(lot["qty"])
            if remaining > 0:
                used = min(lot_qty, remaining)
                lot_qty -= used
                remaining -= used
                if used > 0:
                    consumed.append({
                        "received_date": pd.Timestamp(lot["date"]).isoformat(),
                        "quantity": float(used),
                    })
            if lot_qty > 1e-9:
                kept.append({"date": lot["date"], "qty": lot_qty})
        self.lots_by_sku[unique_id] = kept
        if remaining > 1e-9 + 1e-12 * float(qty):
            raise ValueError(f"FIFO lot ledger has insufficient stock for SKU {unique_id}")
        return consumed

    def balances(self) -> pd.Series:
        """Total ledger quantity per SKU."""
        return pd.Series(
            {
                unique_id: sum(float(lot["qty"]) for lot in lots)
                for unique_id, lots in self.lots_by_sku.items()
            },
            dtype=float,
        )


def _require_shelf_life_days(shelf_life_days) -> None:
    if (
        not isinstance(shelf_life_days, int)
        or isinstance(shelf_life_days, bool)
        or shelf_life_days < 1
    ):
        raise ValueError("shelf_life_days must be an integer >= 1")


class ShelfLife(InventoryProcess):
    """
    FIFO shelf life as an ``InventoryProcess``.

    Stock is tracked as dated lots in a ``FIFOLotLedger``. Each period:

        1. before demand, lots aged ``shelf_life_days`` or more expire; the
           units leave on_hand before receipts, the order decision and demand,
           and are recorded in ``expired_units``;
        2. every receipt (pipeline arrivals, then zero-lead-time receipts)
           becomes a lot dated with the current period date;
        3. after demand, fulfilled demand and cleared backlog consume the
           oldest lots first, and the ledger is checked against on_hand.

    Other processes' flows and callback adjustments are mirrored: removals
    consume the oldest lots, and additions need an explicit, unexpired
    ``received_date`` (never invented).

    Args:
        shelf_life_days: calendar days a lot remains usable (integer >= 1).
        opening_lots: DataFrame with ``unique_id``, ``received_date`` and
            ``quantity``; lot quantities must add up to opening on_hand for
            every SKU.
        opening_expiry_handling: ``"reject"`` (default) fails if an opening
            lot is already expired at the opening date;
            ``"expire_before_initial_decision"`` writes that stock off before
            the run (recorded in the manifest, not as a period flow);
            ``"preprocessed"`` asserts no opening lot is expired.

    ``ledger`` holds the lots of the latest run (the last branch of a
    comparison).
    """

    name = "shelf_life"
    flows = (Flow("expired", "outflow", category="expiry"),)

    def __init__(self, shelf_life_days: int, opening_lots: pd.DataFrame,
                 opening_expiry_handling: str = "reject"):
        _require_shelf_life_days(shelf_life_days)
        if opening_expiry_handling not in OPENING_EXPIRY_HANDLING:
            raise ValueError(
                "opening_expiry_handling must be 'reject', "
                "'expire_before_initial_decision', or 'preprocessed'"
            )
        if not isinstance(opening_lots, pd.DataFrame):
            raise TypeError("opening_lots must be a pandas DataFrame")
        self.shelf_life_days = shelf_life_days
        self.opening_lots = opening_lots.copy(deep=True)
        self.opening_expiry_handling = opening_expiry_handling
        self.ledger = FIFOLotLedger(shelf_life_days)
        self.expired_this_period: Dict[object, float] = {}
        self._pending_ledger = None
        self._opening_expired_units: Dict[object, float] = {}

    # ---- run preparation (engine-private) ---------------------------------

    def _prepare_opening(self, inventory: InventoryStateDataFrame) -> None:
        """Validate and seed the opening lots against the run's own state copy.

        With ``expire_before_initial_decision`` the expired opening stock is
        removed from that copy's on_hand before the run starts.
        """
        ledger = FIFOLotLedger(self.shelf_life_days)
        ledger.seed_from_lots(inventory, self.opening_lots)
        opening_date = pd.Timestamp(inventory.get_dataframe()["date"].iloc[0])
        stale_units = ledger.expire(opening_date)
        handling = self.opening_expiry_handling
        if stale_units and handling in {"reject", "preprocessed"}:
            stale_skus = _identifier_sample(stale_units)
            if handling == "preprocessed":
                raise ValueError(
                    "opening_expiry_handling='preprocessed' requires opening_lots "
                    "with no stock already expired at the opening date; "
                    f"affected SKUs: {stale_skus}"
                )
            raise ValueError(
                "opening_lots contains stock already expired at the opening date; "
                f"affected SKUs: {stale_skus}. Use "
                "opening_expiry_handling='expire_before_initial_decision' to write it off."
            )
        if stale_units and handling == "expire_before_initial_decision":
            for unique_id, quantity in stale_units.items():
                mask = inventory.data[inventory.sku_column] == unique_id
                inventory.data.loc[mask, "on_hand"] -= float(quantity)
            if (inventory.data["on_hand"] < -1e-9).any():
                raise ValueError("opening expired-lot write-off would make on_hand negative")
            inventory.data["on_hand"] = inventory.data["on_hand"].clip(lower=0.0)
        self._pending_ledger = ledger
        self._opening_expired_units = stale_units

    def _opening_lot_fingerprint(self) -> dict:
        opening_lot_data = self.opening_lots.copy()
        opening_lot_data["received_date"] = pd.to_datetime(
            opening_lot_data["received_date"]
        )
        opening_lot_data["quantity"] = pd.to_numeric(opening_lot_data["quantity"])
        return {
            "sha256": SimulationEngine._dataframe_checksum(
                opening_lot_data,
                sort_columns=["unique_id", "received_date", "quantity"],
            ),
            "rows": len(opening_lot_data),
            "columns": list(opening_lot_data.columns),
        }

    def _opening_expired_rows(self) -> list:
        stale_units = self._opening_expired_units
        return [
            {"unique_id": sku, "quantity": float(stale_units[sku])}
            for sku in sorted(
                stale_units,
                key=lambda value: (type(value).__name__, repr(value)),
            )
        ]

    def _engine_settings(self) -> dict:
        """The ``run_settings`` keys ShelfLifeEngine has always recorded."""
        return {
            "shelf_life": self.shelf_life_days,
            "shelf_life_unit": "calendar_days",
            "opening_lot_count": len(self.opening_lots),
            "opening_lots": self._opening_lot_fingerprint(),
            "opening_expiry_handling": self.opening_expiry_handling,
            "opening_expired_units": self._opening_expired_rows(),
        }

    def get_config(self) -> dict:
        return {
            "shelf_life_days": self.shelf_life_days,
            "shelf_life_unit": "calendar_days",
            "opening_lot_count": len(self.opening_lots),
            "opening_lots": self._opening_lot_fingerprint(),
            "opening_expiry_handling": self.opening_expiry_handling,
            "opening_expired_units": self._opening_expired_rows(),
        }

    # ---- process hooks ------------------------------------------------------

    def reset(self, context) -> None:
        if self._pending_ledger is None:
            raise RuntimeError("ShelfLife opening lots were not prepared for this run")
        self.ledger = self._pending_ledger
        self._pending_ledger = None
        self.expired_this_period = {}

    def before_demand(self, context):
        self.expired_this_period = self.ledger.expire(context.date)
        if self.expired_this_period:
            return ProcessFlows({"expired": self.expired_this_period})
        return None

    def on_receipt(self, context) -> None:
        # With several suppliers, all of a SKU's deliveries received in one
        # period form one lot dated that period.
        for unique_id, quantity in zip(context.unique_id.tolist(), context.received.tolist()):
            if quantity > 0:
                self.ledger.receive(unique_id, quantity, context.date)

    def after_demand(self, context):
        # Backlog clearance consumes the same FIFO lots as current demand.
        for unique_id, fulfilled, backorders_fulfilled in zip(
            context.unique_id.tolist(),
            context.fulfilled.tolist(),
            context.backorders_fulfilled.tolist(),
        ):
            self.ledger.consume(unique_id, fulfilled + backorders_fulfilled)

    def check(self, context) -> None:
        lots_by_sku = self.ledger.lots_by_sku
        expected = np.array([
            sum(float(lot["qty"]) for lot in lots_by_sku[unique_id])
            if unique_id in lots_by_sku else 0.0
            for unique_id in context.unique_id.tolist()
        ], dtype=float)
        if not np.allclose(context.on_hand.to_numpy(dtype=float), expected, atol=1e-6):
            raise AssertionError("FIFO shelf-life ledger no longer matches Stockcast on_hand")

    def validate_stock_change(self, change, context) -> None:
        if change.quantity <= 0:
            return
        if pd.isna(change.received_date):
            if change.origin == "callback":
                raise ValueError(
                    "positive ShelfLifeEngine inventory adjustments require received_date"
                )
            raise ValueError(
                f"shelf life needs a received_date for inflow {change.source!r}; "
                "return it with ProcessFlows(..., received_dates=...)"
            )
        if (pd.Timestamp(context.date) - pd.Timestamp(change.received_date)).days >= self.shelf_life_days:
            if change.origin == "callback":
                raise ValueError("inventory adjustment would add stock that is already expired")
            raise ValueError(f"inflow {change.source!r} would add stock that is already expired")

    def on_stock_change(self, change, context) -> str:
        quantity_delta = change.quantity
        if quantity_delta > 0:
            received_date = pd.Timestamp(change.received_date)
            current_date = pd.Timestamp(context.date)
            if received_date > current_date:
                raise ValueError("inventory adjustment.received_date cannot be in the future")
            if (current_date - received_date).days >= self.shelf_life_days:
                raise ValueError("inventory adjustment would add stock that is already expired")
            self.ledger.receive(change.unique_id, quantity_delta, received_date)
            return json.dumps([{
                "action": "receive",
                "received_date": received_date.isoformat(),
                "quantity": float(quantity_delta),
            }], sort_keys=True, separators=(",", ":"))
        if quantity_delta < 0:
            consumed = self.ledger.consume(change.unique_id, -quantity_delta)
            return json.dumps(
                [{"action": "consume", **row} for row in consumed],
                sort_keys=True,
                separators=(",", ":"),
            )
        return "[]"


class ShelfLifeEngine(SimulationEngine):
    """
    SimulationEngine with FIFO shelf-life expiry.

    The simple way to run shelf life: one engine, one shelf life, dated
    opening lots. Each run uses a ``ShelfLife`` process, so

        ShelfLifeEngine(shelf_life_days=3).run(..., opening_lots=lots)

    gives exactly the same results as

        SimulationEngine().run(..., processes=[ShelfLife(3, lots)])

    (apart from the manifest keys each form records). Each period, before
    demand is processed:
        1. Expired lots are removed from the ledger and deducted from on_hand.
        2. Stock arriving this period (in_transit[0]) is recorded as a new lot.
           With several suppliers, all of a SKU's deliveries due in the same
           period form one lot dated that period.

    After demand, fulfilled units are consumed from the oldest lots before
    typed physical callbacks run. The ledger is asserted to match Stockcast
    on_hand after demand and after every accepted callback batch.

    Opening lot ages are mandatory run inputs. Backorder clearances consume the
    same FIFO lots as current-period fulfilled demand. Further processes can
    be added with ``processes=[...]``; they run after shelf life.
    """

    def __init__(self, shelf_life_days: int, verbose: int = 0):
        super().__init__(verbose=verbose)
        _require_shelf_life_days(shelf_life_days)
        self.shelf_life_days = shelf_life_days
        self.ledger = FIFOLotLedger(self.shelf_life_days)
        self.expired_this_period: Dict[object, float] = {}

    def run(
        self,
        policy,
        demand_source,
        inventory,
        n_periods,
        *,
        period_frequency,
        initial_decision="none",
        warmup_periods,
        scoring_periods,
        settlement_periods,
        order_during_settlement,
        demand_source_name,
        random_seed,
        opening_lots,
        opening_expiry_handling="reject",
        policy_schedule=None,
        order_constraints=None,
        callbacks=None,
        supply=None,
        processes=None,
    ):
        shelf = ShelfLife(self.shelf_life_days, opening_lots, opening_expiry_handling)
        inventory = copy.deepcopy(inventory)
        shelf._prepare_opening(inventory)
        self._engine_processes = (shelf,)
        try:
            result = super().run(
                policy,
                demand_source,
                inventory,
                n_periods,
                period_frequency=period_frequency,
                initial_decision=initial_decision,
                warmup_periods=warmup_periods,
                scoring_periods=scoring_periods,
                settlement_periods=settlement_periods,
                order_during_settlement=order_during_settlement,
                demand_source_name=demand_source_name,
                random_seed=random_seed,
                policy_schedule=policy_schedule,
                order_constraints=order_constraints,
                callbacks=callbacks,
                supply=supply,
                processes=processes,
            )
        finally:
            del self._engine_processes
            self.ledger = shelf.ledger
            self.expired_this_period = shelf.expired_this_period
        shelf_settings = shelf._engine_settings()
        result.run_settings.update(shelf_settings)
        result.run_manifest["run_settings"].update(shelf_settings)
        return result

    def run_comparison(
        self,
        policies,
        demand_source,
        inventory,
        n_periods,
        *,
        period_frequency,
        initial_decision="none",
        warmup_periods,
        scoring_periods,
        settlement_periods,
        order_during_settlement,
        demand_source_name,
        random_seed,
        opening_lots,
        opening_expiry_handling="reject",
        labels=None,
        policy_schedules=None,
        order_constraints=None,
        callbacks=None,
        supply=None,
        processes=None,
    ):
        """Compare policies with identical demand and identical opening lots.

        Each branch reseeds a fresh FIFO ledger from ``opening_lots``; after
        the call, ``self.ledger`` reflects the last branch.
        """
        return self._run_comparison(
            policies, demand_source, inventory, n_periods,
            period_frequency=period_frequency,
            initial_decision=initial_decision,
            warmup_periods=warmup_periods,
            scoring_periods=scoring_periods,
            settlement_periods=settlement_periods,
            order_during_settlement=order_during_settlement,
            demand_source_name=demand_source_name,
            random_seed=random_seed,
            labels=labels,
            policy_schedules=policy_schedules,
            order_constraints=order_constraints,
            callbacks=callbacks,
            branch_run_options={
                "opening_lots": opening_lots,
                "opening_expiry_handling": opening_expiry_handling,
                "supply": supply,
                "processes": processes,
            },
        )
