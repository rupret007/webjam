"""Portable admission checks never substitute for the explicit Windows control."""
import importlib.util
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CONTROL = ROOT / "tests/support/windows_directory_sharing_control.py"


def test_native_directory_control_is_mandatory_in_windows_job():
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    job = workflow.split("  windows-diagnostic-control:\n", 1)[1].split("\n  test:", 1)[0]
    marker = "      - name: Prove native directory ancestor sharing fence\n"
    step = job.split(marker, 1)[1].split("      - name:", 1)[0]
    assert "timeout-minutes: 1" in step
    assert "python -X dev -m tests.support.windows_directory_sharing_control" in step
    assert "if:" not in step and "continue-on-error" not in step


def test_native_directory_control_retains_all_four_distinct_regressions():
    spec = importlib.util.spec_from_file_location("directory_control", CONTROL)
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    names = control.unittest.defaultTestLoader.getTestCaseNames(control.DirectorySharing)
    assert set(names) == {
        "test_directory_only_fence_without_leaf_handle",
        "test_preexisting_delete_refuses_directory_pin",
        "test_full_guard_fences_ancestor_delete_and_rename_until_release",
        "test_full_guard_refuses_existing_ancestor_delete_and_cleans_up",
    }


def test_native_directory_control_rejects_selectors_on_every_platform():
    result = subprocess.run(
        [sys.executable, str(CONTROL), "--collect-only"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "no selectors or skips" in result.stderr
