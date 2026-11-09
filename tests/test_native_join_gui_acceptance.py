"""Keep an unattended malformed-invite check from starting a valid legacy join."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core.network_invite import BandInvite
from tools.check_native_join_recovery import (
    CheckStopped,
    LaunchControls,
    MALFORMED_INVITATION,
)
from webjam_qt.invitation_ingress import (
    InvitationIngressError,
    InvitationSource,
    parse_invitation_at_ingress,
)


def test_gui_fixture_is_rejected_by_the_real_invitation_ingress():
    with pytest.raises(InvitationIngressError):
        parse_invitation_at_ingress(MALFORMED_INVITATION, source=InvitationSource.PASTE)


def test_dns_shaped_dummy_is_a_valid_legacy_join_not_an_invalid_fixture():
    invitation = parse_invitation_at_ingress(
        "webjam-private-qa-invalid-invitation", source=InvitationSource.PASTE,
    )
    assert isinstance(invitation, BandInvite)


@pytest.mark.parametrize("action", ["Host", "Start Session", "Try Again", "Play"])
def test_gui_check_refuses_media_actions_before_using_accessibility(action, monkeypatch):
    controls = LaunchControls(123)

    def unexpected_accessibility(_):
        pytest.fail("forbidden action reached accessibility")

    monkeypatch.setattr(controls, "_execute", unexpected_accessibility)
    with pytest.raises(CheckStopped, match="media-or-unrelated-action-forbidden"):
        controls.press(action)


def run_cli(output, pid):
    script = Path(__file__).resolve().parents[1] / "tools/check_native_join_recovery.py"
    environment = {k: v for k, v in os.environ.items() if k != "BW_SESSION"}
    return subprocess.run(
        [sys.executable, "-B", str(script), "--pid", str(pid),
         "--expected-source", "0" * 40, "--output", str(output)],
        capture_output=True, text=True, timeout=15, env=environment,
    )


def test_existing_report_is_preserved_without_exposing_its_path(tmp_path):
    output = tmp_path / "private-report-location.json"
    original = b'{"preserve":"original attempt"}\n'
    output.write_bytes(original)
    result = run_cli(output, 123)
    transcript = result.stdout + result.stderr
    assert result.returncode == 1
    assert output.read_bytes() == original
    assert "Traceback" not in transcript
    assert str(output) not in transcript
    assert output.name not in transcript
    assert json.loads(result.stdout)["reason"] == "output-already-exists"


def test_invalid_pid_produces_a_complete_stopped_report_before_native_access(tmp_path):
    output = tmp_path / "invalid-process.json"
    result = run_cli(output, 0)
    assert result.returncode == 1
    assert result.stderr == ""
    report = json.loads(output.read_text())
    assert report == json.loads(result.stdout)
    assert report["state"] == "STOPPED"
    assert report["reason"] == "invalid-process-id"
    assert report["mediaRequested"] is False
