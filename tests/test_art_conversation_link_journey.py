"""Actual ApplicationController Art conversation links open at the requested field."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QRect, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from core.settings import AppSettings, load_settings, save_settings
from services.remote_session_runtime import RemoteSessionPhase
from tests import test_art_notes_conversation_journey as notes_journey
from tests.test_art_notes_conversation_journey import (
    _click_talk_share,
    _composer_state,
    _leave,
    _make_notes,
    _notes_state,
    _open_notes,
    qapp as _qapp_fixture,
    room as _room_fixture,
)
from webex_integration import WebexLaunchState
from webjam_qt.windows.simple_settings import SimpleSettingsDialog

qapp = _qapp_fixture
room = _room_fixture

_NEW_LINK = "https://studio.webex.com/meet/PRIVATE_NEW_ART_LINK?token=PRIVATE_ART_TOKEN"
_CANCELED_LINK = "https://studio.webex.com/meet/PRIVATE_CANCELED_LINK"


@pytest.fixture(autouse=True)
def saved_personal_settings(monkeypatch):
    """Use a real saved identity/profile, without the legacy fixture's defaults.

    Music's saved start is empty; talk_and_make belongs to Art. Saving before
    constructing the real controller also avoids an OS-name suggestion being
    mistaken for a meeting-link edit during the first Settings save.
    """
    def create_settings(**kwargs):
        kwargs["musician_name"] = "Guest Artist"
        if kwargs["last_creator_profile_key"] == "music":
            kwargs["last_creator_start_key"] = ""
        settings = AppSettings(**kwargs)
        save_settings(settings)
        return load_settings(settings.config_file)

    monkeypatch.setattr(notes_journey, "AppSettings", create_settings)


def _assert_private_and_unchanged(pair, caplog):
    app = pair.app
    assert app._room_participant.generation == pair.generation
    assert app._reference_video_identity() == pair.identity
    if pair.role == "native":
        assert app._remote_session is pair.owner
        assert app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED
    else:
        assert app._room_participant.lan_guest is pair.owner
    assert app.settings.musician_name == "Guest Artist"
    assert app.settings.last_creator_profile_key == pair.profile
    assert app.settings.last_creator_start_key == (
        "" if pair.profile == "music" else "talk_and_make"
    )
    assert pair.launcher.joined == [] and pair.launcher.host_pages == 0
    pair.player_factory.assert_not_called()
    app.bridge.launch_webex.assert_not_called()
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app.host_peer.publish_reference_video_state.assert_not_called()
    app.host_peer.publish_shared_canvas_state.assert_not_called()
    app.jamulus.send_chat.assert_not_called()
    public = (
        caplog.text + repr(app._last_session_conductor)
        + repr(app.window.art_room_overview._overview)
        + app.window.art_room_overview.accessibleDescription()
        + repr(app.window.flash_message.call_args_list)
    )
    for marker in (
        notes_journey._NOTES, notes_journey._MUSIC_NOTES, notes_journey._DRAFT,
        notes_journey._INCOMING, notes_journey._MEETING, "PRIVATE_MEETING_MARKER",
    ):
        assert marker not in public


@pytest.fixture
def external_handoffs(monkeypatch):
    """No settings navigation or save may launch an external app or service."""
    meeting, browser = Mock(return_value=False), Mock(return_value=False)
    monkeypatch.setattr("webex_integration.open_webex_meeting", meeting)
    monkeypatch.setattr("PySide6.QtGui.QDesktopServices.openUrl", browser)
    return meeting, browser


def _drive_settings(monkeypatch, check):
    """Drive the real modal after its show/focus callbacks have been delivered."""
    observed = SimpleNamespace(dialogs=[], failures=[], results=[])
    original_exec = SimpleSettingsDialog.exec

    def execute(dialog):
        observed.dialogs.append(dialog)

        def finish():
            try:
                check(dialog)
            except BaseException as error:
                observed.failures.append(error)
            finally:
                if dialog.isVisible():
                    dialog.reject()

        # The second event-loop turn lets a requested section's queued initial
        # focus settle, without manually expanding or focusing the target.
        QTimer.singleShot(0, lambda: QTimer.singleShot(0, finish))
        result = original_exec(dialog)
        observed.results.append(result)
        return result

    monkeypatch.setattr(SimpleSettingsDialog, "exec", execute)
    return observed


def _raise_dialog_failures(observed, *, accepted=False):
    assert len(observed.dialogs) == 1
    if observed.failures:
        raise observed.failures[0]
    expected = (
        SimpleSettingsDialog.DialogCode.Accepted
        if accepted else SimpleSettingsDialog.DialogCode.Rejected
    )
    assert observed.results == [expected]


def _button(dialog, text):
    matches = [button for button in dialog.findChildren(QPushButton) if button.text() == text]
    assert len(matches) == 1
    return matches[0]


def _settings_bytes(app):
    path = Path(app.settings.config_file)
    return path.read_bytes() if path.exists() else None


def _assert_no_external_handoff(pair, caplog, external_handoffs):
    for opener in external_handoffs:
        opener.assert_not_called()
    public = (
        caplog.text + repr(pair.app.window.flash_message.call_args_list)
        + repr(pair.app._last_session_conductor)
        + pair.app.window.art_room_overview.accessibleDescription()
    )
    for marker in (
        _NEW_LINK, _CANCELED_LINK, "PRIVATE_NEW_ART_LINK", "PRIVATE_ART_TOKEN",
        "PRIVATE_CANCELED_LINK",
    ):
        assert marker not in public
    assert pair.launcher.joined == [] and pair.launcher.host_pages == 0
    pair.player_factory.assert_not_called()
    pair.app.bridge.launch_webex.assert_not_called()
    pair.app._launch_native_jamulus_for_startup.assert_not_called()
    pair.app._start_hosted_server_for_startup.assert_not_called()
    pair.app.jamulus.send_chat.assert_not_called()


@pytest.mark.parametrize("role", ["lan", "native"])
@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("configured", [False, True])
def test_art_notes_add_or_change_link_opens_the_visible_focused_meeting_field(
    room, qapp, monkeypatch, caplog, external_handoffs, role, profile, configured,
):
    pair = room(role=role, profile=profile, configured=configured)
    app = pair.app
    canvas = _open_notes(pair, qapp)
    notes = _make_notes(pair)
    composer = _composer_state(canvas)
    notes_path = app._persistence._notes_path()
    saved_notes = notes_path.read_bytes()
    _click_talk_share(pair, qapp)
    panel = app.window.webex_embed
    settings = replace(app.settings)
    settings_bytes = _settings_bytes(app)
    assert panel._change_link_btn.text() == ("Change Link" if configured else "Add Link")

    def check(dialog):
        assert dialog._conversation_toggle.isChecked()
        assert dialog._conversation_body.isVisibleTo(dialog)
        assert dialog._video.isVisibleTo(dialog)
        assert dialog._video.hasFocus()
        assert dialog._video.text() == app.settings.webex_url
        if configured:
            assert dialog._video.selectedText() == app.settings.webex_url
        # The dedicated conversation entry does not offer an unrelated sound
        # check. Ordinary Settings retains its existing sound-check action.
        assert not any(
            button.text() == "Verify Sound" and button.isVisibleTo(dialog)
            for button in dialog.findChildren(QPushButton)
        )
        dialog._video.selectAll()
        QTest.keyClicks(dialog._video, _CANCELED_LINK)
        QTest.mouseClick(_button(dialog, "Cancel"), Qt.MouseButton.LeftButton)

    observed = _drive_settings(monkeypatch, check)
    QTest.mouseClick(panel._change_link_btn, Qt.MouseButton.LeftButton)
    _raise_dialog_failures(observed)
    qapp.processEvents()
    assert app.settings == settings
    assert _settings_bytes(app) == settings_bytes
    assert _notes_state(canvas) == notes
    assert notes_path.read_bytes() == saved_notes
    assert _composer_state(canvas) == composer
    assert app.window.webex_embed is panel and panel.isVisibleTo(app.window)
    assert panel._meeting_configured is configured
    assert panel._launch_status == WebexLaunchState.NOT_OPENED.value
    assert app.window.focusWidget() is (
        panel._fallback_btn if configured else panel._change_link_btn
    )
    _assert_no_external_handoff(pair, caplog, external_handoffs)
    _assert_private_and_unchanged(pair, caplog)


@pytest.mark.parametrize("role,profile,operation", [
    ("lan", "music", "add"), ("native", "art", "add"),
    ("lan", "art", "change"), ("native", "music", "change"),
    ("lan", "music", "remove"), ("native", "art", "remove"),
])
def test_save_updates_current_conversation_immediately_without_restarting_or_opening(
    room, qapp, monkeypatch, caplog, external_handoffs, role, profile, operation,
):
    pair = room(role=role, profile=profile, configured=operation != "add")
    app = pair.app
    canvas = _open_notes(pair, qapp)
    notes = _make_notes(pair)
    notes_path = app._persistence._notes_path()
    saved_notes = notes_path.read_bytes()
    _click_talk_share(pair, qapp)
    panel = app.window.webex_embed
    settings = replace(app.settings)
    target = "" if operation == "remove" else _NEW_LINK
    if operation != "add":
        # An old OS handoff is evidence only about the previous link. Editing
        # it must not claim that the new destination was opened or joined.
        app.webex.launch_state = WebexLaunchState.OPENED_EXTERNALLY
        app.webex.browser_opened = True
        app.bridge.webex_state = WebexLaunchState.OPENED_EXTERNALLY.value
        panel.set_launch_status(WebexLaunchState.OPENED_EXTERNALLY.value)

    def check(dialog):
        assert dialog._video.hasFocus()
        dialog._video.selectAll()
        if target:
            QTest.keyClicks(dialog._video, target)
        else:
            QTest.keyClick(dialog._video, Qt.Key.Key_Backspace)
        QTest.mouseClick(_button(dialog, "Save"), Qt.MouseButton.LeftButton)
        assert dialog.result() == SimpleSettingsDialog.DialogCode.Accepted, dialog._error.text()

    observed = _drive_settings(monkeypatch, check)
    app.window.flash_message.reset_mock()
    QTest.mouseClick(panel._change_link_btn, Qt.MouseButton.LeftButton)
    _raise_dialog_failures(observed, accepted=True)
    qapp.processEvents()
    assert app.settings == replace(settings, webex_url=target)
    assert load_settings(app.settings.config_file).webex_url == target
    assert app.webex.meeting_url == target
    assert app.bridge.webex_controller is app.webex
    assert app.window.webex_embed is panel and panel.isVisibleTo(app.window)
    assert panel._meeting_configured is bool(target)
    assert panel._copy_link_btn.isEnabled() is bool(target)
    assert panel._fallback_btn.isEnabled() is bool(target)
    assert panel._change_link_btn.text() == ("Change Link" if target else "Add Link")
    assert panel._launch_status == WebexLaunchState.NOT_OPENED.value
    assert app.webex.launch_state is WebexLaunchState.NOT_OPENED
    assert not app.webex.browser_opened and not app.webex.is_connected
    assert app.window.focusWidget() is (
        panel._fallback_btn if target else panel._change_link_btn
    )
    feedback = " ".join(str(call.args[0]) for call in app.window.flash_message.call_args_list)
    assert "saved" in feedback.lower() or "removed" in feedback.lower()
    assert "next time you start" not in feedback.lower()
    assert "restart" not in feedback.lower()
    if operation != "add":
        assert "already open" in feedback.lower() or "stays open" in feedback.lower()
    assert _notes_state(canvas) == notes
    assert notes_path.read_bytes() == saved_notes
    assert _composer_state(canvas) == pair.draft
    _assert_no_external_handoff(pair, caplog, external_handoffs)
    _assert_private_and_unchanged(pair, caplog)
    _open_notes(pair, qapp)
    assert _notes_state(canvas) == notes
    canvas._notes.undo()
    assert canvas._notes.document().isRedoAvailable()
    canvas._notes.redo()
    assert canvas.current_notes() == notes[0]


@pytest.mark.parametrize("role,configured", [("lan", False), ("native", True)])
def test_compact_conversation_link_entry_is_visible_and_keyboard_usable(
    room, qapp, monkeypatch, caplog, external_handoffs, role, configured,
):
    pair = room(role=role, configured=configured)
    app = pair.app
    app.window.resize(760, 600)
    qapp.processEvents()
    _open_notes(pair, qapp)
    _make_notes(pair)
    _click_talk_share(pair, qapp)
    panel = app.window.webex_embed
    settings = replace(app.settings)

    def check(dialog):
        assert app.window.width() == 760 and app.window.height() == 600
        assert dialog.width() <= 760 and dialog.height() <= 600
        assert dialog._video.hasFocus()
        viewport = dialog._settings_scroll.viewport()
        field_rect = QRect(dialog._video.mapTo(viewport, QPoint()), dialog._video.size())
        assert viewport.rect().contains(field_rect.adjusted(1, 1, -1, -1))
        save = _button(dialog, "Save")
        assert dialog.rect().contains(QRect(save.mapTo(dialog, QPoint()), save.size()))
        assert dialog._video.accessibleName() == "Optional meeting link"
        # Start typing without clicking or searching the settings form. Change
        # replaces the selected URL; Add starts from the empty focused field.
        QTest.keyClicks(dialog._video, _CANCELED_LINK)
        assert dialog._video.text() == _CANCELED_LINK
        QTest.keyClick(dialog._video, Qt.Key.Key_Escape)

    observed = _drive_settings(monkeypatch, check)
    panel._change_link_btn.setFocus()
    QTest.keyClick(panel._change_link_btn, Qt.Key.Key_Space)
    _raise_dialog_failures(observed)
    qapp.processEvents()
    assert app.settings == settings
    assert panel.isVisibleTo(app.window)
    assert app.window.focusWidget() is (
        panel._fallback_btn if configured else panel._change_link_btn
    )
    _assert_no_external_handoff(pair, caplog, external_handoffs)
    _assert_private_and_unchanged(pair, caplog)


@pytest.mark.parametrize("role,transition", [("lan", "notes"), ("native", "leave")])
def test_save_does_not_reopen_stale_conversation_after_context_changes_in_modal(
    room, qapp, monkeypatch, caplog, external_handoffs, role, transition,
):
    pair = room(role=role, profile="music")
    app = pair.app
    canvas = _open_notes(pair, qapp)
    _make_notes(pair)
    _click_talk_share(pair, qapp)
    panel = app.window.webex_embed
    current = SimpleNamespace()

    def check(dialog):
        # Native callbacks continue during exec(). A current workspace or room
        # change must win over the older Conversation entry's focus request.
        if transition == "leave":
            _leave(pair, qapp)
            assert app.creator_profile.key == "music"
        app.window.side_rail.trigger("canvas")
        qapp.processEvents()
        canvas._notes.setFocus()
        current.settings = replace(app.settings)
        current.notes = _notes_state(canvas)
        current.composer = _composer_state(canvas)
        current.conversation_visible = panel.isVisibleTo(app.window)
        current.generation = app._room_participant.generation
        dialog._video.setText(_NEW_LINK)
        QTest.mouseClick(_button(dialog, "Save"), Qt.MouseButton.LeftButton)
        assert dialog.result() == SimpleSettingsDialog.DialogCode.Accepted, dialog._error.text()

    observed = _drive_settings(monkeypatch, check)
    QTest.mouseClick(panel._change_link_btn, Qt.MouseButton.LeftButton)
    _raise_dialog_failures(observed, accepted=True)
    qapp.processEvents()
    assert app.settings == replace(current.settings, webex_url=_NEW_LINK)
    assert app._last_content_key == "canvas"
    assert canvas.isVisibleTo(app.window)
    assert panel.isVisibleTo(app.window) is current.conversation_visible
    assert app.window.focusWidget() is canvas._notes
    assert _notes_state(canvas) == current.notes
    if transition == "leave":
        # Cocoa may clear an inactive QLineEdit selection when closing the
        # modal reveals Music. Draft contents and insertion point still belong
        # to that current workspace; Conversation must neither replace nor send.
        assert _composer_state(canvas)[:2] == current.composer[:2]
    else:
        assert _composer_state(canvas) == current.composer
    assert canvas._chat_input.text() == pair.draft[0]
    assert app._room_participant.generation == current.generation
    if transition == "leave":
        assert app._remote_session is None
        assert app._room_participant.lan_guest is None
        assert app.creator_profile.key == "music"
        assert app._session_conductor_facts().art_room_closed
    else:
        _assert_private_and_unchanged(pair, caplog)
    _assert_no_external_handoff(pair, caplog, external_handoffs)
