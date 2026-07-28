from __future__ import annotations

import os
import threading
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


def _controller(tmp_path) -> ApplicationController:
    QApplication.instance() or QApplication([])
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Shutdown safety",
    )
    return ApplicationController(
        window,
        settings=AppSettings(
            config_file=str(tmp_path / "settings.json"),
            takes_directory=str(tmp_path / "takes"),
        ),
    )


def test_shutdown_keeps_primary_jam_when_private_peer_stop_is_unproved(
    tmp_path,
) -> None:
    controller = _controller(tmp_path)
    peer = MagicMock()
    peer.stop.side_effect = RuntimeError("still running")
    controller.guest_peer = peer
    controller.bridge.stop_jamulus = MagicMock(return_value=True)

    try:
        with patch.object(QMessageBox, "information") as information:
            assert controller.shutdown() is False

        assert controller._shutdown is False
        assert controller.guest_peer is peer
        assert controller.window.recording_studio._waveform_shutdown is False
        controller.bridge.stop_jamulus.assert_not_called()
        assert information.call_args.args[1] == (
            "Private recording transfer is still stopping"
        )
    finally:
        peer.stop.side_effect = None
        controller.shutdown()


def test_shutdown_keeps_primary_jam_when_pocket_stage_stop_is_unproved(
    tmp_path,
) -> None:
    controller = _controller(tmp_path)
    controller.pocket_stage_gateway._running = True
    controller.bridge.stop_jamulus = MagicMock(return_value=True)

    try:
        with (
            patch.object(
                controller.pocket_stage_gateway,
                "stop",
                side_effect=RuntimeError("still running"),
            ),
            patch.object(QMessageBox, "information") as information,
        ):
            assert controller.shutdown() is False

        assert controller._shutdown is False
        assert controller.window.recording_studio._waveform_shutdown is False
        controller.bridge.stop_jamulus.assert_not_called()
        assert information.call_args.args[1] == "iPhone sharing is still stopping"
    finally:
        controller.pocket_stage_gateway._running = False
        controller._pocket_stage_stop_unresolved = False
        controller._pocket_stage_stopping = False
        controller._pocket_stage_session_end_stop_confirmed = True
        controller.shutdown()


def test_shutdown_remains_retryable_when_primary_jamulus_stop_fails(
    tmp_path,
) -> None:
    controller = _controller(tmp_path)
    controller.bridge.stop_jamulus = MagicMock(return_value=False)
    controller._clear_remote_invite_owner = MagicMock(return_value=True)
    controller._stop_remote_transport = MagicMock(return_value=True)

    try:
        with patch.object(QMessageBox, "information") as information:
            assert controller.shutdown() is False

        assert controller._shutdown is False
        assert controller.window.recording_studio._waveform_shutdown is False
        controller._clear_remote_invite_owner.assert_not_called()
        controller._stop_remote_transport.assert_not_called()
        assert information.call_args.args[1] == (
            "Music connection is still stopping"
        )
    finally:
        controller.bridge.stop_jamulus.return_value = True
        controller.shutdown()


def test_shutdown_does_not_hide_failed_private_transport_cleanup(
    tmp_path,
) -> None:
    controller = _controller(tmp_path)
    controller.bridge.stop_jamulus = MagicMock(return_value=True)
    controller._clear_remote_invite_owner = MagicMock(return_value=True)
    controller._stop_remote_transport = MagicMock(return_value=False)

    try:
        with patch.object(QMessageBox, "information") as information:
            assert controller.shutdown() is False

        assert controller._shutdown is False
        assert controller.window.recording_studio._waveform_shutdown is False
        assert information.call_args.args[1] == (
            "Private connection is still stopping"
        )
    finally:
        controller._stop_remote_transport.return_value = True
        controller.shutdown()


def test_shutdown_does_not_hide_a_live_companion_listener(tmp_path) -> None:
    controller = _controller(tmp_path)
    controller.bridge.stop_jamulus = MagicMock(return_value=True)
    controller._clear_remote_invite_owner = MagicMock(return_value=True)
    controller._stop_remote_transport = MagicMock(return_value=True)
    controller.api_bridge.stop = MagicMock(return_value=False)

    try:
        with patch.object(QMessageBox, "information") as information:
            assert controller.shutdown() is False

        assert controller._shutdown is False
        assert controller.window.recording_studio._waveform_shutdown is False
        assert information.call_args.args[1] == (
            "Companion connection is still stopping"
        )
    finally:
        controller.api_bridge.stop.return_value = True
        controller.shutdown()


def test_shutdown_waits_for_the_existing_session_teardown_owner(tmp_path) -> None:
    controller = _controller(tmp_path)
    controller.audio.stopping = True
    controller._invite_switch_in_flight = True
    controller.bridge.stop_jamulus = MagicMock(return_value=True)
    controller._stop_session_peer = MagicMock(return_value=True)

    try:
        with patch.object(controller.window, "flash_message") as flash:
            assert controller.shutdown() is False

        assert controller._shutdown is False
        assert controller._shutdown_in_progress is False
        assert controller._shutdown_cleanup_pending is False
        controller._stop_session_peer.assert_not_called()
        controller.bridge.stop_jamulus.assert_not_called()
        assert "Session cleanup is still running" in flash.call_args.args[0]
    finally:
        controller.audio.stopping = False
        controller._invite_switch_in_flight = False
        controller.shutdown()


