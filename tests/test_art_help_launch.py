"""Art's Current workspace Help is a short launch guide, with one file note."""
from __future__ import annotations

import re

import pytest
from PySide6.QtWidgets import QApplication

from core.creative_modes import get_creator_profile_by_key
from tests.support.start_ux import assert_no_banned_first_screen_words
from webjam_qt.windows.conductor_window import ConductorWindow


@pytest.fixture
def art_help():
    app = QApplication.instance() or QApplication([])
    window = ConductorWindow(mode_entries=[("music_jam", "Music Jam")],
                             initial_mode_key="music_jam", initial_title="Art Help")
    window.set_creator_profile(get_creator_profile_by_key("art"))
    window.show_help()
    app.processEvents()
    try:
        yield window._workflow_help_dialog
    finally:
        window._workflow_help_dialog.close()
        window.close()
        window.deleteLater()
        app.processEvents()


def test_art_help_launch_has_three_short_steps_and_one_file_copy_note(art_help):
    assert art_help._topics.currentItem().text() == "Current workspace"
    text = art_help._body.toPlainText()
    steps = re.findall(r"(?m)^(\d+)\.\s*(.+)$", text)
    assert [number for number, _ in steps] == ["1", "2", "3", "4"]
    launch = [body for _, body in steps[:3]]
    file_note = steps[3][1]
    # A bullet count alone must not permit the old paragraphs to be concatenated.
    assert all(len(body.split()) <= 45 for _, body in steps)
    assert_no_banned_first_screen_words(launch[0])
    assert all(label in launch[0] for label in ("Make together", "Paint along", "Host"))
    assert all(label in " ".join(launch) for label in ("Copy Invite", "Join", "invite"))
    assert "own tools" in launch[2]
    assert "Conversation" in launch[2] and "Webex" in launch[2]
    assert "Paint along" in file_note and "Open my copy" in file_note
    assert "matching" in file_note and "silent" in file_note
    assert "Local files are not transferred" in file_note


def test_art_help_keeps_detailed_lesson_setup_searchable(art_help):
    art_help._search.setText("shared lesson")
    titles = [art_help._topics.item(row).text() for row in range(art_help._topics.count())]
    art_help._topics.setCurrentRow(titles.index("Paint along and play along with sound"))
    text = art_help._body.toPlainText()
    assert all(label in text for label in (
        "Watch a shared lesson", "Choose YouTube", "Open in browser",
        "Copy Link", "Add Link", "Webex", "computer sound",
    ))
