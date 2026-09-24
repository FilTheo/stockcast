import math
from statistics import NormalDist

import pandas as pd
import pytest

from stockcast import PeriodicSchedule
from stockcast.policies import (
    ColumnPeriodicReviewTargets,
    FixedPeriodicReviewTargets,
    OrderUpToPolicy,
    PeriodicReviewPolicy,
    PeriodicReviewTargetProvider,
    PeriodicReviewTargets,
    ReorderPointPolicy,
)

ORIGIN = pd.Timestamp("2025-01-01")


def _normal_forecast():
    return pd.DataFrame({
        "unique_id": ["A", "A"],
        "fh": [1, 2],
        "date": [pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")],
        "mean": [10.0, 20.0],
        "std": [2.0, 3.0],
    })


def _calendar_args():
    return {
        "forecast_origin": ORIGIN,
        "forecast_frequency": "D",
    }


def test_order_up_to_never_invents_missing_uncertainty():
    with pytest.raises(ValueError, match="mean_column and std_column are both required"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            service_level=0.95,
            allow_backorders=False,
        ).fit(
            _normal_forecast(),
            mean_column="mean",
            target_probability=0.95,
            protection_horizon=2,
            aggregation_method="independent_normal",
            forecast_date_column="date",
            **_calendar_args(),
        )


def test_marginal_quantiles_cannot_be_passed_as_direct_targets():
    marginal_quantiles = pd.DataFrame({
        "unique_id": ["A", "A"],
        "fh": [1, 2],
        "up_95": [10.0, 20.0],
        "target_end": [pd.Timestamp("2025-01-03")] * 2,
    })

    with pytest.raises(ValueError, match="exactly one row per SKU"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            service_level=0.95,
            allow_backorders=False,
        ).fit(
            marginal_quantiles,
            target_column="up_95",
            target_probability=0.95,
            protection_horizon=2,
            target_source="external_direct",
            target_end_date_column="target_end",
            **_calendar_args(),
        )


def test_aggregation_labels_are_rejected_for_direct_targets():
    target = pd.DataFrame({
        "unique_id": ["A"],
        "target": [30.0],
        "target_end": [pd.Timestamp("2025-01-03")],
    })
    with pytest.raises(ValueError, match="does not apply to direct targets"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            service_level=0.95,
            allow_backorders=False,
        ).fit(
            target,
            target_column="target",
            target_probability=0.95,
            protection_horizon=2,
            aggregation_method="sum_marginal_quantiles",
            target_source="external_direct",
            target_end_date_column="target_end",
            **_calendar_args(),
        )


def test_service_level_must_match_probability_metadata_and_column_label():
    target = pd.DataFrame({
        "unique_id": ["A"],
        "up_80": [30.0],
        "target_end": [pd.Timestamp("2025-01-03")],
    })
    with pytest.raises(ValueError, match="does not match policy service_level"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            service_level=0.99,
            allow_backorders=False,
        ).fit(
            target,
            target_column="up_80",
            target_probability=0.80,
            protection_horizon=2,
            target_source="external_direct",
            target_end_date_column="target_end",
            **_calendar_args(),
        )

    with pytest.raises(ValueError, match="denotes probability 0.8, not 0.99"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            service_level=0.99,
            allow_backorders=False,
        ).fit(
            target,
            target_column="up_80",
            target_probability=0.99,
            protection_horizon=2,
            target_source="external_direct",
            target_end_date_column="target_end",
            **_calendar_args(),
        )


def test_independent_normal_mode_is_explicit_and_records_provenance():
    policy = OrderUpToPolicy(
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    ).fit(
        _normal_forecast(),
        mean_column="mean",
        std_column="std",
        target_probability=0.95,
        protection_horizon=2,
        aggregation_method="independent_normal",
        forecast_date_column="date",
        **_calendar_args(),
    )

    expected = 30.0 + NormalDist().inv_cdf(0.95) * math.sqrt(13.0)
    assert policy.get_target_levels().loc[0, "target_level"] == pytest.approx(expected)
    assert policy.get_target_metadata() == {
        "representation": "independent_normal_marginals",
        "target_probability": 0.95,
        "protection_horizon": 2,
        "target_source": "built_in_calculation",
        "forecast_origin": "2025-01-01T00:00:00",
        "forecast_frequency": "D",
        "target_end_date": "2025-01-03T00:00:00",
        "calculation_method": "independent_normal",
    }
    decision = policy.predict(
        pd.DataFrame({"unique_id": ["A"], "inventory_position": [0.0]}),
        current_period=0,
        return_dataframe=True,
    )
    assert decision.loc[0, "order_quantity"] == pytest.approx(expected)


@pytest.mark.parametrize("frequency", ["0D", "-1D"])
def test_forecast_frequency_must_advance_time(frequency):
    with pytest.raises(ValueError, match="advance time strictly forward"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            service_level=0.95,
            allow_backorders=False,
        ).fit(
            _normal_forecast(),
            mean_column="mean",
            std_column="std",
            target_probability=0.95,
            protection_horizon=2,
            aggregation_method="independent_normal",
            forecast_date_column="date",
            forecast_origin=ORIGIN,
            forecast_frequency=frequency,
        )


def test_independent_normal_overflow_warns_and_fails_closed():
    forecast = _normal_forecast()
    forecast[["mean", "std"]] = 1e308
    policy = OrderUpToPolicy(
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    )
    with (
        pytest.warns(RuntimeWarning, match="non-finite target"),
        pytest.raises(ValueError, match="target is non-finite"),
    ):
        policy.fit(
            forecast,
            mean_column="mean",
            std_column="std",
            target_probability=0.95,
            protection_horizon=2,
            aggregation_method="independent_normal",
            forecast_date_column="date",
            **_calendar_args(),
        )


@pytest.mark.parametrize("position", [float("inf"), float("-inf"), float("nan")])
def test_policy_rejects_non_finite_plain_inventory_position(position):
    policy = OrderUpToPolicy(
        lead_time=1,
        review_period=1,
        service_level=0.95,
        allow_backorders=False,
    ).fit(
        _normal_forecast(),
        mean_column="mean",
        std_column="std",
        target_probability=0.95,
        protection_horizon=2,
        aggregation_method="independent_normal",
        forecast_date_column="date",
        **_calendar_args(),
    )
    with pytest.raises(ValueError, match="inventory_position must contain finite"):
        policy.predict(
            pd.DataFrame({"unique_id": ["A"], "inventory_position": [position]}),
            current_period=0,
        )


def test_forecast_dates_must_match_origin_frequency_and_horizon():
    forecast = _normal_forecast()
    forecast.loc[forecast["fh"] == 2, "date"] = pd.Timestamp("2025-01-04")

    with pytest.raises(ValueError, match="fh=2 must be 2025-01-03"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            service_level=0.95,
            allow_backorders=False,
        ).fit(
            forecast,
            mean_column="mean",
            std_column="std",
            target_probability=0.95,
            protection_horizon=2,
            aggregation_method="independent_normal",
            forecast_date_column="date",
            **_calendar_args(),
        )


def test_direct_target_end_date_must_match_protection_horizon():
    target = pd.DataFrame({
        "unique_id": ["A"],
        "target": [30.0],
        "target_end": [pd.Timestamp("2025-01-04")],
    })

    with pytest.raises(ValueError, match="must equal 2025-01-03"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            service_level=0.95,
            allow_backorders=False,
        ).fit(
            target,
            target_column="target",
            target_probability=0.95,
            protection_horizon=2,
            target_source="external_direct",
            target_end_date_column="target_end",
            **_calendar_args(),
        )


def test_zero_lead_time_is_supported_and_negative_is_rejected():
    policy = OrderUpToPolicy(lead_time=0, review_period=1, service_level=.95, allow_backorders=False)
    assert policy.lead_time == 0
    with pytest.raises(ValueError, match="lead_time must be an integer >= 0"):
        OrderUpToPolicy(lead_time=-1, review_period=1, service_level=.95, allow_backorders=False)


def test_sq_requires_explicit_quantity_and_provenance():
    with pytest.raises(ValueError, match="order_quantity is required"):
        ReorderPointPolicy(
            lead_time=1,
            review_period=1,
            policy_type="sQ",
            service_level=0.95,
            allow_backorders=False,
        )

    with pytest.raises(ValueError, match="order_quantity_source is required"):
        ReorderPointPolicy(
            lead_time=1,
            review_period=1,
            policy_type="sQ",
            service_level=0.95,
            order_quantity=12,
            allow_backorders=False,
        )


def test_reorder_point_review_timing_is_explicit():
    arguments = dict(
        lead_time=1,
        policy_type="sQ",
        service_level=0.95,
        order_quantity=12,
        order_quantity_source="supplier_case_pack",
        allow_backorders=False,
    )
    with pytest.raises(ValueError, match="supply schedule or review_period"):
        ReorderPointPolicy(**arguments)

    assert ReorderPointPolicy(review_period=1, **arguments).review_period == 1
    weekly = ReorderPointPolicy(schedule=PeriodicSchedule(7, start=2), **arguments)
    assert weekly.schedule.to_manifest() == {"type": "periodic", "every": 7, "start": 2}


def test_sq_records_direct_reorder_target_and_quantity_provenance():
    targets = pd.DataFrame({
        "unique_id": ["A"],
        "reorder_q95": [25.0],
        "reorder_end": [pd.Timestamp("2025-01-04")],
    })
    policy = ReorderPointPolicy(
        lead_time=2,
        review_period=1,
        policy_type="sQ",
        service_level=0.95,
        order_quantity=12,
        order_quantity_source="supplier_case_pack",
        allow_backorders=False,
    ).fit(
        targets,
        reorder_point_column="reorder_q95",
        target_probability=0.95,
        reorder_horizon=3,
        target_source="external_direct",
        reorder_end_date_column="reorder_end",
        **_calendar_args(),
    )

    assert policy.get_parameters().loc[0, "order_quantity"] == 12.0
    assert policy.get_target_metadata()["reorder_horizon"] == 3
    assert policy.get_target_metadata()["order_quantity_source"] == "supplier_case_pack"
    assert policy.get_target_metadata()["target_source"] == "external_direct"
    decision = policy.predict(
        pd.DataFrame({"unique_id": ["A"], "inventory_position": [20.0]}),
        current_period=0,
        return_dataframe=True,
    )
    assert decision.loc[0, "order_quantity"] == 12.0


def test_ss_treats_order_up_to_level_as_a_policy_parameter_not_a_quantile():
    targets = pd.DataFrame({
        "unique_id": ["A"],
        "reorder_q95": [25.0],
        "restore": [40.0],
        "reorder_end": [pd.Timestamp("2025-01-04")],
    })
    policy = ReorderPointPolicy(
        lead_time=2,
        review_period=1,
        policy_type="sS",
        service_level=0.95,
        allow_backorders=False,
    )
    arguments = dict(
        reorder_point_column="reorder_q95",
        target_probability=0.95,
        reorder_horizon=3,
        target_source="external_direct",
        reorder_end_date_column="reorder_end",
        **_calendar_args(),
    )
    with pytest.raises(ValueError, match="order_up_to_column is required"):
        policy.fit(targets, **arguments)
    with pytest.raises(ValueError, match="greater than or equal to reorder points"):
        policy.fit(targets.assign(restore=20.0), order_up_to_column="restore", **arguments)

    policy.fit(targets, order_up_to_column="restore", **arguments)
    assert policy.get_parameters().loc[0, "order_up_to_level"] == 40.0
    metadata = policy.get_target_metadata()
    assert metadata["order_up_to_representation"] == "external_policy_level"
    assert "order_up_to_horizon" not in metadata
    decision = policy.predict(
        pd.DataFrame({"unique_id": ["A"], "inventory_position": [20.0]}),
        current_period=0,
        return_dataframe=True,
    )
    assert decision.loc[0, "order_quantity"] == 20.0


def test_reorder_point_planner_mode_accepts_jointly_chosen_s_and_S():
    targets = pd.DataFrame({
        "unique_id": ["A"],
        "s": [7.0],
        "S": [30.0],
        "end": [pd.Timestamp("2025-01-08")],
    })
    policy = ReorderPointPolicy(
        lead_time=0,
        schedule=PeriodicSchedule(7),
        policy_type="sS",
        allow_backorders=True,
    )
    arguments = dict(
        reorder_point_column="s",
        order_up_to_column="S",
        reorder_end_date_column="end",
        reorder_horizon=7,
        target_source="external_direct",
        **_calendar_args(),
    )
    with pytest.raises(ValueError, match="set service_level"):
        policy.fit(targets, target_probability=0.9, **arguments)
    with pytest.raises(ValueError, match="quantile-labelled"):
        policy.fit(targets.rename(columns={"s": "q90"}), **{**arguments, "reorder_point_column": "q90"})

    policy.fit(targets, **arguments)
    metadata = policy.get_target_metadata()
    assert metadata["representation"] == "external_reorder_point"
    assert metadata["target_probability"] is None
    assert metadata["reorder_horizon"] == 7


def test_periodic_rss_uses_only_explicit_reorder_and_restore_targets():
    targets = pd.DataFrame({
        "unique_id": ["A"],
        "reorder": [10.0],
        "restore": [20.0],
    })
    policy = PeriodicReviewPolicy(
        lead_time=1,
        review_period=2,
        service_level=0.95,
        allow_backorders=False,
    ).fit(
        targets,
        target_provider=ColumnPeriodicReviewTargets(
            reorder_point_column="reorder",
            order_up_to_column="restore",
        ),
        information_origin=ORIGIN,
        information_frequency="D",
    )

    order = policy.predict(
        pd.DataFrame({"unique_id": ["A"], "inventory_position": [8.0]}),
        current_period=0,
        return_dataframe=True,
    )
    no_order = policy.predict(
        pd.DataFrame({"unique_id": ["A"], "inventory_position": [15.0]}),
        current_period=0,
        return_dataframe=True,
    )
    assert order.loc[0, "order_quantity"] == 12.0
    assert no_order.loc[0, "order_quantity"] == 0.0
    metadata = policy.get_target_metadata()
    assert metadata["target_source"] == "external_direct"
    assert "reorder_horizon" not in metadata


def test_periodic_fixed_provider_supports_scalar_targets():
    policy = PeriodicReviewPolicy(
        lead_time=1,
        review_period=2,
        service_level=0.95,
        allow_backorders=False,
    ).fit(
        pd.DataFrame({"unique_id": ["A", "B"]}),
        target_provider=FixedPeriodicReviewTargets(
            reorder_point=5.0,
            order_up_to_level=12.0,
        ),
        information_origin=ORIGIN,
        information_frequency="D",
    )
    assert policy.get_parameters()["order_up_to_level"].tolist() == [12.0, 12.0]


def test_custom_periodic_provider_is_revalidated_centrally():
    class BrokenProvider(PeriodicReviewTargetProvider):
        def provide(self, target_data, *, sku_column):
            return PeriodicReviewTargets(pd.DataFrame({
                sku_column: ["A"],
                "reorder_point": [10.0],
                "order_up_to_level": [5.0],
            }))

    with pytest.raises(ValueError, match="order-up-to levels"):
        PeriodicReviewPolicy(
            lead_time=1,
            review_period=2,
            service_level=0.95,
            allow_backorders=False,
        ).fit(
            pd.DataFrame({"unique_id": ["A"]}),
            target_provider=BrokenProvider(),
            information_origin=ORIGIN,
            information_frequency="D",
        )


def test_custom_periodic_provider_metadata_must_be_serializable():
    class InvalidMetadataProvider(PeriodicReviewTargetProvider):
        def provide(self, target_data, *, sku_column):
            return PeriodicReviewTargets(
                pd.DataFrame({
                    sku_column: ["A"],
                    "reorder_point": [5.0],
                    "order_up_to_level": [10.0],
                }),
                metadata={"invalid": object()},
            )

    with pytest.raises(ValueError, match="JSON-serializable"):
        PeriodicReviewPolicy(
            lead_time=1,
            review_period=2,
            service_level=0.95,
            allow_backorders=False,
        ).fit(
            pd.DataFrame({"unique_id": ["A"]}),
            target_provider=InvalidMetadataProvider(),
            information_origin=ORIGIN,
            information_frequency="D",
        )
