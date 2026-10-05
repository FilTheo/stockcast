import pandas as pd
import pytest

from stockcast import InventoryStateDataFrame, SimulationEngine
from stockcast.core.base_policy import BasePolicy
from stockcast.core.data_structures import OrderDecision
from stockcast.utils import DemandGenerator


class FixedOrderPolicy(BasePolicy):
    def __init__(self, order_quantity=0.0, **kwargs):
        super().__init__(**kwargs)
        self.order_quantity = float(order_quantity)
        self.predict_calls = 0
        self.fitted_ = True

    def fit(self, forecast_df=None, **kwargs):
        self.fitted_ = True
        return self

    def predict(self, inventory_state_df, current_period=0, **kwargs):
        self.predict_calls += 1
        positions = inventory_state_df.inventory_position()
        result = positions[[inventory_state_df.sku_column, "inventory_position"]].copy()
        result["order_quantity"] = self.order_quantity
        result["target_level"] = self.order_quantity
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


class OriginFixedOrderPolicy(FixedOrderPolicy):
    def __init__(self, forecast_origin, forecast_frequency="D", **kwargs):
        super().__init__(**kwargs)
        self.forecast_origin = pd.Timestamp(forecast_origin)
        self.forecast_frequency = forecast_frequency

    def get_target_metadata(self):
        return {
            "forecast_origin": self.forecast_origin.isoformat(),
            "forecast_frequency": self.forecast_frequency,
        }


def _inventory(skus=("A",), opening_date="2025-01-01"):
    return InventoryStateDataFrame(list(skus), max_lead_time=3).initialize_zero(
        opening_date=pd.Timestamp(opening_date)
    )


def _policy(review_period=1, order_quantity=0.0):
    return FixedOrderPolicy(
        lead_time=1,
        review_period=review_period,
        service_level=0.95,
        allow_backorders=False,
        order_quantity=order_quantity,
    )


def _run_contract(n_periods):
    return {
        "warmup_periods": 0,
        "scoring_periods": n_periods,
        "settlement_periods": 0,
        "order_during_settlement": False,
        "demand_source_name": "unit_test",
        "random_seed": None,
    }


def test_missing_sku_period_is_rejected_before_inventory_mutation():
    inventory = _inventory(("A", "B"))
    inventory.data["on_hand"] = 5.0
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [1.0],
    })

    with pytest.raises(ValueError, match="incomplete SKU grid"):
        SimulationEngine().run(
            _policy(),
            demand,
            inventory,
            n_periods=1,
            freq="D",
            **_run_contract(1),
        )
    assert inventory.data["on_hand"].tolist() == [5.0, 5.0]


def test_missing_calendar_period_is_rejected():
    demand = pd.DataFrame({
        "unique_id": ["A", "A"],
        "period": [0, 2],
        "date": [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-04")],
        "y": [1.0, 1.0],
    })
    with pytest.raises(ValueError, match=r"every period 0..2, .* missing \[1\] \(date 2025-01-03\)"):
        SimulationEngine().run(
            _policy(),
            demand,
            _inventory(),
            n_periods=3,
            freq="D",
            **_run_contract(3),
        )
    # A weekly table without one week names the week's date.
    weekly = pd.DataFrame({
        "unique_id": "A",
        "date": pd.to_datetime(["2025-01-08", "2025-01-15", "2025-01-29"]),
        "y": 1.0,
    })
    with pytest.raises(ValueError, match=r"missing \[2\] \(date 2025-01-22\)"):
        SimulationEngine().run(_policy(), weekly, _inventory(), n_periods=4, freq="W-WED")


def test_missing_dates_are_never_derived_from_an_assumed_frequency():
    demand = pd.DataFrame({"unique_id": ["A"], "period": [0], "y": [1.0]})
    # No frequency given and none recorded by the (custom) policy: rejected.
    with pytest.raises(ValueError, match="freq is required"):
        SimulationEngine().run(_policy(), demand, _inventory())
    # A declared frequency dates period p as opening date + (p + 1) periods.
    result = SimulationEngine().run(_policy(), demand, _inventory(), freq="D")
    assert result.to_event_frame()["date"].tolist() == [pd.Timestamp("2025-01-02")]


def test_weekly_frequency_validates_calendar_and_is_recorded():
    demand = pd.DataFrame({
        "unique_id": ["A", "A"],
        "period": [0, 1],
        "date": [pd.Timestamp("2025-01-13"), pd.Timestamp("2025-01-20")],
        "y": [1.0, 1.0],
    })
    result = SimulationEngine().run(
        _policy(),
        demand,
        _inventory(opening_date="2025-01-06"),
        n_periods=2,
        freq="W-MON",
        **_run_contract(2),
    )

    assert result.run_settings["period_frequency"] == "W-MON"
    assert list(result.to_event_frame()["date"]) == [
        pd.Timestamp("2025-01-13"),
        pd.Timestamp("2025-01-20"),
    ]


@pytest.mark.parametrize("frequency", ["0D", "-1D"])
def test_simulation_frequency_must_advance_time(frequency):
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-01")],
        "y": [0.0],
    })
    with pytest.raises(ValueError, match="advance time strictly forward"):
        SimulationEngine().run(
            _policy(),
            demand,
            _inventory(),
            n_periods=1,
            freq=frequency,
            **_run_contract(1),
        )


def test_wrong_date_for_declared_frequency_is_rejected():
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-08")],
        "y": [1.0],
    })
    with pytest.raises(ValueError, match="not on the period grid starting 2025-01-13"):
        SimulationEngine().run(
            _policy(),
            demand,
            _inventory(opening_date="2025-01-06"),
            n_periods=1,
            freq="W-MON",
            **_run_contract(1),
        )


