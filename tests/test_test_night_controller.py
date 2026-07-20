"""Controller-level coverage for the private, operator-only Test Night flow.

These tests intentionally construct the controller's small pilot boundary in
isolation.  They exercise only durable, allowlisted evidence; no Jamulus,
audio device, second Mac, or recording hardware is started or implied.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.pilot_evidence import (  # noqa: E402
    EvidenceReference,
    PilotObservationClass,
    PilotRole,
    load_pilot_ledger,
)
from core.session_conductor import SessionConductorPhase  # noqa: E402
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)


class _Window:
    """Only the non-visual controller surface the pilot methods require."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, int]] = []

    def flash_message(self, message: str, *, ms: int) -> None:
        self.messages.append((message, ms))


def _controller(
    storage_dir: Path,
    *,
    operator_mode: bool = True,
    phase: SessionConductorPhase = SessionConductorPhase.INVITE_READY,
) -> ApplicationController:
    """Build just enough controller state to test its pilot coordinator."""

    controller = ApplicationController.__new__(ApplicationController)
    controller.window = _Window()
    controller._operator_mode = operator_mode
    controller._pilot_ledger = None
    controller._pilot_run_state = "not_started"
    controller._pilot_check_status = {}
    controller._pilot_last_conductor_phase = ""
    controller._test_night_dialog = None
    controller._last_session_conductor = SimpleNamespace(phase=phase)
    controller._pilot_storage_dir = lambda: storage_dir
    controller._pilot_role = lambda: PilotRole.HOST
    controller._shutdown = False
    return controller


def test_normal_controller_cannot_open_hidden_test_night(tmp_path: Path) -> None:
    """The controller has no normal-user path into the operator workflow."""

    controller = _controller(tmp_path, operator_mode=False)

    with patch("webjam_qt.windows.test_night.TestNightDialog") as dialog_type:
        controller._open_test_night()

    dialog_type.assert_not_called()
    assert controller._test_night_dialog is None
    assert controller._pilot_ledger is None


@pytest.mark.parametrize(
    "target",
    ("macos-arm64", "macos-x64", "windows-x64", "linux-x64"),
)
def test_frozen_pilot_identity_uses_the_actual_desktop_target(
    tmp_path: Path,
    target: str,
) -> None:
    controller = _controller(tmp_path)
    with patch(
        "webjam_qt.controllers.application_controller.sys.frozen",
        True,
        create=True,
    ), patch("core.build_info.desktop_target", return_value=target), patch(
        "core.build_info.build_id", return_value="a" * 40
    ):
        controller._start_test_night()

    from webjam_qt import __version__

    assert controller._pilot_ledger is not None
    assert controller._pilot_ledger.artifact_identity == (
        f"webjam-v{__version__}-test-night-{target}"
    )


