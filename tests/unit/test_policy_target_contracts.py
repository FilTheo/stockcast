import math
from statistics import NormalDist

import pandas as pd
import pytest

from stockcast import PeriodicSchedule
from stockcast.policies import (
    OrderUpToPolicy,
    ReorderPointPolicy,
    ReorderPointTargetProvider,
    ReorderPointTargets,
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
    }


def test_order_up_to_never_invents_missing_uncertainty():
    with pytest.raises(ValueError, match="mean_column and std_column are both required"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            freq="D",
            service_level=0.95,
            allow_backorders=False,
            date_column="date",
        ).fit(
            _normal_forecast(),
            mean_column="mean",
            target_probability=0.95,
            protection_horizon=2,
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
            freq="D",
            service_level=0.95,
            allow_backorders=False,
            date_column="target_end",
        ).fit(
            marginal_quantiles,
            target_column="up_95",
            target_probability=0.95,
            protection_horizon=2,
            **_calendar_args(),
        )


def test_marginal_quantile_summation_has_no_entry_point():
    target = pd.DataFrame({
        "unique_id": ["A"],
        "target": [30.0],
        "target_end": [pd.Timestamp("2025-01-03")],
    })
    with pytest.raises(TypeError, match="aggregation_method"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            freq="D",
            service_level=0.95,
            allow_backorders=False,
            date_column="target_end",
        ).fit(
            target,
            target_column="target",
            target_probability=0.95,
            protection_horizon=2,
            aggregation_method="sum_marginal_quantiles",
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
            freq="D",
            service_level=0.99,
            allow_backorders=False,
            date_column="target_end",
        ).fit(
            target,
            target_column="up_80",
            target_probability=0.80,
            protection_horizon=2,
            **_calendar_args(),
        )

    with pytest.raises(ValueError, match="denotes probability 0.8, not 0.99"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            freq="D",
            service_level=0.99,
            allow_backorders=False,
            date_column="target_end",
        ).fit(
            target,
            target_column="up_80",
            target_probability=0.99,
            protection_horizon=2,
            **_calendar_args(),
        )


def test_independent_normal_mode_is_explicit_and_records_provenance():
    policy = OrderUpToPolicy(
        lead_time=1,
        review_period=1,
        freq="D",
        service_level=0.95,
        allow_backorders=False,
        date_column="date",
    ).fit(
        _normal_forecast(),
        mean_column="mean",
        std_column="std",
        target_probability=0.95,
        protection_horizon=2,
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
    ).get_dataframe()
    assert decision.loc[0, "order_quantity"] == pytest.approx(expected)


@pytest.mark.parametrize("frequency", ["0D", "-1D"])
def test_forecast_frequency_must_advance_time(frequency):
    with pytest.raises(ValueError, match="advance time strictly forward"):
        OrderUpToPolicy(
            lead_time=1,
            review_period=1,
            freq=frequency,
            service_level=0.95,
            allow_backorders=False,
            date_column="date",
        ).fit(
            _normal_forecast(),
            mean_column="mean",
            std_column="std",
            target_probability=0.95,
            protection_horizon=2,
            forecast_origin=ORIGIN,
        )


def test_independent_normal_overflow_warns_and_fails_closed():
    forecast = _normal_forecast()
    forecast[["mean", "std"]] = 1e308
    policy = OrderUpToPolicy(
        lead_time=1,
        review_period=1,
        freq="D",
        service_level=0.95,
        allow_backorders=False,
        date_column="date",
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
            **_calendar_args(),
        )


@pytest.mark.parametrize("position", [float("inf"), float("-inf"), float("nan")])
def test_policy_rejects_non_finite_plain_inventory_position(position):
    policy = OrderUpToPolicy(
        lead_time=1,
        review_period=1,
        freq="D",
        service_level=0.95,
        allow_backorders=False,
        date_column="date",
    ).fit(
        _normal_forecast(),
        mean_column="mean",
        std_column="std",
        target_probability=0.95,
        protection_horizon=2,
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
            freq="D",
            service_level=0.95,
            allow_backorders=False,
            date_column="date",
        ).fit(
            forecast,
            mean_column="mean",
            std_column="std",
            target_probability=0.95,
            protection_horizon=2,
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
            freq="D",
            service_level=0.95,
            allow_backorders=False,
            date_column="target_end",
        ).fit(
            target,
            target_column="target",
            target_probability=0.95,
            protection_horizon=2,
            **_calendar_args(),
        )


def test_zero_lead_time_is_supported_and_negative_is_rejected():
    policy = OrderUpToPolicy(lead_time=0, review_period=1, service_level=.95, allow_backorders=False)
    assert policy.lead_time == 0
    with pytest.raises(ValueError, match="lead_time must be an integer >= 0"):
        OrderUpToPolicy(lead_time=-1, review_period=1, service_level=.95, allow_backorders=False)