def test_first_decision_uses_schedule_and_old_opening_option_fails_explicitly():
    demand = pd.DataFrame({"unique_id": ["A"], "period": [0],
                           "date": [pd.Timestamp("2025-01-02")], "y": [4.]})
    policy = FixedOrderPolicy(lead_time=0, review_period=3, service_level=None,
                              allow_backorders=False, order_quantity=10)
    result = SimulationEngine().run(policy, demand, _inventory(), 1,
                                    freq="D", **_run_contract(1))
    event = result.to_event_frame().iloc[0]
    assert event["event_type"] == "period"
    assert event["received_units"] == 10
    assert event["fulfilled_units"] == 4
    assert event["ending_on_hand"] == 6
    with pytest.raises(TypeError, match="initial_decision"):
        SimulationEngine().run(policy, demand, _inventory(), 1, freq="D",
                               initial_decision="before_first_demand", **_run_contract(1))


def test_comparison_materializes_callable_once_and_copies_policies():
    calls = []

    def demand_source(period):
        calls.append(period)
        return pd.DataFrame({
            "unique_id": ["A"],
            "period": [period],
            "date": [pd.date_range("2025-01-02", periods=period + 1, freq="D")[-1]],
            "y": [0.0],
        })

    first = _policy(review_period=3, order_quantity=1)
    second = _policy(review_period=3, order_quantity=2)
    comparison = SimulationEngine().run_comparison(
        [first, second],
        demand_source,
        _inventory(),
        n_periods=2,
        freq="D",
        labels=["first", "second"],
        **_run_contract(2),
    )

    assert calls == [0, 1]
    assert first.predict_calls == 0
    assert second.predict_calls == 0
    assert comparison["first"].inventory.data.loc[0, "on_hand"] == 1.0
    assert comparison["second"].inventory.data.loc[0, "on_hand"] == 2.0
    for result in comparison.results.values():
        assert result.run_manifest["demand_source"]["type"] == "callable"
        assert result.run_manifest["demand_source"]["materialized_once_for_comparison"]


def test_comparison_rejects_duplicate_user_labels_before_demand_is_materialized():
    calls = []

    def demand_source(period):
        calls.append(period)
        return pd.DataFrame()

    with pytest.raises(ValueError, match="labels must be unique"):
        SimulationEngine().run_comparison(
            [_policy(), _policy()],
            demand_source,
            _inventory(),
            n_periods=1,
            freq="D",
            labels=["same", "same"],
            **_run_contract(1),
        )

    assert calls == []


def test_run_does_not_mutate_input_or_include_pre_run_history():
    inventory = _inventory()
    pre_run_snapshot = inventory.get_dataframe()
    inventory._history.append(pre_run_snapshot.assign(period=-1))
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [1.0],
    })

    result = SimulationEngine().run(
        _policy(),
        demand,
        inventory,
        n_periods=1,
        freq="D",
        **_run_contract(1),
    )

    pd.testing.assert_frame_equal(inventory.get_dataframe(), pre_run_snapshot)
    assert inventory.allow_backorders is None
    assert len(inventory._history) == 1
    assert len(result.history) == 1
    assert result.history["period"].tolist() == [1]


def test_demand_identifier_types_must_match_inventory_identifiers():
    demand = pd.DataFrame({
        "unique_id": [1],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [0.0],
    })

    with pytest.raises(ValueError, match="incomplete SKU grid"):
        SimulationEngine().run(
            _policy(),
            demand,
            _inventory(),
            n_periods=1,
            freq="D",
            **_run_contract(1),
        )


def test_numeric_identifiers_are_preserved_through_orders_and_events():
    inventory = InventoryStateDataFrame([1], max_lead_time=3).initialize_zero(
        opening_date=pd.Timestamp("2025-01-01")
    )
    demand = pd.DataFrame({
        "unique_id": [1],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [0.0],
    })

    result = SimulationEngine().run(
        _policy(order_quantity=2.0),
        demand,
        inventory,
        n_periods=1,
        freq="D",
        **_run_contract(1),
    )

    event = result.to_event_frame().iloc[0]
    assert event["unique_id"] == 1
    assert event["sku_order_line_count"] == 1
    assert event["order_quantity"] == 2.0


def test_policy_schedule_updates_targets_only_at_declared_decisions():
    demand = pd.DataFrame({
        "unique_id": ["A", "A"],
        "period": [0, 1],
        "date": [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")],
        "y": [0.0, 0.0],
    })
    result = SimulationEngine().run(
        _policy(order_quantity=1.0),
        demand,
        _inventory(),
        n_periods=2,
        freq="D",
        policy_schedule={1: _policy(order_quantity=10.0)},
        **_run_contract(2),
    )

    assert result.inventory.data.loc[0, "on_hand"] == 1.0
    assert result.inventory.data.loc[0, "in_transit"].sum() == 10.0
    assert result.run_settings["policy_update_periods"] == [1]
    assert result.run_settings["policy_update_log"] == [{
        "decision_period": 1,
        "policy_name": "FixedOrderPolicy",
        "target_metadata": {},
        "target_data": None,
        "fitted_levels": None,
    }]


def test_policy_schedule_rejects_non_decision_periods():
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [0.0],
    })
    with pytest.raises(ValueError, match="not a decision event"):
        SimulationEngine().run(
            _policy(review_period=2),
            demand,
            _inventory(),
            n_periods=1,
            freq="D",
            policy_schedule={1: _policy(review_period=2)},
            **_run_contract(1),
        )


