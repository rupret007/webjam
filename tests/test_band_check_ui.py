from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QPushButton,
    QWidget,
)

from core.band_check import (  # noqa: E402
    BandCheckMode,
    BandCheckObservations,
    BandCheckOutcome,
    BandCheckSession,
    BandCheckStatus,
    BandCheckStep,
    BandCheckStepKey,
)
from core.band_check_audio import BandCheckAudioError  # noqa: E402
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


def test_private_technical_details_are_not_an_empty_disclosure() -> None:
    dialog = _dialog(_session())
    try:
        rendered = " ".join(label.text() for label in dialog.findChildren(QLabel))
        assert "Technical Details" not in rendered
        assert "Private technical values are hidden" not in rendered
        assert any(
            button.text() == "Save Support Bundle"
            for button in dialog.findChildren(QPushButton)
        )
    finally:
        dialog.close()


def test_report_never_renders_private_backend_values() -> None:
    secret = "token=do-not-show"
    session = BandCheckSession(
        BandCheckMode.PRE_SESSION,
        [
            BandCheckStep(
                BandCheckStepKey.MUSIC_ENGINE,
                "Music engine",
                BandCheckStatus.ACTION_NEEDED,
                f"Failed at /tmp/private/session.json with {secret}",
                technical_details=(
                    f"/Volumes/Private/engine.log {secret}",
                    "https://company.webex.com/meet/private-room",
                ),
            )
        ],
    )
    dialog = _dialog(session)
    try:
        rendered = " ".join(label.text() for label in dialog.findChildren(QLabel))
        accessible = " ".join(
            widget.accessibleName() for widget in dialog.findChildren(QWidget)
        )
        combined = f"{rendered} {accessible}"
        assert "do-not-show" not in combined
        assert "/tmp/private" not in combined
        assert "/Volumes/Private" not in combined
        assert "private-room" not in combined
        assert any(
            button.text() == "Save Support Bundle"
            for button in dialog.findChildren(QPushButton)
        )
        assert "Technical Details" not in rendered
        assert "Private technical values are hidden" not in rendered
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
    ) as start, mock.patch(
        "webjam_qt.windows.ready_check.microphone_permission_status"
    ) as permission_status:
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
        permission_status.assert_not_called()
        assert session.step(BandCheckStepKey.AUDIO_INPUT).status is BandCheckStatus.PASS
        assert "WebJam can hear this input" in session.step(
            BandCheckStepKey.AUDIO_INPUT
        ).detail
        dialog.close()


def test_first_input_action_explains_mac_permission_before_opening_probe() -> None:
    session = _session()
    dialog = _dialog(session)
    try:
        with mock.patch(
            "webjam_qt.windows.ready_check.microphone_permission_status",
            return_value="not_determined",
        ) as permission_status, mock.patch(
            "webjam_qt.windows.ready_check.InputActivityProbe"
        ) as probe_type:
            dialog._run_primary_action()

            probe_type.assert_not_called()
            step = session.step(BandCheckStepKey.AUDIO_INPUT)
            assert step.status is BandCheckStatus.RUNNING
            assert "macOS prompt" in step.detail
            assert step.next_action == "Continue"
            assert dialog._primary.text() == "Continue"

            dialog._run_primary_action()

            permission_status.assert_called_with()
            assert permission_status.call_count == 2
            probe_type.assert_called_once_with(
                device=0,
                sample_rate=48_000,
                blocksize=0,
            )
            probe_type.return_value.start.assert_called_once_with()
    finally:
        dialog.close()


