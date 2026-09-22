import json

import numpy as np
import pandas as pd
import pytest

from stockcast import (
    CallbackError,
    InventoryAdjustmentResult,
    InventoryStateDataFrame,
    MaximumOrderQuantity,
    OrderAdjustmentResult,
    OrderingConstraints,
    ScheduledInventoryAdjustment,
    ScheduledOrderHold,
    ScheduledOrderMultiplier,
    ScheduledOrderOverride,
    SimulationCallback,
    SimulationEngine,
)
from stockcast.core.base_policy import BasePolicy
from stockcast.core.data_structures import OrderDecision
from stockcast.core.shelf_life import ShelfLifeEngine


class FixedPolicy(BasePolicy):
    def __init__(self, quantity=10.0, **kwargs):
        super().__init__(**kwargs)
        self.quantity = float(quantity)
        self.fitted_ = True

    def fit(self, forecast_df=None, **kwargs):
        return self

    def predict(self, inventory_state_df, current_period=0, **kwargs):
        frame = inventory_state_df.inventory_position()
        frame["order_quantity"] = self.quantity
        frame["target_level"] = frame["inventory_position"] + self.quantity
        frame["reorder_point"] = pd.NA
        frame["order_period"] = current_period
        frame["expected_delivery_period"] = current_period + self.lead_time
        return OrderDecision(
            frame,
            sku_column=inventory_state_df.sku_column,
            lead_time=self.lead_time,
            review_period=self.review_period,
        )


def _policy(quantity=10.0, *, review_period=1, allow_backorders=False):
    return FixedPolicy(
        quantity,
        lead_time=1,
        review_period=review_period,
        service_level=0.95,
        allow_backorders=allow_backorders,
    )


def _inventory(on_hand=0.0):
    inventory = InventoryStateDataFrame(["A"], max_lead_time=2).initialize_zero(
        start_date=pd.Timestamp("2025-01-01")
    )
    inventory.data["on_hand"] = float(on_hand)
    return inventory


def _demand(values):
    return pd.DataFrame({
        "unique_id": ["A"] * len(values),
        "period": list(range(len(values))),
        "date": pd.date_range("2025-01-02", periods=len(values), freq="D"),
        "y": values,
    })


def _run(engine, callbacks=None, *, inventory=None, policy=None, values=(0.0,), **kwargs):
    values = tuple(values)
    return engine.run(
        policy or _policy(),
        _demand(values),
        inventory or _inventory(),
        n_periods=len(values),
        period_frequency="D",
        initial_decision=kwargs.pop("initial_decision", "none"),
        warmup_periods=kwargs.pop("warmup_periods", 0),
        scoring_periods=kwargs.pop("scoring_periods", len(values)),
        settlement_periods=kwargs.pop("settlement_periods", 0),
        order_during_settlement=kwargs.pop("order_during_settlement", False),
        demand_source_name="callback_test",
        random_seed=None,
        callbacks=callbacks,
        **kwargs,
    )


def _schedule(**values):
    return pd.DataFrame({
        "unique_id": ["A"],
        "period": [values.pop("period", 1)],
        "reason": ["planned test"],
        "source": ["unit test"],
        **{key: [value] for key, value in values.items()},
    })


def test_callbacks_none_and_empty_preserve_events_and_final_state():
    none = _run(SimulationEngine(), callbacks=None, values=(1.0, 0.0), inventory=_inventory(3))
    empty = _run(SimulationEngine(), callbacks=[], values=(1.0, 0.0), inventory=_inventory(3))
    pd.testing.assert_frame_equal(none.to_event_frame(), empty.to_event_frame())
    pd.testing.assert_frame_equal(
        none.inventory.get_dataframe(), empty.inventory.get_dataframe()
    )
    assert none.run_manifest["run_settings"]["callbacks"] == []


