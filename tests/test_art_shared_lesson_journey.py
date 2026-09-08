"""Paint along reaches the existing Conversation without opening media or apps."""

import sys
from unittest.mock import Mock, PropertyMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMenu, QVBoxLayout

from core.reference_video import (
    ReferenceVideoFollowSnapshot,
    ReferenceVideoFollowState,
    ReferenceVideoSnapshot,
    ReferenceVideoState,
)

from tests.test_art_notes_conversation_journey import (
    _make_notes,
    _leave,
    _notes_state,
    qapp as _qapp_fixture,
    room as _room_fixture,
)
from webjam_qt.controllers.application_controller import ApplicationController

qapp = _qapp_fixture
room = _room_fixture


@pytest.fixture(autouse=True)
def no_unhandled_qt_slot_errors(monkeypatch):
    errors = []
    monkeypatch.setattr(
        sys, "excepthook", lambda error_type, error, traceback: errors.append(error_type.__name__),
    )
    yield
    assert errors == [], f"Qt callback errors must not masquerade as rejected actions: {errors}"


@pytest.fixture
def external_handoffs(monkeypatch):
    meeting, browser = Mock(), Mock()
    monkeypatch.setattr("webex_integration.open_webex_meeting", meeting)
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", browser)
    return meeting, browser


def _paint_along(pair, qapp):
    app: ApplicationController = pair.app
    app._open_reference_video()
    qapp.processEvents()
    dialog = app._reference_video_dialog
    assert dialog is not None and dialog.isVisibleTo(app.window)
    assert dialog._hosting is (pair.role == "host")
    return dialog


def _assert_no_handoff(pair, external_handoffs):
    pair.player_factory.assert_not_called()
    pair.app.bridge.launch_webex.assert_not_called()
    pair.app.jamulus.send_chat.assert_not_called()
    pair.app._launch_native_jamulus_for_startup.assert_not_called()
    pair.app._start_hosted_server_for_startup.assert_not_called()
    assert pair.launcher.joined == [] and pair.launcher.host_pages == 0
    for opener in external_handoffs:
        opener.assert_not_called()


@pytest.mark.parametrize("role", ["host", "lan", "native"])
@pytest.mark.parametrize("configured", [False, True])
def test_shared_lesson_uses_existing_conversation_without_a_local_file(
    room, qapp, monkeypatch, caplog, external_handoffs, role, configured,
):
    pair = room(role=role, profile="art", configured=configured)
    app = pair.app
    canvas = app.window.session_canvas
    notes = _make_notes(pair)
    notes_path = app._persistence._notes_path()
    saved_notes = notes_path.read_bytes()
    panel = app.window.webex_embed
    dialog = _paint_along(pair, qapp)
    context = Mock(wraps=panel.set_shared_lesson_context)
    monkeypatch.setattr(panel, "set_shared_lesson_context", context)
    button = dialog._watch_lesson_button
    assert button.isVisibleTo(app.window) and button.isEnabled()
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    qapp.processEvents()

    context.assert_called_once_with(hosting=role == "host")
    assert app.window.webex_embed is panel
    assert panel.isVisibleTo(app.window)
    assert not dialog.isVisibleTo(app.window)
    assert app.window.focusWidget() is (
        panel._fallback_btn if configured else panel._change_link_btn
    )
    assert app._reference_video_identity() == pair.identity
    assert app._room_participant.generation == pair.generation
    assert _notes_state(canvas) == notes
    assert notes_path.read_bytes() == saved_notes
    assert "PRIVATE_ART_NOTES" not in caplog.text
    _assert_no_handoff(pair, external_handoffs)


