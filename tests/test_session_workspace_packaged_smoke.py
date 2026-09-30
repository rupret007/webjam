"""The frozen hook's Qt workflows also run without fixtures or user data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from services.session_workspace_packaged_smoke import SUCCESS_MARKER


def test_workspace_smoke_exercises_native_saved_work_and_review_in_fresh_process():
    result = subprocess.run(
        [sys.executable, "-m", "tests.support.run_session_workspace_smoke"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == SUCCESS_MARKER
    proof = json.loads(lines[-2])
    assert proof["music"] == {"songs": 2, "plain_notes": 1, "verified_bookmarks": 1}
    assert proof["art"] == {"references": 1, "manual_bookmarks": 1, "explicit_opens": 1}
    assert proof["review"] == {"favorites": 1, "saved_notes": 1, "rendered_comparisons": 2}
    assert proof["export"]["verified_files"] >= 4
    assert proof["export"]["changed_output_rejected"]
    assert proof["originals_unchanged"]
