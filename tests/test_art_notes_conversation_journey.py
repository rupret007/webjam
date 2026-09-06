"""Real ApplicationController Art rooms expose Conversation without sending notes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from core.room_state import RoomState
from core.session_conductor import ArtRoomState
from core.session_transfer import SessionCredentials
from core.settings import AppSettings
from services.remote_session_runtime import RemoteSessionPhase
from tests.test_art_room_controller import RoomBackend, arm_lan, drain, invitation, remote
from tests.test_shared_canvas_coordinator import FakeLauncher
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow

_NOTES = "PRIVATE_ART_NOTES: keep the warm background."
_INSERT = " Leave room for the figure."
_MUSIC_NOTES = "PRIVATE_MUSIC_NOTES: next rehearsal."
_DRAFT = "PRIVATE_UNSENT_MUSIC_MESSAGE"
_INCOMING = "PRIVATE_QUEUED_JAMULUS_MESSAGE"
_MEETING = "https://studio.webex.com/meet/PRIVATE_MEETING_MARKER"


class Journey(SimpleNamespace):
    def __repr__(self):
        return f"ArtNotesJourney(role={self.role!r}, profile={self.profile!r})"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def room(qapp, monkeypatch, tmp_path):
    made = []
    launcher = FakeLauncher()
    player_factory = Mock(side_effect=AssertionError("Conversation must not load a player"))
    monkeypatch.setattr("services.native_remote_transport.NativeGuestTransportBackend", RoomBackend)
    monkeypatch.setattr("services.native_remote_transport.reference_local_host_requested", lambda: False)
    monkeypatch.setattr("services.drawpile_service.create_canvas_launcher", lambda settings: launcher)
    monkeypatch.setattr(
        "webjam_qt.widgets.reference_video_player.create_qt_reference_video_player", player_factory,
    )
    monkeypatch.setattr("core.network_invite.local_band_address", lambda: "192.168.1.20")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    notes_home = tmp_path / "local-notes"
    notes_home.mkdir()
    monkeypatch.setattr(
        "webjam_qt.controllers.session_persistence._persistence_home", lambda: notes_home,
    )
    def detect_webex(app):
        app.window.webex_embed.set_app_status("not-installed")
        return True

    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", detect_webex)
    previous_style = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())

    def create(*, role="native", profile="music", configured=False, connect=True):
        root = tmp_path / str(len(made))
        root.mkdir()
        invite = invitation() if role == "lan" else None
        if invite is not None:
            arm_lan(monkeypatch, invite)
        settings = AppSettings(
            config_file=str(root / "settings.json"), takes_directory=str(root / "takes"),
            last_creator_profile_key=profile, last_creator_start_key="talk_and_make",
            host_server_enabled=role == "host", webex_url=_MEETING if configured else "",
        )
        window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Personal studio",
        )
        app = ApplicationController(window, settings=settings, session_invite=invite)
        made.append(app)
        app._launch_native_jamulus_for_startup = Mock()
        app._start_hosted_server_for_startup = Mock()
        app.bridge.launch_webex = Mock()
        app.window.flash_message = Mock()
        app.jamulus.send_chat = Mock(return_value=True)
        canvas = window.session_canvas
        canvas._notes.setPlainText(_MUSIC_NOTES if profile == "music" else "")
        assert app._save_notes()
        canvas._chat_input.setText(_DRAFT)
        canvas._chat_input.setSelection(3, 9)
        draft = _composer_state(canvas)
        pair = Journey(
            app=app, role=role, profile=profile, invite=invite, configured=configured,
            launcher=launcher, player_factory=player_factory, draft=draft, owner=None,
        )
        if role == "host":
            host = SimpleNamespace(
                active=False, credentials=None, server=None,
                invite_link=lambda **kwargs: "private-host-invitation",
                publish_reference_video_state=Mock(), publish_shared_canvas_state=Mock(),
            )

            def start(address, **kwargs):
                assert kwargs["creator_profile_key"] == "art"
                host.credentials = SessionCredentials.create()
                host.server = SimpleNamespace(
                    address=(address, 22125), room_participants=lambda: frozenset(),
                )
                host.active = True

            def stop():
                host.active = False
                host.server = host.credentials = None
                return True

            host.start, host.stop = start, stop
            app.host_peer = host
        else:
            app.host_peer.publish_reference_video_state = Mock()
            app.host_peer.publish_shared_canvas_state = Mock()

        def join():
            if role == "native":
                assert app.accept_invitation(remote())
                drain(qapp, lambda: app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED)
                backend = RoomBackend.instances[-1]
                backend.emit(RoomState(1, "art", "talk_and_make"))
                drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
                pair.owner = app._remote_session
                pair.backend = backend
            else:
                assert app.begin_startup_journey()
                if role == "lan":
                    pair.owner = app._room_participant.lan_guest
                    pair.owner.poll_once()
                    drain(qapp, lambda: app._room_participant.state is ArtRoomState.CONNECTED)
                else:
                    pair.owner = app.host_peer
                    assert pair.owner.active
            assert app.creator_profile.key == "art"
            pair.identity = app._reference_video_identity()
            pair.generation = app._room_participant.generation
            window.resize(820, 640)
            window.show()
            window.activateWindow()
            app._tick_creator_start()
            qapp.processEvents()

        pair.connect = join
        if connect:
            join()
        return pair

    yield create
    for app in reversed(made):
        app._shutdown_cleanup_pending = False
        qapp.processEvents()
        assert app.shutdown()
        window = app.window
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(window)
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.setStyleSheet(previous_style)


def _composer_state(canvas):
    editor = canvas._chat_input
    return editor.text(), editor.cursorPosition(), editor.selectionStart(), editor.selectedText()


def _open_notes(pair, qapp):
    window = pair.app.window
    panel = window.webex_embed
    conversation_visible = panel.isVisibleTo(window)
    meeting_state = (
        panel._launch_status, panel._meeting_configured, panel._service_label,
        panel._native_app_available, panel._fallback_btn.text(), panel._fallback_btn.isEnabled(),
    )
    compact_art = pair.app.creator_profile.key == "art" and window.width() < 900
    window.side_rail.trigger("canvas")
    qapp.processEvents()
    assert window.session_canvas.isVisibleTo(window)
    assert window.webex_embed is panel
    assert panel.isVisibleTo(window) is (False if compact_art else conversation_visible)
    if compact_art:
        assert not window._room_stage.isVisibleTo(window)
        assert window.session_canvas.width() >= window.workspace_stack.width() - 2
    assert (
        panel._launch_status, panel._meeting_configured, panel._service_label,
        panel._native_app_available, panel._fallback_btn.text(), panel._fallback_btn.isEnabled(),
    ) == meeting_state
    return window.session_canvas


def _make_notes(pair):
    canvas = pair.app.window.session_canvas
    editor = canvas._notes
    editor.setPlainText(_NOTES)
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.beginEditBlock()
    cursor.insertText(_INSERT)
    cursor.endEditBlock()
    cursor.setPosition(4)
    cursor.setPosition(16, QTextCursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    assert pair.app._save_notes()
    return _notes_state(canvas)


def _notes_state(canvas):
    cursor = canvas._notes.textCursor()
    document = canvas._notes.document()
    return (
        canvas.current_notes(), cursor.position(), cursor.anchor(),
        document.isUndoAvailable(), document.isRedoAvailable(), canvas._notes_save_state,
    )


def _click_talk_share(pair, qapp):
    button = pair.app.window.session_canvas.talk_share_button()
    assert button.isVisible() and button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    qapp.processEvents()


def _assert_conversation(pair):
    window = pair.app.window
    panel = window.webex_embed
    assert window.workspace_stack.currentWidget() is window.center_splitter
    assert window.art_room_overview.isVisibleTo(window)
    assert panel.isVisibleTo(window)
    assert not window.session_canvas.isVisibleTo(window)
    assert panel._meeting_configured is pair.configured
    assert panel._copy_link_btn.isEnabled() is pair.configured
    assert panel._change_link_btn.text() == ("Change Link" if pair.configured else "Add Link")
    target = panel._fallback_btn if pair.configured else panel._change_link_btn
    assert target.isEnabled() and target.isVisibleTo(window)
    assert window.focusWidget() is target


def _assert_private_and_unchanged(pair, caplog):
    app = pair.app
    assert app._room_participant.generation == pair.generation
    assert app._reference_video_identity() == pair.identity
    if pair.role == "native":
        assert app._remote_session is pair.owner
        assert app._remote_session.snapshot.phase is RemoteSessionPhase.CONNECTED
    elif pair.role == "lan":
        assert app._room_participant.lan_guest is pair.owner
    else:
        assert app.host_peer is pair.owner and pair.owner.active
    assert app.settings.last_creator_profile_key == pair.profile
    assert app.settings.last_creator_start_key == "talk_and_make"
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
    for marker in (_NOTES, _MUSIC_NOTES, _DRAFT, _INCOMING, _MEETING, "PRIVATE_MEETING_MARKER"):
        assert marker not in public


def _leave(pair, qapp):
    pair.app.window.session_strip.launch_audio_requested.emit()
    drain(qapp, lambda: not pair.app.audio.stopping)
    drain(qapp, lambda: pair.app.creator_profile.key == pair.profile)
    assert pair.app._room_participant.lan_guest is None
    assert _composer_state(pair.app.window.session_canvas) == pair.draft


@pytest.mark.parametrize("role,profile", [
    ("host", "art"), ("lan", "music"), ("lan", "art"),
    ("native", "music"), ("native", "art"),
])
@pytest.mark.parametrize("configured", [False, True])
def test_art_notes_opens_existing_conversation_and_preserves_work(
    room, qapp, caplog, role, profile, configured,
):
    pair = room(role=role, profile=profile, configured=configured)
    canvas = _open_notes(pair, qapp)
    notes = _make_notes(pair)
    notes_path = pair.app._persistence._notes_path()
    saved = notes_path.read_bytes()
    assert not canvas._chat_input.isVisibleTo(pair.app.window)
    assert not canvas._chat_input.isEnabled()
    assert _composer_state(canvas) == pair.draft
    # Neither a queued Enter nor a direct signal can feed Art's notes to Jamulus.
    canvas._chat_input.returnPressed.emit()
    canvas.chat_submitted.emit(_DRAFT)
    assert _composer_state(canvas) == pair.draft
    assert _notes_state(canvas) == notes

    _click_talk_share(pair, qapp)

    _assert_conversation(pair)
    assert _notes_state(canvas) == notes
    assert _composer_state(canvas) == pair.draft
    assert notes_path.read_bytes() == saved
    _assert_private_and_unchanged(pair, caplog)
    _open_notes(pair, qapp)
    assert _notes_state(canvas) == notes
    canvas._notes.undo()
    assert canvas.current_notes() == _NOTES
    canvas._notes.redo()
    assert canvas.current_notes() == _NOTES + _INSERT
    panel = pair.app.window.webex_embed
    _click_talk_share(pair, qapp)
    assert pair.app.window.webex_embed is panel
    _assert_conversation(pair)
    assert canvas.current_notes() == _NOTES + _INSERT
    assert _composer_state(canvas) == pair.draft
    _assert_private_and_unchanged(pair, caplog)
    if role != "host":
        _leave(pair, qapp)
        _open_notes(pair, qapp)
        assert canvas._chat_input.isVisibleTo(pair.app.window) is (profile == "music")
        assert canvas._chat_input.isEnabled() is (profile == "music")
        assert canvas.current_notes() == (_MUSIC_NOTES if profile == "music" else _NOTES + _INSERT)
        # A stale Art route cannot move the returned Music workspace.
        if profile == "music":
            conversation_visible = pair.app.window.webex_embed.isVisibleTo(pair.app.window)
            canvas.talk_share_requested.emit()
            qapp.processEvents()
            assert canvas.isVisibleTo(pair.app.window)
            assert pair.app.window.webex_embed.isVisibleTo(pair.app.window) is conversation_visible
        pair.app.jamulus.send_chat.assert_not_called()


@pytest.mark.parametrize("role", ["lan", "native"])
def test_queued_music_chat_cannot_enter_art_or_returned_music_notes(
    room, qapp, monkeypatch, caplog, role,
):
    pair = room(role=role, profile="music", connect=False)
    app = pair.app
    queued = []
    with monkeypatch.context() as patch:
        patch.setattr(app._ui_invoker, "invoke", queued.append)
        app._on_jamulus_chat(f"<b>{_INCOMING}</b>")
    assert len(queued) == 1
    pair.connect()
    canvas = _open_notes(pair, qapp)
    notes = _make_notes(pair)
    queued[0]()
    app._on_jamulus_chat(_INCOMING)
    qapp.processEvents()
    assert _notes_state(canvas) == notes
    _click_talk_share(pair, qapp)
    _assert_conversation(pair)
    _assert_private_and_unchanged(pair, caplog)
    _leave(pair, qapp)
    queued[0]()
    qapp.processEvents()
    assert canvas.current_notes() == _MUSIC_NOTES
    assert _composer_state(canvas) == pair.draft
    # A new receipt in returned Music remains usable; only the old epoch expired.
    app._on_jamulus_chat("Current band message")
    drain(qapp, lambda: "Current band message" in canvas.current_notes())
    assert _INCOMING not in canvas.current_notes()
    assert _INCOMING not in caplog.text


@pytest.mark.parametrize("role", ["lan", "native"])
@pytest.mark.parametrize("cleanup", ["room", "shutdown"])
def test_art_notes_conversation_respects_current_cleanup_owner(
    room, qapp, caplog, role, cleanup,
):
    pair = room(role=role)
    app = pair.app
    canvas = _open_notes(pair, qapp)
    notes = _make_notes(pair)
    if cleanup == "room":
        app.audio.require_cleanup_retry(
            hosting=False, art_room=True, error="The room connection is still closing.",
            title="Finish leaving the room",
        )
    else:
        app._shutdown_cleanup_pending = True
        app._update_session_hud()
    # The existing Art timer projects direct owner cleanup into Notes.
    drain(qapp, lambda: not canvas.talk_share_button().isEnabled())
    canvas.talk_share_requested.emit()
    qapp.processEvents()
    assert canvas.isVisibleTo(app.window)
    assert not app.window.webex_embed.isVisibleTo(app.window)
    assert _notes_state(canvas) == notes
    assert _composer_state(canvas) == pair.draft
    _assert_private_and_unchanged(pair, caplog)


@pytest.mark.parametrize("role", ["lan", "native"])
def test_conversation_preserves_an_unsaved_art_notes_draft(
    room, qapp, monkeypatch, caplog, role,
):
    pair = room(role=role)
    app = pair.app
    canvas = _open_notes(pair, qapp)
    _make_notes(pair)
    canvas._notes.insertPlainText(" PRIVATE_PENDING_EDIT")
    with monkeypatch.context() as patch:
        patch.setattr(
            "webjam_qt.controllers.session_persistence.atomic_write_text",
            Mock(side_effect=OSError("Controlled unavailable notes destination")),
        )
        assert app._save_notes() is False
        notes = _notes_state(canvas)
        assert canvas._notes_save_state == "failed"
        pending = dict(app._persistence._pending_notes)
        _click_talk_share(pair, qapp)
        _assert_conversation(pair)
        assert _notes_state(canvas) == notes
        assert app._persistence._pending_notes == pending
        _open_notes(pair, qapp)
        assert _notes_state(canvas) == notes
        _assert_private_and_unchanged(pair, caplog)
    assert app._save_notes()
