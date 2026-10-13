"""Music workflow help step 1 names Art alongside Music (W09)."""
from __future__ import annotations

import sys
from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication
from unittest import mock

from core.creative_modes import get_creator_profile_by_key
from core.settings import AppSettings
from webjam_qt.windows.conductor_window import ConductorWindow

@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


_STEP_ONE = (
    "choose <b>Music</b> or <b>Art</b> "
    "(<b>Make together</b> / <b>Paint along</b>), then <b>Host</b> "
    "or <b>Join</b>"
)


@pytest.fixture
def window(qapp):
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music Jam")],
        initial_mode_key="music_jam",
        initial_title="Help step one",
    )
    window.set_creator_profile(get_creator_profile_by_key("music"))
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_music_help_step_one_names_art_starts(qapp, window):
    with mock.patch(
        "webjam_qt.windows.help_dialog.HelpDialog.show",
        return_value=0,
    ), mock.patch("PySide6.QtWidgets.QTextBrowser.setHtml") as set_html:
        window.show_help()
    body = set_html.call_args.args[0]
    assert _STEP_ONE in body
    assert "choose <b>Music</b>, then <b>Host</b>" not in body


def test_music_door_still_passes_first_screen_gate_after_help_copy_change(
    qapp, tmp_path: Path,
):
    from tests.support.start_ux import assert_no_banned_first_screen_words, harvest_first_screen
    from webjam_qt.windows.launch_dialog import LaunchDialog

    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    settings.last_creator_profile_key = "music"
    with mock.patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    try:
        spoken = harvest_first_screen(dialog)
        assert_no_banned_first_screen_words(spoken)
        assert "make together" not in spoken
        assert "paint along" not in spoken
    finally:
        dialog.deleteLater()
