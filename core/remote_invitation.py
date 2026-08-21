"""Strict, secret-safe invitations for version-3 remote WebJam sessions.

The public URL deliberately contains only an allowlisted rendezvous profile
identifier and one fixed-size opaque payload.  The payload carries random
session and invitation references, a one-use enrollment capability, the
expected host public-key (SPKI) SHA-256 pin, and bounded lifetime/enrollment
facts.
It never carries a musician name, network address, port, path, or relay
credential.

Parsing is intentionally dependency-free and does not perform network work.
The rendezvous service remains authoritative for expiry and enrollment state;
the encoded timestamps allow an advisory local check and bind the same facts
into the later authenticated enrollment transcript.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import math
import operator
import re
import secrets
import struct
import threading
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from enum import Enum

REMOTE_INVITATION_VERSION = 3
DEFAULT_INVITATION_TTL_SECONDS = 10 * 60
MAX_INVITATION_TTL_SECONDS = 60 * 60
REMOTE_PARTICIPANT_LIMIT = 1
MAX_REMOTE_INVITATION_URL_BYTES = 512

_SCHEME = "webjam"
_ACTION = "join"
_INNER_MAGIC = b"WJ3\x01"
_PROFILE_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")

# magic, issued-at, expires-at, participant-limit, session-ref, invite-ref,
# enrollment capability, expected host SPKI SHA-256.
_ENVELOPE = struct.Struct("!4sQQB16s16s32s32s")
_ENCODED_ENVELOPE_LENGTH = len(
    base64.urlsafe_b64encode(bytes(_ENVELOPE.size)).rstrip(b"=")
)


class RemoteInvitationErrorCode(str, Enum):
    """Stable internal reason codes whose messages never contain input data."""

    MALFORMED = "malformed"
    INCOMPATIBLE = "incompatible"
    UNTRUSTED_PROFILE = "untrusted_profile"


class RemoteInvitationError(ValueError):
    """A safe parse/creation failure for a v3 remote invitation."""

    def __init__(self, code: RemoteInvitationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _malformed() -> RemoteInvitationError:
    return RemoteInvitationError(
        RemoteInvitationErrorCode.MALFORMED,
        "That WebJam invitation is not valid.",
    )


def _incompatible() -> RemoteInvitationError:
    return RemoteInvitationError(
        RemoteInvitationErrorCode.INCOMPATIBLE,
        "That invitation needs a different WebJam version.",
    )


def _untrusted_profile() -> RemoteInvitationError:
    return RemoteInvitationError(
        RemoteInvitationErrorCode.UNTRUSTED_PROFILE,
        "That invitation uses an unavailable WebJam service.",
    )


def _canonical_profile(value: object) -> str:
    profile = str(value or "")
    if not profile.isascii() or not _PROFILE_PATTERN.fullmatch(profile):
        raise _untrusted_profile()
    return profile


def _require_allowed_profile(
    profile: object,
    allowed_profiles: Collection[str],
) -> str:
    canonical = _canonical_profile(profile)
    if isinstance(allowed_profiles, (str, bytes)):
        raise TypeError("allowed_profiles must be a collection of profile IDs")
    try:
        allowed = frozenset(str(item) for item in allowed_profiles)
    except TypeError as exc:
        raise TypeError("allowed_profiles must be a collection of profile IDs") from exc
    if canonical not in allowed:
        raise _untrusted_profile()
    return canonical


def _fixed_bytes(value: object, length: int, field: str) -> bytes:
    try:
        raw = bytes(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be {length} bytes") from exc
    if len(raw) != length or not any(raw):
        raise ValueError(f"{field} must be {length} non-zero bytes")
    return raw


def _uint64(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        result = operator.index(value)  # type: ignore[arg-type]
    except (TypeError, OverflowError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if result < 0 or result > 2**64 - 1:
        raise ValueError(f"{field} is outside the supported range")
    return result


def _validate_lifetime(issued_at: int, expires_at: int) -> None:
    lifetime = expires_at - issued_at
    if not 1 <= lifetime <= MAX_INVITATION_TTL_SECONDS:
        raise ValueError("invitation lifetime is outside the supported range")


class PrivateInvitationLink:
    """A typed invitation with one explicit, transient serialization boundary."""

    __slots__ = ("_invitation", "_sealed")

    def __init__(self, invitation: RemoteInvitation) -> None:
        if not isinstance(invitation, RemoteInvitation):
            raise TypeError("invitation must be a RemoteInvitation")
        object.__setattr__(self, "_invitation", invitation)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, _name: str, _value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("PrivateInvitationLink is immutable")
        object.__setattr__(self, _name, _value)

    def reveal_for_clipboard(self) -> str:
        """Serialize only at the explicit clipboard boundary."""

        return _serialize_invitation(self._invitation)

    def __str__(self) -> str:
        return "[private WebJam invitation]"

    def __repr__(self) -> str:
        return "PrivateInvitationLink([redacted])"


class RemoteInvitation:
    """Immutable parsed v3 data with an explicit capability reveal method."""

    __slots__ = (
        "_capability",
        "_expires_at_unix",
        "_host_spki_sha256",
        "_invite_reference",
        "_issued_at_unix",
        "_participant_limit",
        "_profile_id",
        "_sealed",
        "_session_reference",
    )

    def __init__(
        self,
        *,
        profile_id: str,
        issued_at_unix: int,
        expires_at_unix: int,
        participant_limit: int,
        session_reference: bytes,
        invite_reference: bytes,
        enrollment_capability: bytes,
        host_spki_sha256: bytes,
    ) -> None:
        object.__setattr__(self, "_sealed", False)
        object.__setattr__(self, "_profile_id", _canonical_profile(profile_id))
        issued = _uint64(issued_at_unix, "issued_at_unix")
        expires = _uint64(expires_at_unix, "expires_at_unix")
        _validate_lifetime(issued, expires)
        if isinstance(participant_limit, bool):
            raise ValueError("v3 invitations allow exactly one participant")
        try:
            canonical_limit = operator.index(participant_limit)
        except (TypeError, OverflowError) as exc:
            raise ValueError(
                "v3 invitations allow exactly one participant"
            ) from exc
        if canonical_limit != REMOTE_PARTICIPANT_LIMIT:
            raise ValueError("v3 invitations allow exactly one participant")
        session = _fixed_bytes(session_reference, 16, "session_reference")
        invite = _fixed_bytes(invite_reference, 16, "invite_reference")
        if hmac.compare_digest(session, invite):
            raise ValueError("session and invitation references must be distinct")
        object.__setattr__(self, "_issued_at_unix", issued)
        object.__setattr__(self, "_expires_at_unix", expires)
        object.__setattr__(self, "_participant_limit", REMOTE_PARTICIPANT_LIMIT)
        object.__setattr__(self, "_session_reference", session)
        object.__setattr__(self, "_invite_reference", invite)
        object.__setattr__(
            self,
            "_capability",
            _fixed_bytes(enrollment_capability, 32, "enrollment_capability"),
        )
        object.__setattr__(
            self,
            "_host_spki_sha256",
            _fixed_bytes(
                host_spki_sha256,
                32,
                "host_spki_sha256",
            ),
        )
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, _name: str, _value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("RemoteInvitation is immutable")
        object.__setattr__(self, _name, _value)

    @property
    def version(self) -> int:
        return REMOTE_INVITATION_VERSION

    @property
    def is_remote(self) -> bool:
        return True

    @property
    def profile_id(self) -> str:
        return self._profile_id

    @property
    def issued_at_unix(self) -> int:
        return self._issued_at_unix

    @property
    def expires_at_unix(self) -> int:
        return self._expires_at_unix

    @property
    def participant_limit(self) -> int:
        return self._participant_limit

    @property
    def session_reference(self) -> bytes:
        return self._session_reference

    @property
    def invite_reference(self) -> bytes:
        return self._invite_reference

    @property
    def host_spki_sha256(self) -> bytes:
        return self._host_spki_sha256

    def capability_for_enrollment(self) -> bytes:
        """Reveal the bearer only to the authenticated enrollment protocol."""

        return self._capability

    def advisory_expired(self, now_unix: float | None = None) -> bool:
        """Return local-clock expiry truth; the service remains authoritative."""

        now = time.time() if now_unix is None else float(now_unix)
        return bool(math.isfinite(now) and now >= self._expires_at_unix)

    def __str__(self) -> str:
        return "[private WebJam remote invitation]"

    def __repr__(self) -> str:
        return (
            "RemoteInvitation(version=3, "
            f"profile_id={self._profile_id!r}, "
            f"expires_at_unix={self._expires_at_unix}, "
            "participant_limit=1, private=[redacted])"
        )


class IssuedRemoteInvitation:
    """A host-side invitation and its explicit private copy boundary."""

    __slots__ = ("_sealed", "invitation", "private_link")

    def __init__(
        self,
        invitation: RemoteInvitation,
        private_link: PrivateInvitationLink,
    ) -> None:
        object.__setattr__(self, "invitation", invitation)
        object.__setattr__(self, "private_link", private_link)
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, _name: str, _value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("IssuedRemoteInvitation is immutable")
        object.__setattr__(self, _name, _value)

    def __repr__(self) -> str:
        return "IssuedRemoteInvitation(private=[redacted])"


def _encode_payload(invitation: RemoteInvitation) -> str:
    payload = _ENVELOPE.pack(
        _INNER_MAGIC,
        invitation.issued_at_unix,
        invitation.expires_at_unix,
        invitation.participant_limit,
        invitation.session_reference,
        invitation.invite_reference,
        invitation.capability_for_enrollment(),
        invitation.host_spki_sha256,
    )
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _serialize_invitation(invitation: RemoteInvitation) -> str:
    encoded = _encode_payload(invitation)
    return (
        f"{_SCHEME}://{_ACTION}?v={REMOTE_INVITATION_VERSION}"
        f"&r={invitation.profile_id}&i={encoded}"
    )


def issue_remote_invitation(
    profile_id: str,
    *,
    allowed_profiles: Collection[str],
    host_spki_sha256: bytes,
    issued_at_unix: int | None = None,
    ttl_seconds: int = DEFAULT_INVITATION_TTL_SECONDS,
    session_reference: bytes | None = None,
    invite_reference: bytes | None = None,
    enrollment_capability: bytes | None = None,
) -> IssuedRemoteInvitation:
    """Create one canonical, private v3 invitation using CSPRNG defaults."""

    profile = _require_allowed_profile(profile_id, allowed_profiles)
    issued = _uint64(
        int(time.time()) if issued_at_unix is None else issued_at_unix,
        "issued_at_unix",
    )
    if isinstance(ttl_seconds, bool):
        raise ValueError("ttl_seconds must be an integer")
    try:
        ttl = operator.index(ttl_seconds)
    except (TypeError, OverflowError) as exc:
        raise ValueError("ttl_seconds must be an integer") from exc
    if not 1 <= ttl <= MAX_INVITATION_TTL_SECONDS:
        raise ValueError("ttl_seconds is outside the supported range")
    if issued > 2**64 - 1 - ttl:
        raise ValueError("invitation expiry is outside the supported range")
    session = (
        secrets.token_bytes(16)
        if session_reference is None
        else session_reference
    )
    invite = (
        secrets.token_bytes(16)
        if invite_reference is None
        else invite_reference
    )
    capability = (
        secrets.token_bytes(32)
        if enrollment_capability is None
        else enrollment_capability
    )
    invitation = RemoteInvitation(
        profile_id=profile,
        issued_at_unix=issued,
        expires_at_unix=issued + ttl,
        participant_limit=REMOTE_PARTICIPANT_LIMIT,
        session_reference=session,
        invite_reference=invite,
        enrollment_capability=capability,
        host_spki_sha256=host_spki_sha256,
    )
    return IssuedRemoteInvitation(invitation, PrivateInvitationLink(invitation))


def _strict_query(value: str) -> tuple[str, str]:
    """Return (profile, envelope), accepting exactly the canonical v3 query."""

    # ``urlsplit`` normalizes URI schemes to lowercase. Check the serialized
    # prefix first so alternate spellings cannot become a second canonical
    # representation of the same private capability.
    if not value.startswith(f"{_SCHEME}://{_ACTION}?"):
        raise _malformed()
    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(value)
    except ValueError as exc:
        raise _malformed() from exc
    if (
        parsed.scheme != _SCHEME
        or parsed.netloc != _ACTION
        or parsed.path
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise _malformed()
    query = parsed.query
    if not query or "%" in query or "+" in query:
        raise _malformed()
    parts = query.split("&")
    if len(parts) != 3:
        raise _malformed()
    pairs: list[tuple[str, str]] = []
    for part in parts:
        if part.count("=") != 1:
            raise _malformed()
        key, item = part.split("=", 1)
        if not key or not item or not key.isascii() or not item.isascii():
            raise _malformed()
        pairs.append((key, item))
    if [key for key, _item in pairs] != ["v", "r", "i"]:
        raise _malformed()
    version, profile, encoded = (item for _key, item in pairs)
    if version != str(REMOTE_INVITATION_VERSION):
        raise _incompatible()
    return profile, encoded


def parse_remote_invitation_link(
    text: str,
    *,
    allowed_profiles: Collection[str],
) -> RemoteInvitation:
    """Parse exactly one canonical v3 URL without doing network or clock I/O."""

    raw = str(text or "")
    try:
        raw.encode("ascii")
    except UnicodeEncodeError as exc:
        raise _malformed() from exc
    value = raw.strip()
    if not value or len(value.encode("ascii")) > MAX_REMOTE_INVITATION_URL_BYTES:
        raise _malformed()
    if any(character.isspace() for character in value):
        raise _malformed()
    profile_text, encoded = _strict_query(value)
    profile = _require_allowed_profile(profile_text, allowed_profiles)
    if (
        len(encoded) != _ENCODED_ENVELOPE_LENGTH
        or "=" in encoded
        or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded)
    ):
        raise _malformed()
    try:
        payload = base64.b64decode(
            encoded.encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise _malformed() from exc
    canonical = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    if not hmac.compare_digest(canonical, encoded) or len(payload) != _ENVELOPE.size:
        raise _malformed()
    try:
        (
            magic,
            issued,
            expires,
            participant_limit,
            session,
            invite,
            capability,
            host_pin,
        ) = _ENVELOPE.unpack(payload)
        if magic != _INNER_MAGIC:
            raise ValueError("wrong inner invitation schema")
        return RemoteInvitation(
            profile_id=profile,
            issued_at_unix=issued,
            expires_at_unix=expires,
            participant_limit=participant_limit,
            session_reference=session,
            invite_reference=invite,
            enrollment_capability=capability,
            host_spki_sha256=host_pin,
        )
    except (struct.error, TypeError, ValueError) as exc:
        raise _malformed() from exc


class InvitationState(str, Enum):
    ISSUED = "issued"
    RESERVED = "reserved"
    CONSUMED = "consumed"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InvitationLifecycleErrorCode(str, Enum):
    EXPIRED = "expired"
    REVOKED = "revoked"
    CONSUMED = "consumed"
    REPLAY = "replay"
    DOWNGRADE = "downgrade"
    VERSION_MISMATCH = "version_mismatch"
    CROSS_SESSION = "cross_session"
    WRONG_INVITATION = "wrong_invitation"
    INVALID_CAPABILITY = "invalid_capability"


class InvitationLifecycleError(RuntimeError):
    """A terminal or rejected enrollment action with a secret-free message."""

    def __init__(self, code: InvitationLifecycleErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class EnrollmentClaim:
    """One guest's capability-bound attempt; repr/str omit all identifiers."""

    __slots__ = (
        "_capability",
        "_sealed",
        "claim_reference",
        "guest_key_sha256",
        "invite_reference",
        "protocol_version",
        "session_reference",
    )

    def __init__(
        self,
        *,
        protocol_version: int,
        session_reference: bytes,
        invite_reference: bytes,
        claim_reference: bytes,
        guest_key_sha256: bytes,
        enrollment_capability: bytes,
    ) -> None:
        if isinstance(protocol_version, bool):
            raise ValueError("protocol_version must be an integer")
        try:
            canonical_version = operator.index(protocol_version)
        except (TypeError, OverflowError) as exc:
            raise ValueError("protocol_version must be an integer") from exc
        object.__setattr__(self, "_sealed", False)
        object.__setattr__(self, "protocol_version", canonical_version)
        object.__setattr__(
            self,
            "session_reference",
            _fixed_bytes(session_reference, 16, "session_reference"),
        )
        object.__setattr__(
            self,
            "invite_reference",
            _fixed_bytes(invite_reference, 16, "invite_reference"),
        )
        object.__setattr__(
            self,
            "claim_reference",
            _fixed_bytes(claim_reference, 16, "claim_reference"),
        )
        object.__setattr__(
            self,
            "guest_key_sha256",
            _fixed_bytes(guest_key_sha256, 32, "guest_key_sha256"),
        )
        object.__setattr__(
            self,
            "_capability",
            _fixed_bytes(enrollment_capability, 32, "enrollment_capability"),
        )
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, _name: str, _value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("EnrollmentClaim is immutable")
        object.__setattr__(self, _name, _value)

    @classmethod
    def for_invitation(
        cls,
        invitation: RemoteInvitation,
        *,
        guest_public_key: bytes,
        claim_reference: bytes | None = None,
        protocol_version: int = REMOTE_INVITATION_VERSION,
    ) -> EnrollmentClaim:
        try:
            guest_key = bytes(guest_public_key)
        except (TypeError, ValueError) as exc:
            raise ValueError("guest_public_key must be bounded non-zero bytes") from exc
        if not 16 <= len(guest_key) <= 4096 or not any(guest_key):
            raise ValueError("guest_public_key must be bounded non-zero bytes")
        return cls(
            protocol_version=protocol_version,
            session_reference=invitation.session_reference,
            invite_reference=invitation.invite_reference,
            claim_reference=(
                secrets.token_bytes(16)
                if claim_reference is None
                else claim_reference
            ),
            guest_key_sha256=hashlib.sha256(guest_key).digest(),
            enrollment_capability=invitation.capability_for_enrollment(),
        )

    def capability_for_host(self) -> bytes:
        return self._capability

    def __str__(self) -> str:
        return "[private WebJam enrollment claim]"

    def __repr__(self) -> str:
        return "EnrollmentClaim(private=[redacted])"


