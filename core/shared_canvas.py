"""The shared canvas for Art: one Drawpile session, brokered by WebJam.

Art's other optional add-on, the reference video, is played by WebJam itself.
The canvas deliberately is not.  Real-time collaborative painting is a solved
open-source problem -- Drawpile already has the operational transform, the
MyPaint and Krita-style brush engines, layers, ORA and PSD export, and tablet
pressure -- and a canvas widget invented inside WebJam would be a worse toy
pretending to be a tool.  So WebJam does for Drawpile exactly what it does for
Jamulus: it finds the real program, launches it, and carries the one piece of
joining information a guest would otherwise have to be sent separately.

That produces a short, honest contract:

* The host hosts in Drawpile.  WebJam opens Drawpile on its Host page and
  stops there, because Drawpile's host flow asks for a title, a password, and
  a server, and answering those on someone's behalf would be a guess.  The
  recommended shape is Drawpile's default **Personal** session, which is
  password-protected, rather than a public listed one.
* The host pastes the invitation Drawpile produced back into WebJam, which
  parses it, and publishes it to the room over the same authenticated peer
  plane that carries the reference video.  A guest who joined with one WebJam
  invitation therefore receives the canvas too, including one who joins late.
* A guest opens that canvas in their own Drawpile.  WebJam launches it; the
  drawing, the account decision, and the connection are Drawpile's.

Everything fails closed.  No Drawpile means WebJam says so and offers the
install, never a blank surface implying a canvas is open.  An unparseable
projection is refused rather than handed to a launcher.  WebJam never reports
that the canvas is *open* on anyone else's computer, because it cannot see
that, and it never posts a session anywhere on the artist's behalf.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from core.drawpile import (
    NOT_A_CANVAS_INVITE_MESSAGE,
    CanvasInvite,
    DrawpileError,
    parse_canvas_invite,
)

HOST_ONLY_CANVAS_MESSAGE = "Only the session host can share or stop the canvas."
NO_CANVAS_MESSAGE = "No shared canvas. Talk and work as usual."
NEEDS_DRAWPILE_MESSAGE = (
    "The host is painting on a shared Drawpile canvas. Install Drawpile to "
    "join it, or keep working here and just talk."
)
CANVAS_READY_MESSAGE = (
    "A shared canvas is ready. Open it to paint in Drawpile alongside the room."
)
CANVAS_OPENED_MESSAGE = (
    "Drawpile was opened on the shared canvas. WebJam cannot see the canvas, "
    "so Drawpile itself shows who is painting."
)
CANVAS_UNREADABLE_MESSAGE = (
    "The host shared a canvas WebJam could not read, so it will not open "
    "anything. Ask the host to share the Drawpile invitation again."
)
HOST_NEEDS_DRAWPILE_MESSAGE = (
    "Install Drawpile to host a shared canvas. Everything else in this room "
    "keeps working without it."
)
HOST_CANVAS_HINT = (
    "WebJam does not paint the strokes; Drawpile does. Host a Personal "
    "session so only people with your invitation can join, then paste the "
    "Drawpile invitation here to share it with the room."
)


class SharedCanvasError(RuntimeError):
    """A bounded, credential-free shared-canvas failure safe to show."""


@runtime_checkable
class CanvasLauncher(Protocol):
    """The seam a real Drawpile process launcher and test fakes both satisfy."""

    def available(self) -> bool:
        """Whether a real Drawpile executable was found on this computer."""

    def open_host_page(self) -> None:
        """Open Drawpile on its own Host page."""

    def open_canvas(self, invite: CanvasInvite) -> None:
        """Open Drawpile joined to one already-parsed canvas invitation."""


@runtime_checkable
class HostCanvasProjection(Protocol):
    """Host-published canvas truth a guest may act on.

    ``core.session_transfer.SharedCanvasSessionSnapshot`` satisfies this
    structurally, which keeps the wire schema out of this module's import
    graph and this module out of the transfer layer's.
    """

    @property
    def shared(self) -> bool: ...

    @property
    def join_url(self) -> str: ...

    @property
    def server_label(self) -> str: ...

    @property
    def session_label(self) -> str: ...


class SharedCanvasState(str, Enum):
    """Host-owned shared canvas lifecycle."""

    #: No canvas.  This is the first-class "just talk and work" path, not a
    #: degraded one.
    IDLE = "idle"
    SHARED = "shared"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SharedCanvasSnapshot:
    """Host-local canvas truth, safe to render and to project.

    The join URL is deliberately absent: it can carry a Drawpile session
    password, so it stays behind :meth:`SharedCanvasHostController.invite`
    rather than travelling inside a value that panels render and logs repeat.
    """

    state: SharedCanvasState = SharedCanvasState.IDLE
    shared: bool = False
    server_label: str = ""
    session_label: str = ""
    carries_password: bool = False
    launcher_available: bool = False
    error: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.state is SharedCanvasState.FAILED or bool(self.error)


class SharedCanvasHostController:
    """Host-only ownership of which Drawpile canvas the room is pointed at.

    The controller runs no server and holds no Drawpile credential.  It
    launches the real program, remembers one parsed invitation, and refuses
    every share or withdraw request that does not come from the host.
    """

    def __init__(
        self,
        launcher: CanvasLauncher,
        *,
        is_host: Callable[[], bool],
        on_change: Callable[[SharedCanvasSnapshot], None] | None = None,
    ) -> None:
        self._launcher = launcher
        self._is_host = is_host
        self._on_change = on_change
        self._lock = threading.RLock()
        self._state = SharedCanvasState.IDLE
        self._invite: CanvasInvite | None = None
        self._error = ""

    # -- reads ---------------------------------------------------------

    @property
    def snapshot(self) -> SharedCanvasSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def invite(self) -> CanvasInvite | None:
        """Return the shared invitation for publication, or ``None``."""

        with self._lock:
            return self._invite if self._state is SharedCanvasState.SHARED else None

    def _snapshot_locked(self) -> SharedCanvasSnapshot:
        shared = self._state is SharedCanvasState.SHARED and self._invite is not None
        invite = self._invite if shared else None
        return SharedCanvasSnapshot(
            state=self._state,
            shared=shared,
            server_label=invite.server_label if invite is not None else "",
            session_label=invite.session_label if invite is not None else "",
            carries_password=bool(invite is not None and invite.carries_password),
            launcher_available=self._launcher_available(),
            error=self._error,
        )

    def _launcher_available(self) -> bool:
        try:
            return bool(self._launcher.available())
        except Exception:  # noqa: BLE001 - a probe must never break the room
            return False

    def _notify(self, snapshot: SharedCanvasSnapshot) -> SharedCanvasSnapshot:
        if self._on_change is not None:
            self._on_change(snapshot)
        return snapshot

    def _require_host(self) -> None:
        if not self._is_host():
            raise SharedCanvasError(HOST_ONLY_CANVAS_MESSAGE)

    def _fail_locked(self, message: str) -> SharedCanvasSnapshot:
        self._state = SharedCanvasState.FAILED
        self._error = str(message).strip() or "The shared canvas couldn't continue."
        self._invite = None
        return self._notify(self._snapshot_locked())

    # -- host actions --------------------------------------------------

    def open_drawpile_to_host(self) -> SharedCanvasSnapshot:
        """Open Drawpile's Host page so the artist can host their own session.

        The launcher is asked directly rather than probed first, because only
        it knows *why* it cannot run: a missing install and a build that
        cannot launch Drawpile at all need different recoveries, and a
        pre-check here would flatten both into one guess.
        """

        self._require_host()
        with self._lock:
            try:
                self._launcher.open_host_page()
            except (DrawpileError, SharedCanvasError):
                raise
            except Exception as exc:
                raise SharedCanvasError(
                    "WebJam couldn't start Drawpile on this computer."
                ) from exc
            return self._snapshot_locked()

    def share(self, invite_text: object) -> SharedCanvasSnapshot:
        """Parse one Drawpile invitation and point the room at it."""

        self._require_host()
        with self._lock:
            if self._state is SharedCanvasState.CLOSED:
                raise SharedCanvasError("This canvas session has ended.")
            try:
                invite = parse_canvas_invite(invite_text)
            except DrawpileError as exc:
                # A bad paste is a correctable mistake, not a broken canvas.
                # Raising keeps whatever was already shared intact.
                raise SharedCanvasError(str(exc)) from exc
            self._invite = invite
            self._error = ""
            self._state = SharedCanvasState.SHARED
            return self._notify(self._snapshot_locked())

    def withdraw(self) -> SharedCanvasSnapshot:
        """Stop pointing the room at a canvas, without closing anyone's Drawpile."""

        self._require_host()
        with self._lock:
            if self._state is SharedCanvasState.CLOSED:
                return self._snapshot_locked()
            self._invite = None
            self._error = ""
            self._state = SharedCanvasState.IDLE
            return self._notify(self._snapshot_locked())

    def open_canvas(self) -> SharedCanvasSnapshot:
        """Reopen the shared canvas in Drawpile on the host's own computer."""

        with self._lock:
            invite = self._invite
            if self._state is not SharedCanvasState.SHARED or invite is None:
                raise SharedCanvasError("No canvas is shared yet.")
            try:
                self._launcher.open_canvas(invite)
            except (DrawpileError, SharedCanvasError):
                raise
            except Exception:
                return self._fail_locked(
                    "WebJam couldn't open Drawpile on this computer."
                )
            return self._snapshot_locked()

    def close(self) -> SharedCanvasSnapshot:
        with self._lock:
            self._state = SharedCanvasState.CLOSED
            self._invite = None
            self._error = ""
            return self._notify(self._snapshot_locked())


