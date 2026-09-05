"""Art End/Leave retires the same owners without inventing Music work."""

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from PySide6.QtWidgets import QMessageBox

from core.session_lifecycle import SessionLifecyclePhase
from webjam_qt.controllers.audio_coordinator import AudioCoordinator
from webjam_qt.session_state import SessionPhase


@pytest.fixture
def room_cleanup(monkeypatch):
    """Control worker dispatch while exercising real cleanup and retry methods."""

    workers = []

    class QueuedThread:
        def __init__(self, *, target, args, daemon, name):
            self.target = target
            self.args = args
            assert daemon is True
            assert name == "webjam-session-stop"

        def start(self):
            workers.append(self)

        def run(self):
            self.target(*self.args)

    monkeypatch.setattr(
        "webjam_qt.controllers.audio_coordinator.threading.Thread", QueuedThread
    )
    question = Mock(return_value=QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "question", question)
    bridge = SimpleNamespace(
        jamulus_process=None,
        hosted_server_process=None,
        _hosted_runtime_paths=None,
        _hosted_restart_inflight=False,
        hosted_server_alive=Mock(return_value=False),
        _runtime_component_lifecycle_is_active=Mock(return_value=False),
        stop_jamulus=Mock(return_value=True),
        stop_hosted_server=Mock(return_value=True),
    )
    recording = SimpleNamespace(
        is_recording_active=False,
        take_in_progress=False,
        _local_capture=None,
        _validation_take_id="",
        _shutdown_validation_pending_take_id="",
        _shutdown_validation_dispatch_take_id="",
        stop_server_recording_for_shutdown=Mock(return_value=True),
        on_audio_session_stopped=Mock(),
    )
    controller = SimpleNamespace(
        bridge=bridge,
        recording=recording,
        settings=SimpleNamespace(host_server_enabled=False, webex_url=""),
        window=MagicMock(),
        _art_room_active=Mock(return_value=True),
        _art_room_role="guest",
        _server_recording=False,
        _recorder_armed=False,
        _is_jamulus_running=Mock(return_value=False),
        _clear_primary_local_roster_proof=Mock(),
        _prepare_pocket_stage_for_session_end=Mock(),
        _complete_pocket_stage_session_end=Mock(),
        _stop_pocket_stage_for_session_end=Mock(return_value=True),
        participants={},
        _push_participants_to_grid=Mock(),
        _transition_lifecycle=Mock(),
        _sync_reference_track_primary_gate=Mock(),
        _level_timer=Mock(),
        _connection_timer=Mock(),
        _sync_self_mute_button=Mock(),
        _stop_reference_track_for_session_end=Mock(return_value=True),
        _stop_session_peer=Mock(return_value=True),
        _clear_remote_invite_owner=Mock(return_value=True),
        _stop_remote_transport=Mock(return_value=True),
        _ui_invoker=SimpleNamespace(invoke=lambda callback: callback()),
        _remote_route_base_settings=None,
        _finish_art_room_profile_restore=Mock(),
    )
    audio = AudioCoordinator(controller)
    controller.audio = audio
    # Idle rendering belongs to ApplicationController integration. Keep all
    # worker, lifecycle, recorder and failed-owner transitions real here.
    audio.reset_to_idle = Mock()
    return controller, workers, question


@pytest.mark.parametrize("role", ["host", "guest"])
def test_room_confirmation_uses_owned_role_and_preserves_external_apps(
    room_cleanup, role
):
    controller, workers, question = room_cleanup
    controller._art_room_role = role
    controller.settings.host_server_enabled = role != "host"
    controller.settings.webex_url = "https://example.webex.com/meet/artist"
    question.return_value = QMessageBox.StandardButton.No

    controller.audio.stop()

    title, body = question.call_args.args[1:3]
    assert title == ("End Room?" if role == "host" else "Leave Room?")
    assert "for everyone" in body if role == "host" else "stay connected" in body
    assert "Webex meeting and external canvas stay open" in body
    assert "recording" not in body.lower()
    assert "audio" not in body.lower()
    assert workers == []
    controller._stop_session_peer.assert_not_called()


