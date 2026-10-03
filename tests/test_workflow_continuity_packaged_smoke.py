"""Run the exact packaged navigation proof in an isolated interpreter."""
import json
import os
from pathlib import Path
import subprocess
import sys

from services.workflow_continuity_packaged_smoke import SUCCESS_MARKER


def test_smoke_isolates_settings_database_environment_and_log_handlers():
    result = subprocess.run(
        [sys.executable, "-m", "tests.support.run_smoke_isolation"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("WebJam smoke ownership and cleanup passed")


def test_packaged_proof_replaces_real_controllers_and_routes_local_help():
    result = subprocess.run(
        [sys.executable, "-m", "tests.support.run_workflow_continuity_smoke"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.strip().splitlines()
    assert lines[-1] == SUCCESS_MARKER
    assert json.loads(lines[-2]) == {
        "round_trips": 2, "fresh_controllers": 5, "help_routes": 4,
        "retired_callbacks_ignored": True, "saved_notes_unchanged": True,
        "local_project_identity_retained": True,
        "physical_audio": "not_run",
    }
