import pandas as pd
import pytest

import stockcast
from stockcast import InventoryStateDataFrame, OrderUpToPolicy, SimulationEngine
from stockcast.core.base_policy import BasePolicy
from stockcast.core.data_structures import OrderDecision
from stockcast.policies import ReorderPointPolicy
from stockcast.utils import update_inventory_with_orders
from stockcast.visualization import (
    plot_demand_vs_orders,
    plot_inventory,
    plot_simulation_dashboard,
)


class NoOrderPolicy(BasePolicy):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fitted_ = True

    def fit(self, forecast_df=None, **kwargs):
        self.fitted_ = True
        return self

    def predict(self, inventory_state_df, current_period=0, **kwargs):
        inv = inventory_state_df.inventory_position()
        result = inv[[inventory_state_df.sku_column, "inventory_position"]].copy()
        result["order_quantity"] = 0.0
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
        )


def test_public_stockcast_exports():
    assert stockcast.InventoryStateDataFrame is InventoryStateDataFrame
    assert stockcast.OrderUpToPolicy is OrderUpToPolicy
    assert stockcast.SimulationEngine is SimulationEngine


def test_summary_uses_event_frame_and_catches_final_stockout():
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
    demand = pd.DataFrame({
        "unique_id": ["A", "A"],
        "period": [0, 1],
        "date": [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")],
        "y": [0.0, 20.0],
    })

    result = SimulationEngine().run(
        policy,
        demand,
        inventory,
        n_periods=2,
        freq="D",
        warmup_periods=0,
        scoring_periods=2,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="unit_test",
        random_seed=None,
    )
    summary = result.summary()

    assert summary["fill_rate"] == 0.5
    assert summary["demand_period_service_level"] == 0.0
    assert summary["stockout_periods"] == 1


def test_summary_rejects_missing_event_quantities_instead_of_using_zero():
    inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    policy = NoOrderPolicy(
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [1.0],
    })
    result = SimulationEngine().run(
        policy,
        demand,
        inventory,
        n_periods=1,
        freq="D",
        warmup_periods=0,
        scoring_periods=1,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="unit_test",
        random_seed=None,
    )
    result._event_frame.loc[:, "demand"] = float("nan")

    with pytest.raises(ValueError, match="period events must contain complete"):
        result.summary()


def test_simulation_engine_exposes_no_live_state_hook_surface():
    for name in (
        "before_step",
        "before_demand",
        "on_stockout",
        "on_review_period",
        "after_period_event",
        "after_step",
    ):
        assert not hasattr(SimulationEngine, name)


def test_negative_demand_is_rejected():
    inventory = InventoryStateDataFrame(
        ["A"], max_lead_time=1, allow_backorders=False
    ).initialize_zero(start_date=pd.Timestamp("2025-01-01"))
    demand = pd.DataFrame({"unique_id": ["A"], "y": [-1.0]})

    with pytest.raises(ValueError, match="demand_df.y"):
        inventory.process_demand(demand, freq="D")


def test_duplicate_demand_rows_are_rejected():
    inventory = InventoryStateDataFrame(
        ["A"], max_lead_time=1, allow_backorders=False
    ).initialize_zero(start_date=pd.Timestamp("2025-01-01"))
    demand = pd.DataFrame({"unique_id": ["A", "A"], "y": [1.0, 2.0]})

    with pytest.raises(ValueError, match="duplicate"):
        inventory.process_demand(demand, freq="D")


def test_unknown_demand_sku_is_rejected():
    inventory = InventoryStateDataFrame(
        ["A"], max_lead_time=1, allow_backorders=False
    ).initialize_zero(start_date=pd.Timestamp("2025-01-01"))
    demand = pd.DataFrame({"unique_id": ["B"], "y": [1.0]})

    with pytest.raises(ValueError, match="unknown SKUs"):
        inventory.process_demand(demand, freq="D")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lead_time": -1, "review_period": 1, "service_level": 0.95, "allow_backorders": False},
        {"lead_time": 1, "review_period": 0, "service_level": 0.95, "allow_backorders": False},
        {"lead_time": 1, "review_period": 1, "service_level": 0, "allow_backorders": False},
        {"lead_time": 1, "review_period": 1, "service_level": 1, "allow_backorders": False},
        {"lead_time": 1, "review_period": 1, "service_level": 1.1, "allow_backorders": False},
    ],
)
def test_invalid_policy_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        OrderUpToPolicy(**kwargs)