@pytest.mark.parametrize("role", ["host", "lan", "native"])
@pytest.mark.parametrize("destination", ["notes", "room", "conversation", "paint_along"])
def test_ordinary_navigation_clears_the_previous_shared_lesson_instructions(
    room, qapp, external_handoffs, role, destination,
):
    pair = room(role=role, profile="art", configured=True)
    app = pair.app
    dialog = _paint_along(pair, qapp)
    QTest.mouseClick(dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    panel = app.window.webex_embed
    assert panel._shared_lesson_hosting is (role == "host")
    assert "YouTube" in panel._mode_label.text()

    if destination == "notes":
        app.window.side_rail.trigger("canvas")
    elif destination == "room":
        app.window.side_rail.trigger("stage")
    elif destination == "conversation":
        app._show_webex_conversation()
    else:
        app._open_reference_video()
    qapp.processEvents()

    assert panel._shared_lesson_hosting is None
    assert "YouTube" not in panel._mode_label.text()
    assert "own tools" in panel._mode_label.text()
    assert app._reference_video_identity() == pair.identity
    assert app._room_participant.generation == pair.generation
    _assert_no_handoff(pair, external_handoffs)


@pytest.mark.parametrize("role", ["lan", "native"])
@pytest.mark.parametrize("transition", ["leave", "profile"])
def test_leaving_art_clears_shared_lesson_role_guidance(
    room, qapp, external_handoffs, role, transition,
):
    pair = room(role=role, profile="music", configured=True)
    app = pair.app
    dialog = _paint_along(pair, qapp)
    QTest.mouseClick(dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    panel = app.window.webex_embed
    assert panel._shared_lesson_hosting is False

    if transition == "leave":
        _leave(pair, qapp)
    else:
        app._apply_creator_profile_key("music")
        qapp.processEvents()

    assert app.creator_profile.key == "music"
    assert panel._shared_lesson_hosting is None
    assert "YouTube" not in panel._mode_label.text()
    _assert_no_handoff(pair, external_handoffs)


@pytest.mark.parametrize("role", ["lan", "native"])
@pytest.mark.parametrize("local_state", ["missing", "mismatch", "loading", "failed"])
def test_a_local_copy_problem_does_not_block_the_shared_lesson(
    room, qapp, monkeypatch, external_handoffs, role, local_state,
):
    pair = room(role=role, profile="art", configured=True)
    app = pair.app
    dialog = _paint_along(pair, qapp)
    coordinator = app._reference_video
    state = {
        "missing": ReferenceVideoFollowState.NEEDS_FILE,
        "mismatch": ReferenceVideoFollowState.MISMATCHED_FILE,
        "loading": ReferenceVideoFollowState.NEEDS_FILE,
        "failed": ReferenceVideoFollowState.LOCAL_ATTENTION,
    }[local_state]
    snapshot = ReferenceVideoFollowSnapshot(
        state=state, source_display_name="PRIVATE_LOCAL_REFERENCE.mp4", duration_s=300,
    )
    monkeypatch.setattr(
        type(coordinator), "follow_snapshot", PropertyMock(return_value=snapshot),
    )
    dialog._copy_opening = local_state == "loading"
    dialog.set_follow_snapshot(snapshot)
    qapp.processEvents()
    assert not dialog._position.isEnabled()
    assert dialog._watch_lesson_button.isVisibleTo(app.window)
    assert dialog._watch_lesson_button.isEnabled()

    QTest.mouseClick(dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()

    assert app.window.webex_embed.isVisibleTo(app.window)
    assert app.window.focusWidget() is app.window.webex_embed._fallback_btn
    assert app._reference_video is coordinator
    assert coordinator.follow_snapshot is snapshot
    assert not dialog._position.isEnabled()
    _assert_no_handoff(pair, external_handoffs)


@pytest.mark.parametrize("state", [ReferenceVideoState.LOADING, ReferenceVideoState.FAILED])
def test_host_local_video_loading_or_failure_keeps_the_shared_lesson_available(
    room, qapp, monkeypatch, external_handoffs, state,
):
    pair = room(role="host", profile="art", configured=True)
    app = pair.app
    dialog = _paint_along(pair, qapp)
    coordinator = app._reference_video
    snapshot = ReferenceVideoSnapshot(state=state)
    monkeypatch.setattr(
        type(coordinator), "host_snapshot", PropertyMock(return_value=snapshot),
    )
    dialog.set_host_snapshot(snapshot)
    qapp.processEvents()
    assert dialog._watch_lesson_button.isVisibleTo(app.window)
    assert dialog._watch_lesson_button.isEnabled()
    QTest.mouseClick(dialog._watch_lesson_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert app.window.webex_embed.isVisibleTo(app.window)
    assert app._reference_video is coordinator
    assert coordinator.host_snapshot is snapshot
    _assert_no_handoff(pair, external_handoffs)


@pytest.mark.parametrize("role", ["host", "lan", "native"])
@pytest.mark.parametrize("block", ["stale_dialog", "stale_room", "cleanup", "notes"])
def test_queued_shared_lesson_action_cannot_replace_newer_work(
    room, qapp, monkeypatch, external_handoffs, role, block,
):
    pair = room(role=role, profile="art", configured=True)
    app = pair.app
    notes = _make_notes(pair)
    dialog = _paint_along(pair, qapp)
    panel = app.window.webex_embed
    context = Mock(wraps=panel.set_shared_lesson_context)
    monkeypatch.setattr(panel, "set_shared_lesson_context", context)
    with monkeypatch.context() as patch:
        if block == "stale_dialog":
            patch.setattr(app, "_reference_video_dialog", None)
        elif block == "stale_room":
            patch.setattr(app, "_reference_video_binding", ("guest", "new-room", "new-token"))
        elif block == "cleanup":
            patch.setattr(app.audio, "cleanup_retry_required", True)
        else:
            app.window.side_rail.trigger("canvas")
            qapp.processEvents()
            app.window.session_canvas._notes.setFocus()
            assert not dialog.isVisibleTo(app.window)
        workspace = app.window.workspace_stack.currentWidget()
        focused = QApplication.focusWidget()
        visible = panel.isVisibleTo(app.window)
        dialog.watch_lesson_requested.emit()
        assert app.window.workspace_stack.currentWidget() is workspace
        assert panel.isVisibleTo(app.window) is visible
        assert QApplication.focusWidget() is focused
        assert _notes_state(app.window.session_canvas) == notes
        context.assert_not_called()
    _assert_no_handoff(pair, external_handoffs)


@pytest.mark.parametrize("role", ["lan", "native"])
@pytest.mark.parametrize("transition", ["leave", "profile"])
def test_old_room_lesson_callback_cannot_reenter_after_leave_or_profile_change(
    room, qapp, external_handoffs, role, transition,
):
    pair = room(role=role, profile="music", configured=True)
    app = pair.app
    dialog = _paint_along(pair, qapp)
    coordinator = app._reference_video
    # Save exactly the queued connection's operands before teardown may delete
    # the Qt object; the real handler must reject those stale owners first.
    def callback():
        app._watch_shared_lesson(coordinator, dialog)
    if transition == "leave":
        _leave(pair, qapp)
    else:
        app._apply_creator_profile_key("music")
        qapp.processEvents()
    assert app.creator_profile.key == "music"
    app.window.side_rail.trigger("canvas")
    qapp.processEvents()
    app.window.session_canvas._notes.setFocus()
    workspace = app.window.workspace_stack.currentWidget()
    focused = QApplication.focusWidget()
    panel = app.window.webex_embed
    visible = panel.isVisibleTo(app.window)
    notes = _notes_state(app.window.session_canvas)

    callback()
    qapp.processEvents()

    assert app.window.workspace_stack.currentWidget() is workspace
    assert panel.isVisibleTo(app.window) is visible
    assert QApplication.focusWidget() is focused
    assert _notes_state(app.window.session_canvas) == notes
    _assert_no_handoff(pair, external_handoffs)


@pytest.mark.parametrize("role", ["host", "lan", "native"])
@pytest.mark.parametrize("layer", ["modal", "menu"])
def test_pending_lesson_action_respects_an_active_modal_or_popup(
    room, qapp, monkeypatch, external_handoffs, role, layer,
):
    pair = room(role=role, profile="art", configured=True)
    app = pair.app
    dialog = _paint_along(pair, qapp)
    panel = app.window.webex_embed
    context = Mock(wraps=panel.set_shared_lesson_context)
    monkeypatch.setattr(panel, "set_shared_lesson_context", context)
    if layer == "menu":
        overlay = QMenu(app.window)
        overlay.addAction("Keep this choice")
        overlay.popup(app.window.mapToGlobal(app.window.rect().center()))
    else:
        overlay = QDialog(app.window)
        QVBoxLayout(overlay).addWidget(QLineEdit("Keep this choice", overlay))
        overlay.setModal(True)
        overlay.show()
        overlay.activateWindow()
    qapp.processEvents()
    if layer == "menu":
        assert QApplication.activePopupWidget() is overlay
    else:
        assert QApplication.activeModalWidget() is overlay
    workspace, focused = app.window.workspace_stack.currentWidget(), QApplication.focusWidget()
    visible = panel.isVisibleTo(app.window)
    try:
        dialog.watch_lesson_requested.emit()
        qapp.processEvents()
        assert app.window.workspace_stack.currentWidget() is workspace
        assert panel.isVisibleTo(app.window) is visible
        assert overlay.isVisible()
        assert QApplication.focusWidget() is focused
        context.assert_not_called()
    finally:
        overlay.close()
        overlay.deleteLater()
        qapp.processEvents()
    _assert_no_handoff(pair, external_handoffs)