def test_policy_schedule_rejects_configuration_changes():
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [0.0],
    })
    with pytest.raises(ValueError, match="may change fitted targets only"):
        SimulationEngine().run(
            _policy(),
            demand,
            _inventory(),
            n_periods=1,
            freq="D",
            policy_schedule={0: FixedOrderPolicy(
                order_quantity=2.0,
                lead_time=2,
                review_period=1,
                service_level=0.95,
                allow_backorders=False,
            )},
            **_run_contract(1),
        )


def test_run_windows_control_scoring_and_settlement_ordering():
    demand = pd.DataFrame({
        "unique_id": ["A"] * 4,
        "period": [0, 1, 2, 3],
        "date": pd.date_range("2025-01-02", periods=4, freq="D"),
        "y": [0.0] * 4,
    })
    result = SimulationEngine().run(
        _policy(order_quantity=1.0),
        demand,
        _inventory(),
        n_periods=4,
        freq="D",
        warmup_periods=1,
        scoring_periods=2,
        settlement_periods=1,
        order_during_settlement=False,
        demand_source_name="window_contract_test",
        random_seed=17,
    )

    events = result.to_event_frame()
    assert events["run_window"].tolist() == [
        "warmup",
        "scoring",
        "scoring",
        "settlement",
    ]
    assert events["order_quantity"].tolist() == [1.0, 1.0, 1.0, 0.0]
    assert result.summary()["mean_ending_on_hand_per_sku_period"] == 1.5
    assert result.run_manifest["demand_source"]["random_seed"] == 17
    assert result.run_manifest["demand_source"]["rows"] == 4
    assert len(result.run_manifest["demand_source"]["sha256"]) == 64
    assert result.run_manifest["opening_inventory"]["rows"] == 1
    assert len(result.run_manifest["opening_inventory"]["sha256"]) == 64
    assert result.run_manifest["opening_inventory"]["max_lead_time"] == 3
    assert result.run_manifest["opening_inventory"]["allow_backorders"] is False
    source = result.run_manifest["package"]["source"]
    assert source["commit"] is None or len(source["commit"]) == 40
    assert source["dirty"] is None


def test_clipped_demand_diagnostics_are_recorded_in_run_manifest():
    generator = DemandGenerator(
        ["A"],
        first_date=pd.Timestamp("2025-01-02"),
        freq="D",
        random_seed=1,
        negative_demand_handling="clip_zero",
    )
    with pytest.warns(RuntimeWarning, match="clipped to zero"):
        demand = generator.trend(2, initial=0.0, growth_rate=-1.0, std=0.0)
    result = SimulationEngine().run(
        _policy(), demand, _inventory(), n_periods=2,
        freq="D",
        **_run_contract(2),
    )
    provenance = result.run_manifest["demand_source"]["generation_provenance"]
    assert provenance == {
        "negative_demand_handling": "clip_zero",
        "clipped_negative_count": 1,
        "minimum_clipped_value": -1.0,
    }


def test_run_window_lengths_must_match_total_periods():
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [0.0],
    })
    with pytest.raises(ValueError, match="must equal n_periods"):
        SimulationEngine().run(
            _policy(),
            demand,
            _inventory(),
            n_periods=1,
            freq="D",
            warmup_periods=0,
            scoring_periods=1,
            settlement_periods=1,
            order_during_settlement=False,
            demand_source_name="unit_test",
            random_seed=None,
        )


def test_initial_policy_cannot_use_a_future_information_origin():
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [0.0],
    })
    policy = OriginFixedOrderPolicy(
        forecast_origin="2025-01-02",
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )
    with pytest.raises(ValueError, match="must not be after"):
        SimulationEngine().run(
            policy,
            demand,
            _inventory(),
            n_periods=1,
            freq="D",
            **_run_contract(1),
        )


def test_scheduled_policy_origin_must_equal_its_decision_date():
    demand = pd.DataFrame({
        "unique_id": ["A", "A"],
        "period": [0, 1],
        "date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
        "y": [0.0, 0.0],
    })
    base = OriginFixedOrderPolicy(
        forecast_origin="2025-01-01",
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )
    wrong_snapshot = OriginFixedOrderPolicy(
        forecast_origin="2025-01-04",
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )
    with pytest.raises(ValueError, match="must equal decision information date"):
        SimulationEngine().run(
            base,
            demand,
            _inventory(),
            n_periods=2,
            freq="D",
            policy_schedule={1: wrong_snapshot},
            **_run_contract(2),
        )


def test_policy_forecast_frequency_must_match_simulation_periods():
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [0.0],
    })
    policy = OriginFixedOrderPolicy(
        forecast_origin="2025-01-01",
        forecast_frequency="W-MON",
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )

    with pytest.raises(ValueError, match="must match the run's freq"):
        SimulationEngine().run(
            policy,
            demand,
            _inventory(),
            n_periods=1,
            freq="D",
            **_run_contract(1),
        )


# Run defaults: values the inputs already fix are inferred.

def _daily_demand(values, skus=("A",), start="2025-01-02"):
    dates = pd.date_range(start, periods=len(values), freq="D")
    return pd.DataFrame([
        {"unique_id": sku, "period": period, "date": dates[period], "y": float(value)}
        for sku in skus
        for period, value in enumerate(values)
    ])


def _order_up_to(frequency="D"):
    from stockcast.policies import OrderUpToPolicy

    return OrderUpToPolicy(1, 1, freq=frequency, service_level=0.9, allow_backorders=False).fit(
        pd.DataFrame({"unique_id": ["A"], "S": [8.0]}),
        target_column="S",
        forecast_origin=pd.Timestamp("2025-01-01"),
    )


def _stable(result):
    manifest = {
        key: value for key, value in result.run_manifest.items()
        if key not in {"run_id", "created_at_utc"}
    }
    return result.to_event_frame(), result.run_settings, manifest


