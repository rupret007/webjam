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
