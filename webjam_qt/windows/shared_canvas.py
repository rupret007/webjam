"""Art's shared canvas: one status line, one chip, and the room's pulse.

The panel renders immutable snapshots and emits semantic intent. It decides
nothing -- whether a link is a real Drawpile invitation, whether Drawpile is
installed, and what a guest may do all belong to :mod:`core.shared_canvas`.

The chip carries one primary action. Hosts can also change or stop an
accepted invitation through quiet controls. The masked paste field appears
after the explicit Host action or while changing an accepted invitation;
publication and retry never open Drawpile by themselves.

There is no brush, colour, or layer control here, and no canvas. Not because
they are hard, but because Drawpile already has them and a second, worse copy
inside WebJam would be a lie about where the painting happens.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.room_clock import RoomClockView
from core.shared_canvas import (
    HOST_CANVAS_HINT,
    NO_CANVAS_MESSAGE,
    SharedCanvasFollowSnapshot,
    SharedCanvasFollowState,
    SharedCanvasPendingAction,
    SharedCanvasSnapshot,
    SharedCanvasState,
)
from webjam_qt.theme.tokens import Space
from webjam_qt.widgets.room_clock_label import RoomClockLabel
from webjam_qt.widgets.status_chip import QuietAction, StatusChip

_NO_CANVAS_HEADLINE = "No shared canvas"
_GUEST_HINT = (
    "Optional. The canvas is a Drawpile session; WebJam only carries the "
    "invitation. Drawpile decides who may paint, and WebJam cannot see the "
    "canvas or who is on it."
)
_HOST_NO_DRAWPILE = "Install Drawpile to paint together."
_GUEST_NO_DRAWPILE = "Install Drawpile to join the canvas, or just talk."
_SHARED_STATUS = (
    "The room can open this canvas, including anyone who joins later. WebJam "
    "cannot see the canvas, so Drawpile shows who is actually painting."
)
_NO_PASSWORD_STATUS = (
    " This invitation carries no session password. If you hosted a Personal "
    "session, re-copy it with the password included."
)
_HOSTING_STARTED_STATUS = (
    "Host a Personal session in Drawpile, then copy its "
    "invitation from Session, Invite and paste it below."
)


class _CanvasHeadline(QLabel):
    """Elide bounded canvas labels while preserving their full spoken text."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = str(text)
        self.setAccessibleDescription(self._full_text)
        self.setToolTip(self._full_text)
        self._render_text()

    def _render_text(self) -> None:
        super().setText(self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.contentsRect().width()
        ))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_text()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, "_full_text"):
            self._render_text()