class SharedCanvasFollowState(str, Enum):
    """What a guest's computer can honestly say about the shared canvas."""

    NO_CANVAS = "no_canvas"
    NEEDS_DRAWPILE = "needs_drawpile"
    UNREADABLE = "unreadable"
    READY = "ready"
    OPENED = "opened"


_FOLLOW_MESSAGES: dict[SharedCanvasFollowState, str] = {
    SharedCanvasFollowState.NO_CANVAS: NO_CANVAS_MESSAGE,
    SharedCanvasFollowState.NEEDS_DRAWPILE: NEEDS_DRAWPILE_MESSAGE,
    SharedCanvasFollowState.UNREADABLE: CANVAS_UNREADABLE_MESSAGE,
    SharedCanvasFollowState.READY: CANVAS_READY_MESSAGE,
    SharedCanvasFollowState.OPENED: CANVAS_OPENED_MESSAGE,
}


@dataclass(frozen=True, slots=True)
class SharedCanvasFollowSnapshot:
    """A guest's bounded view of the host's canvas."""

    state: SharedCanvasFollowState = SharedCanvasFollowState.NO_CANVAS
    can_open: bool = False
    server_label: str = ""
    session_label: str = ""
    message: str = NO_CANVAS_MESSAGE

    @property
    def blocked(self) -> bool:
        """True when a canvas is shared but this computer cannot open it."""

        return self.state in {
            SharedCanvasFollowState.NEEDS_DRAWPILE,
            SharedCanvasFollowState.UNREADABLE,
        }


