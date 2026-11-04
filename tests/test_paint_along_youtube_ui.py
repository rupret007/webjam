"""The same Paint along panel offers file and URL sources with honest actions."""

import pytest
from PySide6.QtWidgets import QFileDialog, QInputDialog

from core.reference_video import ReferenceVideoSnapshot, ReferenceVideoState, ReferenceVideoFollowSnapshot, ReferenceVideoFollowState
from tests.test_reference_video_ui import qapp as qapp
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


def test_host_online_failure_names_the_explicit_shared_lesson_handoff(qapp):
    panel = ReferenceVideoDialog(hosting=True)
    panel.show()
    qapp.processEvents()
    panel.set_host_snapshot(ReferenceVideoSnapshot(
        state=ReferenceVideoState.FAILED, source_kind="youtube", error="That lesson is unavailable.",
    ))
    assert "That lesson is unavailable." in panel._status.text()
    assert panel._watch_lesson_button.text() in panel._status.text()
    assert not panel._watch_lesson_button.isHidden()
    assert panel._watch_lesson_button.isEnabled()
    handoffs = []
    panel.watch_lesson_requested.connect(lambda: handoffs.append(True))
    assert handoffs == []
    panel._watch_lesson_button.click()
    assert handoffs == [True]
    panel.close()


@pytest.mark.parametrize("index, url", [
    (1, "https://www.youtube.com/watch?v=vgbMONXc9Cs"),
    (3, "https://www.youtube.com/watch?v=mT0RNrTDHkI"),
])
def test_curated_episode_uses_normal_lesson_share_and_respects_room_lifetime(qapp, index, url):
    panel = ReferenceVideoDialog(hosting=True)
    panel.show()
    qapp.processEvents()
    links = []
    files = []
    panel.share_youtube_requested.connect(links.append)
    panel.share_requested.connect(files.append)
    assert panel._episode_picker.isVisible()
    assert panel._episode_picker.count() == 4
    panel._episode_picker.setCurrentIndex(index)
    panel._episode_button.click()
    assert links == [url]
    assert not files
    panel.set_host_snapshot(ReferenceVideoSnapshot(state=ReferenceVideoState.LOADING))
    panel._choose_episode()
    assert len(links) == 1
    panel.set_host_snapshot(ReferenceVideoSnapshot())
    panel.set_room_available(False)
    panel._choose_episode()
    assert len(links) == 1
    assert not panel._episode_button.isEnabled()
    panel.set_room_available(True)
    assert panel._episode_button.isVisible() and panel._episode_button.isEnabled()
    panel._episode_button.click()
    assert len(links) == 2
    panel.close()