def test_pilot_run_persists_automatic_and_explicit_human_evidence(
    tmp_path: Path,
) -> None:
    """Lifecycle facts persist, while audibility remains a human assertion."""

    controller = _controller(tmp_path)
    controller._start_test_night()

    assert controller._pilot_run_state == "running"
    assert controller._pilot_ledger is not None
    initial_run_id = controller._pilot_ledger.run_id
    initial_classes = {event.observation_class for event in controller._pilot_ledger.events}
    assert {
        PilotObservationClass.APP_LAUNCHED,
        PilotObservationClass.PACKAGE_IDENTITY,
        PilotObservationClass.INVITE_AVAILABILITY,
    } <= initial_classes
    assert not any(
        event.observation_class
        in {
            PilotObservationClass.HUMAN_HOST_HEARD_BANDMATE,
            PilotObservationClass.HUMAN_BANDMATE_HEARD_HOST,
        }
        for event in controller._pilot_ledger.events
    )

    controller._record_pilot_conductor_presentation(
        SimpleNamespace(phase=SessionConductorPhase.RECORDING)
    )
    persisted = load_pilot_ledger(tmp_path, initial_run_id)
    assert PilotObservationClass.RECORDER_CONFIRMATION in {
        event.observation_class for event in persisted.events
    }

    controller._record_test_night_manual_outcome("hear_each_other", "verified")
    human_events = [
        event
        for event in controller._pilot_ledger.events
        if event.observation_class
        in {
            PilotObservationClass.HUMAN_HOST_HEARD_BANDMATE,
            PilotObservationClass.HUMAN_BANDMATE_HEARD_HOST,
        }
    ]
    assert len(human_events) == 2
    assert all(
        event.evidence_reference is EvidenceReference.HUMAN_CONFIRMATION
        for event in human_events
    )
    assert controller._pilot_check_status["hear_each_other"] == "verified"

    controller._pause_test_night()
    assert controller._pilot_run_state == "paused"
    controller._resume_test_night()
    assert controller._pilot_run_state == "running"
    controller._abandon_test_night()
    assert controller._pilot_run_state == "abandoned"
    assert controller._pilot_ledger.events[-1].observation_class is (
        PilotObservationClass.PILOT_ABANDONED
    )

    controller._restart_test_night()
    assert controller._pilot_run_state == "running"
    assert controller._pilot_ledger is not None
    assert controller._pilot_ledger.run_id != initial_run_id
    abandoned = load_pilot_ledger(tmp_path, initial_run_id)
    assert abandoned.events[-1].observation_class is PilotObservationClass.PILOT_ABANDONED


def test_unfinished_run_restores_paused_until_operator_resumes(tmp_path: Path) -> None:
    """A process restart never silently resumes a Test Night run."""

    first_controller = _controller(tmp_path)
    first_controller._start_test_night()
    assert first_controller._pilot_ledger is not None
    run_id = first_controller._pilot_ledger.run_id

    restarted_controller = _controller(tmp_path)
    assert restarted_controller._pilot_restore_latest() is True
    assert restarted_controller._pilot_run_state == "paused"
    assert restarted_controller._pilot_ledger is not None
    assert restarted_controller._pilot_ledger.run_id == run_id
    assert not any(
        event.observation_class is PilotObservationClass.PILOT_RESUMED
        for event in restarted_controller._pilot_ledger.events
    )

    restarted_controller._resume_test_night()
    assert restarted_controller._pilot_run_state == "running"
    resumed = load_pilot_ledger(tmp_path, run_id)
    assert PilotObservationClass.PILOT_RESUMED in {
        event.observation_class for event in resumed.events
    }


def test_late_track_export_callback_cannot_mutate_a_paused_pilot(
    tmp_path: Path,
) -> None:
    """A worker completion after Pause is ordinary UI state, not new evidence."""

    controller = _controller(tmp_path)
    controller._start_test_night()
    assert controller._pilot_ledger is not None
    controller._pause_test_night()
    event_count = len(controller._pilot_ledger.events)
    controller._update_session_hud = lambda: None

    controller._on_studio_export_finished(True)

    assert controller._pilot_run_state == "paused"
    assert len(controller._pilot_ledger.events) == event_count
    assert all(
        event.observation_class is not PilotObservationClass.TRACK_EXPORT
        for event in controller._pilot_ledger.events
    )


def test_exported_pilot_report_is_sanitized_and_omits_local_paths(
    tmp_path: Path,
) -> None:
    """Explicit export keeps local storage and destination paths private."""

    controller = _controller(tmp_path)
    controller._start_test_night()
    controller._pause_test_night()
    destination = tmp_path / "operator-export"
    destination.mkdir()

    with patch(
        "PySide6.QtWidgets.QFileDialog.getExistingDirectory",
        return_value=str(destination),
    ):
        controller._export_test_night_report()

    report_path = destination / "WebJam-private-pilot-report.json"
    payload = report_path.read_text(encoding="utf-8")
    report = json.loads(payload)
    assert report["privacy"]["paths_included"] is False
    assert str(tmp_path) not in payload
    assert str(destination) not in payload
    assert ".webjam-pilot-evidence" not in payload
    assert report["run"]["run_id"] == controller._pilot_ledger.run_id
