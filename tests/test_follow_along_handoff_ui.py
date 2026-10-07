"""An online lesson retains a route to audible meeting sharing."""

import pytest

from core.reference_video import (
    ReferenceVideoFollowSnapshot, ReferenceVideoFollowState,
    ReferenceVideoSnapshot, ReferenceVideoState,
)
from tests.test_reference_video_ui import qapp as qapp
from webjam_qt.windows.reference_video import ReferenceVideoDialog


@pytest.mark.parametrize("hosting", [True, False])
@pytest.mark.parametrize("failed", [False, True])
def test_youtube_keeps_audible_meeting_handoff_available(qapp, hosting, failed):
    panel = ReferenceVideoDialog(hosting=hosting)
    panel.set_embedded(True)
    panel.resize(720, 560)
    if hosting:
        panel.set_host_snapshot(ReferenceVideoSnapshot(
            state=ReferenceVideoState.FAILED if failed else ReferenceVideoState.READY,
            shared=not failed, source_kind="youtube", video_id="M7lc1UVf-VE",
            source_display_name="YouTube lesson", duration_s=120 if not failed else 0,
        ))
    else:
        panel.set_follow_snapshot(ReferenceVideoFollowSnapshot(
            state=ReferenceVideoFollowState.LOCAL_ATTENTION if failed else ReferenceVideoFollowState.NEEDS_FILE,
            source_kind="youtube", video_id="M7lc1UVf-VE", source_display_name="YouTube lesson",
        ))
    events = []
    panel.watch_lesson_requested.connect(lambda: events.append("meeting"))
    panel.show()
    qapp.processEvents()
    try:
        assert panel._watch_lesson_button.isVisibleTo(panel)
        assert panel._watch_lesson_button.isEnabled()
        panel._watch_lesson_button.click()
        assert events == ["meeting"]
    finally:
        panel.close()
        panel.deleteLater()
        qapp.processEvents()