def test_sq_requires_explicit_quantity():
    with pytest.raises(ValueError, match="order_quantity is required"):
        ReorderPointPolicy(
            lead_time=1,
            review_period=1,
            policy_type="sQ",
            service_level=0.95,
            allow_backorders=False,
        )
    with pytest.raises(TypeError, match="order_quantity_source"):
        ReorderPointPolicy(
            lead_time=1,
            review_period=1,
            policy_type="sQ",
            service_level=0.95,
            order_quantity=12,
            order_quantity_source="supplier_case_pack",
            allow_backorders=False,
        )


def test_reorder_point_review_timing_is_explicit():
    arguments = dict(
        lead_time=1,
        policy_type="sQ",
        service_level=0.95,
        order_quantity=12,
        allow_backorders=False,
    )
    with pytest.raises(ValueError, match="give review_period or schedule"):
        ReorderPointPolicy(**arguments)

    assert ReorderPointPolicy(review_period=1, **arguments).review_period == 1
    weekly = ReorderPointPolicy(schedule=PeriodicSchedule(7, start=2), **arguments)
    assert weekly.schedule.to_manifest() == {"type": "periodic", "every": 7, "start": 2}


def test_sq_records_direct_reorder_target_and_quantity():
    targets = pd.DataFrame({
        "unique_id": ["A"],
        "reorder_q95": [25.0],
        "reorder_end": [pd.Timestamp("2025-01-04")],
    })
    policy = ReorderPointPolicy(
        lead_time=2,
        review_period=1,
        freq="D",
        policy_type="sQ",
        service_level=0.95,
        order_quantity=12,
        allow_backorders=False,
        date_column="reorder_end",
    ).fit(
        targets,
        reorder_point_column="reorder_q95",
        target_probability=0.95,
        reorder_horizon=3,
        **_calendar_args(),
    )

    assert policy.get_parameters().loc[0, "order_quantity"] == 12.0
    assert policy.get_target_metadata()["reorder_horizon"] == 3
    assert "order_quantity_source" not in policy.get_target_metadata()
    assert policy.get_target_metadata()["target_source"] == "external_direct"
    decision = policy.predict(
        pd.DataFrame({"unique_id": ["A"], "inventory_position": [20.0]}),
        current_period=0,
    ).get_dataframe()
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
        freq="D",
        policy_type="sS",
        service_level=0.95,
        allow_backorders=False,
        date_column="reorder_end",
    )
    arguments = dict(
        reorder_point_column="reorder_q95",
        target_probability=0.95,
        reorder_horizon=3,
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
    ).get_dataframe()
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
        freq="D",
        policy_type="sS",
        allow_backorders=True,
        date_column="end",
    )
    arguments = dict(
        reorder_point_column="s",
        order_up_to_column="S",
        reorder_horizon=7,
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


def test_fixed_rss_values_from_table_columns_order_only_below_s():
    targets = pd.DataFrame({
        "unique_id": ["A"],
        "reorder": [10.0],
        "restore": [20.0],
    })
    policy = ReorderPointPolicy(
        lead_time=1,
        review_period=2,
        freq="D",
        allow_backorders=False,
    ).fit(
        reorder_point=targets.set_index("unique_id")["reorder"],
        order_up_to_level=targets.set_index("unique_id")["restore"],
        forecast_origin=ORIGIN,
    )

    order = policy.predict(
        pd.DataFrame({"unique_id": ["A"], "inventory_position": [8.0]}),
        current_period=0,
    ).get_dataframe()
    no_order = policy.predict(
        pd.DataFrame({"unique_id": ["A"], "inventory_position": [15.0]}),
        current_period=0,
    ).get_dataframe()
    assert order.loc[0, "order_quantity"] == 12.0
    assert no_order.loc[0, "order_quantity"] == 0.0
    metadata = policy.get_target_metadata()
    assert metadata["target_source"] == "external_direct"
    assert "reorder_horizon" not in metadata


def test_fixed_scalar_values_cover_the_given_skus():
    policy = ReorderPointPolicy(
        lead_time=1,
        review_period=2,
        freq="D",
        allow_backorders=False,
    ).fit(
        pd.DataFrame({"unique_id": ["A", "B"]}),
        reorder_point=5.0,
        order_up_to_level=12.0,
        forecast_origin=ORIGIN,
    )
    assert policy.get_parameters()["order_up_to_level"].tolist() == [12.0, 12.0]
    assert policy.get_target_metadata()["representation"] == "fixed_policy_levels"


def test_custom_target_provider_is_revalidated_centrally():
    class BrokenProvider(ReorderPointTargetProvider):
        def provide(self, target_data, *, sku_column):
            return ReorderPointTargets(pd.DataFrame({
                sku_column: ["A"],
                "reorder_point": [10.0],
                "order_up_to_level": [5.0],
            }))

    with pytest.raises(ValueError, match="order-up-to levels"):
        ReorderPointPolicy(
            lead_time=1,
            review_period=2,
            freq="D",
            allow_backorders=False,
        ).fit(
            pd.DataFrame({"unique_id": ["A"]}),
            target_provider=BrokenProvider(),
            forecast_origin=ORIGIN,
        )


def test_custom_target_provider_metadata_must_be_serializable():
    class InvalidMetadataProvider(ReorderPointTargetProvider):
        def provide(self, target_data, *, sku_column):
            return ReorderPointTargets(
                pd.DataFrame({
                    sku_column: ["A"],
                    "reorder_point": [5.0],
                    "order_up_to_level": [10.0],
                }),
                metadata={"invalid": object()},
            )

    with pytest.raises(ValueError, match="JSON-serializable"):
        ReorderPointPolicy(
            lead_time=1,
            review_period=2,
            freq="D",
            allow_backorders=False,
        ).fit(
            pd.DataFrame({"unique_id": ["A"]}),
            target_provider=InvalidMetadataProvider(),
            forecast_origin=ORIGIN,
        )


# Inferred fit inputs: values the policy already knows are not repeated.

def _direct_target(end=pd.Timestamp("2025-01-03"), column="S"):
    return pd.DataFrame({"unique_id": ["A"], column: [30.0], "end": [end]})


def test_direct_fit_infers_probability_horizon_and_end_date():
    policy = OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False)
    minimal = policy.fit(
        _direct_target(), target_column="S", **_calendar_args(),
    ).get_target_metadata()
    explicit = OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False, date_column="end").fit(
        _direct_target(),
        target_column="S",
        target_probability=0.95,
        protection_horizon=2,
        **_calendar_args(),
    ).get_target_metadata()
    assert minimal == explicit
    assert minimal["target_probability"] == 0.95
    assert minimal["protection_horizon"] == 2
    assert minimal["target_source"] == "external_direct"
    assert minimal["target_end_date"] == "2025-01-03T00:00:00"


