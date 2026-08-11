from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.band_check import BandCheckMode  # noqa: E402
from core.jamulus_profile import (  # noqa: E402
    StartupAttemptRecord,
    StartupClientPhase,
    StartupConnectionState,
    StartupNextAction,
    StartupRole,
    StartupServerPhase,
    StartupWebexDecision,
)
from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.controllers.audio_coordinator import AudioCoordinator  # noqa: E402
from webjam_qt.controllers.recording_coordinator import (  # noqa: E402
    RecordingCoordinator,
)
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


APP = QApplication.instance() or QApplication([])


def _bare_controller(state: str = "Not launched") -> ApplicationController:
    controller = ApplicationController.__new__(ApplicationController)
    controller.bridge = SimpleNamespace(
        jamulus_state=state,
        hosted_server_alive=mock.Mock(return_value=False),
    )
    return controller


def test_idle_start_and_retry_use_the_band_check_gate() -> None:
    controller = _bare_controller()
    controller.start_session_or_band_check = mock.Mock()
    controller._on_launch_audio = mock.Mock()
    controller.window = SimpleNamespace(flash_message=mock.Mock())

    controller._on_session_audio_requested()
    controller._retry_session()

    assert controller.start_session_or_band_check.call_count == 2
    controller._on_launch_audio.assert_not_called()


def test_live_end_action_never_opens_a_pre_session_gate() -> None:
    controller = _bare_controller("Running")
    controller.start_session_or_band_check = mock.Mock()
    controller._on_launch_audio = mock.Mock()

    controller._on_session_audio_requested()

    controller._on_launch_audio.assert_called_once_with()
    controller.start_session_or_band_check.assert_not_called()


def test_feedback_warning_is_default_safe_and_requires_explicit_override() -> None:
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Feedback Guard",
    )
    controller = ApplicationController.__new__(ApplicationController)
    controller.window = window
    captured: dict[str, object] = {}

    def inspect_and_cancel(box: QMessageBox) -> int:
        start_anyway = box.button(QMessageBox.StandardButton.Yes)
        go_back = box.button(QMessageBox.StandardButton.Cancel)
        captured.update(
            start_text=start_anyway.text(),
            back_text=go_back.text(),
            default_is_back=box.defaultButton() is go_back,
            escape_is_back=box.escapeButton() is go_back,
        )
        return int(QMessageBox.StandardButton.Cancel)

    try:
        with mock.patch.object(QMessageBox, "exec", inspect_and_cancel):
            assert controller._confirm_builtin_audio_feedback_risk() is False
    finally:
        window.close()

    assert captured == {
        "start_text": "Start Anyway",
        "back_text": "Go Back",
        "default_is_back": True,
        "escape_is_back": True,
    }


def test_existing_manual_band_check_is_promoted_without_losing_progress() -> None:
    controller = _bare_controller()
    existing = SimpleNamespace(
        _mode=BandCheckMode.PRE_SESSION,
        _start_session_when_ready=False,
        _settings_generation=0,
        isVisible=mock.Mock(return_value=True),
        session_start_requested=SimpleNamespace(connect=mock.Mock()),
        _refresh_action_button=mock.Mock(),
        _refresh_live_observations=mock.Mock(),
        raise_=mock.Mock(),
        activateWindow=mock.Mock(),
    )
    controller._ready_check_dialog = existing

    controller._open_band_check(start_session_when_ready=True)

    assert existing._start_session_when_ready is True
    callback = existing.session_start_requested.connect.call_args.args[0]
    assert callable(callback)
    existing._refresh_action_button.assert_called_once_with()
    existing.raise_.assert_called_once_with()


def test_stale_band_check_signal_cannot_start_replacement_settings() -> None:
    controller = _bare_controller()
    controller._settings_generation = 2
    controller._shutdown = False
    controller.audio = SimpleNamespace(stopping=False)
    controller._on_launch_audio = mock.Mock()
    controller._open_band_check = mock.Mock()

    controller._start_after_band_check(1)

    controller._on_launch_audio.assert_not_called()
    controller._open_band_check.assert_called_once_with(start_session_when_ready=True)


