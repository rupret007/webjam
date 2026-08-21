"""Session-scoped ownership of Art's reference video.

The coordinator is the seam between three things that must not know about each
other: the host transport in :mod:`core.reference_video`, the private peer
plane in :mod:`core.session_transfer`, and a Qt player. It owns none of their
policy. It decides only which role this computer is playing, forwards host
transport onto the peer plane, and drives a follower from what the host
published.

It holds no Qt types so it can be exercised headlessly with a fake player and
a fake host peer.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from core.reference_video import (
    ReferenceVideoError,
    ReferenceVideoFollower,
    ReferenceVideoFollowSnapshot,
    ReferenceVideoFollowState,
    ReferenceVideoHostController,
    ReferenceVideoPlayer,
    ReferenceVideoSnapshot,
    ReferenceVideoState,
    session_identity_signer,
)

LOGGER = logging.getLogger("webjam.qt.reference_video_coordinator")

PLAYER_UNAVAILABLE_MESSAGE = (
    "This computer cannot play video in WebJam, so the reference video is "
    "unavailable here. You can still talk and work in the room."
)
NOT_HOSTING_MESSAGE = "Only the session host can share a reference video."
NOT_FOLLOWING_MESSAGE = "Only a guest opens their own copy of the host's video."

# Maps host lifecycle onto the bounded projection guests may render. CLOSED
# leaves the room with no video rather than a stale final frame.
_PEER_STATES: dict[ReferenceVideoState, str] = {
    ReferenceVideoState.IDLE: "idle",
    ReferenceVideoState.READY: "ready",
    ReferenceVideoState.PLAYING: "playing",
    ReferenceVideoState.PAUSED: "paused",
    ReferenceVideoState.FAILED: "failed",
    ReferenceVideoState.CLOSED: "idle",
}


class ReferenceVideoCoordinator:
    """Own one room's reference video for whichever role this computer has."""

    def __init__(
        self,
        *,
        player_factory: Callable[[], ReferenceVideoPlayer],
        host_peer_provider: Callable[[], Any] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
        on_host_snapshot: Callable[[ReferenceVideoSnapshot], None] | None = None,
        on_follow_snapshot: (
            Callable[[ReferenceVideoFollowSnapshot], None] | None
        ) = None,
    ) -> None:
        self._player_factory = player_factory
        self._host_peer_provider = host_peer_provider
        self._clock = clock
        self._on_host_snapshot = on_host_snapshot
        self._on_follow_snapshot = on_follow_snapshot
        self._role = ""
        self._signer: Callable[[str], str] | None = None
        self._host: ReferenceVideoHostController | None = None
        self._follower: ReferenceVideoFollower | None = None
        self._player: ReferenceVideoPlayer | None = None
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

    def begin_host(self, *, session_id: str, session_key: str) -> None:
        """Take host ownership for one session's reference video."""

        self.end()
        self._signer = session_identity_signer(
            session_id=session_id, session_key=session_key
        )
        self._role = "host"

    def begin_guest(self, *, session_id: str, session_key: str) -> None:
        """Prepare to follow a host without opening any file yet."""

        self.end()
        signer = session_identity_signer(
            session_id=session_id, session_key=session_key
        )
        self._signer = signer
        self._role = "guest"
        # The follower exists from the start so hiding and host observation
        # work before anyone chooses a file.
        self._follower = ReferenceVideoFollower(identity_signer=signer, player=None)

    def end(self) -> None:
        """Release players and return this computer to the no-video path."""

        host = self._host
        if host is not None:
            try:
                host.close()
            except Exception:  # noqa: BLE001 - teardown must not raise
                LOGGER.debug("Reference video host close failed", exc_info=True)
            self._publish_unshared()
        follower = self._follower
        if follower is not None:
            try:
                follower.close_local_copy()
            except Exception:  # noqa: BLE001 - teardown must not raise
                LOGGER.debug("Reference video follower close failed", exc_info=True)
        self._host = None
        self._follower = None
        self._player = None
        self._signer = None
        self._role = ""
        self._publish_failed = False

    @property
    def player_surface(self):
        """The rendering widget of the player this room built, if any."""

        return getattr(self._player, "surface", None)

    # -- host transport ------------------------------------------------

    def share(self, path: str) -> ReferenceVideoSnapshot:
        return self._host_operation(lambda host: host.share(path))

    def play(self) -> ReferenceVideoSnapshot:
        return self._host_operation(lambda host: host.play())

    def pause(self) -> ReferenceVideoSnapshot:
        return self._host_operation(lambda host: host.pause())

    def stop(self) -> ReferenceVideoSnapshot:
        return self._host_operation(lambda host: host.stop())

    def seek(self, position_s: float) -> ReferenceVideoSnapshot:
        return self._host_operation(lambda host: host.seek(float(position_s)))

    def withdraw(self) -> ReferenceVideoSnapshot:
        return self._host_operation(lambda host: host.withdraw())

    @property
    def host_snapshot(self) -> ReferenceVideoSnapshot:
        host = self._host
        return host.snapshot if host is not None else ReferenceVideoSnapshot()

    def _host_operation(
        self, operation: Callable[[ReferenceVideoHostController], ReferenceVideoSnapshot]
    ) -> ReferenceVideoSnapshot:
        if not self.hosting:
            raise ReferenceVideoError(NOT_HOSTING_MESSAGE)
        snapshot = operation(self._host_controller())
        self._publish(snapshot)
        if self._on_host_snapshot is not None:
            self._on_host_snapshot(snapshot)
        return snapshot

    def _host_controller(self) -> ReferenceVideoHostController:
        if self._host is not None:
            return self._host
        signer = self._signer
        if signer is None:  # pragma: no cover - guarded by ``hosting``
            raise ReferenceVideoError(NOT_HOSTING_MESSAGE)
        self._host = ReferenceVideoHostController(
            self._build_player(),
            identity_signer=signer,
            is_host=lambda: self.hosting,
        )
        return self._host

    # -- follower ------------------------------------------------------

    def open_local_copy(self, path: str) -> ReferenceVideoFollowSnapshot:
        follower = self._require_follower()
        follower.set_player(self._build_player())
        return self._notify_follow(follower.open_local_copy(path))

    def close_local_copy(self) -> ReferenceVideoFollowSnapshot:
        return self._notify_follow(self._require_follower().close_local_copy())

    def set_hidden(self, hidden: bool) -> ReferenceVideoFollowSnapshot:
        return self._notify_follow(self._require_follower().set_hidden(bool(hidden)))

    @property
    def hidden(self) -> bool:
        follower = self._follower
        return bool(follower is not None and follower.hidden)

    @property
    def follow_snapshot(self) -> ReferenceVideoFollowSnapshot:
        follower = self._follower
        if follower is None:
            return ReferenceVideoFollowSnapshot()
        return follower.resolve(self._clock())

    def observe_host_state(self, session_state: object) -> None:
        """Record the newest host projection carried by the peer plane."""

        follower = self._follower
        if follower is None:
            return
        projection = getattr(session_state, "reference_video", None)
        follower.observe(projection, received_monotonic_s=self._clock())

    def _require_follower(self) -> ReferenceVideoFollower:
        follower = self._follower
        if follower is None or not self.following:
            raise ReferenceVideoError(NOT_FOLLOWING_MESSAGE)
        return follower

    def _notify_follow(
        self, snapshot: ReferenceVideoFollowSnapshot
    ) -> ReferenceVideoFollowSnapshot:
        if self._on_follow_snapshot is not None:
            self._on_follow_snapshot(snapshot)
        return snapshot

    # -- periodic work -------------------------------------------------

    def tick(self) -> None:
        """Advance whichever side this computer owns.

        A host samples its own player and republishes; a follower corrects
        local drift toward the host's position. Both are best-effort: a
        failure here must never interrupt the conversation.
        """

        if self.hosting and self._host is not None:
            snapshot = self._host.refresh()
            self._publish(snapshot)
            if self._on_host_snapshot is not None:
                self._on_host_snapshot(snapshot)
            return
        follower = self._follower
        if follower is None:
            return
        try:
            snapshot = follower.apply(self._clock())
        except ReferenceVideoError as exc:
            LOGGER.debug("Reference video follow failed: %s", exc)
            snapshot = follower.resolve(self._clock())
        self._notify_follow(snapshot)

    # -- peer plane ----------------------------------------------------

    def _publish(self, snapshot: ReferenceVideoSnapshot) -> None:
        publish = self._peer_publisher()
        if publish is None:
            return
        shared = bool(snapshot.shared)
        state = _PEER_STATES.get(snapshot.state, "failed")
        if state in {"idle", "failed"}:
            shared = False
        try:
            publish(
                state=state,
                shared=shared,
                source_display_name=snapshot.source_display_name if shared else "",
                identity_digest=snapshot.identity_digest if shared else "",
                position_s=snapshot.position_s if shared else 0.0,
                duration_s=snapshot.duration_s if shared else 0.0,
                needs_attention=bool(snapshot.needs_attention),
            )
        except Exception:  # noqa: BLE001 - peer boundary stays UI-optional
            if not self._publish_failed:
                LOGGER.warning("Reference video peer state could not be published")
            self._publish_failed = True
        else:
            self._publish_failed = False

    def _publish_unshared(self) -> None:
        publish = self._peer_publisher()
        if publish is None:
            return
        try:
            publish(state="idle", shared=False)
        except Exception:  # noqa: BLE001 - teardown must not raise
            LOGGER.debug("Reference video teardown publish failed", exc_info=True)

    def _peer_publisher(self):
        host_peer = self._host_peer_provider()
        publish = getattr(host_peer, "publish_reference_video_state", None)
        if not bool(getattr(host_peer, "active", False)) or not callable(publish):
            return None
        return publish

    # -- players -------------------------------------------------------

    def _build_player(self) -> ReferenceVideoPlayer:
        if self._player is not None:
            return self._player
        try:
            player = self._player_factory()
        except ReferenceVideoError:
            raise
        except Exception as exc:
            raise ReferenceVideoError(PLAYER_UNAVAILABLE_MESSAGE) from exc
        # Policy, stated where policy lives: the room's sound belongs to the
        # live audio path and to whatever meeting app is carrying the voices.
        # A reference video is the picture. Because the file is never routed
        # anywhere, every computer holds its own copy, so an unmuted one would
        # put a second soundtrack on top of the conversation on every machine.
        mute = getattr(player, "set_muted", None)
        if callable(mute):
            try:
                mute(True)
            except Exception:  # noqa: BLE001 - a silent player is the default
                LOGGER.debug("Reference video mute failed", exc_info=True)
        self._player = player
        return player


def follow_state_is_blocked(state: object) -> bool:
    """True when the host is sharing but this computer must not play."""

    try:
        return ReferenceVideoFollowState(state) in {
            ReferenceVideoFollowState.NEEDS_FILE,
            ReferenceVideoFollowState.MISMATCHED_FILE,
            ReferenceVideoFollowState.FILE_UNAVAILABLE,
            ReferenceVideoFollowState.HOST_ATTENTION,
            ReferenceVideoFollowState.STALLED,
        }
    except ValueError:
        return False


__all__ = [
    "NOT_FOLLOWING_MESSAGE",
    "NOT_HOSTING_MESSAGE",
    "PLAYER_UNAVAILABLE_MESSAGE",
    "ReferenceVideoCoordinator",
    "follow_state_is_blocked",
]
