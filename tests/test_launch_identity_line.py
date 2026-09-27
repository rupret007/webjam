from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from core.settings import AppSettings, save_settings
from tests.support.start_ux import assert_no_banned_first_screen_words, harvest_first_screen
from webjam_qt.widgets.musician_identity_line import format_appearing_as_line
from webjam_qt.widgets.session_hud import SessionHud
from webjam_qt.windows.launch_dialog import LaunchDialog, default_musician_name
from webjam_qt.windows.simple_settings import SimpleSettingsDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication(sys.argv[:1])


def test_format_appearing_as_uses_mixer_wrap_for_long_names():
    visible, accessible = format_appearing_as_line("Jeff Story")
    assert visible == "You'll appear as 'Jeff Sto'\n'ry'"
    assert "Jeff Sto" in accessible
    assert "ry" in accessible


def test_join_page_shows_identity_line_for_saved_name(qapp, tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        musician_name="Sam",
    )
    save_settings(settings)
    dialog = LaunchDialog(settings)
    dialog.show_join()
    dialog.show()
    qapp.processEvents()
    try:
        label = dialog.findChild(QLabel, "LaunchIdentityLine")
        assert label is not None
        assert "'Sam'" in label.text()
        assert label.accessibleName()
        change_button = dialog._join_identity_line._change
        assert change_button.accessibleName() == "Change"
        assert change_button.accessibleDescription()
    finally:
        dialog.close()


def test_first_screen_harvest_unchanged_with_join_identity_line(qapp, tmp_path):
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    dialog.show()
    qapp.processEvents()
    try:
        spoken = harvest_first_screen(dialog)
        assert_no_banned_first_screen_words(spoken)
        assert "you'll appear as" not in spoken
    finally:
        dialog.close()


def test_join_change_opens_settings_on_your_name(qapp, tmp_path):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        musician_name="Sam",
    )
    save_settings(settings)
    dialog = LaunchDialog(settings)
    dialog.show_join()
    dialog.show()
    qapp.processEvents()
    opened: list[SimpleSettingsDialog] = []

    class RecordingDialog(SimpleSettingsDialog):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            opened.append(self)

        def exec(self):
            return SimpleSettingsDialog.DialogCode.Rejected

    with patch(
        "webjam_qt.windows.simple_settings.SimpleSettingsDialog",
        RecordingDialog,
    ):
        dialog._join_identity_line._change.click()
    qapp.processEvents()
    try:
        assert len(opened) == 1
        assert opened[0]._musician_name_focus_requested is True
    finally:
        dialog.close()


def test_host_handoff_hud_shows_identity_line(qapp):
    hud = SessionHud()
    hud.set_musician_identity("Sam")
    label = hud.findChild(QLabel, "SessionHudIdentityLine")
    assert label is not None
    assert "'Sam'" in label.text()
    assert hud._identity_line._change.accessibleName() == "Change"
    hud.clear_musician_identity()
    assert not hud._identity_line.isVisibleTo(hud)


def test_default_musician_name_used_on_join_line(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "webjam_qt.windows.launch_dialog.getpass",
        lambda: "Jeff",
    )
    settings = AppSettings(config_file=str(tmp_path / "settings.json"))
    dialog = LaunchDialog(settings)
    dialog.show_join()
    dialog.show()
    qapp.processEvents()
    try:
        label = dialog.findChild(QLabel, "LaunchIdentityLine")
        assert label is not None
        assert default_musician_name(settings) in label.text()
    finally:
        dialog.close()