def test_in_place_setup_change_invalidates_visible_band_check() -> None:
    controller = _bare_controller()
    controller._settings_generation = 4
    dialog = mock.Mock()
    dialog.isVisible.return_value = True
    dialog._start_session_when_ready = True
    controller._ready_check_dialog = dialog

    visible, start_when_ready = controller._invalidate_band_check_evidence()

    assert (visible, start_when_ready) == (True, True)
    assert controller._settings_generation == 5
    dialog.close.assert_called_once_with()


def test_host_server_alone_does_not_turn_retry_gate_into_live_observe() -> None:
    controller = _bare_controller()
    controller.bridge.hosted_server_alive.return_value = True
    controller.settings = SimpleNamespace()
    controller.window = mock.Mock()
    controller._ready_check_dialog = None
    controller._open_settings_wizard = mock.Mock()
    controller._open_microphone_settings = mock.Mock()
    controller._on_practice_requested = mock.Mock()
    controller._on_save_support_bundle = mock.Mock()
    captured: dict[str, object] = {}

    class _Signal:
        def connect(self, _callback) -> None:
            pass

    class _Dialog:
        def __init__(self, *_args, **kwargs) -> None:
            captured.update(kwargs)
            self.settings_requested = _Signal()
            self.recording_settings_requested = _Signal()
            self.system_input_requested = _Signal()
            self.microphone_settings_requested = _Signal()
            self.practice_requested = _Signal()
            self.support_requested = _Signal()
            self.session_start_requested = _Signal()
            self.finished = _Signal()

        def show(self) -> None:
            pass

    with mock.patch("webjam_qt.windows.ready_check.BandCheckDialog", _Dialog):
        controller._open_band_check(start_session_when_ready=True)

    assert captured["mode"] is BandCheckMode.PRE_SESSION
    assert captured["start_session_when_ready"] is True


def test_stale_inflight_verification_never_launches_new_settings() -> None:
    controller = _bare_controller()
    old_settings = SimpleNamespace(config_file="/tmp/old-settings.json")
    new_settings = SimpleNamespace(config_file="/tmp/new-settings.json")
    controller.settings = old_settings
    controller.window = mock.Mock()
    controller._ui_invoker = SimpleNamespace(invoke=lambda callback: callback())
    controller._on_launch_audio = mock.Mock()
    controller._open_band_check = mock.Mock()
    controller._band_check_start_pending = False
    threads = []

    class _DeferredThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target
            threads.append(self)

        def start(self) -> None:
            pass

    saved = mock.Mock()
    saved.matches.return_value = True
    with (
        mock.patch(
            "webjam_qt.controllers.application_controller.threading.Thread",
            _DeferredThread,
        ),
        mock.patch("core.band_check.build_verification_signature"),
        mock.patch("core.band_check.load_verification", return_value=saved),
        mock.patch(
            "core.band_check.verification_path", return_value="/verification.json"
        ),
    ):
        controller.start_session_or_band_check()
        controller.start_session_or_band_check()
        assert len(threads) == 1

        controller.settings = new_settings
        threads[0].target()

    controller._on_launch_audio.assert_not_called()
    assert len(threads) == 2
    assert controller._band_check_start_pending is True


def test_inflight_verification_restarts_after_same_object_setup_change() -> None:
    controller = _bare_controller()
    settings = SimpleNamespace(config_file="/tmp/settings.json")
    controller.settings = settings
    controller._settings_generation = 0
    controller.window = mock.Mock()
    controller._ui_invoker = SimpleNamespace(invoke=lambda callback: callback())
    controller._on_launch_audio = mock.Mock()
    controller._open_band_check = mock.Mock()
    controller._band_check_start_pending = False
    threads = []

    class _DeferredThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target
            threads.append(self)

        def start(self) -> None:
            pass

    saved = mock.Mock()
    saved.matches.return_value = True
    with (
        mock.patch(
            "webjam_qt.controllers.application_controller.threading.Thread",
            _DeferredThread,
        ),
        mock.patch("core.band_check.build_verification_signature"),
        mock.patch("core.band_check.load_verification", return_value=saved),
        mock.patch(
            "core.band_check.verification_path", return_value="/verification.json"
        ),
    ):
        controller.start_session_or_band_check()
        controller._settings_generation += 1
        threads[0].target()

    controller._on_launch_audio.assert_not_called()
    assert len(threads) == 2
    assert controller._band_check_start_pending is True