def test_direct_fit_reads_the_origin_from_the_end_date_column():
    policy = OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False, date_column="end").fit(
        _direct_target(), target_column="S",
    )
    assert policy.get_target_metadata()["forecast_origin"] == ORIGIN.isoformat()


def test_direct_fit_needs_the_origin_or_the_end_date():
    policy = OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False)
    with pytest.raises(ValueError, match="give forecast_origin, or a date column"):
        policy.fit(_direct_target(), target_column="S")
    dated = OrderUpToPolicy(
        1, 1, freq="D", service_level=0.95, allow_backorders=False,
        date_column="end",
    )
    with pytest.raises(ValueError, match="one end date for every SKU"):
        dated.fit(
            pd.DataFrame({
                "unique_id": ["A", "B"],
                "S": [30.0, 20.0],
                "end": [pd.Timestamp("2025-01-03"), pd.Timestamp("2025-01-04")],
            }),
            target_column="S",
        )
    weekly = OrderUpToPolicy(1, 1, freq="W-MON", service_level=0.95, allow_backorders=False, date_column="end")
    with pytest.raises(ValueError, match="not on the W-MON period grid"):
        weekly.fit(
            _direct_target(end=pd.Timestamp("2025-01-08")),  # a Wednesday
            target_column="S",
        )


def test_inferred_values_are_still_checked_when_given():
    policy = OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False)
    with pytest.raises(ValueError, match="does not match policy service_level"):
        policy.fit(_direct_target(), target_column="S", target_probability=0.9,
                   **_calendar_args())
    with pytest.raises(ValueError, match="policy horizon 2"):
        policy.fit(_direct_target(), target_column="S", protection_horizon=3,
                   **_calendar_args())
    dated = OrderUpToPolicy(
        1, 1, freq="D", service_level=0.95, allow_backorders=False,
        date_column="end",
    )
    with pytest.raises(ValueError, match="must equal 2025-01-03"):
        dated.fit(_direct_target(end=pd.Timestamp("2025-01-04")), target_column="S",
                  **_calendar_args())
    # A quantile label is still read against the implied probability.
    with pytest.raises(ValueError, match="denotes probability 0.9"):
        policy.fit(_direct_target(column="q90"), target_column="q90", **_calendar_args())


def test_nonperiodic_schedules_still_require_an_explicit_horizon():
    from stockcast import ExplicitSchedule

    policy = OrderUpToPolicy(
        1, freq="D", schedule=ExplicitSchedule([0, 3]), service_level=0.95, allow_backorders=False,
    )
    with pytest.raises(ValueError, match="protection_horizon is required for a nonperiodic"):
        policy.fit(_direct_target(), target_column="S", **_calendar_args())


def test_mean_std_fit_reads_the_origin_from_forecast_dates():
    with_origin = OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False, date_column="date").fit(
        _normal_forecast(), mean_column="mean", std_column="std",
        **_calendar_args(),
    )
    without_origin = OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False, date_column="date").fit(
        _normal_forecast(), mean_column="mean", std_column="std",
    )
    assert with_origin.get_target_metadata() == without_origin.get_target_metadata()
    assert without_origin.get_target_metadata()["calculation_method"] == "independent_normal"
    pd.testing.assert_frame_equal(
        with_origin.get_target_levels(), without_origin.get_target_levels(),
    )


def test_dropped_single_value_arguments_are_not_accepted():
    policy = OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False)
    with pytest.raises(TypeError, match="target_source"):
        policy.fit(_direct_target(), target_column="S", target_source="external_direct",
                   **_calendar_args())


