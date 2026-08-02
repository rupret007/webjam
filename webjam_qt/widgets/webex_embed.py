"""Lightweight external-Webex conversation card for the live workspace.

WebJam never embeds, authenticates, joins, monitors, or controls a Webex
meeting. The native Webex application or system browser owns sign-in, media
devices, meeting membership, mute state, and leave state. This widget keeps
navigation, app activation, and an explicit meeting-link handoff separate.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QApplication,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.webex_url import is_allowed_webex_url
from webjam_qt.theme.tokens import Space

LOGGER = logging.getLogger("webjam.qt.webex_embed")


class WebexEmbed(QFrame):
    """External launch card retaining the former widget's caller interface."""

    meeting_state_changed = Signal(str)
    install_webex_requested = Signal()
    bring_forward_requested = Signal()
    open_meeting_requested = Signal()
    change_link_requested = Signal()
    mute_in_webex_requested = Signal()
    recheck_webex_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("WebexEmbed")
        self.setMinimumHeight(112)
        self.setMaximumHeight(152)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._audio_mode = "talkback"
        self._meeting_configured = False
        self._launch_busy = False
        self._native_app_available = False
        self._native_action_busy = False
        self._native_focus_restore: QPushButton | None = None

        self._title_label = QLabel("Webex conversation")
        self._title_label.setObjectName("WebexEmbedTitle")
        self._title_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        self._app_status_label = QLabel()
        self._app_status_label.setObjectName("WebexAppStatusLabel")
        self._app_status_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._app_status_label.setAccessibleName("Webex app status")
        self._app_status_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._app_status_label.setVisible(False)

        self._mode_label = QLabel()
        self._mode_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._mode_label.setWordWrap(True)
        self._mode_label.setObjectName("BodyLabel")

        self._status_label = QLabel("Webex has not been opened from WebJam yet.")
        self._status_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._status_label.setWordWrap(True)
        self._status_label.setObjectName("WebexStatusLabel")
        self._status_label.setAccessibleName("Webex launch status")
        self._status_label.setAccessibleDescription(self._status_label.text())

        self._bring_forward_btn = QPushButton("Show Webex App")
        self._bring_forward_btn.setObjectName("GhostButton")
        # Name and description are refreshed with the label in
        # _sync_native_actions; a screen reader must announce the same thing
        # the button says, and the label depends on the meeting state.
        self._bring_forward_btn.setAccessibleName("Show Webex")
        self._bring_forward_btn.setAccessibleDescription(
            "Brings Webex forward. No meeting link or browser is opened."
        )
        self._bring_forward_btn.setToolTip(
            "Bring Webex forward. No meeting link or browser is opened.\n"
            "If Webex is closed, this starts it."
        )
        self._bring_forward_btn.clicked.connect(
            self.bring_forward_requested.emit
        )
        self._bring_forward_btn.setEnabled(False)

        self._mute_btn = QPushButton("Mute in Webex")
        self._mute_btn.setObjectName("GhostButton")
        self._mute_btn.setAccessibleName("Mute in Webex")
        self._mute_btn.setAccessibleDescription(
            "Show the verified Webex app so you can use its Mute control. "
            "WebJam cannot verify or change mute in the external Webex app."
        )
        self._mute_btn.setToolTip(
            "Brings Webex forward so you can use its own Mute control."
        )
        self._mute_btn.clicked.connect(self.mute_in_webex_requested.emit)
        self._mute_btn.setEnabled(False)

        self._fallback_btn = QPushButton("Join / Open Meeting")
        self._fallback_btn.setObjectName("GhostButton")
        self._fallback_btn.setAccessibleName("Join or open the Webex meeting")
        self._fallback_btn.setAccessibleDescription(
            "Explicitly open the configured meeting in Webex or a browser."
        )
        self._fallback_btn.setToolTip(
            "Open the configured meeting link once in Webex or your browser.\n"
            "Use Show Meeting when it is already open."
        )
        self._fallback_btn.clicked.connect(self.open_meeting_requested.emit)
        self._fallback_btn.setEnabled(False)

        self._change_link_btn = QPushButton("Add Link")
        self._change_link_btn.setObjectName("GhostButton")
        self._change_link_btn.setAccessibleName("Add Webex meeting link")
        self._change_link_btn.setAccessibleDescription(
            "Open WebJam Settings to add a Webex Meeting or Personal Room link."
        )
        self._change_link_btn.setToolTip(
            "Open Settings to add or change the Webex meeting link."
        )
        self._change_link_btn.clicked.connect(self.change_link_requested.emit)

        self._install_btn = QPushButton("Get Webex")
        self._install_btn.setObjectName("GhostButton")
        self._install_btn.setAccessibleName("Get Webex")
        self._install_btn.setAccessibleDescription(
            "Open Cisco's official Webex download. WebJam will not install "
            "software or accept terms automatically."
        )
        self._install_btn.setVisible(False)
        self._install_btn.clicked.connect(self.install_webex_requested.emit)

        self._recheck_btn = QPushButton("Check Again")
        self._recheck_btn.setObjectName("GhostButton")
        self._recheck_btn.setAccessibleName("Check for the Webex app again")
        self._recheck_btn.setAccessibleDescription(
            "Recheck Webex after installing, updating, or repairing it."
        )
        self._recheck_btn.setToolTip(
            "Check again after you finish installing or repairing Webex."
        )
        self._recheck_btn.setVisible(False)
        self._recheck_btn.clicked.connect(self.recheck_webex_requested.emit)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(Space.SM)
        header.addWidget(self._title_label, stretch=1)
        header.addWidget(self._app_status_label)

        text_column = QVBoxLayout()
        text_column.setSpacing(0)
        text_column.addLayout(header)
        text_column.addWidget(self._mode_label)
        text_column.addWidget(self._status_label)

        actions = QGridLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(Space.SM)
        actions.addWidget(self._bring_forward_btn, 0, 0)
        actions.addWidget(self._mute_btn, 0, 1)
        actions.addWidget(self._fallback_btn, 1, 0)
        actions.addWidget(self._change_link_btn, 1, 1)
        actions.addWidget(self._install_btn, 2, 0)
        actions.addWidget(self._recheck_btn, 2, 1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        layout.setSpacing(Space.LG)
        layout.addLayout(text_column, stretch=1)
        layout.addLayout(
            actions,
        )
        actions.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._render_audio_guidance()

    def fallback_button(self) -> QPushButton:
        """Return the explicit external meeting-link handoff button."""

        return self._fallback_btn

    def bring_forward_button(self) -> QPushButton:
        """Return the installed-app activation action."""

        return self._bring_forward_btn

    def show_app_button(self) -> QPushButton:
        """Return the explicit app-only activation action."""

        return self._bring_forward_btn

    def mute_button(self) -> QPushButton:
        """Return the truthful external mute-guidance action."""

        return self._mute_btn

    def change_link_button(self) -> QPushButton:
        """Return the Webex-link Settings action."""

        return self._change_link_btn

    def install_button(self) -> QPushButton:
        """Return the normally hidden official-installer action."""

        return self._install_btn

    def recheck_button(self) -> QPushButton:
        """Return the explicit post-install native-app rescan action."""

        return self._recheck_btn

    def set_app_checking(self) -> None:
        """Show a bounded native-app rescan without changing meeting truth."""

        text = "Checking for the Webex app…"
        self._app_status_label.setText(text)
        self._app_status_label.setAccessibleDescription(text)
        self._app_status_label.setVisible(True)
        self._recheck_btn.setVisible(True)
        self._native_app_available = False
        self._set_native_busy(True)
        self._announce_description_change(self._app_status_label)

    def set_native_action_busy(self, busy: bool) -> None:
        """Disable duplicate app activations while publisher checks run."""

        self._set_native_busy(bool(busy))

    def set_app_status(
        self,
        status: object,
        *,
        version: str = "",
        publisher_verified: bool = False,
        reason_code: str = "",
    ) -> None:
        """Show native-app availability without changing meeting-launch truth."""

        value = str(getattr(status, "value", status) or "").strip().lower()
        clean_reason = str(reason_code or "").strip().lower()
        clean_version = str(version or "").strip()
        if (
            len(clean_version) > 32
            or any(ord(character) < 32 for character in clean_version)
        ):
            clean_version = ""
        installed_status = (
            (
                "Webex app verified"
                + (f" • {clean_version}" if clean_version else ""),
                (
                    "Cisco Webex is installed and its publisher is verified. "
                    "Show Webex App activates or launches the app itself "
                    "without a URL; opening a meeting still requires Join / "
                    "Open Meeting."
                ),
            )
            if publisher_verified
            else (
                "Webex app found"
                + (f" • {clean_version}" if clean_version else ""),
                (
                    "The Webex app was found, but publisher verification is "
                    "not available on this platform, so WebJam will not "
                    "activate it directly. Use Join / Open Meeting for the "
                    "configured meeting."
                ),
            )
        )
        descriptions = {
            "installed": (
                installed_status[0],
                False,
                "Get Webex",
                installed_status[1],
            ),
            "not-installed": (
                "Webex app not installed",
                True,
                "Get Webex",
                (
                    "Open Cisco's official Webex download. WebJam will not "
                    "install software or accept terms automatically."
                ),
            ),
            "invalid": (
                "Webex app needs attention",
                True,
                "Get Webex",
                (
                    "Open Cisco's official Webex download to repair or replace "
                    "the unverified installation."
                ),
            ),
            "unsupported": (
                "Webex app check unavailable",
                False,
                "Get Webex",
                (
                    "Native Webex app detection is unavailable on this "
                    "platform. The configured meeting can still open in a "
                    "supported browser."
                ),
            ),
        }
        try:
            text, show_install, button_text, accessible_description = descriptions[
                value
            ]
        except KeyError as exc:
            raise ValueError("unsupported Webex app status") from exc
        retryable_detection_failure = (
            value == "unsupported" and clean_reason == "detection-failed"
        )
        if retryable_detection_failure:
            text = "Webex app check failed"
            accessible_description = (
                "The native Webex app check did not finish. Choose Check "
                "Again; the configured meeting can still open in a supported "
                "browser."
            )
        self._app_status_label.setText(text)
        self._app_status_label.setAccessibleDescription(accessible_description)
        self._app_status_label.setToolTip(accessible_description)
        self._app_status_label.setVisible(True)
        self._install_btn.setText(button_text)
        self._install_btn.setAccessibleName(button_text)
        self._install_btn.setAccessibleDescription(accessible_description)
        self._install_btn.setToolTip(accessible_description)
        self._install_btn.setVisible(show_install)
        self._recheck_btn.setVisible(
            value in {"not-installed", "invalid"}
            or retryable_detection_failure
        )
        self._native_app_available = bool(
            value == "installed" and publisher_verified
        )
        self._native_action_busy = False
        self._sync_native_actions()
        self._restore_native_focus()
        self._announce_description_change(self._app_status_label)

    def set_meeting_configured(self, configured: bool) -> None:
        """Render whether Join/Open has a trusted saved link to hand off."""

        self._meeting_configured = bool(configured)
        self._change_link_btn.setText(
            "Change Link" if self._meeting_configured else "Add Link"
        )
        self._change_link_btn.setAccessibleName(
            (
                "Change Webex meeting link"
                if self._meeting_configured
                else "Add Webex meeting link"
            )
        )
        self._change_link_btn.setAccessibleDescription(
            "Open WebJam Settings to "
            + (
                "change the saved Webex Meeting or Personal Room link."
                if self._meeting_configured
                else "add a Webex Meeting or Personal Room link."
            )
        )
        self._sync_meeting_action()
        # "Show Meeting" vs "Show Webex" depends on this same state.
        self._sync_native_actions()

    def set_launch_status(self, status: str) -> None:
        """Show external-launch truth without implying meeting membership."""

        descriptions = {
            "Not opened": "Webex has not been opened from WebJam yet.",
            "Opening…": "Opening Webex externally…",
            "Opened externally": "Opened externally—finish joining in Webex.",
            "Open failed": "Webex could not be opened. Retry or check Settings.",
        }
        description = descriptions.get(status, str(status))
        self._status_label.setText(description)
        self._status_label.setAccessibleDescription(description)
        self._announce_description_change(self._status_label)
        button_text = (
            "Opening…"
            if status == "Opening…"
            else "Open Again"
            if status == "Opened externally"
            else "Join / Open Meeting"
        )
        self._fallback_btn.setText(button_text)
        self._launch_busy = status == "Opening…"
        self._sync_meeting_action()
        self._fallback_btn.setAccessibleName(button_text)
        self._fallback_btn.setAccessibleDescription(description)

    def focus_primary_action(self) -> None:
        """Place keyboard focus on the safest useful Conversation action."""

        target = (
            self._bring_forward_btn
            if self._bring_forward_btn.isEnabled()
            else self._fallback_btn
            if self._fallback_btn.isEnabled()
            else self._change_link_btn
        )
        target.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def set_audio_mode(self, mode: str) -> None:
        """Render concise role guidance for the selected Webex audio mode."""

        self._audio_mode = (
            mode
            if mode in {"talkback", "video_only", "audience_bridge"}
            else "talkback"
        )
        self._render_audio_guidance()

    def load_meeting(self, meeting_url: str, **_unused: object) -> bool:
        """Compatibility guard: embedded meetings are no longer supported."""

        if not is_allowed_webex_url(meeting_url):
            LOGGER.warning("Webex launch card refused an untrusted meeting URL")
        else:
            LOGGER.warning(
                "Embedded Webex is retired; use the external launch action"
            )
        self.meeting_state_changed.emit("error")
        return False

    def leave_meeting(self) -> None:
        """Compatibility no-op because WebJam does not own external Webex."""

    def shutdown(self) -> None:
        """Compatibility no-op; this card owns no browser or media process."""

    def _sync_meeting_action(self) -> None:
        self._fallback_btn.setEnabled(
            self._meeting_configured and not self._launch_busy
        )

    def _show_webex_label(self) -> str:
        """Name what the musician gets, which depends on the meeting state.

        With a meeting set up, raising Webex puts the call in front, so the
        button says so. With no meeting yet there is nothing to show but the
        app itself, and claiming otherwise would be a promise WebJam cannot
        keep.
        """

        return "Show Meeting" if self._meeting_configured else "Show Webex"

    def _sync_native_actions(self) -> None:
        enabled = self._native_app_available and not self._native_action_busy
        self._bring_forward_btn.setEnabled(enabled)
        self._mute_btn.setEnabled(enabled)
        self._recheck_btn.setEnabled(not self._native_action_busy)
        label = (
            "Verifying…"
            if self._native_action_busy
            else self._show_webex_label()
        )
        self._bring_forward_btn.setText(label)
        # Keep the announced name identical to the visible label.
        self._bring_forward_btn.setAccessibleName(label)

    def _set_native_busy(self, busy: bool) -> None:
        busy = bool(busy)
        if busy and not self._native_action_busy:
            focused = QApplication.focusWidget()
            if focused in {
                self._bring_forward_btn,
                self._mute_btn,
                self._recheck_btn,
            }:
                self._native_focus_restore = focused
                self._app_status_label.setFocus(
                    Qt.FocusReason.OtherFocusReason
                )
        self._native_action_busy = busy
        self._sync_native_actions()
        if not busy:
            self._restore_native_focus()

    def _restore_native_focus(self) -> None:
        target = self._native_focus_restore
        self._native_focus_restore = None
        if (
            target is not None
            and target.isVisible()
            and target.isEnabled()
        ):
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def _render_audio_guidance(self) -> None:
        titles = {
            "talkback": "Webex conversation",
            "video_only": "Webex video",
            "audience_bridge": "Webex audience feed",
        }
        guidance = {
            "talkback": (
                "Keep Webex muted while playing. To speak, mute your audio "
                "interface or end the WebJam session first."
            ),
            "video_only": (
                "Join Webex without computer audio; music stays in Jamulus."
            ),
            "audience_bridge": (
                "Advanced audience feed: musicians must disconnect Webex audio "
                "to prevent delayed duplicate music."
            ),
        }
        self._title_label.setText(titles[self._audio_mode])
        self._mode_label.setText(guidance[self._audio_mode])

    @staticmethod
    def _announce_description_change(label: QLabel) -> None:
        try:
            QAccessible.updateAccessibility(
                QAccessibleEvent(
                    label,
                    QAccessible.Event.DescriptionChanged,
                )
            )
        except (RuntimeError, TypeError):
            # Some headless and teardown paths no longer have an accessibility
            # backend. The visible and semantic text is still updated.
            pass