def test_matching_saved_verification_never_bypasses_v3_host_or_guest_path() -> None:
    class _ImmediateThread:
        def __init__(self, *, target, **_kwargs) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    for role, stage in (("host", "prepared"), ("guest", "connected")):
        controller = _bare_controller()
        controller.settings = AppSettings(host_server_enabled=role == "host")
        controller.window = mock.Mock()
        controller._ui_invoker = SimpleNamespace(invoke=lambda callback: callback())
        controller._on_launch_audio = mock.Mock()
        controller._open_band_check = mock.Mock()
        controller._begin_remote_host = mock.Mock()
        controller._band_check_start_pending = False
        controller._settings_generation = 0
        controller._shutdown = False
        controller._remote_invitation = None
        controller._remote_invite_owner = object() if role == "host" else None
        controller._remote_session = (
            controller._remote_invite_owner if role == "host" else object()
        )
        controller._remote_band_check_token = (
            role,
            1,
            "secure_relay",
            stage,
        )
        controller._remote_band_check_completed_token = None
        controller.guest_peer = None
        controller._guest_invite = None
        saved = mock.Mock()
        saved.matches.return_value = True

        with (
            mock.patch(
                "webjam_qt.controllers.application_controller.threading.Thread",
                _ImmediateThread,
            ),
            mock.patch(
                "core.band_check.build_verification_signature",
                return_value=mock.sentinel.signature,
            ),
            mock.patch(
                "core.band_check.load_verification",
                return_value=saved,
            ),
            mock.patch(
                "core.band_check.verification_path",
                return_value="/verification.json",
            ),
        ):
            controller.start_session_or_band_check()

        saved.matches.assert_not_called()
        controller._on_launch_audio.assert_not_called()
        controller._open_band_check.assert_called_once_with(
            start_session_when_ready=True
        )

        controller._start_after_band_check(controller._settings_generation)

        assert not controller._remote_band_check_required()
        controller._on_launch_audio.assert_called_once_with()


def test_v2_guest_peer_waits_for_post_gate_audio_start(tmp_path) -> None:
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        local_capture_enabled=True,
        input_maps=[
            {
                "name": "Guest Voice",
                "channels": 1,
                "enabled": True,
                "local_original_enabled": True,
            }
        ],
    )
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Guest Gate",
    )
    guest = mock.Mock()
    guest.originals_root = tmp_path
    guest.recovered_captures = ()
    restarted_guest = mock.Mock()
    restarted_guest.originals_root = tmp_path
    restarted_guest.recovered_captures = ()
    invite = SimpleNamespace(peer_enabled=True)

    with mock.patch(
        "webjam_qt.controllers.application_controller.GuestPeerSession",
        return_value=guest,
    ) as guest_type:
        controller = ApplicationController(
            window,
            settings=settings,
            session_invite=invite,
        )
    try:
        peer_kwargs = guest_type.call_args.kwargs
        assert peer_kwargs["capture_enabled"]() is True
        assert peer_kwargs["capture_tracks"]() == (("local-Guest Voice", 0),)
        guest.start.assert_not_called()
        controller.audio.on_launch_toggle = mock.Mock(return_value=True)
        with mock.patch.object(
            controller,
            "_feedback_guard_allows_audio_start",
            return_value=False,
        ):
            controller._on_launch_audio()
        controller.audio.on_launch_toggle.assert_not_called()
        guest.start.assert_not_called()

        events: list[str] = []
        guest.start.side_effect = lambda: events.append("peer")

        def _launch_audio() -> bool:
            events.append("audio")
            return True

        controller.audio.on_launch_toggle = mock.Mock(side_effect=_launch_audio)

        controller._on_launch_audio()

        assert events == ["audio", "peer"]

        # The low-level cleanup helper may retain an active invite while a
        # settings repair rebuilds the peer. The successful Leave path uses
        # clear_invite=True and is covered separately below.
        controller._stop_session_peer()
        assert controller.guest_peer is None
        assert controller._guest_invite is invite

        with mock.patch(
            "webjam_qt.controllers.application_controller.GuestPeerSession",
            return_value=restarted_guest,
        ):
            controller._on_launch_audio()
        restarted_guest.start.assert_called_once_with()
        assert controller.guest_peer is restarted_guest
    finally:
        controller.shutdown()


