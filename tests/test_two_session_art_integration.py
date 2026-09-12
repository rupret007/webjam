"""Composed invitation and lesson entry preserve one room's Conversation owner."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from core.room_state import RoomState
from core.session_conductor import ArtRoomState
from core.settings import load_settings
from services.remote_session_runtime import RemoteSessionPhase
from tests.test_art_conversation_link_journey import (
    _button,
    _drive_room_meeting,
    _raise_dialog_failures,
    _room_meeting_field,
)
from tests.test_art_room_controller import RoomBackend, drain
from tests.test_invitation_conversation_journey import (
    PERSONAL,
    ROOM,
    apps as _apps_fixture,
    qapp as _qapp_fixture,
)
from tests.test_invitation_conversation_lifetime import _remote_message, _start_lan
from webjam_qt.controllers.application_controller import ApplicationController

apps = _apps_fixture
qapp = _qapp_fixture

REPLACEMENT = "https://us02web.zoom.us/j/123456789"


@pytest.fixture(autouse=True)
def no_unhandled_qt_slot_errors(monkeypatch):
    errors = []
    monkeypatch.setattr(
        sys, "excepthook",
        lambda error_type, error, traceback: errors.append(error_type.__name__),
    )
    yield
    assert errors == [], f"A swallowed Qt callback is not a rejected action: {errors}"


@pytest.fixture(params=["lan", "native"])
def guarded_app(apps, qapp, monkeypatch, request):
    """Use real room ownership and widgets with all external effects intercepted."""
    guards = []

    def forbid(target):
        guard = Mock(side_effect=AssertionError("This Art journey must not launch or capture"))
        monkeypatch.setattr(target, guard)
        guards.append(guard)
        return guard

    forbid("webjam_qt.widgets.reference_video_player.create_qt_reference_video_player")
    forbid("webjam_qt.platform_permissions.microphone_permission_status")
    forbid("webjam_qt.controllers.application_controller.GuestPeerSession")
    forbid("webex_integration.open_webex_meeting")
    forbid("PySide6.QtGui.QDesktopServices.openUrl")
    forbid("services.drawpile_service.DrawpileLauncher.open_canvas")
    forbid("services.drawpile_service.DrawpileLauncher.open_host_page")
    monkeypatch.setattr(
        "services.native_remote_transport.NativeGuestTransportBackend", RoomBackend,
    )
    monkeypatch.setattr(
        QMessageBox, "question", Mock(return_value=QMessageBox.StandardButton.Yes),
    )

    # Cover both cold LaunchDialog and warm native full-message entry. Personal
    # Webex deliberately differs from the invited Google Meet in either path.
    app = apps(cold=request.param == "lan")
    for owner, name in (
        (app.bridge, "launch_jamulus"),
        (app.bridge, "launch_practice_session"),
        (app.recording, "on_record_requested"),
        (app.jamulus, "send_chat"),
    ):
        guard = Mock(side_effect=AssertionError("This Art journey must not start music or send"))
        monkeypatch.setattr(owner, name, guard)
        guards.append(guard)
    backend = None
    if request.param == "lan":
        owner = _start_lan(app, qapp, monkeypatch)
    else:
        app._launch_native_jamulus_for_startup = Mock()
        app._start_hosted_server_for_startup = Mock()
        assert app.accept_invite_url(_remote_message())
        assert app._remote_invitation is not None
        owner = app._remote_session
        drain(qapp, lambda: owner.snapshot.phase is RemoteSessionPhase.CONNECTED)
        backend = RoomBackend.instances[-1]
        backend.emit(RoomState(1, "art", "paint_along"))
        drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
        # Session Conversation must outlive the consumed enrollment capability.
        assert app._remote_invitation is None
    app.window.resize(820, 640)
    app.window.show()
    app.window.activateWindow()
    app._tick_creator_start()
    qapp.processEvents()
    yield app, owner, backend
    # Unwind an intentionally failed Leave even if an earlier assertion failed,
    # so the real controller fixture can finish its ordinary cleanup.
    if app.audio.cleanup_retry_required:
        app.audio.retry_stop()
        drain(qapp, lambda: not app.audio.stopping)
    for guard in guards:
        guard.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()


def _assert_personal_settings(app, original_bytes):
    assert app.settings.webex_url == PERSONAL
    assert load_settings(app.settings.config_file).webex_url == PERSONAL
    assert Path(app.settings.config_file).read_bytes() == original_bytes


@pytest.mark.parametrize("replacement", [REPLACEMENT, ""], ids=["change-provider", "remove-link"])
def test_complete_art_invite_lesson_edit_and_leave_keep_conversation_in_its_room(
    guarded_app, qapp, monkeypatch, caplog, replacement,
):
    app, owner, backend = guarded_app
    settings_bytes = Path(app.settings.config_file).read_bytes()
    assert app._effective_meeting_url() == ROOM and ROOM != PERSONAL
    generation = app._room_participant.generation
    identity = app._reference_video_identity()
    panel = app.window.webex_embed

    app._open_reference_video()
    qapp.processEvents()
    dialog = app._reference_video_dialog
    coordinator = app._reference_video
    assert dialog is not None and dialog.isVisibleTo(app.window)
    assert coordinator.following and not coordinator.hosting
    assert not dialog._hosting and not dialog._position.isEnabled()
    seek = Mock(side_effect=AssertionError("A guest lesson action cannot seek"))
    monkeypatch.setattr(coordinator, "seek", seek)
    # Even a stale/direct signal cannot acquire a host transport connection.
    dialog.seek_requested.emit(30.0)
    seek.assert_not_called()
    watch = dialog._watch_lesson_button
    assert watch.isVisibleTo(app.window) and watch.isEnabled()
    watch.setFocus()
    QTest.keyClick(watch, Qt.Key.Key_Space)
    qapp.processEvents()

    assert panel.isVisibleTo(app.window)
    assert panel._shared_lesson_hosting is False
    assert panel._service_label == "Google Meet"
    assert "Google Meet" in panel._mode_label.text()
    assert "Webex" not in panel._mode_label.text()
    assert app.window.focusWidget() is panel._fallback_btn
    app.bridge.launch_webex.assert_not_called()
    _assert_personal_settings(app, settings_bytes)

    def edit_meeting(modal):
        field = _room_meeting_field(modal)
        assert field.hasFocus() and field.text() == ROOM
        field.selectAll()
        if replacement:
            QTest.keyClicks(field, replacement)
        else:
            QTest.keyClick(field, Qt.Key.Key_Backspace)
        QTest.mouseClick(_button(modal, "OK"), Qt.MouseButton.LeftButton)
        assert modal.result() == QInputDialog.DialogCode.Accepted

    observed = _drive_room_meeting(monkeypatch, edit_meeting)
    QTest.mouseClick(panel._change_link_btn, Qt.MouseButton.LeftButton)
    _raise_dialog_failures(observed, accepted=True)
    qapp.processEvents()

    assert app._effective_meeting_url() == replacement
    assert app.webex.meeting_url == replacement
    assert app._room_participant.generation == generation
    assert app._reference_video_identity() == identity
    assert panel._shared_lesson_hosting is False
    assert panel._service_label == ("Zoom" if replacement else "")
    assert "Google Meet" not in panel._mode_label.text()
    assert "Webex" not in panel._mode_label.text()
    assert ("Zoom" if replacement else "your meeting") in panel._mode_label.text()
    assert panel._meeting_configured is bool(replacement)
    assert panel._fallback_btn.isEnabled() is bool(replacement)
    assert panel._change_link_btn.text() == ("Change Link" if replacement else "Add Link")
    assert app.window.focusWidget() is (
        panel._fallback_btn if replacement else panel._change_link_btn
    )
    assert not dialog._position.isEnabled()
    seek.assert_not_called()
    _assert_personal_settings(app, settings_bytes)
    app.bridge.launch_webex.assert_not_called()

    # The explicit button's bridge request is captured; no provider or browser
    # opens. With no room link, even clicking the disabled button cannot fall
    # back to the unrelated personal meeting.
    QTest.mouseClick(panel._fallback_btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    if replacement:
        app.bridge.launch_webex.assert_called_once_with(manual=True, meeting_url=replacement)
    else:
        app.bridge.launch_webex.assert_not_called()
    requested_handoffs = app.bridge.launch_webex.call_count

    stopped_object = backend if backend is not None else owner
    real_stop = stopped_object.stop
    host_state = None if backend is not None else owner.last_state
    # The native backend reports failed cleanup through an exception; the real
    # runtime derives STOP_FAILED and the controller retains its owner for retry.
    stopped_object.stop = (
        Mock(side_effect=RuntimeError("Simulated native cleanup refusal"))
        if backend is not None else Mock(return_value=False)
    )
    try:
        app.window.session_strip.launch_audio_requested.emit()
        drain(qapp, lambda: not app.audio.stopping)
        assert app.audio.cleanup_retry_required
        assert (app._remote_session if backend is not None else app._room_participant.lan_guest) is owner
        assert app._effective_meeting_url() == replacement
        assert app.webex.meeting_url == replacement
        assert panel._shared_lesson_hosting is None
        _assert_personal_settings(app, settings_bytes)
    finally:
        stopped_object.stop = real_stop

    app.audio.retry_stop()
    drain(qapp, lambda: not app.audio.stopping)
    assert not app.audio.cleanup_retry_required
    assert app._room_participant.lan_guest is None
    assert app._remote_session is None
    assert app._guest_invite is None
    assert app._remote_invitation is None
    assert app._effective_meeting_url() == PERSONAL
    assert app.webex.meeting_url == PERSONAL
    assert panel._shared_lesson_hosting is None
    assert "YouTube" not in panel._mode_label.text()
    assert app.bridge.webex_state == "Not opened"

    app.window.side_rail.trigger("canvas")
    qapp.processEvents()
    app.window.session_canvas._notes.setFocus()
    workspace, focus = app.window.workspace_stack.currentWidget(), QApplication.focusWidget()
    visible = panel.isVisibleTo(app.window)
    # Retired transport state and the exact old callback operands must not
    # revive the old room's meeting, guidance, media intent, or focus.
    if backend is not None:
        backend.emit(RoomState(2, "art", "paint_along"))
        app._on_remote_session_snapshot(owner.snapshot, source=owner)
    else:
        app._room_participant.receive_lan(owner, generation, host_state)
    ApplicationController._watch_shared_lesson(app, coordinator, dialog)
    qapp.processEvents()
    assert app.window.workspace_stack.currentWidget() is workspace
    assert QApplication.focusWidget() is focus
    assert panel.isVisibleTo(app.window) is visible
    assert panel._shared_lesson_hosting is None
    assert app._effective_meeting_url() == PERSONAL
    assert app.bridge.launch_webex.call_count == requested_handoffs
    seek.assert_not_called()
    _assert_personal_settings(app, settings_bytes)
    assert all(value not in caplog.text for value in (PERSONAL, ROOM, REPLACEMENT))
