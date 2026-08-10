"""
SetupWizard — first-run configuration wizard.

Guides new users through:
  Page 0 — Welcome
  Page 1 — Jamulus server (host + port)
  Page 2 — Webex meeting (URL)
  Page 3 — Optional local recording input
  Page 4 — All done

The wizard saves settings to ``~/.webjam_config.json`` on Finish.
It is shown automatically on first run (config file absent) and can be
reopened via the Settings side-rail button.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWizard,
    QWizardPage,
    QWidget,
)

from core.jamulus_name import (
    DEFAULT_JAMULUS_NAME,
    JamulusNameError,
    validate_jamulus_name,
)
from core.settings import AppSettings, load_settings
from core.meeting_link import (
    identify_meeting_service,
    meeting_link_error,
    meeting_link_hostname,
    meeting_service_label,
    normalize_meeting_url,
)
from webjam_qt.theme.tokens import Color, Font, Space
from webjam_qt.widgets.jamulus_name_preview import JamulusNamePreview

LOGGER = logging.getLogger("webjam.qt.setup_wizard")


# ---------------------------------------------------------------------------
# Page IDs
# ---------------------------------------------------------------------------
class _PageId:
    WELCOME  = 0
    JAMULUS  = 1
    WEBEX    = 2
    ROUTING  = 3
    DONE     = 4


# ---------------------------------------------------------------------------
# Helper — styled section header
# ---------------------------------------------------------------------------
def _section_label(text: str, parent: Optional[QWidget] = None) -> QLabel:
    lbl = QLabel(text, parent)
    font = lbl.font()
    font.setPixelSize(Font.SIZE_MD)
    font.setWeight(QFont.Weight.DemiBold)
    lbl.setFont(font)
    return lbl


def _body_label(text: str, parent: Optional[QWidget] = None) -> QLabel:
    lbl = QLabel(text, parent)
    lbl.setWordWrap(True)
    lbl.setObjectName("BodyLabel")
    return lbl


# ---------------------------------------------------------------------------
# Page 0 — Welcome
# ---------------------------------------------------------------------------
class _WelcomePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Welcome to WebJam")
        self.setSubTitle(
            "WebJam coordinates Jamulus music, native Webex conversation, "
            "recording, and session notes."
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(Space.LG)

        layout.addWidget(_body_label(
            "This short wizard will configure:\n\n"
            "  \u2022  Your Jamulus server connection\n"
            "  \u2022  Your Webex meeting link\n"
            "  \u2022  An optional local meter and recording input\n\n"
            "You can change any setting later from the Settings panel."
        ))

        # Jamulus prerequisite notice — appears on first page. macOS builds
        # bundle Jamulus (zero-install); Windows builds bundle its official
        # installer. Dev checkouts and older builds fall back to a manual
        # install, so this stays visible as a heads-up either way.
        notice = _body_label(
            "\u2139\ufe0f  WebJam uses Jamulus for the actual band audio. Recent "
            "builds bundle it (macOS: zero-install; Windows: an in-wizard "
            "installer on the next page) — if yours doesn't, get it free at "
            "<a href='https://jamulus.io'>jamulus.io</a>."
        )
        notice.setOpenExternalLinks(True)
        notice.setTextFormat(Qt.TextFormat.RichText)
        notice.setWordWrap(True)
        notice.setStyleSheet(
            f"QLabel {{ background: {Color.BG_CARD}; "
            f"border: 1px solid {Color.ACCENT_PRIMARY}; "
            "border-radius: 6px; padding: 10px; }"
        )
        layout.addWidget(notice)
        layout.addStretch(1)


# ---------------------------------------------------------------------------
# Page 1 — Jamulus
# ---------------------------------------------------------------------------
class _JamulusPage(QWizardPage):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self._settings = settings
        self.setTitle("Jamulus Server")
        self.setSubTitle(
            "Enter your band's Jamulus server details. Ask your band admin if you're not sure."
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(Space.MD)

        layout.addWidget(_section_label("Server host"))
        self._host = QLineEdit(settings.jamulus_server)
        self._host.setPlaceholderText("e.g. 192.168.1.100 or myband.example.com")
        self._host.setAccessibleName("Jamulus server hostname or IP address")
        layout.addWidget(self._host)

        # Live-validation hint — hidden unless the user types something dodgy.
        self._host_hint = _body_label("")
        self._host_hint.setTextFormat(Qt.TextFormat.PlainText)
        self._host_hint.setVisible(False)
        layout.addWidget(self._host_hint)
        self._host.textChanged.connect(self._validate_host_live)

        # Helpful hint for users who don't yet have a server
        _hint = _body_label(
            "Don't have a server? Browse free public ones at "
            "<a href='https://explorer.jamulus.io'>explorer.jamulus.io</a> "
            "or, on macOS, let WebJam host one with the option below."
        )
        _hint.setOpenExternalLinks(True)
        _hint.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(_hint)

        layout.addWidget(_section_label("Port"))
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(settings.jamulus_port)
        self._port.setAccessibleName("Jamulus server port number")
        layout.addWidget(self._port)

        layout.addWidget(_section_label("Local Jamulus control port"))
        self._rpc_port = QSpinBox()
        self._rpc_port.setRange(1, 65535)
        self._rpc_port.setValue(settings.jamulus_rpc_port)
        self._rpc_port.setAccessibleName("Jamulus JSON-RPC port (--jsonrpcport)")
        layout.addWidget(self._rpc_port)

        layout.addWidget(_body_label(
            "Leave this as 22222 unless your band admin says otherwise. "
            "WebJam assigns this port to the Jamulus client it launches so "
            "it can read participant names and control your mixer. It is "
            "not the band's audio-server or recorder-control port."
        ))

        layout.addWidget(_section_label("Your musician name"))
        configured_name = getattr(settings, "musician_name", "") or ""
        if configured_name == DEFAULT_JAMULUS_NAME:
            configured_name = ""
        elif configured_name:
            try:
                configured_name = validate_jamulus_name(configured_name).value
            except JamulusNameError:
                configured_name = ""
        self._musician_name = QLineEdit(configured_name)
        self._musician_name.setPlaceholderText("e.g. Jeff — Guitar")
        self._musician_name.setAccessibleName(
            "Your musician name shown to Jamulus participants"
        )
        layout.addWidget(self._musician_name)
        self._musician_name_preview = JamulusNamePreview(self._musician_name)
        layout.addWidget(self._musician_name_preview)

        host_section = _section_label("Band server")
        layout.addWidget(host_section)
        self._host_server = QCheckBox("This Mac hosts the band server")
        hosting_supported = sys.platform == "darwin"
        self._host_server.setChecked(hosting_supported and bool(
            getattr(settings, "host_server_enabled", False)
        ))
        self._host_server.setAccessibleName("Host the band server on this Mac")
        layout.addWidget(self._host_server)
        from services.bridge_service import _bundled_jamulus_server_candidate
        server_availability = (
            "The downloadable macOS build includes JamulusServer.app 3.12.2. "
            if _bundled_jamulus_server_candidate()
            else "Source builds require official JamulusServer.app 3.12.2. "
        )
        self._host_note = QLabel(
            server_availability
            + "A server WebJam starts stays up through Stop Audio and stops "
            "when WebJam quits. An authenticated manual server is adopted "
            "without WebJam taking ownership. "
            "Recordings and the recorder secret live in the server app's "
            "own container. Exactly one Mac in the band hosts."
        )
        self._host_note.setWordWrap(True)
        self._host_note.setObjectName("BodyLabel")
        layout.addWidget(self._host_note)
        host_section.setVisible(hosting_supported)
        self._host_server.setVisible(hosting_supported)
        self._host_note.setVisible(hosting_supported)
        self._host_server.toggled.connect(self._on_host_server_toggled)
        self._on_host_server_toggled(self._host_server.isChecked())

        layout.addWidget(_section_label("Jamulus executable"))
        # Pre-populate with first existing user/default candidate path...
        detected_path = ""
        for candidate in settings.jamulus_candidates:
            if Path(candidate).exists():
                detected_path = candidate
                break

        # ...falling back to the copy bundled inside WebJam itself (macOS
        # zero-install nesting — see THIRD_PARTY_NOTICES.md) so a fresh
        # install doesn't need this field touched at all.
        bundled_note = False
        if not detected_path:
            from services.bridge_service import _bundled_jamulus_candidate
            bundled = _bundled_jamulus_candidate()
            if bundled:
                detected_path = bundled
                bundled_note = True

        self._jamulus_path = QLineEdit(detected_path)
        self._jamulus_path.setPlaceholderText(
            "Enter the full path to Jamulus"
        )
        self._jamulus_path.setAccessibleName("Path to Jamulus executable")
        browse_btn = QPushButton("Browse…")
        browse_btn.setObjectName("GhostButton")
        browse_btn.clicked.connect(self._browse_jamulus)
        path_row = QHBoxLayout()
        path_row.setSpacing(Space.SM)
        path_row.addWidget(self._jamulus_path, stretch=1)
        path_row.addWidget(browse_btn)
        layout.addLayout(path_row)

        if bundled_note:
            layout.addWidget(_body_label(
                "\u2713 Using the copy of Jamulus bundled with WebJam — "
                "nothing else to install."
            ))
        else:
            layout.addWidget(_body_label(
                "macOS: /Applications/Jamulus.app/Contents/MacOS/Jamulus\n"
                "Windows: C:\\Program Files\\Jamulus\\Jamulus.exe\n"
                "Linux: /usr/bin/jamulus\n"
                "Required — WebJam launches Jamulus for you (free at jamulus.io)."
            ))

        # Windows: WebJam ships the official Jamulus installer inside its
        # own install directory (see the Jamulus/ datas block in
        # webjam.spec). If nothing was found above but that bundled
        # installer is present, offer to run it instead of sending the
        # user to jamulus.io — mirrors the audio-routing page's "Show me
        # how to set this up" pattern below.
        self._bundled_installer_path: Optional[str] = None
        install_btn = QPushButton("Install Jamulus now")
        install_btn.setObjectName("GhostButton")
        install_btn.setVisible(False)
        install_btn.clicked.connect(self._install_bundled_jamulus)
        layout.addWidget(install_btn)
        self._install_jamulus_btn = install_btn

        self._install_status = QLabel("")
        self._install_status.setWordWrap(True)
        self._install_status.setVisible(False)
        layout.addWidget(self._install_status)

        self._install_poll_timer = QTimer(self)
        self._install_poll_timer.setInterval(2000)
        self._install_poll_timer.timeout.connect(self._poll_for_jamulus_install)
        self._install_poll_ticks = 0

        if not detected_path:
            from services.bridge_service import _bundled_jamulus_installer
            installer = _bundled_jamulus_installer()
            if installer:
                self._bundled_installer_path = installer
                self._install_jamulus_btn.setVisible(True)

        layout.addStretch(1)

        # Shown by validatePage() so a blocked Next always says why.
        self._page_error = QLabel("")
        self._page_error.setObjectName("WizardError")
        self._page_error.setWordWrap(True)
        self._page_error.setVisible(False)
        layout.addWidget(self._page_error)
        for editor in (self._host, self._musician_name, self._jamulus_path):
            editor.textChanged.connect(self._hide_page_error)

        # Deliberately NOT mandatory ("*") fields: Qt only treats a mandatory
        # field as complete once its value changes from what it was at
        # registration, so pre-filling them from saved settings leaves Next
        # permanently disabled with no explanation. validatePage() is the
        # single gate and reports failures through _page_error instead.
        self.registerField("jamulus_server", self._host)
        self.registerField("jamulus_port",    self._port, "value")
        self.registerField("jamulus_rpc_port", self._rpc_port, "value")
        self.registerField("musician_name", self._musician_name)

    def _install_bundled_jamulus(self) -> None:
        """Launch the bundled Jamulus installer and poll for completion.

        Non-blocking equivalent of the wait-and-poll pattern used by the
        legacy VB-CABLE install flow (legacy/webjam_launch_session.py):
        instead of a blocking sleep loop, a QTimer ticks every 2s so the
        wizard UI stays responsive while the user works through the
        (Jamulus-owned) installer UI and UAC prompt.
        """
        if not self._bundled_installer_path:
            return
        from services.bridge_service import _is_pinned_jamulus_installer

        if not _is_pinned_jamulus_installer(self._bundled_installer_path):
            self._bundled_installer_path = None
            self._install_jamulus_btn.setEnabled(False)
            self._install_status.setText(
                "The included Jamulus installer failed its integrity check. "
                "Re-extract an official WebJam download and try again."
            )
            self._install_status.setVisible(True)
            return
        try:
            subprocess.Popen([self._bundled_installer_path], shell=False)
        except Exception as exc:
            LOGGER.warning("Failed to launch bundled Jamulus installer: %s", exc)
            return
        self._install_jamulus_btn.setEnabled(False)
        self._install_jamulus_btn.setText("Waiting for install to finish\u2026")
        self._install_status.setVisible(False)
        self._install_poll_ticks = 0
        self._install_poll_timer.start()

    # 150 ticks \u00d7 2 s = 5 minutes; a cancelled or failed installer must not
    # leave the button stuck on "Waiting\u2026" forever.
    _INSTALL_POLL_LIMIT = 150

    def _poll_for_jamulus_install(self) -> None:
        """QTimer tick: fill in the path field as soon as install lands."""
        for candidate in self._settings.jamulus_candidates:
            path = Path(candidate).expanduser()
            if path.is_file():
                self._install_poll_timer.stop()
                self._jamulus_path.setText(str(path))
                self._install_jamulus_btn.setVisible(False)
                self._install_status.setVisible(False)
                return
        self._install_poll_ticks += 1
        if self._install_poll_ticks >= self._INSTALL_POLL_LIMIT:
            self._install_poll_timer.stop()
            self._install_jamulus_btn.setEnabled(True)
            self._install_jamulus_btn.setText("Install Jamulus now")
            self._install_status.setText(
                "The installer didn't finish after 5 minutes. If Jamulus is "
                "already installed, click Browse\u2026 to point WebJam at it, "
                "or press Install Jamulus now to try again."
            )
            self._install_status.setVisible(True)

    def _browse_jamulus(self) -> None:
        start_dir = ""
        import sys
        if sys.platform == "darwin":
            start_dir = "/Applications"
        path, _ = QFileDialog.getOpenFileName(
            self, "Find Jamulus", start_dir,
            "Jamulus (Jamulus Jamulus.exe);;All files (*)"
        )
        if not path:
            return
        # On macOS the user might pick the .app bundle itself
        p = Path(path)
        if p.suffix == ".app":
            binary = p / "Contents" / "MacOS" / "Jamulus"
            if binary.exists():
                path = str(binary)
        self._jamulus_path.setText(path)

    @Slot(str)
    def _validate_host_live(self, text: str) -> None:
        """Show a typing-time hint about obvious host-input mistakes."""
        stripped = text.strip()
        if not stripped:
            # Empty — placeholder text is enough; stay silent.
            self._host_hint.setVisible(False)
            return
        if " " in stripped:
            self._host_hint.setText(
                "Host shouldn't contain spaces \u2014 use myband.example.com"
            )
            self._host_hint.setVisible(True)
            return
        self._host_hint.setVisible(False)

    def _auto_detected_jamulus(self) -> str:
        checked: set[str] = set()
        for candidate in list(self._settings.jamulus_candidates) + list(
            AppSettings().jamulus_candidates
        ):
            if candidate in checked:
                continue
            checked.add(candidate)
            if Path(candidate).expanduser().exists():
                return candidate
        from services.bridge_service import _bundled_jamulus_candidate
        return _bundled_jamulus_candidate() or ""

    def _resolved_jamulus_path(self) -> str:
        explicit = self._jamulus_path.text().strip()
        if explicit:
            path = Path(explicit).expanduser()
            return str(path) if path.exists() else ""
        return self._auto_detected_jamulus()

    def _hide_page_error(self, _text: str = "") -> None:
        self._page_error.setVisible(False)

    def _show_page_error(self, message: str) -> None:
        self._page_error.setText(message)
        self._page_error.setVisible(True)

    def validatePage(self) -> bool:
        host = self._host.text().strip()
        if not host:
            self._show_page_error(
                "Enter your band's server host — or tick “This Mac "
                "hosts the band server”."
            )
            self._host.setFocus()
            return False
        try:
            validate_jamulus_name(self._musician_name.text())
        except JamulusNameError as exc:
            self._show_page_error(str(exc))
            self._musician_name.setFocus()
            return False
        if not self._resolved_jamulus_path():
            explicit = self._jamulus_path.text().strip()
            if explicit:
                # A saved path can go stale (moved install, macOS App
                # Translocation). If detection finds a working copy, heal the
                # field visibly instead of dead-ending the wizard.
                detected = self._auto_detected_jamulus()
                if detected:
                    self._jamulus_path.setText(detected)
                    self._hide_page_error()
                    return True
                self._show_page_error(
                    f"Jamulus wasn't found at “{explicit}”. Check "
                    "the path, click Browse…, or clear the field to use the "
                    "auto-detected copy."
                )
            else:
                self._show_page_error(
                    "Jamulus isn't installed — click Browse… to point WebJam "
                    "at it, or install it free from jamulus.io."
                )
            self._jamulus_path.setFocus()
            self._jamulus_path.selectAll()
            return False
        self._hide_page_error()
        return True

    def _on_host_server_toggled(self, checked: bool) -> None:
        """Hosted mode is always a same-Mac loopback topology."""
        if checked:
            self._host.setText("127.0.0.1")
            self._host.setEnabled(False)
            self._host_hint.setVisible(False)
        else:
            self._host.setEnabled(True)

    @property
    def host(self) -> str:
        return "127.0.0.1" if self.host_server_enabled else self._host.text().strip()

    @property
    def port(self) -> int:
        return self._port.value()

    @property
    def rpc_port(self) -> int:
        return self._rpc_port.value()

    @property
    def jamulus_path(self) -> str:
        return self._resolved_jamulus_path() or self._jamulus_path.text().strip()

    @property
    def musician_name(self) -> str:
        return self._musician_name.text().strip()

    @property
    def host_server_enabled(self) -> bool:
        return sys.platform == "darwin" and self._host_server.isChecked()


# ---------------------------------------------------------------------------
# Page 2 — Webex
# ---------------------------------------------------------------------------
class _WebexPage(QWizardPage):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.setTitle("Webex Conversation")
        self.setSubTitle(
            "Enter the meeting or Personal Room link your band uses."
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(Space.MD)

        layout.addWidget(_section_label("Meeting or Personal Room link"))
        self._url = QLineEdit(settings.webex_url)
        self._url.setPlaceholderText(
            "https://your-site.webex.com/meet/your-room"
        )
        self._url.setAccessibleName("Webex meeting or Personal Room link")
        self._url.setToolTip(
            "Enter your Webex Meeting or Personal Room link. If you forget "
            "the https:// prefix, "
            "we'll add it for you.\n\n"
            "Example: https://your-site.webex.com/meet/your-room"
        )
        layout.addWidget(self._url)

        # Live-validation hint — hidden unless the user types something dodgy.
        self._url_hint = _body_label("")
        self._url_hint.setTextFormat(Qt.TextFormat.PlainText)
        self._url_hint.setVisible(False)
        layout.addWidget(self._url_hint)
        self._url.textChanged.connect(self._validate_url_live)

        self._site = _body_label("")
        self._site.setTextFormat(Qt.TextFormat.PlainText)
        self._site.setAccessibleName("Webex site")
        self._site.setVisible(False)
        layout.addWidget(self._site)

        layout.addWidget(_body_label(
            "WebJam opens this room in the native Webex app or your default "
            "browser. Webex handles sign-in, camera, microphone, and meeting "
            "controls. Your WebJam musician name does not change your Webex "
            "identity, and WebJam never claims to know whether you joined. "
            "Jamulus remains the performance-audio path."
        ))
        layout.addStretch(1)
        self._validate_url_live(self._url.text())

        # Not a mandatory ("*") field: a value pre-filled from saved settings
        # would leave Next permanently disabled (Qt only counts a mandatory
        # field as complete once it changes). validatePage() gates instead.
        self.registerField("webex_url", self._url)

    @Slot(str)
    def _validate_url_live(self, text: str) -> None:
        """Show a typing-time hint about obvious URL-input mistakes."""
        stripped = text.strip()
        hostname = meeting_link_hostname(stripped)
        service = meeting_service_label(identify_meeting_service(stripped))
        self._site.setText(
            f"{service} site: {hostname}" if hostname else ""
        )
        self._site.setVisible(bool(hostname))
        if not stripped:
            self._url_hint.setVisible(False)
            return
        if " " in stripped or ".." in stripped:
            self._url_hint.setText("URL shouldn't contain spaces or '..'")
            self._url_hint.setVisible(True)
            return
        if "://" in stripped:
            error = meeting_link_error(stripped)
            if error:
                self._url_hint.setText(error)
                self._url_hint.setVisible(True)
                return
            self._url_hint.setVisible(False)
            return
        # No scheme yet — informational nudge if it looks domain-shaped.
        first_segment = stripped.split("/", 1)[0]
        if "." in first_segment:
            self._url_hint.setText("\u2713 Will auto-prepend https://")
            self._url_hint.setVisible(True)
            return
        self._url_hint.setVisible(False)

    def validatePage(self) -> bool:
        url = normalize_meeting_url(self._url.text())
        if url != self._url.text().strip():
            self._url.setText(url)
        error = meeting_link_error(url)
        if error:
            self._url_hint.setText(error)
            self._url_hint.setVisible(True)
            self._url.setFocus()
            self._url.selectAll()
            return False
        return True

    @property
    def webex_url(self) -> str:
        return normalize_meeting_url(self._url.text())

# ---------------------------------------------------------------------------
# Page 3 — Optional local recording
# ---------------------------------------------------------------------------
class _RoutingPage(QWizardPage):
    def __init__(self, settings: Optional[AppSettings] = None) -> None:
        super().__init__()
        self.setTitle("Local Meter and Recording")
        self.setSubTitle(
            "Jamulus owns band audio and Webex owns conversation. "
            "This optional input is for WebJam's meter and local stems only."
        )
        self._complete = True
        self._saved_device_index = settings.audio_input_device_index if settings else -1
        self._local_capture_enabled = bool(
            getattr(settings, "local_capture_enabled", False) if settings else False
        )

        self._capture_chk = QCheckBox("Record a supplemental local input stem")
        self._capture_chk.setAccessibleName("Enable supplemental local recording")
        self._capture_chk.setChecked(self._local_capture_enabled)
        self._capture_chk.stateChanged.connect(self._on_capture_changed)
        self._capture_hint = _body_label(
            "Adds the selected input as a local stem during recording. "
            "The same picker always supplies WebJam's input meter."
        )
        self._takes_label = _section_label("Takes folder")
        self._takes_path = QLineEdit(
            str(getattr(settings, "takes_directory", "") or "")
            if settings else ""
        )
        self._takes_path.setPlaceholderText(
            "Choose the folder where Jamulus server takes appear"
        )
        self._takes_path.setAccessibleName(
            "Folder for Jamulus takes and supplemental local recordings"
        )
        self._takes_path.textChanged.connect(lambda _text: self.completeChanged.emit())
        self._takes_browse = QPushButton("Browse…")
        self._takes_browse.setObjectName("GhostButton")
        self._takes_browse.setAccessibleName("Choose Takes folder")
        self._takes_browse.clicked.connect(self._choose_takes_directory)
        takes_row = QHBoxLayout()
        takes_row.setSpacing(Space.SM)
        takes_row.addWidget(self._takes_path, stretch=1)
        takes_row.addWidget(self._takes_browse)
        self._takes_row = takes_row

        # Device picker — populated independently of the loopback scan.
        # Defaults to "System default" so the wizard remains usable when
        # sounddevice is missing or no devices are detected.
        self._device_label = _section_label("Meter and local recording input")
        self._device_picker = QComboBox()
        self._device_picker.setAccessibleName("Meter and local recording input")
        self._device_picker.addItem("System default", -1)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setAccessibleName("Local meter and recording setup options")
        content = QWidget()
        scroll.setWidget(content)
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)

        layout = QVBoxLayout(content)
        layout.setSpacing(Space.MD)
        layout.addWidget(_body_label(
            "This selection does not configure Jamulus or Webex. Choose the "
            "input whose signal WebJam should meter. Enable the optional stem "
            "only if you also want WebJam to record that local input."
        ))
        layout.addWidget(self._capture_chk)
        layout.addWidget(self._capture_hint)
        layout.addWidget(self._takes_label)
        layout.addLayout(takes_row)
        layout.addWidget(self._device_label)
        layout.addWidget(self._device_picker)
        layout.addStretch(1)
        self._update_capture_controls()

    def initializePage(self) -> None:
        self._populate_device_picker()
        self._complete = True
        self.completeChanged.emit()

    def _populate_device_picker(self) -> None:
        """Fill the device combo with all input-capable devices.

        First entry is always "System default" (data = -1).  The remaining
        entries come from :func:`core.audio_routing.list_input_devices`,
        which returns [] when sounddevice is unavailable, so the picker
        stays usable in that case.
        """
        from core.audio_routing import list_input_devices

        # On the first populate, fall back to the saved settings index;
        # on subsequent re-scans, preserve whatever the user had picked.
        if getattr(self, "_picker_populated", False):
            target = self._device_picker.currentData()
            if target is None:
                target = -1
        else:
            target = self._saved_device_index

        self._device_picker.blockSignals(True)
        try:
            self._device_picker.clear()
            self._device_picker.addItem("System default", -1)
            for dev in list_input_devices():
                label = f"{dev['name']} ({dev['channels']} ch)"
                self._device_picker.addItem(label, dev["index"])

            for i in range(self._device_picker.count()):
                if self._device_picker.itemData(i) == target:
                    self._device_picker.setCurrentIndex(i)
                    break
        finally:
            self._device_picker.blockSignals(False)
        self._picker_populated = True

    def _on_capture_changed(self, state: int) -> None:
        self._local_capture_enabled = bool(state)
        self._update_capture_controls()
        self.completeChanged.emit()

    def _update_capture_controls(self) -> None:
        visible = self._capture_chk.isChecked()
        self._capture_hint.setVisible(visible)
        self._takes_label.setVisible(visible)
        self._takes_path.setVisible(visible)
        self._takes_browse.setVisible(visible)

    def _choose_takes_directory(self) -> None:
        start = self._takes_path.text().strip() or str(Path.home() / "Music")
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Takes folder",
            start,
        )
        if selected:
            self._takes_path.setText(selected)

    def isComplete(self) -> bool:
        capture_ready = (
            not self._capture_chk.isChecked()
            or bool(self._takes_path.text().strip())
        )
        return self._complete and capture_ready

    @property
    def device_index(self) -> int:
        """Return the user's selected input device index, or -1 for default."""
        data = self._device_picker.currentData()
        if data is None:
            return -1
        try:
            return int(data)
        except (TypeError, ValueError):
            return -1

    @property
    def local_capture_enabled(self) -> bool:
        return self._capture_chk.isChecked()

    @property
    def takes_directory(self) -> str:
        return self._takes_path.text().strip()


