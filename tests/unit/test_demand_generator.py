import pandas as pd
import pytest

from stockcast.utils import DemandGenerator


def _generator(**kwargs):
    return DemandGenerator(
        ["A", "B"],
        start_date="2025-01-06",
        period_frequency="W-MON",
        random_seed=17,
        negative_demand_handling="raise",
        **kwargs,
    )


def test_generator_requires_explicit_reproducibility_and_calendar_inputs():
    with pytest.raises(TypeError):
        DemandGenerator(["A"])
    with pytest.raises(ValueError, match="period_frequency"):
        DemandGenerator(
            ["A"],
            start_date="2025-01-01",
            period_frequency="not-a-frequency",
            random_seed=1,
            negative_demand_handling="raise",
        )
    with pytest.raises(ValueError, match="one identifier type"):
        DemandGenerator(
            ["1", 2],
            start_date="2025-01-01",
            period_frequency="D",
            random_seed=1,
            negative_demand_handling="raise",
        )
    for frequency in ["0D", "-1D"]:
        with pytest.raises(ValueError, match="advance time strictly forward"):
            DemandGenerator(
                ["A"],
                start_date="2025-01-01",
                period_frequency=frequency,
                random_seed=1,
                negative_demand_handling="raise",
            )


def test_generator_uses_declared_frequency_and_complete_sku_parameters():
    generator = _generator()
    demand = generator.constant(2, {"A": 1.0, "B": 2.0})

    assert demand.groupby("period")["date"].first().tolist() == [
        pd.Timestamp("2025-01-06"),
        pd.Timestamp("2025-01-13"),
    ]
    with pytest.raises(ValueError, match="exactly the generator SKUs"):
        generator.constant(1, {"A": 1.0})


def test_negative_draw_handling_is_explicit():
    default_rejecting = DemandGenerator(
        ["A"],
        start_date="2025-01-01",
        period_frequency="D",
        random_seed=1,
    )
    rejecting = DemandGenerator(
        ["A"],
        start_date="2025-01-01",
        period_frequency="D",
        random_seed=1,
        negative_demand_handling="raise",
    )
    clipping = DemandGenerator(
        ["A"],
        start_date="2025-01-01",
        period_frequency="D",
        random_seed=1,
        negative_demand_handling="clip_zero",
    )

    with pytest.raises(ValueError, match="negative values"):
        rejecting.trend(2, initial=0.0, growth_rate=-1.0, std=0.0)
    with pytest.raises(ValueError, match="negative values"):
        default_rejecting.trend(2, initial=0.0, growth_rate=-1.0, std=0.0)
    with pytest.warns(RuntimeWarning, match="clipped to zero"):
        clipped = clipping.trend(2, initial=0.0, growth_rate=-1.0, std=0.0)
    assert clipped["y"].tolist() == [0.0, 0.0]
    assert clipped.attrs["stockcast_demand_provenance"] == {
        "negative_demand_handling": "clip_zero",
        "clipped_negative_count": 1,
        "minimum_clipped_value": -1.0,
    }


def test_historical_sampling_rejects_implicit_fallbacks():
    generator = _generator()
    one_observation = pd.DataFrame({
        "unique_id": ["A", "B"],
        "y": [1.0, 2.0],
    })

    with pytest.raises(ValueError, match="at least two observations"):
        generator.from_historical(
            one_observation,
            n_periods=2,
            sampling_method="normal_moments",
        )
    with pytest.raises(ValueError, match="sampling_method"):
        generator.from_historical(
            one_observation,
            n_periods=2,
            sampling_method="automatic",
        )


def _poisson(rng, periods):
    return rng.poisson(6.0, periods.size)


def _weekends_only(rng, periods):
    return 10.0 * (periods % 7 >= 5)


def test_existing_generators_keep_their_random_streams():
    generator = _generator()
    assert generator.normal(3, mean=10.0, std=2.0)["y"].round(6).tolist() == [
        12.202525, 7.479516, 10.676863, 6.210757, 8.920057, 10.037277,
    ]
    fn = generator.normal_fn(mean=10.0, std=2.0)
    assert fn(0)["y"].round(6).tolist() == [8.378866, 8.255688]


def test_one_sampler_generates_the_whole_panel():
    demand = _generator().sample(4, _poisson)

    assert demand.columns.tolist() == ["unique_id", "y", "period", "date"]
    assert demand["unique_id"].tolist() == ["A", "B"] * 4
    assert demand["period"].tolist() == [0, 0, 1, 1, 2, 2, 3, 3]
    assert demand["y"].dtype == float
    assert demand.attrs["stockcast_demand_provenance"] == {
        "negative_demand_handling": "raise",
        "clipped_negative_count": 0,
        "minimum_clipped_value": None,
    }
    # Each SKU gets its own draws from the shared seeded stream.
    by_sku = demand.pivot(index="period", columns="unique_id", values="y")
    assert not by_sku["A"].equals(by_sku["B"])