def test_order_callbacks_compose_before_constraints_and_record_exact_trail():
    callbacks = [
        ScheduledOrderMultiplier(_schedule(multiplier=2.0)),
        ScheduledOrderOverride(_schedule(order_quantity=18.0)),
    ]
    result = _run(
        SimulationEngine(),
        callbacks,
        order_constraints=OrderingConstraints([MaximumOrderQuantity(15.0, mode="adjust")]),
    )
    event = result.to_event_frame().iloc[0]
    assert event["requested_order_quantity"] == 10.0
    assert event["callback_adjustment_units"] == 8.0
    assert event["callback_adjusted_order_quantity"] == 18.0
    assert event["constraint_adjustment_units"] == -3.0
    assert event["constrained_order_quantity"] == 15.0
    assert event["order_quantity"] == 15.0
    audit = result.to_callback_audit_frame()
    assert audit["before_value"].tolist() == [10.0, 20.0]
    assert audit["after_value"].tolist() == [20.0, 18.0]
    assert len(json.loads(audit.to_json(orient="records", date_format="iso"))) == 2


def test_mutating_defensive_order_decision_then_returning_none_has_no_effect():
    class MutatesView(SimulationCallback):
        def on_after_prediction(self, decision, context):
            decision.data.loc[:, "order_quantity"] = 999.0

    result = _run(SimulationEngine(), [MutatesView()])
    event = result.to_event_frame().iloc[0]
    assert event["requested_order_quantity"] == 10.0
    assert event["callback_adjusted_order_quantity"] == 10.0
    assert event["order_quantity"] == 10.0


def test_order_hold_runs_for_opening_prediction_but_not_without_prediction():
    hold = ScheduledOrderHold(_schedule(period=0))
    opening = _run(
        SimulationEngine(), [hold], initial_decision="before_first_demand",
        policy=_policy(review_period=3),
    )
    assert opening.to_event_frame().iloc[0]["event_type"] == "initial_decision"
    assert opening.to_event_frame().iloc[0]["order_quantity"] == 0.0
    assert len(opening.to_callback_audit_frame()) == 1
    no_opening = _run(SimulationEngine(), [hold], policy=_policy(review_period=3))
    assert no_opening.to_callback_audit_frame().empty


def test_physical_adjustment_is_after_demand_before_order_and_audited():
    adjustment = ScheduledInventoryAdjustment(_schedule(quantity_delta=-2.0))
    result = _run(
        SimulationEngine(), [adjustment], inventory=_inventory(5.0), values=(3.0,)
    )
    event = result.to_event_frame().iloc[0]
    assert event["fulfilled_units"] == 3.0
    assert event["inventory_adjustment_units"] == -2.0
    assert event["ending_on_hand"] == 0.0
    assert event["requested_order_quantity"] == 10.0
    audit = result.to_callback_audit_frame().iloc[0]
    assert audit["before_value"] == 2.0
    assert audit["after_value"] == 0.0
    assert result.inventory.get_dataframe().iloc[0]["on_hand"] == 0.0
    assert result.history.iloc[-1]["on_hand"] == 0.0


def test_physical_adjustment_changes_inventory_sensitive_policy_prediction():
    class OrderUpToTen(BasePolicy):
        def __init__(self):
            super().__init__(
                lead_time=1,
                review_period=1,
                service_level=0.95,
                allow_backorders=False,
            )
            self.fitted_ = True

        def predict(self, inventory_state_df, current_period=0, **kwargs):
            frame = inventory_state_df.inventory_position()
            frame["order_quantity"] = (10.0 - frame["inventory_position"]).clip(lower=0.0)
            frame["order_period"] = current_period
            frame["expected_delivery_period"] = current_period + self.lead_time
            return OrderDecision(
                frame,
                sku_column=inventory_state_df.sku_column,
                lead_time=self.lead_time,
                review_period=self.review_period,
            )

    adjustment = ScheduledInventoryAdjustment(_schedule(quantity_delta=4.0))
    result = _run(
        SimulationEngine(),
        [adjustment],
        policy=OrderUpToTen(),
        inventory=_inventory(0.0),
    )
    event = result.to_event_frame().iloc[0]
    assert event["inventory_adjustment_units"] == 4.0
    assert event["requested_order_quantity"] == 6.0


