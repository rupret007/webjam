"""A fresh OS process must reconstruct saved work rather than inherit objects."""
import json
import os
from pathlib import Path
import subprocess
import sys


def test_saved_rehearsal_reviews_and_exact_takes_survive_fresh_process_restart(tmp_path):
    results = []
    for phase in ("save", "reopen"):
        process = subprocess.run(
            [sys.executable, "-m", "tests.support.run_saved_work_restart", phase, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            capture_output=True, text=True, timeout=45,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        results.append(json.loads(process.stdout.strip().splitlines()[-1]))
    saved, reopened = results
    assert saved["phase"] == "saved"
    assert saved["coordinator_recordings"] == saved["production_publications"] == 2
    assert saved["verified_exports"] == 1 and saved["synthetic_rpc"]
    assert saved["pid"] == reopened["writer_pid"] != reopened["pid"]
    assert reopened == {
        "phase": "reopened", "pid": reopened["pid"], "writer_pid": saved["pid"],
        "takes": 2, "songs": 2, "reviews": 2, "notes_retained": True,
        "originals_unchanged": True, "older_take_is_not_new": True,
        "automatic_startup": False, "physical_audio": "not_run",
        "verified_exports": 1, "production_publications": 2,
    }
