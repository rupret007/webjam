"""An Art guest returns to the current room while keeping local work intact."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from shiboken6 import isValid

from core.reference_video import ReferenceVideoFollowState
from core.session_conductor import ArtRoomState
from core.session_transfer import ReferenceVideoSessionSnapshot, SharedCanvasSessionSnapshot
from tests.test_paint_along_guest_journey import (
    _choose,
    _observe,
    _video,
    journey as _journey_fixture,
    qapp as _qapp_fixture,
)
from tests.test_shared_canvas_coordinator import FakeLauncher
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet

journey = _journey_fixture
base_qapp = _qapp_fixture


@pytest.fixture
def owned_players():
    return []


@pytest.fixture
def qapp(base_qapp, owned_players):
    # The reused journey depends on this fixture, so its controller/window
    # teardown finishes before we flush deletion of detached video surfaces.
    yield base_qapp
    base_qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    for group in owned_players:
        for player in group:
            assert not isValid(player.surface)


@pytest.fixture
def guest(journey, monkeypatch, tmp_path, qapp, owned_players):
    # The ApplicationController repository fixture is isolated too. Give
    # these notes their own root so cursor/undo proof never depends on a
    # previously collected test's saved draft.
    notes_home = tmp_path / "notes-home"
    notes_home.mkdir()
    monkeypatch.setattr(
        "webjam_qt.controllers.session_persistence._persistence_home", lambda: notes_home,
    )
    previous_theme = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())
    launcher = FakeLauncher()
    monkeypatch.setattr("services.drawpile_service.create_canvas_launcher", lambda settings: launcher)

    def create(profile="music"):
        app: ApplicationController
        app, invite, root, players = journey(profile)
        owned_players.append(players)
        app.window.resize(760, 600)
        app.window.activateWindow()
        qapp.processEvents()
        app.host_peer.publish_reference_video_state = Mock()
        app.host_peer.publish_shared_canvas_state = Mock()
        room = app._room_participant
        return SimpleNamespace(
            app=app, invite=invite, root=root, players=players, launcher=launcher,
            owner=room.lan_guest, generation=room.generation, saved_profile=profile,
        )

    yield create
    qapp.setStyleSheet(previous_theme)


def _click(button, qapp):
    assert button.isVisible() and button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    qapp.processEvents()


def _notes_snapshot(app):
    canvas = app.window.session_canvas
    cursor = canvas._notes.textCursor()
    path = app._persistence._notes_path()
    return {
        "text": canvas.current_notes(),
        "position": cursor.position(),
        "anchor": cursor.anchor(),
        "undo": canvas._notes.document().isUndoAvailable(),
        "redo": canvas._notes.document().isRedoAvailable(),
        "save_state": app._persistence.notes_save_state,
        "profile": app._persistence.profile_key,
        "pending": dict(app._persistence._pending_notes),
        "disk": path.read_bytes() if path.exists() else None,
    }


def _open_notes(pair, qapp, *, edit=True):
    app = pair.app
    app.window.session_strip._notes_action.trigger()
    qapp.processEvents()
    canvas = app.window.session_canvas
    assert canvas.isVisibleTo(app.window)
    assert not app.window.art_room_overview.isVisibleTo(app.window)
    if edit:
        canvas.set_notes("PRIVATE_LOCAL_NOTES: graphite and ink")
        canvas._notes.moveCursor(QTextCursor.MoveOperation.End)
        canvas._notes.setFocus()
        QTest.keyClicks(canvas._notes, " with a second idea")
        cursor = canvas._notes.textCursor()
        cursor.setPosition(4)
        cursor.setPosition(18, QTextCursor.MoveMode.KeepAnchor)
        canvas._notes.setTextCursor(cursor)
        assert canvas._notes.document().isUndoAvailable()
        assert app._save_notes()
    return _notes_snapshot(app)


def _poll(pair, qapp, offer):
    pair.owner.client.state = lambda *_: offer
    pair.owner.poll_once()
    qapp.processEvents()
    pair.app._tick_creator_start()


def _offer_canvas(pair, qapp):
    offer = replace(pair.owner.last_state, shared_canvas=SharedCanvasSessionSnapshot(
        generation=1, shared=True,
        join_url="drawpile://example.com/lesson?v1&p=PRIVATE_CANVAS_INVITATION",
        server_label="example.com", session_label="lesson",
    ))
    _poll(pair, qapp, offer)
    return offer


def _open_video_from_notes(pair, qapp):
    pair.app.window.session_strip._reference_video_action.trigger()
    qapp.processEvents()
    panel = pair.app._reference_video_dialog
    assert panel is not None and panel.isVisibleTo(pair.app.window)
    return panel


def _assert_full_room(pair):
    app = pair.app
    assert app.window.workspace_stack.currentWidget() is app.window.center_splitter
    assert app.window._room_stage.isVisibleTo(app.window)
    assert app.window.art_room_overview.isVisibleTo(app.window)
    assert not app.window.session_canvas.isVisibleTo(app.window)
    assert app.window.center_splitter.sizes()[0] > 0
    assert app.window.center_splitter.sizes()[1] == 0
    assert app._last_content_key == "stage"
    assert app.window.art_room_overview.hasFocus()


def _assert_room_and_local_work_unchanged(pair, notes, caplog):
    app, room = pair.app, pair.app._room_participant
    assert room.lan_guest is pair.owner and room.generation == pair.generation
    assert app.creator_profile.key == "art"
    assert app.settings.last_creator_profile_key == pair.saved_profile
    assert _notes_snapshot(app) == notes
    assert not pair.launcher.joined and not pair.launcher.host_pages
    app._launch_native_jamulus_for_startup.assert_not_called()
    app._start_hosted_server_for_startup.assert_not_called()
    app.bridge.launch_webex.assert_not_called()
    app.host_peer.publish_reference_video_state.assert_not_called()
    app.host_peer.publish_shared_canvas_state.assert_not_called()
    public = (repr(app.art_room_state()) + repr(app.window.art_room_overview._overview)
              + app.window.art_room_overview.accessibleDescription() + caplog.text)
    for private in ("PRIVATE_LOCAL_NOTES", "PRIVATE_CANVAS_INVITATION", "PRIVATE_VIDEO"):
        assert private not in public


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("entry", ["explicit", "first_host_offer"])
def test_paint_along_back_restores_the_full_room_after_compact_notes(
    guest, monkeypatch, qapp, caplog, profile, entry,
):
    pair = guest(profile)
    path = _video(pair.root / "PRIVATE_VIDEO.mp4", b"a process to watch locally")
    if entry == "explicit":
        _observe(pair.app, pair.invite, path, playing=False)
    notes = _open_notes(pair, qapp)
    if entry == "first_host_offer":
        assert pair.app._reference_video_dialog is None
        _observe(pair.app, pair.invite, path, playing=False)
        panel = pair.app._reference_video_dialog
        assert panel.isVisibleTo(pair.app.window)
    else:
        panel = _open_video_from_notes(pair, qapp)
    # A new authenticated canvas arrives while the room overview is hidden.
    _offer_canvas(pair, qapp)
    assert panel.isVisibleTo(pair.app.window)
    assert not pair.app.window.art_room_overview.isVisibleTo(pair.app.window)

    _click(panel._back_button, qapp)

    _assert_full_room(pair)
    assert pair.app.window.art_room_overview._overview.phase == "connected"
    assert pair.app._room_participant.state is ArtRoomState.CONNECTED
    assert set(pair.app.window.art_room_overview._overview.activity_actions) == {"canvas", "video"}
    assert pair.players == []
    _assert_room_and_local_work_unchanged(pair, notes, caplog)
    # Navigation did not replace the QTextDocument or silently flatten undo.
    editor = pair.app.window.session_canvas._notes
    editor.undo()
    assert editor.toPlainText() != notes["text"]
    editor.redo()
    assert editor.toPlainText() == notes["text"]
    assert pair.app._save_notes()


@pytest.mark.parametrize("origin", ["paint_along", "notes"])
@pytest.mark.parametrize("hidden", [False, True])
def test_return_preserves_one_local_video_copy_and_the_guests_hide_choice(
    guest, monkeypatch, qapp, caplog, origin, hidden,
):
    pair = guest()
    app = pair.app
    path = _video(pair.root / "PRIVATE_VIDEO.mp4", b"a proven local copy")
    video = _observe(app, pair.invite, path, playing=False)
    _choose(app, monkeypatch, path)
    player = pair.players[0]
    if hidden:
        _click(app._reference_video_dialog._hide_button, qapp)
    expected_state = ReferenceVideoFollowState.HIDDEN if hidden else ReferenceVideoFollowState.FOLLOWING
    assert video.follow_snapshot.state is expected_state
    notes = _open_notes(pair, qapp)
    _offer_canvas(pair, qapp)
    before = (player.state, player.position, list(player.seeks), list(player.loads))
    if origin == "paint_along":
        button = _open_video_from_notes(pair, qapp)._back_button
    else:
        button = app.window.session_canvas.room_return_button()

    _click(button, qapp)

    _assert_full_room(pair)
    assert app._reference_video is video
    assert video.follow_snapshot.state is expected_state
    assert len(pair.players) == 1 and player.muted
    assert (player.state, player.position, player.seeks, player.loads) == before
    _assert_room_and_local_work_unchanged(pair, notes, caplog)
    # Notes remain reachable with the same draft and selection after returning.
    assert _open_notes(pair, qapp, edit=False) == notes


@pytest.mark.parametrize("origin", ["paint_along", "notes"])
@pytest.mark.parametrize("change", [
    "withdraw_canvas", "withdraw_video", "reconnecting", "failed", "cleanup",
])
def test_return_shows_current_room_facts_after_an_update_while_working(
    guest, qapp, caplog, origin, change,
):
    pair = guest()
    app = pair.app
    path = _video(pair.root / "PRIVATE_VIDEO.mp4", b"a pending local choice")
    _observe(app, pair.invite, path, playing=False)
    offer = _offer_canvas(pair, qapp)
    notes = _open_notes(pair, qapp)
    if origin == "paint_along":
        button = _open_video_from_notes(pair, qapp)._back_button
    else:
        button = app.window.session_canvas.room_return_button()
    if change == "withdraw_canvas":
        _poll(pair, qapp, replace(offer, shared_canvas=SharedCanvasSessionSnapshot(generation=2)))
        phase, actions = "connected", ("video",)
    elif change == "withdraw_video":
        _poll(pair, qapp, replace(offer, reference_video=ReferenceVideoSessionSnapshot(generation=2)))
        phase, actions = "connected", ("canvas",)
    elif change == "cleanup":
        app.audio.require_cleanup_retry(
            hosting=False, art_room=True, error="The room connection is still closing.",
            title="Finish leaving the room",
        )
        phase, actions = "cleanup_required", ()
    else:
        app._room_participant.lose_lan(pair.owner, pair.generation, change == "failed")
        phase, actions = change, ()
    assert button.isVisibleTo(app.window)

    _click(button, qapp)

    _assert_full_room(pair)
    overview = app.window.art_room_overview._overview
    assert overview.phase == phase
    assert overview.activity_actions == actions
    assert overview.conversation_enabled is (change != "cleanup")
    assert pair.players == []
    _assert_room_and_local_work_unchanged(pair, notes, caplog)


@pytest.mark.parametrize("failed_save", [False, True])
def test_notes_back_preserves_saved_or_failed_local_draft_and_conversation(
    guest, monkeypatch, qapp, caplog, failed_save,
):
    pair = guest()
    app = pair.app
    app._show_webex_conversation()
    notes = _open_notes(pair, qapp)
    conversation_visible = app.window.webex_embed.isVisible()
    with monkeypatch.context() as failure:
        if failed_save:
            def refuse_write(*args, **kwargs):
                raise OSError("PRIVATE_DISK_DETAIL")
            failure.setattr("webjam_qt.controllers.session_persistence.atomic_write_text", refuse_write)
            editor = app.window.session_canvas._notes
            editor.moveCursor(QTextCursor.MoveOperation.End)
            QTest.keyClicks(editor, " kept locally")
            assert app._save_notes() is False
            notes = _notes_snapshot(app)
            assert notes["save_state"] == "failed" and notes["pending"]

        _click(app.window.session_canvas.room_return_button(), qapp)

        _assert_full_room(pair)
        assert app.window.webex_embed.isVisible() is conversation_visible
        assert app.window.art_room_overview._overview.activity_actions == ()
        _assert_room_and_local_work_unchanged(pair, notes, caplog)
    assert app._save_notes()


@pytest.mark.parametrize("retired", ["replaced_dialog", "profile_swap", "shutdown"])
def test_retired_paint_along_return_cannot_hijack_the_current_workspace(
    guest, qapp, retired,
):
    pair = guest()
    app = pair.app
    path = _video(pair.root / "PRIVATE_VIDEO.mp4", b"an older panel")
    _observe(app, pair.invite, path, playing=False)
    old_dialog = app._reference_video_dialog
    app._release_reference_video()
    if retired == "replaced_dialog":
        app._open_reference_video()
        assert app._reference_video_dialog is not old_dialog
    elif retired == "profile_swap":
        app._apply_creator_profile_key("music", host_owned=False)
    app._on_rail_view_changed("canvas")
    qapp.processEvents()
    workspace = app.window.workspace_stack.currentWidget()
    if retired == "shutdown":
        app._shutdown = True
    try:
        # Model the queued callback's captured old dialog, including after
        # its underlying Qt widget has already been deleted.
        app._return_to_art_room(old_dialog)
        if retired != "replaced_dialog":
            app.window.session_canvas.return_to_room_requested.emit()
        assert app.window.workspace_stack.currentWidget() is workspace
        assert app.window.session_canvas.isVisibleTo(app.window)
        assert not app.window.art_room_overview.isVisibleTo(app.window)
        assert app._last_content_key == "canvas"
    finally:
        if retired == "shutdown":
            app._shutdown = False


def test_escape_restores_prior_notes_then_explicit_back_selects_the_room(
    guest, qapp, caplog,
):
    pair = guest()
    app = pair.app
    path = _video(pair.root / "PRIVATE_VIDEO.mp4", b"an offered process video")
    _observe(app, pair.invite, path, playing=False)
    _offer_canvas(pair, qapp)
    notes = _open_notes(pair, qapp)
    panel = _open_video_from_notes(pair, qapp)
    app.window.activateWindow()
    panel.setFocus()
    qapp.processEvents()

    QTest.keyClick(panel, Qt.Key.Key_Escape)
    qapp.processEvents()

    assert app.window.workspace_stack.currentWidget() is app.window.center_splitter
    assert app.window.session_canvas.isVisibleTo(app.window)
    assert not app.window.art_room_overview.isVisibleTo(app.window)
    assert app._last_content_key == "canvas"
    _assert_room_and_local_work_unchanged(pair, notes, caplog)

    same_panel = _open_video_from_notes(pair, qapp)
    assert same_panel is panel
    _click(panel._back_button, qapp)

    _assert_full_room(pair)
    assert set(app.window.art_room_overview._overview.activity_actions) == {"canvas", "video"}
    assert pair.players == []
    _assert_room_and_local_work_unchanged(pair, notes, caplog)
