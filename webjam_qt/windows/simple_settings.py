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
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.settings import AppSettings, save_settings
from core.webex_url import (
    normalize_webex_url,
    webex_site_hostname,
    webex_url_error,
)
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
        webex_opener: Optional[Callable[[str], bool]] = None,
        settings_provider: Optional[Callable[[], AppSettings]] = None,
    ) -> None:
        super().__init__(parent)
        # Keep edits isolated until the settings file is saved successfully.
        self._settings = deepcopy(settings)
        self._settings_provider = settings_provider or (lambda: settings)
        self._run_band_check_after_save = False
        if webex_opener is None:
            from webex_integration import open_webex_meeting

            webex_opener = open_webex_meeting
        self._webex_opener = webex_opener
        self.setObjectName("SimpleSettingsDialog")
        self.setWindowTitle("WebJam Settings")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.resize(640, 560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        outer.setSpacing(Space.MD)
        self._settings_scroll = QScrollArea()
        self._settings_scroll.setObjectName("SimpleSettingsScroll")
        self._settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._settings_scroll.setWidgetResizable(True)
        self._settings_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, Space.XS, 0)
        root.setSpacing(Space.MD)
        self._settings_scroll.setWidget(content)
        outer.addWidget(self._settings_scroll, stretch=1)

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
        self._conversation_toggle.setText("Webex (optional)")
        self._conversation_toggle.setCheckable(True)
        self._conversation_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._conversation_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._conversation_toggle.setAccessibleName(
            "Optional Webex meeting or Personal Room link"
        )
        conversation.layout().addWidget(self._conversation_toggle)
        self._conversation_body = QWidget()
        conversation_body = QVBoxLayout(self._conversation_body)
        conversation_body.setContentsMargins(0, Space.SM, 0, 0)
        conversation_body.setSpacing(Space.XS)
        video_note = QLabel(
            "Webex is optional for talking or video. Webex handles sign-in, "
            "camera, microphone, and meeting controls. Your WebJam musician "
            "name does not change your Webex identity."
        )
        video_note.setObjectName("SimpleSettingsHint")
        video_note.setWordWrap(True)
        video_label = self._field_label("Meeting or Personal Room link")
        self._video = QLineEdit(settings.webex_url)
        self._video.setPlaceholderText(
            "https://your-site.webex.com/meet/your-room"
        )
        self._video.setAccessibleName("Webex meeting or Personal Room link")
        self._video_site = QLabel("")
        self._video_site.setObjectName("SimpleSettingsHint")
        self._video_site.setTextFormat(Qt.TextFormat.PlainText)
        self._video_site.setVisible(False)
        self._open_webex = QPushButton("Open in Webex")
        self._open_webex.setObjectName("QuietButton")
        self._open_webex.setAccessibleName("Open this link in Webex")
        self._open_webex.setAccessibleDescription(
            "Opens the entered link externally. Sign-in and joining remain in Webex."
        )
        self._open_webex.clicked.connect(self._open_webex_test)
        self._webex_status = QLabel("")
        self._webex_status.setObjectName("SimpleSettingsHint")
        self._webex_status.setAccessibleName("Webex link test result")
        self._webex_status.setWordWrap(True)
        self._webex_status.setTextFormat(Qt.TextFormat.PlainText)
        self._webex_status.setVisible(False)
        conversation_body.addWidget(video_note)
        conversation_body.addWidget(video_label)
        conversation_body.addWidget(self._video)
        conversation_body.addWidget(self._video_site)
        conversation_body.addWidget(
            self._open_webex, alignment=Qt.AlignmentFlag.AlignLeft
        )
        conversation_body.addWidget(self._webex_status)
        conversation.layout().addWidget(self._conversation_body)
        root.addWidget(conversation)
        self._conversation_toggle.toggled.connect(self._set_conversation_visible)
        self._conversation_toggle.setChecked(bool(settings.webex_url.strip()))
        self._set_conversation_visible(self._conversation_toggle.isChecked())

        self._error = QLabel("")
        self._error.setObjectName("SimpleSettingsError")
        self._error.setAccessibleName("Settings error")
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
        outer.addLayout(footer)

        self._name.textChanged.connect(self._clear_error)
        self._video.textChanged.connect(self._on_webex_text_changed)
        self._on_webex_text_changed(self._video.text())

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
        self._error.setAccessibleDescription("")
        self._error.setVisible(False)

    def _on_webex_text_changed(self, text: str) -> None:
        self._clear_error()
        self._webex_status.clear()
        self._webex_status.setAccessibleDescription("")
        self._webex_status.setVisible(False)
        hostname = webex_site_hostname(text)
        self._video_site.setText(f"Webex site: {hostname}" if hostname else "")
        self._video_site.setVisible(bool(hostname))
        self._open_webex.setEnabled(bool(str(text or "").strip()))

    def _open_webex_test(self) -> None:
        """Validate and externally open the draft link without saving it."""

        url = normalize_webex_url(self._video.text())
        error = webex_url_error(url)
        if error:
            self._webex_status.setText(f"Check this link before opening: {error}.")
            self._webex_status.setVisible(True)
            self._announce(self._webex_status, focus=self._video)
            return
        if url != self._video.text().strip():
            self._video.setText(url)
        try:
            opened = bool(self._webex_opener(url))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "External Webex test launch failed (%s)", type(exc).__name__
            )
            opened = False
        self._webex_status.setText(
            "Opened externally—finish joining in Webex. Choose Save to keep "
            "this link in WebJam."
            if opened
            else "Webex could not be opened. Check your browser or Webex app and try again."
        )
        self._webex_status.setVisible(True)
        self._announce(self._webex_status)

    @staticmethod
    def _announce(label: QLabel, *, focus: Optional[QWidget] = None) -> None:
        """Expose dynamic validation/results to assistive technology."""

        label.setAccessibleDescription(label.text())
        try:
            QAccessible.updateAccessibility(
                QAccessibleEvent(label, QAccessible.Event.DescriptionChanged)
            )
        except (RuntimeError, TypeError):
            pass
        if focus is not None:
            focus.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_error(
        self,
        message: str,
        *,
        focus: Optional[QWidget] = None,
    ) -> None:
        self._error.setText(message)
        self._error.setVisible(True)
        self._announce(self._error, focus=focus)

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
                self._show_error(error, focus=self._video)
                return False
        # This dialog owns only identity and the optional Webex link. A modal
        # Qt dialog continues processing native invitation callbacks; merge
        # into the controller's latest settings object so a role/endpoint
        # replacement cannot be overwritten by this dialog's stale hidden
        # copy.
        try:
            candidate = deepcopy(self._settings_provider())
        except Exception:  # noqa: BLE001 - retain the opening snapshot safely
            candidate = deepcopy(self._settings)
        candidate.musician_name = name
        candidate.webex_url = video
        candidate.webex_audio_mode = "talkback"
        # Legacy live-route values remain in the saved object for a bounded
        # migration window, but no UI here reads or updates them.  Jamulus
        # owns live audio; Recording Setup and Studio own their own routes.
        try:
            save_settings(candidate)
        except Exception as exc:  # noqa: BLE001
            # The exception may include the private configuration path.  Keep
            # diagnostics useful without copying paths, URLs, or credentials
            # into a support bundle.
            LOGGER.error(
                "Could not save musician settings (%s)",
                type(exc).__name__,
            )
            self._show_error(
                "WebJam couldn't save these settings. Check folder access and try again."
            )
            return False
        self._settings = candidate
        return True