def test_successful_leave_finishes_ui_after_worker_owned_private_cleanup() -> None:
    controller = _bare_controller()
    controller.window = SimpleNamespace(
        session_strip=SimpleNamespace(
            reset_session_clock=mock.Mock(),
            set_tools_enabled=mock.Mock(),
        ),
        session_hud=SimpleNamespace(set_state=mock.Mock()),
    )
    controller.recording = SimpleNamespace(on_audio_session_stopped=mock.Mock())
    controller._stop_session_peer = mock.Mock(return_value=True)
    controller._clear_remote_invite_owner = mock.Mock(return_value=True)
    controller._stop_remote_transport = mock.Mock(return_value=True)
    controller._transition_lifecycle = mock.Mock()
    controller.audio = AudioCoordinator(controller)
    controller.audio.reset_to_idle = mock.Mock()

    controller.audio._finish_session_stop_ui()

    # Private owners are stopped and checked in the worker before the primary
    # music client. The UI completion callback must not run that teardown a
    # second time or discard its result.
    controller._stop_session_peer.assert_not_called()
    controller.audio.reset_to_idle.assert_called_once_with()


def test_permission_blocked_launch_does_not_start_guest_peer() -> None:
    controller = _bare_controller()
    controller.guest_peer = mock.Mock()
    controller.window = SimpleNamespace(
        participant_grid=SimpleNamespace(set_session_state=mock.Mock()),
        session_hud=SimpleNamespace(set_state=mock.Mock()),
        session_strip=SimpleNamespace(set_tools_enabled=mock.Mock()),
    )
    controller.audio = AudioCoordinator(controller)

    with mock.patch(
        "webjam_qt.platform_permissions.microphone_permission_status",
        return_value="not_determined",
    ):
        controller._on_launch_audio()

    assert controller.audio.permission_explained is True
    controller.guest_peer.start.assert_not_called()


def test_synchronously_rejected_audio_launch_does_not_start_timer_or_peer() -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(host_server_enabled=False)
    controller._is_jamulus_running = mock.Mock(return_value=False)
    controller.bridge.effective_server = mock.Mock(return_value="band.test:22124")
    controller.bridge.launch_jamulus = mock.Mock(return_value=False)
    controller.window = mock.Mock()
    controller._connection_timer = mock.Mock()
    controller.guest_peer = mock.Mock()
    controller.audio = AudioCoordinator(controller)

    with mock.patch(
        "webjam_qt.platform_permissions.microphone_permission_status",
        return_value="authorized",
    ):
        controller._on_launch_audio()

    controller.bridge.launch_jamulus.assert_called_once_with(manual=True)
    controller._connection_timer.start.assert_not_called()
    controller.guest_peer.start.assert_not_called()


def test_v1_effective_band_check_ignores_saved_capture_opt_in() -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        host_server_enabled=False,
        local_capture_enabled=True,
    )
    controller.guest_peer = None

    effective = controller._effective_band_check_settings()

    assert effective.local_capture_enabled is False
    assert controller.settings.local_capture_enabled is True


