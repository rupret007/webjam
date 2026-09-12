"""Combined native lifetime recovery retains Art work, not retired authority.

These are in-process controller/owner journeys. The process contract is faked;
they do not establish elapsed-time, remote-network, playback, or physical proof.
"""

from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from core.remote_invitation import InvitationState
from core.session_conductor import ArtRoomState, SessionConductorPhase
from core.settings import load_settings, save_settings
from services.remote_session_runtime import RemoteSessionErrorCode, RemoteSessionPhase
from services.transport_runtime import TransportProcessError
from tests.test_art_conversation_link_journey import (
    _button,
    _drive_room_meeting,
    _raise_dialog_failures,
    _room_meeting_field,
)
from tests.test_art_notes_conversation_journey import _make_notes, _notes_state
from tests.test_art_shared_lesson_native_host import (
    controllers as _controllers_fixture,
    native_host as _native_host_fixture,
    no_unhandled_qt_slot_errors as _qt_errors_fixture,
    qapp as _qapp_fixture,
)
from tests.test_invitation_conversation_journey import PERSONAL, ROOM
from webjam_qt.controllers.application_controller import ApplicationController

controllers = _controllers_fixture
native_host = _native_host_fixture
no_unhandled_qt_slot_errors = _qt_errors_fixture
qapp = _qapp_fixture