@pytest.mark.parametrize("role", ["host", "guest"])
def test_pure_art_uses_one_cleanup_worker_without_recorder_finalization(
    room_cleanup, role
):
    controller, workers, question = room_cleanup
    controller._art_room_role = role
    controller.audio.stop()
    controller.audio.stop()

    assert len(workers) == 1
    question.assert_called_once()
    progress = controller.window.participant_grid.set_session_state.call_args.args[0]
    assert progress.phase is SessionPhase.ENDING
    assert progress.title == (
        "Ending this room…" if role == "host" else "Leaving the room…"
    )
    assert not progress.primary_enabled
    assert not progress.show_ready_check
    assert "recording" not in progress.message.lower()
    assert "audio" not in progress.message.lower()

    workers.pop().run()

    controller.recording.stop_server_recording_for_shutdown.assert_not_called()
    controller.recording.on_audio_session_stopped.assert_not_called()
    phases = [call.args[0] for call in controller._transition_lifecycle.call_args_list]
    assert SessionLifecyclePhase.FINALIZING_RECORDINGS not in phases
    assert phases[-1] is SessionLifecyclePhase.COMPLETED
    controller._stop_reference_track_for_session_end.assert_called_once_with(
        background=False
    )
    controller._stop_session_peer.assert_called_once_with(clear_invite=True)
    controller._clear_remote_invite_owner.assert_called_once_with()
    controller._stop_remote_transport.assert_called_once_with(restore_route=False)
    controller.audio.reset_to_idle.assert_called_once_with()
    controller._finish_art_room_profile_restore.assert_called_once_with()
    assert controller.audio.ended_by_user
    assert not controller.audio.cleanup_retry_required
    assert not controller.audio.stopping
    controller.window.webex_embed.close.assert_not_called()
    controller.window.shared_canvas.close.assert_not_called()


def test_art_guest_retains_residual_host_server_until_recorder_and_stop_confirm(
    room_cleanup,
):
    controller, workers, _ = room_cleanup
    owned_server = object()
    controller.bridge.hosted_server_process = owned_server
    controller.recording.stop_server_recording_for_shutdown.return_value = False
    controller.audio.stop()
    workers.pop().run()

    controller.recording.stop_server_recording_for_shutdown.assert_called_once_with()
    controller.bridge.stop_hosted_server.assert_not_called()
    controller.bridge.stop_jamulus.assert_not_called()
    controller._stop_session_peer.assert_not_called()
    assert controller.bridge.hosted_server_process is owned_server
    assert controller.audio.cleanup_retry_required
    assert controller.audio._stop_hosting is False
    assert controller.window.session_strip.set_audio_state.call_args.args[0] == (
        "Try Leave Room"
    )
    controller.audio.reset_to_idle.assert_not_called()
    controller._finish_art_room_profile_restore.assert_not_called()

    controller.recording.stop_server_recording_for_shutdown.return_value = True
    controller.bridge.stop_hosted_server.return_value = False
    controller.audio.retry_stop()
    workers.pop().run()

    controller.bridge.stop_hosted_server.assert_called_once_with()
    assert controller.bridge.hosted_server_process is owned_server
    assert controller.audio.cleanup_retry_required
    controller._clear_remote_invite_owner.assert_not_called()
    controller._stop_remote_transport.assert_not_called()
    controller._finish_art_room_profile_restore.assert_not_called()

    def release_server():
        controller.bridge.hosted_server_process = None
        return True

    controller.bridge.stop_hosted_server.side_effect = release_server
    controller.audio.retry_stop()
    workers.pop().run()

    assert controller.bridge.hosted_server_process is None
    assert not controller.audio.cleanup_retry_required
    controller.recording.on_audio_session_stopped.assert_called_once_with()
    controller._finish_art_room_profile_restore.assert_called_once_with()
    controller.audio.reset_to_idle.assert_called_once_with()


@pytest.mark.parametrize(
    "evidence",
    ["pending_validation", "live_client", "unknown_lifecycle", "unknown_server"],
)
def test_art_does_not_skip_unresolved_audio_or_recording_ownership(
    room_cleanup, evidence
):
    controller, workers, _ = room_cleanup
    controller._art_room_role = "host"
    if evidence == "pending_validation":
        controller.recording._validation_take_id = "pending-take"
    elif evidence == "live_client":
        controller.bridge._runtime_component_lifecycle_is_active.return_value = True
    elif evidence == "unknown_lifecycle":
        controller.bridge._runtime_component_lifecycle_is_active.side_effect = (
            RuntimeError("ownership unavailable")
        )
    else:
        controller.bridge.hosted_server_alive.side_effect = RuntimeError(
            "ownership unavailable"
        )
    controller.recording.stop_server_recording_for_shutdown.return_value = False

    controller.audio.stop()
    workers.pop().run()

    controller.recording.stop_server_recording_for_shutdown.assert_called_once_with()
    assert controller.audio.cleanup_retry_required
    controller._stop_session_peer.assert_not_called()
    controller.bridge.stop_jamulus.assert_not_called()
    controller.audio.reset_to_idle.assert_not_called()


