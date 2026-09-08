"""Paint along can lead to a shared meeting lesson without opening media."""

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from core.creative_modes import get_creator_profile_by_key
from core.reference_video import (
    ReferenceVideoFollowSnapshot,
    ReferenceVideoFollowState,
    ReferenceVideoSnapshot,
    ReferenceVideoState,
)
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.webex_embed import WebexEmbed
from webjam_qt.windows.reference_video import ReferenceVideoDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def theme(qapp):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())
    yield
    qapp.setStyleSheet(previous)


def _settle(qapp):
    for _ in range(4):
        qapp.processEvents()


def _rect(widget, owner):
    return QRect(widget.mapTo(owner, QPoint()), widget.size())


def _assert_contained(widget, owner):
    assert owner.rect().contains(_rect(widget, owner))
    if isinstance(widget, QLabel) and widget.wordWrap():
        assert widget.height() >= widget.heightForWidth(widget.width())
    else:
        assert widget.width() >= widget.sizeHint().width()


@pytest.mark.parametrize("hosting", (True, False))
def test_empty_paint_along_offers_shared_lesson_without_requiring_a_file(qapp, hosting):
    panel = ReferenceVideoDialog(hosting=hosting)
    panel.set_embedded(True)
    panel.resize(720, 560)
    panel.show()
    qapp.processEvents()
    try:
        offered = [button for button in panel.findChildren(QPushButton)
                   if button.text() == "Watch a shared lesson"
                   and button.isVisibleTo(panel) and button.isEnabled()]
        assert len(offered) == 1
        assert offered[0].objectName() == "GhostButton"
        assert offered[0].y() < panel._headline.y()
    finally:
        panel.close()
        panel.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("state", tuple(ReferenceVideoState))
def test_host_lesson_navigation_does_not_depend_on_video_state(qapp, state):
    panel = ReferenceVideoDialog(hosting=True)
    panel.set_embedded(True)
    panel.resize(720, 560)
    shared = state in {ReferenceVideoState.READY, ReferenceVideoState.PLAYING, ReferenceVideoState.PAUSED}
    events = []
    for name in ("watch_lesson_requested", "play_requested", "pause_requested", "seek_requested"):
        getattr(panel, name).connect(lambda *args, event=name: events.append(event))
    snapshot = ReferenceVideoSnapshot(
        state=state, shared=shared, duration_s=120.0 if shared else 0.0,
        identity_digest="a" * 64 if shared else "",
    )
    panel.set_host_snapshot(snapshot)
    panel.show()
    _settle(qapp)
    try:
        button = panel._watch_lesson_button
        assert button.isVisibleTo(panel) and button.isEnabled()
        _assert_contained(button, panel)
        _assert_contained(panel._lesson_hint, panel)
        _assert_contained(panel._hint, panel)
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
        assert events == ["watch_lesson_requested"]
        assert panel._last_host_snapshot is snapshot
        assert panel._attached_surface is None
    finally:
        panel.close()
        panel.deleteLater()
        _settle(qapp)


@pytest.mark.parametrize("state", tuple(ReferenceVideoFollowState))
def test_guest_can_reach_lesson_help_without_gaining_playback_authority(qapp, state):
    panel = ReferenceVideoDialog(hosting=False)
    panel.set_embedded(True)
    panel.resize(720, 560)
    snapshot = ReferenceVideoFollowSnapshot(state=state, duration_s=120.0, target_position_s=30.0)
    panel.set_follow_snapshot(snapshot)
    events = []
    for name in ("watch_lesson_requested", "play_requested", "pause_requested", "seek_requested"):
        getattr(panel, name).connect(lambda *args, event=name: events.append(event))
    panel.show()
    _settle(qapp)
    try:
        button = panel._watch_lesson_button
        assert button.isVisibleTo(panel) and button.isEnabled()
        _assert_contained(button, panel)
        _assert_contained(panel._lesson_hint, panel)
        _assert_contained(panel._hint, panel)
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert events == ["watch_lesson_requested"]
        assert panel._last_follow_snapshot is snapshot
        assert not panel._position.isEnabled()
        assert panel._attached_surface is None
    finally:
        panel.close()
        panel.deleteLater()
        _settle(qapp)


@pytest.mark.parametrize("hosting", (True, False))
def test_disabled_or_hidden_lesson_action_drops_queued_widget_input(qapp, hosting):
    panel = ReferenceVideoDialog(hosting=hosting)
    panel.set_embedded(True)
    panel.resize(720, 560)
    events = []
    panel.watch_lesson_requested.connect(lambda: events.append("lesson"))
    panel.show()
    _settle(qapp)
    try:
        panel.set_watch_lesson_available(False)
        panel._watch_lesson_button.clicked.emit()
        assert events == []
        panel.set_watch_lesson_available(True)
        panel.hide()
        panel._watch_lesson_button.clicked.emit()
        assert events == []
        panel.show()
        _settle(qapp)
        panel._watch_lesson_button.click()
        assert events == ["lesson"]
    finally:
        panel.close()
        panel.deleteLater()
        _settle(qapp)


@pytest.fixture
def card(qapp):
    owner = QWidget()
    layout = QVBoxLayout(owner)
    layout.setContentsMargins(0, 0, 0, 0)
    panel = WebexEmbed()
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    layout.addWidget(panel)
    layout.addStretch(1)
    events = []
    for name in ("bring_forward_requested", "open_meeting_requested", "change_link_requested",
                 "mute_in_webex_requested", "install_webex_requested", "meeting_state_changed"):
        getattr(panel, name).connect(lambda *args, event=name: events.append(event))
    yield owner, panel, events
    owner.close()
    owner.deleteLater()
    _settle(qapp)