def test_host_or_v2_effective_band_check_keeps_capture_opt_in() -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        host_server_enabled=True,
        local_capture_enabled=True,
    )
    controller.guest_peer = None
    assert controller._effective_band_check_settings().local_capture_enabled is True

    controller.settings.host_server_enabled = False
    controller.guest_peer = mock.Mock()
    assert controller._effective_band_check_settings().local_capture_enabled is True


def test_v1_recording_setup_never_claims_local_originals() -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        host_server_enabled=False,
        local_capture_enabled=True,
    )
    controller.guest_peer = None
    controller.host_peer = SimpleNamespace(active=False)
    controller.participants = {}
    controller.window = SimpleNamespace(
        recording_studio=SimpleNamespace(
            set_output_device=mock.Mock(),
            set_takes_directory=mock.Mock(),
        ),
        flash_message=mock.Mock(),
    )
    controller._sync_local_originals_action = mock.Mock()
    controller._invalidate_band_check_evidence = mock.Mock(return_value=(False, False))
    controller._reopen_invalidated_band_check = mock.Mock()
    controller._reconfigure_services_after_settings = mock.Mock()
    committed = AppSettings(
        host_server_enabled=False,
        local_capture_enabled=True,
        take_playback_output_device="Committed Output",
        takes_directory="/committed/takes",
    )

    with (
        mock.patch(
            "webjam_qt.windows.recording_setup.RecordingSetupDialog"
        ) as dialog_type,
        mock.patch("core.settings.load_settings", return_value=committed),
    ):
        dialog_type.return_value.exec.return_value = dialog_type.DialogCode.Accepted
        controller._open_recording_setup()

    assert dialog_type.call_args.kwargs["local_originals_available"] is False
    assert dialog_type.call_args.kwargs["takes_folder_editable"] is True
    assert controller.settings is committed
    controller._reconfigure_services_after_settings.assert_called_once()
    controller.window.recording_studio.set_output_device.assert_called_once_with(
        "Committed Output"
    )
    controller.window.recording_studio.set_takes_directory.assert_called_once_with(
        "/committed/takes"
    )
    controller._sync_local_originals_action.assert_called_once_with()
    message = controller.window.flash_message.call_args.args[0]
    assert "this session" in message
    assert "local originals are unavailable" in message


def test_first_host_record_can_start_shared_take_without_local_originals(
    tmp_path,
) -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
    )
    controller.window = SimpleNamespace(flash_message=mock.Mock())
    controller.recording = SimpleNamespace(on_record_requested=mock.Mock())

    with mock.patch(
        "webjam_qt.windows.recording_setup.LocalOriginalsChoiceDialog"
    ) as choice_type:
        choice = choice_type.return_value
        choice.exec.return_value = choice_type.DialogCode.Accepted
        choice.choice = "shared"

        controller._on_record_requested()

    assert controller.settings.local_capture_choice_made is True
    assert controller.settings.local_capture_enabled is False
    controller.recording.on_record_requested.assert_called_once_with()
    from core.settings import load_settings

    saved = load_settings(controller.settings.config_file)
    assert saved.local_capture_choice_made is True
    assert saved.local_capture_enabled is False


def test_first_host_record_opens_local_original_setup_only_when_requested(
    tmp_path,
) -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
    )
    controller.window = SimpleNamespace(flash_message=mock.Mock())
    controller.recording = SimpleNamespace(on_record_requested=mock.Mock())
    controller._open_recording_setup = mock.Mock()

    with mock.patch(
        "webjam_qt.windows.recording_setup.LocalOriginalsChoiceDialog"
    ) as choice_type:
        choice = choice_type.return_value
        choice.exec.return_value = choice_type.DialogCode.Accepted
        choice.choice = "local"

        controller._on_record_requested()

    assert controller.settings.local_capture_choice_made is True
    controller._open_recording_setup.assert_called_once_with()
    controller.recording.on_record_requested.assert_not_called()