def test_minimal_run_matches_the_fully_explicit_run():
    demand = _daily_demand([3, 5, 2, 4, 6, 1])
    minimal = SimulationEngine().run(
        policy=_order_up_to(), demand_source=demand, inventory=_inventory(),
    )
    explicit = SimulationEngine().run(
        policy=_order_up_to(),
        demand_source=demand,
        inventory=_inventory(),
        n_periods=6,
        freq="D",
        warmup_periods=0,
        scoring_periods=6,
        settlement_periods=0,
        order_during_settlement=False,
        random_seed=None,
    )
    events, settings, manifest = _stable(minimal)
    pd.testing.assert_frame_equal(events, explicit.to_event_frame())
    assert settings == explicit.run_settings
    assert manifest == _stable(explicit)[2]
    assert manifest["demand_source"]["name"] is None
    assert manifest["demand_source"]["random_seed"] is None


def test_scoring_defaults_to_the_periods_left_after_warmup_and_settlement():
    result = SimulationEngine().run(
        policy=_order_up_to(),
        demand_source=_daily_demand([3, 5, 2, 4, 6, 1]),
        inventory=_inventory(),
        warmup_periods=2,
        settlement_periods=1,
        order_during_settlement=False,
    )
    assert (
        result.run_settings["warmup_periods"],
        result.run_settings["scoring_periods"],
        result.run_settings["settlement_periods"],
    ) == (2, 3, 1)
    assert result.to_event_frame()["run_window"].tolist() == (
        ["warmup"] * 2 + ["scoring"] * 3 + ["settlement"]
    )
    with pytest.raises(ValueError, match="leave no scoring periods"):
        SimulationEngine().run(
            policy=_order_up_to(), demand_source=_daily_demand([3, 5]),
            inventory=_inventory(), warmup_periods=2,
        )


def test_settlement_requires_an_explicit_ordering_choice():
    with pytest.raises(ValueError, match="order_during_settlement is required"):
        SimulationEngine().run(
            policy=_order_up_to(),
            demand_source=_daily_demand([3, 5, 2]),
            inventory=_inventory(),
            settlement_periods=1,
        )


def test_n_periods_is_required_for_callable_demand():
    demand = _daily_demand([3, 5, 2])
    with pytest.raises(ValueError, match="n_periods is required when demand_source is a callable"):
        SimulationEngine().run(
            policy=_order_up_to(),
            demand_source=lambda period: demand[demand["period"] == period],
            inventory=_inventory(),
        )
    result = SimulationEngine().run(
        policy=_order_up_to(),
        demand_source=lambda period: demand[demand["period"] == period],
        inventory=_inventory(),
        n_periods=3,
    )
    assert result.run_settings["scoring_periods"] == 3


def test_run_freq_comes_from_the_policy_or_must_be_given():
    demand = _daily_demand([3, 5, 2])
    with pytest.raises(ValueError, match="freq is required"):
        SimulationEngine().run(policy=_policy(), demand_source=demand, inventory=_inventory())
    result = SimulationEngine().run(
        policy=_policy(), demand_source=demand, inventory=_inventory(), freq="D",
    )
    assert result.run_settings["scoring_periods"] == 3
    with pytest.raises(ValueError, match="must match the run's freq"):
        SimulationEngine().run(
            policy=_order_up_to(), demand_source=demand, inventory=_inventory(),
            freq="2D",
        )


def test_comparison_infers_periods_and_a_shared_frequency():
    demand = _daily_demand([3, 5, 2, 4])
    comparison = SimulationEngine().run_comparison(
        [_order_up_to(), _order_up_to()], demand, _inventory(), labels=["a", "b"],
    )
    assert comparison["a"].run_settings["scoring_periods"] == 4
    # A custom policy without target metadata does not constrain the frequency.
    mixed = SimulationEngine().run_comparison(
        [_order_up_to(), _policy()], demand, _inventory(), labels=["a", "custom"],
    )
    assert mixed["custom"].run_settings["scoring_periods"] == 4
    with pytest.raises(ValueError, match="different forecast frequencies"):
        SimulationEngine().run_comparison(
            [_order_up_to("D"), _order_up_to("2D")], demand, _inventory(),
            labels=["a", "b"],
        )


def test_optional_labels_are_recorded_when_given():
    result = SimulationEngine().run(
        policy=_order_up_to(), demand_source=_daily_demand([3, 5]), inventory=_inventory(),
        demand_source_name=" shop_7 ", random_seed=11,
    )
    assert result.run_manifest["demand_source"]["name"] == "shop_7"
    assert result.run_manifest["demand_source"]["random_seed"] == 11
    with pytest.raises(ValueError, match="demand_source_name must be a non-empty string or None"):
        SimulationEngine().run(
            policy=_order_up_to(), demand_source=_daily_demand([3, 5]),
            inventory=_inventory(), demand_source_name=" ",
        )


def test_unsized_pipeline_matches_an_explicitly_sized_one():
    from stockcast.core import Supplier, SupplyModel

    demand = _daily_demand([3, 5, 2, 4, 6, 1])
    counted = pd.DataFrame({"unique_id": ["A"], "on_hand": [6.0]})
    opening = pd.Timestamp("2025-01-01")
    for supply, size in ((None, 1), (SupplyModel([Supplier("far", lead_time=3)]), 3)):
        unsized = InventoryStateDataFrame.from_observed(counted, opening_date=opening)
        sized = InventoryStateDataFrame.from_observed(
            counted, opening_date=opening, max_lead_time=size,
        )
        runs = [
            SimulationEngine().run(
                policy=_order_up_to(), demand_source=demand, inventory=state, supply=supply,
            )
            for state in (unsized, sized)
        ]
        pd.testing.assert_frame_equal(runs[0].to_event_frame(), runs[1].to_event_frame())
        assert runs[0].run_settings == runs[1].run_settings
        assert _stable(runs[0])[2] == _stable(runs[1])[2]
        assert unsized.max_lead_time == 0  # the caller's state is unchanged


