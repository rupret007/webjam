from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolButton  # noqa: E402

from core.band_check import (  # noqa: E402
    BandCheckMode,
    BandCheckObservations,
    BandCheckSession,
    BandCheckStatus,
    BandCheckStep,
    BandCheckStepKey,
)
from webjam_qt.windows.ready_check import (  # noqa: E402
    BandCheckDialog,
    ReadyCheckDialog,
)
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402


APP = QApplication.instance() or QApplication([])


def _settings():
    return SimpleNamespace(
        audio_input_device_index=0,
        audio_samplerate=48_000,
        audio_blocksize=0,
        take_playback_output_device="",
        config_file="/tmp/.webjam-test-config.json",
    )


def _session(mode: BandCheckMode = BandCheckMode.PRE_SESSION) -> BandCheckSession:
    return BandCheckSession(
        mode,
        [
            BandCheckStep(
                BandCheckStepKey.MUSIC_ENGINE,
                "Music engine",
                BandCheckStatus.PASS,
                "Available",
                technical_details=("version=3.12.2",),
            ),
            BandCheckStep(
                BandCheckStepKey.AUDIO_INPUT,
                "Your instrument input",
                BandCheckStatus.PENDING,
                "Play a note",
                "Check Input",
            ),
            BandCheckStep(
                BandCheckStepKey.HEADPHONES,
                "Headphones",
                BandCheckStatus.PENDING,
                "Explicit test",
                "Play Left & Right",
            ),
            BandCheckStep(
                BandCheckStepKey.TEST_RECORDING,
                "Five-second recording",
                BandCheckStatus.PENDING,
                "Explicit recording",
                "Record 5 Seconds",
            ),
        ],
    )


def _dialog(session: BandCheckSession) -> BandCheckDialog:
    with mock.patch(
        "webjam_qt.windows.ready_check.build_band_check_session",
        return_value=session,
    ):
        dialog = BandCheckDialog(lambda: _settings(), mode=session.mode)
        dialog.show()
        for _ in range(30):
            APP.processEvents()
            if dialog._session is not None:
                break
    return dialog


def test_old_class_name_is_only_a_compatibility_alias() -> None:
    assert ReadyCheckDialog is BandCheckDialog


def test_dialog_uses_band_check_name_exact_outcome_and_plain_next_action() -> None:
    dialog = _dialog(_session())
    try:
        assert dialog.windowTitle() == "WebJam — Band Check"
        assert dialog._summary.text() == "Action Needed"
        assert dialog._next.text() == "Next: Check Input."
        assert dialog._primary.text() == "Check Input"
        assert dialog._primary.accessibleName() == "Check Input"
    finally:
        dialog.close()


def test_constructing_dialog_never_plays_or_records() -> None:
    with mock.patch(
        "webjam_qt.windows.ready_check.build_band_check_session",
        return_value=_session(),
    ), mock.patch(
        "webjam_qt.windows.ready_check.InputActivityProbe.start"
    ) as input_start, mock.patch(
        "webjam_qt.windows.ready_check.ScratchRecorder.start"
    ) as record_start, mock.patch(
        "webjam_qt.windows.ready_check.HeadphoneTonePlayer.play"
    ) as tone_play:
        dialog = BandCheckDialog(lambda: _settings())
        for _ in range(20):
            APP.processEvents()
        input_start.assert_not_called()
        record_start.assert_not_called()
        tone_play.assert_not_called()
        dialog.close()


def test_pre_session_host_scan_runs_production_server_certification_in_worker() -> None:
    settings = _settings()
    settings.host_server_enabled = True
    certification = mock.sentinel.certification
    service = SimpleNamespace(
        certify_hosted_server_lifecycle=mock.Mock(return_value=certification)
    )
    with mock.patch(
        "webjam_qt.windows.ready_check.build_band_check_session",
        return_value=_session(),
    ) as build:
        dialog = BandCheckDialog(
            lambda: settings,
            host_server_service=service,
        )
        for _ in range(30):
            APP.processEvents()
            if dialog._session is not None:
                break
    try:
        service.certify_hosted_server_lifecycle.assert_called_once_with()
        assert build.call_args.kwargs["host_server_certification"] is certification
    finally:
        dialog.close()


