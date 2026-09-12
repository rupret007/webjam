"""External meeting card with separate native-Webex controls.

WebJam never embeds, authenticates, joins, monitors, or controls a meeting.
The selected service or system browser owns sign-in, media devices, meeting
membership, mute state, and leave state. This widget keeps the generic link
handoff separate from its explicitly labeled native Webex app controls.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from itertools import islice

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
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.meeting_link import is_allowed_meeting_link
from core.lesson_request import LessonRequestIntent, LessonRequestNotice
from webjam_qt.theme.tokens import Space

LOGGER = logging.getLogger("webjam.qt.webex_embed")


class _LessonRequestName(QLabel):
    """A bounded, plain label must not make a narrow card grow."""

    def __init__(self) -> None:
        super().__init__()
        self._name = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def set_name(self, name: str) -> None:
        self._name = " ".join(str(name).split())[:80] or "Guest"
        self.setAccessibleName(self._name)
        self._fit_name()

    def _fit_name(self) -> None:
        self.setText(self.fontMetrics().elidedText(
            self._name, Qt.TextElideMode.ElideRight, max(0, self.contentsRect().width()),
        ))

    def resizeEvent(self, event) -> None:
        self._fit_name()
        super().resizeEvent(event)


class _LessonRequestRow(QFrame):
    def __init__(self, acknowledge) -> None:
        super().__init__()
        self.name_label = _LessonRequestName()
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.ack_button = QPushButton("Acknowledge request")
        self.ack_button.setObjectName("GhostButton")
        self.ack_button.setAutoDefault(False)
        self.ack_button.clicked.connect(acknowledge)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, Space.SM)
        layout.setSpacing(Space.XS)
        layout.addWidget(self.name_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.ack_button)

    def present(self, name: str, notice: LessonRequestNotice) -> None:
        self.name_label.set_name(name)
        intent = ("Asked for a pause" if notice.intent is LessonRequestIntent.PAUSE
                  else "Ready to continue")
        remaining = max(0, (notice.expires_in_ms + 999) // 1000)
        expired = notice.state == "expired" or remaining == 0
        state = ("Request expired" if expired else f"{intent} · acknowledged"
                 if notice.state == "acknowledged" else intent)
        self.status_label.setText(
            state if expired else f"{state} · {remaining}s left"
        )
        self.ack_button.setAccessibleName(
            f"Acknowledge request from {self.name_label._name}"
        )
        self.ack_button.setAccessibleDescription(
            f"{intent}. Acknowledging does not pause or resume the browser."
        )
        self.ack_button.setEnabled(notice.state == "accepted" and not expired)

    def retire(self) -> None:
        self.ack_button.setEnabled(False)
        self.name_label.set_name("")
        self.status_label.clear()
        self.ack_button.setAccessibleName("Acknowledge request")
        self.ack_button.setAccessibleDescription("")
        self.hide()


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
    lesson_request_intent = Signal(str)
    lesson_request_retry = Signal()
    lesson_request_acknowledge = Signal(str, str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WebexEmbed")
        self.setMinimumHeight(112)
        self.setMaximumHeight(152)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._audio_mode = "talkback"
        self._creator_profile_key = "music"
        self._shared_lesson_hosting: bool | None = None
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
            "Copy the meeting link used by Conversation so you can paste it anywhere."
        )
        self._copy_link_btn.clicked.connect(self.copy_link_requested.emit)
        self._copy_link_btn.setEnabled(False)

        self._change_link_btn = QPushButton("Add Link")
        self._change_link_btn.setObjectName("GhostButton")
        self._change_link_btn.setAccessibleName("Add a meeting link from any platform")
        self._change_link_btn.setAccessibleDescription(
            "Add or change the public HTTPS link used by Conversation, from "
            "any meeting platform."
        )
        self._change_link_btn.setToolTip(
            "Add or change the meeting link used by Conversation."
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
        self._build_lesson_request_panel(text_column)

        actions = self._actions_layout = QGridLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(Space.SM)
        self._action_positions = (
            (self._bring_forward_btn, 0, 0),
            (self._mute_btn, 0, 1),
            (self._fallback_btn, 1, 0),
            (self._change_link_btn, 1, 1),
            (self._copy_link_btn, 2, 0),
            (self._install_btn, 2, 1),
            (self._recheck_btn, 3, 0),
        )
        self._actions_single_column = False
        for button, row, column in self._action_positions:
            actions.addWidget(button, row, column)

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

    def _build_lesson_request_panel(self, layout: QVBoxLayout) -> None:
        self._lesson_request_frame = QFrame()
        request_layout = QVBoxLayout(self._lesson_request_frame)
        request_layout.setContentsMargins(0, Space.SM, 0, 0)
        request_layout.setSpacing(Space.XS)
        self._lesson_request_guest_status = QLabel()
        self._lesson_request_guest_status.setTextFormat(Qt.TextFormat.PlainText)
        self._lesson_request_guest_status.setWordWrap(True)
        self._lesson_request_guest_status.setAccessibleName("Your lesson request")
        request_layout.addWidget(self._lesson_request_guest_status)
        self._lesson_pause_button = QPushButton("Ask for a pause")
        self._lesson_ready_button = QPushButton("Ready to continue")
        self._lesson_retry_button = QPushButton("Retry this request")
        for button in (self._lesson_pause_button, self._lesson_ready_button, self._lesson_retry_button):
            button.setObjectName("GhostButton")
            button.setAutoDefault(False)
            button.setAccessibleName(button.text())
            button.setAccessibleDescription(
                "Send an explicit request. The host controls the browser."
            )
            request_layout.addWidget(button)
        self._lesson_pause_button.clicked.connect(lambda: self._emit_lesson_intent("pause"))
        self._lesson_ready_button.clicked.connect(lambda: self._emit_lesson_intent("ready"))
        self._lesson_retry_button.clicked.connect(self._emit_lesson_retry)
        self._lesson_request_host_hint = QLabel(
            "Each request is separate. You control the browser."
        )
        self._lesson_request_host_hint.setTextFormat(Qt.TextFormat.PlainText)
        self._lesson_request_host_hint.setWordWrap(True)
        request_layout.addWidget(self._lesson_request_host_hint)
        self._lesson_request_scroll = QScrollArea()
        self._lesson_request_scroll.setWidgetResizable(True)
        self._lesson_request_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._lesson_request_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._lesson_request_scroll.setFixedHeight(208)
        self._lesson_request_scroll.setAccessibleName("Requests for this lesson")
        content = QWidget()
        self._lesson_request_host_layout = QVBoxLayout(content)
        self._lesson_request_host_layout.setContentsMargins(0, 0, Space.XS, 0)
        self._lesson_request_host_layout.setSpacing(Space.SM)
        self._lesson_request_host_layout.addStretch(1)
        self._lesson_request_scroll.setWidget(content)
        request_layout.addWidget(self._lesson_request_scroll)
        self._lesson_request_rows: dict[tuple[str, str, int], _LessonRequestRow] = {}
        self._lesson_request_notices: dict[tuple[str, str, int], LessonRequestNotice] = {}
        self._lesson_guest_status = ""
        self._lesson_guest_permissions = (False, False, False)
        layout.addWidget(self._lesson_request_frame)
        self._sync_lesson_requests()

    def _clear_lesson_requests(self) -> None:
        self._lesson_guest_status = ""
        self._lesson_guest_permissions = (False, False, False)
        self._lesson_request_notices.clear()
        for row in self._lesson_request_rows.values():
            row.retire()
            self._lesson_request_host_layout.removeWidget(row)
            row.deleteLater()
        self._lesson_request_rows.clear()
        self._lesson_request_guest_status.setText("")
        self._lesson_request_guest_status.setAccessibleDescription("")

    def set_lesson_request_guest(
        self, *, status: str, can_pause: bool = False,
        can_ready: bool = False, can_retry: bool = False,
    ) -> None:
        """Project one guest's current receipt; never allocate or send an intent."""

        if self._creator_profile_key != "art" or self._shared_lesson_hosting is not False:
            return
        self._lesson_guest_status = " ".join(str(status).split())[:512]
        self._lesson_guest_permissions = (can_pause is True, can_ready is True, can_retry is True)
        self._sync_lesson_requests()
        self._sync_art_layout()

    def set_lesson_request_host(self, notices: Sequence[tuple[str, LessonRequestNotice]]) -> None:
        """Render bounded host-local notices. The caller owns time and authority."""

        if self._creator_profile_key != "art" or self._shared_lesson_hosting is not True:
            return
        current = {}
        for name, notice in islice(notices, 32):
            if not isinstance(notice, LessonRequestNotice):
                continue
            key = (notice.context_id, notice.admission_id, notice.revision)
            if key in current:
                continue
            current[key] = notice
            row = self._lesson_request_rows.get(key)
            if row is None:
                row = _LessonRequestRow(lambda _checked=False, captured=key: self._emit_lesson_ack(captured))
                self._lesson_request_rows[key] = row
                self._lesson_request_host_layout.insertWidget(
                    self._lesson_request_host_layout.count() - 1, row,
                )
            row.present(name, notice)
        for key in self._lesson_request_rows.keys() - current.keys():
            row = self._lesson_request_rows.pop(key)
            row.retire()
            self._lesson_request_host_layout.removeWidget(row)
            row.deleteLater()
        self._lesson_request_notices = current
        self._sync_lesson_requests()
        self._sync_art_layout()

    def _sync_lesson_requests(self) -> None:
        guest = self._creator_profile_key == "art" and self._shared_lesson_hosting is False
        host = self._creator_profile_key == "art" and self._shared_lesson_hosting is True
        self._lesson_request_guest_status.setVisible(guest)
        self._lesson_request_guest_status.setText(self._lesson_guest_status if guest else "")
        self._lesson_request_guest_status.setAccessibleDescription(self._lesson_guest_status if guest else "")
        for button, allowed in zip(
            (self._lesson_pause_button, self._lesson_ready_button, self._lesson_retry_button),
            self._lesson_guest_permissions,
        ):
            button.setVisible(guest and (button is not self._lesson_retry_button or allowed))
            button.setEnabled(guest and allowed)
        has_notices = host and bool(self._lesson_request_notices)
        self._lesson_request_host_hint.setVisible(has_notices)
        self._lesson_request_scroll.setVisible(has_notices)
        self._lesson_request_frame.setVisible(
            (guest and bool(self._lesson_guest_status)) or has_notices
        )

    def _emit_lesson_intent(self, intent: str) -> None:
        button = self._lesson_pause_button if intent == "pause" else self._lesson_ready_button
        if (self._creator_profile_key == "art" and self._shared_lesson_hosting is False
                and self.isVisible() and button.isVisibleTo(self) and button.isEnabled()):
            self.lesson_request_intent.emit(intent)

    def _emit_lesson_retry(self) -> None:
        if (self._creator_profile_key == "art" and self._shared_lesson_hosting is False
                and self.isVisible() and self._lesson_retry_button.isVisibleTo(self)
                and self._lesson_retry_button.isEnabled()):
            self.lesson_request_retry.emit()

    def _emit_lesson_ack(self, key: tuple[str, str, int]) -> None:
        row = self._lesson_request_rows.get(key)
        notice = self._lesson_request_notices.get(key)
        if (self._creator_profile_key == "art" and self._shared_lesson_hosting is True
                and self.isVisible() and row is not None and notice is not None
                and row.ack_button.isVisibleTo(self) and row.ack_button.isEnabled()
                and notice.state == "accepted" and notice.expires_in_ms > 0):
            self.lesson_request_acknowledge.emit(*key)

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
            action_width = max(self._action_column_widths(), default=0)
            if not self._lesson_request_frame.isHidden():
                action_width = max(action_width, self._lesson_request_frame.minimumSizeHint().width())
            hint.setWidth(
                max(text_width, action_width)
                + margins.left() + margins.right() + 2 * self.frameWidth()
            )
        return hint

    def _action_column_widths(self) -> tuple[int, int]:
        """Measure the original grid independently of its current arrangement."""

        widths = [0, 0]
        for button, _row, column in self._action_positions:
            if not button.isHidden():
                widths[column] = max(
                    widths[column], button.minimumSizeHint().width(), button.minimumWidth()
                )
        return widths[0], widths[1]

    def _set_actions_single_column(self, single_column: bool) -> None:
        if self._actions_single_column == single_column:
            return
        self._actions_single_column = single_column
        # Move the existing layout items only. The widgets retain their
        # connections, native ownership, keyboard focus, and tab order.
        for button, _row, _column in self._action_positions:
            self._actions_layout.removeWidget(button)
        for index, (button, row, column) in enumerate(self._action_positions):
            self._actions_layout.addWidget(
                button, index if single_column else row, 0 if single_column else column
            )

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
            column_widths = self._action_column_widths()
            two_column_width = sum(column_widths) + (
                self._actions_layout.horizontalSpacing() if all(column_widths) else 0
            )
            self._set_actions_single_column(art and available < two_column_width)
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
            narrow = art and available < text_width + two_column_width + Space.LG
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
        if (self._creator_profile_key == "art" and status == "Opening…"
                and self._fallback_btn.hasFocus()):
            # Disabling a focused button otherwise lets Qt choose a different
            # action. Repeated input during the handoff must remain inert.
            self._status_label.setFocus(Qt.FocusReason.OtherFocusReason)
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
                "Change the Conversation meeting link"
                if self._meeting_configured
                else "Add a meeting link from any platform"
            )
        self._change_link_btn.setAccessibleName(accessible_name)
        self._change_link_btn.setAccessibleDescription(
            (
                f"Change the {service} meeting link used by Conversation."
                if service else "Change the meeting link used by Conversation."
            )
            if self._meeting_configured
            else "Add a public HTTPS meeting link for Conversation."
        )
        self._change_link_btn.setToolTip(
            f"Add or change the {service} meeting link used by Conversation."
            if service
            else "Add or change the meeting link used by Conversation."
        )

    def focus_primary_action(self) -> None:
        """Place keyboard focus on the safest useful Conversation action."""

        if self._creator_profile_key == "art":
            self._art_next_action().setFocus(Qt.FocusReason.ShortcutFocusReason)
            return
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
        self._sync_art_next_action()

    def _art_next_action(self) -> QWidget:
        """A saved meeting is the destination; native app discovery is not."""

        if not self._meeting_configured:
            return self._change_link_btn
        if self._launch_busy:
            return self._status_label
        if self._native_action_busy and self._native_focus_restore is not None:
            return self._app_status_label
        if (self._service_label == "Webex" and self._launch_status == "Opened externally"
                and self._bring_forward_btn.isEnabled()):
            return self._bring_forward_btn
        return self._fallback_btn

    def _sync_art_next_action(self) -> None:
        """Emphasize one existing action without moving focus or emitting intent."""

        art = self._creator_profile_key == "art"
        target = self._art_next_action() if art else None
        if target is self._status_label:
            # Keep the pending link action visible but disabled while its
            # status holds focus. Completion alone never takes focus back.
            target = self._fallback_btn
        for button in (self._change_link_btn, self._fallback_btn, self._bring_forward_btn):
            name = "PrimaryButton" if button is target else "GhostButton"
            if button.objectName() != name:
                button.setObjectName(name)
                button.style().unpolish(button)
                button.style().polish(button)
                button.update()
        self._status_label.setFocusPolicy(
            Qt.FocusPolicy.StrongFocus if art else Qt.FocusPolicy.NoFocus
        )
        service = self._service_label
        destination = f"{service} or your browser" if service else "its service or your browser"
        self._fallback_btn.setToolTip(
            f"Open the configured meeting link once in {destination}."
            f"{self._show_webex_advice()}"
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
        if self._creator_profile_key == "art" and not (
            self._meeting_configured and self._service_label == "Webex"
            and self._launch_status == "Opened externally"
        ):
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
        self._sync_art_next_action()

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
            and QApplication.focusWidget() is self._app_status_label
            and target.isVisible()
            and target.isEnabled()
        ):
            target.setFocus(Qt.FocusReason.OtherFocusReason)

    def set_creator_profile(self, profile) -> None:
        self._creator_profile_key = profile.key
        if profile.key != "art":
            self._shared_lesson_hosting = None
            self._clear_lesson_requests()
        self._sync_lesson_requests()
        self._render_audio_guidance()
        self._sync_native_actions()
        self._sync_art_layout()

    def set_shared_lesson_context(self, hosting: bool | None) -> None:
        """Explain an explicitly selected meeting lesson; never open anything."""

        if hosting is not None and not isinstance(hosting, bool):
            raise ValueError("Shared lesson context must be a room role or None.")
        current = hosting if self._creator_profile_key == "art" else None
        if current is None or current is not self._shared_lesson_hosting:
            self._clear_lesson_requests()
        self._shared_lesson_hosting = current
        self._sync_lesson_requests()
        self._render_audio_guidance()
        self._sync_art_layout()

    def _render_audio_guidance(self) -> None:
        service = self._service_label
        if self._creator_profile_key == "art":
            self._title_label.setText("Conversation")
            if self._shared_lesson_hosting is not None:
                meeting = service or "your meeting"
                if self._shared_lesson_hosting:
                    share = (
                        "Webex app: Share your YouTube window with Include computer sound. "
                        "Browser meeting: share the YouTube tab with tab audio. "
                        if service == "Webex" else
                        f"Share a YouTube browser window or tab with computer sound in {meeting}. "
                    )
                    self._mode_label.setText(
                        share
                        + "Keep faces visible there. Pause and resume in your browser when asked. "
                        "YouTube player volume changes the shared lesson; your meeting's speaker volume and microphone mute are yours."
                    )
                else:
                    self._mode_label.setText(
                        f"Watch the host's shared YouTube lesson and faces in {meeting}. "
                        "Ask the host to pause or resume when you need time; the host controls the browser. "
                        "Use your meeting's speaker volume for what you hear and microphone mute for your voice."
                    )
                return
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