def test_demand_may_give_only_dates_or_only_periods():
    full = _daily_demand([3, 5, 2, 4], skus=("A",))
    reference = SimulationEngine().run(policy=_order_up_to(), demand_source=full, inventory=_inventory())
    for frame in (full.drop(columns="period"), full.drop(columns="date")):
        result = SimulationEngine().run(policy=_order_up_to(), demand_source=frame, inventory=_inventory())
        pd.testing.assert_frame_equal(result.to_event_frame(), reference.to_event_frame())
        assert (
            result.run_manifest["demand_source"]["sha256"]
            == reference.run_manifest["demand_source"]["sha256"]
        )
    by_period = SimulationEngine().run(
        policy=_order_up_to(),
        demand_source=lambda period: full.loc[full["period"] == period, ["unique_id", "y"]],
        inventory=_inventory(), n_periods=4,
    )
    pd.testing.assert_frame_equal(by_period.to_event_frame(), reference.to_event_frame())
    off_grid = full.drop(columns="period").assign(
        date=lambda frame: frame["date"] + pd.Timedelta(hours=12)
    )
    with pytest.raises(ValueError, match="not on the period grid"):
        SimulationEngine().run(policy=_order_up_to(), demand_source=off_grid, inventory=_inventory())
    gap = full.drop(columns="period").iloc[[0, 2, 3]]
    with pytest.raises(ValueError, match=r"every period 0..3, .* missing \[1\]"):
        SimulationEngine().run(policy=_order_up_to(), demand_source=gap, inventory=_inventory())



@pytest.mark.parametrize("arrays", [True, False])
@pytest.mark.parametrize("backorders", [False, True])
def test_float_residue_shortage_is_not_flagged(monkeypatch, arrays, backorders):
    """0.1 + 0.2 demand against 0.3 stock leaves a ~5.6e-17 shortage.

    The engine flags must use the validator's tolerance, so the ledger of a
    valid run is always accepted by ``validate_event_frame``.
    """
    from stockcast.evaluation import InventoryEvaluator, validate_event_frame

    if not arrays:
        monkeypatch.setattr(SimulationEngine, "_array_hooks_supported", lambda self: False)
    inventory = _inventory()
    inventory.data["on_hand"] = 0.3
    demand = pd.DataFrame({
        "unique_id": ["A"], "period": [0], "date": [pd.Timestamp("2025-01-02")],
        "y": [0.1 + 0.2],
    })
    policy = FixedOrderPolicy(
        lead_time=1, review_period=1, allow_backorders=backorders, order_quantity=0.0,
    )
    result = SimulationEngine().run(policy, demand, inventory, freq="D")
    events = result.to_event_frame()
    assert 0 < events["shortage_units"].iloc[0] < 1e-12
    assert not events["stockout_flag"].iloc[0]
    assert not events["backorder_flag"].iloc[0]
    validate_event_frame(events)
    InventoryEvaluator().fit(result)


def test_comparison_accepts_a_label_to_policy_dict():
    demand = _daily_demand([3, 5, 2, 4])
    as_list = SimulationEngine().run_comparison(
        [_order_up_to(), _order_up_to()], demand, _inventory(), labels=["a", "b"],
    )
    as_dict = SimulationEngine().run_comparison(
        {"a": _order_up_to(), "b": _order_up_to()}, demand, _inventory(),
    )
    for label in ("a", "b"):
        pd.testing.assert_frame_equal(
            as_dict[label].to_event_frame(), as_list[label].to_event_frame(),
        )
    with pytest.raises(ValueError, match="do not also pass labels"):
        SimulationEngine().run_comparison(
            {"a": _order_up_to()}, demand, _inventory(), labels=["a"],
        )
    with pytest.raises(ValueError, match="labels with no policy"):
        SimulationEngine().run_comparison(
            {"a": _order_up_to()}, demand, _inventory(), policy_schedules={"z": {}},
        )
    with pytest.raises(TypeError, match="only when policies is a"):
        SimulationEngine().run_comparison(
            [_order_up_to()], demand, _inventory(), policy_schedules={"a": {}},
        )
    with pytest.raises(ValueError, match="non-empty list"):
        SimulationEngine().run_comparison({}, demand, _inventory())


def test_comparison_dict_maps_schedules_by_label():
    first, second, schedule = object(), object(), {4: object()}
    policies, labels, schedules = SimulationEngine._comparison_branches(
        {"a": first, "b": second}, None, {"b": schedule},
    )
    assert (policies, labels, schedules) == ([first, second], ["a", "b"], [None, schedule])
    listed = ([first], ["x"], [None])
    assert SimulationEngine._comparison_branches(*listed) == listed


def test_date_only_demand_without_a_frequency_names_the_missing_frequency():
    demand = _daily_demand([3, 5, 2]).drop(columns="period")
    with pytest.raises(ValueError, match="freq is required.*cannot be numbered"):
        SimulationEngine().run(policy=_policy(), demand_source=demand, inventory=_inventory())
    result = SimulationEngine().run(
        policy=_policy(), demand_source=demand, inventory=_inventory(), freq="D",
    )
    assert len(result.to_event_frame()) == 3


def _fixed_reorder_point(freq=None, **dates):
    from stockcast.policies import ReorderPointPolicy

    return ReorderPointPolicy(lead_time=1, review_period=1, freq=freq, allow_backorders=False).fit(
        reorder_point=2.0, order_up_to_level=6.0, **dates,
    )


