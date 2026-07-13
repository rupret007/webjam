"""The entire startup experience: Host a Jam or Join a Jam.

This is intentionally not a setup wizard.  A role selection applies safe
defaults and starts the session; the join branch asks for exactly one value,
the invitation link.
"""

from __future__ import annotations

import getpass
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.jamulus_endpoint import DEFAULT_JAMULUS_PORT
from core.network_invite import BandInvite, InviteLinkError, invite_from_text
from core.settings import (
    AppSettings,
    hosted_server_recordings_dir,
    hosted_server_secret_path,
    save_settings,
)
from webjam_qt.theme.brand import BrandMark
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.jam_signal_graphic import JamSignalGraphic


LOGGER = logging.getLogger("webjam.qt.launch")


def default_musician_name(settings: AppSettings) -> str:
    """Return a useful identity without turning launch into a form."""
    configured = str(settings.musician_name or "").strip()
    if configured and configured != "WebJam Musician":
        return configured
    if os.name == "posix":
        try:
            import pwd

            full_name = pwd.getpwuid(os.getuid()).pw_gecos.split(",", 1)[0].strip()
            if full_name:
                return full_name[:60]
        except (ImportError, KeyError, OSError):
            pass
    account = getpass.getuser().replace("_", " ").replace(".", " ").strip()
    return account.title()[:60] if account else "Musician"


def apply_host_defaults(settings: AppSettings) -> None:
    """Derive every host-side implementation detail from one role choice."""
    settings.musician_name = default_musician_name(settings)
    settings.host_server_enabled = True
    settings.jamulus_server = "127.0.0.1"
    settings.jamulus_port = DEFAULT_JAMULUS_PORT
    settings.jamulus_rpc_port = 22222
    settings.server_rpc_port = 22240
    settings.server_rpc_secret_file = str(hosted_server_secret_path())
    settings.takes_directory = str(hosted_server_recordings_dir())
    settings.webex_audio_mode = "talkback"
    # Recording Setup is an explicit host preference, not connection plumbing.
    # Keep it across launches so reopening WebJam cannot silently drop the two
    # isolated interface stems the musician asked us to capture.


def apply_join_invite(settings: AppSettings, invite: BandInvite) -> None:
    """Apply an invitation without exposing its connection components."""
    settings.musician_name = default_musician_name(settings)
    settings.host_server_enabled = False
    settings.jamulus_server = invite.host
    settings.jamulus_port = invite.port
    settings.jamulus_rpc_port = 22222
    settings.server_rpc_secret_file = ""
    if not settings.takes_directory:
        settings.takes_directory = str(Path.home() / "Music" / "WebJam Takes")
    settings.webex_audio_mode = "talkback"
    # Isolated local recording is an explicit musician preference. Joining a
    # band must never silently disable a choice already made in Recording
    # Setup; the host's authenticated recording signal controls when it runs.