# ---------------------------------------------------------------------------
# Page 4 — Done
# ---------------------------------------------------------------------------
class _DonePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Configuration saved")
        self.setSubTitle("Run Band Check before your first jam.")

        layout = QVBoxLayout(self)
        layout.addWidget(_body_label(
            "Click Finish to launch the Conductor.\n\n"
            "Quick-start:\n"
            "  1.  Open Band Check (F2)\n"
            "  2.  Click \u201cStart Audio\u201d — WebJam will connect to your Jamulus server\n"
            "  3.  Click \u201cOpen Webex\u201d and finish joining in Webex\n"
            "  4.  Adjust faders as musicians join the session\n\n"
            "You can reopen this wizard any time from the Settings panel."
        ))
        layout.addStretch(1)


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------
class SetupWizard(QWizard):
    """First-run configuration wizard.  Call ``wizard.exec()`` to run modally."""

    def __init__(
        self,
        settings: Optional[AppSettings] = None,
        parent: Optional[QWidget] = None,
        *,
        skip_welcome: bool = False,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or load_settings()

        self.setWindowTitle("WebJam Setup")
        if skip_welcome:
            # Reopened from inside an active session — skip Welcome and Routing
            # since the user already knows what WebJam is and routing was set
            # at first launch.
            self.setWindowTitle("WebJam Settings")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(560, 520)
        self.setOption(QWizard.WizardOption.NoBackButtonOnLastPage, True)

        # Pages
        self._welcome = _WelcomePage()
        self._jamulus = _JamulusPage(self._settings)
        self._webex   = _WebexPage(self._settings)
        self._routing = _RoutingPage(self._settings)
        self._done    = _DonePage()

        self.setPage(_PageId.WELCOME, self._welcome)
        self.setPage(_PageId.JAMULUS, self._jamulus)
        self.setPage(_PageId.WEBEX,   self._webex)
        self.setPage(_PageId.ROUTING, self._routing)
        self.setPage(_PageId.DONE,    self._done)

        if skip_welcome:
            # Start at Jamulus; users in-session don't need the welcome page.
            self.setStartId(_PageId.JAMULUS)

        # Open tall enough for the densest page (Jamulus) instead of Qt's
        # minimum, which clips its word-wrapped guidance labels. Bounded so
        # small screens still fit; the user can resize freely.
        hint = self.sizeHint()
        self.resize(max(hint.width(), 640), min(max(hint.height(), 760), 840))

        self.accepted.connect(self._save_settings)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _save_settings(self) -> None:
        candidate_name = self._jamulus.musician_name
        if (
            not candidate_name
            and self._settings.musician_name == DEFAULT_JAMULUS_NAME
        ):
            # ``_save_settings`` is also exercised directly by migration and
            # smoke callers. The interactive Finish path cannot reach here
            # with a blank field because ``validatePage`` rejects it.
            candidate_name = DEFAULT_JAMULUS_NAME
        try:
            musician_name = validate_jamulus_name(
                candidate_name
            ).value
        except JamulusNameError:
            LOGGER.error("Refusing to save an invalid Jamulus musician name")
            return
        cfg = asdict(self._settings)

        cfg["jamulus_server"]             = self._jamulus.host
        cfg["jamulus_port"]               = self._jamulus.port
        cfg["jamulus_rpc_port"]           = self._jamulus.rpc_port
        cfg["musician_name"]              = musician_name
        cfg["host_server_enabled"]        = self._jamulus.host_server_enabled
        cfg["webex_url"]                  = self._webex.webex_url
        cfg["audio_input_device_index"]   = self._routing.device_index
        cfg["local_capture_enabled"]       = self._routing.local_capture_enabled
        cfg["takes_directory"]             = self._routing.takes_directory
        if cfg["host_server_enabled"]:
            from core.settings import (
                hosted_server_recordings_dir,
                hosted_server_secret_path,
            )
            cfg["jamulus_server"] = "127.0.0.1"
            cfg["server_rpc_secret_file"] = str(hosted_server_secret_path())
            cfg["takes_directory"] = str(hosted_server_recordings_dir())
        # Guest Issuer authentication is deprecated by Webex and unsafe for a
        # local desktop client.  Omit legacy credentials on the next save.
        cfg.pop("webex_guest_issuer_id", None)
        cfg.pop("webex_guest_issuer_secret", None)
        cfg.pop("webex_display_name", None)
        cfg.pop("webex_audio_bridge_enabled", None)
        cfg.pop("webex_audio_mode", None)

        # Insert user-specified Jamulus path at the front of candidates
        jamulus_path = self._jamulus.jamulus_path
        if jamulus_path:
            existing = list(cfg.get("jamulus_candidates") or [])
            # Remove duplicates, put user choice first
            deduped = [jamulus_path] + [c for c in existing if c != jamulus_path]
            cfg["jamulus_candidates"] = deduped

        path = Path(self._settings.config_file)
        try:
            from core.file_io import atomic_write_text
            # Keep user settings private even though Webex credentials are no
            # longer collected or persisted.
            atomic_write_text(path, json.dumps(cfg, indent=2), mode=0o600)
            LOGGER.info("Settings saved to %s", path)
        except Exception as exc:
            LOGGER.error("Failed to save settings: %s", exc)

    @staticmethod
    def should_show_on_startup(settings: AppSettings) -> bool:
        """Return True when the config file doesn't exist yet (first run)."""
        return not Path(settings.config_file).exists()