def test_reorder_point_fit_infers_horizon_probability_and_end_date():
    def policy(**columns):
        return ReorderPointPolicy(
            2, 1, freq="D", policy_type="sQ", service_level=0.95, order_quantity=12,
            allow_backorders=False, **columns,
        )

    table = pd.DataFrame({"unique_id": ["A"], "s": [25.0], "end": [pd.Timestamp("2025-01-04")]})
    minimal = policy().fit(table, reorder_point_column="s", **_calendar_args())
    from_end = policy(date_column="end").fit(table, reorder_point_column="s")
    assert minimal.get_target_metadata() == from_end.get_target_metadata()
    assert minimal.get_target_metadata()["reorder_horizon"] == 3
    assert minimal.get_target_metadata()["target_probability"] == 0.95
    with pytest.raises(ValueError, match="give forecast_origin, or a date column"):
        policy().fit(table, reorder_point_column="s")


def test_single_order_fit_reads_the_origin_from_the_season_end():
    from stockcast.policies import SingleOrderPolicy

    def policy(**columns):
        return SingleOrderPolicy(2, freq="D", selling_horizon=3, service_level=0.75,
                                 allow_backorders=False, **columns)

    table = pd.DataFrame({"unique_id": ["A"], "t": [20.0], "end": [ORIGIN + pd.Timedelta(days=5)]})
    from_origin = policy().fit(table, target_column="t", **_calendar_args())
    from_end = policy(date_column="end").fit(table, target_column="t")
    assert from_origin.get_target_metadata() == from_end.get_target_metadata()
    assert from_end.get_target_metadata()["forecast_origin"] == ORIGIN.isoformat()


def test_reorder_point_policy_type_follows_from_the_order_quantity():
    fixed = ReorderPointPolicy(1, 1, order_quantity=12, allow_backorders=False)
    up_to = ReorderPointPolicy(1, 1, allow_backorders=False)
    assert (fixed.policy_type, up_to.policy_type) == ("sQ", "sS")
    assert fixed.policy_name == "Reorder Point (sQ)"
    with pytest.raises(ValueError, match="order_quantity applies only to an"):
        ReorderPointPolicy(1, 1, policy_type="sS", order_quantity=12, allow_backorders=False)
    with pytest.raises(ValueError, match="order_quantity is required"):
        ReorderPointPolicy(1, 1, policy_type="sQ", allow_backorders=False)


def _positions(values):
    return pd.DataFrame({
        "unique_id": list(values),
        "inventory_position": list(values.values()),
    })


def test_undated_scalar_levels_apply_to_every_sku_the_run_sees():
    policy = ReorderPointPolicy(lead_time=1, review_period=1, allow_backorders=False).fit(
        reorder_point=5.0, order_up_to_level=12.0,
    )
    orders = policy.predict(_positions({"A": 5.0, "B": 6.0, "C": -1.0}), current_period=0)
    assert orders.get_dataframe()["order_quantity"].tolist() == [7.0, 0.0, 13.0]
    metadata = policy.get_target_metadata()
    assert metadata["forecast_origin"] is None
    assert metadata["forecast_frequency"] is None
    assert metadata["representation"] == "fixed_policy_levels"
    with pytest.raises(ValueError, match="one value for every SKU.*pass target_df"):
        policy.get_parameters()


def test_fixed_levels_accept_dict_or_series_by_sku():
    by_dict = ReorderPointPolicy(lead_time=1, review_period=2, allow_backorders=True).fit(
        reorder_point={"A": 3.0, "B": 8.0}, order_up_to_level={"A": 10.0, "B": 20.0},
    )
    by_series = ReorderPointPolicy(lead_time=1, review_period=2, allow_backorders=True).fit(
        reorder_point=pd.Series({"A": 3.0, "B": 8.0}),
        order_up_to_level=pd.Series({"A": 10.0, "B": 20.0}),
    )
    for policy in (by_dict, by_series):
        pd.testing.assert_frame_equal(policy.get_parameters(), pd.DataFrame({
            "unique_id": ["A", "B"],
            "reorder_point": [3.0, 8.0],
            "order_up_to_level": [10.0, 20.0],
        }))
    with pytest.raises(ValueError, match=r"No reorder_point was fitted for SKUs: \['C'\]"):
        by_dict.predict(_positions({"A": 0.0, "C": 0.0}), current_period=0)


def test_fixed_sq_levels_order_the_fixed_quantity():
    policy = ReorderPointPolicy(
        lead_time=0, review_period=1, order_quantity=4.0, allow_backorders=False,
    ).fit(reorder_point={"A": 2.0, "B": 2.0})
    orders = policy.predict(_positions({"A": 2.0, "B": 2.5}), current_period=3)
    assert orders.get_dataframe()["order_quantity"].tolist() == [4.0, 0.0]
    assert "order_up_to_representation" not in policy.get_target_metadata()


