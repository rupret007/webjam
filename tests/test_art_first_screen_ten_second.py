"""Art's first screens must read in ten seconds without requiring art skill."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core.creative_modes import get_creator_profile_by_key
from core.meeting_companion import ART_NO_SKILL_NEEDED, ART_OWN_TOOLS_LINE
from core.settings import AppSettings
from tests.support.start_ux import assert_no_banned_first_screen_words, harvest_first_screen
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.launch_dialog import LaunchDialog, StartCard


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _dialog(tmp_path: Path) -> LaunchDialog:
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    settings.last_creator_profile_key = "art"
    with patch.object(sys, "platform", "linux"):
        return LaunchDialog(settings)


def test_art_start_cards_tell_a_non_artist_what_to_choose(qapp, tmp_path: Path):
    art = get_creator_profile_by_key("art")
    make, paint = art.starts
    assert make.summary == (
        "You do not need an art app. Talk and make with what you have."
    )
    assert len(make.summary) <= 72
    assert paint.summary == "Watch one video together and make along."
    assert len(paint.summary) <= 72
    for start in (make, paint):
        spoken = f"{start.label} {start.summary} {start.detail}".casefold()
        assert "paint, sculpt" not in spoken
        assert "3d print" not in spoken
        assert "clay" not in spoken
        assert "printer" not in spoken
        assert "generat" not in spoken

    dialog = _dialog(tmp_path)
    try:
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(800, 600)
        dialog.show()
        qapp.processEvents()
        cards = [
            card for card in dialog._visible_start_cards() if not card.isHidden()
        ]
        assert [(card.accessibleName(), card.description()) for card in cards] == [
            (make.label, make.summary),
            (paint.label, paint.summary),
        ]
        spoken = harvest_first_screen(dialog)
        assert_no_banned_first_screen_words(spoken)
        assert "you do not need an art app" in spoken
        assert "watch one video together and make along" in spoken
        assert dialog._host_button.isVisibleTo(dialog)
        assert dialog._join_button.isVisibleTo(dialog)
        for card in cards:
            assert isinstance(card, StartCard)
            assert dialog.rect().contains(card.geometry())
    finally:
        dialog.close()
        dialog.deleteLater()


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