def test_invalid_max_lead_time_is_rejected():
    with pytest.raises(ValueError, match="max_lead_time"):
        InventoryStateDataFrame(["A"], max_lead_time=-1)
    with pytest.raises(ValueError, match="max_lead_time"):
        InventoryStateDataFrame(["A"], max_lead_time=True)


def test_inventory_identifiers_are_complete_unique_and_type_consistent():
    with pytest.raises(ValueError, match="blank strings"):
        InventoryStateDataFrame(["A", " "], max_lead_time=1)
    with pytest.raises(ValueError, match="one identifier type"):
        InventoryStateDataFrame(["1", 2], max_lead_time=1)
    with pytest.raises(ValueError, match="duplicate"):
        InventoryStateDataFrame(["A", "A"], max_lead_time=1)


def test_opening_backlog_and_pipeline_are_not_silently_invented():
    inventory = InventoryStateDataFrame(
        pd.DataFrame({
            "unique_id": ["A"],
            "on_hand": [1.0],
            "period": [0],
            "date": [pd.Timestamp("2025-01-01")],
        }),
        max_lead_time=1,
        allow_backorders=False,
    )

    with pytest.raises(ValueError, match="backorders"):
        inventory._validate_ready_state()
    inventory.data["backorders"] = 0.0
    with pytest.raises(ValueError, match="in_transit"):
        inventory._validate_ready_state()


def test_opening_state_rejects_physically_inconsistent_backlog():
    inventory = InventoryStateDataFrame(
        ["A"],
        max_lead_time=1,
        allow_backorders=True,
    ).initialize_zero(start_date=pd.Timestamp("2025-01-01"))
    inventory.data["on_hand"] = 2.0
    inventory.data["backorders"] = 1.0

    with pytest.raises(ValueError, match="positive on_hand and backorders"):
        inventory._validate_ready_state()

    inventory.data["on_hand"] = 0.0
    inventory.allow_backorders = False
    with pytest.raises(ValueError, match="must be zero"):
        inventory._validate_ready_state()


def test_multiple_order_events_accumulate_direct_period_flow():
    inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.allow_backorders = False

    def decision(quantity):
        return OrderDecision(
            pd.DataFrame({
                "unique_id": ["A"],
                "order_quantity": [quantity],
                "target_level": [quantity],
                "order_period": [0],
                "expected_delivery_period": [1],
            }),
            lead_time=1,
        )

    inventory = update_inventory_with_orders(inventory, decision(2.0))
    inventory = update_inventory_with_orders(inventory, decision(3.0))

    assert inventory.data.loc[0, "latest_order"] == 5.0
    assert inventory.data.loc[0, "in_transit"].sum() == 5.0


def test_expected_delivery_metadata_must_match_physical_lead_time():
    inventory = InventoryStateDataFrame(["A"], max_lead_time=2).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.allow_backorders = False
    decision = OrderDecision(
        pd.DataFrame({
            "unique_id": ["A"],
            "order_quantity": [2.0],
            "target_level": [2.0],
            "order_period": [0],
            "expected_delivery_period": [1],
        }),
        lead_time=2,
    )

    with pytest.raises(ValueError, match=r"must equal order_period \+ lead_time"):
        update_inventory_with_orders(inventory, decision)


