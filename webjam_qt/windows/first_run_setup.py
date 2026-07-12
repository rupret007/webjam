"""Compact, role-driven first-run setup for WebJam."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCommandLinkButton,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.jamulus_endpoint import (
    DEFAULT_JAMULUS_PORT,
    JamulusEndpointError,
    parse_jamulus_endpoint,
)
from core.settings import (
    AppSettings,
    hosted_server_recordings_dir,
    hosted_server_secret_path,
)
from core.webex_url import normalize_webex_url, webex_url_error
from webjam_qt.theme import Space

LOGGER = logging.getLogger("webjam.qt.first_run")


@dataclass(frozen=True)
class FirstRunSetupResult:
    role: str
    musician_name: str
    jamulus_host: str
    jamulus_port: int
    webex_url: str
    local_capture_enabled: bool
    audio_input_device_index: int


def _body(text: str, *, name: str = "FirstRunBody") -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


def _find_client(settings: AppSettings) -> Optional[str]:
    for candidate in settings.jamulus_candidates:
        try:
            if Path(candidate).expanduser().is_file():
                return str(Path(candidate).expanduser())
        except OSError:
            continue
    from services.bridge_service import _bundled_jamulus_candidate
    return _bundled_jamulus_candidate()


def _find_server() -> tuple[Optional[str], str]:
    installed = Path(
        "/Applications/JamulusServer.app/Contents/MacOS/JamulusServer"
    )
    if installed.is_file():
        return str(installed), "installed"
    from services.bridge_service import _bundled_jamulus_server_candidate
    bundled = _bundled_jamulus_server_candidate()
    return (bundled, "bundled") if bundled else (None, "missing")


class FirstRunSetupDialog(QDialog):
    """Two-step setup containing only decisions required before Ready Check."""

    stepChanged = Signal(int)

    def __init__(
        self,
        settings: Optional[AppSettings] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or AppSettings()
        self._step = 0
        self._result: Optional[FirstRunSetupResult] = None
        self._client_path = _find_client(self._settings)
        self._server_path, self._server_source = _find_server()
        self._installer_path = self._bundled_windows_installer()

        self.setObjectName("FirstRunSetupDialog")
        self.setWindowTitle("Set up WebJam")
        self.setModal(True)
        self.setMinimumSize(560, 520)
        self.resize(680, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.XL, Space.XL, Space.LG)
        root.setSpacing(Space.LG)

        heading_row = QHBoxLayout()
        heading_text = QVBoxLayout()
        heading_text.setSpacing(Space.XS)
        self._title = QLabel("Choose your setup")
        self._title.setObjectName("FirstRunTitle")
        self._subtitle = QLabel("Tell WebJam what this Mac will do.")
        self._subtitle.setObjectName("FirstRunSubtitle")
        heading_text.addWidget(self._title)
        heading_text.addWidget(self._subtitle)
        heading_row.addLayout(heading_text, 1)
        self._progress = QLabel("1 of 2")
        self._progress.setObjectName("FirstRunProgress")
        self._progress.setAccessibleName("Setup step 1 of 2")
        heading_row.addWidget(self._progress, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(heading_row)

        self._pages = QStackedWidget()
        self._pages.setObjectName("FirstRunPages")
        self._pages.addWidget(self._build_role_page())
        self._pages.addWidget(self._build_webex_page())
        root.addWidget(self._pages, 1)

        footer = QHBoxLayout()
        footer.setSpacing(Space.SM)
        self._cancel = QPushButton("Cancel")
        self._cancel.setObjectName("GhostButton")
        self._cancel.clicked.connect(self.reject)
        self._back = QPushButton("Back")
        self._back.setObjectName("GhostButton")
        self._back.clicked.connect(self._show_previous_step)
        self._primary = QPushButton("Continue")
        self._primary.setObjectName("PrimaryButton")
        self._primary.clicked.connect(self._advance_or_finish)
        self._primary.setDefault(True)
        footer.addWidget(self._cancel)
        footer.addStretch(1)
        footer.addWidget(self._back)
        footer.addWidget(self._primary)
        root.addLayout(footer)

        self._back.hide()
        self._refresh_role_details()
        self._refresh_navigation()
        self.setTabOrder(self._host_card, self._join_card)
        self.setTabOrder(self._join_card, self._name)
        self.setTabOrder(self._name, self._server_address)

    @staticmethod
    def should_show_on_startup(settings: AppSettings) -> bool:
        return not Path(settings.config_file).exists()

    @staticmethod
    def _bundled_windows_installer() -> Optional[str]:
        from services.bridge_service import _bundled_jamulus_installer
        return _bundled_jamulus_installer()

    def _build_role_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("FirstRunPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.MD)

        self._role_group = QButtonGroup(self)
        self._role_group.setExclusive(True)
        self._host_card = QCommandLinkButton(
            "Host the band",
            "Run the included band server on this Mac.",
        )
        self._join_card = QCommandLinkButton(
            "Join a band",
            "Connect to the address your host sends you.",
        )
        for card, accessible in (
            (self._host_card, "Host the band on this Mac"),
            (self._join_card, "Join a band hosted elsewhere"),
        ):
            card.setObjectName("FirstRunRoleCard")
            card.setCheckable(True)
            card.setAccessibleName(accessible)
            card.setMinimumHeight(68)
            self._role_group.addButton(card)
            layout.addWidget(card)
        self._host_card.setAccessibleDescription(
            "Use the bundled Jamulus server. Other musicians connect to this Mac."
        )
        self._join_card.setAccessibleDescription(
            "Connect to another musician's or a public Jamulus server."
        )
        self._role_group.buttonToggled.connect(self._on_role_changed)

        if sys.platform != "darwin":
            self._host_card.setEnabled(False)
            self._host_card.setDescription("Hosting is currently available on macOS.")
        elif not self._server_path:
            self._host_card.setDescription(
                "Band server unavailable — reinstall the downloadable WebJam build."
            )

        self._component_status = _body("", name="FirstRunStatus")
        self._component_status.setAccessibleName("Bundled component status")
        layout.addWidget(self._component_status)

        name_label = QLabel("Musician name")
        name_label.setObjectName("FirstRunFieldLabel")
        self._name = QLineEdit()
        configured_name = (self._settings.musician_name or "").strip()
        if configured_name != "WebJam Musician":
            self._name.setText(configured_name)
        self._name.setPlaceholderText("Jeff — Guitar")
        self._name.setAccessibleName("Your musician name")
        self._name.textChanged.connect(self._refresh_navigation)
        layout.addWidget(name_label)
        layout.addWidget(self._name)

        self._server_label = QLabel("Band server address")
        self._server_label.setObjectName("FirstRunFieldLabel")
        self._server_address = QLineEdit()
        self._server_address.setPlaceholderText("100.64.0.2 or band.example.com:22124")
        self._server_address.setAccessibleName("Band server address")
        self._server_address.setText(self._join_endpoint_from_settings())
        self._server_address.textChanged.connect(self._on_server_text_changed)
        self._server_error = _body("", name="FirstRunError")
        self._server_error.setAccessibleName("Band server address error")
        layout.addWidget(self._server_label)
        layout.addWidget(self._server_address)
        layout.addWidget(self._server_error)

        self._install_client = QPushButton("Install Jamulus")
        self._install_client.setObjectName("GhostButton")
        self._install_client.clicked.connect(self._launch_windows_installer)
        layout.addWidget(self._install_client)
        layout.addStretch(1)
        return page

    def _build_webex_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("FirstRunPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.MD)

        url_label = QLabel("Webex meeting URL")
        url_label.setObjectName("FirstRunFieldLabel")
        self._webex_url = QLineEdit(self._settings.webex_url)
        self._webex_url.setPlaceholderText("https://company.webex.com/meet/bandroom")
        self._webex_url.setAccessibleName("Webex meeting URL")
        self._webex_url.textChanged.connect(self._on_webex_text_changed)
        self._webex_error = _body("", name="FirstRunError")
        self._webex_error.setAccessibleName("Webex meeting URL error")
        layout.addWidget(url_label)
        layout.addWidget(self._webex_url)
        layout.addWidget(self._webex_error)

        signal_flow = QLabel("MUSIC  ·  JAMULUS      TALK  ·  WEBEX")
        signal_flow.setObjectName("FirstRunSignalFlow")
        signal_flow.setAccessibleName(
            "Jamulus carries music and Webex carries conversation"
        )
        layout.addWidget(signal_flow)

        self._capture = QCheckBox("Record an isolated input on this Mac")
        self._capture.setAccessibleName("Record an isolated local input")
        self._capture.setChecked(False)
        self._capture.toggled.connect(self._on_capture_toggled)
        layout.addWidget(self._capture)

        self._device_label = QLabel("Recording input")
        self._device_label.setObjectName("FirstRunFieldLabel")
        self._device = QComboBox()
        self._device.setAccessibleName("Local recording input device")
        self._device.currentIndexChanged.connect(self._refresh_navigation)
        self._capture_error = _body("", name="FirstRunError")
        self._capture_error.setAccessibleName("Recording input error")
        layout.addWidget(self._device_label)
        layout.addWidget(self._device)
        layout.addWidget(self._capture_error)

        next_note = _body(
            "Ready Check opens next to verify your audio device, Webex settings, "
            "and band server before sound starts.",
            name="FirstRunStatus",
        )
        layout.addWidget(next_note)
        layout.addStretch(1)
        self._on_capture_toggled(False)
        return page

    def _join_endpoint_from_settings(self) -> str:
        host = (self._settings.jamulus_server or "").strip()
        if not host or host == "127.0.0.1":
            return ""
        if self._settings.jamulus_port == DEFAULT_JAMULUS_PORT:
            return host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{host}:{self._settings.jamulus_port}"

    def _selected_role(self) -> Optional[str]:
        if self._host_card.isChecked():
            return "host"
        if self._join_card.isChecked():
            return "join"
        return None

    def _on_role_changed(self, _button, checked: bool) -> None:
        if not checked:
            return
        self._refresh_role_details()
        self._refresh_navigation()

    def _refresh_role_details(self) -> None:
        joining = self._selected_role() == "join"
        for widget in (self._server_label, self._server_address, self._server_error):
            widget.setVisible(joining)
        if self._selected_role() == "host":
            if self._server_path and self._client_path:
                self._component_status.setText(
                    "✓ Jamulus music engine + band server included — nothing to install"
                )
            else:
                self._component_status.setText(
                    "Band components are missing. Reinstall the downloadable WebJam build."
                )
        elif joining:
            self._component_status.setText(
                "✓ Jamulus music engine included — enter the address from your host"
                if self._client_path else "Jamulus must be installed before continuing."
            )
        else:
            self._component_status.setText("Choose one role to continue.")
        self._install_client.setVisible(
            joining and not self._client_path and bool(self._installer_path)
        )

    def _on_server_text_changed(self, _text: str) -> None:
        self._server_error.clear()
        self._refresh_navigation()

    def _on_webex_text_changed(self, _text: str) -> None:
        self._webex_error.clear()
        self._refresh_navigation()

    def _on_capture_toggled(self, checked: bool) -> None:
        if checked and not self._device.count():
            self._populate_devices()
        self._device_label.setVisible(checked)
        self._device.setVisible(checked)
        self._capture_error.setVisible(checked)
        self._refresh_navigation()

    def _populate_devices(self) -> None:
        from core.audio_routing import list_input_devices
        devices = list_input_devices()
        self._device.clear()
        if not devices:
            self._device.addItem("No input devices found", None)
            self._device.setEnabled(False)
            self._capture_error.setText(
                "Connect an audio input, then reopen setup or leave recording off."
            )
            return
        self._device.setEnabled(True)
        self._device.addItem("System default", -1)
        for device in devices:
            self._device.addItem(
                f"{device['name']} ({device['channels']} ch)", device["index"]
            )
        saved = self._settings.audio_input_device_index
        for index in range(self._device.count()):
            if self._device.itemData(index) == saved:
                self._device.setCurrentIndex(index)
                break
        self._capture_error.clear()

    def _role_page_valid(self, *, show_error: bool = False) -> bool:
        role = self._selected_role()
        if role is None or not self._name.text().strip() or not self._client_path:
            return False
        if role == "host":
            return bool(sys.platform == "darwin" and self._server_path)
        try:
            parse_jamulus_endpoint(self._server_address.text())
        except JamulusEndpointError as exc:
            if show_error and self._server_address.text().strip():
                self._server_error.setText(str(exc))
            return False
        return True

    def _webex_page_valid(self, *, show_error: bool = False) -> bool:
        url = normalize_webex_url(self._webex_url.text())
        error = webex_url_error(url)
        capture_valid = not self._capture.isChecked() or (
            self._device.isEnabled() and self._device.currentData() is not None
        )
        if show_error and error:
            self._webex_error.setText(error)
        return error is None and capture_valid

    def _refresh_navigation(self, *_args) -> None:
        if not hasattr(self, "_primary"):
            return
        valid = (
            self._role_page_valid() if self._step == 0 else self._webex_page_valid()
        )
        self._primary.setEnabled(valid)

    def _show_previous_step(self) -> None:
        self._show_step(0)

    def _show_step(self, step: int) -> None:
        self._step = step
        self._pages.setCurrentIndex(step)
        second = step == 1
        self._back.setVisible(second)
        self._title.setText("Webex and recording" if second else "Choose your setup")
        self._subtitle.setText(
            "Add your talk room and optional local stem."
            if second else "Tell WebJam what this Mac will do."
        )
        self._progress.setText(f"{step + 1} of 2")
        self._progress.setAccessibleName(f"Setup step {step + 1} of 2")
        self._primary.setText("Finish Setup" if second else "Continue")
        if second:
            self.setTabOrder(self._webex_url, self._capture)
            self.setTabOrder(self._capture, self._device)
            self._webex_url.setFocus()
        else:
            self._name.setFocus()
        self._refresh_navigation()
        self.stepChanged.emit(step)

    def _advance_or_finish(self) -> None:
        if self._step == 0:
            if self._role_page_valid(show_error=True):
                self._show_step(1)
            return
        if not self._webex_page_valid(show_error=True):
            return
        result = self._build_result()
        if self._save(result):
            self._result = result
            self.accept()

    def _build_result(self) -> FirstRunSetupResult:
        role = self._selected_role()
        if role == "host":
            host, port = "127.0.0.1", DEFAULT_JAMULUS_PORT
        else:
            endpoint = parse_jamulus_endpoint(self._server_address.text())
            host, port = endpoint.host, endpoint.port
        device = self._device.currentData() if self._capture.isChecked() else -1
        return FirstRunSetupResult(
            role=role or "join",
            musician_name=self._name.text().strip(),
            jamulus_host=host,
            jamulus_port=port,
            webex_url=normalize_webex_url(self._webex_url.text()),
            local_capture_enabled=self._capture.isChecked(),
            audio_input_device_index=int(device if device is not None else -1),
        )

    def _save(self, result: FirstRunSetupResult) -> bool:
        cfg = asdict(self._settings)
        cfg.update({
            "jamulus_server": result.jamulus_host,
            "jamulus_port": result.jamulus_port,
            "jamulus_rpc_port": 22222,
            "server_rpc_port": 22240,
            "musician_name": result.musician_name,
            "host_server_enabled": result.role == "host",
            "webex_url": result.webex_url,
            "webex_audio_mode": "talkback",
            "local_capture_enabled": result.local_capture_enabled,
            "audio_input_device_index": result.audio_input_device_index,
        })
        if result.role == "host":
            cfg["server_rpc_secret_file"] = str(hosted_server_secret_path())
            cfg["takes_directory"] = str(hosted_server_recordings_dir())
        elif result.local_capture_enabled and not cfg.get("takes_directory"):
            cfg["takes_directory"] = str(Path.home() / "Music" / "WebJam Recordings")
        if self._client_path:
            existing = list(cfg.get("jamulus_candidates") or [])
            cfg["jamulus_candidates"] = [self._client_path] + [
                value for value in existing if value != self._client_path
            ]
        for key in (
            "webex_guest_issuer_id", "webex_guest_issuer_secret",
            "webex_display_name", "webex_audio_bridge_enabled",
        ):
            cfg.pop(key, None)
        try:
            from core.file_io import atomic_write_text
            atomic_write_text(
                Path(self._settings.config_file),
                json.dumps(cfg, indent=2),
                mode=0o600,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("First-run settings save failed: %s", exc)
            self._webex_error.setText(
                "WebJam couldn't save setup. Check folder permissions and try again."
            )
            return False

    def _launch_windows_installer(self) -> None:
        if not self._installer_path:
            return
        try:
            subprocess.Popen([self._installer_path], shell=False)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to launch Jamulus installer: %s", exc)
            self._component_status.setText(
                "Jamulus installer could not open. Reinstall WebJam and try again."
            )
            return
        self._component_status.setText(
            "Finish the Jamulus installer, then restart WebJam."
        )
        self._install_client.setEnabled(False)

    @property
    def result(self) -> Optional[FirstRunSetupResult]:
        return self._result

