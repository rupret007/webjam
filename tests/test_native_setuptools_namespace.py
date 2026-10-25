"""Exercise patched legacy wheel metadata using only disposable local files.

These are real setuptools operations, not Windows installation or native
platform trust evidence. Every possible destination stays in pytest's lab.
"""

from pathlib import Path
from zipfile import ZipFile

import pytest

from setuptools.wheel import Wheel


def _namespace_wheel(directory: Path, namespace: str) -> Path:
    path = directory / "probe-1.0-py3-none-any.whl"
    with ZipFile(path, "w") as wheel:
        prefix = "probe-1.0.dist-info/"
        wheel.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        wheel.writestr(prefix + "METADATA", "Metadata-Version: 2.1\nName: probe\nVersion: 1.0\n")
        wheel.writestr(prefix + "namespace_packages.txt", namespace + "\n")
        wheel.writestr(prefix + "RECORD", "")
    return path


def test_legacy_wheel_rejects_absolute_namespace_without_outside_write(tmp_path):
    destination = tmp_path / "destination"
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "canary.txt"
    canary.write_bytes(b"unchanged namespace-metadata canary")
    wheel = _namespace_wheel(tmp_path, str(outside))

    with pytest.raises(ValueError, match="namespace"):
        Wheel(str(wheel)).install_as_egg(str(destination))

    assert not (outside / "__init__.py").exists()
    assert canary.read_bytes() == b"unchanged namespace-metadata canary"
    assert sorted(path.name for path in outside.iterdir()) == ["canary.txt"]


def test_legacy_wheel_keeps_valid_namespace_inside_selected_destination(tmp_path):
    destination = tmp_path / "destination"
    wheel = _namespace_wheel(tmp_path, "safe_namespace")

    Wheel(str(wheel)).install_as_egg(str(destination))

    assert (destination / "safe_namespace" / "__init__.py").is_file()
    assert (destination / "EGG-INFO" / "PKG-INFO").is_file()
    assert not (tmp_path / "safe_namespace").exists()
