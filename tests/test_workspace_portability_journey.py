"""Two production publications survive a media move and a fresh OS process."""
import json
import os
from pathlib import Path
import subprocess
import sys

from services.workspace_portability_smoke import SUCCESS_MARKER


def test_controlled_recordings_and_art_resume_from_portable_backups_after_restart(tmp_path):
    results = []
    for phase in ("prepare", "resume"):
        process = subprocess.run(
            [sys.executable, "-m", "tests.support.run_workspace_portability", phase, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            capture_output=True, text=True, timeout=90,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        lines = process.stdout.strip().splitlines()
        assert lines[-1] == SUCCESS_MARKER
        results.append(json.loads(lines[-2]))
    prepared, resumed = results
    assert prepared["production_publications"] == prepared["coordinator_recordings"] == 2
    assert prepared["synthetic_rpc"] and prepared["imported_workspaces"] == 3
    assert prepared["draft_and_owner_retained"] and prepared["duplicate_ids_distinct"]
    assert resumed["writer_pid"] == prepared["pid"] != resumed["pid"]
    assert resumed["fresh_process"] and resumed["rendered_distinct_takes"] == resumed["declared_sources"] == 2
    assert resumed["verified_exports"] == resumed["explicit_art_opens"] == 1
    assert resumed["art_relinked"] and resumed["originals_unchanged"] and resumed["restored_media_unchanged"]
    assert not resumed["automatic_startup"] and resumed["physical_audio"] == "not_run"
    assert resumed["passive_media_probes"] == 0
    assert resumed["export_kind"] in {"studio_arrangement", "aligned_originals"}


def test_frozen_portability_workflow_uses_labeled_fixtures_and_same_real_controls():
    process = subprocess.run(
        [sys.executable, "-m", "tests.support.run_workspace_portability", "frozen"],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True, text=True, timeout=90,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    lines = process.stdout.strip().splitlines()
    assert lines[-1] == SUCCESS_MARKER
    result = json.loads(lines[-2])
    assert result["recording"]["fixture_takes"] == 2 and result["recording"]["production_publications"] == 0
    assert not result["fresh_process"] and result["rendered_distinct_takes"] == 2
    assert result["verified_exports"] == result["explicit_art_opens"] == 1
    assert result["art_relinked"] and result["originals_unchanged"] and result["restored_media_unchanged"]
    assert result["passive_media_probes"] == 0