def test_custom_calendar_callback_uses_defensive_dataframe_copy():
    class SecondWeekLoss(SimulationCallback):
        def on_after_demand(self, context):
            state = context.inventory
            quantity_delta = -0.10 * state["on_hand"]
            state.loc[:, "on_hand"] = 999.0
            if 8 <= context.date.day <= 14:
                return InventoryAdjustmentResult(pd.DataFrame({
                    "unique_id": state[context.sku_column],
                    "quantity_delta": quantity_delta,
                    "reason": "second-week loss",
                    "source": "custom callback",
                }))
            return None

        def get_config(self):
            return {"fraction": 0.1, "days": [8, 14]}

    result = _run(
        SimulationEngine(), [SecondWeekLoss()], inventory=_inventory(10.0),
        values=(0.0,) * 8, policy=_policy(0.0),
    )
    # Only Jan 8 and Jan 9 fall in the selected range for this run.
    assert result.to_event_frame()["inventory_adjustment_units"].sum() == pytest.approx(-1.9)
    assert result.inventory.get_dataframe().iloc[0]["on_hand"] == pytest.approx(8.1)


def test_invalid_batch_is_atomic_and_callback_failures_are_contextual():
    class InvalidBatch(SimulationCallback):
        def on_after_demand(self, context):
            return InventoryAdjustmentResult(pd.DataFrame({
                "unique_id": ["A"],
                "quantity_delta": [-20.0],
                "reason": ["invalid"],
                "source": ["test"],
            }))

    inventory = _inventory(5.0)
    with pytest.raises(CallbackError, match=r"callback\[0\].*on_after_demand.*period 1"):
        _run(SimulationEngine(), [InvalidBatch()], inventory=inventory)
    assert inventory.get_dataframe().iloc[0]["on_hand"] == 5.0


def test_unsupported_columns_and_opaque_config_fail_closed():
    class Unsupported(SimulationCallback):
        def on_after_demand(self, context):
            return InventoryAdjustmentResult(pd.DataFrame({
                "unique_id": ["A"], "quantity_delta": [1.0],
                "reason": ["x"], "source": ["x"], "backorders": [0.0],
            }))

    class Opaque(SimulationCallback):
        def get_config(self):
            return {"object": object()}

    with pytest.raises(CallbackError, match="unsupported columns"):
        _run(SimulationEngine(), [Unsupported()])
    with pytest.raises(CallbackError, match="preflight.*JSON serializable"):
        _run(SimulationEngine(), [Opaque()])


def test_positive_adjustment_with_backlog_fails_closed():
    add = ScheduledInventoryAdjustment(_schedule(quantity_delta=1.0))
    with pytest.raises(CallbackError, match="positive inventory adjustment.*backlog"):
        _run(
            SimulationEngine(), [add], policy=_policy(allow_backorders=True),
            inventory=_inventory(), values=(2.0,),
        )


def test_shelf_life_adjustments_keep_fifo_ledger_in_sync():
    schedule = _schedule(
        quantity_delta=3.0,
        received_date=pd.Timestamp("2025-01-02"),
    )
    result = _run(
        ShelfLifeEngine(shelf_life_days=3),
        [ScheduledInventoryAdjustment(schedule)],
        inventory=_inventory(),
        values=(0.0, 0.0, 0.0, 0.0),
        opening_lots=pd.DataFrame(columns=["unique_id", "received_date", "quantity"]),
    )
    events = result.to_event_frame().set_index("period")
    assert events.loc[1, "inventory_adjustment_units"] == 3.0
    assert events.loc[4, "expired_units"] == 3.0
    assert result.to_callback_audit_frame().iloc[0]["lot_evidence"]