@pytest.mark.parametrize(
    "fit_kwargs, message",
    [
        ({}, "choose exactly one target source"),
        (
            {"reorder_point": 1.0, "reorder_point_column": "s", "order_up_to_level": 2.0},
            "choose exactly one target source",
        ),
        ({"reorder_point": 1.0}, "order_up_to_level is required"),
        ({"reorder_point": 5.0, "order_up_to_level": 4.0}, "greater than or equal"),
        ({"reorder_point": -1.0, "order_up_to_level": 4.0}, "finite number >= 0"),
        ({"reorder_point": True, "order_up_to_level": 4.0}, "finite number >= 0"),
        (
            {"reorder_point": {"A": 1.0}, "order_up_to_level": {"B": 4.0}},
            "must name the same SKUs",
        ),
        (
            {"reorder_point": 1.0, "order_up_to_level": 4.0, "reorder_horizon": 2},
            "reorder_horizon applies only to reorder_point_column",
        ),
        (
            {"reorder_point": 1.0, "order_up_to_level": 4.0, "forecast_origin": ORIGIN},
            "freq is required: forecast_origin dates the levels",
        ),
        (
            {
                "target_df": pd.DataFrame({"unique_id": ["A"]}),
                "reorder_point": {"B": 1.0}, "order_up_to_level": {"B": 4.0},
            },
            "exactly the SKUs of target_df",
        ),
    ],
)
def test_fixed_levels_reject_ambiguous_or_invalid_inputs(fit_kwargs, message):
    policy = ReorderPointPolicy(lead_time=1, review_period=1, allow_backorders=False)
    with pytest.raises(ValueError, match=message):
        policy.fit(**fit_kwargs)


def test_fixed_levels_and_providers_need_service_level_none():
    policy = ReorderPointPolicy(
        lead_time=1, review_period=1, service_level=0.9, allow_backorders=False,
    )
    with pytest.raises(ValueError, match="service_level=None"):
        policy.fit(reorder_point=1.0, order_up_to_level=4.0)


def test_sq_rejects_an_order_up_to_level():
    policy = ReorderPointPolicy(
        lead_time=1, review_period=1, order_quantity=3.0, allow_backorders=False,
    )
    with pytest.raises(ValueError, match="applies only to an \\(s,S\\) policy"):
        policy.fit(reorder_point=1.0, order_up_to_level=4.0)


class _ScaledProvider(ReorderPointTargetProvider):
    def __init__(self, with_S=True):
        self.with_S = with_S

    def provide(self, target_data, *, sku_column):
        frame = target_data[[sku_column]].copy()
        frame["reorder_point"] = target_data["rate"] * 2.0
        if self.with_S:
            frame["order_up_to_level"] = target_data["rate"] * 5.0
        return ReorderPointTargets(frame=frame, metadata={"multiplier": 2.0})


def test_target_provider_sets_levels_and_records_its_manifest():
    rates = pd.DataFrame({"unique_id": ["A", "B"], "rate": [4.0, 1.0]})
    policy = ReorderPointPolicy(lead_time=1, review_period=1, allow_backorders=False).fit(
        rates, target_provider=_ScaledProvider(),
    )
    assert policy.get_parameters()["order_up_to_level"].tolist() == [20.0, 5.0]
    metadata = policy.get_target_metadata()
    assert metadata["representation"] == "provider_policy_levels"
    assert metadata["target_source"] == "custom_provider"
    assert metadata["provider"]["provider_class"] == "_ScaledProvider"
    assert metadata["provider_metadata"] == {"multiplier": 2.0}

    sq = ReorderPointPolicy(
        lead_time=1, review_period=1, order_quantity=6.0, allow_backorders=False,
    ).fit(rates, target_provider=_ScaledProvider(with_S=False))
    assert sq.get_parameters()["order_quantity"].tolist() == [6.0, 6.0]
    with pytest.raises(ValueError, match="must not return order_up_to_level"):
        ReorderPointPolicy(
            lead_time=1, review_period=1, order_quantity=6.0, allow_backorders=False,
        ).fit(rates, target_provider=_ScaledProvider())
    with pytest.raises(TypeError, match="must be a ReorderPointTargetProvider"):
        ReorderPointPolicy(lead_time=1, review_period=1, allow_backorders=False).fit(
            rates, target_provider=object(),
        )


# freq on the constructor; read from per-step forecast dates only when exact (93.21).

def _steps(dates, skus=("A",)):
    dates = list(pd.DatetimeIndex(dates))
    return pd.DataFrame([
        {"unique_id": sku, "fh": step, "date": date, "mean": 5.0, "std": 1.0}
        for sku in skus
        for step, date in enumerate(dates, start=1)
    ])


def _normal_fit(frame, freq=None, lead_time=2, **kwargs):
    return OrderUpToPolicy(lead_time, 1, freq=freq, service_level=0.9, allow_backorders=False, date_column="date").fit(
        frame, mean_column="mean", std_column="std", **kwargs,
    )


