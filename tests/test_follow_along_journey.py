"""Explicit lesson choices survive useful edits, never canceled owners."""
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest

from core.reference_video import ReferenceVideoSnapshot, ReferenceVideoState
from tests.test_art_shared_lesson_journey import (
    _paint_along, external_handoffs as external_handoffs,
    no_unhandled_qt_slot_errors as no_unhandled_qt_slot_errors,
    qapp as qapp, room as room,
)

LINK = "https://youtu.be/M7lc1UVf-VE?t=90"
CANONICAL = "https://www.youtube.com/watch?v=M7lc1UVf-VE&t=90s"


def music(room, qapp, *, hosting=True):
    pair = room(role="host" if hosting else "native", profile="music", connect=False)
    pair.app.window.show()
    qapp.processEvents()
    return pair.app


@pytest.mark.parametrize("hosting", [True, False])
def test_music_ensemble_opens_role_owned_tool(room, qapp, monkeypatch, hosting):
    app = music(room, qapp, hosting=hosting)
    monkeypatch.setattr(app, "_reference_track_is_host", lambda: hosting)
    track, mixer = Mock(), Mock()
    monkeypatch.setattr(app, "_open_reference_track", track)
    monkeypatch.setattr(app, "_bring_jamulus_forward", mixer)
    app.window.session_strip._play_along_button.click()
    qapp.processEvents()
    choice = app.follow_along._choice
    assert choice.isVisible()
    choice.buttons["ensemble"].setFocus()
    QTest.keyClick(choice.buttons["ensemble"], Qt.Key.Key_Space)
    qapp.processEvents()
    assert track.call_count == int(hosting)
    assert mixer.call_count == int(not hosting)
    assert app.follow_along._choice is None


def test_music_lesson_meeting_edit_rebinds_and_old_click_is_rejected(room, qapp, monkeypatch, external_handoffs):
    app = music(room, qapp)
    app.follow_along.show_music_choices()
    app.follow_along._choice.buttons["lesson"].click()
    qapp.processEvents()
    panel = app.follow_along.panel
    assert panel.isVisibleTo(app.window)
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", lambda *a, **k: (LINK, True))
    panel.choose_button.click()
    assert panel.lesson_url == CANONICAL
    external_handoffs[1].assert_not_called()
    old_generation = panel.generation
    app._set_session_meeting_url("https://studio.webex.com/meet/lesson")
    app.follow_along.open_lesson(old_generation)
    external_handoffs[1].assert_not_called()
    panel.open_button.click()
    assert external_handoffs[1].call_args.args[0].toString() == CANONICAL
    app._release_reference_video()  # Passive Music state reads must preserve it.
    assert panel.isVisibleTo(app.window)
    assert panel.lesson_url == CANONICAL
    assert app._stop_session_peer()
    qapp.processEvents()
    assert not panel.isVisibleTo(app.window)


