"""Compatibility facade for the Studio schema-2 arrangement boundary.

The v0.16 API exposed a small mix-only ``StudioTakeState``.  v0.17 keeps the
same import surface while the real immutable model lives in
``core.studio_project`` and crash-safe persistence lives in
``core.studio_store``.  Existing callers therefore gain durable regions,
markers, take lanes, comp ranges, recovery, and strict migration without a
reckless UI rewrite.

New controller code should prefer ``load_studio_document`` and
``save_studio_document`` directly so it can retain the exact CAS token and
surface recovery provenance.  These wrappers remain safe for the legacy
single-window caller and never touch source audio or ``webjam-take.json``.
"""

from __future__ import annotations

from pathlib import Path

from core.studio_project import (
    MAX_GAIN,
    MAX_STUDIO_TRACKS,
    StudioDocument,
    StudioProjectError,
    StudioTrack,
)
from core.studio_store import (
    MAX_STUDIO_STATE_BYTES,
    STUDIO_STATE_FILENAME,
    STUDIO_STATE_SCHEMA_VERSION,
    StudioStoreError,
    load_studio_document,
    save_studio_document,
    studio_state_path,
)


# Preserve the established names while making the arrangement document the
# single source of Studio truth.
StudioStateError = StudioProjectError
StudioTakeState = StudioDocument
StudioTrackState = StudioTrack


def load_studio_state(take_dir: str | Path) -> StudioDocument:
    """Load the arrangement document through the legacy state-only API."""

    try:
        return load_studio_document(take_dir).document
    except StudioStoreError as exc:
        raise StudioStateError(str(exc)) from exc


def save_studio_state(take_dir: str | Path, state: StudioDocument) -> Path:
    """Persist one document for the legacy single-window Studio caller.

    ``load_studio_state`` attaches the exact primary snapshot token to the
    otherwise immutable document. Immutable edit operations preserve it, so
    this facade performs the same byte-exact compare-and-swap as the new API;
    revision numbers are never mistaken for writer ancestry.
    """

    if not isinstance(state, StudioDocument):
        raise StudioStateError("Studio state must be a StudioDocument value.")
    try:
        return save_studio_document(
            take_dir,
            state,
            expected_token=state.store_token,
        ).path
    except StudioStoreError as exc:
        raise StudioStateError(str(exc)) from exc


__all__ = [
    "MAX_GAIN",
    "MAX_STUDIO_STATE_BYTES",
    "MAX_STUDIO_TRACKS",
    "STUDIO_STATE_FILENAME",
    "STUDIO_STATE_SCHEMA_VERSION",
    "StudioStateError",
    "StudioTakeState",
    "StudioTrackState",
    "load_studio_state",
    "save_studio_state",
    "studio_state_path",
]
