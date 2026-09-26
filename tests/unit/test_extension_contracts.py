"""Focused tests for package extension boundaries."""

from stockcast import SimulationEngine


def test_after_step_is_not_part_of_the_engine_extension_surface():
    """A finalized event must not be followed by an unrestricted state hook."""
    assert not hasattr(SimulationEngine, "after_step")
