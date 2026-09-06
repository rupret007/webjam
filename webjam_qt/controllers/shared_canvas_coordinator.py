"""Session-scoped ownership of Art's shared Drawpile canvas.

The coordinator is the seam between three things that must not know about
each other: the host controller in :mod:`core.shared_canvas`, the private peer
plane in :mod:`core.session_transfer`, and whatever launches a real Drawpile.
It owns none of their policy.  It decides only which role this computer is
playing, forwards the host's canvas choice onto the peer plane, and feeds a
follower what the host published.

It holds no Qt types, so it can be exercised headlessly with a fake launcher
and a fake host peer.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from core.drawpile import DrawpileError, parse_canvas_invite
from core.session_transfer import SharedCanvasSessionSnapshot
from core.shared_canvas import (
    CanvasLauncher,
    SharedCanvasError,
    SharedCanvasFollower,
    SharedCanvasFollowSnapshot,
    SharedCanvasFollowState,
    SharedCanvasHostController,
    SharedCanvasPendingAction,
    SharedCanvasSnapshot,
)

LOGGER = logging.getLogger("webjam.qt.shared_canvas_coordinator")

LAUNCHER_UNAVAILABLE_MESSAGE = (
    "This computer cannot start Drawpile from WebJam, so the shared canvas is "
    "unavailable here. You can still talk and work in the room."
)
NOT_HOSTING_MESSAGE = "Only the session host can share a canvas."
NOT_FOLLOWING_MESSAGE = "Only a guest opens the host's canvas."

SHARE_NOT_CONFIRMED_MESSAGE = (
    "Sharing this canvas with the room is not confirmed. Try sharing again."
)
WITHDRAW_NOT_CONFIRMED_MESSAGE = (
    "Stopping this canvas offer is not confirmed. Artists may still receive "
    "the previous invitation. Try stop sharing again."
)


@dataclass(frozen=True, repr=False)
class _PendingPublication:
    action: SharedCanvasPendingAction
    projection: SharedCanvasSessionSnapshot


class SharedCanvasCoordinator:
    """Own one room's shared canvas for whichever role this computer has."""

    def __init__(
        self,
        *,
        launcher_factory: Callable[[], CanvasLauncher],
        host_peer_provider: Callable[[], Any] = lambda: None,
        on_host_snapshot: Callable[[SharedCanvasSnapshot], None] | None = None,
        on_follow_snapshot: (
            Callable[[SharedCanvasFollowSnapshot], None] | None
        ) = None,
    ) -> None:
        self._launcher_factory = launcher_factory
        self._host_peer_provider = host_peer_provider
        self._on_host_snapshot = on_host_snapshot
        self._on_follow_snapshot = on_follow_snapshot
        self._role = ""
        self._host: SharedCanvasHostController | None = None
        self._follower: SharedCanvasFollower | None = None
        self._launcher: CanvasLauncher | None = None
        self._generation = 0
        self._intent_generation = 0
        self._pending: _PendingPublication | None = None
        self._inflight: _PendingPublication | None = None
        self._publication_peer = None
        self._observed_canvas = None

    # -- lifecycle -----------------------------------------------------

    @property
    def role(self) -> str:
        return self._role

    @property
    def hosting(self) -> bool:
        return self._role == "host"

    @property
    def following(self) -> bool:
        return self._role == "guest"

    def begin_host(self) -> None:
        """Take host ownership of which canvas this room points at."""

        self.end()
        self._role = "host"

    def begin_guest(self) -> None:
        """Prepare to receive the host's canvas without opening anything."""

        self.end()
        self._role = "guest"
        # The follower exists from the start so a canvas shared before this
        # panel is ever opened still reaches the artist.
        self._follower = SharedCanvasFollower(launcher=self._build_launcher())

    @property
    def bound(self) -> bool:
        return bool(self._role)

    def end(self) -> None:
        """Return this computer to the no-canvas path.

        Drawpile is a separate program the artist started; leaving a WebJam
        room must not close their painting. Only WebJam's own pointer to the
        canvas is released.
        """

        # Retire the room before any foreign close/publication call can
        # re-enter. An old completion must not change the next room.
        self._generation += 1
        self._intent_generation += 1
        host, peer = self._host, self._publication_peer
        self._host = None
        self._follower = None
        self._launcher = None
        self._role = ""
        self._pending = None
        self._inflight = None
        self._publication_peer = None
        self._observed_canvas = None
        generation = self._generation
        if host is not None:
            try:
                host.close()
            except Exception:  # teardown has no private exception detail
                LOGGER.debug("Shared canvas host close failed")
        if peer is not None and generation == self._generation:
            try:
                current = self._host_peer_provider()
                if (generation == self._generation and current is peer
                        and bool(getattr(peer, "active", False))):
                    peer.publish_shared_canvas_state(shared=False)
            except Exception:
                LOGGER.debug("Shared canvas teardown publish failed")

    @property
    def launcher_available(self) -> bool:
        try:
            return bool(self._build_launcher().available())
        except SharedCanvasError:
            return False

    # -- host actions --------------------------------------------------

    def open_drawpile_to_host(self) -> SharedCanvasSnapshot:
        return self._host_operation(
            lambda host: host.open_drawpile_to_host()
        )

    def share(self, invite_text: str) -> SharedCanvasSnapshot:
        self._require_host()
        try:
            invite = parse_canvas_invite(invite_text)
        except DrawpileError as exc:
            raise SharedCanvasError(str(exc)) from exc
        projection = SharedCanvasSessionSnapshot(
            shared=True, join_url=invite.join_url,
            server_label=invite.server_label, session_label=invite.session_label,
        )
        return self._request_publication(SharedCanvasPendingAction.SHARE, projection)

    def withdraw(self) -> SharedCanvasSnapshot:
        self._require_host()
        return self._request_publication(
            SharedCanvasPendingAction.WITHDRAW, SharedCanvasSessionSnapshot(),
        )

    def retry_publication(self) -> SharedCanvasSnapshot:
        self._require_host()
        pending = self._pending
        if pending is None or pending is self._inflight:
            return self.host_snapshot
        return self._attempt_publication(pending)

    def open_canvas_as_host(self) -> SharedCanvasSnapshot:
        return self._host_operation(lambda host: host.open_canvas())

    @property
    def host_snapshot(self) -> SharedCanvasSnapshot:
        host = self._host
        snapshot = (host.snapshot if host is not None else
                    SharedCanvasSnapshot(launcher_available=self.launcher_available))
        pending = self._pending
        if pending is None:
            return snapshot
        return replace(
            snapshot, pending_action=pending.action,
            can_retry_publication=self.hosting and pending is not self._inflight,
            error=(SHARE_NOT_CONFIRMED_MESSAGE
                   if pending.action is SharedCanvasPendingAction.SHARE
                   else WITHDRAW_NOT_CONFIRMED_MESSAGE),
        )

    def _require_host(self) -> None:
        if not self.hosting:
            raise SharedCanvasError(NOT_HOSTING_MESSAGE)

    def _host_operation(
        self,
        operation: Callable[[SharedCanvasHostController], SharedCanvasSnapshot],
    ) -> SharedCanvasSnapshot:
        self._require_host()
        generation = self._generation
        host = self._host_controller()
        operation(host)
        return self._notify_host(generation, host)

    def _notify_host(self, generation, host, *, intent=None) -> SharedCanvasSnapshot:
        snapshot = self.host_snapshot
        if (generation == self._generation and host is self._host and self.hosting
                and (intent is None or intent == self._intent_generation)
                and self._on_host_snapshot is not None):
            self._on_host_snapshot(snapshot)
        return snapshot

    def _host_controller(self) -> SharedCanvasHostController:
        if self._host is not None:
            return self._host
        self._host = SharedCanvasHostController(
            self._build_launcher(),
            is_host=lambda: self.hosting,
        )
        return self._host

    # -- follower ------------------------------------------------------

    def open_canvas(self) -> SharedCanvasFollowSnapshot:
        return self._notify_follow(self._require_follower().open_canvas())

    @property
    def follow_snapshot(self) -> SharedCanvasFollowSnapshot:
        follower = self._follower
        if follower is None:
            return SharedCanvasFollowSnapshot()
        return follower.resolve()

    def observe_host_state(self, session_state: object) -> None:
        """Record the newest canvas projection carried by the peer plane."""

        follower = self._follower
        if follower is None:
            return
        projection = getattr(session_state, "shared_canvas", None)
        snapshot = follower.observe(projection)
        if follower is self._follower:
            self._observed_canvas = projection
            self._notify_follow(snapshot)

    def canvas_is_current(self, session_state: object) -> bool:
        """A new room receipt must be observed before its canvas can open."""

        return bool(
            self.following and self._observed_canvas is not None
            and self._observed_canvas == getattr(session_state, "shared_canvas", None)
        )

    def _require_follower(self) -> SharedCanvasFollower:
        follower = self._follower
        if follower is None or not self.following:
            raise SharedCanvasError(NOT_FOLLOWING_MESSAGE)
        return follower

    def _notify_follow(
        self, snapshot: SharedCanvasFollowSnapshot
    ) -> SharedCanvasFollowSnapshot:
        if self._on_follow_snapshot is not None:
            self._on_follow_snapshot(snapshot)
        return snapshot

    # -- peer plane ----------------------------------------------------

    def _request_publication(self, action, projection) -> SharedCanvasSnapshot:
        self._host_controller()
        self._intent_generation += 1
        pending = _PendingPublication(action, projection)
        self._pending = pending
        return self._attempt_publication(pending)

    @staticmethod
    def _matching_receipt(receipt, requested) -> bool:
        return type(receipt) is SharedCanvasSessionSnapshot and all(
            getattr(receipt, name) == getattr(requested, name)
            for name in ("shared", "join_url", "server_label", "session_label")
        )

    def _attempt_publication(self, pending) -> SharedCanvasSnapshot:
        generation, intent = self._generation, self._intent_generation
        host = self._host
        self._inflight = pending
        # A publisher may pump UI events. Disable the visible retry before
        # entering it, then honor any newer intent or End from that callback.
        self._notify_host(generation, host, intent=intent)
        if not (generation == self._generation and intent == self._intent_generation
                and host is self._host and self.hosting and pending is self._pending):
            if self._inflight is pending:
                self._inflight = None
            return self.host_snapshot
        peer = None
        accepted = False
        try:
            peer = self._host_peer_provider()
            publish = getattr(peer, "publish_shared_canvas_state", None)
            if (generation == self._generation and intent == self._intent_generation
                    and host is self._host and self.hosting and pending is self._pending
                    and bool(getattr(peer, "active", False)) and callable(publish)):
                self._publication_peer = peer
                projection = pending.projection
                receipt = publish(
                    shared=projection.shared, join_url=projection.join_url,
                    server_label=projection.server_label,
                    session_label=projection.session_label,
                )
                accepted = self._matching_receipt(receipt, projection)
        except Exception:  # payloads and transport details stay private
            accepted = False
        current = (generation == self._generation and intent == self._intent_generation
                   and host is self._host and self.hosting and pending is self._pending)
        if current and accepted:
            try:
                accepted = (self._host_peer_provider() is peer
                            and bool(getattr(peer, "active", False)))
            except Exception:
                accepted = False
            # The provider may itself retire the room or supersede the intent.
            current = (generation == self._generation and intent == self._intent_generation
                       and host is self._host and self.hosting and pending is self._pending)
            if current and accepted:
                if pending.action is SharedCanvasPendingAction.SHARE:
                    host.share(pending.projection.join_url)
                else:
                    host.withdraw()
                if (generation == self._generation and intent == self._intent_generation
                        and pending is self._pending):
                    self._pending = None
        if self._inflight is pending:
            self._inflight = None
        return self._notify_host(generation, host, intent=intent)

    # -- launcher ------------------------------------------------------

    def _build_launcher(self) -> CanvasLauncher:
        """Return a launcher, substituting one that can only say "no".

        A computer that cannot even construct a launcher is a computer with no
        canvas, which is a first-class state in this room rather than an
        error. Raising here instead would let an optional add-on break binding
        on the path that renders the whole session.
        """

        if self._launcher is None:
            try:
                self._launcher = self._launcher_factory()
            except Exception:  # noqa: BLE001 - reported as "no canvas here"
                LOGGER.debug("Drawpile launcher unavailable")
                self._launcher = _UnavailableLauncher()
        return self._launcher


class _UnavailableLauncher:
    """A launcher for a computer that cannot start Drawpile at all."""

    def available(self) -> bool:
        return False

    def open_host_page(self) -> None:
        raise SharedCanvasError(LAUNCHER_UNAVAILABLE_MESSAGE)

    def open_canvas(self, invite: object) -> None:
        raise SharedCanvasError(LAUNCHER_UNAVAILABLE_MESSAGE)


def follow_state_is_blocked(state: object) -> bool:
    """True when a canvas is shared but this computer must not open it."""

    try:
        return SharedCanvasFollowState(state) in {
            SharedCanvasFollowState.NEEDS_DRAWPILE,
            SharedCanvasFollowState.UNREADABLE,
        }
    except ValueError:
        return False


__all__ = [
    "LAUNCHER_UNAVAILABLE_MESSAGE",
    "NOT_FOLLOWING_MESSAGE",
    "NOT_HOSTING_MESSAGE",
    "SharedCanvasCoordinator",
    "follow_state_is_blocked",
]
