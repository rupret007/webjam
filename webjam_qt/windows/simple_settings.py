"""The few musician preferences that matter during a jam.

Connection setup belongs at launch.  This dialog deliberately keeps the
in-session choices to identity, the input WebJam listens to, and the output
used when reviewing a take.
"""

from __future__ import annotations

from copy import deepcopy
import logging
from pathlib import Path
import sys
from typing import Optional

from PySide6.QtCore import Qt, QProcess, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.audio_routing import list_input_devices, list_output_devices
from core.settings import AppSettings, save_settings
from core.webex_url import normalize_webex_url, webex_url_error
from webjam_qt.theme.tokens import Space
from webjam_qt.windows.launch_dialog import default_musician_name


LOGGER = logging.getLogger("webjam.qt.simple_settings")


class SimpleSettingsDialog(QDialog):
    """A compact setup page for choices the musician can actually make."""

    band_check_requested = Signal()

    def __init__(
        self,
        settings: AppSettings,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        # Keep edits isolated until the settings file is saved successfully.
        self._settings = deepcopy(settings)
        self.setObjectName("SimpleSettingsDialog")
        self.setWindowTitle("WebJam Settings")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.resize(620, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        title = QLabel("Your setup")
        title.setObjectName("SimpleSettingsTitle")
        subtitle = QLabel(
            "Choose the input you play through. WebJam keeps the connection "
            "and music engine automatic."
        )
        subtitle.setObjectName("SimpleSettingsSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        role = QLabel(
            "Hosting this jam" if settings.host_server_enabled else "Joined this jam"
        )
        role.setObjectName("SimpleSettingsRole")
        root.addWidget(role)

        identity = self._section("You")
        name_label = self._field_label("Your name")
        self._name = QLineEdit(default_musician_name(settings))
        self._name.setPlaceholderText("Your name")
        self._name.setAccessibleName("Your musician name")
        identity.layout().addWidget(name_label)
        identity.layout().addWidget(self._name)
        root.addWidget(identity)

        audio = self._section("Audio")
        input_label = self._field_label("Input for Band Check")
        input_row = QHBoxLayout()
        self._input = QComboBox()
        self._input.setAccessibleName("Input for Band Check")
        input_row.addWidget(self._input, 1)
        refresh = QPushButton("Refresh")
        refresh.setObjectName("QuietButton")
        refresh.setAccessibleName("Refresh audio device list")
        refresh.clicked.connect(self._populate_audio_devices)
        input_row.addWidget(refresh)
        audio.layout().addWidget(input_label)
        audio.layout().addLayout(input_row)

        review_label = self._field_label("Review playback")
        self._output = QComboBox()
        self._output.setAccessibleName("Review playback output")
        audio.layout().addWidget(review_label)
        audio.layout().addWidget(self._output)

        audio_note = QLabel(
            "Live Jamulus output follows your Mac's audio device. Review playback "
            "uses the choice above."
        )
        audio_note.setObjectName("SimpleSettingsHint")
        audio_note.setWordWrap(True)
        audio.layout().addWidget(audio_note)
        self._system_audio = QPushButton("Open Audio MIDI Setup")
        self._system_audio.setObjectName("QuietButton")
        self._system_audio.setAccessibleName("Open macOS Audio MIDI Setup")
        self._system_audio.setVisible(sys.platform == "darwin")
        self._system_audio.clicked.connect(self._open_system_audio)
        audio.layout().addWidget(self._system_audio, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addWidget(audio)

        conversation = self._section("Conversation")
        self._conversation_toggle = QToolButton()
        self._conversation_toggle.setObjectName("SimpleSettingsDisclosure")
        self._conversation_toggle.setText("Conversation link (optional)")
        self._conversation_toggle.setCheckable(True)
        self._conversation_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._conversation_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._conversation_toggle.setAccessibleName("Optional conversation link")
        conversation.layout().addWidget(self._conversation_toggle)
        self._conversation_body = QWidget()
        conversation_body = QVBoxLayout(self._conversation_body)
        conversation_body.setContentsMargins(0, Space.SM, 0, 0)
        conversation_body.setSpacing(Space.XS)
        video_note = QLabel("Paste a Webex meeting link only if your band uses one.")
        video_note.setObjectName("SimpleSettingsHint")
        video_note.setWordWrap(True)
        self._video = QLineEdit(settings.webex_url)
        self._video.setPlaceholderText("https://…")
        self._video.setAccessibleName("Optional Webex meeting link")
        conversation_body.addWidget(video_note)
        conversation_body.addWidget(self._video)
        conversation.layout().addWidget(self._conversation_body)
        root.addWidget(conversation)
        self._conversation_toggle.toggled.connect(self._set_conversation_visible)
        self._conversation_toggle.setChecked(bool(settings.webex_url.strip()))
        self._set_conversation_visible(self._conversation_toggle.isChecked())

        self._error = QLabel("")
        self._error.setObjectName("SimpleSettingsError")
        self._error.setWordWrap(True)
        self._error.setTextFormat(Qt.TextFormat.PlainText)
        self._error.setVisible(False)
        root.addWidget(self._error)
        root.addStretch(1)

        self._populate_audio_devices()

        footer = QHBoxLayout()
        band_check = QPushButton("Run Band Check")
        band_check.setObjectName("GhostButton")
        band_check.setAccessibleName("Close settings and run Band Check")
        band_check.clicked.connect(self.band_check_requested.emit)
        footer.addWidget(band_check)
        footer.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("GhostButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setObjectName("PrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

        self._name.textChanged.connect(self._clear_error)
        self._video.textChanged.connect(self._clear_error)
        self._input.currentIndexChanged.connect(self._clear_error)
        self._output.currentIndexChanged.connect(self._clear_error)

    @staticmethod
    def should_show_on_startup(settings: AppSettings) -> bool:
        """Compatibility helper; launch is now owned by LaunchDialog."""
        return not Path(settings.config_file).expanduser().exists()

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SimpleSettingsFieldLabel")
        return label

    @staticmethod
    def _section(title_text: str) -> QFrame:
        section = QFrame()
        section.setObjectName("SimpleSettingsSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        layout.setSpacing(Space.XS)
        title = QLabel(title_text)
        title.setObjectName("SimpleSettingsSectionTitle")
        layout.addWidget(title)
        return section

    def _populate_audio_devices(self) -> None:
        """Refresh both simple, stable device selectors without changing drafts."""

        saved_input = self._input.currentData()
        if saved_input is None:
            saved_input = int(self._settings.audio_input_device_index)
        self._input.blockSignals(True)
        self._input.clear()
        self._input.addItem("System default", -1)
        for device in list_input_devices():
            name = str(device.get("name") or "").strip()
            index = int(device.get("index", -1))
            channels = int(device.get("channels", 0) or 0)
            if name and index >= 0 and self._input.findData(index) < 0:
                suffix = f" · {channels} input" + ("s" if channels != 1 else "")
                self._input.addItem(name + suffix, index)
        input_index = self._input.findData(saved_input)
        if input_index < 0 and saved_input != -1:
            self._input.addItem(f"Saved input {saved_input} (unavailable)", saved_input)
            input_index = self._input.count() - 1
        self._input.setCurrentIndex(max(0, input_index))
        self._input.blockSignals(False)

        saved_output = self._output.currentData()
        if saved_output is None:
            saved_output = str(self._settings.take_playback_output_device or "")
        self._output.blockSignals(True)
        self._output.clear()
        self._output.addItem("System default", "")
        for device in list_output_devices():
            name = str(device.get("name") or "").strip()
            if name and self._output.findData(name) < 0:
                self._output.addItem(name, name)
        output_index = self._output.findData(saved_output)
        if output_index < 0 and saved_output:
            self._output.addItem(f"{saved_output} (unavailable)", saved_output)
            output_index = self._output.count() - 1
        self._output.setCurrentIndex(max(0, output_index))
        self._output.blockSignals(False)
        self._clear_error()

    def _set_conversation_visible(self, visible: bool) -> None:
        self._conversation_body.setVisible(visible)
        self._conversation_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )

    def _open_system_audio(self) -> None:
        if sys.platform == "darwin":
            QProcess.startDetached("open", ["-a", "Audio MIDI Setup"])

    def _clear_error(self, *_args) -> None:
        self._error.clear()
        self._error.setVisible(False)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)

    def _save(self) -> None:
        name = self._name.text().strip() or default_musician_name(self._settings)
        video = normalize_webex_url(self._video.text())
        if video:
            error = webex_url_error(video)
            if error:
                self._show_error(error)
                return

        input_device = self._input.currentData()
        if input_device is None:
            self._show_error("Choose an input, then save your setup.")
            return
        self._settings.musician_name = name
        self._settings.webex_url = video
        self._settings.webex_audio_mode = "talkback"
        self._settings.audio_input_device_index = int(input_device)
        self._settings.take_playback_output_device = str(self._output.currentData() or "")
        try:
            save_settings(self._settings)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Could not save musician settings: %s", exc)
            self._show_error(
                "WebJam couldn't save these settings. Check folder access and try again."
            )
            return
        self.accept()