def test_forecasts_are_sorted_by_horizon_before_target_calculation():
    forecast = pd.DataFrame({
        "unique_id": ["A"] * 4,
        "fh": [4, 3, 1, 2],
        "date": pd.to_datetime(["2025-01-05", "2025-01-04", "2025-01-02", "2025-01-03"]),
        "mean": [2000.0, 1000.0, 10.0, 20.0],
        "std": [0.0, 0.0, 0.0, 0.0],
    })

    policy = OrderUpToPolicy(
        lead_time=1,
        review_period=1,
        freq="D",
        service_level=0.5,
        allow_backorders=False,
    ).fit(
        forecast,
        mean_column="mean",
        std_column="std",
        target_probability=0.5,
        protection_horizon=2,
        forecast_origin=pd.Timestamp("2025-01-01"),
        forecast_date_column="date",
    )

    assert policy.get_target_levels()["target_level"].iloc[0] == 30.0


def test_missing_forecast_horizon_fails_fast():
    forecast = pd.DataFrame({
        "unique_id": ["A", "A"],
        "fh": [1, 2],
        "date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
        "mean": [10.0, 20.0],
        "std": [1.0, 1.0],
    })

    with pytest.raises(ValueError, match="consecutive fh 1..3"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=2,
            freq="D",
            service_level=0.95,
            allow_backorders=False,
        ).fit(
            forecast,
            mean_column="mean",
            std_column="std",
            target_probability=0.95,
            protection_horizon=3,
            forecast_origin=pd.Timestamp("2025-01-01"),
            forecast_date_column="date",
        )


def test_precomputed_one_row_target_is_allowed():
    forecast = pd.DataFrame({
        "unique_id": ["A"],
        "target": [123.0],
        "target_end": [pd.Timestamp("2025-01-15")],
    })

    policy = OrderUpToPolicy(
        lead_time=7,
        review_period=7,
        freq="D",
        service_level=0.95,
        allow_backorders=False,
    ).fit(
        forecast,
        target_column="target",
        target_probability=0.95,
        protection_horizon=14,
        forecast_origin=pd.Timestamp("2025-01-01"),
        target_end_date_column="target_end",
    )

    assert policy.get_target_levels()["target_level"].iloc[0] == 123.0


def test_heuristic_initializers_are_absent():
    assert not hasattr(InventoryStateDataFrame, "initialize_from_forecast")
    assert not hasattr(InventoryStateDataFrame, "initialize_from_historical_data")


def test_observed_opening_stock_requires_a_complete_sku_grid():
    inventory = InventoryStateDataFrame(["A", "B"], max_lead_time=1)
    opening = pd.DataFrame({"unique_id": ["A", "B"], "observed": [3.0, 5.0]})

    inventory.initialize_from_observed(
        opening,
        on_hand_column="observed",
        start_date=pd.Timestamp("2025-01-01"),
    )
    assert inventory.get_dataframe()["on_hand"].tolist() == [3.0, 5.0]

    with pytest.raises(ValueError, match="exactly the inventory SKUs"):
        inventory.initialize_from_observed(
            opening.iloc[:1],
            on_hand_column="observed",
            start_date=pd.Timestamp("2025-01-01"),
        )


def test_reorder_point_validates_schedule_protection_window():
    targets = pd.DataFrame({
        "unique_id": ["A"],
        "reorder_point": [10.0],
        "reorder_end": [pd.Timestamp("2025-01-04")],
    })
    policy = ReorderPointPolicy(
        lead_time=3,
        review_period=1,
        freq="D",
        policy_type="sQ",
        service_level=0.95,
        order_quantity=5,
        allow_backorders=False,
    )

    # Every-period review: the window is L + R = 3 + 1, not the lead time alone.
    with pytest.raises(ValueError, match="policy horizon 4"):
        policy.fit(
            targets,
            reorder_point_column="reorder_point",
            target_probability=0.95,
            reorder_horizon=3,
            forecast_origin=pd.Timestamp("2025-01-01"),
            reorder_end_date_column="reorder_end",
        )


def test_plots_work_from_event_frame():
    inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.data["on_hand"] = 5.0
    policy = NoOrderPolicy(
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [2.0],
    })
    result = SimulationEngine().run(
        policy,
        demand,
        inventory,
        n_periods=1,
        freq="D",
        warmup_periods=0,
        scoring_periods=1,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="unit_test",
        random_seed=None,
    )

    assert plot_inventory(result) is not None
    assert plot_demand_vs_orders(result) is not None
    assert len(plot_simulation_dashboard(result)) == 2