def test_unexpected_shutdown_exception_keeps_explicit_retry_working(
    tmp_path,
) -> None:
    controller = _controller(tmp_path)
    controller.bridge.stop_jamulus = MagicMock(return_value=True)
    controller._clear_remote_invite_owner = MagicMock(
        side_effect=[RuntimeError("unexpected owner failure"), True]
    )
    controller._stop_remote_transport = MagicMock(return_value=True)

    with patch.object(QMessageBox, "information") as information:
        assert controller.shutdown() is False

    assert controller._shutdown is False
    assert controller._shutdown_in_progress is False
    assert controller._shutdown_cleanup_pending is True
    assert controller._confirm_close() is True
    assert information.call_args.args[1] == "WebJam is still finishing cleanup"

    assert controller.shutdown() is True
    assert controller._shutdown is True
    assert controller._shutdown_cleanup_pending is False
    assert controller._clear_remote_invite_owner.call_count == 2


def test_late_shutdown_failure_blocks_new_work_until_quit_retry(tmp_path) -> None:
    controller = _controller(tmp_path)
    controller.bridge.stop_jamulus = MagicMock(return_value=True)
    controller._clear_remote_invite_owner = MagicMock(return_value=True)
    controller._stop_remote_transport = MagicMock(return_value=True)
    controller.api_bridge.stop = MagicMock(return_value=False)

    with patch.object(QMessageBox, "information"):
        assert controller.shutdown() is False

    assert controller._shutdown is False
    assert controller._shutdown_cleanup_pending is True
    assert controller._confirm_close() is True
    assert controller.window.session_strip._audio_button.isEnabled() is False
    assert controller.window.session_strip._tools_button.isEnabled() is False

    controller.begin_startup_journey = MagicMock()
    controller.audio.on_practice_requested = MagicMock()
    controller.recording.on_record_requested = MagicMock()
    controller.bridge.launch_webex = MagicMock()
    controller._reference_track_is_host = MagicMock(return_value=True)
    controller.pocket_stage_gateway.start = MagicMock()

    controller._on_session_audio_requested()
    controller.start_session_or_band_check()
    controller._open_band_check()
    controller._on_practice_requested()
    controller._on_record_requested()
    controller._on_join_video()
    assert controller.accept_invitation(object()) is False
    controller._open_settings_wizard()
    controller._open_recording_setup()
    controller._open_reference_track()
    controller._open_pocket_stage()

    controller.begin_startup_journey.assert_not_called()
    controller.audio.on_practice_requested.assert_not_called()
    controller.recording.on_record_requested.assert_not_called()
    controller.bridge.launch_webex.assert_not_called()
    controller._reference_track_is_host.assert_not_called()
    controller.pocket_stage_gateway.start.assert_not_called()
    assert getattr(controller, "_ready_check_dialog", None) is None

    controller.api_bridge.stop.return_value = True
    assert controller.shutdown() is True
    assert controller._shutdown is True
    assert controller._shutdown_cleanup_pending is False


def test_shutdown_joins_startup_worker_before_host_ownership_snapshot(
    tmp_path,
) -> None:
    controller = _controller(tmp_path)
    cancel_event = threading.Event()
    server_ready = threading.Event()

    def finish_startup_after_cancel() -> None:
        cancel_event.wait()
        server_ready.set()

    worker = threading.Thread(target=finish_startup_after_cancel, daemon=True)
    controller._startup_attempt = {
        "cancel_event": cancel_event,
        "phase": "starting_host",
    }
    controller._startup_host_thread = worker
    controller.bridge.hosted_server_alive = MagicMock(
        side_effect=lambda: server_ready.is_set()
    )
    controller.bridge.hosted_server_owned = MagicMock(return_value=True)
    controller.bridge.stop_hosted_server = MagicMock(
        side_effect=lambda: not server_ready.clear()
    )
    controller.recording.stop_server_recording_for_shutdown = MagicMock(
        return_value=True
    )
    controller.bridge.stop_jamulus = MagicMock(return_value=True)
    controller._clear_remote_invite_owner = MagicMock(return_value=True)
    controller._stop_remote_transport = MagicMock(return_value=True)
    worker.start()

    assert controller.shutdown() is True

    assert cancel_event.is_set()
    assert not worker.is_alive()
    controller.recording.stop_server_recording_for_shutdown.assert_called_once_with()
    controller.bridge.stop_hosted_server.assert_called_once_with()


def test_shutdown_cleans_host_server_created_during_primary_stop(tmp_path) -> None:
    controller = _controller(tmp_path)
    server_ready = threading.Event()
    controller.bridge.hosted_server_alive = MagicMock(
        side_effect=lambda: server_ready.is_set()
    )
    controller.bridge.hosted_server_owned = MagicMock(return_value=True)

    def finish_queued_launch() -> bool:
        server_ready.set()
        return True

    def stop_late_server() -> bool:
        server_ready.clear()
        return True

    controller.bridge.stop_jamulus = MagicMock(side_effect=finish_queued_launch)
    controller.bridge.stop_hosted_server = MagicMock(side_effect=stop_late_server)
    controller.recording.stop_server_recording_for_shutdown = MagicMock(
        return_value=True
    )
    controller._clear_remote_invite_owner = MagicMock(return_value=True)
    controller._stop_remote_transport = MagicMock(return_value=True)

    assert controller.shutdown() is True

    controller.recording.stop_server_recording_for_shutdown.assert_called_once_with()
    controller.bridge.stop_hosted_server.assert_called_once_with()
    assert not server_ready.is_set()
