"""Navigation contracts for the unified Reference Studio destination."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from core.take_player import TakePlayer
from webjam_qt.widgets.recording_studio import RecordingStudio
from webjam_qt.widgets.reference_studio_shell import ReferenceStudioShell


class _Sink:
    def play(self, *_args, **_kwargs) -> None:
        return None

    def stop(self) -> None:
        return None


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def _shell() -> ReferenceStudioShell:
    return ReferenceStudioShell(
        RecordingStudio(player=TakePlayer(samplerate=48_000, sink=_Sink()))
    )


def test_shell_starts_at_project_home_and_preserves_take_review_instance() -> None:
    shell = _shell()
    assert shell.current_view() == "home"
    assert shell.take_review is shell.stack.widget(2)
    assert shell.accessibleName() == "Reference Studio"


def test_home_semantic_requests_are_forwarded_without_fake_navigation() -> None:
    shell = _shell()
    events: list[str] = []
    shell.new_project_requested.connect(lambda: events.append("new"))
    shell.open_project_requested.connect(lambda: events.append("open"))
    shell.play_along_requested.connect(lambda: events.append("play"))
    shell.recent_project_requested.connect(lambda path: events.append(path))
    shell.home.new_button.click()
    shell.home.open_button.click()
    shell.home.play_along_button.click()
    shell.home.recent_project_requested.emit("/tmp/Song.webjam")
    assert events == ["new", "open", "play", "/tmp/Song.webjam"]
    assert shell.current_view() == "home"


def test_controller_owned_view_switching_and_take_review_action() -> None:
    shell = _shell()
    events: list[str] = []
    shell.take_review_requested.connect(lambda: events.append("takes"))
    shell.show_project()
    assert shell.current_view() == "project"
    shell.show_home()
    assert shell.current_view() == "home"
    shell.review_takes_button.click()
    assert shell.current_view() == "takes"
    assert events == ["takes"]
    shell.show_home()
    assert shell.current_view() == "home"


def test_podcast_profile_is_applied_across_shell_home_and_workspace() -> None:
    shell = _shell()
    shell.set_creator_profile("podcast_voice")

    assert shell.creator_profile_key == "podcast_voice"
    assert shell.home.creator_profile_key == "podcast_voice"
    assert shell.workspace.creator_profile_key == "podcast_voice"
    assert shell.home.title.text() == "Podcast & Voice Studio"
    assert shell.home.play_along_button.text() == "New Recording"
    assert shell.home.new_button.text() == "New Episode Project"
    assert shell.home.recent_label.text() == "Recent Episodes"
    visible_home_copy = " ".join(
        (
            shell.home.subtitle.text(),
            shell.home.play_along_button.text(),
            shell.home.play_along_button.accessibleDescription(),
            shell.home.empty_recent.text(),
        )
    ).casefold()
    for music_only_term in ("play along", "backing", "song section", "songwriting"):
        assert music_only_term not in visible_home_copy


def test_review_preview_truthfully_disables_local_studio_entry_points() -> None:
    shell = _shell()
    shell.set_creator_profile("review_rehearsal")

    assert "preview" in shell.home.title.text().casefold()
    assert "not available" in shell.home.subtitle.text().casefold()
    assert not shell.home.play_along_button.isEnabled()
    assert not shell.home.new_button.isEnabled()
    assert not shell.home.open_button.isEnabled()
    assert not shell.home.recent_list.isEnabled()
    assert not shell.workspace.import_backing_button.isEnabled()
    assert not shell.workspace.add_track_button.isEnabled()
