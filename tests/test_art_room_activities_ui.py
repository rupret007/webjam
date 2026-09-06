"""Every offered Art activity has a reachable, factual room-panel action."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from shiboken6 import isValid

from core.art_companion import (
    ArtCompanionProjection, CanvasCompanionState, VideoCompanionState,
)
from core.art_room_overview import art_room_overview
from core.art_room_activities import art_room_activities
from core.art_room_presence import ABSENT
from core.creative_modes import get_creator_profile_by_key
from core.session_conductor import ArtRoomState
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous_font = app.font()
    previous_tab_behavior = app.styleHints().tabFocusBehavior()
    # Exercise full keyboard navigation independently of macOS's option to
    # tab through text fields only. This changes this Qt process, not the OS.
    app.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    font_ids = []
    fonts = Path(__file__).resolve().parents[1] / "webjam_qt/theme/fonts"
    for path in sorted(fonts.glob("Inter-*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            font_ids.append(font_id)
    font = QFont("Inter") if "Inter" in QFontDatabase.families() else QFont(previous_font)
    font.setPixelSize(13)
    app.setFont(font)
    try:
        yield app
    finally:
        app.styleHints().setTabFocusBehavior(previous_tab_behavior)
        app.setFont(previous_font)
        for font_id in font_ids:
            QFontDatabase.removeApplicationFont(font_id)


def _settle(qapp):
    for _ in range(4):
        qapp.processEvents()


def room_overview(canvas="ready", video="hidden", *, hosting=False, **room_changes):
    activities = art_room_activities(ArtCompanionProjection(
        in_room=True, transport_allowed=hosting,
        canvas=CanvasCompanionState(canvas),
        video=VideoCompanionState(video),
    ), hosting=hosting)
    return art_room_overview(
        state=ArtRoomState.CONNECTED, hosting=hosting,
        presence=activities[0] if activities else ABSENT,
        secondary_presence=activities[1] if len(activities) > 1 else ABSENT,
        **room_changes,
    )


@pytest.fixture
def window(qapp):
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music jam")],
        initial_mode_key="music_jam", initial_title="Making together",
    )
    window.setStyleSheet(load_stylesheet())
    window.set_creator_profile(get_creator_profile_by_key("art"))
    window.set_art_room_overview(room_overview())
    window.session_strip.set_audio_state("Leave Room")
    window.session_hud.set_state(
        "Your Art room", "Your current room connection and activities are below.",
        action_visible=False,
    )
    window.resize(720, 560)
    window.show()
    window.activateWindow()
    _settle(qapp)
    try:
        yield window
    finally:
        window.session_strip._record_clock.stop()
        window.session_strip.stop_session_clock()
        window._room_help_dialog.close()
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        assert not isValid(window)
        _settle(qapp)


def test_hidden_video_has_its_own_quiet_route_beside_canvas(window, qapp):
    panel = window.art_room_overview
    current = panel._overview
    primary = panel.activity_button()
    secondary = panel.secondary_activity_button()
    assert current.activity_actions == ("canvas", "video")
    assert primary.objectName() == "PrimaryButton"
    assert secondary.objectName() == "GhostButton"
    assert secondary.isVisibleTo(window) and secondary.isEnabled()
    assert secondary.text() == "Open Paint along"
    assert panel._secondary_activity.text() == "Paint along (hidden)"
    assert current.secondary_activity_detail in secondary.accessibleDescription()
    assert current.secondary_activity_label in panel.accessibleDescription()
    assert current.activity_label in panel.accessibleDescription()

    actions, conversations = [], []
    panel.activity_requested.connect(actions.append)
    panel.conversation_requested.connect(lambda: conversations.append(True))
    primary.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(primary, Qt.Key.Key_Space)
    QTest.keyClick(primary, Qt.Key.Key_Tab)
    _settle(qapp)
    assert secondary.hasFocus()
    QTest.keyClick(secondary, Qt.Key.Key_Space)
    QTest.keyClick(secondary, Qt.Key.Key_Tab)
    _settle(qapp)
    assert panel.conversation_button().hasFocus()
    QTest.keyClick(panel.conversation_button(), Qt.Key.Key_Space)
    assert actions == ["canvas", "video"]
    assert conversations == [True]
    # Opening a panel is navigation. This view must not imply Show video,
    # select a local file or open an external canvas merely from its click.
    assert panel._overview is current
    assert panel._secondary_activity.text() == "Paint along (hidden)"


def test_actions_read_the_current_priority_and_reject_retired_rows(window, qapp):
    panel = window.art_room_overview
    primary, secondary = panel.activity_button(), panel.secondary_activity_button()
    actions = []
    panel.activity_requested.connect(actions.append)
    # A video requiring a copy becomes primary; the same buttons now route
    # to their freshly rendered roles, not closures over the original roles.
    current = room_overview(video="needs_file")
    window.set_art_room_overview(current)
    primary.click()
    secondary.click()
    assert actions == ["video", "canvas"]

    # A queued delivery from an earlier enabled button must consult the
    # latest snapshot, even when emitted directly past Qt's disabled gate.
    window.set_art_room_overview(room_overview(stopping=True))
    primary.clicked.emit()
    secondary.clicked.emit()
    assert actions == ["video", "canvas"]

    window.set_art_room_overview(room_overview(canvas="none"))
    _settle(qapp)
    assert not panel._secondary_activity_row.isVisibleTo(window)
    assert not secondary.isVisibleTo(window)
    secondary.clicked.emit()
    assert actions == ["video", "canvas"]
    assert "Shared canvas" not in panel.accessibleDescription()
    assert not panel._secondary_activity.text()


@pytest.mark.parametrize(("canvas", "video"), [
    ("none", "hidden"), ("ready", "none"), ("none", "none"),
])
def test_absent_second_activity_leaves_no_visual_or_spoken_placeholder(
    window, qapp, canvas, video
):
    panel = window.art_room_overview
    window.set_art_room_overview(room_overview(canvas=canvas, video=video))
    _settle(qapp)
    assert not panel._secondary_activity_row.isVisibleTo(window)
    assert not panel.secondary_activity_button().isVisibleTo(window)
    assert not panel._secondary_activity.text()
    assert not panel._secondary_activity_detail.text()
    assert panel.conversation_button().isVisibleTo(window)
    assert panel.conversation_button().isEnabled()
    panel.activity_button().setFocus(Qt.FocusReason.TabFocusReason)
    if panel.activity_button().isVisibleTo(window):
        QTest.keyClick(panel.activity_button(), Qt.Key.Key_Tab)
        _settle(qapp)
        assert panel.conversation_button().hasFocus()


def test_second_activity_renders_plain_text_with_full_accessible_status(window):
    panel = window.art_room_overview
    current = replace(
        room_overview(),
        secondary_activity_label="<b>Paint & process</b>",
        secondary_activity_detail="<a href='https://example.invalid'>Own copy</a>",
        secondary_activity_action_label="Open & inspect",
    )
    window.set_art_room_overview(current)
    assert panel._secondary_activity.textFormat() == Qt.TextFormat.PlainText
    assert panel._secondary_activity_detail.textFormat() == Qt.TextFormat.PlainText
    assert panel.secondary_activity_button().text() == "Open && inspect"
    assert panel.secondary_activity_button().accessibleName() == "Open & inspect"
    assert panel.secondary_activity_button().toolTip() == current.secondary_activity_detail


@pytest.mark.parametrize("size", [(720, 560), (1040, 720)])
@pytest.mark.parametrize("conversation_open", [False, True])
@pytest.mark.parametrize("font_stretch", [100, 125])
@pytest.mark.parametrize(("canvas", "video"), [
    ("ready", "hidden"), ("missing_app", "hidden"),
    ("ready", "needs_file"), ("missing_app", "local_attention"),
    ("share_pending", "hidden"), ("withdraw_pending", "hidden"),
])
def test_both_production_activities_fit_and_keep_keyboard_actions_reachable(
    window, qapp, size, conversation_open, font_stretch, canvas, video
):
    hosting = canvas in {"share_pending", "withdraw_pending"}
    current = room_overview(canvas=canvas, video=video, hosting=hosting)
    window.set_art_room_overview(current)
    window.session_strip.set_audio_state("End Room" if hosting else "Leave Room")
    panel = window.art_room_overview
    assert current.role_label == ("Host" if hosting else "Guest")
    # Deterministically exercise wider glyph metrics without changing a
    # machine's font/DPI settings or replacing production stylesheet sizes.
    for widget in panel._content.findChildren(QLabel) + panel._content.findChildren(QPushButton):
        font = widget.font()
        font.setStretch(font_stretch)
        widget.setFont(font)
    window.webex_embed.set_meeting_configured(True)
    window.webex_embed.set_service_label("Webex")
    window.webex_embed.setVisible(conversation_open)
    window.resize(*size)
    _settle(qapp)
    assert window.size() == QSize(*size)
    assert panel.horizontalScrollBar().maximum() == 0
    if not conversation_open:
        assert panel.verticalScrollBar().maximum() == 0
    assert panel._secondary_activity.font().stretch() == font_stretch
    visible = [
        widget for widget in panel._content.findChildren(QLabel)
        + panel._content.findChildren(QPushButton)
        if widget.isVisibleTo(window)
    ]
    rects = []
    for widget in visible:
        rect = QRect(widget.mapTo(panel._content, QPoint()), widget.size())
        assert panel._content.rect().contains(rect), (widget.text(), rect)
        if isinstance(widget, QLabel):
            assert widget.height() >= widget.heightForWidth(widget.width()), (
                widget.text(), widget.size(), widget.heightForWidth(widget.width())
            )
        else:
            assert widget.width() >= widget.minimumSizeHint().width(), widget.text()
            assert widget.height() >= widget.minimumSizeHint().height(), widget.text()
        assert all(not previous.intersects(rect) for previous in rects), (widget.text(), rect)
        rects.append(rect)
    if conversation_open:
        room_rect = QRect(panel.mapTo(window, QPoint()), panel.size())
        meeting_rect = QRect(window.webex_embed.mapTo(window, QPoint()), window.webex_embed.size())
        assert room_rect.bottom() < meeting_rect.top()
        assert window.rect().contains(meeting_rect)
    for button in (
        panel.activity_button(), panel.secondary_activity_button(),
        panel.conversation_button(),
    ):
        assert button.isVisibleTo(window) and button.isEnabled()
        button.setFocus(Qt.FocusReason.TabFocusReason)
        _settle(qapp)
        assert button.hasFocus()
        rect = QRect(button.mapTo(panel.viewport(), QPoint()), button.size())
        assert panel.viewport().rect().contains(rect), (button.text(), rect)
