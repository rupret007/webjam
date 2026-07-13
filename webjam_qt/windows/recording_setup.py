"""Focused recording preferences for the integrated Studio."""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.audio_routing import list_input_devices, list_output_devices
from core.settings import AppSettings, save_settings
from webjam_qt.theme.tokens import Space


LOGGER = logging.getLogger("webjam.qt.recording_setup")


class RecordingSetupDialog(QDialog):
    """Configure review output and optional host-side isolated inputs."""

    def __init__(
        self,
        settings: AppSettings,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setObjectName("RecordingSetupDialog")
        self.setWindowTitle("WebJam Recording Setup")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.resize(620, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        title = QLabel("Recording setup")
        title.setObjectName("SimpleSettingsTitle")
        subtitle = QLabel(
            "The host always records one synchronized Jamulus track per musician. "
            "Choose where Studio plays and, if useful, add the host interface's "
            "first two inputs as separate local stems."
        )
        subtitle.setObjectName("SimpleSettingsSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        output_label = QLabel("Studio playback output")
        output_label.setObjectName("SimpleSettingsFieldLabel")
        self._output = QComboBox()
        self._output.setAccessibleName("Studio playback output")
        self._output.addItem("System Default", "")
        for device in list_output_devices():
            name = str(device.get("name") or "").strip()
            if name and self._output.findData(name) < 0:
                self._output.addItem(name, name)
        saved_output = str(settings.take_playback_output_device or "")
        output_index = self._output.findData(saved_output)
        if output_index < 0 and saved_output:
            self._output.addItem(f"{saved_output} (unavailable)", saved_output)
            output_index = self._output.count() - 1
        self._output.setCurrentIndex(max(0, output_index))
        root.addWidget(output_label)
        root.addWidget(self._output)

        self._capture = QCheckBox(
            "Also record interface inputs 1 and 2 as isolated local stems"
        )
        self._capture.setAccessibleName("Record two isolated local input stems")
        self._capture.setChecked(bool(settings.local_capture_enabled))
        if not settings.host_server_enabled:
            self._capture.setEnabled(False)
            self._capture.setChecked(False)
            self._capture.setToolTip(
                "The band host owns synchronized recording and local stem capture."
            )
        root.addWidget(self._capture)

        self._capture_help = QLabel(
            "Use this when one interface carries two distinct sources, such as "
            "guitar on input 1 and vocal on input 2. The device must support two "
            "input channels at 48 kHz and be shareable with Jamulus."
        )
        self._capture_help.setObjectName("SimpleSettingsSubtitle")
        self._capture_help.setWordWrap(True)
        root.addWidget(self._capture_help)

        self._input_label = QLabel("Two-channel recording input")
        self._input_label.setObjectName("SimpleSettingsFieldLabel")
        self._input = QComboBox()
        self._input.setAccessibleName("Two-channel isolated recording input")
        for device in list_input_devices():
            if int(device.get("channels", 0) or 0) < 2:
                continue
            name = str(device.get("name") or "").strip()
            index = int(device.get("index", -1))
            if name and index >= 0:
                self._input.addItem(f"{name} · {device['channels']} inputs", index)
        saved_input = int(settings.audio_input_device_index)
        input_index = self._input.findData(saved_input)
        if input_index >= 0:
            self._input.setCurrentIndex(input_index)
        root.addWidget(self._input_label)
        root.addWidget(self._input)

        folder_row = QHBoxLayout()
        folder = QLabel(
            "Takes: " + (str(settings.takes_directory or "Not configured"))
        )
        folder.setObjectName("SimpleSettingsSubtitle")
        folder.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        folder_row.addWidget(folder, 1)
        show_folder = QPushButton("Show Folder")
        show_folder.setObjectName("GhostButton")
        show_folder.setEnabled(bool(settings.takes_directory))
        show_folder.clicked.connect(self._show_folder)
        folder_row.addWidget(show_folder)
        root.addLayout(folder_row)

        self._error = QLabel("")
        self._error.setObjectName("SimpleSettingsError")
        self._error.setWordWrap(True)
        self._error.setTextFormat(Qt.TextFormat.PlainText)
        self._error.setVisible(False)
        root.addWidget(self._error)
        root.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("GhostButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save Recording Setup")
        save.setObjectName("PrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

        self._capture.toggled.connect(self._sync_capture_fields)
        self._sync_capture_fields()

    def _sync_capture_fields(self) -> None:
        visible = self._capture.isEnabled() and self._capture.isChecked()
        self._input_label.setVisible(visible)
        self._input.setVisible(visible)
        self._capture_help.setVisible(self._capture.isEnabled())
        self._error.clear()
        self._error.setVisible(False)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)

    def _show_folder(self) -> None:
        path = str(self._settings.takes_directory or "")
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _save(self) -> None:
        capture = self._capture.isEnabled() and self._capture.isChecked()
        input_index = self._input.currentData()
        if capture and input_index is None:
            self._show_error(
                "Connect a two-channel input interface, then reopen Recording Setup."
            )
            return
        self._settings.take_playback_output_device = str(
            self._output.currentData() or ""
        )
        self._settings.local_capture_enabled = capture
        if capture:
            self._settings.audio_input_device_index = int(input_index)
        try:
            save_settings(self._settings)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Could not save recording setup: %s", exc)
            self._show_error(
                "WebJam couldn't save recording setup. Check folder access and "
                "try again."
            )
            return
        self.accept()
