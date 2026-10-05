import pandas as pd
import pytest

from stockcast import (
    ConstraintResult,
    InventoryStateDataFrame,
    MaximumOrderQuantity,
    MinimumOrderQuantity,
    OrderingConstraint,
    OrderingConstraints,
    OrderMultiple,
    SimulationEngine,
)
from stockcast.core.base_policy import BasePolicy
from stockcast.core.data_structures import OrderDecision


class FixedOrderPolicy(BasePolicy):
    def __init__(self, quantity, **kwargs):
        super().__init__(**kwargs)
        self.quantity = float(quantity)
        self.fitted_ = True

    def fit(self, forecast_df=None, **kwargs):
        return self

    def predict(self, inventory_state_df, current_period=0, **kwargs):
        result = inventory_state_df.inventory_position()
        result["order_quantity"] = self.quantity
        result["target_level"] = result["inventory_position"] + self.quantity
        result["reorder_point"] = pd.NA
        result["order_period"] = current_period
        result["expected_delivery_period"] = current_period + self.lead_time
        return OrderDecision(
            result,
            sku_column=inventory_state_df.sku_column,
            lead_time=self.lead_time,
        )


def _decision(skus, quantities):
    return OrderDecision(pd.DataFrame({
        "unique_id": skus,
        "order_quantity": quantities,
        "target_level": quantities,
        "inventory_position": [0.0] * len(skus),
        "reorder_point": [pd.NA] * len(skus),
        "order_period": [1] * len(skus),
        "expected_delivery_period": [2] * len(skus),
    }), lead_time=1)


def _inventory(skus=("A",)):
    return InventoryStateDataFrame(list(skus), max_lead_time=1).initialize_zero(
        opening_date=pd.Timestamp("2025-01-01")
    )


def _context(inventory):
    from stockcast import ConstraintContext
    return ConstraintContext(inventory=inventory, policy=None, decision_period=1)


def test_explicit_composition_order_applies_moq_then_multiple():
    constraints = OrderingConstraints([
        MinimumOrderQuantity({"A": 4.0, "B": 4.0}, mode="adjust"),
        OrderMultiple({"A": 2.0, "B": 2.0}, mode="adjust"),
    ])
    result = constraints.apply(
        _decision(["A", "B"], [3.0, 7.0]),
        _context(_inventory(("A", "B"))),
    )

    assert result.order.get_dataframe()["order_quantity"].tolist() == [4.0, 8.0]
    assert result.audit["binding_constraints"].tolist() == [
        "minimum_order_quantity",
        "order_multiple",
    ]


@pytest.mark.parametrize(
    "constraints",
    [
        [
            OrderMultiple(6.0, mode="adjust"),
            MaximumOrderQuantity(20.0, mode="adjust"),
        ],
        [
            MaximumOrderQuantity(20.0, mode="adjust"),
            OrderMultiple(6.0, mode="adjust"),
        ],
    ],
)
def test_composition_rejects_a_final_order_that_breaks_an_earlier_constraint(
    constraints,
):
    with pytest.raises(ValueError, match="jointly feasible quantity"):
        OrderingConstraints(constraints).apply(
            _decision(["A"], [19.0]),
            _context(_inventory()),
        )


def test_constraints_normalize_omitted_inventory_skus_to_zero_orders():
    result = OrderingConstraints([
        MaximumOrderQuantity(10.0, mode="adjust"),
    ]).apply(
        _decision(["A"], [5.0]),
        _context(_inventory(("A", "B"))),
    )

    frame = result.order.get_dataframe().set_index("unique_id")
    assert frame.loc["A", "order_quantity"] == 5.0
    assert frame.loc["B", "order_quantity"] == 0.0
    assert result.audit.set_index("unique_id").loc[
        "B", "requested_order_quantity"
    ] == 0.0


def test_constraints_still_reject_unknown_order_skus():
    with pytest.raises(ValueError, match="unknown SKUs.*C"):
        OrderingConstraints([
            MaximumOrderQuantity(10.0, mode="adjust"),
        ]).apply(
            _decision(["A", "C"], [5.0, 1.0]),
            _context(_inventory(("A", "B"))),
        )


def test_raise_mode_rejects_maximum_violation():
    constraint = MaximumOrderQuantity(5.0, mode="raise")
    with pytest.raises(ValueError, match="maximum_order_quantity"):
        constraint.apply(_decision(["A"], [7.0]), _context(_inventory()))


