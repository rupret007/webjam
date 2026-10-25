"""CI wiring controls; passing these is not native Windows API evidence."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


def control_job():
    match = re.search(
        r"(?ms)^  windows-diagnostic-control:\n.*?(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        WORKFLOW,
    )
    assert match is not None
    return match.group(0)


def step(name):
    job = control_job()
    marker = f"      - name: {name}\n"
    start = job.index(marker)
    end = job.find("\n      - name:", start + len(marker))
    return job[start : end if end >= 0 else len(job)]


def test_actual_acl_diagnostic_uses_existing_unconditional_windows_control_job():
    job = control_job()
    assert "    runs-on: windows-2025\n" in job
    assert "    steps:\n" in job
    assert "    if:" not in job.partition("    steps:\n")[0]
    assert "Prove native process ownership and startup failure cleanup" in job
    assert "Prove native debugger control independently of product gates" in job
    run = step("Prove actual Windows transport ACL and share controls")
    assert "        if: always()\n" in run
    assert "        timeout-minutes: 3\n" in run
    assert "tests/native_windows/test_transport_permissions_native.py" in run
    assert "--confcutdir tests/native_windows" in run
    assert "continue-on-error" not in run
    assert "workflow_dispatch" not in run


def test_native_acl_results_are_retained_even_when_controls_fail():
    retained = step("Retain independent native control evidence")
    assert "        if: always()\n" in retained
    assert "out/windows-diagnostic-control/" in retained
    assert "out/windows-diagnostic-process-tests/" in retained
    run = step("Prove actual Windows transport ACL and share controls")
    for path in (
        "out/windows-transport-acl-native/",
        "out/windows-transport-acl-native.xml",
        "out/windows-transport-acl-native-pytest/",
    ):
        assert path in retained
    assert "--junitxml out/windows-transport-acl-native.xml" in run
    assert "--basetemp out/windows-transport-acl-native-pytest" in run
