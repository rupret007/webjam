"""Read-only room discovery/following on the existing private-LAN peer plane.

No capture engine, originals directory, upload, device or recording recovery
is constructed here. A non-Art result is handed back to the existing Music
startup owner; it never grants recording authority to this observer.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable

from core.network_invite import BandInvite, _validate_private_peer_host
from core.session_transfer import (
    SessionCredentials,
    SessionPeerClient,
    SessionStateSnapshot,
    SessionTransferError,
)


class LanRoomGuest:
    def __init__(
        self,
        invite: BandInvite,
        *,
        display_name: str,
        on_state: Callable,
        on_loss: Callable,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(invite, BandInvite) or not invite.peer_enabled:
            raise ValueError("A complete private invitation is required.")
        _validate_private_peer_host(invite.host)
        self.invite = invite
        self._name = display_name
        self._installation_id = str(uuid.uuid4())
        self._on_state, self._on_loss, self._clock = on_state, on_loss, clock
        self.client = SessionPeerClient(
            invite.host,
            invite.peer_port,
            credentials=SessionCredentials(invite.session_id, invite.invite_token),
            timeout_s=2.0,
        )
        self._enrollment = None
        self._stop = threading.Event()
        self._thread = None
        self._last_seen = None
        self._started_at = None
        self.last_state = None

    @property
    def connection_available(self) -> bool:
        return (
            not self._stop.is_set()
            and self._last_seen is not None
            and 0 <= self._clock() - self._last_seen < 5.0
        )

    def start(self) -> None:
        if self._thread is not None or self._stop.is_set():
            return
        self._started_at = self._clock()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="webjam-room-guest"
        )
        self._thread.start()

    def poll_once(self):
        if self._stop.is_set():
            return None
        if self._enrollment is None:
            self._enrollment = self.client.enroll(self._installation_id, self._name)
        if self._stop.is_set():
            return None
        state = self.client.state(self._enrollment)
        if self._stop.is_set():
            return None
        if (
            not isinstance(state, SessionStateSnapshot)
            or state.session_id != self.invite.session_id
        ):
            raise SessionTransferError("The host returned a different room.")
        self.last_state = state
        self._last_seen = self._clock()
        # Every successful receipt counts once. Replaying a cached UI state
        # must never refresh the video follower's network age.
        self._on_state(self, state)
        return state

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:  # private server details stay out of logs/UI
                if not self._stop.is_set() and not self.connection_available:
                    origin = (
                        self._last_seen
                        if self._last_seen is not None
                        else self._started_at
                    )
                    terminal = origin is not None and self._clock() - origin >= 30.0
                    self._on_loss(self, terminal)
                    if terminal:
                        self.stop()
                        break
            self._stop.wait(0.5)

    def stop(self) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.5)
            if thread.is_alive():
                return False
        self.last_state = None
        self._enrollment = None
        self._last_seen = None
        return True