def test_denied_input_action_opens_settings_then_retries() -> None:
    session = _session()
    dialog = _dialog(session)
    opened = []
    dialog.microphone_settings_requested.connect(lambda: opened.append(True))
    try:
        with mock.patch(
            "webjam_qt.windows.ready_check.microphone_permission_status",
            return_value="denied",
        ) as permission_status, mock.patch(
            "webjam_qt.windows.ready_check.InputActivityProbe"
        ) as probe_type:
            dialog._run_primary_action()

            probe_type.assert_not_called()
            step = session.step(BandCheckStepKey.AUDIO_INPUT)
            assert step.status is BandCheckStatus.ACTION_NEEDED
            assert step.next_action == "Open System Settings"
            assert dialog._primary.text() == "Open System Settings"

            dialog._run_primary_action()

            assert opened == [True]
            assert session.step(BandCheckStepKey.AUDIO_INPUT).next_action == "Try Again"
            assert dialog._primary.text() == "Try Again"
            probe_type.assert_not_called()

            permission_status.return_value = "authorized"
            dialog._run_primary_action()
            probe_type.return_value.start.assert_called_once_with()
    finally:
        dialog.close()


def test_permission_denied_during_mac_prompt_routes_to_system_settings() -> None:
    session = _session()
    dialog = _dialog(session)
    opened: list[bool] = []
    dialog.microphone_settings_requested.connect(lambda: opened.append(True))
    try:
        with mock.patch(
            "webjam_qt.windows.ready_check.microphone_permission_status",
            side_effect=["not_determined", "not_determined", "denied"],
        ), mock.patch(
            "webjam_qt.windows.ready_check.InputActivityProbe"
        ) as probe_type:
            probe_type.return_value.start.side_effect = BandCheckAudioError(
                "token=do-not-show /tmp/device"
            )

            dialog._run_primary_action()
            dialog._run_primary_action()

            step = session.step(BandCheckStepKey.AUDIO_INPUT)
            assert step.next_action == "Open System Settings"
            assert "do-not-show" not in step.detail
            dialog._run_primary_action()
            assert opened == [True]
    finally:
        dialog.close()


def test_stale_saved_input_offers_system_input_with_matching_copy() -> None:
    session = _session()
    dialog = _dialog(session)
    try:
        with mock.patch(
            "webjam_qt.windows.ready_check.microphone_permission_status",
            return_value="authorized",
        ), mock.patch(
            "webjam_qt.windows.ready_check.InputActivityProbe"
        ) as probe_type:
            probe_type.return_value.start.side_effect = BandCheckAudioError(
                "device missing"
            )
            dialog._run_primary_action()

        step = session.step(BandCheckStepKey.AUDIO_INPUT)
        assert step.next_action == "Use System Input"
        assert "system input" in step.detail
        assert "Open Settings" not in step.detail
    finally:
        dialog.close()


def test_band_check_wires_microphone_settings_to_controller_opener() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    controller.settings = _settings()
    controller.window = mock.Mock()
    controller.bridge = SimpleNamespace(hosted_server_alive=lambda: False)
    controller._is_jamulus_running = mock.Mock(return_value=False)
    controller._band_check_observations = mock.Mock()
    controller._open_settings_wizard = mock.Mock()
    controller._open_microphone_settings = mock.Mock()
    controller._on_practice_requested = mock.Mock()
    controller._on_save_support_bundle = mock.Mock()

    with mock.patch(
        "webjam_qt.windows.ready_check.BandCheckDialog"
    ) as dialog_type:
        controller._open_band_check()

    dialog_type.return_value.microphone_settings_requested.connect.assert_called_once_with(
        controller._open_microphone_settings
    )
    dialog_type.return_value.recording_settings_requested.connect.assert_called_once_with(
        controller._open_recording_setup
    )
    dialog_type.return_value.system_input_requested.connect.assert_called_once_with(
        controller._use_system_input
    )


def test_scan_failure_never_renders_raw_exception_text() -> None:
    dialog = _dialog(_session())
    secret = "token=do-not-show /Users/alice/private/input.wav"
    try:
        dialog._show_scan_failure(RuntimeError(secret))

        rendered = " ".join(
            label.text() for label in dialog.findChildren(QLabel)
        )
        assert secret not in rendered
        assert "do-not-show" not in rendered
        assert "/Users/alice" not in rendered
        assert "save a Support Bundle" in rendered
    finally:
        dialog.close()