def test_room_retry_survives_guest_profile_and_settings_restoration(room_cleanup):
    controller, workers, question = room_cleanup
    transport = object()
    controller._remote_session = transport
    stop_results = iter((False, True))

    def stop_peer(*, clear_invite):
        assert clear_invite
        controller._art_room_active.return_value = False
        controller._art_room_role = ""
        controller.settings.host_server_enabled = True
        return True

    def stop_transport(*, restore_route):
        assert not restore_route
        if next(stop_results):
            controller._remote_session = None
            return True
        return False

    controller._stop_session_peer.side_effect = stop_peer
    controller._stop_remote_transport.side_effect = stop_transport
    controller.audio.stop()
    workers.pop().run()

    assert controller._remote_session is transport
    assert controller.audio.cleanup_retry_required
    assert controller.audio._stop_art_room
    assert controller.audio._stop_hosting is False
    assert controller.window.session_strip.set_audio_state.call_args.args[0] == (
        "Try Leave Room"
    )
    failed = controller.window.participant_grid.set_session_state.call_args.args[0]
    assert "Try Leave Room" in failed.message
    assert "jam" not in failed.message.lower()
    assert "Try Leave Room" in controller.window.session_hud.set_state.call_args.args[1]
    controller.audio.reset_to_idle.assert_not_called()

    controller.audio.stop()
    assert workers[-1].args == (False,)
    progress = controller.window.participant_grid.set_session_state.call_args.args[0]
    assert progress.title == "Leaving the room…"
    workers.pop().run()

    question.assert_called_once()
    assert controller._remote_session is None
    assert not controller.audio.cleanup_retry_required
    controller.audio.reset_to_idle.assert_called_once_with()
    controller.recording.stop_server_recording_for_shutdown.assert_not_called()


def test_art_guest_with_hosted_take_cannot_end_before_take_saved(
    room_cleanup, monkeypatch
):
    controller, workers, question = room_cleanup
    controller.bridge.hosted_server_process = object()
    controller.recording.is_recording_active = True
    controller.recording.take_in_progress = True
    information = Mock()
    monkeypatch.setattr(QMessageBox, "information", information)

    controller.audio.stop()

    assert "leaving the room" in information.call_args.args[2]
    assert "Take saved" in information.call_args.args[2]
    question.assert_not_called()
    assert not workers
    controller.bridge.stop_hosted_server.assert_not_called()


def test_music_host_keeps_existing_confirmation_and_recorder_gate(room_cleanup):
    controller, workers, question = room_cleanup
    controller._art_room_active.return_value = False
    controller.settings.host_server_enabled = True
    controller.audio.stop()
    assert question.call_args.args[1] == "End Jam?"
    assert "finish any recording" in question.call_args.args[2]

    workers.pop().run()

    controller.recording.stop_server_recording_for_shutdown.assert_called_once_with()
    controller.bridge.stop_hosted_server.assert_called_once_with()
    controller.recording.on_audio_session_stopped.assert_called_once_with()
    controller.audio.reset_to_idle.assert_called_once_with()


def test_failed_saved_profile_restore_remains_retryable_after_services_stop(room_cleanup):
    controller, workers, _ = room_cleanup
    controller._finish_art_room_profile_restore.side_effect = RuntimeError("unavailable")

    controller.audio.stop()
    workers.pop().run()

    assert controller.audio.cleanup_retry_required
    controller.audio.reset_to_idle.assert_not_called()
    phases = [call.args[0] for call in controller._transition_lifecycle.call_args_list]
    assert SessionLifecyclePhase.COMPLETED not in phases
    controller._complete_pocket_stage_session_end.assert_called_once_with(succeeded=False)

    controller._finish_art_room_profile_restore.side_effect = None
    controller.audio.retry_stop()
    workers.pop().run()

    assert not controller.audio.cleanup_retry_required
    controller.audio.reset_to_idle.assert_called_once_with()


def test_art_retains_capture_retirement_after_later_transport_failure(room_cleanup):
    controller, workers, _ = room_cleanup
    controller.recording._local_capture = object()
    controller._stop_remote_transport.side_effect = (False, True)

    def stop_peer(*, clear_invite):
        assert clear_invite
        controller.recording._local_capture = None
        return True

    controller._stop_session_peer.side_effect = stop_peer
    controller.audio.stop()
    workers.pop().run()

    assert controller.recording._local_capture is None
    assert controller.audio.cleanup_retry_required
    controller.recording.on_audio_session_stopped.assert_not_called()

    controller.audio.retry_stop()
    workers.pop().run()

    assert not controller.audio.cleanup_retry_required
    controller.recording.on_audio_session_stopped.assert_called_once_with()


def test_music_guest_preserves_capture_retirement_without_host_shutdown(room_cleanup):
    controller, workers, _ = room_cleanup
    controller._art_room_active.return_value = False
    controller.recording._local_capture = object()

    controller.audio.stop()
    workers.pop().run()

    controller.recording.stop_server_recording_for_shutdown.assert_not_called()
    controller.bridge.stop_hosted_server.assert_not_called()
    controller.recording.on_audio_session_stopped.assert_called_once_with()
    controller.audio.reset_to_idle.assert_called_once_with()
