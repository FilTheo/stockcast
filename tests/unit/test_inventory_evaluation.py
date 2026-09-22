import pandas as pd
import pytest

import stockcast.evaluation as evaluation
from stockcast.core.base_policy import BasePolicy
from stockcast.core.data_structures import InventoryStateDataFrame, OrderDecision
from stockcast.core.simulation_engine import RUN_MANIFEST_REQUIRED_SECTIONS, SimulationEngine
from stockcast.evaluation import (
    BaseInventoryMetric,
    CANONICAL_EVENT_COLUMNS,
    CoverageMetric,
    InventoryEvaluator,
    avg_on_hand,
    backlog_unit_periods,
    cycle_service_level,
    demand_units,
    demand_period_service_level,
    ending_on_hand_variance,
    fill_rate,
    inventory_turns,
    lost_sales_units,
    order_event_count,
    ordering_cost,
    peak_ending_on_hand,
    sku_order_line_count,
    stockout_period_rate,
    terminal_backlog_units,
    terminal_pipeline_units,
    total_cost,
)


def test_ambiguous_backorder_metric_is_absent():
    assert not hasattr(evaluation, "backorder_units_end")
    assert "backorder_units_end" not in evaluation.__all__


class FixedOrderPolicy(BasePolicy):
    def __init__(self, order_quantity: float, **kwargs):
        super().__init__(**kwargs)
        self.fixed_order_quantity = order_quantity
        self.fitted_ = True
        self.policy_name = "FixedOrderPolicy"

    def fit(self, forecast_df=None, **kwargs):
        self.fitted_ = True
        return self

    def predict(self, inventory_state_df, current_period=0, **kwargs):
        inventory_df = inventory_state_df.inventory_position()
        result_df = inventory_df[[inventory_state_df.sku_column, "inventory_position"]].copy()
        result_df["order_quantity"] = self.fixed_order_quantity
        result_df["target_level"] = result_df["inventory_position"] + self.fixed_order_quantity
        result_df["reorder_point"] = pd.NA
        result_df["order_period"] = current_period
        result_df["expected_delivery_period"] = current_period + self.lead_time
        return OrderDecision(
            result_df[[
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


class MaxBackordersMetric(BaseInventoryMetric):
    name = "max_backorders_end"

    def compute(self, event_frame, context):
        return float(event_frame["backorders_end"].max())


def test_simulation_result_exposes_normalized_event_frame():
    inventory = InventoryStateDataFrame(["SKU_A"], max_lead_time=2).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.data["on_hand"] = 5.0
    policy = FixedOrderPolicy(
        order_quantity=5.0,
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=True,
    )
    demand_df = pd.DataFrame(
        {
            "unique_id": ["SKU_A", "SKU_A"],
            "period": [0, 1],
            "date": [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")],
            "y": [3.0, 7.0],
        }
    )

    result = SimulationEngine().run(
        policy=policy,
        demand_source=demand_df,
        inventory=inventory,
        n_periods=2,
        period_frequency="D",
        initial_decision="none",
        warmup_periods=0,
        scoring_periods=2,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="unit_test",
        random_seed=None,
    )

    event_frame = result.to_event_frame()

    assert set(CANONICAL_EVENT_COLUMNS).issubset(event_frame.columns)
    assert set(RUN_MANIFEST_REQUIRED_SECTIONS).issubset(result.run_manifest)

    assert list(event_frame["period"]) == [1.0, 2.0]
    assert list(event_frame["demand_period"]) == [0, 1]
    assert result.run_settings["input_period_convention"] == "zero_based"
    assert list(event_frame["received_units"]) == [0.0, 5.0]
    assert list(event_frame["order_quantity"]) == [5.0, 5.0]
    assert list(event_frame["starting_on_hand"]) == [5.0, 2.0]
    assert list(event_frame["ending_on_hand"]) == [2.0, 0.0]
    assert list(event_frame["on_order_end"]) == [5.0, 5.0]
    assert event_frame["stockout_flag"].sum() == 0


def test_inventory_evaluator_supports_builtin_and_custom_metrics():
    inventory = InventoryStateDataFrame(["SKU_A"], max_lead_time=2).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    policy = FixedOrderPolicy(
        order_quantity=4.0,
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )
    demand_df = pd.DataFrame(
        {
            "unique_id": ["SKU_A", "SKU_A"],
            "period": [0, 1],
            "date": [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")],
            "y": [5.0, 1.0],
        }
    )

    result = SimulationEngine().run(
        policy=policy,
        demand_source=demand_df,
        inventory=inventory,
        n_periods=2,
        period_frequency="D",
        initial_decision="none",
        warmup_periods=0,
        scoring_periods=2,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="unit_test",
        random_seed=None,
    )

    external = InventoryEvaluator().fit(event_frame=result.to_event_frame())
    assert not external.event_frame_.empty
    invalid = result.to_event_frame()
    invalid.loc[invalid.index[0], "ending_on_hand"] += 1.0
    with pytest.raises(ValueError, match="physical inventory balance"):
        InventoryEvaluator().fit(event_frame=invalid)

    with pytest.raises(ValueError, match="window is required"):
        InventoryEvaluator().fit(simulation_result=result)
    evaluator = InventoryEvaluator().fit(simulation_result=result, window="scoring")
    with pytest.raises(ValueError, match="groupby must be explicit"):
        evaluator.evaluate(metrics=[fill_rate])
    metrics_df = evaluator.evaluate(
        metrics=[
            fill_rate,
            lost_sales_units,
            stockout_period_rate,
            avg_on_hand,
            total_cost,
            CoverageMetric(mode="trailing"),
            CoverageMetric(mode="forward"),
            MaxBackordersMetric(),
        ],
        groupby=["unique_id"],
        context={
            "cost_components": ["holding", "shortage", "ordering"],
            "holding_cost_per_unit_period": 1.0,
            "shortage_cost_per_unit": 2.0,
            "order_cost_per_sku_line": 3.0,
            "order_cost_per_unit": 0.5,
            "forward_demand_rate": 2.0,
        },
    )

    row = metrics_df.iloc[0]
    assert row["unique_id"] == "SKU_A"
    assert row["fill_rate"] == 1.0 / 6.0
    assert row["lost_sales_units"] == 5.0
    assert row["stockout_period_rate"] == 0.5
    assert row["avg_on_hand"] == 1.5
    assert row["total_cost"] == 23.0
    assert row["coverage_trailing"] == 0.5
    assert row["coverage_forward"] == 0.75
    assert row["max_backorders_end"] == 0.0


def test_cost_metrics_reject_implicit_zero_rates():
    events = pd.DataFrame({
        "event_type": ["period"],
        "ending_on_hand": [1.0],
        "shortage_units": [0.0],
        "order_quantity": [0.0],
    })

    with pytest.raises(ValueError, match="cost_components"):
        total_cost(events, {})
    with pytest.raises(ValueError, match="holding_cost_per_unit_period"):
        total_cost(events, {"cost_components": ["holding"]})


def test_metrics_reject_missing_event_quantities_instead_of_skipping_them():
    events = pd.DataFrame({
        "event_type": ["period", "period"],
        "demand": [1.0, pd.NA],
    })

    with pytest.raises(ValueError, match="event_frame.demand must be complete"):
        demand_units(events)


def test_cycle_service_uses_receipt_to_receipt_cycles():
    events = pd.DataFrame({
        "unique_id": ["A"] * 5,
        "event_type": ["period"] * 5,
        "period": [1, 2, 3, 4, 5],
        "received_units": [0.0, 5.0, 0.0, 5.0, 0.0],
        "shortage_units": [0.0, 0.0, 1.0, 0.0, 0.0],
        "demand": [1.0] * 5,
    })

    assert cycle_service_level(events, {"include_partial_cycles": False}) == 0.0
    assert cycle_service_level(events, {"include_partial_cycles": True}) == pytest.approx(2 / 3)
    assert demand_period_service_level(events) == 0.8
    with pytest.raises(ValueError, match="include_partial_cycles"):
        cycle_service_level(events)


def test_terminal_and_order_metrics_have_explicit_grain():
    events = pd.DataFrame({
        "unique_id": ["A", "B", "A", "B"],
        "event_type": ["period"] * 4,
        "period": [1, 1, 2, 2],
        "backorders_end": [1.0, 2.0, 3.0, 4.0],
        "on_order_end": [5.0, 6.0, 7.0, 8.0],
        "order_quantity": [2.0, 0.0, 3.0, 4.0],
        "order_event_count": [1, 0, 1, 0],
        "sku_order_line_count": [1, 0, 1, 1],
    })

    assert backlog_unit_periods(events) == 10.0
    assert terminal_backlog_units(events) == 7.0
    assert terminal_pipeline_units(events) == 15.0
    assert sku_order_line_count(events) == 3
    assert order_event_count(events) == 2


def test_fixed_ordering_cost_is_sku_level_and_row_order_independent():
    inventory = InventoryStateDataFrame(["A", "B"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    demand = pd.DataFrame({
        "unique_id": ["A", "B"],
        "period": [0, 0],
        "date": [pd.Timestamp("2025-01-02")] * 2,
        "y": [0.0, 0.0],
    })
    events = SimulationEngine().run(
        FixedOrderPolicy(
            order_quantity=2.0,
            lead_time=1,
            review_period=1,
            service_level=0.95,
            allow_backorders=False,
        ),
        demand,
        inventory,
        n_periods=1,
        period_frequency="D",
        initial_decision="none",
        warmup_periods=0,
        scoring_periods=1,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="ordering_cost_test",
        random_seed=None,
    ).to_event_frame()
    context = {
        "order_cost_per_sku_line": 5.0,
        "order_cost_per_unit": 0.0,
    }

    def by_sku(frame):
        result = InventoryEvaluator().fit(event_frame=frame).evaluate(
            [ordering_cost],
            groupby=["unique_id"],
            context=context,
        )
        return result.set_index("unique_id")["ordering_cost"].to_dict()

    assert by_sku(events) == {"A": 5.0, "B": 5.0}
    assert by_sku(events.iloc[::-1].reset_index(drop=True)) == {"B": 5.0, "A": 5.0}
    assert ordering_cost(events, context) == 10.0


def test_system_inventory_metrics_aggregate_skus_by_period():
    events = pd.DataFrame({
        "unique_id": ["A", "B", "A", "B"],
        "event_type": ["period"] * 4,
        "period": [1, 1, 2, 2],
        "demand_period": [0, 0, 1, 1],
        "ending_on_hand": [10.0, 0.0, 0.0, 10.0],
        "fulfilled_units": [2.0, 2.0, 2.0, 2.0],
    })

    assert ending_on_hand_variance(events) == 0.0
    assert peak_ending_on_hand(events) == 10.0
    assert inventory_turns(events, {"periods_per_year": 2}) == 0.8