def test_closed_music_chooser_can_reopen_after_meeting_change(room, qapp):
    app = music(room, qapp)
    app.follow_along.show_music_choices()
    old = app.follow_along._choice
    old.activateWindow()
    qapp.processEvents()
    QTest.keyClick(old, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert app.follow_along._choice is None
    app._set_session_meeting_url("https://studio.webex.com/meet/new")
    app.follow_along.show_music_choices()
    current = app.follow_along._choice
    assert current is not old
    current.buttons["lesson"].click()
    assert app.follow_along.panel.isVisibleTo(app.window)


def test_art_factory_failure_retains_explicit_browser_fallback(room, qapp, monkeypatch, external_handoffs):
    pair = room(role="host", profile="art")
    dialog = _paint_along(pair, qapp)
    factory = Mock(side_effect=RuntimeError("native player unavailable"))
    monkeypatch.setattr("webjam_qt.widgets.youtube_video_player.create_youtube_video_player", factory)
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", lambda *a, **k: (LINK, True))
    dialog._choose_youtube_lesson()
    factory.assert_called_once()
    assert dialog.meeting_lesson_url() == CANONICAL
    dialog._watch_lesson_button.click()
    panel = pair.app.follow_along.panel
    assert panel.lesson_url == CANONICAL
    external_handoffs[1].assert_not_called()
    panel.open_button.click()
    assert external_handoffs[1].call_args.args[0].toString() == CANONICAL


@pytest.mark.parametrize("cancel", ["withdraw", "back", "local", "room"])
def test_youtube_cancellation_cannot_restore_old_browser_choice(qapp, monkeypatch, cancel):
    from webjam_qt.windows.reference_video import ReferenceVideoDialog
    panel = ReferenceVideoDialog(hosting=True)
    panel.show()
    qapp.processEvents()
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", lambda *a, **k: (LINK, True))

    def opening(_url):
        panel.set_host_snapshot(ReferenceVideoSnapshot(state=ReferenceVideoState.LOADING, source_kind="youtube"))
        if cancel == "withdraw":
            panel.withdraw_requested.emit()
        elif cancel == "back":
            panel.hide()
        elif cancel == "local":
            panel.share_requested.emit("new-local.mp4")
        else:
            panel.set_room_available(False)
        panel.set_host_snapshot(ReferenceVideoSnapshot(
            state=ReferenceVideoState.READY if cancel == "local" else ReferenceVideoState.IDLE,
            shared=cancel == "local", source_kind="local",
        ))

    panel.share_youtube_requested.connect(opening)
    try:
        panel._choose_youtube_lesson()
        assert panel.meeting_lesson_url() == ""
    finally:
        panel.close()
        panel.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("width", [760, 1040])
def test_play_along_remains_reachable_in_busy_music_toolbar(room, qapp, width):
    app = music(room, qapp)
    strip = app.window.session_strip
    strip.set_recording_available(True)
    strip.set_reference_track_available(True)
    strip.set_song_line("Verse 2 · 120 BPM")
    strip.set_audio_state("End Session")
    app.window.resize(width, 760)
    qapp.processEvents()
    try:
        assert app.window.width() == width
        assert strip._play_along_button.isVisibleTo(app.window)
        buttons = [strip._record_button, strip._audio_button, strip._video_button,
                   strip._play_along_button, strip._reference_track_button,
                   strip._song_button, strip._studio_button, strip._tools_button]
        for button in buttons:
            if button.isVisibleTo(strip):
                assert button.width() >= button.minimumSizeHint().width(), button.text()
                assert app.window.rect().contains(QRect(button.mapTo(app.window, QPoint()), button.size())), button.text()
    finally:
        strip.set_song_line("")


@pytest.mark.parametrize("embedded", [False, True])
def test_art_browser_choice_survives_notes_return_until_video_source_changes(
    room, qapp, monkeypatch, external_handoffs, embedded,
):
    from unittest.mock import PropertyMock
    pair = room(role="host", profile="art", configured=True)
    app = pair.app
    dialog = _paint_along(pair, qapp)
    if embedded:
        snapshot = ReferenceVideoSnapshot(
            state=ReferenceVideoState.READY, shared=True, source_kind="youtube",
            video_id="dQw4w9WgXcQ", identity_digest="a" * 64, duration_s=300,
        )
        monkeypatch.setattr(type(app._reference_video), "host_snapshot", PropertyMock(return_value=snapshot))
        dialog.set_host_snapshot(snapshot)
    dialog._watch_lesson_button.click()
    panel = app.follow_along.panel
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", lambda *a, **k: (LINK, True))
    panel.choose_button.click()
    assert panel.lesson_url == CANONICAL
    app.window.side_rail.trigger("canvas")
    qapp.processEvents()
    assert app.window.session_canvas.isVisibleTo(app.window)
    dialog = _paint_along(pair, qapp)
    dialog._watch_lesson_button.click()
    assert panel.lesson_url == CANONICAL
    panel.open_button.click()
    assert external_handoffs[1].call_args.args[0].toString() == CANONICAL
    dialog = _paint_along(pair, qapp)
    dialog.withdraw_requested.emit()
    dialog._watch_lesson_button.click()
    assert panel.lesson_url != CANONICAL