def test_per_sku_values_require_exact_identifier_types():
    constraint = MaximumOrderQuantity({1: 5.0}, mode="raise")
    with pytest.raises(ValueError, match="exactly the inventory SKUs"):
        constraint.apply(_decision(["1"], [1.0]), _context(_inventory(("1",))))


def test_custom_constraint_output_is_centrally_revalidated():
    class BrokenConstraint(OrderingConstraint):
        def apply(self, order, context):
            frame = order.get_dataframe()
            frame.loc[0, "order_quantity"] = -1.0
            return ConstraintResult(
                OrderDecision(frame, lead_time=1),
                pd.DataFrame(),
            )

    constraints = OrderingConstraints([BrokenConstraint()])
    with pytest.raises(ValueError, match="non-negative"):
        constraints.apply(_decision(["A"], [2.0]), _context(_inventory()))


def test_constraint_names_and_manifests_are_unambiguous_and_serializable():
    class InvalidManifestConstraint(OrderingConstraint):
        name = "invalid_manifest"

        def apply(self, order, context):
            raise AssertionError("not called")

        def to_manifest(self):
            return {"invalid": object()}

    with pytest.raises(ValueError, match="names must be unique"):
        OrderingConstraints([
            MaximumOrderQuantity(5.0),
            MaximumOrderQuantity(4.0),
        ])
    with pytest.raises(ValueError, match="JSON-serializable"):
        OrderingConstraints([InvalidManifestConstraint()]).to_manifest()


def test_engine_records_constraint_sequence_and_binding_name():
    constraints = OrderingConstraints([
        MaximumOrderQuantity({"A": 5.0}, mode="adjust"),
    ])
    demand = pd.DataFrame({
        "unique_id": ["A"],
        "period": [0],
        "date": [pd.Timestamp("2025-01-02")],
        "y": [0.0],
    })
    result = SimulationEngine().run(
        FixedOrderPolicy(
            7.0, lead_time=1, review_period=1, service_level=0.95,
            allow_backorders=False,
        ),
        demand,
        _inventory(),
        n_periods=1,
        freq="D",
        warmup_periods=0,
        scoring_periods=1,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="constraint_test",
        random_seed=None,
        order_constraints=constraints,
    )

    event = result.to_event_frame().iloc[0]
    assert event["requested_order_quantity"] == 7.0
    assert event["order_quantity"] == 5.0
    assert event["constraint_adjustment_units"] == -2.0
    assert event["binding_constraints"] == "maximum_order_quantity"
    manifest = result.run_manifest["run_settings"]["order_constraints"]
    assert manifest["application_order"] == ["maximum_order_quantity"]


def test_engine_preserves_sparse_order_decisions_with_constraints():
    class SparseFixedOrderPolicy(FixedOrderPolicy):
        def predict(self, inventory_state_df, current_period=0, **kwargs):
            decision = super().predict(
                inventory_state_df,
                current_period=current_period,
                **kwargs,
            )
            return OrderDecision(
                decision.get_dataframe().iloc[[0]],
                sku_column=decision.sku_column,
                lead_time=decision.lead_time,
            )

    demand = pd.DataFrame({
        "unique_id": ["A", "B"],
        "period": [0, 0],
        "date": [pd.Timestamp("2025-01-02")] * 2,
        "y": [0.0, 0.0],
    })
    result = SimulationEngine().run(
        SparseFixedOrderPolicy(
            5.0, lead_time=1, review_period=1, service_level=0.95,
            allow_backorders=False,
        ),
        demand,
        _inventory(("A", "B")),
        n_periods=1,
        freq="D",
        warmup_periods=0,
        scoring_periods=1,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="sparse_constraint_test",
        random_seed=None,
        order_constraints=OrderingConstraints([
            MaximumOrderQuantity(10.0, mode="adjust"),
        ]),
    )

    events = result.to_event_frame().set_index("unique_id")
    assert events.loc["A", "order_quantity"] == 5.0
    assert events.loc["B", "order_quantity"] == 0.0


def _unchanged_audit(frame, name):
    quantity = frame["order_quantity"].to_numpy(dtype=float)
    return pd.DataFrame({
        "unique_id": frame["unique_id"],
        "requested_order_quantity": quantity,
        "constrained_order_quantity": quantity,
        "constraint_adjustment_units": 0.0,
        "constraint_binding_flag": False,
        "capacity_violation_flag": False,
        "binding_constraints": "",
    })


class ReversedRows(OrderingConstraint):
    """Changes no quantity; returns the rows in reverse order."""

    name = "reversed_rows"

    def apply(self, order, context):
        frame = order.get_dataframe()
        reversed_frame = frame.iloc[::-1].reset_index(drop=True)
        return ConstraintResult(
            OrderDecision(reversed_frame, lead_time=order.lead_time),
            _unchanged_audit(frame, self.name),
        )


