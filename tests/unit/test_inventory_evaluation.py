import numpy as np
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
    TotalCost,
    avg_on_hand,
    backlog_unit_periods,
    cycle_service_level,
    demand_units,
    demand_period_service_level,
    ending_on_hand_variance,
    fill_rate,
    holding_cost,
    inventory_turns,
    lost_sales_units,
    order_event_count,
    ordering_cost,
    peak_ending_on_hand,
    shortage_cost,
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
        freq="D",
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
        freq="D",
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

    evaluator = InventoryEvaluator().fit(simulation_result=result, window="scoring")
    # Defaults: the scoring window and one pooled row.
    default = InventoryEvaluator().fit(result)
    pd.testing.assert_frame_equal(default.event_frame_, evaluator.event_frame_)
    pd.testing.assert_frame_equal(
        default.evaluate([fill_rate]), evaluator.evaluate([fill_rate], groupby=[]),
    )
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


def test_cycle_service_counts_arrival_cycles_per_sku_in_any_row_order():
    # A: arrivals in periods 2, 4, 6 -> cycles {1}, {2, 3}, {4, 5}, {6}; shortages in 1, 3, 6,
    # so only {4, 5} is clean. B: no arrival and no shortage -> one partial cycle.
    events = pd.DataFrame({
        "unique_id": ["A"] * 6 + ["B"] * 3,
        "event_type": ["period"] * 9,
        "period": [1, 2, 3, 4, 5, 6, 1, 2, 3],
        "order_arrival_flag": [False, True, False, True, False, True, False, False, False],
        "received_units": [0.0] * 9,
        "shortage_units": [1.0, 0.0, 1.0, 0.0, 0.0, 2.0, 0.0, 0.0, 0.0],
    }).sample(frac=1.0, random_state=3)
    complete, partial = {"include_partial_cycles": False}, {"include_partial_cycles": True}
    sku_a, sku_b = events[events["unique_id"] == "A"], events[events["unique_id"] == "B"]

    assert cycle_service_level(sku_a, complete) == 0.5
    assert cycle_service_level(sku_a, partial) == 0.25
    assert np.isnan(cycle_service_level(sku_b, complete))
    assert cycle_service_level(sku_b, partial) == 1.0
    assert cycle_service_level(events, complete) == 0.5
    assert cycle_service_level(events, partial) == 0.4
    # A copy of the portfolio under new SKU ids doubles every count: same share.
    doubled = pd.concat([events, events.assign(unique_id=events["unique_id"] + "2")])
    assert cycle_service_level(doubled, partial) == 0.4


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
        freq="D",
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
        "backorders_fulfilled": [0.0, 0.0, 0.0, 0.0],
    })

    assert ending_on_hand_variance(events) == 0.0
    assert peak_ending_on_hand(events) == 10.0
    assert inventory_turns(events, {"periods_per_year": 2}) == 0.8
    # Backorders served later also leave the shelf: (8 + 2) / 2 periods * 2 / 10.
    events["backorders_fulfilled"] = [1.0, 0.0, 0.0, 1.0]
    assert inventory_turns(events, {"periods_per_year": 2}) == 1.0


