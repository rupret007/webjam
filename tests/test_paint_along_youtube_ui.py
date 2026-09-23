"""The same Paint along panel offers file and URL sources with honest actions."""

import pytest
from PySide6.QtWidgets import QFileDialog, QInputDialog

from core.reference_video import ReferenceVideoSnapshot, ReferenceVideoState, ReferenceVideoFollowSnapshot, ReferenceVideoFollowState
from tests.test_reference_video_ui import qapp
from webjam_qt.windows.reference_video import ReferenceVideoDialog


def test_host_source_choices_are_visible_and_url_choice_emits_no_file_request(qapp, monkeypatch):
    panel = ReferenceVideoDialog(hosting=True)
    panel.show()
    qapp.processEvents()
    files, links = [], []
    panel.share_requested.connect(files.append)
    panel.share_youtube_requested.connect(links.append)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("https://youtu.be/M7lc1UVf-VE", True))
    assert panel._share_button.isVisible() and panel._youtube_button.isVisible()
    assert "local" in panel._status.text().lower() and "YouTube" in panel._status.text()
    panel._youtube_button.click()
    assert links == ["https://youtu.be/M7lc1UVf-VE"] and not files
    panel.close()


@pytest.mark.parametrize("state", [ReferenceVideoFollowState.NEEDS_FILE, ReferenceVideoFollowState.LOCAL_ATTENTION])
def test_guest_opens_the_shared_online_lesson_without_asking_for_a_local_copy(qapp, monkeypatch, state):
    panel = ReferenceVideoDialog(hosting=False)
    panel.show()
    qapp.processEvents()
    opened = []
    panel.open_youtube_requested.connect(lambda: opened.append(True))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: pytest.fail("online lesson opened a file chooser"))
    panel.set_follow_snapshot(ReferenceVideoFollowSnapshot(
        state=state, source_kind="youtube", video_id="M7lc1UVf-VE", source_display_name="YouTube lesson",
        message="Choose Open lesson to follow silently.",
    ))
    assert panel._open_button.text() == "Open lesson"
    assert "Open lesson" in panel._status.text()
    panel._open_button.click()
    assert opened == [True]
    panel.set_room_available(False)
    panel._open_button.click()
    assert opened == [True] and panel._return_button.isVisible()
    panel.close()


def test_host_online_failure_names_the_online_retry_control(qapp):
    panel = ReferenceVideoDialog(hosting=True)
    panel.set_host_snapshot(ReferenceVideoSnapshot(
        state=ReferenceVideoState.FAILED, source_kind="youtube", error="That lesson is unavailable.",
    ))
    assert panel._youtube_button.text() in panel._status.text()
    assert not panel._youtube_button.isHidden()
    panel.close()