def test_plot_inventory_accepts_numeric_sku_scalar():
    inventory = InventoryStateDataFrame([101], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    policy = NoOrderPolicy(
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )
    demand = pd.DataFrame({
        "unique_id": [101],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [0.0],
    })
    result = SimulationEngine().run(
        policy,
        demand,
        inventory,
        n_periods=1,
        freq="D",
        warmup_periods=0,
        scoring_periods=1,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="numeric_sku_test",
        random_seed=None,
    )

    assert plot_inventory(result, sku=101) is not None


@pytest.mark.parametrize("data", [[], pd.DataFrame({"unique_id": []})])
def test_empty_inventory_sku_universe_is_rejected(data):
    with pytest.raises(ValueError, match="SKU universe must be non-empty"):
        InventoryStateDataFrame(data, max_lead_time=1)


def _counted(on_hand=(40.0,), **kwargs):
    skus = ["a", "b", "c"][:len(on_hand)]
    return InventoryStateDataFrame.from_observed(
        pd.DataFrame({"unique_id": skus, "on_hand": list(on_hand)}),
        start_date=pd.Timestamp("2026-03-23"), **kwargs,
    )


def test_from_observed_equals_the_two_step_initializer():
    one_step = _counted((40.0, 12.0), max_lead_time=2, allow_backorders=False)
    two_step = InventoryStateDataFrame(
        ["a", "b"], max_lead_time=2, allow_backorders=False,
    ).initialize_from_observed(
        pd.DataFrame({"unique_id": ["a", "b"], "on_hand": [40.0, 12.0]}),
        on_hand_column="on_hand", start_date=pd.Timestamp("2026-03-23"),
    )
    pd.testing.assert_frame_equal(one_step.get_dataframe(), two_step.get_dataframe())
    assert one_step.max_lead_time == two_step.max_lead_time == 2
    with pytest.raises(ValueError, match="duplicate|unique"):
        InventoryStateDataFrame.from_observed(
            pd.DataFrame({"unique_id": ["a", "a"], "on_hand": [1.0, 2.0]}),
            start_date=pd.Timestamp("2026-03-23"),
        )


def test_unsized_pipeline_grows_only_with_empty_far_slots():
    state = _counted(allow_backorders=False)
    assert state.max_lead_time == 0
    decision = OrderDecision(
        pd.DataFrame({
            "unique_id": ["a"], "order_quantity": [5.0],
            "order_period": [0], "expected_delivery_period": [3],
        }),
        lead_time=3,
    )
    assert repr(decision).endswith("lead_time=3)")
    ordered = update_inventory_with_orders(state, decision)
    assert ordered.max_lead_time == 3
    assert ordered.data["in_transit"].iloc[0].tolist() == [0.0, 0.0, 5.0]
    assert state.max_lead_time == 0  # the caller's state is unchanged
    fixed = _counted(max_lead_time=2, allow_backorders=False)
    with pytest.raises(ValueError, match="must be >= orders.lead_time"):
        update_inventory_with_orders(fixed, decision)


def test_unsized_pipeline_fits_declared_open_orders():
    state = _counted().with_open_orders(
        pd.DataFrame({"unique_id": ["a"], "due_period": [3], "quantity": [4.0]})
    )
    assert state.max_lead_time == 3
    assert state.data["in_transit"].iloc[0].tolist() == [0.0, 0.0, 4.0]
    with pytest.raises(ValueError, match="due_period must satisfy"):
        _counted(max_lead_time=2).with_open_orders(
            pd.DataFrame({"unique_id": ["a"], "due_period": [3], "quantity": [4.0]})
        )


def test_positions_can_be_read_before_the_shortage_mode_is_set():
    state = _counted((5.0,))
    assert state.allow_backorders is None
    assert state.inventory_position()["inventory_position"].tolist() == [5.0]
    with pytest.raises(ValueError, match="allow_backorders must be explicitly supplied"):
        state.advance_period(freq="D", is_review_period=False)
