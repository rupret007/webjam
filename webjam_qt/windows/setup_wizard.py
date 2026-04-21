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
from dataclasses import asdict
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGroupBox,
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
from webjam_qt.theme.tokens import Color, Font, Space

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
        layout.addStretch(1)


# ---------------------------------------------------------------------------
# Page 1 — Jamulus
# ---------------------------------------------------------------------------
class _JamulusPage(QWizardPage):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.setTitle("Jamulus Server")
        self.setSubTitle(
            "Enter your band's Jamulus server details. Ask your band admin if you're not sure."
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(Space.MD)

        layout.addWidget(_section_label("Server host"))
        self._host = QLineEdit(settings.jamulus_server)
        self._host.setPlaceholderText("e.g. 172.24.194.9 or myband.example.com")
        self._host.setAccessibleName("Jamulus server hostname or IP address")
        layout.addWidget(self._host)

        layout.addWidget(_section_label("Port"))
        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(settings.jamulus_port)
        self._port.setAccessibleName("Jamulus server port number")
        layout.addWidget(self._port)

        layout.addWidget(_section_label("Server control port"))
        self._rpc_port = QSpinBox()
        self._rpc_port.setRange(1, 65535)
        self._rpc_port.setValue(settings.jamulus_rpc_port)
        self._rpc_port.setAccessibleName("Jamulus JSON-RPC port (--jsonrpcport)")
        layout.addWidget(self._rpc_port)

        layout.addWidget(_body_label(
            "Leave this as 22222 unless your band admin says otherwise. "
            "This lets WebJam read participant names and control the mixer."
        ))
        layout.addStretch(1)

        self.registerField("jamulus_server*", self._host)
        self.registerField("jamulus_port",    self._port, "value")
        self.registerField("jamulus_rpc_port", self._rpc_port, "value")

    def validatePage(self) -> bool:
        host = self._host.text().strip()
        if not host:
            self._host.setFocus()
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
        layout.addWidget(self._url)

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

    def validatePage(self) -> bool:
        url = self._url.text().strip()
        parsed = urlparse(url)
        if not parsed.scheme in ("http", "https") or not parsed.netloc:
            self._url.setFocus()
            return False
        return True

    @property
    def webex_url(self) -> str:
        return self._url.text().strip()

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
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Audio Routing")
        self.setSubTitle(
            "This connects your band's audio into the video call, "
            "so Webex participants can hear everyone play."
        )
        self._complete = False
        self._status_label = _body_label("Checking your audio setup…")

        install_btn = QPushButton("Show me how to set this up")
        install_btn.setObjectName("GhostButton")
        install_btn.clicked.connect(self._open_install_url)
        install_btn.setVisible(False)
        self._install_btn = install_btn

        skip_chk = QCheckBox("Skip for now — I'll set this up later")
        skip_chk.stateChanged.connect(self._on_skip_changed)
        self._skip_chk = skip_chk

        layout = QVBoxLayout(self)
        layout.setSpacing(Space.MD)
        layout.addWidget(self._status_label)
        layout.addWidget(install_btn)
        layout.addStretch(1)
        layout.addWidget(skip_chk)

    def initializePage(self) -> None:
        if getattr(self, '_scan_done', False):
            return  # Already scanned

        from core.audio_routing import scan_loopback_devices
        import threading

        def _scan():
            status = scan_loopback_devices()
            # Must update UI on main thread — use QMetaObject
            from PySide6.QtCore import QMetaObject, Qt as _Qt
            QMetaObject.invokeMethod(
                self, "_apply_routing", _Qt.ConnectionType.QueuedConnection,
            )
            self._routing_status = status

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
                "WebJam will use this for audio automatically."
            )
            self._install_btn.setVisible(False)
            self._complete = True
        else:
            self._status_label.setText(
                "\u26a0\ufe0f  No audio routing device found.\n\n"
                f"{status.install_hint}.\n\n"
                "After installing, restart WebJam to activate audio routing.\n"
                "You can skip this step and configure it later."
            )
            self._install_btn.setVisible(True)
            self._install_url = status.install_url
            self._complete = False
        self._scan_done = True
        self.completeChanged.emit()

    def _open_install_url(self) -> None:
        url = getattr(self, "_install_url", "https://existential.audio/blackhole/")
        QDesktopServices.openUrl(QUrl(url))

    def _on_skip_changed(self, state: int) -> None:
        self._complete = bool(state)
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._complete


# ---------------------------------------------------------------------------
# Page 4 — Done
# ---------------------------------------------------------------------------
class _DonePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("You\u2019re all set!")
        self.setSubTitle("WebJam is configured and ready for your session.")

        layout = QVBoxLayout(self)
        layout.addWidget(_body_label(
            "Click Finish to launch the Conductor.\n\n"
            "Quick-start:\n"
            "  1.  Click \u201cLaunch Audio\u201d to start Jamulus\n"
            "  2.  Click \u201cJoin Video\u201d to open Webex\n"
            "  3.  Adjust faders as musicians join\n\n"
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
    ) -> None:
        super().__init__(parent)
        self._settings = settings or load_settings()

        self.setWindowTitle("WebJam Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setMinimumSize(560, 520)
        self.setOption(QWizard.WizardOption.NoBackButtonOnLastPage, True)

        # Pages
        self._welcome = _WelcomePage()
        self._jamulus = _JamulusPage(self._settings)
        self._webex   = _WebexPage(self._settings)
        self._routing = _RoutingPage()
        self._done    = _DonePage()

        self.setPage(_PageId.WELCOME, self._welcome)
        self.setPage(_PageId.JAMULUS, self._jamulus)
        self.setPage(_PageId.WEBEX,   self._webex)
        self.setPage(_PageId.ROUTING, self._routing)
        self.setPage(_PageId.DONE,    self._done)

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

        path = Path(self._settings.config_file)
        try:
            path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            LOGGER.info("Settings saved to %s", path)
        except Exception as exc:
            LOGGER.error("Failed to save settings: %s", exc)

    @staticmethod
    def should_show_on_startup(settings: AppSettings) -> bool:
        """Return True when the config file doesn't exist yet (first run)."""
        return not Path(settings.config_file).exists()
