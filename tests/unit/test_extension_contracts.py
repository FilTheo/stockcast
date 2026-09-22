"""Focused tests for staging package boundaries."""

from pathlib import Path

from stockcast import SimulationEngine


def test_after_step_is_not_part_of_the_engine_extension_surface():
    """A finalized event must not be followed by an unrestricted state hook."""
    assert not hasattr(SimulationEngine, "after_step")


def test_staging_tree_does_not_ship_a_pystate_compatibility_package():
    """The retired namespace must not remain as an active source package."""
    project_root = Path(__file__).resolve().parents[2]
    assert not (project_root / "src" / "pystate").exists()
