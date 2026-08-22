"""Authoritative, privacy-safe session lifecycle truth.

The Qt UI, Jamulus process supervisor, and recording coordinator each own
different pieces of work.  This module is deliberately smaller than those
components: it owns the *meaning* of a session transition and keeps a bounded
timeline suitable for a support bundle.  It never stores invitations, device
names, addresses, filenames, or musician content.

The lifecycle is intentionally useful without a GUI.  A caller can ask
whether a transition is valid, record an idempotent state update, and expose a
safe snapshot to diagnostics.  It does not turn a running child process into a
false ``connected`` state: only the controller may promote the lifecycle to
``CONNECTED`` after its real roster/RPC truth is satisfied.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from core.redaction import redact_text


class SessionLifecyclePhase(str, Enum):
    """The one user-meaningful session lifecycle vocabulary."""

    IDLE = "idle"
    PREPARING = "preparing"
    CHECKING_PERMISSIONS = "checking_permissions"
    RUNNING_PREFLIGHT = "running_preflight"
    STARTING_HOST = "starting_host"
    WAITING_FOR_REACHABILITY = "waiting_for_reachability"
    READY_TO_SHARE = "ready_to_share"
    JOINING = "joining"
    CONNECTED = "connected"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    ENDING = "ending"
    FINALIZING_RECORDINGS = "finalizing_recordings"
    COMPLETED = "completed"
    FAILED_RECOVERABLE = "failed_recoverable"
    FAILED_FINAL = "failed_final"


_TERMINAL = frozenset(
    {
        SessionLifecyclePhase.COMPLETED,
        SessionLifecyclePhase.FAILED_FINAL,
    }
)

_ALLOWED: dict[SessionLifecyclePhase, frozenset[SessionLifecyclePhase]] = {
    SessionLifecyclePhase.IDLE: frozenset(
        {
            SessionLifecyclePhase.PREPARING,
            SessionLifecyclePhase.CHECKING_PERMISSIONS,
            SessionLifecyclePhase.RUNNING_PREFLIGHT,
            SessionLifecyclePhase.STARTING_HOST,
            SessionLifecyclePhase.JOINING,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.FAILED_FINAL,
        }
    ),
    SessionLifecyclePhase.PREPARING: frozenset(
        {
            SessionLifecyclePhase.CHECKING_PERMISSIONS,
            SessionLifecyclePhase.RUNNING_PREFLIGHT,
            SessionLifecyclePhase.STARTING_HOST,
            SessionLifecyclePhase.JOINING,
            SessionLifecyclePhase.ENDING,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.FAILED_FINAL,
        }
    ),
    SessionLifecyclePhase.CHECKING_PERMISSIONS: frozenset(
        {
            SessionLifecyclePhase.RUNNING_PREFLIGHT,
            SessionLifecyclePhase.STARTING_HOST,
            SessionLifecyclePhase.JOINING,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.ENDING,
        }
    ),
    SessionLifecyclePhase.RUNNING_PREFLIGHT: frozenset(
        {
            SessionLifecyclePhase.STARTING_HOST,
            SessionLifecyclePhase.JOINING,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.ENDING,
        }
    ),
    SessionLifecyclePhase.STARTING_HOST: frozenset(
        {
            SessionLifecyclePhase.WAITING_FOR_REACHABILITY,
            SessionLifecyclePhase.READY_TO_SHARE,
            SessionLifecyclePhase.CONNECTED,
            SessionLifecyclePhase.DEGRADED,
            SessionLifecyclePhase.RECONNECTING,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.FAILED_FINAL,
            SessionLifecyclePhase.ENDING,
        }
    ),
    SessionLifecyclePhase.WAITING_FOR_REACHABILITY: frozenset(
        {
            SessionLifecyclePhase.READY_TO_SHARE,
            SessionLifecyclePhase.CONNECTED,
            SessionLifecyclePhase.DEGRADED,
            SessionLifecyclePhase.RECONNECTING,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.FAILED_FINAL,
            SessionLifecyclePhase.ENDING,
        }
    ),
    SessionLifecyclePhase.READY_TO_SHARE: frozenset(
        {
            SessionLifecyclePhase.CONNECTED,
            SessionLifecyclePhase.DEGRADED,
            SessionLifecyclePhase.RECONNECTING,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.ENDING,
        }
    ),
    SessionLifecyclePhase.JOINING: frozenset(
        {
            SessionLifecyclePhase.CONNECTED,
            SessionLifecyclePhase.DEGRADED,
            SessionLifecyclePhase.RECONNECTING,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.FAILED_FINAL,
            SessionLifecyclePhase.ENDING,
        }
    ),
    SessionLifecyclePhase.CONNECTED: frozenset(
        {
            SessionLifecyclePhase.READY_TO_SHARE,
            SessionLifecyclePhase.DEGRADED,
            SessionLifecyclePhase.RECONNECTING,
            SessionLifecyclePhase.ENDING,
            SessionLifecyclePhase.FINALIZING_RECORDINGS,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.FAILED_FINAL,
        }
    ),
    SessionLifecyclePhase.DEGRADED: frozenset(
        {
            SessionLifecyclePhase.CONNECTED,
            SessionLifecyclePhase.RECONNECTING,
            SessionLifecyclePhase.ENDING,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.FAILED_FINAL,
        }
    ),
    SessionLifecyclePhase.RECONNECTING: frozenset(
        {
            SessionLifecyclePhase.CONNECTED,
            SessionLifecyclePhase.DEGRADED,
            SessionLifecyclePhase.ENDING,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.FAILED_FINAL,
        }
    ),
    SessionLifecyclePhase.ENDING: frozenset(
        {
            SessionLifecyclePhase.FINALIZING_RECORDINGS,
            SessionLifecyclePhase.COMPLETED,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.FAILED_FINAL,
        }
    ),
    SessionLifecyclePhase.FINALIZING_RECORDINGS: frozenset(
        {
            SessionLifecyclePhase.COMPLETED,
            SessionLifecyclePhase.FAILED_RECOVERABLE,
            SessionLifecyclePhase.FAILED_FINAL,
        }
    ),
    SessionLifecyclePhase.FAILED_RECOVERABLE: frozenset(
        {
            SessionLifecyclePhase.PREPARING,
            SessionLifecyclePhase.CHECKING_PERMISSIONS,
            SessionLifecyclePhase.RUNNING_PREFLIGHT,
            SessionLifecyclePhase.STARTING_HOST,
            SessionLifecyclePhase.JOINING,
            SessionLifecyclePhase.ENDING,
            SessionLifecyclePhase.COMPLETED,
            SessionLifecyclePhase.FAILED_FINAL,
        }
    ),
    SessionLifecyclePhase.COMPLETED: frozenset({SessionLifecyclePhase.IDLE}),
    SessionLifecyclePhase.FAILED_FINAL: frozenset({SessionLifecyclePhase.IDLE}),
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _safe_reason(value: str) -> str:
    """Keep a support timeline bounded before bundle redaction is applied."""

    return " ".join(redact_text(str(value or "")).split())[:240]


@dataclass(frozen=True)
class SessionLifecycleSnapshot:
    phase: SessionLifecyclePhase
    role: str = ""
    recovery_attempt: int = 0
    transition_count: int = 0
    last_reason: str = ""

    def to_public_dict(self) -> dict[str, str | int]:
        return {
            "phase": self.phase.value,
            "role": self.role,
            "recovery_attempt": self.recovery_attempt,
            "transition_count": self.transition_count,
        }


class SessionLifecycle:
    """Small state machine shared by production lifecycle and diagnostics."""

    def __init__(self, *, role: str = "", max_events: int = 100) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._role = self._normalise_role(role)
        self._phase = SessionLifecyclePhase.IDLE
        self._recovery_attempt = 0
        self._transition_count = 0
        self._last_reason = ""
        self._events: deque[dict[str, str]] = deque(maxlen=max_events)

    @staticmethod
    def _normalise_role(role: str) -> str:
        value = str(role or "").strip().lower()
        return value if value in {"host", "join", "practice"} else ""

    @property
    def snapshot(self) -> SessionLifecycleSnapshot:
        return SessionLifecycleSnapshot(
            phase=self._phase,
            role=self._role,
            recovery_attempt=self._recovery_attempt,
            transition_count=self._transition_count,
            last_reason=self._last_reason,
        )

    @property
    def phase(self) -> SessionLifecyclePhase:
        return self._phase

    def set_role(self, role: str) -> None:
        """Set only a safe role label; it is never inferred from an invite."""

        self._role = self._normalise_role(role)

    def can_transition(self, phase: SessionLifecyclePhase) -> bool:
        target = SessionLifecyclePhase(phase)
        if target is self._phase:
            return True
        if self._phase in _TERMINAL:
            return target is SessionLifecyclePhase.IDLE
        return target in _ALLOWED.get(self._phase, frozenset())

    def transition(
        self,
        phase: SessionLifecyclePhase,
        *,
        reason: str = "",
        recovery_attempt: int | None = None,
    ) -> bool:
        """Record one valid meaningful transition.

        Repeating the same state is deliberately idempotent.  It can update a
        recovery attempt without adding noisy duplicate history.  Invalid
        transitions return ``False`` so stale worker callbacks cannot change a
        completed session back into a live state.
        """

        target = SessionLifecyclePhase(phase)
        if not self.can_transition(target):
            return False
        safe_reason = _safe_reason(reason)
        if recovery_attempt is not None:
            self._recovery_attempt = max(0, int(recovery_attempt))
        if target is self._phase:
            if safe_reason:
                self._last_reason = safe_reason
            return True
        previous = self._phase
        self._phase = target
        self._last_reason = safe_reason
        self._transition_count += 1
        self._events.append(
            {
                "at": _timestamp(),
                "component": "session",
                "event": "transition",
                "from_state": previous.value,
                "to_state": target.value,
                "status": "ok",
                "reason": safe_reason,
            }
        )
        return True

    def reset(self, *, reason: str = "ready for a new session") -> bool:
        """Close the prior lifecycle into an idle, startable session."""

        if self._phase not in _TERMINAL and self._phase is not SessionLifecyclePhase.IDLE:
            # Reset is used after cancellation and failed preflight as well as
            # a normal End Session.  Those active phases cannot jump straight
            # to COMPLETED without weakening the transition contract, so close
            # them through the same explicit ending path first.
            if self._phase is not SessionLifecyclePhase.ENDING:
                self.transition(SessionLifecyclePhase.ENDING, reason=reason)
            if self._phase is SessionLifecyclePhase.ENDING:
                self.transition(SessionLifecyclePhase.COMPLETED, reason=reason)
        result = self.transition(SessionLifecyclePhase.IDLE, reason=reason)
        self._recovery_attempt = 0
        return result

    def public_timeline(self) -> tuple[dict[str, str], ...]:
        """Return copied allowlisted records for diagnostics only."""

        return tuple(dict(event) for event in self._events)
