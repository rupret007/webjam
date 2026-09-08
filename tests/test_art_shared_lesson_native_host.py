"""Native host lesson navigation follows its real pending/connected owner."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from core.session_conductor import ArtRoomState
from services import native_remote_transport as native
from services.remote_session_runtime import RemoteSessionPhase
from services.transport_runtime import TransportEvent
from tests.test_art_room_controller import (
    controllers as _controllers_fixture,
    qapp as _qapp_fixture,
)
from tests.test_art_shared_lesson_journey import (
    no_unhandled_qt_slot_errors as _qt_errors_fixture,
)
from tests.test_native_remote_transport import FakeProcess
from webjam_qt.controllers.application_controller import ApplicationController

controllers = _controllers_fixture
qapp = _qapp_fixture
no_unhandled_qt_slot_errors = _qt_errors_fixture


class LessonProcess(FakeProcess):
    """Existing native process contract, with no sockets or media."""

    def publish_room_state(self, state, *, generation):
        return TransportEvent(
            event_id=51, request_id=51, event_type="room_state_accepted", code="ok",
            state="connected", mode="host", profile_id="reference-local",
            generation=generation,
        )


class NativeHostLesson(SimpleNamespace):
    def __repr__(self):
        return f"NativeHostLesson(phase={self.owner.snapshot.phase.value!r})"


@pytest.fixture
def native_host(controllers, qapp, monkeypatch):
    monkeypatch.setattr(native, "TransportProcess", LessonProcess)
    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", lambda self: True)
    player = Mock(side_effect=AssertionError("Lesson guidance must not create a player"))
    browser, meeting = Mock(), Mock()
    monkeypatch.setattr(
        "webjam_qt.widgets.reference_video_player.create_qt_reference_video_player", player,
    )
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", browser)
    monkeypatch.setattr("webex_integration.open_webex_meeting", meeting)
    app = controllers(profile="art", hosting=True)
    app._launch_native_jamulus_for_startup = Mock()
    app._start_hosted_server_for_startup = Mock()
    app.bridge.launch_webex = Mock()
    app._room_participant.prepare_native("host")
    holder = {}

    def receive(snapshot):
        if "owner" in holder:
            app._on_remote_session_snapshot(snapshot, source=holder["owner"])

    owner = native.NativeHostTransportOwner(
        target_port=22124, binary="/private/test-webjam-fabric", expected_build="abc1234",
        on_snapshot=receive, schedule_callback=app._ui_invoker.invoke,
    )
    holder["owner"] = owner
    app._install_remote_invite_owner(owner)
    app._remote_session = owner
    app._room_participant.tick()
    assert app._room_participant.state is ArtRoomState.WAITING
    assert owner.snapshot.phase is RemoteSessionPhase.PREPARING
    assert not owner.connection_available and owner.invitation_available
    app.window.resize(820, 640)
    app.window.show()
    app.window.activateWindow()
    app._open_reference_video()
    qapp.processEvents()
    dialog = app._reference_video_dialog
    assert dialog.isVisibleTo(app.window) and dialog._watch_lesson_button.isEnabled()
    pair = NativeHostLesson(
        app=app, owner=owner, process=FakeProcess.instances[-1], dialog=dialog,
        coordinator=app._reference_video, identity=app._reference_video_identity(),
        generation=app._room_participant.generation,
    )
    yield pair
    player.assert_not_called()
    browser.assert_not_called()
    meeting.assert_not_called()
    app.bridge.launch_webex.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    assert pair.process.guest_generations == []
    assert pair.process.help_requests == []


@pytest.mark.parametrize("connected", [False, True])
def test_native_host_can_prepare_lesson_before_or_after_guest_arrives(native_host, qapp, connected):
    pair = native_host
    app, owner = pair.app, pair.owner
    if connected:
        pair.process.emit_host_connected(owner.snapshot.generation)
        qapp.processEvents()
        assert owner.connection_available and not owner.invitation_available
    before = owner.snapshot
    QTest.mouseClick(pair.dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()

    assert app.window.webex_embed.isVisibleTo(app.window)
    assert app.window.webex_embed._shared_lesson_hosting is True
    assert app.window.focusWidget() is app.window.webex_embed._change_link_btn
    assert not pair.dialog.isVisibleTo(app.window)
    assert app._reference_video is pair.coordinator
    assert app._reference_video_identity() == pair.identity
    assert app._room_participant.generation == pair.generation
    assert owner.snapshot is before
    assert owner.connection_available is connected
    assert pair.process.host_generations == [1]


@pytest.mark.parametrize("failure", ["pending_error", "connected_error", "process_loss"])
def test_native_host_rejects_lesson_after_failure_before_queued_ui_receipt(native_host, failure):
    pair = native_host
    app, owner = pair.app, pair.owner
    if failure != "pending_error":
        pair.process.emit_host_connected(owner.snapshot.generation)
        assert owner.connection_available
    if failure == "process_loss":
        # The owner detects a dead process during the action's live read.
        # Its last snapshot still says CONNECTED until that read happens.
        pair.process.running = False
        assert owner.snapshot.phase is RemoteSessionPhase.CONNECTED
    else:
        pair.process.on_event(TransportEvent(
            event_id=0, event_type="error", code="transport_failed", state="failed",
        ))
        assert owner.snapshot.phase is RemoteSessionPhase.FAILED
    # Do not process Qt events: the old button may still look enabled.
    workspace = app.window.workspace_stack.currentWidget()
    panel = app.window.webex_embed
    visible = panel.isVisibleTo(app.window)
    pair.dialog._watch_lesson_button.setFocus()

    pair.dialog.watch_lesson_requested.emit()

    assert app.window.workspace_stack.currentWidget() is workspace
    assert panel.isVisibleTo(app.window) is visible
    # Rendering room loss may move focus off a newly disabled control, but
    # it must stay in Paint along rather than selecting Conversation.
    assert pair.dialog.isAncestorOf(app.window.focusWidget())
    assert panel._shared_lesson_hosting is None
    assert owner.snapshot.phase is RemoteSessionPhase.FAILED
    assert owner.room_identity is None


def test_native_host_rekey_rejects_old_lesson_panel_without_needing_new_owner(native_host):
    pair = native_host
    app, owner = pair.app, pair.owner
    owner.reset()
    assert owner.snapshot.phase is RemoteSessionPhase.PREPARING
    assert app._remote_session is owner
    assert app._reference_video_identity() != pair.identity
    workspace = app.window.workspace_stack.currentWidget()

    pair.dialog.watch_lesson_requested.emit()

    assert app.window.workspace_stack.currentWidget() is workspace
    assert app.window.webex_embed._shared_lesson_hosting is None
    assert not pair.dialog._watch_lesson_button.isEnabled()
    assert pair.process.host_generations == [1, 2]
