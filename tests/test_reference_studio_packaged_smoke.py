"""Frozen Reference Studio runtime-smoke contracts."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from services.reference_studio_packaged_smoke import (
    SUCCESS_MARKER,
    run_frozen_reference_studio_smoke,
)


def test_reference_studio_packaged_smoke_exercises_complete_core_path() -> None:
    with tempfile.TemporaryDirectory(
        prefix="webjam-reference-studio-smoke-"
    ) as directory:
        result = Path(directory) / "result.txt"

        assert run_frozen_reference_studio_smoke(result_path=result) == 0

        assert result.read_text(encoding="utf-8") == SUCCESS_MARKER + "\n"


def test_reference_studio_packaged_smoke_rejects_unowned_result_path(
    tmp_path: Path,
) -> None:
    result = tmp_path / "result.txt"

    with pytest.raises(RuntimeError, match="result path is invalid"):
        run_frozen_reference_studio_smoke(result_path=result)

    assert not result.exists()
