"""Durable, non-destructive Studio state for schema-v2 takes.

``webjam-take.json`` is the recording's evidence: it describes immutable
media, capture provenance, and timeline truth.  Studio mix choices must never
be folded back into that evidence.  This module keeps those choices in the
hidden ``.webjam-studio-state.json`` sidecar beside a take instead.

The sidecar is bound to both the schema-v2 project's ``session_id`` and
``take_id``.  Individual settings are bound to durable ``track_id`` values,
not list positions or display names.  Consequently a later manifest can add
or reorder tracks without moving a musician's gain, pan, mute, solo, or Logic
export selection: newly seen tracks receive defaults and stale track entries
are ignored.  A malformed sidecar or a sidecar for a different take raises
``StudioStateError`` and is never applied.

Typical Studio use is deliberately small::

    state = load_studio_state(take_folder)
    state = state.update_track(track_id, gain=0.8, muted=True)
    save_studio_state(take_folder, state)

``save_studio_state`` writes only the sidecar using :func:`atomic_write_text`.
It never opens or changes source media or ``webjam-take.json``.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from core.file_io import atomic_write_text
from core.take_project import (
    PROJECT_SCHEMA_VERSION,
    TakeProject,
    TakeProjectError,
)


STUDIO_STATE_FILENAME = ".webjam-studio-state.json"
"""Hidden per-take Studio sidecar name."""

STUDIO_STATE_SCHEMA_VERSION = 1
"""Version of the Studio sidecar format, independent of take-project schema."""

MAX_STUDIO_TRACKS = 512
"""Maximum accepted persisted lanes; prevents an unbounded sidecar payload."""

MAX_STUDIO_STATE_BYTES = 256 * 1024
"""Maximum accepted sidecar size before JSON parsing."""

MAX_GAIN = 4.0
"""Largest accepted non-destructive linear gain multiplier."""


class StudioStateError(ValueError):
    """Raised when Studio state cannot safely be associated with a take."""


def _canonical_uuid(value: object, field_name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise StudioStateError(f"{field_name} must be a UUID.") from exc


def _bounded_float(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool):
        raise StudioStateError(f"{field_name} must be a finite number.")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise StudioStateError(f"{field_name} must be a finite number.") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise StudioStateError(
            f"{field_name} must be between {minimum:g} and {maximum:g}."
        )
    return result


def _strict_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise StudioStateError(f"{field_name} must be true or false.")
    return value


@dataclass(frozen=True)
class StudioTrackState:
    """One lane's non-destructive Studio choices, keyed by its durable ID.

    ``gain`` is a linear multiplier from 0 through :data:`MAX_GAIN`; ``pan``
    ranges from -1 (left) through 1 (right).  The booleans influence Studio
    playback/reference-mix behavior only.  ``export_included`` controls
    whether the lane is included in the next Logic handoff, never the source
    recording itself.
    """

    track_id: str
    gain: float = 1.0
    pan: float = 0.0
    muted: bool = False
    solo: bool = False
    export_included: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "track_id", _canonical_uuid(self.track_id, "track_id"))
        object.__setattr__(
            self,
            "gain",
            _bounded_float(self.gain, "gain", minimum=0.0, maximum=MAX_GAIN),
        )
        object.__setattr__(
            self,
            "pan",
            _bounded_float(self.pan, "pan", minimum=-1.0, maximum=1.0),
        )
        object.__setattr__(self, "muted", _strict_bool(self.muted, "muted"))
        object.__setattr__(self, "solo", _strict_bool(self.solo, "solo"))
        object.__setattr__(
            self,
            "export_included",
            _strict_bool(self.export_included, "export_included"),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize this state without source-media or manifest references."""
        return {
            "track_id": self.track_id,
            "gain": self.gain,
            "pan": self.pan,
            "muted": self.muted,
            "solo": self.solo,
            "export_included": self.export_included,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StudioTrackState":
        """Parse one strict persisted lane state."""
        return cls(
            track_id=value.get("track_id", ""),
            gain=value.get("gain", 1.0),
            pan=value.get("pan", 0.0),
            muted=value.get("muted", False),
            solo=value.get("solo", False),
            export_included=value.get("export_included", True),
        )


@dataclass(frozen=True)
class StudioTakeState:
    """All saved Studio choices for one schema-v2 take.

    ``tracks`` is always ordered like the currently loaded project, but state
    is matched solely by ``StudioTrackState.track_id``.  Use
    :meth:`state_for` to fetch a lane and :meth:`update_track` to return an
    updated immutable value suitable for :func:`save_studio_state`.
    """

    session_id: str
    take_id: str
    tracks: tuple[StudioTrackState, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "session_id",
            _canonical_uuid(self.session_id, "session_id"),
        )
        object.__setattr__(self, "take_id", _canonical_uuid(self.take_id, "take_id"))
        tracks = tuple(self.tracks)
        if len(tracks) > MAX_STUDIO_TRACKS:
            raise StudioStateError(
                f"Studio state cannot contain more than {MAX_STUDIO_TRACKS} tracks."
            )
        if any(not isinstance(track, StudioTrackState) for track in tracks):
            raise StudioStateError("Studio state tracks must be StudioTrackState values.")
        if len({track.track_id for track in tracks}) != len(tracks):
            raise StudioStateError("Studio state contains duplicate track IDs.")
        object.__setattr__(self, "tracks", tracks)

    def state_for(self, track_id: str) -> StudioTrackState:
        """Return state for one currently loaded durable track ID.

        A missing lane is an identity mismatch, not a cue to accidentally use
        a positional fallback.  Reload the state after the take changes.
        """
        canonical = _canonical_uuid(track_id, "track_id")
        for item in self.tracks:
            if item.track_id == canonical:
                return item
        raise StudioStateError("Track is not part of this Studio take state.")

    def update_track(self, track_id: str, **changes: object) -> "StudioTakeState":
        """Return a copy with bounded values changed for one durable track.

        Only ``gain``, ``pan``, ``muted``, ``solo``, and ``export_included``
        are accepted.  The track ID itself cannot be changed through this API.
        Validation is performed by :class:`StudioTrackState` before the new
        state is returned.
        """
        allowed = {"gain", "pan", "muted", "solo", "export_included"}
        unexpected = set(changes).difference(allowed)
        if unexpected:
            raise StudioStateError(
                "Unsupported Studio track setting: "
                + ", ".join(sorted(str(item) for item in unexpected))
                + "."
            )
        original = self.state_for(track_id)
        updated = replace(original, **changes)
        return StudioTakeState(
            session_id=self.session_id,
            take_id=self.take_id,
            tracks=tuple(updated if item.track_id == updated.track_id else item for item in self.tracks),
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize durable state only; no audio paths or manifest copy."""
        return {
            "schema_version": STUDIO_STATE_SCHEMA_VERSION,
            "session_id": self.session_id,
            "take_id": self.take_id,
            "tracks": [item.to_dict() for item in self.tracks],
        }


def studio_state_path(take_dir: str | Path) -> Path:
    """Return the fixed hidden sidecar path inside ``take_dir``."""
    return Path(take_dir).expanduser() / STUDIO_STATE_FILENAME


def _load_schema_v2_project(take_dir: str | Path) -> tuple[Path, TakeProject]:
    """Load only an on-disk schema-v2 manifest, never a read-only v1 migration."""
    folder = Path(take_dir).expanduser()
    manifest = folder / "webjam-take.json"
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StudioStateError("Could not read this take's schema-v2 manifest.") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != PROJECT_SCHEMA_VERSION:
        raise StudioStateError("Studio state is available only for schema-v2 takes.")
    try:
        return folder, TakeProject.from_dict(raw)
    except TakeProjectError as exc:
        raise StudioStateError("This take's schema-v2 manifest is not valid.") from exc


def _default_state(project: TakeProject) -> StudioTakeState:
    return StudioTakeState(
        session_id=project.session_id,
        take_id=project.take_id,
        tracks=tuple(StudioTrackState(track.track_id) for track in project.tracks),
    )


def _read_sidecar(path: Path) -> StudioTakeState:
    """Parse bounded sidecar data without accepting redirects or partial truth."""
    if path.is_symlink():
        raise StudioStateError("Studio state sidecar must not be a symbolic link.")
    try:
        if not path.is_file():
            raise StudioStateError("Studio state sidecar is not a regular file.")
        with path.open("rb") as handle:
            payload_bytes = handle.read(MAX_STUDIO_STATE_BYTES + 1)
    except OSError as exc:
        raise StudioStateError("Could not read Studio state sidecar.") from exc
    if len(payload_bytes) > MAX_STUDIO_STATE_BYTES:
        raise StudioStateError("Studio state sidecar is too large.")
    try:
        value = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StudioStateError("Studio state sidecar is not valid JSON.") from exc
    if not isinstance(value, Mapping):
        raise StudioStateError("Studio state sidecar root must be an object.")
    if value.get("schema_version") != STUDIO_STATE_SCHEMA_VERSION:
        raise StudioStateError("Studio state sidecar has an unsupported schema.")
    raw_tracks = value.get("tracks")
    if not isinstance(raw_tracks, list):
        raise StudioStateError("Studio state sidecar tracks must be a list.")
    if len(raw_tracks) > MAX_STUDIO_TRACKS:
        raise StudioStateError("Studio state sidecar has too many tracks.")
    if any(not isinstance(item, Mapping) for item in raw_tracks):
        raise StudioStateError("Studio state sidecar contains an invalid track.")
    return StudioTakeState(
        session_id=value.get("session_id", ""),
        take_id=value.get("take_id", ""),
        tracks=tuple(StudioTrackState.from_dict(item) for item in raw_tracks),
    )


def _reconcile_state(project: TakeProject, state: StudioTakeState) -> StudioTakeState:
    """Match choices by durable ID while defaulting new tracks and dropping stale ones."""
    if state.take_id != project.take_id or state.session_id != project.session_id:
        raise StudioStateError("Studio state belongs to a different take.")
    saved = {item.track_id: item for item in state.tracks}
    return StudioTakeState(
        session_id=project.session_id,
        take_id=project.take_id,
        tracks=tuple(
            saved.get(track.track_id, StudioTrackState(track.track_id))
            for track in project.tracks
        ),
    )


def load_studio_state(take_dir: str | Path) -> StudioTakeState:
    """Load reconciled Studio state for a schema-v2 take.

    A take with no sidecar returns bounded defaults for all current tracks.
    A sidecar with malformed data or another take's identity raises
    :class:`StudioStateError`; callers should leave it untouched and present
    the default/recovery choice rather than applying untrusted settings.
    """
    folder, project = _load_schema_v2_project(take_dir)
    path = folder / STUDIO_STATE_FILENAME
    try:
        path.lstat()
    except FileNotFoundError:
        return _default_state(project)
    except OSError as exc:
        raise StudioStateError("Could not inspect Studio state sidecar.") from exc
    return _reconcile_state(project, _read_sidecar(path))


def save_studio_state(take_dir: str | Path, state: StudioTakeState) -> Path:
    """Atomically persist Studio choices beside their matching schema-v2 take.

    The current project is reloaded before writing, so a state object from a
    different take cannot be saved into this folder.  Added/reordered tracks
    are reconciled by durable ID, while stale IDs are not persisted.  Only the
    hidden sidecar is replaced; source audio and ``webjam-take.json`` remain
    untouched.
    """
    if not isinstance(state, StudioTakeState):
        raise StudioStateError("Studio state must be a StudioTakeState value.")
    folder, project = _load_schema_v2_project(take_dir)
    reconciled = _reconcile_state(project, state)
    path = folder / STUDIO_STATE_FILENAME
    if path.is_symlink():
        raise StudioStateError("Studio state sidecar must not be a symbolic link.")
    payload = json.dumps(reconciled.to_dict(), indent=2, sort_keys=False) + "\n"
    atomic_write_text(path, payload, mode=0o600)
    return path


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
