import numpy as np
import pandas as pd
import pytest
from test_data_structures import NoOrderPolicy

from stockcast import InventoryStateDataFrame
from stockcast.core import FIFOLotLedger, ShelfLifeEngine
from stockcast.core.base_policy import BasePolicy
from stockcast.core.data_structures import OrderDecision


class OrderOncePolicy(BasePolicy):
    """Orders a fixed quantity on one chosen period, nothing otherwise."""

    def __init__(self, order_period: int, order_qty: float, **kwargs):
        super().__init__(**kwargs)
        self.order_period = order_period
        self.order_qty = order_qty
        self.fitted_ = True

    def fit(self, forecast_df=None, **kwargs):
        self.fitted_ = True
        return self

    def predict(self, inventory_state_df, current_period=0, **kwargs):
        inv = inventory_state_df.inventory_position()
        result = inv[[inventory_state_df.sku_column, "inventory_position"]].copy()
        qty = self.order_qty if current_period == self.order_period else 0.0
        result["order_quantity"] = qty
        result["target_level"] = 0.0
        result["reorder_point"] = pd.NA
        result["order_period"] = current_period
        result["expected_delivery_period"] = current_period + self.lead_time
        return OrderDecision(
            result[[
                inventory_state_df.sku_column,
                "order_quantity",
                "target_level",
                "inventory_position",
                "reorder_point",
                "order_period",
                "expected_delivery_period",
            ]],
            sku_column=inventory_state_df.sku_column,
            lead_time=self.lead_time,
            review_period=self.review_period,
        )


def _daily_demand(values, start="2025-01-02"):
    dates = pd.date_range(start, periods=len(values), freq="D")
    return pd.DataFrame({
        "unique_id": ["A"] * len(values),
        "period": list(range(len(values))),
        "date": dates,
        "y": [float(v) for v in values],
    })


def _opening_lots(quantity, received_date="2025-01-01"):
    if quantity <= 0:
        return pd.DataFrame(columns=["unique_id", "received_date", "quantity"])
    return pd.DataFrame({
        "unique_id": ["A"],
        "received_date": [pd.Timestamp(received_date)],
        "quantity": [float(quantity)],
    })


def test_ledger_expires_oldest_lots_and_consumes_fifo():
    ledger = FIFOLotLedger(shelf_life_days=3)
    ledger.receive("A", 4.0, pd.Timestamp("2025-01-01"))
    ledger.receive("A", 2.0, pd.Timestamp("2025-01-02"))

    ledger.consume("A", 3.0)  # eats into the oldest lot only
    assert ledger.balances()["A"] == pytest.approx(3.0)

    expired = ledger.expire(pd.Timestamp("2025-01-04"))  # first lot is 3 days old
    assert expired == {"A": pytest.approx(1.0)}
    assert ledger.balances()["A"] == pytest.approx(2.0)


def test_ledger_rejects_invalid_shelf_life():
    with pytest.raises(ValueError):
        FIFOLotLedger(shelf_life_days=0)

    for value in [True, 3.5, "4"]:
        with pytest.raises(ValueError, match="integer >= 1"):
            ShelfLifeEngine(shelf_life_days=value)


def test_ledger_receive_requires_finite_nonnegative_quantity():
    ledger = FIFOLotLedger(shelf_life_days=3)
    for value in [True, -1.0, np.nan, np.inf, "not-a-number"]:
        with pytest.raises(ValueError, match="finite number >= 0"):
            ledger.receive("A", value, pd.Timestamp("2025-01-01"))

    with pytest.warns(RuntimeWarning, match="zero-quantity"):
        ledger.receive("A", 0.0, pd.Timestamp("2025-01-01"))
    assert ledger.balances().empty


