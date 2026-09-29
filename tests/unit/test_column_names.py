"""One set of input column names, overridable everywhere.

Every object that reads a user table takes the column names as arguments,
defaulting to ``unique_id``, ``date``, ``period`` and ``y``. A run with renamed
columns must equal the run with the default names.
"""

import pandas as pd
import pytest

from stockcast.core import (
    InventoryStateDataFrame,
    ScheduledInventoryAdjustment,
    ScheduledOrderHold,
    ScheduledOrderMultiplier,
    ScheduledOrderOverride,
    ShelfLife,
    SimulationEngine,
)
from stockcast.policies import OrderUpToPolicy
from stockcast.utils import DemandGenerator

OPENING = pd.Timestamp("2026-01-05")
SKUS = ["tea", "coffee"]
DEFAULT = dict(sku_column="unique_id", date_column="date", period_column="period")
CUSTOM = dict(sku_column="item", date_column="ds", period_column="t")


def _poisson(rng, periods):
    return rng.poisson(6.0, periods.size)


def _demand(demand_column="y", **columns):
    generator = DemandGenerator(
        SKUS, first_date=OPENING + pd.Timedelta(days=1), freq="D", random_seed=1,
        demand_column=demand_column, **columns,
    )
    return generator.sample(14, _poisson)


def _policy():
    target = pd.DataFrame({"unique_id": SKUS, "S": [30.0, 25.0]})
    policy = OrderUpToPolicy(1, 2, freq="D", allow_backorders=False)
    return policy.fit(target, target_column="S", forecast_origin=OPENING)


def _inventory():
    stock = pd.DataFrame({"unique_id": SKUS, "on_hand": [20.0, 15.0]})
    return InventoryStateDataFrame.from_observed(stock, opening_date=OPENING)


def _hold(sku_column="unique_id", date_column="date", period_column="period"):
    schedule = pd.DataFrame({sku_column: ["tea"], date_column: [OPENING + pd.Timedelta(days=3)]})
    return ScheduledOrderHold(
        schedule, sku_column=sku_column, date_column=date_column, period_column=period_column,
    )


def _shelf_life(sku_column="unique_id"):
    lots = pd.DataFrame({
        sku_column: SKUS, "received_date": [OPENING, OPENING], "quantity": [20.0, 15.0],
    })
    return ShelfLife(3, lots, sku_column=sku_column)


def _comparable(result):
    manifest = {
        key: value for key, value in result.run_manifest.items()
        if key not in {"run_id", "created_at_utc"}
    }
    return (
        result.to_event_frame(), result.to_order_frame(),
        result.to_callback_audit_frame(), result.to_process_flow_frame(), manifest,
    )


def _assert_same(left, right):
    for a, b in zip(_comparable(left), _comparable(right)):
        if isinstance(a, pd.DataFrame):
            pd.testing.assert_frame_equal(a, b)
        else:
            assert a == b


def test_defaults_are_the_documented_names():
    engine = SimulationEngine()
    assert (engine.sku_column, engine.date_column, engine.period_column,
            engine.demand_column) == (None, "date", "period", "y")
    assert list(_demand().columns) == ["unique_id", "y", "period", "date"]


def test_renamed_columns_give_the_same_run():
    default = SimulationEngine().run(
        _policy(), _demand(**DEFAULT), _inventory(),
        callbacks=[_hold(**DEFAULT)], processes=[_shelf_life()],
    )
    # The hold and the shelf life act, so their renamed inputs are exercised.
    assert (default.to_callback_audit_frame()["callback_class"] == "ScheduledOrderHold").any()
    assert default.to_event_frame()["expired_units"].sum() > 0
    engine = SimulationEngine(demand_column="sales", **CUSTOM)
    demand = _demand(demand_column="sales", **CUSTOM)
    assert list(demand.columns) == ["item", "sales", "t", "ds"]
    renamed = engine.run(
        _policy(), demand, _inventory(),
        callbacks=[_hold(**CUSTOM)], processes=[_shelf_life("item")],
    )
    _assert_same(default, renamed)
    # Date-only and period-only tables work with renamed columns too.
    for dropped in ("ds", "t"):
        _assert_same(default, engine.run(
            _policy(), demand.drop(columns=dropped), _inventory(),
            callbacks=[_hold(**CUSTOM)], processes=[_shelf_life("item")],
        ))


