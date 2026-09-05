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
from typing import Any

from core.shared_canvas import (
    CanvasLauncher,
    SharedCanvasError,
    SharedCanvasFollower,
    SharedCanvasFollowSnapshot,
    SharedCanvasFollowState,
    SharedCanvasHostController,
    SharedCanvasSnapshot,
)

LOGGER = logging.getLogger("webjam.qt.shared_canvas_coordinator")

LAUNCHER_UNAVAILABLE_MESSAGE = (
    "This computer cannot start Drawpile from WebJam, so the shared canvas is "
    "unavailable here. You can still talk and work in the room."
)
NOT_HOSTING_MESSAGE = "Only the session host can share a canvas."
NOT_FOLLOWING_MESSAGE = "Only a guest opens the host's canvas."


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
        # A room starts with no shared canvas, so absence is the only peer
        # projection already known to have been delivered. A callable peer
        # publisher is not delivery evidence: HostPeerSession returns None
        # until authenticated session control accepts the projection.
        self._published: tuple[bool, str, str, str] = (False, "", "", "")
        self._publish_failed = False

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

        host = self._host
        if host is not None:
            try:
                host.close()
            except Exception:  # noqa: BLE001 - teardown must not raise
                LOGGER.debug("Shared canvas host close failed", exc_info=True)
            self._publish_unshared()
        self._host = None
        self._follower = None
        self._launcher = None
        self._role = ""
        self._published = (False, "", "", "")
        self._publish_failed = False

    @property
    def launcher_available(self) -> bool:
        try:
            return bool(self._build_launcher().available())
        except SharedCanvasError:
            return False

    # -- host actions --------------------------------------------------

    def open_drawpile_to_host(self) -> SharedCanvasSnapshot:
        return self._host_operation(
            lambda host: host.open_drawpile_to_host(), publish=False
        )

    def share(self, invite_text: str) -> SharedCanvasSnapshot:
        return self._host_operation(lambda host: host.share(invite_text))

    def withdraw(self) -> SharedCanvasSnapshot:
        return self._host_operation(lambda host: host.withdraw())

    def open_canvas_as_host(self) -> SharedCanvasSnapshot:
        return self._host_operation(lambda host: host.open_canvas(), publish=False)

    @property
    def host_snapshot(self) -> SharedCanvasSnapshot:
        host = self._host
        if host is not None:
            return host.snapshot
        return SharedCanvasSnapshot(launcher_available=self.launcher_available)

    def _host_operation(
        self,
        operation: Callable[[SharedCanvasHostController], SharedCanvasSnapshot],
        *,
        publish: bool = True,
    ) -> SharedCanvasSnapshot:
        if not self.hosting:
            raise SharedCanvasError(NOT_HOSTING_MESSAGE)
        snapshot = operation(self._host_controller())
        if publish:
            projection = self._projection(snapshot)
            if projection != self._published and self._publish(projection):
                self._published = projection
        if self._on_host_snapshot is not None:
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
        self._notify_follow(follower.observe(projection))

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

    def tick(self) -> None:
        """Retry the current host projection until the peer plane accepts it.

        Sharing stays a local Drawpile choice even if the authenticated peer
        session is not ready yet. Remembering only an accepted projection
        ensures a bounded Art timer can carry that choice to late joiners
        without republishing it once delivery succeeds.
        """

        host = self._host
        if not self.hosting or host is None:
            return
        projection = self._projection(host.snapshot)
        if projection != self._published and self._publish(projection):
            self._published = projection

    def _projection(
        self, snapshot: SharedCanvasSnapshot
    ) -> tuple[bool, str, str, str]:
        host = self._host
        invite = host.invite() if host is not None else None
        shared = bool(snapshot.shared and invite is not None)
        return (
            shared,
            invite.join_url if shared and invite is not None else "",
            snapshot.server_label if shared else "",
            snapshot.session_label if shared else "",
        )

    def _publish(self, projection: tuple[bool, str, str, str]) -> bool:
        publish = self._peer_publisher()
        if publish is None:
            return False
        shared, join_url, server_label, session_label = projection
        try:
            accepted = publish(
                shared=shared,
                join_url=join_url,
                server_label=server_label,
                session_label=session_label,
            )
            if accepted is None:
                return False
        except Exception:  # noqa: BLE001 - peer boundary stays UI-optional
            if not self._publish_failed:
                LOGGER.warning("Shared canvas peer state could not be published")
            self._publish_failed = True
            return False
        else:
            self._publish_failed = False
            return True

    def _publish_unshared(self) -> None:
        publish = self._peer_publisher()
        if publish is None:
            return
        try:
            publish(shared=False)
        except Exception:  # noqa: BLE001 - teardown must not raise
            LOGGER.debug("Shared canvas teardown publish failed", exc_info=True)

    def _peer_publisher(self):
        host_peer = self._host_peer_provider()
        publish = getattr(host_peer, "publish_shared_canvas_state", None)
        if not bool(getattr(host_peer, "active", False)) or not callable(publish):
            return None
        return publish

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
                LOGGER.debug("Drawpile launcher unavailable", exc_info=True)
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
