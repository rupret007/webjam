"""One immutable, authoritative pre-record plan for Record Session.

Before recording begins, the host assembles exactly one
:class:`SessionRecordingPlan` under one generation. It consolidates the
binding facts that already live across the recorder schedule, storage
readiness, roster proof, and Shared Track evidence into a single frozen
object the state machine, finalization gate, and take manifest can all
reference — so no phase can quietly disagree about what this take was
supposed to capture.

The plan is facts, not behavior: it never starts, stops, or owns audio.
Constructing one fails closed on any invalid, ambiguous, or
action-needed input (for example storage that cannot accept a recording).
Its repr is redacted and its public projection is path-free, matching the
support-bundle discipline.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from core.jamulus_roster_identity import MAX_JAMULUS_ROSTER_ROWS
from core.recording_readiness import RecordingStorageCheck, RecordingStorageStatus

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_NAME_CHARS = 128
_MAX_INPUT_TRACKS = 32
_MAX_FRAMES = 2**31


def _clean_identity(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > _MAX_NAME_CHARS:
        raise ValueError(f"{label} must be a bounded, non-empty string.")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in text):
        raise ValueError(f"{label} must not include control characters.")
    return text


def _bounded_frames(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer frame count.")
    if not 0 <= value <= _MAX_FRAMES:
        raise ValueError(f"{label} is outside the supported limits.")
    return value


@dataclass(frozen=True, repr=False)
class SharedTrackBinding:
    """The exact Shared Track this take plays against, or nothing.

    Identity is the source fingerprint, never a filename; a plan with a
    binding whose fingerprint cannot be proven must not be constructed.
    """

    source_fingerprint_sha256: str
    playback_generation: int

    def __post_init__(self) -> None:
        fingerprint = str(self.source_fingerprint_sha256 or "").lower()
        if not _SHA256_RE.fullmatch(fingerprint):
            raise ValueError(
                "Shared Track binding requires a proven source fingerprint."
            )
        if isinstance(self.playback_generation, bool) or not isinstance(
            self.playback_generation, int
        ):
            raise ValueError("playback_generation must be an integer.")
        if self.playback_generation < 1:
            raise ValueError("playback_generation must be positive.")
        object.__setattr__(self, "source_fingerprint_sha256", fingerprint)

    def __repr__(self) -> str:
        return "SharedTrackBinding(private=[redacted])"


@dataclass(frozen=True, repr=False)
class InputMapBinding:
    """One locally captured input the musician configured for this take."""

    track_name: str
    channel_count: int
    enabled: bool = True
    local_original_enabled: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "track_name", _clean_identity(self.track_name, "track_name")
        )
        if self.channel_count not in (1, 2):
            raise ValueError("channel_count must be 1 (mono) or 2 (stereo).")
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean.")
        if type(self.local_original_enabled) is not bool:
            raise ValueError("local_original_enabled must be a boolean.")

    def __repr__(self) -> str:
        return "InputMapBinding(private=[redacted])"


def configured_input_map_bindings(
    settings: object,
) -> tuple[InputMapBinding, ...]:
    """Parse the musician's configured input maps from settings.

    Returns only strictly valid bindings (the settings loader already
    fail-safes malformed lists to empty). NOTE: until the capture layer is
    generalized, an enabled local capture records the fixed two host stems
    regardless of this configuration; the SessionRecordingPlan therefore
    binds capture truth, not this wish-list. This helper feeds the editor
    UI and becomes authoritative only when capture consumes it.
    """

    raw = getattr(settings, "input_maps", None)
    if not isinstance(raw, list):
        return ()
    bindings: list[InputMapBinding] = []
    try:
        for entry in raw[:_MAX_INPUT_TRACKS]:
            if not isinstance(entry, dict):
                return ()
            bindings.append(
                InputMapBinding(
                    track_name=entry.get("name", ""),
                    channel_count=entry.get("channels", 0),
                    enabled=bool(entry.get("enabled", True)),
                    local_original_enabled=bool(
                        entry.get("local_original_enabled", False)
                    ),
                )
            )
    except ValueError:
        return ()
    names = [binding.track_name for binding in bindings]
    if len(set(names)) != len(names):
        return ()
    return tuple(bindings)


@dataclass(frozen=True, repr=False)
class SessionRecordingPlan:
    """The single authoritative binding for one Record Session take."""

    session_id: str
    take_id: str
    plan_generation: int
    roster: tuple[tuple[str, str], ...]
    expected_server_stems: tuple[str, ...]
    count_in_frames: int
    pre_roll_frames: int
    storage: RecordingStorageCheck
    expected_source_count: int
    created_at_utc: str
    shared_track: SharedTrackBinding | None = None
    shared_track_planned: bool = False
    input_maps: tuple[InputMapBinding, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _clean_identity(self.session_id, "session_id")
        )
        object.__setattr__(
            self, "take_id", _clean_identity(self.take_id, "take_id")
        )
        if isinstance(self.plan_generation, bool) or not isinstance(
            self.plan_generation, int
        ):
            raise ValueError("plan_generation must be an integer.")
        if self.plan_generation < 1:
            raise ValueError("plan_generation must be positive.")

        roster = tuple(
            (
                _clean_identity(participant_id, "participant_id"),
                _clean_identity(display_name, "display_name"),
            )
            for participant_id, display_name in tuple(self.roster)
        )
        if not roster:
            raise ValueError("A recording plan requires a proven roster.")
        if len(roster) > MAX_JAMULUS_ROSTER_ROWS:
            raise ValueError("roster exceeds the supported limit.")
        if len({participant_id for participant_id, _name in roster}) != len(
            roster
        ):
            raise ValueError("roster participant ids must be unique.")
        object.__setattr__(self, "roster", roster)

        stems = tuple(
            _clean_identity(stem, "expected_server_stem")
            for stem in tuple(self.expected_server_stems)
        )
        if len(stems) > MAX_JAMULUS_ROSTER_ROWS:
            raise ValueError("expected_server_stems exceeds the limit.")
        if len(set(stems)) != len(stems):
            raise ValueError("expected_server_stems must be unique.")
        object.__setattr__(self, "expected_server_stems", stems)

        object.__setattr__(
            self,
            "count_in_frames",
            _bounded_frames(self.count_in_frames, "count_in_frames"),
        )
        object.__setattr__(
            self,
            "pre_roll_frames",
            _bounded_frames(self.pre_roll_frames, "pre_roll_frames"),
        )

        if not isinstance(self.storage, RecordingStorageCheck):
            raise ValueError("storage must be a RecordingStorageCheck.")
        if self.storage.status is RecordingStorageStatus.ACTION_NEEDED:
            # Fail closed: a plan must never exist for storage that cannot
            # accept the recording it describes.
            raise ValueError(
                "storage needs attention; resolve it before planning a take."
            )

        if isinstance(self.expected_source_count, bool) or not isinstance(
            self.expected_source_count, int
        ):
            raise ValueError("expected_source_count must be an integer.")
        if self.expected_source_count < 1:
            raise ValueError("expected_source_count must be at least 1.")

        created = _clean_identity(self.created_at_utc, "created_at_utc")
        object.__setattr__(self, "created_at_utc", created)

        if type(self.shared_track_planned) is not bool:
            raise ValueError("shared_track_planned must be a boolean.")
        if self.shared_track is not None:
            if not isinstance(self.shared_track, SharedTrackBinding):
                raise ValueError("shared_track must be a SharedTrackBinding.")
            if not self.shared_track_planned:
                raise ValueError(
                    "a bound shared_track requires shared_track_planned."
                )

        input_maps = tuple(self.input_maps)
        if len(input_maps) > _MAX_INPUT_TRACKS:
            raise ValueError("input_maps exceeds the 32-track limit.")
        for entry in input_maps:
            if not isinstance(entry, InputMapBinding):
                raise ValueError("input_maps entries must be InputMapBinding.")
        names = [entry.track_name for entry in input_maps]
        if len(set(names)) != len(names):
            raise ValueError("input map track names must be unique.")
        object.__setattr__(self, "input_maps", input_maps)

    def __repr__(self) -> str:
        return "SessionRecordingPlan(private=[redacted])"

    def to_public_dict(self) -> dict[str, object]:
        """A bounded, path-free projection for diagnostics and manifests."""

        return {
            "schema_version": 1,
            "session_id": self.session_id,
            "take_id": self.take_id,
            "plan_generation": self.plan_generation,
            "roster_count": len(self.roster),
            "expected_server_stem_count": len(self.expected_server_stems),
            "count_in_frames": self.count_in_frames,
            "pre_roll_frames": self.pre_roll_frames,
            "storage_status": self.storage.status.value,
            "storage_required_bytes": self.storage.required_bytes,
            "expected_source_count": self.expected_source_count,
            "shared_track_planned": self.shared_track_planned,
            "shared_track_bound": self.shared_track is not None,
            "input_map_count": len(self.input_maps),
            "created_at_utc": self.created_at_utc,
        }

    def plan_fingerprint(self) -> str:
        """A stable digest binding every planned fact for this take.

        The finalization gate and the take manifest record this value so a
        result produced under different facts can never masquerade as this
        plan's outcome.
        """

        canonical = {
            "session_id": self.session_id,
            "take_id": self.take_id,
            "plan_generation": self.plan_generation,
            "roster": list(list(entry) for entry in self.roster),
            "expected_server_stems": list(self.expected_server_stems),
            "count_in_frames": self.count_in_frames,
            "pre_roll_frames": self.pre_roll_frames,
            "storage_required_bytes": self.storage.required_bytes,
            "expected_source_count": self.expected_source_count,
            "shared_track_planned": self.shared_track_planned,
            "shared_track": (
                None
                if self.shared_track is None
                else [
                    self.shared_track.source_fingerprint_sha256,
                    self.shared_track.playback_generation,
                ]
            ),
            "input_maps": [
                [
                    entry.track_name,
                    entry.channel_count,
                    entry.enabled,
                    entry.local_original_enabled,
                ]
                for entry in self.input_maps
            ],
            "created_at_utc": self.created_at_utc,
        }
        payload = json.dumps(
            canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