def test_live_band_check_never_runs_host_server_lifecycle() -> None:
    settings = _settings()
    settings.host_server_enabled = True
    service = SimpleNamespace(certify_hosted_server_lifecycle=mock.Mock())
    with mock.patch(
        "webjam_qt.windows.ready_check.build_band_check_session",
        return_value=_session(BandCheckMode.LIVE_OBSERVE),
    ):
        dialog = BandCheckDialog(
            lambda: settings,
            mode=BandCheckMode.LIVE_OBSERVE,
            host_server_service=service,
        )
        for _ in range(30):
            APP.processEvents()
            if dialog._session is not None:
                break
    try:
        service.certify_hosted_server_lifecycle.assert_not_called()
    finally:
        dialog.close()


def test_technical_details_are_collapsed_by_default() -> None:
    dialog = _dialog(_session())
    try:
        toggle = dialog.findChild(QToolButton, "TechnicalDetailsButton")
        technical = dialog.findChild(QLabel, "TechnicalDetailsText")
        assert toggle is not None and technical is not None
        assert not technical.isVisible()
        toggle.setChecked(True)
        APP.processEvents()
        assert technical.isVisible()
    finally:
        dialog.close()


def test_live_input_action_observes_existing_meter_without_opening_device() -> None:
    session = _session(BandCheckMode.LIVE_OBSERVE)
    observations = BandCheckObservations(
        music_engine_running=True,
        music_engine_responsive=True,
        local_meter_active=True,
        local_meter_rms=0.05,
        local_meter_peak=0.08,
    )
    with mock.patch(
        "webjam_qt.windows.ready_check.build_band_check_session",
        return_value=session,
    ), mock.patch(
        "webjam_qt.windows.ready_check.InputActivityProbe.start"
    ) as start:
        dialog = BandCheckDialog(
            lambda: _settings(),
            mode=BandCheckMode.LIVE_OBSERVE,
            observations_provider=lambda: observations,
        )
        dialog.show()
        for _ in range(30):
            APP.processEvents()
            if dialog._session is not None:
                break
        dialog._run_primary_action()
        start.assert_not_called()
        assert session.step(BandCheckStepKey.AUDIO_INPUT).status is BandCheckStatus.PASS
        assert "WebJam can hear this input" in session.step(
            BandCheckStepKey.AUDIO_INPUT
        ).detail
        dialog.close()


def test_support_action_is_explicit_and_emits() -> None:
    dialog = _dialog(_session())
    received = []
    dialog.support_requested.connect(lambda: received.append(True))
    try:
        button = next(
            item
            for item in dialog.findChildren(QPushButton)
            if item.text() == "Save Support Bundle"
        )
        button.click()
        assert received == [True]
    finally:
        dialog.close()


class _ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def test_startup_reuses_only_a_matching_usable_verification() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    controller.settings = _settings()
    controller.window = mock.Mock()
    controller._ui_invoker = SimpleNamespace(invoke=lambda callback: callback())
    controller._on_launch_audio = mock.Mock()
    controller._open_band_check = mock.Mock()
    signature = mock.sentinel.signature
    saved = mock.Mock()
    saved.matches.return_value = True
    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _ImmediateThread,
    ), mock.patch(
        "core.band_check.build_verification_signature", return_value=signature
    ), mock.patch(
        "core.band_check.load_verification", return_value=saved
    ), mock.patch(
        "core.band_check.verification_path", return_value="/verification.json"
    ):
        controller.start_session_or_band_check()
    saved.matches.assert_called_once_with(signature)
    controller._on_launch_audio.assert_called_once_with()
    controller._open_band_check.assert_not_called()


def test_startup_changed_setup_fails_closed_into_band_check() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    controller.settings = _settings()
    controller.window = mock.Mock()
    controller._ui_invoker = SimpleNamespace(invoke=lambda callback: callback())
    controller._on_launch_audio = mock.Mock()
    controller._open_band_check = mock.Mock()
    saved = mock.Mock()
    saved.matches.return_value = False
    with mock.patch(
        "webjam_qt.controllers.application_controller.threading.Thread",
        _ImmediateThread,
    ), mock.patch(
        "core.band_check.build_verification_signature"
    ), mock.patch(
        "core.band_check.load_verification", return_value=saved
    ), mock.patch(
        "core.band_check.verification_path", return_value="/verification.json"
    ):
        controller.start_session_or_band_check()
    controller._on_launch_audio.assert_not_called()
    controller._open_band_check.assert_called_once_with(
        start_session_when_ready=True
    )
