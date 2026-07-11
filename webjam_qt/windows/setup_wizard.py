"""
SetupWizard — first-run configuration wizard.

Guides new users through:
  Page 0 — Welcome
  Page 1 — Jamulus server (host + port)
  Page 2 — Webex meeting (URL + optional guest-issuer credentials)
  Page 3 — Audio routing (shows detected device or install instructions)
  Page 4 — All done

The wizard saves settings to ``~/.webjam_config.json`` on Finish.
It is shown automatically on first run (config file absent) and can be
reopened via the Settings side-rail button.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWizard,
    QWizardPage,
    QWidget,
)

from core.settings import AppSettings, load_settings
from core.webex_url import normalize_webex_url, webex_url_error
from webjam_qt.theme.tokens import Font, Space

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
            "WebJam combines Jamulus (for band audio) and Webex (video) "
            "in one place — no switching apps."
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(Space.LG)

        layout.addWidget(_body_label(
            "This short wizard will configure:\n\n"
            "  \u2022  Your Jamulus server connection\n"
            "  \u2022  Your Webex meeting link\n"
            "  \u2022  Audio connection so your band is heard in the video call\n\n"
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
            "QLabel { background: rgba(120, 170, 255, 0.10); "
            "border: 1px solid rgba(120, 170, 255, 0.35); "
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
            "or run your own locally (jamulus.io)."
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

        self._install_poll_timer = QTimer(self)
        self._install_poll_timer.setInterval(2000)
        self._install_poll_timer.timeout.connect(self._poll_for_jamulus_install)

        if not detected_path:
            from services.bridge_service import _bundled_jamulus_installer
            installer = _bundled_jamulus_installer()
            if installer:
                self._bundled_installer_path = installer
                self._install_jamulus_btn.setVisible(True)

        layout.addStretch(1)

        self.registerField("jamulus_server*", self._host)
        self.registerField("jamulus_port",    self._port, "value")
        self.registerField("jamulus_rpc_port", self._rpc_port, "value")

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
        try:
            subprocess.Popen([self._bundled_installer_path], shell=False)
        except Exception as exc:
            LOGGER.warning("Failed to launch bundled Jamulus installer: %s", exc)
            return
        self._install_jamulus_btn.setEnabled(False)
        self._install_jamulus_btn.setText("Waiting for install to finish\u2026")
        self._install_poll_timer.start()

    def _poll_for_jamulus_install(self) -> None:
        """QTimer tick: fill in the path field as soon as install lands."""
        for candidate in self._settings.jamulus_candidates:
            path = Path(candidate).expanduser()
            if path.is_file():
                self._install_poll_timer.stop()
                self._jamulus_path.setText(str(path))
                self._install_jamulus_btn.setVisible(False)
                return

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

    def _resolved_jamulus_path(self) -> str:
        explicit = self._jamulus_path.text().strip()
        if explicit:
            path = Path(explicit).expanduser()
            return str(path) if path.exists() else ""
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

    def validatePage(self) -> bool:
        host = self._host.text().strip()
        if not host:
            self._host.setFocus()
            return False
        if not self._resolved_jamulus_path():
            self._jamulus_path.setFocus()
            self._jamulus_path.selectAll()
            return False
        return True

    @property
    def host(self) -> str:
        return self._host.text().strip()

    @property
    def port(self) -> int:
        return self._port.value()

    @property
    def rpc_port(self) -> int:
        return self._rpc_port.value()

    @property
    def jamulus_path(self) -> str:
        return self._resolved_jamulus_path() or self._jamulus_path.text().strip()


# ---------------------------------------------------------------------------
# Page 2 — Webex
# ---------------------------------------------------------------------------
class _WebexPage(QWizardPage):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.setTitle("Webex Meeting")
        self.setSubTitle(
            "Enter the Webex meeting URL your band uses for video calls."
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(Space.MD)

        layout.addWidget(_section_label("Meeting URL"))
        self._url = QLineEdit(settings.webex_url)
        self._url.setPlaceholderText("https://myorg.webex.com/meet/bandroom")
        self._url.setAccessibleName("Webex meeting URL")
        self._url.setToolTip(
            "Enter your Webex meeting URL. If you forget the https:// prefix, "
            "we'll add it for you.\n\n"
            "Example: https://myorg.webex.com/meet/bandroom"
        )
        layout.addWidget(self._url)

        # Live-validation hint — hidden unless the user types something dodgy.
        self._url_hint = _body_label("")
        self._url_hint.setTextFormat(Qt.TextFormat.PlainText)
        self._url_hint.setVisible(False)
        layout.addWidget(self._url_hint)
        self._url.textChanged.connect(self._validate_url_live)

        # Optional: guest issuer
        self._guest_group = QGroupBox("Guest Issuer (optional — developer.webex.com)")
        self._guest_group.setCheckable(True)
        self._guest_group.setChecked(bool(settings.webex_guest_issuer_id))
        guest_layout = QVBoxLayout(self._guest_group)
        guest_layout.setSpacing(Space.SM)

        guest_layout.addWidget(_body_label(
            "When configured, WebJam generates a guest token so musicians join "
            "the embedded Webex meeting without a Cisco account."
        ))

        guest_layout.addWidget(QLabel("Guest Issuer ID"))
        self._issuer_id = QLineEdit(settings.webex_guest_issuer_id)
        self._issuer_id.setPlaceholderText("Y2lzY29zcGFyazovL3Vz…")
        self._issuer_id.setEchoMode(QLineEdit.EchoMode.Normal)
        self._issuer_id.setAccessibleName("Webex Guest Issuer ID")
        guest_layout.addWidget(self._issuer_id)

        guest_layout.addWidget(QLabel("Webex Secret Key"))
        self._secret = QLineEdit(settings.webex_guest_issuer_secret)
        self._secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._secret.setPlaceholderText("base-64 encoded secret")
        self._secret.setAccessibleName("Webex Guest Issuer Secret")
        guest_layout.addWidget(self._secret)

        guest_layout.addWidget(QLabel("Your display name in Webex"))
        self._display_name = QLineEdit(settings.webex_display_name or "WebJam Guest")
        self._display_name.setAccessibleName("Display name shown to other participants")
        guest_layout.addWidget(self._display_name)

        layout.addWidget(self._guest_group)
        layout.addStretch(1)

        self.registerField("webex_url*", self._url)

    @Slot(str)
    def _validate_url_live(self, text: str) -> None:
        """Show a typing-time hint about obvious URL-input mistakes."""
        stripped = text.strip()
        if not stripped:
            self._url_hint.setVisible(False)
            return
        if " " in stripped or ".." in stripped:
            self._url_hint.setText("URL shouldn't contain spaces or '..'")
            self._url_hint.setVisible(True)
            return
        if "://" in stripped:
            error = webex_url_error(stripped)
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
        url = normalize_webex_url(self._url.text())
        if url != self._url.text().strip():
            self._url.setText(url)
        if webex_url_error(url):
            self._url.setFocus()
            self._url.selectAll()
            return False
        return True

    @property
    def webex_url(self) -> str:
        return normalize_webex_url(self._url.text())

    @property
    def guest_issuer_id(self) -> str:
        if not self._guest_group.isChecked():
            return ""
        return self._issuer_id.text().strip()

    @property
    def guest_issuer_secret(self) -> str:
        if not self._guest_group.isChecked():
            return ""
        return self._secret.text().strip()

    @property
    def display_name(self) -> str:
        return self._display_name.text().strip() or "WebJam Guest"


# ---------------------------------------------------------------------------
# Page 3 — Audio routing
# ---------------------------------------------------------------------------
class _RoutingPage(QWizardPage):
    # Emitted from background scan thread; Qt auto-queues it to the UI thread
    _scan_complete = Signal()

    def __init__(self, settings: Optional[AppSettings] = None) -> None:
        super().__init__()
        self._scan_complete.connect(self._apply_routing)
        self.setTitle("Audio Routing")
        self.setSubTitle(
            "This connects your band's audio into the video call, "
            "so Webex participants can hear everyone play."
        )
        self._complete = False
        self._saved_device_index = settings.audio_input_device_index if settings else -1
        self._bridge_enabled = bool(
            settings.webex_audio_bridge_enabled if settings else False
        )
        self._status_label = _body_label("Checking your audio setup…")

        self._bridge_chk = QCheckBox(
            "This Mac sends the Jamulus mix into Webex"
        )
        self._bridge_chk.setChecked(self._bridge_enabled)
        self._bridge_chk.setAccessibleName("Use this Mac as the Webex audio bridge")
        self._bridge_chk.stateChanged.connect(self._on_bridge_changed)

        install_btn = QPushButton("Show me how to set this up")
        install_btn.setObjectName("GhostButton")
        install_btn.clicked.connect(self._open_install_url)
        install_btn.setVisible(False)
        self._install_btn = install_btn

        # Device picker — always present, populated after scan completes.
        # Defaults to "System default" so the wizard remains usable when
        # sounddevice is missing or no devices are detected.
        self._device_label = _section_label("Audio input device")
        self._device_picker = QComboBox()
        self._device_picker.setAccessibleName("Audio input device")
        self._device_picker.addItem("System default", -1)

        skip_chk = QCheckBox("Skip for now — I'll set this up later")
        skip_chk.stateChanged.connect(self._on_skip_changed)
        self._skip_chk = skip_chk

        layout = QVBoxLayout(self)
        layout.setSpacing(Space.MD)
        layout.addWidget(self._bridge_chk)
        layout.addWidget(self._status_label)
        layout.addWidget(install_btn)
        layout.addWidget(self._device_label)
        layout.addWidget(self._device_picker)
        layout.addStretch(1)
        layout.addWidget(skip_chk)

    def initializePage(self) -> None:
        if getattr(self, '_scan_done', False):
            return  # Already scanned

        from core.audio_routing import scan_loopback_devices
        import threading

        def _scan():
            status = scan_loopback_devices()
            self._routing_status = status
            # Signal emission is thread-safe; Qt will queue it to the UI thread
            self._scan_complete.emit()

        self._routing_status = None
        self._scan_thread = threading.Thread(target=_scan, daemon=True)
        self._scan_thread.start()

    @Slot()
    def _apply_routing(self) -> None:  # noqa: N802 (called via invokeMethod)
        if not self.isVisible():
            return
        status = getattr(self, "_routing_status", None)
        if status is None:
            return
        if status.ok:
            self._status_label.setText(
                f"\u2705  Virtual audio device detected:\n\n"
                f"    {status.device_name}\n\n"
                "WebJam can use this for metering. Configure your operating "
                "system and Webex routing before the session."
            )
            self._install_btn.setVisible(False)
            self._complete = True
        else:
            if self._bridge_chk.isChecked():
                self._status_label.setText(
                    "\u26a0\ufe0f  No Webex bridge device found.\n\n"
                    f"{status.install_hint}.\n\n"
                    "After installing, restart WebJam to activate audio routing."
                )
                self._install_btn.setVisible(True)
                self._complete = False
            else:
                self._status_label.setText(
                    "\u2705  Jamulus-only musician mode.\n\n"
                    "A virtual device is not required because this Mac will "
                    "keep Webex microphone and speaker muted while playing."
                )
                self._install_btn.setVisible(False)
                self._complete = True
            self._install_url = status.install_url
        self._populate_device_picker()
        self._scan_done = True
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

    def _open_install_url(self) -> None:
        url = getattr(self, "_install_url", "https://existential.audio/blackhole/")
        QDesktopServices.openUrl(QUrl(url))

    def _on_skip_changed(self, state: int) -> None:
        self._complete = bool(state)
        self.completeChanged.emit()

    def _on_bridge_changed(self, state: int) -> None:
        self._bridge_enabled = bool(state)
        status = getattr(self, "_routing_status", None)
        if status is not None:
            self._apply_routing()

    def isComplete(self) -> bool:
        return self._complete

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
    def bridge_enabled(self) -> bool:
        return self._bridge_chk.isChecked()


# ---------------------------------------------------------------------------
# Page 4 — Done
# ---------------------------------------------------------------------------
class _DonePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Configuration saved")
        self.setSubTitle("Run Ready Check before your first jam.")

        layout = QVBoxLayout(self)
        layout.addWidget(_body_label(
            "Click Finish to launch the Conductor.\n\n"
            "Quick-start:\n"
            "  1.  Open Checks → Ready Check (F2)\n"
            "  2.  Click \u201cStart Audio\u201d — WebJam will connect to your Jamulus server\n"
            "  3.  Click \u201cJoin Video\u201d to open your Webex meeting\n"
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

        self.accepted.connect(self._save_settings)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _save_settings(self) -> None:
        cfg = asdict(self._settings)

        cfg["jamulus_server"]             = self._jamulus.host
        cfg["jamulus_port"]               = self._jamulus.port
        cfg["jamulus_rpc_port"]           = self._jamulus.rpc_port
        cfg["webex_url"]                  = self._webex.webex_url
        cfg["webex_guest_issuer_id"]      = self._webex.guest_issuer_id
        cfg["webex_guest_issuer_secret"]  = self._webex.guest_issuer_secret
        cfg["webex_display_name"]         = self._webex.display_name
        cfg["audio_input_device_index"]   = self._routing.device_index
        cfg["webex_audio_bridge_enabled"] = self._routing.bridge_enabled

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
            # Mode 0o600 because the config can contain webex_guest_issuer_secret.
            atomic_write_text(path, json.dumps(cfg, indent=2), mode=0o600)
            LOGGER.info("Settings saved to %s", path)
        except Exception as exc:
            LOGGER.error("Failed to save settings: %s", exc)

    @staticmethod
    def should_show_on_startup(settings: AppSettings) -> bool:
        """Return True when the config file doesn't exist yet (first run)."""
        return not Path(settings.config_file).exists()