def test_saved_local_original_preference_never_interrupts_later_host_take(
    tmp_path,
) -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        local_capture_enabled=True,
    )
    controller.window = SimpleNamespace(flash_message=mock.Mock())
    controller.recording = SimpleNamespace(on_record_requested=mock.Mock())

    with mock.patch(
        "webjam_qt.windows.recording_setup.LocalOriginalsChoiceDialog"
    ) as choice_type:
        controller._on_record_requested()

    choice_type.assert_not_called()
    controller.recording.on_record_requested.assert_called_once_with()


def test_record_session_starts_ready_shared_track_after_recorder_confirmation(
    tmp_path,
) -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        local_capture_choice_made=True,
    )
    controller.window = SimpleNamespace(
        recording_studio=SimpleNamespace(export_in_progress=False),
        flash_message=mock.Mock(),
    )
    controller.recording = SimpleNamespace(
        phase=SimpleNamespace(value="idle"),
        on_record_requested=mock.Mock(),
    )
    controller._recorder_armed = False
    controller._server_recording = False
    controller._shared_track_play_after_recording = ""
    controller._reference_track = SimpleNamespace(
        snapshot=SimpleNamespace(
            state=SimpleNamespace(value="ready"),
            can_play=True,
            active=False,
        )
    )
    controller._play_reference_track = mock.Mock()
    controller._request_reference_track_teardown = mock.Mock()
    controller._update_session_hud = mock.Mock()
    controller._shutdown_cleanup_blocks_action = mock.Mock(return_value=False)

    controller._on_record_requested()

    assert controller._shared_track_play_after_recording == "play"
    controller.recording.on_record_requested.assert_called_once_with()
    controller._play_reference_track.assert_not_called()

    controller.recording.phase.value = "recording"
    controller._on_recorder_phase_changed("recording")
    controller._play_reference_track.assert_called_once_with()
    assert controller._shared_track_play_after_recording == ""


def test_stop_recording_also_requests_independent_shared_track_teardown(
    tmp_path,
) -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        local_capture_choice_made=True,
    )
    controller.window = SimpleNamespace(
        recording_studio=SimpleNamespace(export_in_progress=False),
        flash_message=mock.Mock(),
    )
    controller.recording = SimpleNamespace(
        phase=SimpleNamespace(value="recording"),
        on_record_requested=mock.Mock(),
    )
    controller._recorder_armed = True
    controller._server_recording = True
    controller._shared_track_play_after_recording = "play"
    controller._reference_track = SimpleNamespace(
        snapshot=SimpleNamespace(active=True)
    )
    controller._request_reference_track_teardown = mock.Mock()
    controller._shutdown_cleanup_blocks_action = mock.Mock(return_value=False)

    controller._on_record_requested()

    controller._request_reference_track_teardown.assert_called_once_with()
    controller.recording.on_record_requested.assert_called_once_with()
    assert controller._shared_track_play_after_recording == ""


def test_record_request_is_blocked_while_studio_export_is_running(tmp_path) -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
    )
    controller.window = SimpleNamespace(
        recording_studio=SimpleNamespace(export_in_progress=True),
        flash_message=mock.Mock(),
    )
    controller.recording = SimpleNamespace(on_record_requested=mock.Mock())

    with mock.patch(
        "webjam_qt.windows.recording_setup.LocalOriginalsChoiceDialog"
    ) as choice_type:
        controller._on_record_requested()

    choice_type.assert_not_called()
    controller.recording.on_record_requested.assert_not_called()
    assert "export" in controller.window.flash_message.call_args.args[0].lower()


def test_recording_retry_is_blocked_if_export_started_after_prompt() -> None:
    flash_message = mock.Mock()
    coordinator = RecordingCoordinator.__new__(RecordingCoordinator)
    coordinator._c = SimpleNamespace(
        window=SimpleNamespace(
            recording_studio=SimpleNamespace(export_in_progress=True),
            flash_message=flash_message,
        )
    )

    coordinator.on_record_requested()

    assert "export" in flash_message.call_args.args[0].lower()


