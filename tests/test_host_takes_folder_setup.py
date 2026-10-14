"""W14: idle host Recording Setup matches the managed Takes folder loader."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from shiboken6 import isValid

from core.settings import AppSettings, hosted_server_recordings_dir, load_settings
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.recording_setup import RecordingSetupDialog

HOST_TAKES_FOLDER_HELP = (
    "WebJam keeps host takes in this folder. Show Folder opens that location."
)

APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _dispose_top_level_widgets() -> None:
    """Retire this test's dialogs after their queued layout work settles."""

    existing = set(QApplication.topLevelWidgets())
    yield
    owned = [
        widget for widget in QApplication.topLevelWidgets() if widget not in existing
    ]
    for widget in owned:
        widget.close()
    APP.processEvents()
    for widget in owned:
        if isValid(widget):
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    APP.processEvents()


def _folder_buttons(dialog: RecordingSetupDialog) -> list[QPushButton]:
    return [
        item
        for item in dialog.findChildren(QPushButton)
        if item.text() == "Choose Folder"
    ]


def test_idle_host_recording_setup_does_not_offer_a_discarded_folder_choice(
    tmp_path,
) -> None:
    custom = tmp_path / "custom-takes"
    custom.mkdir()
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        takes_directory=str(custom),
    )
    managed = str(hosted_server_recordings_dir())

    with patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[],
    ):
        dialog = RecordingSetupDialog(settings, takes_folder_editable=True)

    assert _folder_buttons(dialog) == []
    assert dialog._folder.text() == f"Takes: {managed}"
    assert custom.as_posix() not in dialog._folder.text()
    assert str(custom) not in dialog._folder.text()
    help_labels = [
        item.text()
        for item in dialog.findChildren(QLabel)
        if item.text() == HOST_TAKES_FOLDER_HELP
    ]
    assert help_labels == [HOST_TAKES_FOLDER_HELP]
    assert dialog._show_folder_button.isEnabled()
    assert settings.takes_directory == str(custom)


def test_idle_host_show_folder_opens_the_managed_host_location(tmp_path) -> None:
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        takes_directory=str(tmp_path / "discarded-takes"),
    )
    managed = str(hosted_server_recordings_dir())
    opened: list[str] = []

    with (
        patch(
            "webjam_qt.windows.recording_setup.list_input_devices",
            return_value=[],
        ),
        patch(
            "webjam_qt.windows.recording_setup.QDesktopServices.openUrl",
            side_effect=lambda url: opened.append(url.toLocalFile()) or True,
        ),
        patch(
            "webjam_qt.windows.recording_setup.QFileDialog.getExistingDirectory",
            return_value=str(tmp_path / "another-choice"),
        ) as picker,
    ):
        dialog = RecordingSetupDialog(settings, takes_folder_editable=True)
        dialog._choose_folder()
        dialog._show_folder()

    picker.assert_not_called()
    assert opened == [managed]
    assert dialog._folder.text() == f"Takes: {managed}"


def test_idle_host_save_persists_the_managed_takes_folder_not_a_custom_path(
    tmp_path,
) -> None:
    custom = tmp_path / "custom-takes"
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=True,
        takes_directory=str(custom),
    )
    managed = str(hosted_server_recordings_dir())

    with patch(
        "webjam_qt.windows.recording_setup.list_input_devices",
        return_value=[],
    ):
        dialog = RecordingSetupDialog(settings, takes_folder_editable=True)
        dialog._save()

    saved = json.loads(Path(settings.config_file).read_text())
    assert saved["takes_directory"] == managed
    assert saved["takes_directory"] != str(custom)
    loaded = load_settings(settings.config_file)
    assert loaded.takes_directory == managed
    assert settings.takes_directory == str(custom)


def test_idle_host_controller_does_not_mark_takes_folder_editable() -> None:
    controller = ApplicationController.__new__(ApplicationController)
    controller.bridge = SimpleNamespace(
        jamulus_state="Not launched",
        hosted_server_alive=lambda: False,
    )
    controller.settings = AppSettings(host_server_enabled=True)
    controller.host_peer = SimpleNamespace(active=False)
    controller.window = SimpleNamespace()
    controller._is_jamulus_running = lambda: False

    with patch(
        "webjam_qt.windows.recording_setup.RecordingSetupDialog"
    ) as dialog_type:
        dialog_type.return_value.exec.return_value = dialog_type.DialogCode.Rejected
        controller._open_recording_setup()

    assert dialog_type.call_args.kwargs["takes_folder_editable"] is False


def test_guest_recording_setup_still_offers_a_takes_folder_choice(tmp_path) -> None:
    chosen = tmp_path / "guest-takes"
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        host_server_enabled=False,
        takes_directory=str(tmp_path / "old-takes"),
    )

    with (
        patch(
            "webjam_qt.windows.recording_setup.list_input_devices",
            return_value=[],
        ),
        patch(
            "webjam_qt.windows.recording_setup.QFileDialog.getExistingDirectory",
            return_value=str(chosen),
        ),
    ):
        dialog = RecordingSetupDialog(settings, takes_folder_editable=True)
        buttons = _folder_buttons(dialog)
        assert len(buttons) == 1
        assert buttons[0].isEnabled()
        dialog._choose_folder()
        dialog._save()

    help_labels = [
        item.text()
        for item in dialog.findChildren(QLabel)
        if item.text() == HOST_TAKES_FOLDER_HELP
    ]
    assert help_labels == []
    saved = json.loads(Path(settings.config_file).read_text())
    assert saved["takes_directory"] == str(chosen)
    assert str(chosen) in dialog._folder.text()
