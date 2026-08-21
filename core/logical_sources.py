"""Stable, path-free identities for one session's logical audio sources.

Track, media-segment, and take IDs identify concrete project entities.  They
must change between takes.  A logical source ID instead identifies the same
participant/source slot across repeated takes, so Studio can stack lanes
without guessing from display names or track order.
"""

from __future__ import annotations

import uuid

_LOGICAL_SOURCE_NAMESPACE = uuid.UUID("0c85a31b-4c6e-5faa-90e2-68aff7bedef5")
_SOURCE_KINDS = {"jamulus_server", "local_original", "shared_track"}


def canonical_logical_source_id(value: object, *, optional: bool = False) -> str:
    """Return one canonical UUID, optionally accepting an empty legacy value."""

    if optional and value in (None, ""):
        return ""
    try:
        parsed = uuid.UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("logical_source_id must be a canonical UUID.") from exc
    canonical = str(parsed)
    if str(value).lower() != canonical:
        raise ValueError("logical_source_id must be a canonical UUID.")
    return canonical


def derive_logical_source_id(
    session_id: object,
    participant_id: object,
    source_kind: str,
    ordinal: int = 0,
) -> str:
    """Derive a stable session-scoped ID without exposing names or paths."""

    session = str(session_id or "").strip()
    participant = str(participant_id or "").strip()
    if not session or not participant:
        raise ValueError("session_id and participant_id are required.")
    kind = str(source_kind or "").strip().lower()
    if kind not in _SOURCE_KINDS:
        raise ValueError("source_kind is unsupported.")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("ordinal must be a non-negative integer.")
    return str(
        uuid.uuid5(
            _LOGICAL_SOURCE_NAMESPACE,
            f"webjam-logical-source-v1:{session}:{participant}:{kind}:{ordinal}",
        )
    )


__all__ = ["canonical_logical_source_id", "derive_logical_source_id"]