class SharedCanvasDialog(QDialog):
    """One compact panel: what is true, one thing to press, and the pulse."""

    host_in_drawpile_requested = Signal()
    share_requested = Signal(str)
    withdraw_requested = Signal()
    retry_publication_requested = Signal()
    open_canvas_requested = Signal()
    install_drawpile_requested = Signal()
    return_requested = Signal()

    def __init__(self, *, hosting: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hosting = bool(hosting)
        # Set once the host has actually opened Drawpile, which is the only
        # moment a paste field has anything to receive.
        self._hosting_started = False
        self._changing_invitation = False
        self._last_host_snapshot = None
        self._last_follow_snapshot = None
        self._room_available = True
        self.setWindowTitle("Shared Canvas")
        self.setModal(False)
        # Deliberately narrow: an artist needs Drawpile and the faces of the
        # people they are painting with on screen at the same time, so WebJam's
        # own chrome stays out of the way.
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        layout.setSpacing(Space.SM)

        self._headline = _CanvasHeadline(_NO_CANVAS_HEADLINE)
        self._headline.setObjectName("SharedCanvasHeadline")
        self._headline.setAccessibleName("Shared canvas")
        layout.addWidget(self._headline)

        self._status = QLabel(NO_CANVAS_MESSAGE)
        self._status.setWordWrap(True)
        self._status.setObjectName("SharedCanvasStatus")
        self._status.setAccessibleName("Shared canvas status")
        layout.addWidget(self._status)

        # Where the room is, for the person painting. This is the whole point
        # of a shared clock: a painter should not have to leave the canvas to
        # find out what bar the band is on.
        self._room_clock = RoomClockLabel()
        self._room_clock.setVisible(False)
        layout.addWidget(self._room_clock)

        self._chip = StatusChip()
        self._chip.clicked.connect(self._chip_pressed)
        layout.addWidget(self._chip)

        if self._hosting:
            self._invite_input = QLineEdit()
            self._invite_input.setObjectName("SharedCanvasInviteInput")
            # A Drawpile invitation can embed the session password, so it is
            # handled like WebJam's own invitation field rather than as
            # ordinary visible text.
            self._invite_input.setEchoMode(QLineEdit.EchoMode.Password)
            self._invite_input.setPlaceholderText("Paste the Drawpile invitation")
            self._invite_input.setAccessibleName("Drawpile invitation")
            self._invite_input.setAccessibleDescription(
                "Paste the link Drawpile copied from Session, Invite."
            )
            self._invite_input.returnPressed.connect(self._emit_share)
            self._invite_input.textChanged.connect(self._sync_share_action)
            self._invite_input.setVisible(False)
            layout.addWidget(self._invite_input)

        self._change_button = QuietAction()
        self._change_button.clicked.connect(self._toggle_invitation_change)
        self._quiet = QuietAction()
        self._quiet.clicked.connect(self._quiet_pressed)
        quiet_row = QHBoxLayout()
        quiet_row.addWidget(self._change_button)
        quiet_row.addStretch(1)
        quiet_row.addWidget(self._quiet)
        layout.addLayout(quiet_row)

        hint = QLabel(HOST_CANVAS_HINT if self._hosting else _GUEST_HINT)
        hint.setWordWrap(True)
        hint.setObjectName("SharedCanvasHint")
        layout.addWidget(hint)

        if self._hosting:
            self.set_host_snapshot(SharedCanvasSnapshot())
        else:
            self.set_follow_snapshot(SharedCanvasFollowSnapshot())

    # -- intent --------------------------------------------------------

    def _chip_pressed(self) -> None:
        action = self._chip.property("action")
        if action == "return":
            self.return_requested.emit()
        elif action == "install":
            self.install_drawpile_requested.emit()
        elif action == "host":
            self._hosting_started = True
            self.host_in_drawpile_requested.emit()
        elif action == "share":
            self._emit_share()
        elif action == "retry_publication":
            self.retry_publication_requested.emit()
        elif action == "open":
            self.open_canvas_requested.emit()

    def _quiet_pressed(self) -> None:
        if self._quiet.property("action") == "withdraw":
            self.withdraw_requested.emit()

    def _emit_share(self) -> None:
        value = self._invite_input.text()
        self._invite_input.clear()
        if value.strip():
            self._changing_invitation = False
            self.share_requested.emit(value)

    def _toggle_invitation_change(self) -> None:
        snapshot = self._last_host_snapshot
        if not self._hosting or snapshot is None:
            return
        self._changing_invitation = not self._changing_invitation
        self._invite_input.clear()
        self.set_host_snapshot(snapshot)
        if self._changing_invitation:
            self._invite_input.setFocus()

    def _sync_share_action(self) -> None:
        """Once there is something pasted, the chip becomes Share."""

        # ``isHidden`` rather than ``isVisible``: this reflects the field's own
        # state whether or not the panel's window has been shown yet.
        if not self._hosting or self._invite_input.isHidden():
            return
        has_text = bool(self._invite_input.text().strip())
        if has_text or self._changing_invitation:
            self._chip.setProperty("action", "share")
            self._chip.offer(
                "Share with the room",
                "Send this Drawpile invitation to everyone in the room, "
                "including anyone who joins later.",
            )
            self._chip.setEnabled(has_text)
        else:
            self._offer_host_action()

    def _offer_host_action(self) -> None:
        self._chip.setProperty("action", "host")
        self._chip.offer(
            "Host a canvas in Drawpile",
            "Open Drawpile on its Host page. Choose a Personal session so "
            "only people with your invitation can join.",
        )

    # -- rendering -----------------------------------------------------

    def set_host_snapshot(self, snapshot: SharedCanvasSnapshot) -> None:
        if not self._hosting:
            return
        self._last_host_snapshot = snapshot
        available = bool(snapshot.launcher_available)
        shared = bool(snapshot.shared)
        pending = snapshot.pending_action
        retrying_share = pending is SharedCanvasPendingAction.SHARE
        retrying_withdraw = pending is SharedCanvasPendingAction.WITHDRAW
        has_pending = retrying_share or retrying_withdraw
        if has_pending or not shared:
            self._changing_invitation = False

        if shared:
            self._headline.setText(
                f"Canvas offered: {snapshot.session_label} at {snapshot.server_label}"
            )
        elif has_pending:
            self._headline.setText("Canvas sharing needs attention")
        elif snapshot.state is SharedCanvasState.FAILED:
            self._headline.setText("Shared canvas needs attention")
        else:
            self._headline.setText(_NO_CANVAS_HEADLINE)

        if snapshot.error:
            status = snapshot.error
        elif retrying_share:
            status = "Sharing the invitation is not confirmed. Try sharing again."
        elif retrying_withdraw:
            status = "Stopping the share is not confirmed. Try again."
        elif not available:
            status = _HOST_NO_DRAWPILE
        elif shared:
            status = _SHARED_STATUS
            if not snapshot.carries_password:
                status += _NO_PASSWORD_STATUS
        elif self._hosting_started:
            status = _HOSTING_STARTED_STATUS
        else:
            status = NO_CANVAS_MESSAGE
        self._set_status(status)

        # The paste field has nothing to receive until Drawpile is open, so it
        # is not shown before then.
        show_invite = not has_pending and (
            self._changing_invitation
            or available and not shared and self._hosting_started
        )
        self._invite_input.setVisible(show_invite)
        if not show_invite:
            self._invite_input.clear()

        if has_pending:
            self._chip.setProperty("action", "retry_publication")
            self._chip.offer(
                "Try sharing again" if retrying_share else "Try stop sharing",
                status,
                tone=StatusChip.RECOVERY,
            )
            self._chip.setEnabled(snapshot.can_retry_publication)
        elif self._changing_invitation:
            self._sync_share_action()
        elif not available:
            self._chip.setProperty("action", "install")
            self._chip.offer(
                "Install Drawpile",
                "Open Drawpile's download page. WebJam does not install "
                "anything for you.",
                tone=StatusChip.RECOVERY,
            )
        elif shared:
            self._chip.setProperty("action", "open")
            self._chip.offer(
                "Open canvas",
                "Open Drawpile on the canvas this room is using.",
            )
        elif show_invite:
            self._sync_share_action()
        else:
            self._offer_host_action()

        if shared and not has_pending:
            self._change_button.offer(
                "Cancel change" if self._changing_invitation else "Change invitation",
                "Keep the current invitation." if self._changing_invitation
                else "Paste a different Drawpile invitation. The current canvas "
                     "stays offered until the room accepts the change.",
            )
        else:
            self._change_button.withdraw()

        if retrying_share or shared and not retrying_withdraw:
            self._quiet.setProperty("action", "withdraw")
            self._quiet.offer(
                "Stop sharing",
                "Stop offering this canvas to the room, including any invitation "
                "waiting to be shared. Drawpile keeps running.",
            )
            if retrying_share:
                self._quiet.setEnabled(snapshot.can_retry_publication)
        else:
            self._quiet.withdraw()

    def set_room_available(self, available: bool) -> None:
        """A retained canvas offer is usable only in its current guest room."""

        if self._hosting or self._room_available == bool(available):
            return
        self._room_available = bool(available)
        if self._last_follow_snapshot is not None:
            self.set_follow_snapshot(self._last_follow_snapshot)

    def set_follow_snapshot(self, snapshot: SharedCanvasFollowSnapshot) -> None:
        if self._hosting:
            return
        self._last_follow_snapshot = snapshot
        if not self._room_available:
            self._headline.setText("Waiting for the room")
            self._set_status(
                "WebJam cannot confirm the host's current canvas. "
                "Your work in Drawpile can stay open. Return to the room "
                "to check the connection."
            )
            self._chip.setProperty("action", "return")
            self._chip.offer(
                "Return to room", self._status.text(), tone=StatusChip.RECOVERY,
            )
            self._quiet.withdraw()
            self._change_button.withdraw()
            self._room_clock.setVisible(False)
            return
        state = snapshot.state
        if state is SharedCanvasFollowState.NO_CANVAS:
            self._headline.setText(_NO_CANVAS_HEADLINE)
        elif snapshot.session_label:
            self._headline.setText(
                f"{snapshot.session_label} at {snapshot.server_label}"
            )
        else:
            self._headline.setText("Shared canvas")

        if state is SharedCanvasFollowState.NEEDS_DRAWPILE:
            self._set_status(_GUEST_NO_DRAWPILE)
            self._chip.setProperty("action", "install")
            self._chip.offer(
                "Install Drawpile",
                "Open Drawpile's download page, then open the canvas.",
                tone=StatusChip.RECOVERY,
            )
        else:
            self._set_status(snapshot.message)
            if snapshot.can_open:
                self._chip.setProperty("action", "open")
                self._chip.offer(
                    "Open shared canvas"
                    if state is not SharedCanvasFollowState.OPENED
                    else "Reopen shared canvas",
                    "Open the host's Drawpile canvas in your own Drawpile.",
                )
            else:
                # No canvas, or one WebJam could not read. Either way there is
                # nothing to press, so nothing is offered.
                self._chip.withdraw()
        self._quiet.withdraw()
        self._change_button.withdraw()

    def set_room_clock(self, view: RoomClockView) -> None:
        """Show the room's pulse, or nothing at all when it has none.

        A room with no song and no running video honestly has no clock, and a
        hopeful zero would be worse than an absent line.
        """

        self._room_clock.set_view(view)
        self._room_clock.setVisible(view.present and self._room_available)

    def _set_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.setAccessibleDescription(text)


__all__ = ["SharedCanvasDialog"]
