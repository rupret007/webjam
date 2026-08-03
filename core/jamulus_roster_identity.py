"""Exact ordered-roster identity shared by Jamulus client and server RPC.

Pinned Jamulus 3.12.2/3.12.3 rewrites server channel IDs into a separate
client-local mixer namespace before exposing ``getClientList``.  The one
cross-RPC invariant retained by Jamulus is row order: both the client list and
``jamulusserver/getClients`` are emitted in ascending server-channel order.

This module deliberately hashes only fields represented by both APIs.  Names
remain presentation data, never a key by themselves; the digest covers the
entire ordered roster and is useful only together with an authenticated fresh
roster challenge and the caller's self ordinal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping, Sequence


MAX_JAMULUS_ROSTER_ROWS = 256
_MAX_PROFILE_TEXT = 512
_CLIENT_SKILL_CODES = {
    None: 0,
    "beginner": 1,
    "intermediate": 2,
    "expert": 3,
}


class JamulusRosterIdentityError(ValueError):
    """A roster cannot authorize an identity translation."""


def _bounded_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise JamulusRosterIdentityError(f"{label} is not text")
    if len(value) > _MAX_PROFILE_TEXT:
        raise JamulusRosterIdentityError(f"{label} is too long")
    return value


def _bounded_code(value: object, label: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise JamulusRosterIdentityError(f"{label} is not an integer")
    if not 0 <= value <= maximum:
        raise JamulusRosterIdentityError(f"{label} is out of range")
    return value


@dataclass(frozen=True, slots=True, repr=False)
class JamulusCommonProfile:
    """Profile fields emitted without loss by both pinned RPC surfaces."""

    name: str
    instrument_code: int
    city: str
    skill_level_code: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _bounded_text(self.name, "name"))
        object.__setattr__(
            self,
            "instrument_code",
            _bounded_code(
                self.instrument_code,
                "instrument code",
                maximum=65_535,
            ),
        )
        object.__setattr__(self, "city", _bounded_text(self.city, "city"))
        object.__setattr__(
            self,
            "skill_level_code",
            _bounded_code(
                self.skill_level_code,
                "skill level code",
                maximum=3,
            ),
        )

    def canonical_values(self) -> tuple[str, int, str, int]:
        return (
            self.name,
            self.instrument_code,
            self.city,
            self.skill_level_code,
        )

    def __repr__(self) -> str:
        return "JamulusCommonProfile(<redacted>)"


def client_common_profile(raw: Mapping[str, object]) -> JamulusCommonProfile:
    """Decode one official ``jamulusclient/getClientList`` row."""

    if not isinstance(raw, Mapping):
        raise JamulusRosterIdentityError("client roster row is invalid")
    skill = raw.get("skillLevel")
    if skill not in _CLIENT_SKILL_CODES:
        raise JamulusRosterIdentityError("client skill level is invalid")
    return JamulusCommonProfile(
        name=_bounded_text(raw.get("name"), "client name"),
        instrument_code=_bounded_code(
            raw.get("instrumentId"),
            "client instrument code",
            maximum=65_535,
        ),
        city=_bounded_text(raw.get("city"), "client city"),
        skill_level_code=_CLIENT_SKILL_CODES[skill],
    )


def server_common_profile(raw: Mapping[str, object]) -> JamulusCommonProfile:
    """Decode one official ``jamulusserver/getClients`` row."""

    if not isinstance(raw, Mapping):
        raise JamulusRosterIdentityError("server roster row is invalid")
    return JamulusCommonProfile(
        name=_bounded_text(raw.get("name"), "server name"),
        instrument_code=_bounded_code(
            raw.get("instrumentCode"),
            "server instrument code",
            maximum=65_535,
        ),
        city=_bounded_text(raw.get("city"), "server city"),
        skill_level_code=_bounded_code(
            raw.get("skillLevelCode"),
            "server skill level code",
            maximum=3,
        ),
    )


def ordered_common_roster_digest(
    profiles: Sequence[JamulusCommonProfile],
) -> str:
    """Return a domain-separated SHA-256 digest of one ordered roster."""

    rows = tuple(profiles)
    if len(rows) > MAX_JAMULUS_ROSTER_ROWS:
        raise JamulusRosterIdentityError("roster is too large")
    if not all(isinstance(row, JamulusCommonProfile) for row in rows):
        raise JamulusRosterIdentityError("roster profile is invalid")
    payload = json.dumps(
        {
            "domain": "webjam-jamulus-ordered-common-roster-v1",
            "rows": [row.canonical_values() for row in rows],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def ordered_client_local_roster_fingerprint(
    client_local_ids: Sequence[int],
    *,
    own_ordinal: int,
) -> str:
    """Hash the host-private mixer layout without exposing profile data.

    The common-profile digest cannot distinguish a reorder of two completely
    identical profiles. Pinned Jamulus's client-local mixer mapping normally
    does, so the host binds challenge rotation to this additional private
    fact. It is never sent to a guest or persisted.
    """

    local_ids = tuple(client_local_ids)
    if not 0 < len(local_ids) <= MAX_JAMULUS_ROSTER_ROWS:
        raise JamulusRosterIdentityError("client-local roster size is invalid")
    if (
        isinstance(own_ordinal, bool)
        or not isinstance(own_ordinal, int)
        or not 0 <= own_ordinal < len(local_ids)
    ):
        raise JamulusRosterIdentityError("self ordinal is invalid")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in local_ids
    ):
        raise JamulusRosterIdentityError("client-local roster id is invalid")
    if len(set(local_ids)) != len(local_ids):
        raise JamulusRosterIdentityError("client-local roster ids are ambiguous")
    if local_ids[own_ordinal] != 0 or local_ids.count(0) != 1:
        raise JamulusRosterIdentityError("client-local self row is invalid")
    payload = json.dumps(
        {
            "domain": "webjam-jamulus-private-local-roster-v1",
            "local_ids": local_ids,
            "own_ordinal": own_ordinal,
        },
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()