def test_undated_fixed_levels_run_on_the_demand_calendar():
    demand = _daily_demand([3, 5, 2, 4], skus=("A", "B"))
    with pytest.raises(ValueError, match="freq is required"):
        SimulationEngine().run(
            policy=_fixed_reorder_point(), demand_source=demand,
            inventory=_inventory(skus=("A", "B")),
        )
    result = SimulationEngine().run(
        policy=_fixed_reorder_point(), demand_source=demand,
        inventory=_inventory(skus=("A", "B")), freq="D",
    )
    dated = SimulationEngine().run(
        policy=_fixed_reorder_point(forecast_origin=pd.Timestamp("2025-01-01"), freq="D"),
        demand_source=demand, inventory=_inventory(skus=("A", "B")),
    )
    events = result.to_event_frame()
    pd.testing.assert_frame_equal(
        events.drop(columns="policy"), dated.to_event_frame().drop(columns="policy"),
    )
    # s=2, S=6: order at positions 0 and 1, not at 6 or 5.
    assert events.loc[events["unique_id"] == "A", "order_quantity"].tolist() == [6.0, 0.0, 5.0, 0.0]


def test_dated_fixed_levels_are_checked_against_the_opening_date():
    policy = _fixed_reorder_point(forecast_origin=pd.Timestamp("2025-02-01"), freq="D")
    with pytest.raises(ValueError, match="forecast_origin"):
        SimulationEngine().run(
            policy=policy, demand_source=_daily_demand([3, 5]), inventory=_inventory(),
        )


def test_policy_metadata_with_an_origin_but_no_frequency_is_rejected():
    policy = _fixed_reorder_point()
    policy.target_metadata_["forecast_origin"] = "2025-01-01T00:00:00"
    with pytest.raises(ValueError, match="forecast_origin without a forecast_frequency"):
        SimulationEngine().run(
            policy=policy, demand_source=_daily_demand([3, 5]), inventory=_inventory(),
            freq="D",
        )


def test_fixed_levels_with_freq_and_no_origin_set_the_run_calendar():
    demand = _daily_demand([3, 5, 2, 4], skus=("A", "B"))
    policy = _fixed_reorder_point(freq="D")
    metadata = policy.get_target_metadata()
    assert (metadata["forecast_origin"], metadata["forecast_frequency"]) == (None, "D")
    result = SimulationEngine().run(policy, demand, _inventory(skus=("A", "B")))
    explicit = SimulationEngine().run(
        _fixed_reorder_point(), demand, _inventory(skus=("A", "B")), freq="D",
    )
    assert result.run_settings["period_frequency"] == "D"
    pd.testing.assert_frame_equal(result.to_event_frame(), explicit.to_event_frame())
    with pytest.raises(ValueError, match="policy freq W-MON must match the run's freq D"):
        SimulationEngine().run(
            _fixed_reorder_point(freq="W-MON"), demand, _inventory(skus=("A", "B")), freq="D",
        )


# A date+period table may keep its own period numbering (93.20).

def _levels(backorders):
    from stockcast.policies import ReorderPointPolicy

    return ReorderPointPolicy(lead_time=1, review_period=1, allow_backorders=backorders).fit(
        reorder_point=4.0, order_up_to_level=9.0,
    )


def _shifted(frame, shift):
    return frame.assign(period=frame["period"] + shift)


def _same_run(left, right):
    pd.testing.assert_frame_equal(left.to_event_frame(), right.to_event_frame())
    pd.testing.assert_frame_equal(left.to_order_frame(), right.to_order_frame())
    pd.testing.assert_frame_equal(left.to_process_flow_frame(), right.to_process_flow_frame())
    pd.testing.assert_frame_equal(left.to_callback_audit_frame(), right.to_callback_audit_frame())
    left_manifest, right_manifest = _stable(left)[2], _stable(right)[2]
    for manifest in (left_manifest, right_manifest):
        manifest["run_settings"] = {
            key: value for key, value in manifest["run_settings"].items()
            if key != "demand_period_offset"
        }
    assert left_manifest == right_manifest
    assert left_manifest["demand_source"]["sha256"] == right_manifest["demand_source"]["sha256"]


@pytest.mark.parametrize("backorders", [False, True])
def test_shifted_date_period_table_runs_as_the_renumbered_table(backorders):
    demand = _daily_demand([3, 5, 2, 4, 6, 1, 7, 2], skus=("A", "B"))
    runs = {
        shift: SimulationEngine().run(
            _levels(backorders), _shifted(demand, shift), _inventory(skus=("A", "B")),
            freq="D", warmup_periods=2,
        )
        for shift in (0, 12, -3)
    }
    for shift in (12, -3):
        _same_run(runs[shift], runs[0])
        assert runs[shift].run_settings["demand_period_offset"] == shift
        assert runs[shift].run_manifest["run_settings"]["demand_period_offset"] == shift
        settings = dict(runs[shift].run_settings)
        settings.pop("demand_period_offset")
        reference = dict(runs[0].run_settings)
        assert reference.pop("demand_period_offset") == 0
        assert settings == reference
    assert runs[0].to_event_frame()["period"].min() == 1  # event periods are unchanged


def test_shifted_table_in_run_comparison_records_the_shift_in_every_branch():
    demand = _daily_demand([3, 5, 2, 4, 6, 1], skus=("A", "B"))
    policies = {"lost sales": _levels(False), "backorders": _levels(True)}
    shifted, reference = (
        SimulationEngine().run_comparison(
            policies, frame, _inventory(skus=("A", "B")), freq="D",
            warmup_periods=1,
        )
        for frame in (_shifted(demand, 12), demand)
    )
    for label in policies:
        _same_run(shifted[label], reference[label])
        assert shifted[label].run_settings["demand_period_offset"] == 12
        assert reference[label].run_settings["demand_period_offset"] == 0