@pytest.fixture
def art_host(native_host, qapp, monkeypatch):
    pair = native_host
    app = pair.app
    guards = []
    for owner, name in (
        (app.recording, "on_record_requested"),
        (app.bridge, "launch_jamulus"),
        (app.bridge, "launch_practice_session"),
        (app.jamulus, "send_chat"),
    ):
        guard = Mock(side_effect=AssertionError("Art recovery must not start media or send"))
        monkeypatch.setattr(owner, name, guard)
        guards.append(guard)
    monkeypatch.setattr(
        QMessageBox, "question", Mock(return_value=QMessageBox.StandardButton.Yes),
    )
    app.window.flash_message = Mock()
    settings_wizard = Mock(side_effect=AssertionError("A room link must not open personal Settings"))
    monkeypatch.setattr(app, "_open_settings_wizard", settings_wizard)
    guards.append(settings_wizard)
    app.settings.webex_url = PERSONAL
    save_settings(app.settings)
    # Seed the existing temporary-room boundary explicitly. This is not a
    # claim that fresh hosting itself obtains an invitation's meeting link.
    app._set_session_meeting_url(PERSONAL)
    pair.settings_bytes = Path(app.settings.config_file).read_bytes()
    pair.notes = _make_notes(pair)
    pair.notes_path = app._persistence._notes_path()
    pair.notes_bytes = pair.notes_path.read_bytes()

    pair.process.emit_host_connected(pair.owner.snapshot.generation)
    qapp.processEvents()
    assert pair.owner.connection_available
    assert app._room_participant.state is ArtRoomState.CONNECTED
    QTest.mouseClick(pair.dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    panel = app.window.webex_embed
    assert panel._shared_lesson_hosting is True

    def enter_temporary_meeting(modal):
        field = _room_meeting_field(modal)
        assert field.text() == PERSONAL
        field.selectAll()
        QTest.keyClicks(field, ROOM)
        QTest.mouseClick(_button(modal, "OK"), Qt.MouseButton.LeftButton)

    observed = _drive_room_meeting(monkeypatch, enter_temporary_meeting)
    QTest.mouseClick(panel._change_link_btn, Qt.MouseButton.LeftButton)
    _raise_dialog_failures(observed, accepted=True)
    qapp.processEvents()
    _assert_work_retained(pair)
    yield pair
    for guard in guards:
        guard.assert_not_called()


def _assert_work_retained(pair):
    app = pair.app
    assert _notes_state(app.window.session_canvas) == pair.notes
    assert pair.notes_path.read_bytes() == pair.notes_bytes
    assert app._effective_meeting_url() == ROOM
    assert app.webex.meeting_url == ROOM
    assert app.window.webex_embed._service_label == "Google Meet"
    assert app.settings.webex_url == PERSONAL
    assert load_settings(app.settings.config_file).webex_url == PERSONAL
    assert Path(app.settings.config_file).read_bytes() == pair.settings_bytes


def test_native_host_failed_reset_retries_cleanup_without_replaying_old_art_intent(
    art_host, qapp, monkeypatch, caplog,
):
    pair = art_host
    app, owner, process = pair.app, pair.owner, pair.process
    old_dialog, old_coordinator = pair.dialog, pair.coordinator
    old_snapshot, old_identity = owner.snapshot, pair.identity
    meeting_generation = app._session_meeting_generation
    panel = app.window.webex_embed
    operations = {}
    for name in ("share", "play", "pause", "seek"):
        operation = Mock(side_effect=AssertionError("A retired lesson cannot operate"))
        monkeypatch.setattr(old_coordinator, name, operation)
        operations[name] = operation
    real_close = process.close_peer
    calls = []

    def close_with_one_failed_receipt():
        calls.append("close")
        # The real controller must retire local-file authority before native
        # cleanup can block or fail, including still-queued button signals.
        assert app._reference_video is None
        assert app._reference_video_dialog is None
        assert not old_coordinator.hosting and not old_coordinator.following
        assert owner.room_identity is None and not owner.connection_available
        process.emit_host_connected(old_snapshot.generation)
        assert not owner.connection_available
        if len(calls) == 1:
            raise TransportProcessError("The simulated close receipt is unavailable.")
        return real_close()

    monkeypatch.setattr(process, "close_peer", close_with_one_failed_receipt)
    app.window.session_strip.reset_invite_requested.emit()
    assert calls == ["close"] and process.host_generations == [1]
    assert owner.snapshot.phase is RemoteSessionPhase.FAILED
    assert owner.snapshot.error_code is RemoteSessionErrorCode.STOP_FAILED
    assert not owner.invitation_available and owner.invitation is None
    assert app._reference_video_identity() == ("", "", "")
    # Deliver the original widget signals before Qt destroys the old panel.
    old_dialog.share_requested.emit("/private/unselected-lesson.mp4")
    old_dialog.play_requested.emit()
    old_dialog.pause_requested.emit()
    old_dialog.seek_requested.emit(27.0)
    old_dialog.watch_lesson_requested.emit()
    qapp.processEvents()
    app._room_participant.tick()
    assert app._room_participant.state is ArtRoomState.FAILED
    assert app.session_conductor.snapshot.presentation.phase is SessionConductorPhase.FAILED
    failed_snapshot = owner.snapshot
    assert app._remote_session is owner and app._remote_invite_owner is owner
    assert panel._shared_lesson_hosting is None
    assert app.window.session_strip._reset_invite_action.isVisible()
    _assert_work_retained(pair)
    assert app._session_meeting_generation == meeting_generation
    assert any("couldn’t reset" in call.args[0] for call in app.window.flash_message.call_args_list)

    # A second real menu action must retry the retained old close before
    # creating generation 2, rather than losing ownership of the failed close.
    app.window.session_strip.reset_invite_requested.emit()
    qapp.processEvents()
    app._room_participant.tick()
    assert calls == ["close", "close"]
    assert process.closed == 1 and process.host_generations == [1, 2]
    assert owner.snapshot.phase is RemoteSessionPhase.PREPARING
    assert owner.invitation_available and not owner.connection_available
    assert app.session_conductor.snapshot.presentation.phase is SessionConductorPhase.INVITE_READY
    assert app.window.session_hud._status.text() == "Your room is open"
    assert app.window.participant_grid._last_session_state.title == "Your room is open"
    assert app._reference_video_identity() != old_identity
    assert app._reference_video_identity()[0] == "host"
    _assert_work_retained(pair)

    # Old-generation connection receipts and late callbacks retain neither
    # transport authority nor permission to replace the artist's Notes focus.
    app.window.side_rail.trigger("canvas")
    qapp.processEvents()
    app.window.session_canvas._notes.setFocus()
    workspace, focus = app.window.workspace_stack.currentWidget(), QApplication.focusWidget()
    process.emit_host_connected(old_snapshot.generation)
    app._on_remote_session_snapshot(old_snapshot, source=owner)
    app._on_remote_session_snapshot(failed_snapshot, source=owner)
    ApplicationController._watch_shared_lesson(app, old_coordinator, old_dialog)
    for operation in operations.values():
        app._run_current_host_paint_along(old_coordinator, old_dialog, operation)
    qapp.processEvents()
    assert owner.snapshot.phase is RemoteSessionPhase.PREPARING
    assert app.window.workspace_stack.currentWidget() is workspace
    assert QApplication.focusWidget() is focus
    assert panel._shared_lesson_hosting is None
    _assert_work_retained(pair)

    process.emit_host_connected(owner.snapshot.generation)
    qapp.processEvents()
    assert app.session_conductor.snapshot.presentation.phase is SessionConductorPhase.CONNECTED
    assert app.window.session_hud._status.text() == "You’re in"
    assert app.window.participant_grid._last_session_state.title == "You’re in"
    recovered_token = app.session_conductor.snapshot.token
    app._on_remote_session_snapshot(failed_snapshot, source=owner)
    assert app.session_conductor.snapshot.token == recovered_token
    assert app.session_conductor.snapshot.presentation.phase is SessionConductorPhase.CONNECTED
    assert app.window.session_hud._status.text() == "You’re in"
    app._open_reference_video()
    qapp.processEvents()
    fresh = app._reference_video_dialog
    assert fresh is not old_dialog and app._reference_video is not old_coordinator
    assert fresh._hosting and not app._reference_video.following
    assert fresh._watch_lesson_button.isEnabled()
    assert not fresh._position.isEnabled()  # No local file has been offered.
    QTest.mouseClick(fresh._watch_lesson_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert panel._shared_lesson_hosting is True and panel.isVisibleTo(app.window)
    assert app.window.focusWidget() is panel._fallback_btn
    assert owner.connection_available
    _assert_work_retained(pair)
    for operation in operations.values():
        operation.assert_not_called()
    assert all(value not in caplog.text for value in (PERSONAL, ROOM, "PRIVATE_ART_NOTES"))


def test_healthy_native_host_reset_changes_room_without_restarting_conductor(art_host, qapp):
    pair = art_host
    app, owner = pair.app, pair.owner
    token = app.session_conductor.snapshot.token
    identity, invitation = app._reference_video_identity(), owner.invitation
    assert app.session_conductor.snapshot.presentation.phase is SessionConductorPhase.CONNECTED

    app.window.session_strip.reset_invite_requested.emit()
    qapp.processEvents()
    app._room_participant.tick()

    assert pair.process.closed == 1 and pair.process.host_generations == [1, 2]
    assert owner.invitation is not invitation and owner.invitation_available
    assert app._reference_video_identity() != identity
    assert owner.snapshot.phase is RemoteSessionPhase.PREPARING
    assert app.session_conductor.snapshot.token == token
    assert app.session_conductor.snapshot.presentation.phase is SessionConductorPhase.INVITE_READY
    assert app.window.session_hud._status.text() == "Your room is open"
    assert not pair.coordinator.hosting and not pair.coordinator.following
    _assert_work_retained(pair)

    pair.process.emit_host_connected(owner.snapshot.generation)
    qapp.processEvents()
    assert owner.connection_available
    assert app.session_conductor.snapshot.token == token
    assert app.session_conductor.snapshot.presentation.phase is SessionConductorPhase.CONNECTED
    assert app.window.session_hud._status.text() == "You’re in"
    app._open_reference_video()
    qapp.processEvents()
    assert app._reference_video is not pair.coordinator
    assert app._reference_video.hosting and not app._reference_video.following
    assert app._reference_video_dialog._watch_lesson_button.isEnabled()
    _assert_work_retained(pair)


@pytest.mark.parametrize("cleanup_owner", ["audio-stopping", "audio-retry", "room-stopping"])
def test_reset_during_owned_cleanup_does_not_retire_art_work_or_touch_native_peer(
    art_host, monkeypatch, cleanup_owner,
):
    pair = art_host
    app, owner = pair.app, pair.owner
    snapshot = owner.snapshot
    identity = app._reference_video_identity()
    token = app.session_conductor.snapshot.token
    meeting_generation = app._session_meeting_generation
    workspace, focus = app.window.workspace_stack.currentWidget(), QApplication.focusWidget()
    # These are the real coordinator's independently published lifecycle
    # facts. Keep the native owner and actual Reset action intact: a queued
    # menu action may reach the controller after its visible menu was disabled.
    state_owner, field = {
        "audio-stopping": (app.audio, "stopping"),
        "audio-retry": (app.audio, "cleanup_retry_required"),
        "room-stopping": (app._room_participant, "stopping"),
    }[cleanup_owner]
    with monkeypatch.context() as transition:
        transition.setattr(state_owner, field, True)
        app.window.session_strip.reset_invite_requested.emit()
        assert pair.process.closed == 0 and pair.process.host_generations == [1]
        assert owner.snapshot is snapshot and owner.connection_available
        assert app._remote_session is owner and app._remote_invite_owner is owner
        assert app._reference_video is pair.coordinator
        assert app._reference_video_dialog is pair.dialog
        assert pair.coordinator.hosting and not pair.coordinator.following
        assert app._reference_video_binding == identity
        assert app.session_conductor.snapshot.token == token
        assert app.window.workspace_stack.currentWidget() is workspace
        assert QApplication.focusWidget() is focus
        assert app.window.webex_embed._shared_lesson_hosting is True
        assert app._session_meeting_generation == meeting_generation
        _assert_work_retained(pair)


def test_native_connected_art_context_outlives_admission_expiry_without_a_new_peer(
    art_host, qapp,
):
    pair = art_host
    app, owner = pair.app, pair.owner
    invitation, snapshot = owner.invitation, owner.snapshot
    identity = app._reference_video_identity()
    assert invitation.expires_at_unix - invitation.issued_at_unix == 600
    assert not owner.invitation_available  # The one-use invite is consumed.
    # Advance only the invitation lifecycle's injected observation seam. This
    # is a Python ownership projection, not a wall-clock/native-service proof.
    expired = owner._owner._lifecycle.snapshot(now_unix=invitation.expires_at_unix)
    assert expired.state is InvitationState.EXPIRED
    assert invitation.advisory_expired(invitation.expires_at_unix)
    app._room_participant.tick()
    app._update_session_hud()
    qapp.processEvents()
    assert owner.snapshot is snapshot and owner.connection_available
    assert owner.invitation is invitation and not owner.invitation_available
    assert app._room_participant.state is ArtRoomState.CONNECTED
    assert app.session_conductor.snapshot.presentation.phase is SessionConductorPhase.CONNECTED
    assert app.window.session_hud._status.text() == "You’re in"
    assert not app._last_observed_invite_available
    assert app.window.session_strip._reset_invite_action.isVisible()
    assert app._reference_video_identity() == identity
    assert app.window.webex_embed._shared_lesson_hosting is True
    _assert_work_retained(pair)

    app._open_reference_video()
    qapp.processEvents()
    assert app._reference_video is pair.coordinator
    assert pair.dialog._watch_lesson_button.isEnabled()
    QTest.mouseClick(pair.dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert app.window.webex_embed._shared_lesson_hosting is True
    assert app.window.webex_embed.isVisibleTo(app.window)
    assert pair.process.host_generations == [1] and pair.process.closed == 0
    _assert_work_retained(pair)