class LaunchDialog(QDialog):
    """Two choices on launch; one pasted link after choosing Join."""

    def __init__(
        self,
        settings: AppSettings,
        parent: Optional[QWidget] = None,
        *,
        initial_invite_url: str = "",
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._submitting = False
        self._host_available = sys.platform == "darwin"
        self.selected_role = ""
        self.session_name = "Band Rehearsal"
        self.band_invite: BandInvite | None = None
        self.setObjectName("LaunchDialog")
        self.setWindowTitle("WebJam")
        self.setModal(True)
        self.setMinimumSize(460, 600)
        self.resize(620, 660)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XXL, Space.XL, Space.XXL, Space.XL)
        root.setSpacing(Space.MD)

        self._logo = BrandMark(48)
        self._logo.setObjectName("LaunchBrandMark")
        root.addWidget(self._logo, 0, Qt.AlignmentFlag.AlignHCenter)

        self._pages = QStackedWidget()
        self._choice_page = self._build_choice_page()
        self._join_page = self._build_join_page()
        self._pages.addWidget(self._choice_page)
        self._pages.addWidget(self._join_page)
        root.addWidget(self._pages, 1)

        if initial_invite_url:
            QTimer.singleShot(
                0, lambda value=initial_invite_url: self.accept_invite(value)
            )

    def _build_choice_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(Space.MD)

        graphic = JamSignalGraphic()
        layout.addWidget(graphic)

        title = QLabel("Play together.")
        title.setObjectName("LaunchTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle = QLabel("Start a private jam or join your band.")
        subtitle.setObjectName("LaunchSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch(1)

        self._host_button = QPushButton("Host a Jam")
        self._host_button.setObjectName("LaunchPrimary")
        self._host_button.setMinimumHeight(52)
        self._host_button.setDefault(True)
        self._join_button = QPushButton("Join a Jam")
        self._join_button.setObjectName("LaunchSecondary")
        self._join_button.setMinimumHeight(52)
        self._host_button.setAccessibleName("Host a Jam")
        self._host_button.setAccessibleDescription(
            "Start a band session on this Mac and create an invitation link."
        )
        self._join_button.setAccessibleName("Join a Jam")
        self._join_button.setAccessibleDescription(
            "Join a band session using one WebJam invitation link."
        )
        if not self._host_available:
            self._host_button.setEnabled(False)
        self._host_button.clicked.connect(self._host)
        self._join_button.clicked.connect(self.show_join)
        layout.addWidget(self._host_button)
        layout.addWidget(self._join_button)

        helper_text = (
            "One link. No setup."
            if self._host_available
            else "Hosting is available in the macOS app."
        )
        self._choice_helper = QLabel(helper_text)
        self._choice_helper.setObjectName("LaunchHelper")
        self._choice_helper.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._choice_helper)

        self._choice_error = QLabel("")
        self._choice_error.setObjectName("LaunchError")
        self._choice_error.setAccessibleName("Launch error")
        self._choice_error.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._choice_error.setWordWrap(True)
        layout.addWidget(self._choice_error)
        layout.addStretch(1)
        return page

    def _build_join_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(Space.MD, Space.XL, Space.MD, 0)
        layout.setSpacing(Space.MD)

        title = QLabel("Join your band.")
        title.setObjectName("LaunchTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle = QLabel("Paste the link your host sent you.")
        subtitle.setObjectName("LaunchSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addStretch(1)

        self._invite_input = QLineEdit()
        self._invite_input.setObjectName("LaunchInviteInput")
        # Version-2 invitations contain a private bearer credential. Keep the
        # value available to the parser without rendering it as ordinary text
        # on screen or exposing it as an ordinary editable value to assistive
        # technologies.
        self._invite_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._invite_input.setPlaceholderText("Paste your WebJam invite link")
        self._invite_input.setAccessibleName("WebJam invite link")
        self._invite_input.setAccessibleDescription(
            "Paste the complete invitation from your host."
        )
        self._invite_input.returnPressed.connect(self._join)
        self._invite_input.textChanged.connect(lambda *_: self._join_error.clear())
        layout.addWidget(self._invite_input)

        self._join_error = QLabel("")
        self._join_error.setObjectName("LaunchError")
        self._join_error.setAccessibleName("Join error")
        self._join_error.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._join_error.setWordWrap(True)
        layout.addWidget(self._join_error)

        self._join_button_primary = QPushButton("Join Jam")
        self._join_button_primary.setObjectName("LaunchPrimary")
        self._join_button_primary.setMinimumHeight(48)
        self._join_button_primary.setDefault(True)
        self._join_button_primary.clicked.connect(self._join)
        layout.addWidget(self._join_button_primary)
        layout.addStretch(2)

        back = QPushButton("Back")
        back.setObjectName("GhostButton")
        back.clicked.connect(self.show_choices)
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignHCenter)
        return page

    @property
    def showing_choices(self) -> bool:
        return self._pages.currentWidget() is self._choice_page

    def show_choices(self) -> None:
        self._restore_submission()
        self._pages.setCurrentWidget(self._choice_page)
        self._host_button.setFocus()

    def show_join(self) -> None:
        if not self._submitting:
            self._join_error.clear()
        self._pages.setCurrentWidget(self._join_page)
        self._invite_input.setFocus()

    def _host(self) -> None:
        if not self._begin_submission(self._host_button, "Starting…"):
            return
        apply_host_defaults(self._settings)
        try:
            save_settings(self._settings)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not save host defaults")
            self._choice_error.setText(
                "WebJam couldn’t start this jam. Quit and reopen WebJam, then try again."
            )
            self._announce_error(self._choice_error)
            self._restore_submission()
            return
        self.selected_role = "host"
        self.session_name = "Band Rehearsal"
        self.band_invite = None
        self.accept()

    def _join(self) -> None:
        self.accept_invite(self._invite_input.text())

    def accept_invite(self, value: str) -> bool:
        """Accept a pasted or operating-system-delivered invitation."""
        if not self._begin_submission(self._join_button_primary, "Joining…"):
            return False
        try:
            invite = invite_from_text(value)
        except InviteLinkError as exc:
            self._pages.setCurrentWidget(self._join_page)
            self._invite_input.setText(str(value or ""))
            message = str(exc)
            if str(value or "").strip() and "incompatible" not in message.lower():
                message = "That invite link doesn’t look right. Copy it again from your host."
            self._join_error.setText(message)
            self._announce_error(self._join_error, focus=self._invite_input)
            self._restore_submission()
            return False
        apply_join_invite(self._settings, invite)
        try:
            save_settings(self._settings)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Could not save join invitation")
            self._pages.setCurrentWidget(self._join_page)
            self._join_error.setText(
                "WebJam couldn’t save that invite. Quit and reopen WebJam, then try again."
            )
            self._announce_error(self._join_error, focus=self._invite_input)
            self._restore_submission()
            return False
        self.selected_role = "join"
        self.session_name = invite.session_name
        self.band_invite = invite
        self.accept()
        return True

    def _begin_submission(self, button: QPushButton, label: str) -> bool:
        """Accept exactly one Host/Join activation until it succeeds or fails."""
        if self._submitting:
            return False
        self._submitting = True
        self._host_button.setEnabled(False)
        self._join_button.setEnabled(False)
        self._join_button_primary.setEnabled(False)
        button.setText(label)
        button.setAccessibleName(label)
        return True

    def _restore_submission(self) -> None:
        self._submitting = False
        self._host_button.setText("Host a Jam")
        self._host_button.setAccessibleName("Host a Jam")
        self._host_button.setEnabled(self._host_available)
        self._join_button.setText("Join a Jam")
        self._join_button.setAccessibleName("Join a Jam")
        self._join_button.setEnabled(True)
        if hasattr(self, "_join_button_primary"):
            self._join_button_primary.setText("Join Jam")
            self._join_button_primary.setAccessibleName("Join Jam")
            self._join_button_primary.setEnabled(True)

    @staticmethod
    def _announce_error(label: QLabel, *, focus: Optional[QWidget] = None) -> None:
        label.setAccessibleDescription(label.text())
        try:
            QAccessible.updateAccessibility(
                QAccessibleEvent(label, QAccessible.Event.DescriptionChanged)
            )
        except (RuntimeError, TypeError):
            pass
        if focus is not None:
            focus.setFocus(Qt.FocusReason.OtherFocusReason)
