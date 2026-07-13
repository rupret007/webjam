"""Secondary musician preferences.

Host/Join and connection details belong to the launch gate. Settings changes
only identity and the optional conversation link; it cannot turn into another
setup path.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.settings import AppSettings, save_settings
from core.webex_url import normalize_webex_url, webex_url_error
from webjam_qt.theme.tokens import Space
from webjam_qt.windows.launch_dialog import default_musician_name


LOGGER = logging.getLogger("webjam.qt.simple_settings")


class SimpleSettingsDialog(QDialog):
    """Preferences only; connection plumbing stays automated."""

    def __init__(
        self,
        settings: AppSettings,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setObjectName("SimpleSettingsDialog")
        self.setWindowTitle("WebJam Settings")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.resize(580, 430)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        title = QLabel("Settings")
        title.setObjectName("SimpleSettingsTitle")
        subtitle = QLabel(
            "WebJam handles the connection, audio engine, and recording "
            "automatically."
        )
        subtitle.setObjectName("SimpleSettingsSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        role = QLabel(
            "Hosting this jam"
            if settings.host_server_enabled
            else "Joined this jam"
        )
        role.setObjectName("SimpleSettingsRole")
        root.addWidget(role)

        name_label = QLabel("Your name")
        name_label.setObjectName("SimpleSettingsFieldLabel")
        self._name = QLineEdit(default_musician_name(settings))
        self._name.setPlaceholderText("Your name")
        self._name.setAccessibleName("Your musician name")
        root.addWidget(name_label)
        root.addWidget(self._name)

        video_label = QLabel("Video or conversation link (optional)")
        video_label.setObjectName("SimpleSettingsFieldLabel")
        self._video = QLineEdit(settings.webex_url)
        self._video.setPlaceholderText("Paste a Webex link, or leave blank")
        self._video.setAccessibleName("Optional Webex meeting link")
        root.addWidget(video_label)
        root.addWidget(self._video)

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
        save = QPushButton("Save")
        save.setObjectName("PrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

        self._name.textChanged.connect(self._clear_error)
        self._video.textChanged.connect(self._clear_error)

    @staticmethod
    def should_show_on_startup(settings: AppSettings) -> bool:
        """Compatibility helper; launch is now owned by LaunchDialog."""
        return not Path(settings.config_file).expanduser().exists()

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

        self._settings.musician_name = name
        self._settings.webex_url = video
        self._settings.webex_audio_mode = "talkback"
        try:
            save_settings(self._settings)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Could not save musician settings: %s", exc)
            self._show_error(
                "WebJam couldn't save these settings. Check folder access and "
                "try again."
            )
            return
        self.accept()