@pytest.mark.parametrize("frequency, first", [
    ("D", "2025-01-02"),
    ("W-MON", "2025-01-06"),
    ("MS", "2025-02-01"),
    ("2D", "2025-01-03"),
])
def test_step_forecast_dates_give_the_frequency(frequency, first):
    frame = _steps(pd.date_range(first, periods=4, freq=frequency), skus=("A", "B"))
    inferred = _normal_fit(frame)
    given = _normal_fit(frame, freq=frequency)
    assert inferred.get_target_metadata() == given.get_target_metadata()
    assert inferred.get_target_metadata()["forecast_frequency"] == given._freq_offset.freqstr
    pd.testing.assert_frame_equal(inferred.get_target_levels(), given.get_target_levels())
    assert inferred.freq is None


@pytest.mark.parametrize("dates, message", [
    (pd.bdate_range("2025-01-02", periods=4), r"follow 'B', which is not a calendar"),
    (pd.date_range("2025-01-02", periods=2, freq="D"), "fewer than three forecast dates"),
    (pd.DatetimeIndex(["2025-01-02", "2025-01-03", "2025-01-05"]), "not evenly spaced"),
])
def test_step_forecast_dates_that_do_not_fix_one_calendar_need_freq(dates, message):
    with pytest.raises(ValueError, match=f"freq is required: .*{message}"):
        OrderUpToPolicy(1, 1, service_level=0.9, allow_backorders=False, date_column="date").fit(
            _steps(dates), mean_column="mean", std_column="std",
        )


def test_skus_with_different_step_frequencies_need_freq():
    frame = pd.concat([
        _steps(pd.date_range("2025-01-02", periods=3, freq="D"), skus=("A",)),
        _steps(pd.date_range("2025-01-03", periods=3, freq="2D"), skus=("B",)),
    ])
    with pytest.raises(ValueError, match=r"freq is required: .*different frequencies \['2D', 'D'\]"):
        _normal_fit(frame)


def test_one_row_targets_need_freq():
    from stockcast.policies import SingleOrderPolicy

    with pytest.raises(ValueError, match="freq is required: a direct target"):
        OrderUpToPolicy(1, 1, service_level=0.95, allow_backorders=False).fit(
            _direct_target(), target_column="S", **_calendar_args(),
        )
    with pytest.raises(ValueError, match="freq is required: reorder_point_column targets"):
        ReorderPointPolicy(1, 1, allow_backorders=False).fit(
            pd.DataFrame({"unique_id": ["A"], "s": [3.0], "S": [9.0]}),
            reorder_point_column="s", order_up_to_column="S", **_calendar_args(),
        )
    with pytest.raises(ValueError, match="freq is required: a season target"):
        SingleOrderPolicy(0, selling_horizon=2, allow_backorders=False).fit(
            pd.DataFrame({"unique_id": ["A"], "S": [9.0]}), target_column="S",
            **_calendar_args(),
        )


def test_given_freq_is_checked_against_the_step_dates():
    frame = _steps(pd.date_range("2025-01-02", periods=3, freq="D"))
    with pytest.raises(ValueError, match="forecast date for SKU A fh=2 must be"):
        _normal_fit(frame, freq="2D")


def test_freq_is_validated_by_the_constructor():
    with pytest.raises(ValueError, match="invalid freq 'fortnightly'"):
        ReorderPointPolicy(1, 1, freq="fortnightly", allow_backorders=False)
    with pytest.raises(TypeError, match="forecast_frequency"):
        OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False).fit(
            _direct_target(), target_column="S", forecast_frequency="D", **_calendar_args(),
        )


def test_fixed_levels_may_record_a_frequency_without_an_origin():
    policy = ReorderPointPolicy(1, 1, freq="W-MON", allow_backorders=False).fit(
        reorder_point=2.0, order_up_to_level=6.0,
    )
    metadata = policy.get_target_metadata()
    assert (metadata["forecast_origin"], metadata["forecast_frequency"]) == (None, "W-MON")
    with pytest.raises(ValueError, match="freq is required: forecast_origin dates the levels"):
        ReorderPointPolicy(1, 1, allow_backorders=False).fit(
            reorder_point=2.0, order_up_to_level=6.0, forecast_origin=ORIGIN,
        )


# One date_column: the date of the last demand period each row covers (93.22).

def test_one_row_targets_take_the_origin_from_the_date_column():
    from stockcast.policies import SingleOrderPolicy

    end = pd.Timestamp("2025-01-03")
    dated = pd.DataFrame({"unique_id": ["A", "B"], "S": [30.0, 20.0], "date": [end, end]})

    def order_up_to():
        return OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False)

    from_date = order_up_to().fit(dated, target_column="S")
    from_origin = order_up_to().fit(dated.drop(columns="date"), target_column="S", **_calendar_args())
    both = order_up_to().fit(dated, target_column="S", **_calendar_args())
    assert from_date.get_target_metadata() == from_origin.get_target_metadata()
    assert both.get_target_metadata() == from_origin.get_target_metadata()
    assert from_date._forecast_origin_column == "date"
    assert both._forecast_origin_column is None

    reorder = ReorderPointPolicy(2, 1, freq="D", policy_type="sS", allow_backorders=False).fit(
        pd.DataFrame({"unique_id": ["A"], "s": [4.0], "S": [9.0],
                      "date": [pd.Timestamp("2025-01-04")]}),
        reorder_point_column="s", order_up_to_column="S",
    )
    assert reorder.get_target_metadata()["forecast_origin"] == ORIGIN.isoformat()

    season = SingleOrderPolicy(2, freq="D", selling_horizon=3, allow_backorders=False).fit(
        pd.DataFrame({"unique_id": ["A"], "t": [20.0], "date": [ORIGIN + pd.Timedelta(days=5)]}),
        target_column="t",
    )
    assert season.get_target_metadata()["forecast_origin"] == ORIGIN.isoformat()
    assert season.get_target_metadata()["target_end_date"] == "2025-01-06T00:00:00"


