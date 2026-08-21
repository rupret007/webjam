"""Art's shared canvas panel: a status line and one obvious next action.

The panel renders immutable snapshots and emits semantic intent.  It decides
nothing -- whether a link is a real Drawpile invitation, whether Drawpile is
installed, and what a guest is allowed to do all belong to
:mod:`core.shared_canvas`.

There is deliberately no brush, no colour, no layer, and no canvas here.  Not
because those are hard, but because Drawpile already has them and a second,
worse copy inside WebJam would be a lie about where the painting happens.  A
guest therefore gets exactly one button.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
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

_NO_CANVAS_HEADLINE = "No shared canvas"
_GUEST_HINT = (
    "Optional. The canvas is a Drawpile session; WebJam only carries the "
    "invitation. Drawpile decides who may paint, and WebJam cannot see the "
    "canvas or who is on it."
)
_HOST_NO_DRAWPILE = (
    "Drawpile is not installed on this computer, so WebJam cannot open a "
    "shared canvas here. Everything else in this room keeps working."
)


class SharedCanvasDialog(QDialog):
    """One compact panel for the host's canvas choice and a guest's join."""

    host_in_drawpile_requested = Signal()
    share_requested = Signal(str)
    withdraw_requested = Signal()
    open_canvas_requested = Signal()
    install_drawpile_requested = Signal()

    def __init__(self, *, hosting: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hosting = bool(hosting)
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

        if self._hosting:
            self._build_host_controls(layout)
        else:
            self._build_guest_controls(layout)

        self._install_button = QPushButton("Get Drawpile")
        self._install_button.setObjectName("GhostButton")
        self._install_button.setAccessibleName("Get Drawpile")
        self._install_button.setAccessibleDescription(
            "Open the Drawpile download page in your browser. WebJam does not "
            "install anything for you."
        )
        self._install_button.clicked.connect(self.install_drawpile_requested.emit)
        self._install_button.setVisible(False)
        layout.addWidget(self._install_button)

        hint = QLabel(HOST_CANVAS_HINT if self._hosting else _GUEST_HINT)
        hint.setWordWrap(True)
        hint.setObjectName("SharedCanvasHint")
        layout.addWidget(hint)

    # -- construction --------------------------------------------------

    def _build_host_controls(self, layout: QVBoxLayout) -> None:
        self._host_button = QPushButton("Host in Drawpile")
        self._host_button.setObjectName("GhostButton")
        self._host_button.setAccessibleName("Host a canvas in Drawpile")
        self._host_button.setAccessibleDescription(
            "Open Drawpile on its Host page. Choose a Personal session so "
            "only people with your invitation can join, then copy the "
            "invitation from Session, Invite."
        )
        self._host_button.clicked.connect(self.host_in_drawpile_requested.emit)
        layout.addWidget(self._host_button)

        self._invite_input = QLineEdit()
        self._invite_input.setObjectName("SharedCanvasInviteInput")
        # A Drawpile invitation can embed the session password, so it is
        # handled like WebJam's own invitation field rather than as ordinary
        # visible text.
        self._invite_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._invite_input.setPlaceholderText("Paste the Drawpile invitation")
        self._invite_input.setAccessibleName("Drawpile invitation")
        self._invite_input.setAccessibleDescription(
            "Paste the link Drawpile copied from Session, Invite."
        )
        self._invite_input.returnPressed.connect(self._emit_share)
        layout.addWidget(self._invite_input)

        self._share_button = QPushButton("Share with the room")
        self._share_button.setObjectName("GhostButton")
        self._share_button.setAccessibleName("Share this canvas with the room")
        self._share_button.setAccessibleDescription(
            "Send this Drawpile invitation to everyone in the room, including "
            "anyone who joins later."
        )
        self._share_button.clicked.connect(self._emit_share)
        layout.addWidget(self._share_button)

        shared_row = QHBoxLayout()
        shared_row.setContentsMargins(0, 0, 0, 0)
        shared_row.setSpacing(Space.SM)
        self._open_button = QPushButton("Open canvas")
        self._open_button.setObjectName("GhostButton")
        self._open_button.setAccessibleName("Open the shared canvas")
        self._open_button.setAccessibleDescription(
            "Open Drawpile on the canvas this room is using."
        )
        self._open_button.clicked.connect(self.open_canvas_requested.emit)
        self._withdraw_button = QPushButton("Stop sharing")
        self._withdraw_button.setObjectName("GhostButton")
        self._withdraw_button.setAccessibleName("Stop sharing the canvas")
        self._withdraw_button.setAccessibleDescription(
            "Stop offering this canvas to the room. Drawpile keeps running "
            "and nobody is disconnected."
        )
        self._withdraw_button.clicked.connect(self.withdraw_requested.emit)
        shared_row.addWidget(self._open_button)
        shared_row.addWidget(self._withdraw_button)
        layout.addLayout(shared_row)

    def _build_guest_controls(self, layout: QVBoxLayout) -> None:
        # A guest has exactly one thing to do, so they get exactly one button.
        self._open_button = QPushButton("Open shared canvas")
        self._open_button.setObjectName("GhostButton")
        self._open_button.setAccessibleName("Open the shared canvas")
        self._open_button.setAccessibleDescription(
            "Open the host's Drawpile canvas in your own Drawpile."
        )
        self._open_button.clicked.connect(self.open_canvas_requested.emit)
        self._open_button.setEnabled(False)
        layout.addWidget(self._open_button)

    def _emit_share(self) -> None:
        value = self._invite_input.text()
        self._invite_input.clear()
        if value.strip():
            self.share_requested.emit(value)

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
            status = (
                "The room can open this canvas, including anyone who joins "
                "later. WebJam cannot see the canvas, so Drawpile shows who "
                "is actually painting."
            )
            if not snapshot.carries_password:
                status += (
                    " This invitation carries no session password. If you "
                    "hosted a Personal session, re-copy the invitation with "
                    "the password included."
                )
        else:
            status = NO_CANVAS_MESSAGE
        self._set_status(status)

        self._host_button.setEnabled(available)
        self._host_button.setVisible(not shared)
        self._invite_input.setEnabled(available)
        self._invite_input.setVisible(not shared)
        self._share_button.setEnabled(available)
        self._share_button.setVisible(not shared)
        self._open_button.setEnabled(available and shared)
        self._open_button.setVisible(shared)
        self._withdraw_button.setEnabled(shared)
        self._withdraw_button.setVisible(shared)
        self._install_button.setVisible(not available)

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
        self._set_status(snapshot.message)
        self._open_button.setEnabled(bool(snapshot.can_open))
        self._install_button.setVisible(
            state is SharedCanvasFollowState.NEEDS_DRAWPILE
        )

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