def test_renamed_columns_in_a_callable_and_a_comparison():
    default_demand = _demand()
    renamed_demand = default_demand.rename(
        columns={"unique_id": "item", "date": "ds", "period": "t", "y": "sales"},
    )
    engine = SimulationEngine(demand_column="sales", **CUSTOM)

    def by_period(frame, column):
        return lambda period: frame[frame[column] == period].drop(columns=column)

    default = SimulationEngine().run(
        _policy(), by_period(default_demand, "period"), _inventory(), n_periods=14,
    )
    renamed = engine.run(
        _policy(), by_period(renamed_demand, "t"), _inventory(), n_periods=14,
    )
    _assert_same(default, renamed)

    compared = engine.run_comparison([_policy(), _policy()], renamed_demand, _inventory(),
                                     labels=["a", "b"])
    reference = SimulationEngine().run_comparison([_policy(), _policy()], default_demand,
                                                  _inventory(), labels=["a", "b"])
    for label in ("a", "b"):
        _assert_same(reference.results[label], compared.results[label])
    assert engine._demand_is_canonical is False


def test_every_scheduled_callback_takes_the_column_names():
    day = OPENING + pd.Timedelta(days=2)
    values = {
        ScheduledOrderOverride: {"order_quantity": [5.0]},
        ScheduledOrderMultiplier: {"multiplier": [2.0]},
        ScheduledOrderHold: {},
        ScheduledInventoryAdjustment: {"quantity_delta": [-1.0]},
    }
    for callback_class, extra in values.items():
        default = callback_class(pd.DataFrame({"unique_id": ["tea"], "date": [day], **extra}))
        renamed = callback_class(
            pd.DataFrame({"item": ["tea"], "ds": [day], **extra}), **CUSTOM,
        )
        pd.testing.assert_frame_equal(default.schedule, renamed.schedule)
        assert default.get_config() == renamed.get_config()


@pytest.mark.parametrize("frame, message", [
    (pd.DataFrame({"unique_id": ["tea"], "ds": [OPENING], "y": [1.0]}), r"missing columns: \['sales'\]"),
    (pd.DataFrame({"unique_id": ["tea"], "ds": [OPENING], "sales": [1.0], "y": [1.0]}),
     "holds both 'sales' and 'y'"),
    (pd.DataFrame({"unique_id": ["tea"], "date": [OPENING], "sales": [1.0]}),
     "has a 'date' column, but the named column is 'ds'"),
])
def test_engine_rejects_missing_or_ambiguous_named_columns(frame, message):
    engine = SimulationEngine(date_column="ds", demand_column="sales")
    with pytest.raises(ValueError, match=message):
        engine.run(_policy(), frame, _inventory())


@pytest.mark.parametrize("make", [
    lambda: SimulationEngine(date_column="period"),
    lambda: SimulationEngine(demand_column=""),
    lambda: SimulationEngine(sku_column=3),
    lambda: DemandGenerator(SKUS, first_date=OPENING, freq="D", random_seed=0, date_column="y"),
    lambda: DemandGenerator(SKUS, first_date=OPENING, freq="D", random_seed=0, sku_column=""),
    lambda: ScheduledOrderHold(pd.DataFrame({"unique_id": ["tea"], "date": [OPENING]}),
                               date_column="period"),
])
def test_column_names_must_be_distinct_non_empty_strings(make):
    with pytest.raises(ValueError, match="column|differ"):
        make()


def test_callbacks_and_shelf_life_reject_missing_named_columns():
    schedule = pd.DataFrame({"unique_id": ["tea"], "date": [OPENING]})
    with pytest.raises(ValueError, match=r"schedule is missing columns: \['item'\]"):
        ScheduledOrderHold(schedule, sku_column="item")
    with pytest.raises(ValueError, match="schedule has a 'date' column, but the named column is 'ds'"):
        ScheduledOrderHold(schedule, date_column="ds")
    lots = pd.DataFrame({"unique_id": ["tea"], "received_date": [OPENING], "quantity": [1.0]})
    with pytest.raises(ValueError, match=r"opening_lots is missing columns: \['item'\]"):
        ShelfLife(10, lots, sku_column="item")


