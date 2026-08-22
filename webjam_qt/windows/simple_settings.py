"""Small WebJam preferences, deliberately separate from live music setup.

Jamulus is the authority for an instrument interface, channel map, buffer,
jitter and musician mix.  This dialog therefore never scans, displays, saves,
or applies live-audio devices.  WebJam retains only identity and optional
conversation preferences here; recording and Studio own their separate setup
at the moment those features are used.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from copy import deepcopy

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

from core.jamulus_name import JamulusNameError, validate_jamulus_name
from core.meeting_link import (
    GENERIC_MEETING_SERVICE_KEY,
    identify_meeting_service,
    meeting_link_error,
    meeting_link_hostname,
    meeting_service_label,
    normalize_meeting_url,
)
from core.provider_credentials import (
    AUDIO_PROVIDER_IDS,
    TEXT_PROVIDER_IDS,
    ProviderCredentials,
)
from core.settings import AppSettings, save_settings
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.jamulus_name_preview import JamulusNamePreview
from webjam_qt.widgets.provider_keys import ProviderKeyPanel
from webjam_qt.windows.launch_dialog import default_musician_name

LOGGER = logging.getLogger("webjam.qt.simple_settings")


class SimpleSettingsDialog(QDialog):
    """Edit non-audio WebJam preferences without duplicating Jamulus."""

    audio_settings_requested = Signal()
    install_webex_requested = Signal()

    def __init__(
        self,
        settings: AppSettings,
        parent: QWidget | None = None,
        *,
        primary_action_label: str = "Save",
        show_band_check_action: bool = True,
        webex_opener: Callable[[str], bool] | None = None,
        settings_provider: Callable[[], AppSettings] | None = None,
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
        # The scroll viewport is structural, not an action. Leaving it in the
        # focus chain made Settings open on an unnamed blank target before the
        # musician could reach the name field.
        self._settings_scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
        self._name_preview = JamulusNamePreview(self._name)
        identity.layout().addWidget(self._name_preview)
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
        self._conversation_toggle.setText("Meeting link (optional)")
        self._conversation_toggle.setCheckable(True)
        self._conversation_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._conversation_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._conversation_toggle.setAccessibleName(
            "Optional meeting link for conversation or video"
        )
        conversation.layout().addWidget(self._conversation_toggle)
        self._conversation_body = QWidget()
        conversation_body = QVBoxLayout(self._conversation_body)
        conversation_body.setContentsMargins(0, Space.SM, 0, 0)
        conversation_body.setSpacing(Space.XS)
        video_note = QLabel(
            "Conversation and video are optional. WebJam opens a supported "
            "meeting link externally. Your selected meeting service handles "
            "sign-in, camera, microphone, and meeting controls; your WebJam "
            "musician name does not change your identity there."
        )
        video_note.setObjectName("SimpleSettingsHint")
        video_note.setWordWrap(True)
        video_label = self._field_label(
            "Meeting link (any platform)"
        )
        self._video = QLineEdit(settings.webex_url)
        self._video.setPlaceholderText("Paste a public https:// meeting link")
        self._video.setAccessibleName("Optional meeting link")
        self._video_site = QLabel("")
        self._video_site.setObjectName("SimpleSettingsHint")
        self._video_site.setTextFormat(Qt.TextFormat.PlainText)
        self._video_site.setVisible(False)
        self._open_webex = QPushButton("Open Meeting Link")
        self._open_webex.setObjectName("QuietButton")
        self._open_webex.setAccessibleName("Open this meeting link externally")
        self._open_webex.setAccessibleDescription(
            "Opens the entered link externally. Sign-in and joining remain "
            "in the selected meeting service."
        )
        self._open_webex.clicked.connect(self._open_webex_test)
        self._get_webex = QPushButton("Get Webex from Cisco")
        self._get_webex.setObjectName("QuietButton")
        self._get_webex.setAccessibleName("Get Webex from Cisco")
        self._get_webex.setAccessibleDescription(
            "Open Cisco's official Webex installer download. WebJam does not "
            "bundle, silently install, or accept terms for Webex."
        )
        self._get_webex.clicked.connect(self.install_webex_requested.emit)
        self._webex_status = QLabel("")
        self._webex_status.setObjectName("SimpleSettingsHint")
        self._webex_status.setAccessibleName("Meeting link test result")
        self._webex_status.setWordWrap(True)
        self._webex_status.setTextFormat(Qt.TextFormat.PlainText)
        self._webex_status.setVisible(False)
        conversation_body.addWidget(video_note)
        conversation_body.addWidget(video_label)
        conversation_body.addWidget(self._video)
        conversation_body.addWidget(self._video_site)
        webex_actions = QHBoxLayout()
        webex_actions.setContentsMargins(0, 0, 0, 0)
        webex_actions.setSpacing(Space.SM)
        webex_actions.addWidget(self._open_webex)
        webex_actions.addWidget(self._get_webex)
        webex_actions.addStretch(1)
        conversation_body.addLayout(webex_actions)
        conversation_body.addWidget(self._webex_status)
        conversation.layout().addWidget(self._conversation_body)
        root.addWidget(conversation)
        self._conversation_toggle.toggled.connect(self._set_conversation_visible)
        self._conversation_toggle.setChecked(bool(settings.webex_url.strip()))
        self._set_conversation_visible(self._conversation_toggle.isChecked())

        # Optional keys. Collapsed by default and last, because a musician who
        # opens Settings mid-jam is looking for their name or a meeting link,
        # and nothing below this line is needed to play.
        keys = self._section("Optional keys")
        self._keys_toggle = QToolButton()
        self._keys_toggle.setObjectName("SimpleSettingsDisclosure")
        self._keys_toggle.setText("Song tools and writing help keys (optional)")
        self._keys_toggle.setCheckable(True)
        self._keys_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._keys_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._keys_toggle.setAccessibleName(
            "Optional Music AI and writing help keys"
        )
        keys.layout().addWidget(self._keys_toggle)
        self._keys_panel = ProviderKeyPanel(
            (*AUDIO_PROVIDER_IDS, *TEXT_PROVIDER_IDS),
            credentials=ProviderCredentials(settings=settings),
        )
        self._keys_panel.setVisible(False)
        keys.layout().addWidget(self._keys_panel)
        self._keys_toggle.toggled.connect(self._set_keys_visible)
        root.addWidget(keys)

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

    def show_optional_keys(self) -> None:
        """Open on the keys section, for a caller that came looking for one."""

        self._keys_toggle.setChecked(True)

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

    def _set_keys_visible(self, visible: bool) -> None:
        if visible:
            # Re-read on open: a key may have been set in the environment, or
            # saved from the Song panel, since this dialog was built.
            self._keys_panel.refresh()
        self._keys_panel.setVisible(visible)
        self._keys_toggle.setArrowType(
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
        hostname = meeting_link_hostname(text)
        service = meeting_service_label(identify_meeting_service(text))
        self._video_site.setText(
            f"{service} site: {hostname}" if hostname else ""
        )
        self._video_site.setVisible(bool(hostname))
        self._open_webex.setEnabled(bool(str(text or "").strip()))

    def _open_webex_test(self) -> None:
        """Validate and externally open the draft link without saving it."""

        url = normalize_meeting_url(self._video.text())
        error = meeting_link_error(url)
        if error:
            self._webex_status.setText(f"Check this link before opening: {error}.")
            self._webex_status.setVisible(True)
            self._announce(self._webex_status, focus=self._video)
            return
        if url != self._video.text().strip():
            self._video.setText(url)
        service_key = identify_meeting_service(url)
        service = meeting_service_label(service_key)
        destination = (
            "your meeting service"
            if service_key == GENERIC_MEETING_SERVICE_KEY
            else service
        )
        try:
            opened = bool(self._webex_opener(url))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "External meeting-link test launch failed (%s)",
                type(exc).__name__,
            )
            opened = False
        self._webex_status.setText(
            f"Opened externally—finish joining in {destination}. Choose Save to "
            "keep this link in WebJam."
            if opened
            else (
                f"{service} could not be opened. Check your browser or "
                "meeting app and try again."
                if service_key != GENERIC_MEETING_SERVICE_KEY
                else "The meeting link could not be opened. Check your browser "
                "or meeting app and try again."
            )
        )
        self._webex_status.setVisible(True)
        self._announce(self._webex_status)

    @staticmethod
    def _announce(label: QLabel, *, focus: QWidget | None = None) -> None:
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
        focus: QWidget | None = None,
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
        try:
            name = validate_jamulus_name(self._name.text()).value
        except JamulusNameError as exc:
            self._show_error(str(exc), focus=self._name)
            return False
        video = normalize_meeting_url(self._video.text())
        if video:
            error = meeting_link_error(video)
            if error:
                self._show_error(error, focus=self._video)
                return False
        # This dialog owns only identity and the optional meeting link. A modal
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
