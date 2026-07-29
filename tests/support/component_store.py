"""Test-only component-store isolation helpers."""

from __future__ import annotations

from pathlib import Path
import tempfile


def isolated_component_store_root() -> Path:
    """Return a unique root so intentionally live mock runtimes cannot race."""

    return Path(tempfile.mkdtemp(prefix="webjam-test-components-"))


__all__ = ["isolated_component_store_root"]