class PerSkuOrderPolicy(FixedOrderPolicy):
    def __init__(self, quantities, **kwargs):
        super().__init__(0.0, **kwargs)
        self.quantities = quantities

    def predict(self, inventory_state_df, current_period=0, **kwargs):
        decision = super().predict(inventory_state_df, current_period, **kwargs)
        frame = decision.get_dataframe()
        frame["order_quantity"] = frame["unique_id"].map(self.quantities)
        frame["target_level"] = frame["inventory_position"] + frame["order_quantity"]
        return OrderDecision(frame, sku_column=decision.sku_column,
                             lead_time=decision.lead_time)


def _two_sku_run(constraint, quantities=None):
    demand = pd.DataFrame({
        "unique_id": ["A", "B"],
        "period": [0, 0],
        "date": [pd.Timestamp("2025-01-02")] * 2,
        "y": [1.0, 1.0],
    })
    return SimulationEngine().run(
        PerSkuOrderPolicy(
            quantities or {"A": 5.0, "B": 10.0}, lead_time=1, review_period=1,
            service_level=0.95, allow_backorders=True,
        ),
        demand,
        _inventory(("A", "B")),
        freq="D",
        order_constraints=[constraint],
    )


def test_composition_audit_matches_skus_when_a_constraint_reorders_rows():
    result = OrderingConstraints([ReversedRows()]).apply(
        _decision(["A", "B"], [5.0, 10.0]), _context(_inventory(("A", "B")))
    )

    audit = result.audit.set_index("unique_id")
    assert audit.loc["A", "constrained_order_quantity"] == 5.0
    assert audit.loc["B", "constrained_order_quantity"] == 10.0
    assert (audit["constraint_adjustment_units"] == 0.0).all()
    assert not audit["constraint_binding_flag"].any()


def test_engine_event_table_stays_valid_when_a_constraint_reorders_rows():
    from stockcast.evaluation import validate_event_frame

    events = _two_sku_run(ReversedRows()).to_event_frame()

    validate_event_frame(events)
    first = events[events["period"] == 1].set_index("unique_id")
    assert first.loc["A", "order_quantity"] == 5.0
    assert first.loc["B", "order_quantity"] == 10.0
    assert (first["constraint_adjustment_units"] == 0.0).all()


def test_constraint_hooks_see_a_copy_and_cannot_change_the_stock():
    class AddsStock(OrderingConstraint):
        name = "adds_stock"

        def reset(self, context):
            context.inventory.data["on_hand"] += 100.0

        def apply(self, order, context):
            context.inventory.data["on_hand"] += 100.0
            frame = order.get_dataframe()
            return ConstraintResult(order, _unchanged_audit(frame, self.name))

    baseline = _two_sku_run(ReversedRows()).to_event_frame()
    events = _two_sku_run(AddsStock()).to_event_frame()

    columns = ["starting_on_hand", "ending_on_hand", "order_quantity"]
    pd.testing.assert_frame_equal(events[columns], baseline[columns])
    assert (events.loc[events["period"] == 1, "starting_on_hand"] == 0.0).all()


def test_constraint_hooks_see_a_copy_and_cannot_change_the_policy():
    class ChangesPolicy(OrderingConstraint):
        name = "changes_policy"

        def reset(self, context):
            context.policy.service_level = 0.5

        def apply(self, order, context):
            context.policy.quantities = {"A": 0.0, "B": 0.0}
            frame = order.get_dataframe()
            return ConstraintResult(order, _unchanged_audit(frame, self.name))

    policy = PerSkuOrderPolicy(
        {"A": 5.0, "B": 10.0}, lead_time=1, review_period=1,
        service_level=None, allow_backorders=True,
    )
    demand = pd.DataFrame({
        "unique_id": ["A", "B", "A", "B"],
        "period": [0, 0, 1, 1],
        "date": [pd.Timestamp("2025-01-02")] * 2 + [pd.Timestamp("2025-01-03")] * 2,
        "y": [1.0] * 4,
    })
    result = SimulationEngine().run(
        policy, demand, _inventory(("A", "B")), freq="D",
        order_constraints=[ChangesPolicy()],
    )

    assert policy.service_level is None
    assert policy.quantities == {"A": 5.0, "B": 10.0}
    assert result.run_manifest["policy"]["service_level"] is None
    events = result.to_event_frame()
    assert events.loc[events["period"] == 2, "order_quantity"].tolist() == [5.0, 10.0]
