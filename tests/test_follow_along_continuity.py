"""Ordinary navigation and first saves preserve intent, not old actions."""
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QAction
from core.session_library import SessionLibrary

from core.session_transfer import SessionCredentials
from tests.test_follow_along_journey import (
    CANONICAL, LINK, music, qapp as qapp, room as room,
    external_handoffs as external_handoffs,
    no_unhandled_qt_slot_errors as no_unhandled_qt_slot_errors,
)
from tests.test_art_shared_lesson_journey import _paint_along


def begin(room, qapp, monkeypatch, profile):
    if profile == "music":
        app = music(room, qapp)
        app.follow_along.show_music_choices()
        app.follow_along._choice.buttons["lesson"].click()
    else:
        pair = room(role="host", profile="art", configured=True)
        app = pair.app
        app.window.show()
        qapp.processEvents()
        _paint_along(pair, qapp)._watch_lesson_button.click()
    monkeypatch.setattr("PySide6.QtWidgets.QInputDialog.getText", lambda *a, **k: (LINK, True))
    panel = app.follow_along.panel
    panel.choose_button.click()
    assert panel.lesson_url == CANONICAL
    return app, panel


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("via_notes", [False, True])
def test_conversation_returns_to_owned_lesson(room, qapp, monkeypatch, external_handoffs, profile, via_notes):
    app, panel = begin(room, qapp, monkeypatch, profile)
    generation = panel.generation
    if via_notes:
        app.window.side_rail.trigger("canvas")
        qapp.processEvents()
        assert app.window.session_canvas.isVisibleTo(app.window)
    app.window.session_strip._video_button.click()
    qapp.processEvents()
    assert panel.isVisibleTo(app.window)
    assert panel.lesson_url == CANONICAL
    external_handoffs[1].assert_not_called()
    app.follow_along.open_lesson(generation)
    external_handoffs[1].assert_not_called()
    panel.open_button.click()
    assert external_handoffs[1].call_args.args[0].toString() == CANONICAL
    if profile == "music":
        assert app.window.webex_embed._mute_btn.isHidden()


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("via_notes", [False, True])
def test_first_library_save_keeps_current_lesson_usable(room, qapp, monkeypatch, external_handoffs, profile, via_notes):
    with monkeypatch.context() as disk_failure:
        if profile == "art":
            disk_failure.setattr(SessionLibrary, "create", Mock(side_effect=OSError("Disk unavailable")))
        app, panel = begin(room, qapp, monkeypatch, profile)
    assert app.session_library.current is None
    generation = panel.generation
    if via_notes:
        app.window.side_rail.trigger("canvas")
    action = next(action for action in app.window.findChildren(QAction)
                  if "Session library" in action.text())
    action.trigger()
    qapp.processEvents()
    assert app.session_library.current is not None
    app.session_library.dialog.reject()
    if via_notes:
        app.window.session_strip._video_button.click()
    qapp.processEvents()
    assert panel.isVisibleTo(app.window) and panel.lesson_url == CANONICAL
    app.follow_along.open_lesson(generation)
    external_handoffs[1].assert_not_called()
    panel.open_button.click()
    assert external_handoffs[1].call_args.args[0].toString() == CANONICAL


@pytest.mark.parametrize("profile", ["music", "art"])
@pytest.mark.parametrize("same_link", [False, True])
def test_meeting_edit_while_in_notes_preserves_choice_without_old_actions(
    room, qapp, monkeypatch, external_handoffs, profile, same_link,
):
    app, panel = begin(room, qapp, monkeypatch, profile)
    generation = panel.generation
    app.window.side_rail.trigger("canvas")
    url = app._effective_meeting_url() if same_link else "https://studio.webex.com/meet/new"
    app._set_session_meeting_url(url)
    app.follow_along.open_lesson(generation)
    external_handoffs[1].assert_not_called()
    app.window.session_strip._video_button.click()
    qapp.processEvents()
    assert panel.isVisibleTo(app.window) and panel.lesson_url == CANONICAL
    app.follow_along.open_lesson(generation)
    external_handoffs[1].assert_not_called()
    panel.open_button.click()
    assert external_handoffs[1].call_args.args[0].toString() == CANONICAL


def test_music_browser_choice_survives_initial_host_credentials(room, qapp, monkeypatch, external_handoffs):
    app, panel = begin(room, qapp, monkeypatch, "music")
    assert app._reference_video_identity() == ("", "", "")
    app.host_peer.active = True
    app.host_peer.credentials = SessionCredentials.create()
    assert app._reference_video_identity() != ("", "", "")
    panel.open_button.click()
    assert external_handoffs[1].call_args.args[0].toString() == CANONICAL


@pytest.mark.parametrize("change", ["room", "profile", "ensemble"])
def test_retired_activity_cannot_resume_from_conversation(room, qapp, monkeypatch, external_handoffs, change):
    app, panel = begin(room, qapp, monkeypatch, "music")
    app.window.side_rail.trigger("canvas")
    if change == "room":
        assert app._stop_session_peer()
    elif change == "profile":
        app._apply_creator_profile_key("podcast_voice")
    else:
        monkeypatch.setattr(app, "_open_reference_track", Mock())
        app.follow_along.show_music_choices()
        app.follow_along._choice.buttons["ensemble"].click()
    app.window.session_strip._video_button.click()
    qapp.processEvents()
    assert not panel.isVisibleTo(app.window)
    external_handoffs[1].assert_not_called()