def test_generator_history_slice_runs_with_its_own_period_numbers():
    generator = DemandGenerator(
        ["coffee", "tea"], first_date="2026-01-05", freq="W-MON", random_seed=0,
    )
    sales = generator.sample(20, lambda rng, periods: rng.poisson(20, periods.size))
    future = sales[sales["date"] > "2026-03-23"]
    assert (future["period"].min(), future["period"].max()) == (12, 19)
    stock = InventoryStateDataFrame(["coffee", "tea"]).initialize_zero(
        opening_date=pd.Timestamp("2026-03-23")
    )
    result = SimulationEngine().run(_levels(False), future, stock, freq="W-MON")
    renumbered = SimulationEngine().run(
        _levels(False), future.drop(columns="period"), stock, freq="W-MON",
    )
    assert result.run_settings["demand_period_offset"] == 12
    assert renumbered.run_settings["demand_period_offset"] == 0
    pd.testing.assert_frame_equal(result.to_event_frame(), renumbered.to_event_frame())
    assert result.n_periods == 8


def _break(frame, how):
    frame = _shifted(frame, 12)
    if how == "gap":
        frame.loc[(frame["unique_id"] == "A") & (frame["period"] >= 14), "period"] += 1
    elif how == "per-SKU shift":
        frame.loc[frame["unique_id"] == "B", "period"] -= 12
    elif how == "swapped":
        rows = frame.index[(frame["unique_id"] == "A") & frame["period"].isin([13, 14])]
        frame.loc[rows, "period"] = [14, 13]
    elif how == "non-integer":
        frame["period"] = frame["period"].astype(float)
        frame.loc[1, "period"] = 13.5
    return frame


@pytest.mark.parametrize("how, message", [
    ("gap", r"'A' dated 2025-01-04 has period 15, but its date is run period 2, "
            r"which this table numbers 14 \(its first date, 2025-01-02, is period 12\)"),
    ("per-SKU shift", r"'B' dated 2025-01-02 has period 0, but its date is run period 0, "
                      r"which this table numbers 12"),
    ("swapped", r"'A' dated 2025-01-03 has period 14, but its date is run period 1, "
                r"which this table numbers 13"),
    ("non-integer", r"must contain integers: the row for unique_id 'A' dated 2025-01-03 "
                    r"has period 13.5 \(its date is run period 1\)"),
])
def test_period_numbering_that_does_not_follow_the_dates_is_rejected(how, message):
    demand = _break(_daily_demand([3, 5, 2, 4], skus=("A", "B")), how)
    with pytest.raises(ValueError, match=message):
        SimulationEngine().run(
            _levels(False), demand, _inventory(skus=("A", "B")), freq="D",
        )


def test_period_only_table_must_count_from_the_opening_date():
    demand = _shifted(_daily_demand([3, 5, 2, 4]), 12).drop(columns="date")
    message = (
        r"demand periods count from the inventory's opening date: the first demand "
        r"period is 0 \(2025-01-02\). Got periods 12..15. Add a date column"
    )
    for n_periods in (None, 4):
        with pytest.raises(ValueError, match=message):
            SimulationEngine().run(
                _levels(False), demand, _inventory(), n_periods=n_periods,
                freq="D",
            )


def test_dates_on_or_before_the_opening_date_are_rejected():
    demand = _daily_demand([3, 5, 2, 4], start="2024-12-31")
    with pytest.raises(ValueError, match="on or before the inventory's opening date 2025-01-01"):
        SimulationEngine().run(_levels(False), demand, _inventory(), freq="D")


def test_schedule_with_no_decision_in_the_run_is_rejected():
    from stockcast.core.decision_schedule import ExplicitSchedule, OneTimeSchedule
    from stockcast.policies import ReorderPointPolicy

    for schedule in (ExplicitSchedule((12,)), OneTimeSchedule(8), ExplicitSchedule(())):
        policy = ReorderPointPolicy(0, schedule=schedule, allow_backorders=False).fit(
            reorder_point=4.0, order_up_to_level=9.0,
        )
        with pytest.raises(ValueError, match=r"no decision in this run's periods 0..7"):
            SimulationEngine().run(
                policy, _daily_demand([3] * 8), _inventory(), freq="D",
            )


def test_decisions_only_in_settlement_without_ordering_are_rejected():
    from stockcast.core.decision_schedule import ExplicitSchedule
    from stockcast.policies import ReorderPointPolicy

    def run(periods, **windows):
        policy = ReorderPointPolicy(0, schedule=ExplicitSchedule(periods), allow_backorders=False)
        return SimulationEngine().run(
            policy.fit(reorder_point=4.0, order_up_to_level=9.0), _daily_demand([3] * 8),
            _inventory(), freq="D", **windows,
        )

    with pytest.raises(ValueError, match=r"falls in the settlement window \(periods 6..7\)"):
        run((6, 7), settlement_periods=2, order_during_settlement=False)
    ordering = run((6, 7), settlement_periods=2, order_during_settlement=True)
    assert ordering.to_event_frame()["decision_flag"].sum() == 2
    warm_up_only = run((0,), warmup_periods=2, settlement_periods=2, order_during_settlement=False)
    assert warm_up_only.to_event_frame()["decision_flag"].sum() == 1


# freq: one name for the period length (93.21).