@pytest.mark.parametrize("hosting", (True, False))
@pytest.mark.parametrize("service", ("", "Webex", "Google Meet"))
def test_lesson_context_explains_the_selected_provider_without_launching(card, qapp, hosting, service):
    owner, panel, events = card
    panel.set_service_label(service)
    panel.set_meeting_configured(bool(service))
    normal = panel._mode_label.text()
    panel.set_shared_lesson_context(hosting)
    text = panel._mode_label.text()
    assert "YouTube" in text and "faces" in text
    assert "pause" in text.casefold() and "resume" in text
    assert "speaker volume" in text and "microphone mute" in text
    if hosting:
        assert "computer sound" in text and "shared lesson" in text
        assert "YouTube player volume" in text
        if service == "Webex":
            assert "Share" in text and "Include computer sound" in text
            assert "Browser meeting" in text and "tab audio" in text
    else:
        assert "Ask the host" in text and "host controls the browser" in text
    if service:
        assert service in text
    if service != "Webex":
        assert "Webex" not in text
    assert panel._title_label.text() == "Conversation"
    assert events == []
    panel.set_shared_lesson_context(None)
    assert panel._mode_label.text() == normal
    assert events == []


@pytest.mark.parametrize("hosting", (True, False))
@pytest.mark.parametrize("width", (320, 390, 760))
def test_lesson_card_fits_compact_width_without_changing_primary_action(card, qapp, hosting, width):
    owner, panel, events = card
    owner.resize(width, 900)
    panel.set_service_label("Webex")
    panel.set_meeting_configured(True)
    panel.set_app_status("installed", publisher_verified=True)
    panel.set_launch_status("Not opened")
    owner.show()
    _settle(qapp)
    panel.focus_primary_action()
    focused = QApplication.focusWidget()
    assert focused is panel._fallback_btn
    panel.set_shared_lesson_context(hosting)
    _settle(qapp)
    assert panel.width() == width
    assert QApplication.focusWidget() is focused
    labels = (panel._title_label, panel._mode_label, panel._status_label, panel._app_status_label)
    visible = [label for label in labels if label.isVisibleTo(panel)]
    visible.extend(button for button in panel.findChildren(QPushButton) if button.isVisibleTo(panel))
    for widget in visible:
        _assert_contained(widget, panel)
    for index, widget in enumerate(visible):
        for other in visible[index + 1:]:
            assert not _rect(widget, panel).intersects(_rect(other, panel))
    assert events == []


def test_lesson_context_does_not_leak_through_music_profile_or_native_status(card):
    _, panel, events = card
    panel.set_shared_lesson_context(True)
    host_text = panel._mode_label.text()
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    assert panel._mode_label.text() == host_text
    panel.set_app_status("not-installed")
    assert panel._mode_label.text() == host_text
    panel.set_service_label("Google Meet")
    assert "Google Meet" in panel._mode_label.text()
    assert "Webex" not in panel._mode_label.text()
    panel.set_creator_profile(get_creator_profile_by_key("music"))
    assert "YouTube" not in panel._mode_label.text()
    panel.set_shared_lesson_context(True)
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    assert "YouTube" not in panel._mode_label.text()
    assert "own tools" in panel._mode_label.text()
    assert events == []


@pytest.mark.parametrize("hosting", (True, False))
def test_lesson_actions_fit_larger_font_through_narrow_wide_roundtrip(card, qapp, hosting):
    owner, panel, events = card
    # A larger ordinary font reproduces the Linux card's two-column overflow
    # on macOS too. The product must fit, without changing the user's font.
    panel.setStyleSheet('QPushButton { font-family: "Helvetica"; font-size: 13pt; }')
    panel.set_service_label("Webex")
    panel.set_meeting_configured(True)
    panel.set_app_status("installed", publisher_verified=True)
    panel.set_launch_status("Not opened")
    owner.resize(320, 1000)
    owner.show()
    _settle(qapp)
    panel.focus_primary_action()
    focused = QApplication.focusWidget()
    assert focused is panel._fallback_btn
    buttons = tuple(panel.findChildren(QPushButton))
    for width in (320, 760, 320):
        owner.resize(width, 1000)
        panel.set_shared_lesson_context(hosting)
        _settle(qapp)
        assert panel.width() == width
        assert QApplication.focusWidget() is focused
        assert tuple(panel.findChildren(QPushButton)) == buttons
        assert panel._fallback_btn.font().pointSize() == 13
        visible = [button for button in buttons if button.isVisibleTo(panel)]
        visible.extend(label for label in panel.findChildren(QLabel) if label.isVisibleTo(panel))
        for widget in visible:
            _assert_contained(widget, panel)
        for index, widget in enumerate(visible):
            for other in visible[index + 1:]:
                assert not _rect(widget, panel).intersects(_rect(other, panel))
        open_rect = _rect(panel._fallback_btn, panel)
        edit_rect = _rect(panel._change_link_btn, panel)
        if width == 320:
            assert edit_rect.top() > open_rect.bottom()
        else:
            assert edit_rect.left() > open_rect.right()
        panel.set_shared_lesson_context(None)
        _settle(qapp)
        assert panel.width() == width
        assert QApplication.focusWidget() is focused
    assert events == []
