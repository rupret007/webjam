"""Host ownership for one short-lived, revocable v3 invitation.

No serialized URL is retained.  The owner passes typed invitation material to
the authenticated transport registrar and serializes only for an explicit
clipboard request. Reset revokes first and then issues fresh random bearer
material for the same live session.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

from core.remote_invitation import (
    DEFAULT_INVITATION_TTL_SECONDS,
    EnrollmentClaim,
    InvitationLifecycle,
    InvitationLifecycleError,
    InvitationState,
    IssuedRemoteInvitation,
    RemoteInvitation,
    issue_remote_invitation,
)

LOGGER = logging.getLogger("webjam.services.remote_invitation")


class RemoteInvitationOwnerError(RuntimeError):
    """A fixed-copy host invitation ownership failure."""


class RemoteInvitationRegistrar(Protocol):
    """Secure transport control plane; values remain typed and memory-only."""

    def register_invitation(self, invitation: RemoteInvitation) -> None: ...

    def revoke_invitation(self, invitation: RemoteInvitation) -> None: ...


class RemoteInvitationOwner:
    """Own exactly one host invitation and its monotonic local lifecycle."""

    def __init__(
        self,
        registrar: RemoteInvitationRegistrar,
        *,
        profile_id: str,
        allowed_profiles: frozenset[str],
        host_spki_sha256: bytes,
        ttl_seconds: int = DEFAULT_INVITATION_TTL_SECONDS,
        clock: Callable[[], int | float] = time.time,
    ) -> None:
        self._registrar = registrar
        self._profile_id = str(profile_id)
        self._allowed_profiles = frozenset(allowed_profiles)
        self._host_pin = bytes(host_spki_sha256)
        self._ttl_seconds = int(ttl_seconds)
        self._clock = clock
        self._lock = threading.RLock()
        self._issued: IssuedRemoteInvitation | None = None
        self._lifecycle: InvitationLifecycle | None = None
        self._session_reference: bytes | None = None
        self._copy_available = False

    @property
    def invitation_available(self) -> bool:
        with self._lock:
            lifecycle = self._lifecycle
            if lifecycle is None:
                return False
            state = lifecycle.snapshot().state
            return self._copy_available and state in {
                InvitationState.ISSUED,
                InvitationState.RESERVED,
            }

    @property
    def invitation(self) -> RemoteInvitation | None:
        """Typed host state for enrollment wiring; never a URL."""

        with self._lock:
            return self._issued.invitation if self._issued is not None else None

    def start(self, *, session_reference: bytes | None = None) -> None:
        with self._lock:
            if self._issued is not None:
                raise RemoteInvitationOwnerError(
                    "A remote invitation is already active."
                )
            self._session_reference = (
                None if session_reference is None else bytes(session_reference)
            )
            self._install_new_locked()

    def copy_for_clipboard(self) -> str:
        """Return one transient serialization at the user's copy boundary."""

        with self._lock:
            if not self.invitation_available or self._issued is None:
                raise RemoteInvitationOwnerError(
                    "Create a fresh invitation before copying it."
                )
            return self._issued.private_link.reveal_for_clipboard()

    def reserve(self, claim: EnrollmentClaim) -> bool:
        with self._lock:
            lifecycle = self._require_lifecycle_locked()
            return lifecycle.reserve(claim).newly_reserved

    def consume(self, claim: EnrollmentClaim) -> bool:
        with self._lock:
            lifecycle = self._require_lifecycle_locked()
            consumed = lifecycle.consume(claim)
            self._copy_available = False
            return consumed

    def mark_enrollment_consumed(self, invitation: RemoteInvitation) -> bool:
        """Retire Copy Invite after the native peer-auth boundary.

        The reference service consumes its derived enrollment token before the
        QUIC proof finishes. The desktop deliberately waits for the sidecar's
        authenticated ``peer_connected`` fact before changing user-visible
        state. Identity comparison prevents a late event for an old generation
        from retiring a freshly reset invitation.
        """

        if not isinstance(invitation, RemoteInvitation):
            raise TypeError("invitation must be a RemoteInvitation")
        with self._lock:
            issued = self._issued
            if issued is None or issued.invitation is not invitation:
                return False
            self._copy_available = False
            return True

    def reset(self) -> None:
        """Revoke the current bearer/peer and create a new one atomically.

        There is deliberately no rollback to the old bearer if registration
        of its replacement fails: once revocation begins, fail closed.
        """

        with self._lock:
            self._revoke_current_locked()
            self._issued = None
            self._lifecycle = None
            self._copy_available = False
            try:
                self._install_new_locked()
            except Exception as exc:  # noqa: BLE001 - sanitize transport detail
                LOGGER.error(
                    "Remote invitation replacement failed; exception_type=%s",
                    type(exc).__name__,
                )
                raise RemoteInvitationOwnerError(
                    "WebJam could not create a fresh invitation."
                ) from None

    def stop(self) -> None:
        with self._lock:
            self._revoke_current_locked()
            self._issued = None
            self._lifecycle = None
            self._session_reference = None
            self._copy_available = False

    def _install_new_locked(self) -> None:
        now = self._clock()
        if isinstance(now, bool):
            raise RemoteInvitationOwnerError(
                "WebJam could not create a fresh invitation."
            )
        issued = issue_remote_invitation(
            self._profile_id,
            allowed_profiles=self._allowed_profiles,
            host_spki_sha256=self._host_pin,
            issued_at_unix=int(now),
            ttl_seconds=self._ttl_seconds,
            session_reference=self._session_reference,
        )
        try:
            self._registrar.register_invitation(issued.invitation)
        except Exception as exc:  # noqa: BLE001 - never reflect registrar text
            # Registration APIs may fail after committing remotely. A bounded,
            # idempotent revoke makes that ambiguous outcome fail closed.
            try:
                self._registrar.revoke_invitation(issued.invitation)
            except Exception:  # noqa: BLE001 - the public error stays fixed
                pass
            LOGGER.error(
                "Remote invitation registration failed; exception_type=%s",
                type(exc).__name__,
            )
            raise RemoteInvitationOwnerError(
                "WebJam could not create a fresh invitation."
            ) from None
        self._session_reference = issued.invitation.session_reference
        self._issued = issued
        self._lifecycle = InvitationLifecycle(issued.invitation, clock=self._clock)
        self._copy_available = True

    def _revoke_current_locked(self) -> None:
        issued = self._issued
        lifecycle = self._lifecycle
        self._copy_available = False
        if issued is None:
            return
        try:
            self._registrar.revoke_invitation(issued.invitation)
        except Exception as exc:  # noqa: BLE001 - revocation still fails closed locally
            LOGGER.error(
                "Remote invitation revocation failed; exception_type=%s",
                type(exc).__name__,
            )
        if lifecycle is not None:
            try:
                lifecycle.revoke()
            except InvitationLifecycleError:
                # A consumed invitation is already terminal. Registrar-level
                # peer revocation above remains the session-authoritative act.
                pass

    def _require_lifecycle_locked(self) -> InvitationLifecycle:
        if self._lifecycle is None:
            raise RemoteInvitationOwnerError("No remote invitation is active.")
        return self._lifecycle

    def __repr__(self) -> str:
        return (
            "RemoteInvitationOwner("
            f"available={self.invitation_available!r}, private=[redacted])"
        )