def test_multi_sku_coverage_needs_no_grain_confirmation():
    inventory = InventoryStateDataFrame(["A", "B"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.data["on_hand"] = [4.0, 8.0]
    policy = FixedOrderPolicy(
        order_quantity=0.0, lead_time=1, review_period=1, allow_backorders=False,
    )
    demand = pd.DataFrame({
        "unique_id": ["A", "B"],
        "period": [0, 0],
        "date": [pd.Timestamp("2025-01-02")] * 2,
        "y": [2.0, 2.0],
    })
    result = SimulationEngine().run(
        policy=policy, demand_source=demand, inventory=inventory, freq="D",
    )
    evaluator = InventoryEvaluator().fit(result, window="scoring")
    row = evaluator.evaluate(
        [CoverageMetric(mode="forward")], groupby=[], context={"forward_demand_rate": 2.0},
    ).iloc[0]
    # Mean of the SKU-period ratios: (2 / 2 + 6 / 2) / 2.
    assert row["coverage_forward"] == 2.0


def _costed_run():
    inventory = InventoryStateDataFrame(["A", "B"], max_lead_time=2).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.data["on_hand"] = [3.0, 9.0]
    policy = FixedOrderPolicy(
        order_quantity=4.0, lead_time=2, review_period=2, allow_backorders=True,
    )
    demand = pd.DataFrame({
        "unique_id": ["A", "B"] * 4,
        "period": [0, 0, 1, 1, 2, 2, 3, 3],
        "date": [pd.Timestamp("2025-01-02") + pd.Timedelta(days=p) for p in [0, 0, 1, 1, 2, 2, 3, 3]],
        "y": [5.0, 2.0, 4.0, 1.0, 6.0, 3.0, 2.0, 2.0],
    })
    return SimulationEngine().run(
        policy=policy, demand_source=demand, inventory=inventory, freq="D",
    )


def test_total_cost_object_equals_the_context_route():
    evaluator = InventoryEvaluator().fit(_costed_run())
    rates = {
        "holding": 0.2, "shortage": 1.0, "backlog": 0.5,
        "order_per_line": 3.0, "order_per_unit": 0.1, "purchase": 2.0,
        "terminal_backlog": 4.0, "terminal_pipeline": 1.5,
        "salvage_on_hand": 0.3, "salvage_pipeline": 0.0,
    }
    context = {
        "cost_components": [
            "holding", "shortage", "backlog", "ordering", "purchase",
            "terminal_backlog", "terminal_pipeline", "salvage",
        ],
        "holding_cost_per_unit_period": 0.2, "shortage_cost_per_unit": 1.0,
        "backlog_cost_per_unit_period": 0.5, "order_cost_per_sku_line": 3.0,
        "order_cost_per_unit": 0.1, "purchase_cost_per_unit": 2.0,
        "terminal_backlog_cost_per_unit": 4.0, "terminal_pipeline_cost_per_unit": 1.5,
        "on_hand_salvage_per_unit": 0.3, "pipeline_salvage_per_unit": 0.0,
    }
    for groupby in ([], ["unique_id"]):
        by_object = evaluator.evaluate([TotalCost(**rates)], groupby=groupby)
        by_context = evaluator.evaluate([total_cost], groupby=groupby, context=context)
        pd.testing.assert_frame_equal(by_object, by_context)
    two = evaluator.evaluate([TotalCost(holding=0.2, shortage=1.0)])
    both = evaluator.evaluate([holding_cost, shortage_cost], context=context)
    assert two.loc[0, "total_cost"] == pytest.approx(
        both.loc[0, "holding_cost"] + both.loc[0, "shortage_cost"]
    )


def test_total_cost_object_rejects_ambiguous_rates():
    with pytest.raises(ValueError, match="at least one cost rate"):
        TotalCost()
    with pytest.raises(ValueError, match="both order_per_line and order_per_unit"):
        TotalCost(order_per_line=3.0)
    with pytest.raises(ValueError, match="finite non-negative"):
        TotalCost(holding=-1.0)
    evaluator = InventoryEvaluator().fit(_costed_run())
    with pytest.raises(ValueError, match="differs between context and TotalCost"):
        evaluator.evaluate(
            [TotalCost(holding=0.2)], context={"holding_cost_per_unit_period": 0.3},
        )
    ledger = evaluator.event_frame_.assign(holding_cost_per_unit_period=0.2)
    with pytest.raises(ValueError, match="both a ledger column and a TotalCost"):
        InventoryEvaluator().fit(event_frame=ledger).evaluate([TotalCost(holding=0.2)])


def test_grouping_by_one_column_is_warning_free():
    import warnings

    result = _costed_run()
    evaluator = InventoryEvaluator().fit(result)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        by_sku = evaluator.evaluate([fill_rate], groupby=["unique_id"])
        by_two = evaluator.evaluate([fill_rate], groupby=["unique_id", "run_window"])
    assert by_sku["unique_id"].tolist() == ["A", "B"]
    assert by_two[["unique_id", "run_window"]].values.tolist() == [["A", "scoring"], ["B", "scoring"]]


def _turns_run(frequency, n_periods=6):
    inventory = InventoryStateDataFrame(["A"], max_lead_time=1).initialize_zero(
        start_date=pd.Timestamp("2025-01-06")
    )
    inventory.data["on_hand"] = 30.0
    policy = FixedOrderPolicy(
        order_quantity=4.0, lead_time=1, review_period=1, allow_backorders=False,
    )
    dates = pd.date_range(
        pd.Timestamp("2025-01-06") + pd.tseries.frequencies.to_offset(frequency),
        periods=n_periods, freq=frequency,
    )
    demand = pd.DataFrame({"unique_id": "A", "date": dates, "y": 5.0})
    return SimulationEngine().run(
        policy=policy, demand_source=demand, inventory=inventory, freq=frequency,
    ).to_event_frame()


@pytest.mark.parametrize(
    ("frequency", "per_year"),
    [("D", 365), ("W-MON", 52), ("MS", 12), ("2D", 182.5)],
)
def test_inventory_turns_reads_the_period_length_from_the_dates(frequency, per_year):
    events = _turns_run(frequency)
    assert inventory_turns(events) == inventory_turns(events, {"periods_per_year": per_year})


def test_inventory_turns_asks_when_the_period_length_is_unclear():
    with pytest.raises(ValueError, match="no standard number of '[hH]' periods per year"):
        inventory_turns(_turns_run("h"))
    with pytest.raises(ValueError, match="fewer than three dates"):
        inventory_turns(_turns_run("D", n_periods=2))
    irregular = _turns_run("D", n_periods=4)
    irregular.loc[irregular.index[-1], "date"] += pd.Timedelta(days=3)
    with pytest.raises(ValueError, match="irregular dates"):
        inventory_turns(irregular)
    # An explicit value always wins.
    assert inventory_turns(_turns_run("h"), {"periods_per_year": 8760}) > 0


def test_a_full_ledger_is_scored_on_its_scoring_window_like_the_result():
    inventory = InventoryStateDataFrame(["A", "B"], max_lead_time=2).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.data["on_hand"] = [3.0, 9.0]
    policy = FixedOrderPolicy(order_quantity=4.0, lead_time=2, review_period=2, allow_backorders=True)
    demand = pd.DataFrame({
        "unique_id": ["A", "B"] * 4,
        "period": [0, 0, 1, 1, 2, 2, 3, 3],
        "y": [5.0, 2.0, 4.0, 1.0, 6.0, 3.0, 2.0, 2.0],
    })
    result = SimulationEngine().run(
        policy=policy, demand_source=demand, inventory=inventory, freq="D", warmup_periods=2,
    )
    ledger = result.to_event_frame()
    from_result = InventoryEvaluator().fit(result)
    from_ledger = InventoryEvaluator().fit(event_frame=ledger)
    assert from_ledger.evaluation_window_ == from_result.evaluation_window_ == "scoring"
    pd.testing.assert_frame_equal(from_ledger.event_frame_, from_result.event_frame_)
    score = from_ledger.evaluate([fill_rate]).iloc[0, 0]
    assert score == from_result.evaluate([fill_rate]).iloc[0, 0]
    assert score == pytest.approx(result.summary()["fill_rate"])
    everything = InventoryEvaluator().fit(event_frame=ledger, window="all")
    assert len(everything.event_frame_) == len(ledger)
