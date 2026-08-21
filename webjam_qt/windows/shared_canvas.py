"""Art's shared canvas: one status line, one chip, and the room's pulse.

The panel renders immutable snapshots and emits semantic intent. It decides
nothing -- whether a link is a real Drawpile invitation, whether Drawpile is
installed, and what a guest may do all belong to :mod:`core.shared_canvas`.

Following ADR 0002, it offers exactly one thing to press at a time. The chip's
label *is* the status, so "Install Drawpile" and "Open shared canvas" are the
same control at different moments rather than two buttons competing. A guest
therefore has one verb and a host has one verb per step, and the paste field
only appears once hosting in Drawpile has actually been started -- there is
nothing to paste before that.

There is no brush, colour, or layer control here, and no canvas. Not because
they are hard, but because Drawpile already has them and a second, worse copy
inside WebJam would be a lie about where the painting happens.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from core.room_clock import RoomClockView
from core.shared_canvas import (
    HOST_CANVAS_HINT,
    NO_CANVAS_MESSAGE,
    SharedCanvasFollowSnapshot,
    SharedCanvasFollowState,
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
    "Drawpile is opening. Host a Personal session there, then copy its "
    "invitation from Session, Invite and paste it below."
)


class SharedCanvasDialog(QDialog):
    """One compact panel: what is true, one thing to press, and the pulse."""

    host_in_drawpile_requested = Signal()
    share_requested = Signal(str)
    withdraw_requested = Signal()
    open_canvas_requested = Signal()
    install_drawpile_requested = Signal()

    def __init__(self, *, hosting: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hosting = bool(hosting)
        # Set once the host has actually opened Drawpile, which is the only
        # moment a paste field has anything to receive.
        self._hosting_started = False
        self.setWindowTitle("Shared Canvas")
        self.setModal(False)
        # Deliberately narrow: an artist needs Drawpile and the faces of the
        # people they are painting with on screen at the same time, so WebJam's
        # own chrome stays out of the way.
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        layout.setSpacing(Space.SM)

        self._headline = QLabel(_NO_CANVAS_HEADLINE)
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

        self._quiet = QuietAction()
        self._quiet.clicked.connect(self._quiet_pressed)
        layout.addWidget(self._quiet)

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
        if action == "install":
            self.install_drawpile_requested.emit()
        elif action == "host":
            self._hosting_started = True
            self.host_in_drawpile_requested.emit()
        elif action == "share":
            self._emit_share()
        elif action == "open":
            self.open_canvas_requested.emit()

    def _quiet_pressed(self) -> None:
        if self._quiet.property("action") == "withdraw":
            self.withdraw_requested.emit()

    def _emit_share(self) -> None:
        value = self._invite_input.text()
        self._invite_input.clear()
        if value.strip():
            self.share_requested.emit(value)

    def _sync_share_action(self) -> None:
        """Once there is something pasted, the chip becomes Share."""

        # ``isHidden`` rather than ``isVisible``: this reflects the field's own
        # state whether or not the panel's window has been shown yet.
        if not self._hosting or self._invite_input.isHidden():
            return
        if self._invite_input.text().strip():
            self._chip.setProperty("action", "share")
            self._chip.offer(
                "Share with the room",
                "Send this Drawpile invitation to everyone in the room, "
                "including anyone who joins later.",
            )
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
        available = bool(snapshot.launcher_available)
        shared = bool(snapshot.shared)

        if shared:
            self._headline.setText(
                f"Painting on {snapshot.session_label} at {snapshot.server_label}"
            )
        elif snapshot.state is SharedCanvasState.FAILED:
            self._headline.setText("Shared canvas needs attention")
        else:
            self._headline.setText(_NO_CANVAS_HEADLINE)

        if snapshot.error:
            status = snapshot.error
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
        show_invite = available and not shared and self._hosting_started
        self._invite_input.setVisible(show_invite)
        if not show_invite:
            self._invite_input.clear()

        if not available:
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

        if shared:
            self._quiet.setProperty("action", "withdraw")
            self._quiet.offer(
                "Stop sharing",
                "Stop offering this canvas to the room. Drawpile keeps "
                "running and nobody is disconnected.",
            )
        else:
            self._quiet.withdraw()

    def set_follow_snapshot(self, snapshot: SharedCanvasFollowSnapshot) -> None:
        if self._hosting:
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

    def set_room_clock(self, view: RoomClockView) -> None:
        """Show the room's pulse, or nothing at all when it has none.

        A room with no song and no running video honestly has no clock, and a
        hopeful zero would be worse than an absent line.
        """

        self._room_clock.set_view(view)
        self._room_clock.setVisible(view.present)

    def _set_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.setAccessibleDescription(text)


__all__ = ["SharedCanvasDialog"]