def test_opening_lots_do_not_coerce_identifier_types():
    inventory = InventoryStateDataFrame(["1"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.data["on_hand"] = 1.0
    lots = pd.DataFrame({
        "unique_id": [1],
        "received_date": [pd.Timestamp("2025-01-01")],
        "quantity": [1.0],
    })

    with pytest.raises(ValueError, match="unknown SKUs"):
        FIFOLotLedger(shelf_life_days=3).seed_from_lots(inventory, lots)


def test_engine_expires_opening_stock_before_demand():
    inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.data["on_hand"] = 10.0
    policy = NoOrderPolicy(
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )
    demand = _daily_demand([2.0, 2.0, 2.0, 2.0])

    result = ShelfLifeEngine(shelf_life_days=2).run(
        policy,
        demand,
        inventory,
        n_periods=4,
        period_frequency="D",
        initial_decision="none",
        warmup_periods=0,
        scoring_periods=4,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="unit_test",
        random_seed=None,
        opening_lots=_opening_lots(10.0),
    )
    events = result.to_event_frame().set_index("period")
    opening_lot_manifest = result.run_manifest["run_settings"]["opening_lots"]
    assert opening_lot_manifest["rows"] == 1
    assert len(opening_lot_manifest["sha256"]) == 64

    # The explicitly dated Jan 1 opening lot serves Jan 2 and expires at the
    # start of Jan 3 with 8 units left.
    assert events.loc[1, "expired_units"] == 0.0
    assert events.loc[2, "expired_units"] == pytest.approx(8.0)
    assert events.loc[2, "starting_on_hand"] == 8.0
    assert events.loc[2, "ending_on_hand"] == 0.0
    assert events.loc[2, "shortage_units"] == pytest.approx(2.0)
    assert events.loc[3, "expired_units"] == 0.0
    assert events.loc[4, "expired_units"] == 0.0


def test_engine_tracks_arriving_lot_dates():
    inventory = InventoryStateDataFrame(["A"], max_lead_time=2).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    policy = OrderOncePolicy(
        order_period=1,
        order_qty=5.0,
        lead_time=2,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )
    demand = _daily_demand([0.0, 0.0, 1.0, 0.0, 0.0, 0.0])

    result = ShelfLifeEngine(shelf_life_days=3).run(
        policy,
        demand,
        inventory,
        n_periods=6,
        period_frequency="D",
        initial_decision="none",
        warmup_periods=0,
        scoring_periods=6,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="unit_test",
        random_seed=None,
        opening_lots=_opening_lots(0.0),
    )
    events = result.to_event_frame().set_index("period")

    # Order placed period 1 arrives period 3 (Jan 4), serves demand through
    # Jan 6, and the remaining 4 units expire at the start of Jan 7 (period 6).
    assert events.loc[3, "received_units"] == pytest.approx(5.0)
    assert events.loc[3, "expired_units"] == 0.0
    assert events.loc[3, "ending_on_hand"] == pytest.approx(4.0)
    assert events.loc[6, "expired_units"] == pytest.approx(4.0)
    assert events.loc[6, "ending_on_hand"] == 0.0


def test_engine_shelf_life_changes_outcomes():
    def run(shelf_life_days):
        inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
            start_date=pd.Timestamp("2025-01-01")
        )
        inventory.data["on_hand"] = 10.0
        policy = NoOrderPolicy(
            lead_time=1,
            review_period=1,
            service_level=0.95,
            allow_backorders=False,
        )
        demand = _daily_demand([1.0] * 6)
        result = ShelfLifeEngine(shelf_life_days=shelf_life_days).run(
            policy,
            demand,
            inventory,
            n_periods=6,
            period_frequency="D",
            initial_decision="none",
            warmup_periods=0,
            scoring_periods=6,
            settlement_periods=0,
            order_during_settlement=False,
            demand_source_name="unit_test",
            random_seed=None,
            opening_lots=_opening_lots(10.0),
        )
        return result.to_event_frame()

    short = run(1)
    long = run(30)
    assert short["expired_units"].sum() > 0
    assert long["expired_units"].sum() == 0
    assert short["shortage_units"].sum() > long["shortage_units"].sum()


def test_reused_engine_resets_fifo_ledger_between_runs():
    engine = ShelfLifeEngine(shelf_life_days=2)
    policy = NoOrderPolicy(
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )
    demand = _daily_demand([1.0, 1.0, 1.0])

    def run_once():
        inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
            start_date=pd.Timestamp("2025-01-01")
        )
        inventory.data["on_hand"] = 5.0
        return engine.run(
            policy,
            demand,
            inventory,
            n_periods=3,
            period_frequency="D",
            initial_decision="none",
            warmup_periods=0,
            scoring_periods=3,
            settlement_periods=0,
            order_during_settlement=False,
            demand_source_name="unit_test",
            random_seed=None,
            opening_lots=_opening_lots(5.0),
        ).to_event_frame()

    pd.testing.assert_frame_equal(run_once(), run_once())


