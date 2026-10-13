"""Paint along is the next room action, without stacked own-tools copy."""

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest

from core.art_companion import ArtCompanionProjection, VideoCompanionState
from core.art_room_overview import art_room_overview
from core.art_room_presence import art_room_presence
from core.session_conductor import ArtRoomState
from tests.test_art_room_overview_ui import qapp, window, _settle


def paint_overview(hosting, video=VideoCompanionState.NONE):
    presence = art_room_presence(
        ArtCompanionProjection(in_room=True, transport_allowed=hosting, video=video),
        hosting=hosting, intended_video=hosting, paint_along_room=True,
    )
    return art_room_overview(
        state=ArtRoomState.WAITING if hosting else ArtRoomState.CONNECTED,
        hosting=hosting, presence=presence,
    )


@pytest.mark.parametrize("hosting", [False, True])
@pytest.mark.parametrize("video", list(VideoCompanionState))
def test_paint_along_offer_removes_stacked_copy(hosting, video):
    current = paint_overview(hosting, video)
    assert current.activity_actions == ("video",)
    assert current.making_detail == ""
    assert current.activity_detail
    assert current.connection_detail


@pytest.mark.parametrize("hosting", [False, True])
@pytest.mark.parametrize("size", [(800, 600), (1280, 800)])
def test_paint_along_action_fits_without_scroll_and_copy_returns(window, qapp, hosting, size):
    panel = window.art_room_overview
    current = paint_overview(hosting)
    window.session_strip.set_audio_state("End Room" if hosting else "Leave Room")
    window.session_hud.set_state(
        "Your room is open" if hosting else "You’re in",
        "Copy the invite. Keep making while you wait for another artist." if hosting else "",
        action_text="Copy Invite", action_visible=hosting,
    )
    window.resize(*size)
    actions = []
    panel.activity_requested.connect(actions.append)
    # Repeat after returning to Make together to catch stale visibility/copy.
    for _ in range(2):
        window.set_art_room_overview(current)
        _settle(qapp)
        assert window.size() == QSize(*size)
        assert panel._making_detail.isHidden()
        assert panel._making_detail.text() == ""
        assert panel._making_detail.accessibleDescription() == ""
        assert not panel._title.isVisibleTo(window)
        assert "Start making with paper" not in panel.accessibleDescription()
        assert panel._connection_detail.text() == current.connection_detail
        assert current.connection_detail.rstrip(".") in panel.accessibleDescription()
        assert panel._activity_detail.text() == current.activity_detail
        button = panel.activity_button()
        assert button.text() == "Open Paint along"
        assert button.isVisibleTo(window) and button.isEnabled()
        assert panel.verticalScrollBar().value() == 0
        assert panel.viewport().rect().contains(
            QRect(button.mapTo(panel.viewport(), QPoint()), button.size())
        )
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        assert "Preview" in window.windowTitle()
        own_tools = art_room_overview(state=ArtRoomState.CONNECTED, hosting=hosting)
        window.set_art_room_overview(own_tools)
        _settle(qapp)
        assert panel._activity_detail.isVisibleTo(window)
        assert "paper, clay, a model, printer, or your usual app" in panel._activity_detail.text()
        assert not button.isVisibleTo(window)
    assert actions == ["video", "video"]