def test_callback_instance_resets_for_each_comparison_branch():
    class Counter(SimulationCallback):
        def __init__(self):
            self.reset_count = 0
            self.calls = 0

        def reset(self, context):
            self.reset_count += 1
            self.calls = 0

        def on_after_prediction(self, decision, context):
            self.calls += 1

        def get_config(self):
            return {"kind": "counter"}

    callback = Counter()
    result = SimulationEngine().run_comparison(
        [_policy(), _policy(5.0)],
        _demand((0.0,)),
        _inventory(),
        1,
        period_frequency="D",
        initial_decision="none",
        warmup_periods=0,
        scoring_periods=1,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="comparison_callback_test",
        random_seed=None,
        callbacks=[callback],
    )
    assert callback.reset_count == 2
    assert callback.calls == 1
    assert all(
        value.run_manifest["run_settings"]["callbacks"][0]["config"]
        == {"kind": "counter"}
        for value in result.results.values()
    )
    json.dumps(result["FixedPolicy"].run_manifest)


def test_physical_phase_runs_in_settlement_while_order_phase_respects_disable_flag():
    inventory = ScheduledInventoryAdjustment(_schedule(period=2, quantity_delta=1.0))
    order = ScheduledOrderOverride(_schedule(period=2, order_quantity=99.0))
    result = _run(
        SimulationEngine(), [inventory, order], policy=_policy(0.0), values=(0.0, 0.0),
        scoring_periods=1, settlement_periods=1,
    )
    settlement = result.to_event_frame().set_index("period").loc[2]
    assert settlement["run_window"] == "settlement"
    assert settlement["inventory_adjustment_units"] == 1.0
    assert settlement["decision_flag"]
    assert settlement["order_quantity"] == 0.0
    assert result.to_callback_audit_frame()["phase"].tolist() == ["on_after_demand"]


def test_invalid_order_result_and_callback_exception_stop_later_callbacks():
    calls = []

    class InvalidOrder(SimulationCallback):
        def on_after_prediction(self, decision, context):
            calls.append("invalid")
            return OrderAdjustmentResult(pd.DataFrame({
                "unique_id": ["A"], "order_quantity": [-1.0],
                "reason": ["invalid"], "source": ["test"],
            }))

    class Later(SimulationCallback):
        def on_after_prediction(self, decision, context):
            calls.append("later")

    with pytest.raises(CallbackError, match="order_quantity.*finite values >= 0"):
        _run(SimulationEngine(), [InvalidOrder(), Later()])
    assert calls == ["invalid"]

    class Raises(SimulationCallback):
        def on_after_demand(self, context):
            raise LookupError("original callback cause")

    with pytest.raises(CallbackError, match="original callback cause") as raised:
        _run(SimulationEngine(), [Raises()])
    assert isinstance(raised.value.__cause__, LookupError)


def test_builtin_schedule_both_coordinates_must_agree():
    schedule = _schedule(
        period=1,
        date=pd.Timestamp("2025-01-03"),
        order_quantity=5.0,
    )
    with pytest.raises(CallbackError, match="same simulation point"):
        _run(SimulationEngine(), [ScheduledOrderOverride(schedule)])


@pytest.mark.parametrize(
    ("schedule", "message"),
    [
        (_schedule(quantity_delta=-6.0), "exceeds on_hand"),
        (
            _schedule(quantity_delta=1.0),
            "require received_date",
        ),
        (
            _schedule(
                quantity_delta=1.0,
                received_date=pd.Timestamp("2024-12-01"),
            ),
            "already expired",
        ),
        (
            _schedule(
                quantity_delta=1.0,
                received_date=pd.Timestamp("2025-01-03"),
            ),
            "cannot be in the future",
        ),
    ],
)
def test_shelf_life_adjustment_failures(schedule, message):
    with pytest.raises(CallbackError, match=message):
        _run(
            ShelfLifeEngine(shelf_life_days=3),
            [ScheduledInventoryAdjustment(schedule)],
            inventory=_inventory(5.0),
            opening_lots=pd.DataFrame({
                "unique_id": ["A"],
                "received_date": [pd.Timestamp("2025-01-01")],
                "quantity": [5.0],
            }),
        )


