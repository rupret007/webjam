"""External meeting card with separate native-Webex controls.

WebJam never embeds, authenticates, joins, monitors, or controls a meeting.
The selected service or system browser owns sign-in, media devices, meeting
membership, mute state, and leave state. This widget keeps the generic link
handoff separate from its explicitly labeled native Webex app controls.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.meeting_link import is_allowed_meeting_link
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
    copy_link_requested = Signal()
    recheck_webex_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WebexEmbed")
        self.setMinimumHeight(112)
        self.setMaximumHeight(152)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._audio_mode = "talkback"
        self._creator_profile_key = "music"
        self._meeting_configured = False
        self._launch_busy = False
        self._native_app_available = False
        self._native_action_busy = False
        self._native_focus_restore: QPushButton | None = None
        self._service_label = ""
        self._launch_status = "Not opened"

        self._title_label = QLabel("Conversation")
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

        self._status_label = QLabel(
            "No meeting link has been opened from WebJam yet."
        )
        self._status_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._status_label.setWordWrap(True)
        self._status_label.setObjectName("WebexStatusLabel")
        self._status_label.setAccessibleName("Meeting launch status")
        self._status_label.setAccessibleDescription(self._status_label.text())

        self._bring_forward_btn = QPushButton("Show Webex App")
        self._bring_forward_btn.setObjectName("GhostButton")
        # Name and description are refreshed with the label in
        # _sync_native_actions; a screen reader must announce the same thing
        # the button says.
        self._bring_forward_btn.setAccessibleName("Show Webex App")
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

        # The label must describe what WebJam actually does — bring the
        # external app forward — not claim a mute action WebJam can neither
        # perform nor verify.
        self._mute_btn = QPushButton("Open Webex to Mute")
        self._mute_btn.setObjectName("GhostButton")
        self._mute_btn.setAccessibleName("Open Webex to Mute")
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
        self._fallback_btn.setAccessibleName("Join or open the meeting link")
        self._fallback_btn.setAccessibleDescription(
            "Explicitly open the configured link in its meeting service or a browser."
        )
        # No advice line until detection has run. Until then WebJam does not
        # know whether Show Webex App can do anything on this computer.
        self._fallback_btn.setToolTip(
            "Open the configured meeting link once in its service or your browser."
        )
        self._fallback_btn.clicked.connect(self.open_meeting_requested.emit)
        self._fallback_btn.setEnabled(False)

        self._copy_link_btn = QPushButton("Copy Link")
        self._copy_link_btn.setObjectName("GhostButton")
        self._copy_link_btn.setAccessibleName("Copy the saved meeting link")
        self._copy_link_btn.setAccessibleDescription(
            "Copy the saved meeting link to the clipboard to share it."
        )
        self._copy_link_btn.setToolTip(
            "Copy the saved meeting link so you can paste it anywhere."
        )
        self._copy_link_btn.clicked.connect(self.copy_link_requested.emit)
        self._copy_link_btn.setEnabled(False)

        self._change_link_btn = QPushButton("Add Link")
        self._change_link_btn.setObjectName("GhostButton")
        self._change_link_btn.setAccessibleName("Add a meeting link from any platform")
        self._change_link_btn.setAccessibleDescription(
            "Open WebJam Settings to add a public HTTPS meeting link from "
            "any meeting platform."
        )
        self._change_link_btn.setToolTip(
            "Open Settings to add or change the meeting link."
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

        header = self._header_layout = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(Space.SM)
        header.addWidget(self._title_label, stretch=1)
        header.addWidget(self._app_status_label)

        text_column = QVBoxLayout()
        text_column.setSpacing(0)
        text_column.addLayout(header)
        text_column.addWidget(self._mode_label)
        text_column.addWidget(self._status_label)

        actions = self._actions_layout = QGridLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(Space.SM)
        actions.addWidget(self._bring_forward_btn, 0, 0)
        actions.addWidget(self._mute_btn, 0, 1)
        actions.addWidget(self._fallback_btn, 1, 0)
        actions.addWidget(self._change_link_btn, 1, 1)
        actions.addWidget(self._copy_link_btn, 2, 0)
        actions.addWidget(self._install_btn, 2, 1)
        actions.addWidget(self._recheck_btn, 3, 0)

        layout = self._content_layout = QHBoxLayout(self)
        layout.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        layout.setSpacing(Space.LG)
        layout.addLayout(text_column, stretch=1)
        layout.addLayout(
            actions,
        )
        actions.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self._render_audio_guidance()
        self._render_launch_status()
        self._render_link_accessibility()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.LayoutRequest:
            self._sync_art_layout()
        return super().event(event)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        layout = getattr(self, "_content_layout", None)
        if layout is not None and self._creator_profile_key == "art":
            # A wide layout must still permit its parent to reach the narrow
            # breakpoint. Otherwise its old horizontal minimum prevents the
            # resize event that would stack these same controls.
            labels = (self._title_label, self._mode_label, self._status_label, self._app_status_label)
            text_width = max(
                (label.fontMetrics().horizontalAdvance(word)
                 for label in labels if not label.isHidden()
                 for word in label.text().split()),
                default=0,
            )
            margins = layout.contentsMargins()
            hint.setWidth(
                max(text_width, self._actions_layout.minimumSize().width())
                + margins.left() + margins.right() + 2 * self.frameWidth()
            )
        return hint

    def resizeEvent(self, event) -> None:
        self._sync_art_layout()
        super().resizeEvent(event)

    def _sync_art_layout(self) -> None:
        """Fit Art's retained Conversation card without changing its controls."""

        layout = getattr(self, "_content_layout", None)
        if layout is None or getattr(self, "_updating_art_layout", False):
            return
        self._updating_art_layout = True
        try:
            art = self._creator_profile_key == "art"
            margins = layout.contentsMargins()
            available = self.width() - margins.left() - margins.right() - 2 * self.frameWidth()
            header_width = self._title_label.sizeHint().width()
            if not self._app_status_label.isHidden():
                # Measure unwrapped text so changing the header direction
                # cannot move its own breakpoint and oscillate on resize.
                header_width += (
                    self._app_status_label.fontMetrics().horizontalAdvance(
                        self._app_status_label.text()
                    ) + 2 * self._app_status_label.margin() + Space.SM
                )
            text_width = max(280, header_width)
            narrow = art and available < text_width + self._actions_layout.minimumSize().width() + Space.LG
            direction = (
                QBoxLayout.Direction.TopToBottom if narrow
                else QBoxLayout.Direction.LeftToRight
            )
            if layout.direction() != direction:
                layout.setDirection(direction)
                layout.setStretch(0, 0 if narrow else 1)
            if self._header_layout.direction() != direction:
                self._header_layout.setDirection(direction)
            if self._app_status_label.wordWrap() != narrow:
                self._app_status_label.setWordWrap(narrow)
            alignment = (
                Qt.AlignmentFlag.AlignLeft if narrow else Qt.AlignmentFlag.AlignRight
            ) | Qt.AlignmentFlag.AlignVCenter
            if self._app_status_label.alignment() != alignment:
                self._app_status_label.setAlignment(alignment)
            if art:
                # QLayout accounts for the current wrapped labels, visible
                # actions and stylesheet metrics. No timer or rebuilt widget
                # can disturb the current meeting state or keyboard focus.
                required = layout.totalHeightForWidth(self.width())
                height = max(112, required + 2 * self.frameWidth())
                if self.minimumHeight() != height or self.maximumHeight() != height:
                    self.setFixedHeight(height)
            else:
                if self.minimumHeight() != 112:
                    self.setMinimumHeight(112)
                if self.maximumHeight() != 152:
                    self.setMaximumHeight(152)
        finally:
            self._updating_art_layout = False

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
        """Return the meeting-link Settings action."""

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
        # Detection is what decides whether pointing at Show Webex App is
        # true, so the meeting tooltip is re-rendered rather than left with
        # whatever the previous platform answer implied.
        self._render_launch_status()
        self._restore_native_focus()
        self._announce_description_change(self._app_status_label)

    def set_service_label(self, label: str) -> None:
        """Name the saved link's meeting service on the card truthfully."""

        clean = " ".join(str(label or "").split())[:32]
        if clean == self._service_label:
            return
        self._service_label = clean
        self._render_audio_guidance()
        self._render_launch_status()
        self._render_link_accessibility()

    def set_meeting_configured(self, configured: bool) -> None:
        """Render whether Join/Open has a trusted saved link to hand off."""

        self._meeting_configured = bool(configured)
        self._copy_link_btn.setEnabled(self._meeting_configured)
        self._change_link_btn.setText(
            "Change Link" if self._meeting_configured else "Add Link"
        )
        self._render_link_accessibility()
        self._render_audio_guidance()
        self._sync_meeting_action()
        self._sync_native_actions()

    def set_launch_status(self, status: str) -> None:
        """Show external-launch truth without implying meeting membership."""

        self._launch_status = str(status)
        self._render_launch_status()

    def _render_launch_status(self) -> None:
        """Render the saved provider's handoff state, never meeting membership."""

        status = self._launch_status
        service = self._service_label
        descriptions = (
            {
                "Not opened": f"{service} has not been opened from WebJam yet.",
                "Opening…": f"Opening {service} externally…",
                "Opened externally": (
                    f"Opened externally—finish joining in {service}."
                ),
                "Open failed": (
                    f"{service} could not be opened. Retry or check Settings."
                ),
            }
            if service
            else {
                "Not opened": (
                    "No meeting link has been opened from WebJam yet."
                ),
                "Opening…": "Opening the meeting link externally…",
                "Opened externally": (
                    "Opened externally—finish joining in your meeting service."
                ),
                "Open failed": (
                    "The meeting link could not be opened. Retry or check Settings."
                ),
            }
        )
        description = descriptions.get(status, str(status))
        self._status_label.setText(description)
        self._status_label.setAccessibleName(
            f"{service} launch status" if service else "Meeting launch status"
        )
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
        if service:
            accessible_name = (
                f"Opening {service} meeting"
                if status == "Opening…"
                else f"Open {service} meeting again"
                if status == "Opened externally"
                else f"Join or open the {service} meeting"
            )
        else:
            accessible_name = (
                "Opening the meeting link"
                if status == "Opening…"
                else "Open the meeting link again"
                if status == "Opened externally"
                else "Join or open the meeting link"
            )
        self._fallback_btn.setAccessibleName(accessible_name)
        self._fallback_btn.setAccessibleDescription(description)
        destination = f"{service} or your browser" if service else (
            "its service or your browser"
        )
        self._fallback_btn.setToolTip(
            f"Open the configured meeting link once in {destination}."
            f"{self._show_webex_advice()}"
        )

    def _render_link_accessibility(self) -> None:
        """Keep link-edit semantics aligned with the detected provider."""

        service = self._service_label
        if service:
            accessible_name = (
                f"Change {service} meeting link"
                if self._meeting_configured
                else f"Add {service} meeting link"
            )
        else:
            accessible_name = (
                "Change the saved meeting link"
                if self._meeting_configured
                else "Add a meeting link from any platform"
            )
        self._change_link_btn.setAccessibleName(accessible_name)
        self._change_link_btn.setAccessibleDescription(
            "Open WebJam Settings to "
            + (
                (
                    f"change the saved {service} meeting link."
                    if service
                    else "change the saved meeting link."
                )
                if self._meeting_configured
                else "add a public HTTPS link from any meeting platform."
            )
        )
        self._change_link_btn.setToolTip(
            f"Open Settings to add or change the {service} meeting link."
            if service
            else "Open Settings to add or change the meeting link."
        )

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
        """Render concise role guidance for the selected meeting audio mode."""

        self._audio_mode = (
            mode
            if mode in {"talkback", "video_only", "audience_bridge"}
            else "talkback"
        )
        self._render_audio_guidance()

    def load_meeting(self, meeting_url: str, **_unused: object) -> bool:
        """Compatibility guard: embedded meetings are no longer supported."""

        if not is_allowed_meeting_link(meeting_url):
            LOGGER.warning("Meeting card refused an untrusted meeting URL")
        else:
            LOGGER.warning(
                "Embedded meetings are retired; use the external launch action"
            )
        self.meeting_state_changed.emit("error")
        return False

    def leave_meeting(self) -> None:
        """Compatibility no-op because WebJam does not own external meetings."""

    def shutdown(self) -> None:
        """Compatibility no-op; this card owns no browser or media process."""

    def _sync_meeting_action(self) -> None:
        self._fallback_btn.setEnabled(
            self._meeting_configured and not self._launch_busy
        )

    def _show_webex_label(self) -> str:
        """Name only the application activation WebJam can actually prove."""

        return "Show Webex App"

    def _show_webex_advice(self) -> str:
        """Point at Show Webex App only where it can actually be honoured.

        ADR 0004 keeps native focus disabled on Windows and Linux, because
        their detection does not establish publisher proof. The button is
        correctly disabled there -- but advice is a claim too, and telling
        someone to use a control that cannot do what the sentence says is the
        same overclaim as enabling it.
        """

        if not self._native_app_available:
            return ""
        return (
            "\nUse Show Webex App to bring Webex forward without reopening "
            "the link."
        )

    def _sync_native_actions(self) -> None:
        enabled = self._native_app_available and not self._native_action_busy
        self._bring_forward_btn.setEnabled(enabled)
        # Art uses conversation and work sharing directly in the meeting.
        # The Music-specific shortcut to its mute controls is not a room task.
        show_mute = self._creator_profile_key != "art"
        self._mute_btn.setVisible(show_mute)
        self._mute_btn.setEnabled(enabled and show_mute)
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

    def set_creator_profile(self, profile) -> None:
        self._creator_profile_key = profile.key
        self._render_audio_guidance()
        self._sync_native_actions()
        self._sync_art_layout()

    def _render_audio_guidance(self) -> None:
        service = self._service_label
        if self._creator_profile_key == "art":
            self._title_label.setText("Conversation")
            self._mode_label.setText(
                f"Talk and share a demonstration in {service or 'Webex or your meeting app'} if you like. "
                "Use your own tools. Paint along plays a separate silent local video."
            )
            return
        titles = (
            {
                "talkback": f"{service} conversation",
                "video_only": f"{service} video",
                "audience_bridge": f"{service} audience feed",
            }
            if service
            else {
                "talkback": "Conversation",
                "video_only": "Conversation video",
                "audience_bridge": "Conversation audience feed",
            }
        )
        guidance = (
            {
                "talkback": (
                    f"Keep {service} muted while playing. To speak, mute your "
                    "audio interface or end the WebJam session first."
                ),
                "video_only": (
                    f"Join {service} without computer audio; music stays in Jamulus."
                ),
                "audience_bridge": (
                    "Advanced audience feed: musicians must disconnect "
                    f"{service} audio to prevent delayed duplicate music."
                ),
            }
            if service
            else {
                "talkback": (
                    (
                        "Keep your meeting service muted while you play. To "
                        "speak, mute your audio interface or end the WebJam "
                        "session first."
                    )
                    if self._meeting_configured
                    else (
                        "After adding a meeting link, keep that service muted "
                        "while playing. To speak, mute your audio interface or "
                        "end the WebJam session first."
                    )
                ),
                "video_only": (
                    "Join your meeting service without computer audio; music "
                    "stays in Jamulus."
                ),
                "audience_bridge": (
                    "Advanced audience feed: musicians must disconnect meeting "
                    "audio to prevent delayed duplicate music."
                ),
            }
        )
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