def test_matching_recovery_only_restores_the_next_safe_native_prompt() -> None:
    controller = _bare_controller()
    record = StartupAttemptRecord.new(
        generation=4,
        role=StartupRole.GUEST,
        server_phase=StartupServerPhase.NOT_REQUIRED,
        client_phase=StartupClientPhase.VERIFYING,
        profile_fingerprint="a" * 64,
        connection_state=StartupConnectionState.CONNECTED,
        human_confirmed=False,
        webex_decision=StartupWebexDecision.SKIPPED,
        next_action=StartupNextAction.CONFIRM_AUDIBLE,
        entropy=b"recovery-test",
    )
    attempt = {
        "role": "guest",
        "setup_finished": False,
        "human_confirmed": False,
        "fast_path": False,
        "webex_decision": None,
        "recovery_record": record,
    }

    controller._apply_matching_startup_recovery(
        attempt,
        SimpleNamespace(profile_fingerprint="a" * 64),
    )

    assert attempt["setup_finished"] is True
    assert attempt["human_confirmed"] is False
    assert attempt["fast_path"] is False
    assert attempt["webex_decision"] == "skipped"
    assert attempt["resumed"] is True


def test_startup_conversation_guidance_is_provider_neutral() -> None:
    prompt = ApplicationController._startup_guidance_override(
        {"phase": "conversation", "role": "guest"}
    )
    link = ApplicationController._startup_guidance_override(
        {"phase": "conversation_link", "role": "guest"}
    )

    assert prompt.action_label == "Add Conversation"
    assert link.title == "Add Meeting Link"
    assert link.action_label == "Save Meeting Link"
    assert "public HTTPS meeting link from any platform" in link.message


def test_changed_profile_fails_closed_to_native_setup_after_restart() -> None:
    controller = _bare_controller()
    record = StartupAttemptRecord.new(
        generation=4,
        role=StartupRole.HOST,
        server_phase=StartupServerPhase.READY,
        client_phase=StartupClientPhase.READY,
        profile_fingerprint="a" * 64,
        connection_state=StartupConnectionState.CONNECTED,
        human_confirmed=True,
        webex_decision=StartupWebexDecision.SKIPPED,
        next_action=StartupNextAction.COPY_INVITE,
        entropy=b"changed-profile-test",
    )
    attempt = {
        "role": "host",
        "setup_finished": False,
        "human_confirmed": False,
        "fast_path": False,
        "webex_decision": None,
        "recovery_record": record,
    }

    controller._apply_matching_startup_recovery(
        attempt,
        SimpleNamespace(profile_fingerprint="b" * 64),
    )

    assert attempt["setup_finished"] is False
    assert attempt["human_confirmed"] is False
    assert attempt["fast_path"] is False
    assert "resumed" not in attempt


def test_pre_session_takes_change_rebuilds_v2_guest_peer(tmp_path) -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        takes_directory=str(tmp_path / "old-takes"),
    )
    invite = SimpleNamespace(peer_enabled=True)
    controller._guest_invite = invite
    controller._guest_peer_configuration_failed = True
    controller.guest_peer = None
    controller.host_peer = SimpleNamespace(active=False)
    controller.participants = {}
    controller._is_jamulus_running = mock.Mock(return_value=False)
    controller.window = SimpleNamespace(
        recording_studio=SimpleNamespace(
            set_output_device=mock.Mock(),
            set_takes_directory=mock.Mock(),
        ),
        flash_message=mock.Mock(),
    )
    controller._sync_local_originals_action = mock.Mock()
    controller._invalidate_band_check_evidence = mock.Mock(return_value=(False, False))
    controller._reopen_invalidated_band_check = mock.Mock()
    controller._reconfigure_services_after_settings = mock.Mock()
    controller._configure_guest_peer = mock.Mock()
    committed = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        takes_directory=str(tmp_path / "new-takes"),
    )

    with (
        mock.patch(
            "webjam_qt.windows.recording_setup.RecordingSetupDialog"
        ) as dialog_type,
        mock.patch("core.settings.load_settings", return_value=committed),
    ):
        dialog_type.return_value.exec.return_value = dialog_type.DialogCode.Accepted
        controller._open_recording_setup()

    assert dialog_type.call_args.kwargs["takes_folder_editable"] is True
    controller._configure_guest_peer.assert_called_once_with(invite)
    controller.window.recording_studio.set_takes_directory.assert_called_once_with(
        str(tmp_path / "new-takes")
    )