def test_fifo_removal_records_oldest_lot_evidence():
    inventory = _inventory(5.0)
    lots = pd.DataFrame({
        "unique_id": ["A", "A"],
        "received_date": pd.to_datetime(["2024-12-31", "2025-01-01"]),
        "quantity": [2.0, 3.0],
    })
    result = _run(
        ShelfLifeEngine(shelf_life_days=30),
        [ScheduledInventoryAdjustment(_schedule(quantity_delta=-3.0))],
        inventory=inventory,
        policy=_policy(0.0),
        opening_lots=lots,
    )
    evidence = json.loads(result.to_callback_audit_frame().iloc[0]["lot_evidence"])
    assert evidence == [
        {
            "action": "consume",
            "quantity": 2.0,
            "received_date": "2024-12-31T00:00:00",
        },
        {
            "action": "consume",
            "quantity": 1.0,
            "received_date": "2025-01-01T00:00:00",
        },
    ]


def test_event_ledger_fixture_for_callback_and_constraint_ordering():
    result = _run(
        SimulationEngine(),
        [ScheduledOrderMultiplier(_schedule(multiplier=2.0))],
        order_constraints=OrderingConstraints([
            MaximumOrderQuantity(15.0, mode="adjust")
        ]),
    )
    actual = result.to_event_frame()[[
        "unique_id",
        "event_type",
        "period",
        "requested_order_quantity",
        "callback_adjustment_units",
        "callback_adjusted_order_quantity",
        "constraint_adjustment_units",
        "constrained_order_quantity",
        "order_quantity",
        "inventory_adjustment_units",
    ]].to_dict(orient="records")
    assert actual == [{
        "unique_id": "A",
        "event_type": "period",
        "period": 1,
        "requested_order_quantity": 10.0,
        "callback_adjustment_units": 10.0,
        "callback_adjusted_order_quantity": 20.0,
        "constraint_adjustment_units": -5.0,
        "constrained_order_quantity": 15.0,
        "order_quantity": 15.0,
        "inventory_adjustment_units": 0.0,
    }]


@pytest.mark.parametrize("callbacks", [SimulationCallback(), [object()]])
def test_callback_list_and_elements_are_typed(callbacks):
    with pytest.raises(TypeError, match="callbacks must"):
        _run(SimulationEngine(), callbacks)


@pytest.mark.parametrize("phase", ["inventory", "order"])
def test_callback_results_require_all_declared_columns(phase):
    class MissingColumns(SimulationCallback):
        def on_after_demand(self, context):
            if phase == "inventory":
                return InventoryAdjustmentResult(pd.DataFrame({
                    "unique_id": ["A"], "quantity_delta": [1.0]
                }))

        def on_after_prediction(self, decision, context):
            if phase == "order":
                return OrderAdjustmentResult(pd.DataFrame({
                    "unique_id": ["A"], "order_quantity": [1.0]
                }))

    with pytest.raises(CallbackError, match="missing required columns"):
        _run(SimulationEngine(), [MissingColumns()])


@pytest.mark.parametrize("phase", ["inventory", "order"])
def test_callback_results_reject_unknown_skus(phase):
    class UnknownSku(SimulationCallback):
        def on_after_demand(self, context):
            if phase == "inventory":
                return InventoryAdjustmentResult(pd.DataFrame({
                    "unique_id": ["UNKNOWN"], "quantity_delta": [1.0],
                    "reason": ["invalid"], "source": ["test"],
                }))

        def on_after_prediction(self, decision, context):
            if phase == "order":
                return OrderAdjustmentResult(pd.DataFrame({
                    "unique_id": ["UNKNOWN"], "order_quantity": [1.0],
                    "reason": ["invalid"], "source": ["test"],
                }))

    with pytest.raises(CallbackError, match="UNKNOWN"):
        _run(SimulationEngine(), [UnknownSku()])


