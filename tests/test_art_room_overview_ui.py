"""Art's real room body: lifecycle, navigation, keyboard and compact layout."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from shiboken6 import isValid

from core.art_room_overview import ArtRoomOverview
from core.creative_modes import get_creator_profile_by_key
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.participant_card import ParticipantPresentation
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


def _settle(app):
    for _ in range(3):
        app.processEvents()


def overview(**changes):
    return replace(
        ArtRoomOverview(
            phase="connected",
            phase_label="CONNECTED",
            title="Your Art room",
            role_label="Guest",
            connection_label="Connected to the host",
            connection_detail="The room connection is confirmed.",
            activity_label="Make together",
            activity_detail="Keep making with your own tools. Conversation is optional.",
            activity_action="",
            activity_action_label="",
            activity_enabled=False,
            conversation_enabled=True,
        ),
        **changes,
    )


@pytest.fixture
def window(qapp):
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music jam")],
        initial_mode_key="music_jam",
        initial_title="Making together",
    )
    window.setStyleSheet(load_stylesheet())
    window.set_creator_profile(get_creator_profile_by_key("art"))
    window.set_art_room_overview(overview())
    window.session_strip.set_audio_state("Leave Room")
    window.session_hud.set_state("You’re in", "Make together with your own tools.", action_visible=False)
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
        _settle(qapp)
        # processEvents alone does not deliver DeferredDelete in a local
        # test loop. Retire only this fixture's window tree before later
        # modules restyle QApplication.
        QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
        assert not isValid(window)


def test_art_body_replaces_empty_mixer_and_preserves_music_cards(window, qapp):
    card = ParticipantPresentation(channel_id=3, name="Alex", is_local=True)
    window.participant_grid.set_participants([card])
    _settle(qapp)
    assert window.art_room_overview.isVisibleTo(window)
    assert not window.participant_grid.isVisibleTo(window)
    assert not window.participant_grid._empty_state.isVisibleTo(window)
    spoken = window.art_room_overview.accessibleDescription()
    assert "Connected to the host" in spoken
    assert "0 artists" not in spoken and "mixer" not in spoken
    assert "Preview" in window.windowTitle()
    assert "Preview" in window.session_strip._subtitle.text()

    window.set_creator_profile(get_creator_profile_by_key("music"))
    _settle(qapp)
    assert window.participant_grid.isVisibleTo(window)
    assert not window.art_room_overview.isVisibleTo(window)
    assert window.participant_grid.cards()[0]._presentation is card
    assert window.participant_grid.cards()[0]._fader.isVisibleTo(window)
    window.set_creator_profile(get_creator_profile_by_key("art"))
    _settle(qapp)
    assert window.art_room_overview.isVisibleTo(window)
    assert not window.participant_grid.isVisibleTo(window)


def test_room_updates_do_not_leave_notes_or_replace_conversation(window, qapp):
    window.set_room_stage_visible(False)
    window.session_canvas.show()
    window.set_art_room_overview(overview(phase="reconnecting", phase_label="RECONNECTING"))
    _settle(qapp)
    assert window.session_canvas.isVisibleTo(window)
    assert not window.art_room_overview.isVisibleTo(window)
    window.session_canvas.hide()
    window.set_room_stage_visible(True)
    window.webex_embed.show()
    _settle(qapp)
    assert window.art_room_overview.isVisibleTo(window)
    assert window.webex_embed.isVisibleTo(window)
    assert not window.participant_grid.isVisibleTo(window)


@pytest.mark.parametrize(
    ("phase", "label", "connection"),
    [
        ("opening", "OPENING", "Opening the room"),
        ("waiting", "WAITING", "Your room is open"),
        ("connected", "CONNECTED", "Connected to the host"),
        ("reconnecting", "RECONNECTING", "Room connection interrupted"),
        ("failed", "CONNECTION ENDED", "Room connection ended"),
        ("ending", "CLOSING", "Leaving the room"),
        ("ended", "CLOSED", "You left the room"),
        ("cleanup_required", "FINISH LEAVING", "Room cleanup needs attention"),
    ],
)
def test_each_room_phase_replaces_visible_and_spoken_connection_truth(
    window, qapp, phase, label, connection
):
    current = overview(phase=phase, phase_label=label, connection_label=connection)
    window.set_art_room_overview(current)
    _settle(qapp)
    panel = window.art_room_overview
    assert panel.property("roomPhase") == phase
    assert panel._phase.text() == label
    assert panel._connection.text() == connection
    assert connection in panel.accessibleDescription()
    assert not panel.activity_button().isVisibleTo(window)
    assert [
        button.text() for button in panel.findChildren(QPushButton)
        if button.isVisibleTo(panel)
    ] == ["Conversation"]


def test_current_activity_and_conversation_are_keyboard_navigation_only(window, qapp):
    panel = window.art_room_overview
    activities, conversations = [], []
    panel.activity_requested.connect(activities.append)
    panel.conversation_requested.connect(lambda: conversations.append(True))
    window.set_art_room_overview(overview(
        activity_label="Paint along",
        activity_detail="Open your own copy to follow the host’s video.",
        activity_action="video", activity_action_label="Open your copy",
        activity_enabled=True,
    ))
    _settle(qapp)
    activity = panel.activity_button()
    conversation = panel.conversation_button()
    activity.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(activity, Qt.Key.Key_Space)
    QTest.keyClick(activity, Qt.Key.Key_Tab)
    _settle(qapp)
    assert conversation.hasFocus()
    QTest.keyClick(conversation, Qt.Key.Key_Space)
    assert activities == ["video"]
    assert conversations == [True]
    assert "does not open a meeting" in conversation.accessibleDescription()

    window.set_art_room_overview(overview(
        activity_label="Shared canvas", activity_action="canvas",
        activity_action_label="Open shared canvas", activity_enabled=True,
    ))
    activity.click()
    assert activities == ["video", "canvas"]
    window.set_art_room_overview(overview(
        phase="ending", phase_label="CLOSING", activity_action="canvas",
        activity_action_label="Open shared canvas", activity_enabled=False,
        conversation_enabled=False,
    ))
    activity.click()
    conversation.click()
    assert not activity.isEnabled() and not conversation.isEnabled()
    assert activities == ["video", "canvas"] and conversations == [True]
    window.set_art_room_overview(overview())
    assert not activity.isVisibleTo(window)
    assert conversation.isEnabled()


@pytest.mark.parametrize("size", [(720, 560), (1440, 900)])
@pytest.mark.parametrize("activity", ["own_tools", "video", "canvas"])
def test_themed_room_context_and_actions_fit_compact_and_normal_windows(
    window, qapp, size, activity
):
    changes = {}
    if activity == "video":
        changes = dict(
            activity_label="Paint along",
            activity_detail="The host shared a video. Open your own copy of the same file to follow along.",
            activity_action="video", activity_action_label="Open your Paint along copy",
            activity_enabled=True,
        )
    elif activity == "canvas":
        changes = dict(
            activity_label="Shared canvas",
            activity_detail="A shared canvas is available. Open the existing canvas panel when you want to join.",
            activity_action="canvas", activity_action_label="Open shared canvas",
            activity_enabled=True,
        )
    window.set_art_room_overview(overview(**changes))
    window.resize(*size)
    _settle(qapp)
    assert window.size() == QSize(*size)
    panel = window.art_room_overview
    assert panel.horizontalScrollBar().maximum() == 0
    assert panel.verticalScrollBar().maximum() == 0
    widgets = [
        panel._phase, panel._role, panel._title, panel._connection,
        panel._connection_detail, panel._activity, panel._activity_detail,
        panel.activity_button(), panel.conversation_button(),
    ]
    rects = []
    for widget in widgets:
        if not widget.isVisibleTo(window):
            continue
        rect = QRect(widget.mapTo(panel.viewport(), QPoint()), widget.size())
        assert panel.viewport().rect().contains(rect), (widget.text(), rect)
        assert window.rect().contains(QRect(widget.mapTo(window, QPoint()), widget.size()))
        if isinstance(widget, QLabel):
            height = widget.heightForWidth(widget.width())
            assert widget.height() >= height, (widget.text(), widget.size(), height)
        else:
            assert widget.width() >= widget.minimumSizeHint().width()
            assert widget.height() >= widget.minimumSizeHint().height()
        assert all(not previous.intersects(rect) for previous in rects)
        rects.append(rect)


def test_room_text_renders_as_text_and_does_not_create_rich_links(window):
    panel = window.art_room_overview
    window.set_art_room_overview(overview(
        activity_label="<b>Shared & canvas</b>",
        activity_detail="<a href='https://example.invalid'>Open</a>",
        activity_action="canvas", activity_action_label="Open & inspect",
        activity_enabled=True,
    ))
    assert panel._activity.textFormat() == Qt.TextFormat.PlainText
    assert panel._activity_detail.textFormat() == Qt.TextFormat.PlainText
    assert panel.activity_button().text() == "Open && inspect"
    assert panel.activity_button().accessibleName() == "Open & inspect"

@pytest.mark.parametrize("size", [(720, 560), (1440, 900)])
@pytest.mark.parametrize("conversation_open", [False, True])
@pytest.mark.parametrize(
    ("phase", "hosting"),
    [
        ("waiting", True), ("opening", False),
        ("connected", True), ("connected", False),
        ("reconnecting", True), ("reconnecting", False),
        ("cleanup_required", True), ("cleanup_required", False),
    ],
)
def test_production_room_copy_fits_and_coexists_with_conversation(
    window, qapp, size, conversation_open, phase, hosting
):
    from core.art_room_overview import art_room_overview
    from core.art_room_presence import ArtPresenceTarget, ArtRoomPresence
    from core.session_conductor import ArtRoomState

    room_state = {
        "waiting": ArtRoomState.WAITING,
        "opening": ArtRoomState.STARTING,
        "connected": ArtRoomState.CONNECTED,
        "reconnecting": ArtRoomState.RECONNECTING,
        "cleanup_required": ArtRoomState.CONNECTED,
    }[phase]
    current = art_room_overview(
        state=room_state, hosting=hosting,
        cleanup_required=phase == "cleanup_required",
        presence=ArtRoomPresence(
            label="Open your Paint along copy",
            description="Open your own copy of the same video to follow along.",
            target=ArtPresenceTarget.VIDEO,
        ),
    )
    window.set_art_room_overview(current)
    window.session_strip.set_audio_state(
        ("Try End Room" if hosting else "Try Leave Room")
        if phase == "cleanup_required" else ("End Room" if hosting else "Leave Room")
    )
    window.session_hud.set_state(
        "Finish closing the room" if phase == "cleanup_required" else "Your Art room",
        "The room has not finished disconnecting. Choose the closing action to finish."
        if phase == "cleanup_required" else "Your current room connection and activities are below.",
        action_visible=False,
    )
    window.webex_embed.set_meeting_configured(True)
    window.webex_embed.set_service_label("Webex")
    window.webex_embed.setVisible(conversation_open)
    window.resize(*size)
    _settle(qapp)
    assert window.size() == QSize(*size)
    panel = window.art_room_overview
    assert panel._overview.phase == phase
    assert panel.isVisibleTo(window)
    assert not window.participant_grid.isVisibleTo(window)
    assert panel.horizontalScrollBar().maximum() == 0
    if not conversation_open:
        assert panel.verticalScrollBar().maximum() == 0
    visible = [
        widget for widget in panel._content.findChildren(QLabel)
        if widget.isVisibleTo(window)
    ]
    rects = []
    for label in visible:
        rect = QRect(label.mapTo(panel._content, QPoint()), label.size())
        assert panel._content.rect().contains(rect), (phase, label.text(), rect)
        assert label.height() >= label.heightForWidth(label.width()), (phase, label.text())
        assert all(not previous.intersects(rect) for previous in rects), (phase, label.text())
        rects.append(rect)
    if conversation_open:
        room_rect = QRect(panel.mapTo(window, QPoint()), panel.size())
        meeting_rect = QRect(window.webex_embed.mapTo(window, QPoint()), window.webex_embed.size())
        assert room_rect.bottom() < meeting_rect.top()
        assert window.rect().contains(meeting_rect)
    button = panel.activity_button() if current.activity_enabled else panel.conversation_button()
    if button.isEnabled():
        button.setFocus(Qt.FocusReason.TabFocusReason)
        _settle(qapp)
        rect = QRect(button.mapTo(panel.viewport(), QPoint()), button.size())
        assert panel.viewport().rect().contains(rect), (phase, rect)
