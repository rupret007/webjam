"""Headless UI contracts for the project-first Studio home."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from webjam_qt.widgets.studio_project_home import (
    RecentStudioProject,
    StudioProjectHome,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QApplication.instance() or QApplication([])


def test_home_exposes_three_clear_project_actions() -> None:
    home = StudioProjectHome()
    assert home.play_along_button.text() == "Play Along / Record"
    assert home.new_button.text() == "New Project"
    assert home.open_button.text() == "Open Project…"
    assert home.play_along_button.isDefault()
    for control in (
        home.play_along_button,
        home.new_button,
        home.open_button,
        home.recent_list,
    ):
        assert control.accessibleName()


def test_home_actions_emit_semantic_intent_once() -> None:
    home = StudioProjectHome()
    events: list[str] = []
    home.play_along_requested.connect(lambda: events.append("play"))
    home.new_project_requested.connect(lambda: events.append("new"))
    home.open_project_requested.connect(lambda: events.append("open"))
    home.play_along_button.click()
    home.new_button.click()
    home.open_button.click()
    assert events == ["play", "new", "open"]


def test_recent_projects_render_without_disclosing_full_path() -> None:
    home = StudioProjectHome()
    record = RecentStudioProject(
        path="/Users/Musician/Private Songs/My Song.webjam",
        title="My Song",
        detail="Edited today",
    )
    home.set_recent_projects((record,))
    item = home.recent_list.item(0)
    assert item.text() == "My Song\nEdited today"
    assert "/Users/" not in item.text()
    assert item.data(Qt.ItemDataRole.UserRole) == record.path
    assert home.recent_list.isVisibleTo(home)
    assert not home.empty_recent.isVisibleTo(home)


def test_recent_activation_emits_the_controller_owned_path() -> None:
    home = StudioProjectHome()
    record = RecentStudioProject(path="/tmp/Song.webjam", title="Song")
    home.set_recent_projects((record,))
    opened: list[str] = []
    home.recent_project_requested.connect(opened.append)
    home.recent_list.itemActivated.emit(home.recent_list.item(0))
    assert opened == [record.path]


def test_empty_and_bounded_recent_states_are_truthful() -> None:
    home = StudioProjectHome()
    assert not home.recent_list.isVisibleTo(home)
    assert home.empty_recent.isVisibleTo(home)
    records = tuple(
        RecentStudioProject(path=f"/tmp/{index}.webjam", title=f"Song {index}")
        for index in range(21)
    )
    with pytest.raises(ValueError, match="at most 20"):
        home.set_recent_projects(records)


@pytest.mark.parametrize(
    "value",
    [
        {"path": "/tmp/Song.webjam", "title": "Song"},
        "not-a-sequence",
    ],
)
def test_recent_projects_require_typed_presentations(value) -> None:
    home = StudioProjectHome()
    with pytest.raises(TypeError):
        home.set_recent_projects(value)


def test_recent_presentation_rejects_control_text_and_oversized_paths() -> None:
    with pytest.raises(ValueError):
        RecentStudioProject(path="", title="Song")
    normalized = RecentStudioProject(path="/tmp/Song.webjam", title="Bad\nTitle")
    assert normalized.title == "Bad Title"
    with pytest.raises(ValueError):
        RecentStudioProject(path="/" + ("x" * 4_097), title="Song")


def test_home_fits_supported_compact_workspace_floor() -> None:
    home = StudioProjectHome()
    hint = home.minimumSizeHint()
    assert home.minimumWidth() <= 520
    assert home.minimumHeight() <= 430
    assert hint.width() <= 760
    assert hint.height() <= 600


def test_home_actions_stay_prominent_without_spanning_a_wide_workspace(qapp) -> None:
    home = StudioProjectHome()
    home.resize(1280, 800)
    home.show()
    qapp.processEvents()
    try:
        assert 700 <= home.actions_panel.width() <= 960
        assert home.actions_panel.geometry().center().x() == pytest.approx(
            home.rect().center().x(), abs=2
        )
    finally:
        home.close()
