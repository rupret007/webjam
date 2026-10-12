"""W05: an available copy action must not read like work in progress."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from core.art_companion import ArtCompanionProjection, VideoCompanionState
from core.art_room_overview import art_room_overview
from core.art_room_presence import art_room_presence
from core.reference_video import ReferenceVideoFollowSnapshot, ReferenceVideoFollowState
from core.session_conductor import ArtRoomState
from webjam_qt.widgets.art_room_chip import ArtRoomChip
from webjam_qt.widgets.art_room_overview import ArtRoomOverviewWidget
from webjam_qt.windows.reference_video import ReferenceVideoDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("state", [
    "needs_file", "mismatched_file", "file_unavailable", "local_attention",
])
def test_copy_action_agrees_across_room_chip_overview_and_panel(qapp, state):
    presence = art_room_presence(ArtCompanionProjection(
        in_room=True, video=VideoCompanionState(state),
    ))
    chip = ArtRoomChip()
    overview = ArtRoomOverviewWidget()
    panel = ReferenceVideoDialog(hosting=False)
    try:
        chip.set_presence(presence)
        overview.set_overview(art_room_overview(
            state=ArtRoomState.CONNECTED, hosting=False, presence=presence,
        ))
        panel.set_follow_snapshot(ReferenceVideoFollowSnapshot(
            state=ReferenceVideoFollowState(state),
            source_kind="local", source_display_name="lesson.mp4",
        ))
        for button in (overview.activity_button(), panel._open_button):
            assert button.text() == "Open my copy"
            assert button.accessibleName() == "Open my copy"
            assert button.isEnabled()
            assert not button.isHidden()
        if state == "needs_file":
            assert overview._activity.text() == "Open my copy"
            assert chip.chip.text() == "Open my copy"
            assert chip.chip.accessibleName() == "Open my copy"
        for text in (
            chip.chip.text(), chip.chip.accessibleDescription(),
            overview._activity.text(), overview._activity_detail.text(),
            panel._status.text(),
        ):
            assert "…" not in text
            assert "..." not in text
        assert "Open my copy" in panel._status.text()
    finally:
        for widget in (chip, overview, panel):
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def test_copy_action_is_ready_before_first_host_snapshot(qapp):
    panel = ReferenceVideoDialog(hosting=False)
    try:
        assert panel._open_button.text() == "Open my copy"
        assert panel._open_button.accessibleName() == "Open my copy"
    finally:
        panel.close()
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()
