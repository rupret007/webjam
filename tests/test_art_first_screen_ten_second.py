"""Art's idle first screen must read in ten seconds without requiring art skill.

Door-card Make together / Paint along phrasing belongs to W12 (#300).
This module covers the idle room after Art is already chosen.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core.meeting_companion import ART_NO_SKILL_NEEDED, ART_OWN_TOOLS_LINE
from core.settings import AppSettings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_art_idle_first_screen_names_start_session_and_skips_skill_lists(
    qapp, tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(ApplicationController, "_start_routing_scan", lambda self: None)
    old_home = os.environ.get("HOME")
    os.environ["HOME"] = str(tmp_path)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Art idle",
    )
    window.setStyleSheet(load_stylesheet())
    controller = ApplicationController(
        window,
        settings=AppSettings(
            config_file=str(tmp_path / "settings.json"),
            last_creator_profile_key="art",
        ),
    )
    try:
        controller._conductor_setup_requested = False
        controller._render_session_conductor()
        window.resize(800, 600)
        window.show()
        for _ in range(12):
            qapp.processEvents()

        overview = window.art_room_overview
        assert overview.isVisibleTo(window)
        assert overview._title.text() == "Open the room, then make with what you have"
        assert "Start Session" in overview._connection_detail.text()
        assert ART_NO_SKILL_NEEDED in overview._activity_detail.text()
        assert ART_OWN_TOOLS_LINE in overview._activity_detail.text()
        assert "clay" not in overview._activity_detail.text()
        assert "printer" not in overview._activity_detail.text()

        starts = [
            button
            for button in window.findChildren(QPushButton)
            if button.isVisibleTo(window) and button.text() == "Start Session"
        ]
        assert starts
        assert starts[0].isEnabled()

        visible = " ".join(
            widget.text()
            for widget in window.findChildren(QLabel) + window.findChildren(QPushButton)
            if widget.isVisibleTo(window)
        )
        assert "You do not need an art app" in visible
        assert "paint, sculpt" not in visible.casefold()
        assert "3d print" not in visible.casefold()
        assert "WebJam does not play the movie." not in visible
        hint = window.participant_grid._empty_hint.text()
        assert hint == f"{ART_NO_SKILL_NEEDED} {ART_OWN_TOOLS_LINE}"
        assert "Watch a shared lesson" not in hint
    finally:
        controller.shutdown()
        window.close()
        window.deleteLater()
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        qapp.processEvents()