def test_failed_v2_peer_configuration_retains_invite_for_folder_repair(
    tmp_path,
) -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        takes_directory=str(tmp_path / "missing"),
    )
    controller.window = SimpleNamespace(flash_message=mock.Mock())
    controller.host_peer = mock.Mock()
    controller.host_peer.active = False
    controller._guest_invite = None
    controller._guest_peer_configuration_failed = False
    controller.guest_peer = None
    invite = SimpleNamespace(peer_enabled=True)

    with mock.patch(
        "webjam_qt.controllers.application_controller.GuestPeerSession",
        side_effect=OSError("queue path unavailable"),
    ):
        controller._configure_guest_peer(invite)

    assert controller.guest_peer is None
    assert controller._guest_invite is invite
    assert controller._guest_peer_configuration_failed is True
    assert controller._local_originals_available() is False


def test_v2_peer_replacement_retains_old_owner_when_stop_is_unconfirmed() -> None:
    controller = _bare_controller()
    old_peer = mock.Mock()
    old_peer.stop.return_value = False
    old_invite = mock.sentinel.old_invite
    controller.guest_peer = old_peer
    controller.host_peer = None
    controller._guest_invite = old_invite
    controller._guest_peer_configuration_failed = True

    with mock.patch(
        "webjam_qt.controllers.application_controller.GuestPeerSession"
    ) as guest_type:
        configured = controller._configure_guest_peer(mock.sentinel.new_invite)

    assert configured is False
    old_peer.stop.assert_called_once_with()
    guest_type.assert_not_called()
    assert controller.guest_peer is old_peer
    assert controller._guest_invite is old_invite
    assert controller._guest_peer_configuration_failed is True


def test_failed_inline_output_save_restores_live_setting() -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(take_playback_output_device="Old Output")
    controller.window = SimpleNamespace(
        recording_studio=SimpleNamespace(set_output_device=mock.Mock()),
        flash_message=mock.Mock(),
    )
    controller._invalidate_band_check_evidence = mock.Mock()

    with mock.patch(
        "core.settings.save_settings",
        side_effect=OSError("token=do-not-show /tmp/settings"),
    ):
        controller._save_take_playback_output("New Output")

    assert controller.settings.take_playback_output_device == "Old Output"
    controller.window.recording_studio.set_output_device.assert_called_once_with(
        "Old Output"
    )
    controller._invalidate_band_check_evidence.assert_not_called()
    assert "do-not-show" not in controller.window.flash_message.call_args.args[0]


def test_legacy_system_input_action_foregrounds_jamulus_without_mutation(
    tmp_path,
) -> None:
    controller = _bare_controller()
    controller.settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        audio_input_device_index=7,
    )
    controller._bring_jamulus_forward = mock.Mock()

    controller._use_system_input()

    assert controller.settings.audio_input_device_index == 7
    controller._bring_jamulus_forward.assert_called_once_with()


def test_actionable_error_never_renders_paths_or_secrets() -> None:
    controller = _bare_controller()
    controller.window = mock.Mock()
    secret = "token=do-not-show /Users/alice/private/device.log"

    with mock.patch(
        "webjam_qt.controllers.application_controller.QMessageBox"
    ) as box_type:
        box = box_type.return_value
        box.clickedButton.return_value = None
        controller._show_actionable_error(
            secret,
            what_failed=secret,
            likely_cause=secret,
            next_action=secret,
        )

    rendered = " ".join(
        call.args[0]
        for method in (
            box.setText,
            box.setInformativeText,
            box.setDetailedText,
        )
        for call in method.call_args_list
    )
    assert "do-not-show" not in rendered
    assert "/Users/alice" not in rendered
    assert "Save Support Bundle" in rendered