class SharedCanvasFollower:
    """A guest's side of the shared canvas.

    It exposes no share and no withdraw: which canvas the room uses is the
    host's decision, and a guest that could republish would be able to point
    the room at a canvas the host never chose.
    """

    def __init__(self, *, launcher: CanvasLauncher) -> None:
        self._launcher = launcher
        self._lock = threading.RLock()
        self._invite: CanvasInvite | None = None
        self._unreadable = False
        self._opened = False

    # -- host truth ----------------------------------------------------

    def observe(self, projection: object) -> SharedCanvasFollowSnapshot:
        """Record the newest host projection, refusing anything unreadable."""

        with self._lock:
            shared = bool(getattr(projection, "shared", False))
            join_url = str(getattr(projection, "join_url", "") or "")
            if projection is None or not shared or not join_url:
                self._invite = None
                self._unreadable = False
                self._opened = False
                return self._resolve_locked()
            try:
                invite = parse_canvas_invite(join_url)
            except DrawpileError:
                # The projection came from another computer. A value this one
                # cannot parse must stop here rather than reach a launcher.
                self._invite = None
                self._unreadable = True
                self._opened = False
                return self._resolve_locked()
            if self._invite is None or invite.join_url != self._invite.join_url:
                # A new canvas is not one this computer has opened yet.
                self._opened = False
            self._invite = invite
            self._unreadable = False
            return self._resolve_locked()

    # -- guest actions -------------------------------------------------

    def open_canvas(self) -> SharedCanvasFollowSnapshot:
        """Open the host's canvas in this computer's own Drawpile."""

        with self._lock:
            invite = self._invite
            if invite is None:
                raise SharedCanvasError(
                    CANVAS_UNREADABLE_MESSAGE if self._unreadable else NO_CANVAS_MESSAGE
                )
            try:
                self._launcher.open_canvas(invite)
            except (DrawpileError, SharedCanvasError):
                raise
            except Exception as exc:
                raise SharedCanvasError(
                    "WebJam couldn't open Drawpile on this computer."
                ) from exc
            self._opened = True
            return self._resolve_locked()

    def resolve(self) -> SharedCanvasFollowSnapshot:
        with self._lock:
            return self._resolve_locked()

    def _launcher_available(self) -> bool:
        try:
            return bool(self._launcher.available())
        except Exception:  # noqa: BLE001 - a probe must never break the room
            return False

    def _resolve_locked(self) -> SharedCanvasFollowSnapshot:
        if self._unreadable:
            return _follow(SharedCanvasFollowState.UNREADABLE)
        invite = self._invite
        if invite is None:
            return _follow(SharedCanvasFollowState.NO_CANVAS)
        if not self._launcher_available():
            return _follow(
                SharedCanvasFollowState.NEEDS_DRAWPILE,
                server_label=invite.server_label,
                session_label=invite.session_label,
            )
        state = (
            SharedCanvasFollowState.OPENED
            if self._opened
            else SharedCanvasFollowState.READY
        )
        return _follow(
            state,
            can_open=True,
            server_label=invite.server_label,
            session_label=invite.session_label,
        )


def _follow(
    state: SharedCanvasFollowState,
    *,
    can_open: bool = False,
    server_label: str = "",
    session_label: str = "",
) -> SharedCanvasFollowSnapshot:
    return SharedCanvasFollowSnapshot(
        state=state,
        can_open=can_open,
        server_label=server_label,
        session_label=session_label,
        message=_FOLLOW_MESSAGES[state],
    )


__all__ = [
    "CANVAS_OPENED_MESSAGE",
    "CANVAS_READY_MESSAGE",
    "CANVAS_UNREADABLE_MESSAGE",
    "HOST_CANVAS_HINT",
    "HOST_NEEDS_DRAWPILE_MESSAGE",
    "HOST_ONLY_CANVAS_MESSAGE",
    "NEEDS_DRAWPILE_MESSAGE",
    "NOT_A_CANVAS_INVITE_MESSAGE",
    "NO_CANVAS_MESSAGE",
    "CanvasLauncher",
    "HostCanvasProjection",
    "SharedCanvasError",
    "SharedCanvasFollowSnapshot",
    "SharedCanvasFollowState",
    "SharedCanvasFollower",
    "SharedCanvasHostController",
    "SharedCanvasSnapshot",
    "SharedCanvasState",
]
