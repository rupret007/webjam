"""UI-neutral ownership for one authenticated remote-session connection.

The desktop keeps invitation material typed from paste/file-open ingress to
this boundary.  A backend receives the object directly, performs enrollment,
and returns only non-secret loopback/path evidence.  The capability reference
is released immediately after that backend call returns.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from core.remote_invitation import RemoteInvitation
from core.session_transport import (
    ConnectionQuality,
    SessionRole,
    TransportPath,
)

LOGGER = logging.getLogger("webjam.services.remote_session")


class RemoteSessionPhase(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    CONNECTED = "connected"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


class RemoteSessionErrorCode(str, Enum):
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"
    ENROLLMENT_REJECTED = "enrollment_rejected"
    INVITATION_UNUSABLE = "invitation_unusable"
    TRANSPORT_FAILED = "transport_failed"
    STOP_FAILED = "stop_failed"


class RemoteBackendError(RuntimeError):
    """A backend failure containing only an allowlisted safe reason code."""

    def __init__(self, code: RemoteSessionErrorCode) -> None:
        super().__init__(code.value)
        self.code = RemoteSessionErrorCode(code)


@dataclass(frozen=True, slots=True)
class RemoteGuestConnection:
    """Non-secret facts produced after authenticated enrollment succeeds."""

    loopback_port: int
    path: TransportPath
    quality: ConnectionQuality
    generation: int

    def __post_init__(self) -> None:
        if not 1 <= self.loopback_port <= 65_535:
            raise ValueError("loopback_port is out of range")
        if self.generation < 1:
            raise ValueError("generation must be positive")
        object.__setattr__(self, "path", TransportPath(self.path))
        object.__setattr__(self, "quality", ConnectionQuality(self.quality))


@dataclass(frozen=True, slots=True)
class RemoteSessionSnapshot:
    phase: RemoteSessionPhase
    role: SessionRole
    generation: int
    loopback_port: int = 0
    path: TransportPath | None = None
    quality: ConnectionQuality = ConnectionQuality.UNKNOWN
    error_code: RemoteSessionErrorCode | None = None

    @property
    def invitation_retry_safe(self) -> bool:
        """Whether the same guest invitation is safe to try one more time.

        ``UNAVAILABLE`` is reserved for a sidecar that could not start before
        it received ``open_guest``. Every other guest failure is conservative:
        the reference service may have consumed the one-use enrollment value,
        so the controller must require a fresh invitation.
        """

        return bool(
            self.phase is RemoteSessionPhase.FAILED
            and self.role is SessionRole.GUEST
            and self.error_code is RemoteSessionErrorCode.UNAVAILABLE
        )

    @property
    def musician_status(self) -> str:
        if self.phase is RemoteSessionPhase.PREPARING:
            return "Finding the fastest path"
        if self.phase is RemoteSessionPhase.CONNECTED:
            return self.path.musician_label if self.path is not None else (
                "Your audio session is connected"
            )
        if self.phase is RemoteSessionPhase.FAILED:
            if self.error_code is RemoteSessionErrorCode.EXPIRED:
                return "This invitation expired"
            return "The host is temporarily unreachable"
        if self.phase is RemoteSessionPhase.STOPPING:
            return "Ending the secure connection"
        if self.phase is RemoteSessionPhase.STOPPED:
            return "Audio session ended"
        return "Preparing your jam"


class RemoteSessionBackend(Protocol):
    """Owned native transport boundary; implementations must be thread-safe."""

    def start_guest(
        self,
        invitation: RemoteInvitation,
        *,
        generation: int,
    ) -> RemoteGuestConnection: ...

    def stop(self) -> None: ...


class RemoteSessionRuntime:
    """Own one bounded guest-enrollment worker and publish immutable state."""

    def __init__(
        self,
        backend: RemoteSessionBackend,
        *,
        on_snapshot: Callable[[RemoteSessionSnapshot], None],
        schedule_callback: Callable[[Callable[[], None]], None] = lambda fn: fn(),
    ) -> None:
        self._backend = backend
        self._on_snapshot = on_snapshot
        self._schedule_callback = schedule_callback
        self._condition = threading.Condition(threading.RLock())
        self._worker: threading.Thread | None = None
        self._operation = 0
        self._pending_invitation: RemoteInvitation | None = None
        self._snapshot = RemoteSessionSnapshot(
            phase=RemoteSessionPhase.IDLE,
            role=SessionRole.GUEST,
            generation=1,
        )

    @property
    def snapshot(self) -> RemoteSessionSnapshot:
        with self._condition:
            return self._snapshot

    @property
    def active(self) -> bool:
        return self.snapshot.phase in {
            RemoteSessionPhase.PREPARING,
            RemoteSessionPhase.CONNECTED,
        }

    def start_guest(self, invitation: RemoteInvitation) -> bool:
        if not isinstance(invitation, RemoteInvitation):
            raise TypeError("invitation must be a RemoteInvitation")
        with self._condition:
            if self._snapshot.phase in {
                RemoteSessionPhase.PREPARING,
                RemoteSessionPhase.CONNECTED,
                RemoteSessionPhase.STOPPING,
            }:
                raise RuntimeError("remote session is already active")
            self._operation += 1
            operation = self._operation
            generation = max(1, self._snapshot.generation)
            if invitation.advisory_expired():
                self._pending_invitation = None
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.FAILED,
                    role=SessionRole.GUEST,
                    generation=generation,
                    error_code=RemoteSessionErrorCode.EXPIRED,
                )
                snapshot = self._snapshot
                self._condition.notify_all()
            else:
                self._pending_invitation = invitation
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.PREPARING,
                    role=SessionRole.GUEST,
                    generation=generation,
                )
                snapshot = self._snapshot
                worker = threading.Thread(
                    target=self._guest_worker,
                    args=(operation, generation),
                    name="webjam-remote-enrollment",
                    daemon=True,
                )
                self._worker = worker
        self._publish(snapshot)
        if snapshot.phase is RemoteSessionPhase.PREPARING:
            worker.start()
        return snapshot.phase is RemoteSessionPhase.PREPARING

    def stop(self) -> None:
        with self._condition:
            if self._snapshot.phase in {
                RemoteSessionPhase.IDLE,
                RemoteSessionPhase.STOPPED,
            }:
                self._pending_invitation = None
                return
            self._operation += 1
            self._pending_invitation = None
            generation = self._snapshot.generation
            self._snapshot = RemoteSessionSnapshot(
                phase=RemoteSessionPhase.STOPPING,
                role=self._snapshot.role,
                generation=generation,
            )
            stopping = self._snapshot
            self._condition.notify_all()
        self._publish(stopping)
        error: RemoteSessionErrorCode | None = None
        try:
            self._backend.stop()
        except Exception as exc:  # noqa: BLE001 - sanitize at ownership boundary
            LOGGER.error(
                "Remote session backend stop failed; exception_type=%s",
                type(exc).__name__,
            )
            error = RemoteSessionErrorCode.STOP_FAILED
        with self._condition:
            self._snapshot = RemoteSessionSnapshot(
                phase=(
                    RemoteSessionPhase.FAILED
                    if error is not None
                    else RemoteSessionPhase.STOPPED
                ),
                role=self._snapshot.role,
                generation=generation,
                error_code=error,
            )
            stopped = self._snapshot
            self._condition.notify_all()
        self._publish(stopped)

    def wait_until_settled(self, timeout: float = 5.0) -> RemoteSessionSnapshot:
        """Bounded test/shutdown aid; interactive code consumes callbacks."""

        if timeout <= 0 or timeout > 60:
            raise ValueError("timeout must be between zero and sixty seconds")
        import time

        deadline = time.monotonic() + timeout
        with self._condition:
            while self._snapshot.phase in {
                RemoteSessionPhase.PREPARING,
                RemoteSessionPhase.STOPPING,
            }:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("remote session did not settle in time")
                self._condition.wait(remaining)
            return self._snapshot

    def _guest_worker(self, operation: int, generation: int) -> None:
        with self._condition:
            invitation = self._pending_invitation
        if invitation is None:
            return
        connection: RemoteGuestConnection | None = None
        error: RemoteSessionErrorCode | None = None
        try:
            connection = self._backend.start_guest(
                invitation,
                generation=generation,
            )
            if not isinstance(connection, RemoteGuestConnection):
                raise TypeError("backend returned an invalid connection")
        except RemoteBackendError as exc:
            error = exc.code
        except Exception as exc:  # noqa: BLE001 - never log attacker text
            LOGGER.error(
                "Remote session backend failed; exception_type=%s",
                type(exc).__name__,
            )
            # A backend that cannot prove a failure happened before it entered
            # ``open_guest`` may already have handed the one-use enrollment
            # value to the service. Do not offer a capability replay.
            error = RemoteSessionErrorCode.INVITATION_UNUSABLE
        finally:
            # Drop the runtime's last invitation reference immediately after
            # enrollment returns. Backend implementations must do the same.
            invitation = None
            with self._condition:
                self._pending_invitation = None

        with self._condition:
            if operation != self._operation:
                if self._worker is threading.current_thread():
                    self._worker = None
                self._condition.notify_all()
                return
            if connection is not None:
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.CONNECTED,
                    role=SessionRole.GUEST,
                    generation=connection.generation,
                    loopback_port=connection.loopback_port,
                    path=connection.path,
                    quality=connection.quality,
                )
            else:
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.FAILED,
                    role=SessionRole.GUEST,
                    generation=generation,
                    error_code=(
                        error or RemoteSessionErrorCode.TRANSPORT_FAILED
                    ),
                )
            settled = self._snapshot
            self._worker = None
            self._condition.notify_all()
        self._publish(settled)

    def _publish(self, snapshot: RemoteSessionSnapshot) -> None:
        self._schedule_callback(
            lambda value=snapshot: self._on_snapshot(value)
        )

    def __repr__(self) -> str:
        return (
            "RemoteSessionRuntime("
            f"phase={self.snapshot.phase.value!r}, private=[redacted])"
        )
