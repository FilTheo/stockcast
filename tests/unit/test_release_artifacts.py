"""Packaging regressions: stale local metadata must not enter a release."""

import io
import runpy
import tarfile
import zipfile
from pathlib import Path

import pytest

check_artifacts = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "release/check_artifacts.py")
)["main"]


def test_artifact_check_rejects_foreign_distribution_metadata(tmp_path):
    with zipfile.ZipFile(tmp_path / "stockcast-0.1.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr("stockcast-0.1.0.dist-info/METADATA", "Name: stockcast\n")
        wheel.writestr("stockcast-0.1.0.dist-info/licenses/LICENSE", "test fixture")
    with tarfile.open(tmp_path / "stockcast-0.1.0.tar.gz", "w:gz") as sdist:
        for name in ["PKG-INFO", "LICENSE", "src/pyforia.egg-info/PKG-INFO"]:
            info = tarfile.TarInfo(f"stockcast-0.1.0/{name}")
            data = b"fixture"
            info.size = len(data)
            sdist.addfile(info, io.BytesIO(data))
    with pytest.raises(AssertionError, match="foreign distribution metadata"):
        check_artifacts(tmp_path)


def test_artifact_check_requires_one_wheel_and_one_sdist(tmp_path):
    (tmp_path / "stockcast-one.whl").touch()
    (tmp_path / "stockcast-two.whl").touch()
    with pytest.raises(AssertionError, match="one wheel and one sdist"):
        check_artifacts(tmp_path)