def test_scan_failure_try_again_restarts_the_scan() -> None:
    dialog = _dialog(_session())
    try:
        dialog._show_scan_failure(RuntimeError("backend failed"))
        with mock.patch.object(dialog, "run_checks") as run_checks:
            dialog._primary.click()
        run_checks.assert_called_once_with()
    finally:
        dialog.close()


def test_recovery_actions_open_the_surface_that_can_fix_them() -> None:
    cases = (
        (BandCheckStepKey.AUDIO_INPUT, "Use System Input", "system"),
        (BandCheckStepKey.HEADPHONES, "Recording Setup", "recording"),
        (BandCheckStepKey.RECORDING_PATH, "Recording Setup", "recording"),
        (BandCheckStepKey.WEBEX, "Open Settings", "settings"),
    )
    for key, action, expected in cases:
        session = BandCheckSession(
            BandCheckMode.PRE_SESSION,
            [
                BandCheckStep(
                    key,
                    "Setup needs attention",
                    BandCheckStatus.ACTION_NEEDED,
                    "Choose an available device or folder.",
                    action,
                )
            ],
        )
        dialog = _dialog(session)
        opened: list[str] = []
        dialog.settings_requested.connect(lambda: opened.append("settings"))
        dialog.recording_settings_requested.connect(
            lambda: opened.append("recording")
        )
        dialog.system_input_requested.connect(lambda: opened.append("system"))
        try:
            with mock.patch.object(dialog, "_start_input_check") as input_check, mock.patch.object(
                dialog, "_play_headphone_test"
            ) as headphone_check, mock.patch.object(
                dialog, "_advance_scratch_check"
            ) as scratch_check:
                dialog._run_primary_action()
            assert opened == [expected]
            input_check.assert_not_called()
            headphone_check.assert_not_called()
            scratch_check.assert_not_called()
        finally:
            dialog.close()


def test_live_engine_recovery_closes_report_instead_of_opening_settings() -> None:
    session = BandCheckSession(
        BandCheckMode.LIVE_OBSERVE,
        [
            BandCheckStep(
                BandCheckStepKey.MUSIC_ENGINE,
                "Music engine",
                BandCheckStatus.ACTION_NEEDED,
                "The music engine is not running.",
                "Close Band Check and start the session",
            )
        ],
    )
    dialog = _dialog(session)
    settings_opened: list[bool] = []
    dialog.settings_requested.connect(lambda: settings_opened.append(True))
    try:
        with mock.patch.object(dialog, "close") as close:
            dialog._run_primary_action()
        close.assert_called_once_with()
        assert settings_opened == []
    finally:
        dialog.close()


def test_verification_is_not_saved_across_in_place_settings_change() -> None:
    generation = [0]
    with mock.patch(
        "webjam_qt.windows.ready_check.build_band_check_session",
        return_value=_session(),
    ):
        dialog = BandCheckDialog(
            lambda: _settings(),
            settings_generation_provider=lambda: generation[0],
        )
        dialog._settings_generation = 0
    ready_session = mock.Mock()
    ready_session.outcome = BandCheckOutcome.READY
    dialog._session = ready_session

    def build_changed_signature(*_args, **_kwargs):
        generation[0] += 1
        return mock.sentinel.signature

    try:
        with mock.patch(
            "webjam_qt.windows.ready_check.threading.Thread",
            _ImmediateThread,
        ), mock.patch(
            "webjam_qt.windows.ready_check.build_verification_signature",
            side_effect=build_changed_signature,
        ), mock.patch(
            "webjam_qt.windows.ready_check.save_verification"
        ) as save:
            dialog._persist_verification_if_ready()

        save.assert_not_called()
    finally:
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