def test_engine_fifo_accounts_for_backorder_clearance():
    inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    policy = OrderOncePolicy(
        order_period=1,
        order_qty=5.0,
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=True,
    )
    result = ShelfLifeEngine(shelf_life_days=3).run(
        policy,
        _daily_demand([3.0, 1.0]),
        inventory,
        n_periods=2,
        period_frequency="D",
        initial_decision="none",
        warmup_periods=0,
        scoring_periods=2,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="unit_test",
        random_seed=None,
        opening_lots=_opening_lots(0.0),
    )
    event = result.to_event_frame().set_index("period").loc[2]
    assert event["backorders_fulfilled"] == 3.0
    assert event["fulfilled_units"] == 1.0
    assert event["ending_on_hand"] == 1.0


def test_opening_lots_must_balance_opening_inventory():
    inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.data["on_hand"] = 5.0
    with pytest.raises(ValueError, match="exactly equal opening on_hand"):
        ShelfLifeEngine(shelf_life_days=3).run(
            NoOrderPolicy(
                lead_time=1,
                review_period=1,
                service_level=0.95,
                allow_backorders=False,
            ),
            _daily_demand([0.0]),
            inventory,
            n_periods=1,
            period_frequency="D",
            initial_decision="none",
            warmup_periods=0,
            scoring_periods=1,
            settlement_periods=0,
            order_during_settlement=False,
            demand_source_name="unit_test",
            random_seed=None,
            opening_lots=_opening_lots(4.0),
        )


def test_expired_opening_lots_reject_by_default_or_write_off_explicitly():
    inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.data["on_hand"] = 5.0
    stale_lots = pd.DataFrame({
        "unique_id": ["A"],
        "received_date": [pd.Timestamp("2024-12-29")],
        "quantity": [5.0],
    })
    kwargs = {
        "policy": NoOrderPolicy(
            lead_time=1, review_period=1, service_level=0.95,
            allow_backorders=False,
        ),
        "demand_source": _daily_demand([0.0]),
        "inventory": inventory,
        "n_periods": 1,
        "period_frequency": "D",
        "initial_decision": "before_first_demand",
        "warmup_periods": 0,
        "scoring_periods": 1,
        "settlement_periods": 0,
        "order_during_settlement": False,
        "demand_source_name": "opening_expiry_test",
        "random_seed": None,
        "opening_lots": stale_lots,
    }
    with pytest.raises(ValueError, match="already expired"):
        ShelfLifeEngine(shelf_life_days=3).run(**kwargs)
    with pytest.raises(ValueError, match="preprocessed.*no stock already expired"):
        ShelfLifeEngine(shelf_life_days=3).run(
            **kwargs,
            opening_expiry_handling="preprocessed",
        )

    result = ShelfLifeEngine(shelf_life_days=3).run(
        **kwargs,
        opening_expiry_handling="expire_before_initial_decision",
    )
    assert result.run_manifest["run_settings"]["opening_expired_units"] == [
        {"unique_id": "A", "quantity": 5.0}
    ]
    assert result.to_event_frame().iloc[0]["starting_on_hand"] == 0.0
    assert inventory.get_dataframe().iloc[0]["on_hand"] == 5.0

    clean_inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    clean_inventory.data["on_hand"] = 5.0
    clean_lots = stale_lots.copy()
    clean_lots["received_date"] = pd.Timestamp("2024-12-31")
    verified = ShelfLifeEngine(shelf_life_days=3).run(
        **{
            **kwargs,
            "inventory": clean_inventory,
            "opening_lots": clean_lots,
            "opening_expiry_handling": "preprocessed",
        }
    )
    assert verified.run_manifest["run_settings"]["opening_expiry_handling"] == "preprocessed"
    assert verified.run_manifest["run_settings"]["opening_expired_units"] == []