def test_sampler_dict_assigns_one_sampler_per_sku():
    demand = _generator().sample(7, {"A": _poisson, "B": _weekends_only})
    weekends = demand[demand["unique_id"] == "B"]["y"].tolist()

    assert weekends == [0.0, 0.0, 0.0, 0.0, 0.0, 10.0, 10.0]
    with pytest.raises(ValueError, match="sampler dictionary must contain exactly"):
        _generator().sample(2, {"A": _poisson})


def test_samplers_are_reproducible_from_the_generator_seed():
    first = _generator().sample(10, _poisson)
    second = _generator().sample(10, _poisson)
    other_seed = DemandGenerator(
        ["A", "B"],
        start_date="2025-01-06",
        period_frequency="W-MON",
        random_seed=18,
    ).sample(10, _poisson)

    pd.testing.assert_frame_equal(first, second)
    assert not first["y"].equals(other_seed["y"])


def test_sampler_fn_receives_the_period_index():
    seen = []

    def sampler(rng, periods):
        seen.append(periods.tolist())
        return periods * 2.0

    fn = _generator().sample_fn(sampler)
    frame = fn(3)

    assert seen == [[3], [3]]
    assert frame["y"].tolist() == [6.0, 6.0]
    assert frame["period"].tolist() == [3, 3]
    assert frame["date"].tolist() == [pd.Timestamp("2025-01-27")] * 2
    with pytest.raises(ValueError, match="period must be an integer"):
        fn(-1)


@pytest.mark.parametrize(
    ("sampler", "error", "match"),
    [
        (lambda rng, periods: [1.0], ValueError, r"returned shape \(1,\); expected \(3,\)"),
        (lambda rng, periods: 1.0, ValueError, r"returned shape \(\); expected \(3,\)"),
        (lambda rng, periods: [[1.0, 2.0, 3.0]], ValueError, "returned shape"),
        (lambda rng, periods: [1.0, float("nan"), 2.0], ValueError, "non-finite"),
        (lambda rng, periods: ["1", "2", "3"], ValueError, "numeric values"),
        (lambda rng, periods: [True, False, True], ValueError, "numeric values"),
        (5.0, TypeError, "must be callable"),
    ],
)
def test_sampler_output_is_checked(sampler, error, match):
    with pytest.raises(error, match=match):
        _generator().sample(3, sampler)


def test_negative_sampler_draws_follow_negative_demand_handling():
    def negative(rng, periods):
        return periods - 1.0

    with pytest.raises(ValueError, match="negative values"):
        _generator().sample(2, negative)

    clipping = DemandGenerator(
        ["A", "B"],
        start_date="2025-01-06",
        period_frequency="W-MON",
        random_seed=17,
        negative_demand_handling="clip_zero",
    )
    with pytest.warns(RuntimeWarning, match="clipped to zero"):
        demand = clipping.sample(2, negative)
    assert demand["y"].tolist() == [0.0, 0.0, 0.0, 0.0]
    assert demand.attrs["stockcast_demand_provenance"] == {
        "negative_demand_handling": "clip_zero",
        "clipped_negative_count": 2,
        "minimum_clipped_value": -1.0,
    }


def test_engine_runs_on_a_poisson_sampler():
    from stockcast.core import InventoryStateDataFrame, SimulationEngine
    from stockcast.policies import OrderUpToPolicy

    skus = ["A", "B"]
    opening_date = pd.Timestamp("2025-01-01")
    generator = DemandGenerator(
        skus,
        start_date=opening_date + pd.Timedelta(days=1),
        period_frequency="D",
        random_seed=5,
    )
    inventory = InventoryStateDataFrame(
        skus, max_lead_time=1, allow_backorders=False,
    ).initialize_zero(start_date=opening_date)
    policy = OrderUpToPolicy(
        lead_time=1, review_period=1, service_level=0.9, allow_backorders=False,
    ).fit(
        pd.DataFrame({
            "unique_id": skus,
            "target": [15.0, 15.0],
            "target_end_date": [opening_date + pd.Timedelta(days=2)] * 2,
        }),
        target_column="target",
        target_probability=0.9,
        protection_horizon=2,
        forecast_origin=opening_date,
        forecast_frequency="D",
        target_end_date_column="target_end_date",
    )

    result = SimulationEngine().run(
        policy=policy,
        demand_source=generator.sample_fn(_poisson),
        inventory=inventory,
        n_periods=10,
        period_frequency="D",
        warmup_periods=0,
        scoring_periods=10,
        settlement_periods=0,
        order_during_settlement=False,
        demand_source_name="synthetic_poisson",
        random_seed=5,
    )

    events = result.to_event_frame()
    assert len(events) == 20
    assert (events["demand"] == events["demand"].round()).all()
    assert result.run_manifest["demand_source"]["generation_provenance"] == {
        "negative_demand_handling": "raise",
        "clipped_negative_count": 0,
        "minimum_clipped_value": None,
    }