@dataclass(frozen=True, slots=True)
class InvitationSnapshot:
    state: InvitationState
    issued_at_unix: int
    expires_at_unix: int
    participant_limit: int = REMOTE_PARTICIPANT_LIMIT


@dataclass(frozen=True, slots=True)
class ReservationResult:
    state: InvitationState
    newly_reserved: bool


class InvitationLifecycle:
    """Thread-safe monotonic one-use state for one remote invitation."""

    def __init__(
        self,
        invitation: RemoteInvitation,
        *,
        clock: Callable[[], int | float] = time.time,
    ) -> None:
        self._invitation = invitation
        self._clock = clock
        self._lock = threading.RLock()
        self._state = InvitationState.ISSUED
        self._claim_reference: bytes | None = None
        self._guest_key_sha256: bytes | None = None
        self._last_observed_unix = invitation.issued_at_unix

    def _now_locked(self, supplied: float | None) -> float:
        raw = self._clock() if supplied is None else supplied
        if isinstance(raw, bool):
            raise ValueError("now_unix must be a finite non-negative number")
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("now_unix must be a finite non-negative number") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError("now_unix must be a finite non-negative number")
        self._last_observed_unix = max(self._last_observed_unix, value)
        return self._last_observed_unix

    def _refresh_expiry_locked(self, now_unix: float | None) -> None:
        now = self._now_locked(now_unix)
        if (
            self._state in {InvitationState.ISSUED, InvitationState.RESERVED}
            and now >= self._invitation.expires_at_unix
        ):
            self._state = InvitationState.EXPIRED
            self._claim_reference = None
            self._guest_key_sha256 = None

    def _validate_claim_locked(self, claim: EnrollmentClaim) -> None:
        if claim.protocol_version < REMOTE_INVITATION_VERSION:
            raise InvitationLifecycleError(
                InvitationLifecycleErrorCode.DOWNGRADE,
                "An older invitation protocol cannot enter this session.",
            )
        if claim.protocol_version != REMOTE_INVITATION_VERSION:
            raise InvitationLifecycleError(
                InvitationLifecycleErrorCode.VERSION_MISMATCH,
                "That invitation protocol is not supported by this session.",
            )
        if not hmac.compare_digest(
            claim.session_reference,
            self._invitation.session_reference,
        ):
            raise InvitationLifecycleError(
                InvitationLifecycleErrorCode.CROSS_SESSION,
                "That enrollment belongs to another session.",
            )
        if not hmac.compare_digest(
            claim.invite_reference,
            self._invitation.invite_reference,
        ):
            raise InvitationLifecycleError(
                InvitationLifecycleErrorCode.WRONG_INVITATION,
                "That enrollment does not match this invitation.",
            )
        if not hmac.compare_digest(
            claim.capability_for_host(),
            self._invitation.capability_for_enrollment(),
        ):
            raise InvitationLifecycleError(
                InvitationLifecycleErrorCode.INVALID_CAPABILITY,
                "That enrollment capability is not valid.",
            )

    @staticmethod
    def _terminal_error(state: InvitationState) -> InvitationLifecycleError:
        mapping = {
            InvitationState.EXPIRED: (
                InvitationLifecycleErrorCode.EXPIRED,
                "That invitation has expired.",
            ),
            InvitationState.REVOKED: (
                InvitationLifecycleErrorCode.REVOKED,
                "That invitation was revoked.",
            ),
            InvitationState.CONSUMED: (
                InvitationLifecycleErrorCode.CONSUMED,
                "That invitation was already used.",
            ),
        }
        code, message = mapping[state]
        return InvitationLifecycleError(code, message)

    def snapshot(self, *, now_unix: float | None = None) -> InvitationSnapshot:
        with self._lock:
            self._refresh_expiry_locked(now_unix)
            return InvitationSnapshot(
                state=self._state,
                issued_at_unix=self._invitation.issued_at_unix,
                expires_at_unix=self._invitation.expires_at_unix,
            )

    def reserve(
        self,
        claim: EnrollmentClaim,
        *,
        now_unix: float | None = None,
    ) -> ReservationResult:
        """Atomically reserve the sole enrollment, idempotent for one claimant."""

        with self._lock:
            self._refresh_expiry_locked(now_unix)
            if self._state in {
                InvitationState.EXPIRED,
                InvitationState.REVOKED,
                InvitationState.CONSUMED,
            }:
                raise self._terminal_error(self._state)
            self._validate_claim_locked(claim)
            if self._state is InvitationState.RESERVED:
                same_claim = bool(
                    self._claim_reference is not None
                    and self._guest_key_sha256 is not None
                    and hmac.compare_digest(
                        claim.claim_reference,
                        self._claim_reference,
                    )
                    and hmac.compare_digest(
                        claim.guest_key_sha256,
                        self._guest_key_sha256,
                    )
                )
                if not same_claim:
                    raise InvitationLifecycleError(
                        InvitationLifecycleErrorCode.REPLAY,
                        "That invitation already has an enrollment in progress.",
                    )
                return ReservationResult(InvitationState.RESERVED, False)
            self._claim_reference = claim.claim_reference
            self._guest_key_sha256 = claim.guest_key_sha256
            self._state = InvitationState.RESERVED
            return ReservationResult(InvitationState.RESERVED, True)

    def consume(
        self,
        claim: EnrollmentClaim,
        *,
        now_unix: float | None = None,
    ) -> bool:
        """Consume a matching reservation; repeat confirmation is idempotent."""

        with self._lock:
            self._refresh_expiry_locked(now_unix)
            if self._state in {InvitationState.EXPIRED, InvitationState.REVOKED}:
                raise self._terminal_error(self._state)
            if self._state is InvitationState.CONSUMED:
                # Permit only an exact idempotent confirmation. All other
                # inputs receive the terminal state, not a capability/session
                # oracle describing why their replay differed.
                try:
                    self._validate_claim_locked(claim)
                except InvitationLifecycleError as exc:
                    raise self._terminal_error(self._state) from exc
            same_claim = bool(
                self._claim_reference is not None
                and self._guest_key_sha256 is not None
                and hmac.compare_digest(
                    claim.claim_reference,
                    self._claim_reference,
                )
                and hmac.compare_digest(
                    claim.guest_key_sha256,
                    self._guest_key_sha256,
                )
            )
            if self._state is InvitationState.CONSUMED and same_claim:
                return False
            if self._state is InvitationState.CONSUMED:
                raise self._terminal_error(self._state)
            self._validate_claim_locked(claim)
            if self._state is not InvitationState.RESERVED or not same_claim:
                raise InvitationLifecycleError(
                    InvitationLifecycleErrorCode.REPLAY,
                    "Only the reserved enrollment may consume this invitation.",
                )
            self._state = InvitationState.CONSUMED
            return True

    def revoke(self, *, now_unix: float | None = None) -> bool:
        """Revoke an unused invitation; terminal states never move backward."""

        with self._lock:
            self._refresh_expiry_locked(now_unix)
            if self._state is InvitationState.REVOKED:
                return False
            if self._state is InvitationState.EXPIRED:
                return False
            if self._state is InvitationState.CONSUMED:
                raise self._terminal_error(self._state)
            self._state = InvitationState.REVOKED
            self._claim_reference = None
            self._guest_key_sha256 = None
            return True

    def __repr__(self) -> str:
        return (
            "InvitationLifecycle("
            f"state={self._state.value!r}, private=[redacted])"
        )