@pytest.mark.parametrize(
    ("phase", "quantity"),
    [
        ("inventory", np.nan),
        ("inventory", np.inf),
        ("order", np.nan),
        ("order", np.inf),
    ],
)
def test_callback_results_reject_nonfinite_quantities(phase, quantity):
    class Nonfinite(SimulationCallback):
        def on_after_demand(self, context):
            if phase == "inventory":
                return InventoryAdjustmentResult(pd.DataFrame({
                    "unique_id": ["A"], "quantity_delta": [quantity],
                    "reason": ["invalid"], "source": ["test"],
                }))

        def on_after_prediction(self, decision, context):
            if phase == "order":
                return OrderAdjustmentResult(pd.DataFrame({
                    "unique_id": ["A"], "order_quantity": [quantity],
                    "reason": ["invalid"], "source": ["test"],
                }))

    with pytest.raises(CallbackError, match="finite"):
        _run(SimulationEngine(), [Nonfinite()])


@pytest.mark.parametrize(
    "coordinates",
    [
        {"period": np.nan},
        {"period": 1.5},
        {"period": -1},
        {"date": "not-a-date"},
    ],
)
def test_builtin_schedule_rejects_invalid_coordinate_values(coordinates):
    schedule = pd.DataFrame({
        "unique_id": ["A"],
        "order_quantity": [1.0],
        "reason": ["invalid"],
        "source": ["test"],
        **{key: [value] for key, value in coordinates.items()},
    })
    with pytest.raises(ValueError, match=r"schedule\.(period|date)"):
        ScheduledOrderOverride(schedule)


def test_callback_resets_across_two_ordinary_runs():
    class Counter(SimulationCallback):
        def __init__(self):
            self.reset_count = 0
            self.calls = 0

        def reset(self, context):
            self.reset_count += 1
            self.calls = 0

        def on_after_demand(self, context):
            self.calls += 1

    callback = Counter()
    _run(SimulationEngine(), [callback], policy=_policy(0.0))
    assert (callback.reset_count, callback.calls) == (1, 1)
    _run(SimulationEngine(), [callback], policy=_policy(0.0))
    assert (callback.reset_count, callback.calls) == (2, 1)


def test_failed_demand_preflight_does_not_reset_callback():
    class Counter(SimulationCallback):
        def __init__(self):
            self.reset_count = 0

        def reset(self, context):
            self.reset_count += 1

    callback = Counter()
    invalid_demand = _demand((0.0,))
    invalid_demand.loc[:, "date"] = pd.Timestamp("2025-01-03")

    with pytest.raises(ValueError, match="must use date 2025-01-02"):
        SimulationEngine().run(
            _policy(),
            invalid_demand,
            _inventory(),
            n_periods=1,
            period_frequency="D",
            initial_decision="none",
            warmup_periods=0,
            scoring_periods=1,
            settlement_periods=0,
            order_during_settlement=False,
            demand_source_name="invalid_preflight_test",
            random_seed=None,
            callbacks=[callback],
        )

    assert callback.reset_count == 0


def test_failed_policy_schedule_preflight_does_not_reset_callback():
    class Counter(SimulationCallback):
        def __init__(self):
            self.reset_count = 0

        def reset(self, context):
            self.reset_count += 1

    callback = Counter()
    with pytest.raises(ValueError, match="not a decision event"):
        _run(
            SimulationEngine(),
            [callback],
            policy_schedule={2: _policy()},
        )

    assert callback.reset_count == 0


def test_mutating_returned_callback_audit_does_not_change_result():
    result = _run(
        SimulationEngine(),
        [ScheduledOrderOverride(_schedule(order_quantity=7.0))],
    )
    returned = result.to_callback_audit_frame()
    returned.loc[:, "after_value"] = 999.0

    stored = result.to_callback_audit_frame()
    assert stored.iloc[0]["after_value"] == 7.0


