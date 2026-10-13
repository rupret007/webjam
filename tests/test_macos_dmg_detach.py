"""DMG verification waits only for bounded busy detach, never for other errors."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DETACH_SCRIPT = ROOT / ".github" / "scripts" / "detach-dmg.sh"


def _run_detach(tmp_path, statuses, arguments=None):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    calls = tmp_path / "calls"
    sleeps = tmp_path / "sleeps"
    count = tmp_path / "count"
    mount = tmp_path / "mounted image with spaces"
    mount.mkdir()
    arms = "\n".join(f"  {index}) exit {status} ;;" for index, status in enumerate(statuses, 1))
    hdiutil = tools_dir / "hdiutil"
    hdiutil.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$@" >> "$WEBJAM_DETACH_TEST_CALLS"\n'
        'attempt=0\n'
        'if test -f "$WEBJAM_DETACH_TEST_COUNT"; then\n'
        '  read -r attempt < "$WEBJAM_DETACH_TEST_COUNT"\n'
        'fi\n'
        'attempt=$((attempt + 1))\n'
        'printf "%s\\n" "$attempt" > "$WEBJAM_DETACH_TEST_COUNT"\n'
        'case "$attempt" in\n' + arms + '\n  *) exit 99 ;;\nesac\n'
    )
    hdiutil.chmod(0o755)
    sleeper = tools_dir / "sleep"
    sleeper.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$WEBJAM_DETACH_TEST_SLEEPS"\n')
    sleeper.chmod(0o755)
    result = subprocess.run(
        ["bash", str(DETACH_SCRIPT), *(arguments if arguments is not None else [str(mount)])],
        env={
            **os.environ,
            "PATH": str(tools_dir) + os.pathsep + os.environ["PATH"],
            "WEBJAM_DETACH_TEST_CALLS": str(calls),
            "WEBJAM_DETACH_TEST_SLEEPS": str(sleeps),
            "WEBJAM_DETACH_TEST_COUNT": str(count),
        },
        capture_output=True, text=True, timeout=10,
    )
    return (
        result,
        calls.read_text().splitlines() if calls.exists() else [],
        sleeps.read_text().splitlines() if sleeps.exists() else [],
        str(mount),
    )


@pytest.mark.parametrize("statuses,exit_code,attempts,delays", [
    ([0], 0, 1, []),
    ([16, 0], 0, 2, ["2"]),
    ([16, 16, 0], 0, 3, ["2", "4"]),
    ([16, 16, 16, 0], 16, 3, ["2", "4"]),
    ([1, 0], 1, 1, []),
    ([64, 0], 64, 1, []),
    ([16, 1, 0], 1, 2, ["2"]),
])
def test_only_busy_detach_can_retry_and_persistent_failure_stays_red(
    tmp_path, statuses, exit_code, attempts, delays,
):
    result, calls, sleeps, mount = _run_detach(tmp_path, statuses)
    assert result.returncode == exit_code, result.stderr
    # Every retry targets the same owned mount as one argument. No force
    # detach may turn package verification into a passing result.
    assert calls == ["detach", mount] * attempts
    assert sleeps == delays


@pytest.mark.parametrize("arguments", [[], [""], ["relative-mount"], ["-force"], ["/mount", "-force"]])
def test_invalid_mount_arguments_never_call_hdiutil(tmp_path, arguments):
    result, calls, sleeps, _mount = _run_detach(tmp_path, [0], arguments)
    assert result.returncode == 2
    assert calls == []
    assert sleeps == []


def test_workflow_requires_detach_before_copied_package_verification():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    step = workflow.split("      - name: Verify mounted macOS disk image\n", 1)[1]
    step = step.split("\n      - name:", 1)[0]
    assert step.index('ditto "$mount_dir/WebJam.app" "$copy_dir/WebJam.app"') < step.index(
        '.github/scripts/detach-dmg.sh "$mount_dir"'
    ) < step.index("trap - EXIT") < step.index('codesign --verify --deep --strict "$copied_app"')
    assert 'hdiutil detach "$mount_dir" -force >/dev/null 2>&1 || true' in step
    assert "tests.support.verify_packaged_transport" in step
    assert "tests.support.run_frozen_pocket_stage_smoke" in step
    assert "tests.support.run_frozen_reference_studio_smoke" in step
    assert '"$copied_app/Contents/MacOS/WebJam"' in step