def test_old_frequency_argument_names_are_rejected():
    with pytest.raises(TypeError, match="period_frequency"):
        SimulationEngine().run(
            _levels(False), _daily_demand([3, 5]), _inventory(), period_frequency="D",
        )
    with pytest.raises(TypeError, match="period_frequency"):
        SimulationEngine().run_comparison(
            [_levels(False)], _daily_demand([3, 5]), _inventory(), period_frequency="D",
        )
    with pytest.raises(TypeError, match="period_frequency"):
        DemandGenerator(["A"], first_date="2025-01-02", period_frequency="D", random_seed=0)
    with pytest.raises(TypeError, match="period_frequency"):
        _inventory().advance_period(period_frequency="D", is_review_period=True)


def test_rolling_snapshots_must_keep_the_policy_frequency():
    from stockcast.policies import ReorderPointPolicy

    def levels(freq, **dates):
        return ReorderPointPolicy(1, 1, freq=freq, allow_backorders=False).fit(
            reorder_point=4.0, order_up_to_level=9.0, **dates,
        )

    demand = _daily_demand([3, 5, 2, 4])
    with pytest.raises(ValueError, match="period 2 has freq W-MON, but the policy has freq D"):
        SimulationEngine().run(levels("D"), demand, _inventory(), policy_schedule={2: levels("W-MON")})
    # An undated snapshot keeps the run's frequency.
    updated = SimulationEngine().run(
        levels("D"), demand, _inventory(), policy_schedule={2: levels(None)},
    )
    assert updated.run_settings["period_frequency"] == "D"


def test_a_dated_policy_needs_dated_snapshots():
    from stockcast.policies import ReorderPointPolicy

    def levels(**dates):
        return ReorderPointPolicy(1, 1, freq="D", allow_backorders=False).fit(
            reorder_point=4.0, order_up_to_level=9.0, **dates,
        )

    demand = _daily_demand([3, 5, 2, 4])
    dated = levels(forecast_origin=pd.Timestamp("2025-01-01"))
    with pytest.raises(ValueError, match=(
        r"policy_schedule period 2 has no forecast_origin, but the policy is dated "
        r"\(forecast_origin 2025-01-01 00:00:00\); fit the snapshot with "
        r"forecast_origin 2025-01-03 00:00:00"
    )):
        SimulationEngine().run(dated, demand, _inventory(), policy_schedule={2: levels()})
    # A dated snapshot at its decision's information date is accepted.
    refit = levels(forecast_origin=pd.Timestamp("2025-01-03"))
    result = SimulationEngine().run(dated, demand, _inventory(), policy_schedule={2: refit})
    assert result.run_settings["policy_update_periods"] == [2]


def test_a_missing_sku_fails_before_the_run_and_extra_skus_are_allowed():
    from stockcast.policies import OrderUpToPolicy, ReorderPointPolicy

    demand = _daily_demand([3, 5, 2], skus=("A", "B"))
    inventory = _inventory(("A", "B"))
    origin = pd.Timestamp("2025-01-01")

    def order_up_to(skus):
        return OrderUpToPolicy(1, 1, freq="D", allow_backorders=False).fit(
            pd.DataFrame({"unique_id": list(skus), "S": 9.0}),
            target_column="S", forecast_origin=origin,
        )

    def levels(skus, **dates):
        values = {sku: 4.0 for sku in skus}
        return ReorderPointPolicy(1, 1, freq="D", allow_backorders=False).fit(
            reorder_point=values, order_up_to_level={sku: 9.0 for sku in skus}, **dates,
        )

    with pytest.raises(ValueError, match=r"the policy has no fitted level for 1 inventory SKU\(s\): \['B'\]"):
        SimulationEngine().run(order_up_to("A"), demand, inventory)
    with pytest.raises(ValueError, match=r"the policy has no fitted level .*\['B'\]"):
        SimulationEngine().run(levels("A"), demand, inventory)
    with pytest.raises(ValueError, match=r"policy_schedule period 1 has no fitted level .*\['B'\]"):
        SimulationEngine().run(
            levels("AB", forecast_origin=origin), demand, inventory,
            policy_schedule={1: levels("A", forecast_origin=origin + pd.Timedelta(days=1))},
        )
    # Extra SKUs are not simulated and change nothing.
    plain = SimulationEngine().run(order_up_to("AB"), demand, inventory)
    extra = SimulationEngine().run(order_up_to("ABZ"), demand, inventory)
    pd.testing.assert_frame_equal(
        plain.to_event_frame().drop(columns="policy"), extra.to_event_frame().drop(columns="policy"),
    )
    # One pair for every SKU covers any inventory.
    SimulationEngine().run(_levels(False), demand, inventory, freq="D")


def test_look_ahead_error_names_the_column_the_origin_came_from():
    from stockcast.policies import OrderUpToPolicy

    policy = OrderUpToPolicy(1, 1, freq="D", service_level=0.9, allow_backorders=False).fit(
        pd.DataFrame({"unique_id": ["A"], "S": [8.0], "date": [pd.Timestamp("2025-01-10")]}),
        target_column="S",
    )
    message = (
        r"policy forecast_origin 2025-01-08 00:00:00 \(derived from the 'date' column\) "
        r"must not be after decision information date 2025-01-01"
    )
    with pytest.raises(ValueError, match=message):
        SimulationEngine().run(policy, _daily_demand([3, 5]), _inventory())


def test_ledger_periods_are_integers_whatever_the_opening_state_stored():
    # initialize_zero stores the opening period as 0.0; the ledger counts whole periods.
    result = SimulationEngine().run(_levels(False), _daily_demand([3, 5, 2]), _inventory(), freq="D")
    ledger = result.to_event_frame()
    assert ledger["period"].dtype == "int64"
    assert ledger["period"].tolist() == [1, 2, 3]
