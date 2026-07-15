"""Small WebJam preferences, deliberately separate from live music setup.

Jamulus is the authority for an instrument interface, channel map, buffer,
jitter and musician mix.  This dialog therefore never scans, displays, saves,
or applies live-audio devices.  WebJam retains only identity and optional
conversation preferences here; recording and Studio own their separate setup
at the moment those features are used.
"""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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

from core.settings import AppSettings, save_settings
from core.webex_url import normalize_webex_url, webex_url_error
from webjam_qt.theme.tokens import Space
from webjam_qt.windows.launch_dialog import default_musician_name


LOGGER = logging.getLogger("webjam.qt.simple_settings")


class SimpleSettingsDialog(QDialog):
    """Edit non-audio WebJam preferences without duplicating Jamulus."""

    audio_settings_requested = Signal()

    def __init__(
        self,
        settings: AppSettings,
        parent: Optional[QWidget] = None,
        *,
        primary_action_label: str = "Save",
        show_band_check_action: bool = True,
    ) -> None:
        super().__init__(parent)
        # Keep edits isolated until the settings file is saved successfully.
        self._settings = deepcopy(settings)
        self._run_band_check_after_save = False
        self.setObjectName("SimpleSettingsDialog")
        self.setWindowTitle("WebJam Settings")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.resize(600, 470)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        title = QLabel("WebJam Settings")
        title.setObjectName("SimpleSettingsTitle")
        subtitle = QLabel(
            "Jamulus carries the music. WebJam keeps your jam organized."
        )
        subtitle.setObjectName("SimpleSettingsSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        identity = self._section("You")
        name_label = self._field_label("Your name")
        self._name = QLineEdit(default_musician_name(settings))
        self._name.setPlaceholderText("Your name")
        self._name.setAccessibleName("Your musician name")
        identity.layout().addWidget(name_label)
        identity.layout().addWidget(self._name)
        root.addWidget(identity)

        music = self._section("Live music")
        music_note = QLabel(
            "Choose your interface, input channels, headphones, and buffer in Jamulus."
        )
        music_note.setObjectName("SimpleSettingsHint")
        music_note.setWordWrap(True)
        music.layout().addWidget(music_note)
        self._open_jamulus = QPushButton("Open Jamulus Audio Settings")
        self._open_jamulus.setObjectName("QuietButton")
        self._open_jamulus.setAccessibleName("Open Jamulus audio settings")
        self._open_jamulus.setAccessibleDescription(
            "Brings the current Jamulus client forward. In Jamulus, choose Settings then Audio/Network Settings."
        )
        self._open_jamulus.clicked.connect(self.audio_settings_requested.emit)
        music.layout().addWidget(
            self._open_jamulus, alignment=Qt.AlignmentFlag.AlignLeft
        )
        root.addWidget(music)

        conversation = self._section("Conversation")
        self._conversation_toggle = QToolButton()
        self._conversation_toggle.setObjectName("SimpleSettingsDisclosure")
        self._conversation_toggle.setText("Webex link (optional)")
        self._conversation_toggle.setCheckable(True)
        self._conversation_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._conversation_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._conversation_toggle.setAccessibleName("Optional Webex meeting link")
        conversation.layout().addWidget(self._conversation_toggle)
        self._conversation_body = QWidget()
        conversation_body = QVBoxLayout(self._conversation_body)
        conversation_body.setContentsMargins(0, Space.SM, 0, 0)
        conversation_body.setSpacing(Space.XS)
        video_note = QLabel(
            "Webex is optional for talking or video. Keep its microphone muted while playing."
        )
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

        footer = QHBoxLayout()
        if show_band_check_action:
            band_check = QPushButton("Verify Sound")
            band_check.setObjectName("GhostButton")
            band_check.setAccessibleName("Save these settings and verify sound")
            band_check.clicked.connect(self._save_and_run_band_check)
            footer.addWidget(band_check)
        footer.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("GhostButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton(primary_action_label)
        save.setObjectName("PrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self._save_and_accept)
        footer.addWidget(cancel)
        footer.addWidget(save)
        root.addLayout(footer)

        self._name.textChanged.connect(self._clear_error)
        self._video.textChanged.connect(self._clear_error)

    @staticmethod
    def should_show_on_startup(_settings: AppSettings) -> bool:
        """Legacy compatibility: startup always begins with Host or Join."""

        return False

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

    def _set_conversation_visible(self, visible: bool) -> None:
        self._conversation_body.setVisible(visible)
        self._conversation_toggle.setArrowType(
            Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
        )

    def _clear_error(self, *_args) -> None:
        self._error.clear()
        self._error.setVisible(False)

    def _show_error(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)

    @property
    def run_band_check_after_save(self) -> bool:
        """Whether the user explicitly asked to open the optional verifier."""

        return self._run_band_check_after_save

    def _save_and_accept(self) -> None:
        if self._save():
            self.accept()

    def _save_and_run_band_check(self) -> None:
        if self._save():
            self._run_band_check_after_save = True
            self.accept()

    def _save(self) -> bool:
        name = self._name.text().strip() or default_musician_name(self._settings)
        video = normalize_webex_url(self._video.text())
        if video:
            error = webex_url_error(video)
            if error:
                self._show_error(error)
                return False
        self._settings.musician_name = name
        self._settings.webex_url = video
        self._settings.webex_audio_mode = "talkback"
        # Legacy live-route values remain in the saved object for a bounded
        # migration window, but no UI here reads or updates them.  Jamulus
        # owns live audio; Recording Setup and Studio own their own routes.
        try:
            save_settings(self._settings)
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("Could not save musician settings: %s", exc)
            self._show_error(
                "WebJam couldn't save these settings. Check folder access and try again."
            )
            return False
        return True