def test_policies_take_their_table_column_names_in_the_constructor():
    from stockcast.policies import ReorderPointPolicy, SingleOrderPolicy

    end = OPENING + pd.Timedelta(days=3)
    default_table = pd.DataFrame({"unique_id": SKUS, "S": [30.0, 25.0], "date": end})
    renamed_table = default_table.rename(columns={"unique_id": "item", "date": "end"})
    default = OrderUpToPolicy(1, 2, freq="D", allow_backorders=False).fit(
        default_table, target_column="S",
    )
    renamed = OrderUpToPolicy(
        1, 2, freq="D", allow_backorders=False, sku_column="item", date_column="end",
    ).fit(renamed_table, target_column="S")
    assert renamed.get_target_metadata() == default.get_target_metadata()
    pd.testing.assert_frame_equal(
        renamed.get_target_levels().rename(columns={"item": "unique_id"}),
        default.get_target_levels(),
    )
    stock = InventoryStateDataFrame.from_observed(
        pd.DataFrame({"item": SKUS, "on_hand": [20.0, 15.0]}), opening_date=OPENING,
        sku_column="item",
    )
    assert renamed.predict(stock, current_period=0).get_dataframe()["order_quantity"].tolist() == (
        default.predict(_inventory(), current_period=0).get_dataframe()["order_quantity"].tolist()
    )

    reorder = ReorderPointPolicy(
        1, 2, freq="D", allow_backorders=False, sku_column="item", date_column="end",
    ).fit(renamed_table.assign(s=5.0), reorder_point_column="s", order_up_to_column="S")
    assert reorder.get_target_metadata()["forecast_origin"] == OPENING.isoformat()
    assert reorder.get_parameters().columns.tolist() == ["item", "reorder_point", "order_up_to_level"]

    season = SingleOrderPolicy(
        1, freq="D", selling_horizon=2, allow_backorders=False,
        sku_column="item", date_column="end",
    ).fit(renamed_table, target_column="S")
    assert season.get_target_metadata()["forecast_origin"] == OPENING.isoformat()


@pytest.mark.parametrize("name", ["sku_column", "date_column"])
def test_policy_fit_no_longer_takes_column_names(name):
    policy = OrderUpToPolicy(1, 2, freq="D", allow_backorders=False)
    with pytest.raises(TypeError, match=name):
        policy.fit(pd.DataFrame({"unique_id": SKUS, "S": 1.0}), target_column="S",
                   forecast_origin=OPENING, **{name: "x"})
    with pytest.raises(ValueError, match=f"{name} must be a non-empty column name"):
        OrderUpToPolicy(1, 2, freq="D", allow_backorders=False, **{name: ""})


def test_a_named_policy_date_column_must_exist():
    policy = OrderUpToPolicy(1, 2, freq="D", allow_backorders=False, date_column="end")
    with pytest.raises(ValueError, match="date column 'end' not found in target_df"):
        policy.fit(pd.DataFrame({"unique_id": SKUS, "S": 1.0}), target_column="S",
                   forecast_origin=OPENING)


def test_normal_from_history_reads_the_generators_own_columns():
    generator = DemandGenerator(
        SKUS, first_date=OPENING, freq="D", random_seed=2,
        sku_column="item", demand_column="sales",
    )
    history = generator.normal(10, mean=5.0, std=1.0)
    future = generator.normal_from_history(history, 3)
    assert list(future.columns) == ["item", "sales", "period", "date"]
    with pytest.raises(TypeError):
        generator.normal_from_history(history, 3, "sales")


def test_the_stock_table_and_opening_date_names():
    stock = pd.DataFrame({"unique_id": SKUS, "on_hand": [20.0, 15.0]})
    state = InventoryStateDataFrame(SKUS).initialize_from_observed(
        stock_df=stock, opening_date=OPENING,
    )
    assert state.get_dataframe()["date"].iloc[0] == OPENING
    with pytest.raises(TypeError, match="start_date"):
        InventoryStateDataFrame.from_observed(stock, start_date=OPENING)
    with pytest.raises(TypeError, match="start_date"):
        InventoryStateDataFrame(SKUS).initialize_zero(start_date=OPENING)
    with pytest.raises(ValueError, match=r"give opening_date=\.\.\., or a date column in stock_df"):
        InventoryStateDataFrame.from_observed(stock)