def test_step_forecasts_take_the_origin_from_their_dates():
    with_origin = _normal_fit(_normal_forecast(), lead_time=1, freq="D", **_calendar_args())
    from_dates = _normal_fit(_normal_forecast(), lead_time=1, freq="D")
    assert from_dates.get_target_metadata() == with_origin.get_target_metadata()
    assert from_dates._forecast_origin_column == "date"


def test_given_origin_and_date_column_must_agree():
    policy = OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False)
    with pytest.raises(ValueError, match=r"target_df.date must equal 2025-01-03 00:00:00"):
        policy.fit(
            pd.DataFrame({"unique_id": ["A"], "S": [30.0], "date": [pd.Timestamp("2025-01-04")]}),
            target_column="S", **_calendar_args(),
        )
    with pytest.raises(ValueError, match="forecast date for SKU A fh=1 must be 2025-01-03"):
        _normal_fit(_normal_forecast(), lead_time=1, freq="D", forecast_origin=ORIGIN + pd.Timedelta(days=1))


def test_without_an_origin_or_a_date_column_fit_asks_for_one():
    message = r"give forecast_origin, or a date column \(the policy's date_column\)"
    with pytest.raises(ValueError, match=message):
        OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False).fit(
            pd.DataFrame({"unique_id": ["A"], "S": [30.0]}), target_column="S",
        )
    with pytest.raises(ValueError, match=message):
        _normal_fit(_normal_forecast().drop(columns="date"), lead_time=1, freq="D")
    with pytest.raises(ValueError, match="date column 'end' not found in target_df"):
        OrderUpToPolicy(1, 1, freq="D", service_level=0.95, allow_backorders=False, date_column="end").fit(
            pd.DataFrame({"unique_id": ["A"], "S": [30.0]}), target_column="S",
            **_calendar_args(),
        )


def test_step_numbers_follow_from_the_dates_when_the_origin_is_known():
    with_fh = _normal_fit(_normal_forecast(), lead_time=1, freq="D", **_calendar_args())
    without_fh = _normal_fit(_normal_forecast().drop(columns="fh"), lead_time=1, freq="D", **_calendar_args())
    assert without_fh.get_target_metadata() == with_fh.get_target_metadata()
    pd.testing.assert_frame_equal(without_fh.get_target_levels(), with_fh.get_target_levels())
    weekly = _steps(pd.date_range("2025-01-06", periods=3, freq="W-MON")).drop(columns="fh")
    inferred = _normal_fit(weekly, forecast_origin=pd.Timestamp("2024-12-30"))
    assert inferred.get_target_metadata()["forecast_frequency"] == "W-MON"
    with pytest.raises(ValueError, match="needs an fh column, or give forecast_origin"):
        _normal_fit(_normal_forecast().drop(columns="fh"), lead_time=1, freq="D")
    with pytest.raises(ValueError, match="is not a D period after the forecast origin"):
        _normal_fit(
            _normal_forecast().drop(columns="fh"), lead_time=1, freq="D",
            forecast_origin=ORIGIN + pd.Timedelta(days=1),
        )


def test_fixed_levels_ignore_the_default_date_column_and_reject_another():
    table = pd.DataFrame({"unique_id": ["A"], "date": [pd.Timestamp("2030-01-01")]})
    policy = ReorderPointPolicy(1, 1, allow_backorders=False).fit(
        table, reorder_point=2.0, order_up_to_level=6.0,
    )
    assert policy.get_target_metadata()["forecast_origin"] is None
    with pytest.raises(ValueError, match="date_column applies only to reorder_point_column"):
        ReorderPointPolicy(1, 1, allow_backorders=False, date_column="end").fit(
            table, reorder_point=2.0, order_up_to_level=6.0,
        )
    with pytest.raises(TypeError, match="reorder_end_date_column"):
        ReorderPointPolicy(1, 1, freq="D", allow_backorders=False).fit(
            pd.DataFrame({"unique_id": ["A"], "s": [2.0], "S": [6.0], "end": [ORIGIN]}),
            reorder_point_column="s", order_up_to_column="S", reorder_end_date_column="end",
        )


