import pandas as pd
import pytest

from stockcast.utils import DemandGenerator


def _generator(**kwargs):
    return DemandGenerator(
        ["A", "B"],
        start_date="2025-01-06",
        period_frequency="W-MON",
        seed=17,
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
            seed=1,
            negative_demand_handling="raise",
        )
    with pytest.raises(ValueError, match="one identifier type"):
        DemandGenerator(
            ["1", 2],
            start_date="2025-01-01",
            period_frequency="D",
            seed=1,
            negative_demand_handling="raise",
        )
    for frequency in ["0D", "-1D"]:
        with pytest.raises(ValueError, match="advance time strictly forward"):
            DemandGenerator(
                ["A"],
                start_date="2025-01-01",
                period_frequency=frequency,
                seed=1,
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
        seed=1,
    )
    rejecting = DemandGenerator(
        ["A"],
        start_date="2025-01-01",
        period_frequency="D",
        seed=1,
        negative_demand_handling="raise",
    )
    clipping = DemandGenerator(
        ["A"],
        start_date="2025-01-01",
        period_frequency="D",
        seed=1,
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
