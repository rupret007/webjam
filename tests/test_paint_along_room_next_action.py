"""An unconfigured Paint along host gets one next action in the overview."""

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from core.art_companion import ArtCompanionProjection, VideoCompanionState
from core.art_room_overview import art_room_overview
from core.art_room_presence import art_room_presence
from core.session_conductor import ArtRoomState
from tests.test_art_room_overview_ui import (
    _settle, qapp as _qapp, window as _window,
)

qapp, window = _qapp, _window


def host_overview(*, video=VideoCompanionState.NONE, configured=False,
                  state=ArtRoomState.CONNECTED, hosting=True, **changes):
    presence = art_room_presence(
        ArtCompanionProjection(in_room=True, video=video),
        hosting=hosting, intended_video=True, paint_along_room=True,
    )
    return art_room_overview(
        state=state, hosting=hosting, presence=presence,
        conversation_configured=configured, **changes,
    )


@pytest.mark.parametrize("state", [ArtRoomState.WAITING, ArtRoomState.CONNECTED])
@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
def test_empty_host_overview_has_one_action_and_conversation_in_more(window, qapp, state, size):
    window.set_art_room_overview(host_overview(state=state))
    window.session_strip.set_video_configured(False)
    window.resize(*size)
    _settle(qapp)
    panel = window.art_room_overview
    visible = [b for b in panel.findChildren(QPushButton) if b.isVisibleTo(window)]
    assert visible == [panel.activity_button()]
    button = visible[0]
    assert button.text() == "Open Paint along"
    assert button.objectName() == "PrimaryButton"
    assert button.isEnabled()
    assert panel.viewport().rect().contains(QRect(button.mapTo(panel.viewport(), QPoint()), button.size()))
    assert panel.verticalScrollBar().maximum() == 0
    activities = []
    panel.activity_requested.connect(activities.append)
    button.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert activities == ["video"]

    # The existing More route still reveals conversation controls on demand.
    strip = window.session_strip
    action = strip._video_action
    assert action in strip._tools_button.menu().actions()
    assert action.text() == "Set Up Conversation"
    assert action.isVisible() and action.isEnabled()
    tools = []
    strip.tool_requested.connect(tools.append)
    assert tools == []
    action.trigger()
    assert tools == ["conversation"]


@pytest.mark.parametrize("changes", [
    {"configured": True},
    {"video": VideoCompanionState.READY},
    {"video": VideoCompanionState.HOST_ATTENTION},
    {"hosting": False},
    {"state": ArtRoomState.RECONNECTING},
    {"state": ArtRoomState.FAILED},
    {"ended": True},
    {"stopping": True},
    {"cleanup_required": True},
    {"quitting": True},
])
def test_conversation_returns_when_setup_or_room_state_changes(window, qapp, changes):
    panel = window.art_room_overview
    for current, deferred in (
        (host_overview(), True),
        (host_overview(**changes), False),
        (host_overview(), True),
        (art_room_overview(state=ArtRoomState.CONNECTED, hosting=True), False),
    ):
        window.set_art_room_overview(current)
        _settle(qapp)
        assert panel.conversation_button().isVisibleTo(window) is (not deferred)
        assert panel.conversation_button().isEnabled() is current.conversation_enabled
        assert panel.conversation_button().text() == current.conversation_action_label