@pytest.mark.parametrize("first, periods", [("2025-01-06", 3), ("2025-01-06", 5)])
def test_weekday_only_dates_fit_business_days_too_and_need_freq(first, periods):
    frame = _steps(pd.date_range(first, periods=periods, freq="D"))  # Monday onwards
    with pytest.raises(ValueError, match="freq is required: .*fit both daily and business-day"):
        _normal_fit(frame)
    assert _normal_fit(frame, freq="D").get_target_metadata()["forecast_frequency"] == "D"


@pytest.mark.parametrize("dates, message", [
    # Monday, Wednesday, Friday: every other day, or every other business day.
    (["2025-01-06", "2025-01-08", "2025-01-10"], "fit both 2D and 2B"),
    # Friday, Wednesday, Monday: every 5 days, or every 3 business days.
    (["2025-01-03", "2025-01-08", "2025-01-13"], "fit both 5D and 3B"),
])
def test_weekday_only_multi_day_steps_that_fit_business_days_need_freq(dates, message):
    frame = _steps(pd.to_datetime(dates))
    with pytest.raises(ValueError, match=f"freq is required: .*{message} periods"):
        _normal_fit(frame)


@pytest.mark.parametrize("dates, freq", [
    (["2025-01-06", "2025-01-08", "2025-01-10", "2025-01-12"], "2D"),  # ends on a Sunday
    (["2025-01-07", "2025-01-10", "2025-01-13"], "3D"),  # Tue, Fri, Mon: no business-day step
])
def test_multi_day_steps_that_only_days_fit_are_read(dates, freq):
    assert _normal_fit(_steps(pd.to_datetime(dates))).get_target_metadata()["forecast_frequency"] == freq


@pytest.mark.parametrize("first, periods", [("2025-01-09", 3), ("2025-01-06", 7)])
def test_daily_dates_with_a_weekend_day_give_daily(first, periods):
    frame = _steps(pd.date_range(first, periods=periods, freq="D"))
    assert _normal_fit(frame).get_target_metadata()["forecast_frequency"] == "D"


def test_policy_reprs_show_the_shortage_mode_as_a_boolean():
    from stockcast.policies import OrderUpToPolicy

    assert "allow_backorders=False" in repr(OrderUpToPolicy(1, 1, allow_backorders=False))
    assert "allow_backorders=True" in repr(
        ReorderPointPolicy(lead_time=1, review_period=1, allow_backorders=True)
    )


def test_policy_reprs_name_their_class_and_frequency():
    from stockcast.policies import SingleOrderPolicy

    assert repr(OrderUpToPolicy(1, 2, freq="D", allow_backorders=False)) == (
        "OrderUpToPolicy(lead_time=1, review_period=2, freq='D', service_level=None, "
        "allow_backorders=False, status=not fitted)"
    )
    assert "freq='W-MON'" in repr(ReorderPointPolicy(1, 2, freq="W-MON", allow_backorders=False))
    assert repr(SingleOrderPolicy(1, freq="D", selling_horizon=3, allow_backorders=False)) == (
        "SingleOrderPolicy(lead_time=1, selling_horizon=3, decision_period=0, freq='D', "
        "service_level=None, allow_backorders=False, status=not fitted)"
    )


def test_an_irregular_decision_window_error_names_the_decision():
    from stockcast.core import ExplicitSchedule, InventoryStateDataFrame, SimulationEngine

    policy = OrderUpToPolicy(
        1, freq="D", schedule=ExplicitSchedule([0, 3, 7]), allow_backorders=False,
        date_column="end",
    ).fit(_direct_target(end=ORIGIN + pd.Timedelta(days=4)), target_column="S",
          protection_horizon=4)
    demand = pd.DataFrame({
        "unique_id": "A", "date": pd.date_range(ORIGIN + pd.Timedelta(days=1), periods=10), "y": 1.0,
    })
    state = InventoryStateDataFrame.from_observed(
        pd.DataFrame({"unique_id": ["A"], "on_hand": [5.0]}), opening_date=ORIGIN,
    )
    with pytest.raises(ValueError, match=(
        r"protection_horizon 4 does not cover the decision at period 3: the next "
        r"decision is at period 7, so this decision's window is 4 \+ lead_time 1 = 5"
    )):
        SimulationEngine().run(policy, demand, state)


def test_fixed_levels_name_the_window_arguments_they_reject():
    policy = ReorderPointPolicy(1, 1, freq="D", allow_backorders=False, date_column="end")
    with pytest.raises(ValueError, match="date_column applies only to reorder_point_column"):
        policy.fit(reorder_point=3, order_up_to_level=9)
    with pytest.raises(ValueError, match="date_column, reorder_horizon apply only"):
        policy.fit(reorder_point=3, order_up_to_level=9, reorder_horizon=2)


def test_per_step_rows_given_as_a_target_get_a_hint():
    with pytest.raises(ValueError, match="one row per forecast step, use mean_column and std_column"):
        OrderUpToPolicy(1, 1, freq="D", allow_backorders=False).fit(
            pd.DataFrame({"unique_id": ["A", "A"], "S": [1.0, 2.0]}), target_column="S",
            forecast_origin=ORIGIN,
        )
