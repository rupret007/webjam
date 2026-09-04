"""UI-neutral ownership for one authenticated remote-session connection.

The desktop keeps invitation material typed from paste/file-open ingress to
this boundary.  A backend receives the object directly, performs enrollment,
and returns only non-secret loopback/path evidence.  The capability reference
is released immediately after that backend call returns.
"""

from __future__ import annotations

import logging
import math
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

# The native guest backend owns a 30-second sidecar-start boundary and a
# separate 30-second authenticated-enrollment boundary.  This outer guard is
# intentionally a little wider: it catches a backend or platform primitive
# that fails to honor either inner timeout without racing a legitimate result.
DEFAULT_GUEST_JOIN_TIMEOUT_SECONDS = 65.0


class RemoteSessionPhase(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    CONNECTED = "connected"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


class RemoteSessionStage(str, Enum):
    """Small, secret-free progress vocabulary for one guest join."""

    CONTACTING_HOST = "contacting_host"
    SECURING_CONNECTION = "securing_connection"
    OPENING_JAMULUS = "opening_jamulus"
    CONNECTED = "connected"
    NEEDS_ATTENTION = "needs_attention"


class RemoteSessionErrorCode(str, Enum):
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"
    ENROLLMENT_REJECTED = "enrollment_rejected"
    INVITATION_UNUSABLE = "invitation_unusable"
    TRANSPORT_FAILED = "transport_failed"
    TIMED_OUT = "timed_out"
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
    # Kept last so existing positional construction remains source-compatible.
    stage: RemoteSessionStage | None = None

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
            if self.stage is RemoteSessionStage.SECURING_CONNECTION:
                return "Securing connection"
            return "Contacting host"
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
        join_timeout_seconds: float = DEFAULT_GUEST_JOIN_TIMEOUT_SECONDS,
    ) -> None:
        timeout = float(join_timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0.0 or timeout > 300.0:
            raise ValueError("join_timeout_seconds must be between zero and 300")
        self._backend = backend
        self._on_snapshot = on_snapshot
        self._schedule_callback = schedule_callback
        self._join_timeout_seconds = timeout
        self._condition = threading.Condition(threading.RLock())
        self._worker: threading.Thread | None = None
        self._watchdog: threading.Thread | None = None
        self._watchdog_cancel: threading.Event | None = None
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
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("remote session cleanup is still active")
            self._operation += 1
            operation = self._operation
            generation = max(
                1,
                self._snapshot.generation
                + (self._snapshot.phase is not RemoteSessionPhase.IDLE),
            )
            if invitation.advisory_expired():
                self._pending_invitation = None
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.FAILED,
                    role=SessionRole.GUEST,
                    generation=generation,
                    error_code=RemoteSessionErrorCode.EXPIRED,
                    stage=RemoteSessionStage.NEEDS_ATTENTION,
                )
                snapshot = self._snapshot
                self._condition.notify_all()
            else:
                self._pending_invitation = invitation
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.PREPARING,
                    role=SessionRole.GUEST,
                    generation=generation,
                    stage=RemoteSessionStage.CONTACTING_HOST,
                )
                snapshot = self._snapshot
                worker = threading.Thread(
                    target=self._guest_worker,
                    args=(operation, generation),
                    name="webjam-remote-enrollment",
                    daemon=True,
                )
                self._worker = worker
                watchdog_cancel = threading.Event()
                watchdog = threading.Thread(
                    target=self._watch_guest_join,
                    args=(operation, generation, watchdog_cancel),
                    name="webjam-remote-join-watchdog",
                    daemon=True,
                )
                self._watchdog = watchdog
                self._watchdog_cancel = watchdog_cancel
        self._publish(snapshot)
        if snapshot.phase is RemoteSessionPhase.PREPARING:
            watchdog.start()
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
            self._cancel_watchdog_locked()
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
                stage=(
                    RemoteSessionStage.NEEDS_ATTENTION
                    if error is not None
                    else None
                ),
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
            if (
                invitation is not None
                and operation == self._operation
                and self._snapshot.phase is RemoteSessionPhase.PREPARING
            ):
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.PREPARING,
                    role=SessionRole.GUEST,
                    generation=generation,
                    stage=RemoteSessionStage.SECURING_CONNECTION,
                )
                securing = self._snapshot
            else:
                securing = None
        if invitation is None:
            return
        if securing is not None:
            self._publish(securing)
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
            self._cancel_watchdog_locked()
            if connection is not None:
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.CONNECTED,
                    role=SessionRole.GUEST,
                    generation=connection.generation,
                    loopback_port=connection.loopback_port,
                    path=connection.path,
                    quality=connection.quality,
                    stage=RemoteSessionStage.CONNECTED,
                )
            else:
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.FAILED,
                    role=SessionRole.GUEST,
                    generation=generation,
                    error_code=(
                        error or RemoteSessionErrorCode.TRANSPORT_FAILED
                    ),
                    stage=RemoteSessionStage.NEEDS_ATTENTION,
                )
            settled = self._snapshot
            self._worker = None
            self._condition.notify_all()
        self._publish(settled)

    def _expire_guest_join(self, operation: int, generation: int) -> None:
        """Retire one hung enrollment and prevent its late result from winning."""

        with self._condition:
            if (
                operation != self._operation
                or self._snapshot.phase is not RemoteSessionPhase.PREPARING
            ):
                return
            # The backend may already have entered open_guest.  Invalidate the
            # operation before cleanup and require a fresh one-use invitation.
            self._operation += 1
            self._pending_invitation = None
            self._watchdog = None
            self._watchdog_cancel = None
            self._snapshot = RemoteSessionSnapshot(
                phase=RemoteSessionPhase.FAILED,
                role=SessionRole.GUEST,
                generation=generation,
                error_code=RemoteSessionErrorCode.TIMED_OUT,
                stage=RemoteSessionStage.NEEDS_ATTENTION,
            )
            expired = self._snapshot
            self._condition.notify_all()
        self._publish(expired)

        def cleanup() -> None:
            try:
                self._backend.stop()
            except Exception as exc:  # noqa: BLE001 - no backend text is safe
                LOGGER.error(
                    "Timed-out remote backend cleanup failed; exception_type=%s",
                    type(exc).__name__,
                )

        threading.Thread(
            target=cleanup,
            daemon=True,
            name="webjam-remote-timeout-cleanup",
        ).start()

    def _watch_guest_join(
        self,
        operation: int,
        generation: int,
        cancelled: threading.Event,
    ) -> None:
        if not cancelled.wait(self._join_timeout_seconds):
            self._expire_guest_join(operation, generation)

    def _cancel_watchdog_locked(self) -> None:
        cancelled = self._watchdog_cancel
        self._watchdog = None
        self._watchdog_cancel = None
        if cancelled is not None:
            cancelled.set()

    def _publish(self, snapshot: RemoteSessionSnapshot) -> None:
        self._schedule_callback(
            lambda value=snapshot: self._on_snapshot(value)
        )

    def __repr__(self) -> str:
        return (
            "RemoteSessionRuntime("
            f"phase={self.snapshot.phase.value!r}, private=[redacted])"
        )
