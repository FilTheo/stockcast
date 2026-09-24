"""Frozen pre-process ShelfLifeEngine, kept only as a test oracle.

This is the hook-based ``ShelfLifeEngine`` exactly as it was before shelf life
became an ``InventoryProcess`` (checkpoint 0dd52a9), renamed
``LegacyShelfLifeEngine``. The migration tests require the current
``ShelfLifeEngine`` and ``SimulationEngine(processes=[ShelfLife(...)])`` to
reproduce its outputs exactly. Do not modernize this file.
"""

import copy
import json
from typing import Dict

import numpy as np
import pandas as pd

from stockcast.core.data_structures import InventoryStateDataFrame, _identifier_sample
from stockcast.core.shelf_life import FIFOLotLedger
from stockcast.core.simulation_engine import SimulationEngine


class LegacyShelfLifeEngine(SimulationEngine):
    """
    SimulationEngine with FIFO shelf-life expiry.

    Each period, before demand is processed:
        1. Expired lots are removed from the ledger and deducted from on_hand.
        2. Stock arriving this period (in_transit[0]) is recorded as a new lot.
           With several suppliers, all of a SKU's deliveries due in the same
           period form one lot dated that period.

    After demand, fulfilled units are consumed from the oldest lots before
    typed physical callbacks run. The ledger is asserted to match Stockcast
    on_hand after demand and after every accepted callback batch.

    Opening lot ages are mandatory run inputs. Backorder clearances consume the
    same FIFO lots as current-period fulfilled demand.
    """

    def __init__(self, shelf_life_days: int, verbose: int = 0):
        super().__init__(verbose=verbose)
        if (
            not isinstance(shelf_life_days, int)
            or isinstance(shelf_life_days, bool)
            or shelf_life_days < 1
        ):
            raise ValueError("shelf_life_days must be an integer >= 1")
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
    ):
        if opening_expiry_handling not in {
            "reject",
            "expire_before_initial_decision",
            "preprocessed",
        }:
            raise ValueError(
                "opening_expiry_handling must be 'reject', "
                "'expire_before_initial_decision', or 'preprocessed'"
            )
        inventory = copy.deepcopy(inventory)
        self.ledger = FIFOLotLedger(self.shelf_life_days)
        self.ledger.seed_from_lots(inventory, opening_lots)
        opening_date = pd.Timestamp(inventory.get_dataframe()["date"].iloc[0])
        stale_units = self.ledger.expire(opening_date)
        if stale_units and opening_expiry_handling in {"reject", "preprocessed"}:
            stale_skus = _identifier_sample(stale_units)
            if opening_expiry_handling == "preprocessed":
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
        if stale_units and opening_expiry_handling == "expire_before_initial_decision":
            for unique_id, quantity in stale_units.items():
                mask = inventory.data[inventory.sku_column] == unique_id
                inventory.data.loc[mask, "on_hand"] -= float(quantity)
            if (inventory.data["on_hand"] < -1e-9).any():
                raise ValueError("opening expired-lot write-off would make on_hand negative")
            inventory.data["on_hand"] = inventory.data["on_hand"].clip(lower=0.0)
        self.expired_this_period = {}
        opening_lot_data = opening_lots.copy()
        opening_lot_data["received_date"] = pd.to_datetime(
            opening_lot_data["received_date"]
        )
        opening_lot_data["quantity"] = pd.to_numeric(opening_lot_data["quantity"])
        opening_lot_fingerprint = {
            "sha256": self._dataframe_checksum(
                opening_lot_data,
                sort_columns=["unique_id", "received_date", "quantity"],
            ),
            "rows": len(opening_lot_data),
            "columns": list(opening_lot_data.columns),
        }
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
        )
        shelf_settings = {
            "shelf_life": self.shelf_life_days,
            "shelf_life_unit": "calendar_days",
            "opening_lot_count": len(opening_lots),
            "opening_lots": opening_lot_fingerprint,
            "opening_expiry_handling": opening_expiry_handling,
            "opening_expired_units": [
                {"unique_id": sku, "quantity": float(stale_units[sku])}
                for sku in sorted(
                    stale_units,
                    key=lambda value: (type(value).__name__, repr(value)),
                )
            ],
        }
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
            },
        )

    def _arriving_quantities(self, inventory: InventoryStateDataFrame) -> Dict[object, float]:
        state = inventory.get_dataframe()
        arriving: Dict[object, float] = {}
        for unique_id, pipeline in state[
            [inventory.sku_column, "in_transit"]
        ].itertuples(index=False, name=None):
            in_transit = (
                pipeline
                if isinstance(pipeline, np.ndarray)
                else np.zeros(inventory.max_lead_time)
            )
            qty = float(in_transit[0]) if len(in_transit) else 0.0
            if qty > 0:
                arriving[unique_id] = qty
        return arriving

    def _current_date(self, inventory, demand_df) -> pd.Timestamp:
        if "date" not in demand_df.columns or not len(demand_df):
            raise ValueError("shelf-life demand requires an explicit period date")
        return pd.Timestamp(demand_df["date"].iloc[0])

    def _before_demand_transition(self, inventory, demand_df, period):
        current_date = self._current_date(inventory, demand_df)
        self.expired_this_period = self.ledger.expire(current_date)
        if self.expired_this_period:
            expired = (
                inventory.data[inventory.sku_column].map(self.expired_this_period).fillna(0.0)
            )
            inventory.data["on_hand"] = (inventory.data["on_hand"] - expired).clip(lower=0.0)
        for unique_id, qty in self._arriving_quantities(inventory).items():
            self.ledger.receive(unique_id, qty, current_date)
        return inventory

    def _after_order_receipt(self, before, after):
        prior = before.data.set_index(before.sku_column)["latest_received"]
        for sku, received, date in after.data[[
            after.sku_column, "latest_received", "date"
        ]].itertuples(index=False, name=None):
            quantity = float(received - prior.loc[sku])
            if quantity > 0:
                self.ledger.receive(sku, quantity, pd.Timestamp(date))

    def _after_demand_transition(self, inventory, period):
        state = inventory.get_dataframe()
        for unique_id, fulfilled, backorders_fulfilled in state[[
            inventory.sku_column,
            "latest_fulfilled",
            "latest_backorders_fulfilled",
        ]].itertuples(index=False, name=None):
            self.ledger.consume(
                unique_id,
                float(fulfilled) + float(backorders_fulfilled),
            )
        self._assert_lot_balance(inventory)
        return inventory

    # Array forms of the hooks above, used while the engine holds NumPy state.
    # Each performs the same ledger calls, in the same SKU order.

    def _before_demand_arrays(self, state, current_date):
        self.expired_this_period = self.ledger.expire(current_date)
        if self.expired_this_period:
            remaining = state.on_hand - state.map_by_sku(self.expired_this_period, 0.0)
            # Series.clip(lower=0.0): keep values >= 0 (and NaN), else 0.0.
            state = state.with_on_hand(np.where(remaining < 0.0, 0.0, remaining))
        if state.schema.max_lead_time:
            arriving = state.pipeline[:, 0]
            skus = state.schema.sku_list()
            for position in np.flatnonzero(arriving > 0):
                self.ledger.receive(skus[position], float(arriving[position]), current_date)
        return state

    def _after_order_receipt_arrays(self, before, after):
        received = (after.latest["latest_received"] - before.latest["latest_received"]).tolist()
        for sku, quantity in zip(after.schema.sku_list(), received):
            if quantity > 0:
                self.ledger.receive(sku, quantity, after.date)

    def _after_demand_arrays(self, state):
        for unique_id, fulfilled, backorders_fulfilled in zip(
            state.schema.sku_list(),
            state.latest["latest_fulfilled"].tolist(),
            state.latest["latest_backorders_fulfilled"].tolist(),
        ):
            self.ledger.consume(unique_id, fulfilled + backorders_fulfilled)
        lots_by_sku = self.ledger.lots_by_sku
        expected = np.array([
            sum(float(lot["qty"]) for lot in lots_by_sku[unique_id])
            if unique_id in lots_by_sku else 0.0
            for unique_id in state.schema.sku_list()
        ], dtype=float)
        if not np.allclose(state.on_hand, expected, atol=1e-6):
            raise AssertionError("FIFO shelf-life ledger no longer matches Stockcast on_hand")

    def _period_expired_units(self):
        return dict(self.expired_this_period)

    def _apply_lot_adjustment(
        self, unique_id, quantity_delta, received_date, current_date
    ) -> str:
        if quantity_delta > 0:
            if pd.isna(received_date):
                raise ValueError(
                    "positive ShelfLifeEngine inventory adjustments require received_date"
                )
            received_date = pd.Timestamp(received_date)
            current_date = pd.Timestamp(current_date)
            if received_date > current_date:
                raise ValueError("inventory adjustment.received_date cannot be in the future")
            if (current_date - received_date).days >= self.shelf_life_days:
                raise ValueError("inventory adjustment would add stock that is already expired")
            self.ledger.receive(unique_id, quantity_delta, received_date)
            return json.dumps([{
                "action": "receive",
                "received_date": received_date.isoformat(),
                "quantity": float(quantity_delta),
            }], sort_keys=True, separators=(",", ":"))
        if quantity_delta < 0:
            consumed = self.ledger.consume(unique_id, -quantity_delta)
            return json.dumps(
                [{"action": "consume", **row} for row in consumed],
                sort_keys=True,
                separators=(",", ":"),
            )
        return "[]"

    def _validate_lot_adjustment(
        self, unique_id, quantity_delta, received_date, current_date
    ):
        super()._validate_lot_adjustment(
            unique_id, quantity_delta, received_date, current_date
        )
        if quantity_delta > 0:
            if pd.isna(received_date):
                raise ValueError(
                    "positive ShelfLifeEngine inventory adjustments require received_date"
                )
            if (pd.Timestamp(current_date) - pd.Timestamp(received_date)).days >= self.shelf_life_days:
                raise ValueError("inventory adjustment would add stock that is already expired")

    def _assert_lot_balance(self, inventory):
        lot_balance = self.ledger.balances()
        state = inventory.get_dataframe()
        expected = state[inventory.sku_column].map(lot_balance).fillna(0.0).to_numpy(dtype=float)
        actual = state["on_hand"].to_numpy(dtype=float)
        if not np.allclose(actual, expected, atol=1e-6):
            raise AssertionError("FIFO shelf-life ledger no longer matches Stockcast on_hand")

    def _after_inventory_adjustment_batch(self, inventory):
        self._assert_lot_balance(inventory)
