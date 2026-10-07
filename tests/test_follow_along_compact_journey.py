"""Real composed windows keep readable guidance and reveal focused actions."""
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QLabel

from tests.test_art_shared_lesson_journey import _paint_along
from tests.test_follow_along_journey import (
    music, qapp as qapp, room as room,
    no_unhandled_qt_slot_errors as no_unhandled_qt_slot_errors,
)
from tests.test_composed_music_invitation_conversation import copied_room as copied_room, ROOM


def settle(qapp):
    for _ in range(6):
        qapp.processEvents()


def assert_reachable(app, root, qapp, size, *, in_room=True):
    window = app.window
    for widget in window.findChildren(QLabel) + window.findChildren(QAbstractButton):
        widget.setStyleSheet(widget.styleSheet() + "\nfont-size: 22px;")
    window.resize(*size)
    window.show()
    window.raise_()
    window.activateWindow()
    settle(qapp)
    assert QTest.qWaitForWindowActive(window, 2000)
    assert window.size().width() == size[0] and window.size().height() == size[1]
    assert window.rect().contains(QRect(root.mapTo(window, QPoint()), root.size()))
    for label in root.findChildren(QLabel) + window.session_hud.findChildren(QLabel):
        if not label.isVisibleTo(window):
            continue
        required = label.heightForWidth(label.width()) if label.wordWrap() else label.minimumSizeHint().height()
        assert label.height() >= required, label.accessibleName() or label.text()
    expected = {button for button in root.findChildren(QAbstractButton)
                if button.isVisibleTo(window) and button.isEnabled()}
    assert expected
    behavior = qapp.styleHints().tabFocusBehavior()
    qapp.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    try:
        remaining = set(expected)
        for _ in range(180):
            QTest.keyClick(window, Qt.Key.Key_Tab)
            settle(qapp)
            focused = qapp.focusWidget()
            if focused in remaining:
                assert focused.visibleRegion().contains(focused.rect()), focused.text()
                assert window.rect().contains(QRect(focused.mapTo(window, QPoint()), focused.size()))
                assert focused.width() >= focused.minimumSizeHint().width()
                remaining.remove(focused)
            if not remaining:
                break
        assert not remaining, [button.text() for button in remaining]
    finally:
        qapp.styleHints().setTabFocusBehavior(behavior)
    # Idle Music has no session to end; connected cases must keep End/Leave.
    action = window.session_strip._audio_button
    if in_room:
        assert action.isVisibleTo(window) and action.isEnabled()
        assert action.visibleRegion().contains(action.rect())
    else:
        assert not action.isVisibleTo(window)


@pytest.mark.parametrize("profile", ["art", "music"])
@pytest.mark.parametrize("hosting", [False, True])
@pytest.mark.parametrize("size", [(720, 560), (1040, 720)])
def test_lesson_helper_fits_and_scrolls_with_expanded_sound_tips(room, qapp, monkeypatch, profile, hosting, size):
    if profile == "art":
        pair = room(role="host" if hosting else "native", profile="art", configured=True)
        app = pair.app
        _paint_along(pair, qapp)._watch_lesson_button.click()
    else:
        app = music(room, qapp, hosting=hosting)
        monkeypatch.setattr(app, "_reference_track_is_host", lambda: hosting)
        app.follow_along.show_music_choices()
        app.follow_along._choice.buttons["lesson"].click()
    card = app.window.webex_embed
    if hosting:
        app.follow_along.activate(hosting=True, lesson_url="https://www.youtube.com/watch?v=M7lc1UVf-VE&t=90s")
    for expanded in (False, True):
        card._sound_tips_button.setChecked(expanded)
        assert_reachable(app, card, qapp, size, in_room=profile == "art")


@pytest.mark.parametrize("hosting", [False, True])
def test_initial_art_entry_keeps_both_routes_readable(room, qapp, hosting):
    pair = room(role="host" if hosting else "native", profile="art", configured=True)
    dialog = _paint_along(pair, qapp)
    assert_reachable(pair.app, dialog, qapp, (720, 560))
    assert dialog._watch_lesson_button.y() < dialog._headline.y()


@pytest.mark.parametrize("size", [(720, 560), (1040, 720)])
def test_connected_music_guest_can_reach_setup_and_leave(copied_room, qapp, size):
    def inspect(pair):
        app = pair.app
        app.window.show()
        settle(qapp)
        assert not app._reference_track_is_host()
        app.follow_along.show_music_choices()
        app.follow_along._choice.buttons["lesson"].click()
        card = app.window.webex_embed
        card._sound_tips_button.setChecked(True)
        assert_reachable(app, card, qapp, size)
        assert "Leave" in app.window.session_strip._audio_button.text()
    copied_room(ROOM, inspect)