def test_after_demand_runs_in_warmup_scoring_and_settlement():
    class WindowRecorder(SimulationCallback):
        def reset(self, context):
            self.windows = []

        def on_after_demand(self, context):
            self.windows.append(context.run_window)

    callback = WindowRecorder()
    _run(
        SimulationEngine(), [callback], policy=_policy(0.0),
        values=(0.0, 0.0, 0.0), warmup_periods=1,
        scoring_periods=1, settlement_periods=1,
    )
    assert callback.windows == ["warmup", "scoring", "settlement"]


def test_builtins_match_date_only_and_matching_period_plus_date():
    callbacks = [
        ScheduledOrderMultiplier(pd.DataFrame({
            "unique_id": ["A"],
            "date": [pd.Timestamp("2025-01-02")],
            "multiplier": [2.0],
            "reason": ["date only"],
            "source": ["test"],
        })),
        ScheduledOrderOverride(pd.DataFrame({
            "unique_id": ["A"],
            "period": [2],
            "date": [pd.Timestamp("2025-01-03")],
            "order_quantity": [7.0],
            "reason": ["matching coordinates"],
            "source": ["test"],
        })),
    ]
    result = _run(
        SimulationEngine(), callbacks, policy=_policy(4.0), values=(0.0, 0.0)
    )
    events = result.to_event_frame().set_index("period")
    assert events.loc[1, "callback_adjusted_order_quantity"] == 8.0
    assert events.loc[2, "callback_adjusted_order_quantity"] == 7.0


def test_selected_policy_schedule_predicts_before_order_callback():
    class PredictionRecorder(SimulationCallback):
        def reset(self, context):
            self.quantities = []

        def on_after_prediction(self, decision, context):
            self.quantities.append(
                float(decision.get_dataframe().iloc[0]["order_quantity"])
            )

    callback = PredictionRecorder()
    result = _run(
        SimulationEngine(), [callback], policy=_policy(2.0),
        policy_schedule={1: _policy(9.0)},
    )
    assert callback.quantities == [9.0]
    assert result.to_event_frame().iloc[0]["requested_order_quantity"] == 9.0


def test_immutable_no_callback_and_physical_event_rows():
    no_callback = _run(
        SimulationEngine(), None, policy=_policy(0.0),
        inventory=_inventory(5.0), values=(2.0,),
    ).to_event_frame().iloc[0]
    assert no_callback[[
        "starting_on_hand",
        "demand",
        "fulfilled_units",
        "inventory_adjustment_units",
        "ending_on_hand",
        "requested_order_quantity",
        "callback_adjustment_units",
        "callback_adjusted_order_quantity",
        "order_quantity",
    ]].to_dict() == {
        "starting_on_hand": 5.0,
        "demand": 2.0,
        "fulfilled_units": 2.0,
        "inventory_adjustment_units": 0.0,
        "ending_on_hand": 3.0,
        "requested_order_quantity": 0.0,
        "callback_adjustment_units": 0.0,
        "callback_adjusted_order_quantity": 0.0,
        "order_quantity": 0.0,
    }

    physical = _run(
        SimulationEngine(),
        [ScheduledInventoryAdjustment(_schedule(quantity_delta=-1.0))],
        policy=_policy(0.0), inventory=_inventory(5.0), values=(2.0,),
    ).to_event_frame().iloc[0]
    assert physical[[
        "starting_on_hand",
        "demand",
        "fulfilled_units",
        "inventory_adjustment_units",
        "ending_on_hand",
    ]].to_dict() == {
        "starting_on_hand": 5.0,
        "demand": 2.0,
        "fulfilled_units": 2.0,
        "inventory_adjustment_units": -1.0,
        "ending_on_hand": 2.0,
    }
