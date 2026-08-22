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
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.creative_modes import canonical_creator_profile_key
from core.jamulus_roster_identity import MAX_JAMULUS_ROSTER_ROWS
from core.local_capture import LocalCaptureTrack
from core.logical_sources import canonical_logical_source_id, derive_logical_source_id
from core.recording_readiness import RecordingStorageCheck, RecordingStorageStatus

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_NAME_CHARS = 128
_MAX_INPUT_TRACKS = 32
_MAX_CAPTURE_CHANNELS = 32
_MAX_CAPTURE_STEM_CHARS = 64
_MAX_FRAMES = 2**31
_MAX_STORAGE_BYTES = (1 << 63) - 1
_MAX_STORAGE_DETAIL_CHARS = 512
_MAX_GENERATION = (1 << 63) - 1
_MAX_EXPECTED_SOURCES = (
    MAX_JAMULUS_ROSTER_ROWS * (_MAX_INPUT_TRACKS + 1) + _MAX_INPUT_TRACKS + 1
)

SESSION_RECORDING_PLAN_PRIVATE_SCHEMA_VERSION = 2
_PRIVATE_PLAN_KEYS = {
    "schema_version",
    "session_id",
    "take_id",
    "plan_generation",
    "roster",
    "expected_server_stems",
    "server_logical_source_ids",
    "server_channel_counts",
    "count_in_frames",
    "pre_roll_frames",
    "storage",
    "expected_source_count",
    "created_at_utc",
    "shared_track",
    "shared_track_planned",
    "input_maps",
    "input_map_logical_source_ids",
    "guest_local_originals",
    "creator_profile_key",
    "plan_fingerprint_sha256",
}


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


def _strict_mapping(
    value: object,
    label: str,
    expected_keys: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object.")
    if set(value) != expected_keys:
        raise ValueError(f"{label} fields do not match the private schema.")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} contains a non-text field name.")
    return value


def _strict_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    cleaned = _clean_identity(value, label)
    if cleaned != value:
        raise ValueError(f"{label} must use its canonical text form.")
    return cleaned


def _strict_int(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer.")
    if value < minimum:
        raise ValueError(f"{label} is outside the supported limits.")
    return value


def _strict_bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean.")
    return value


def _strict_creator_profile_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("creator_profile_key must be text.")
    canonical = canonical_creator_profile_key(value)
    if canonical is None or canonical != value:
        raise ValueError("creator_profile_key must be a canonical profile key.")
    return canonical


def _strict_storage_detail(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("storage.detail must be text.")
    if (
        not value
        or value.strip() != value
        or len(value) > _MAX_STORAGE_DETAIL_CHARS
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        raise ValueError("storage.detail must be bounded canonical text.")
    return value


def _strict_storage_bytes(
    value: object,
    label: str,
    *,
    optional: bool = False,
) -> int | None:
    if optional and value is None:
        return None
    integer = _strict_int(value, label)
    if integer > _MAX_STORAGE_BYTES:
        raise ValueError(f"{label} is outside the supported limits.")
    return integer


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
        if not 1 <= self.playback_generation <= _MAX_GENERATION:
            raise ValueError("playback_generation is outside the supported limits.")
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
        if isinstance(self.channel_count, bool) or self.channel_count not in (1, 2):
            raise ValueError("channel_count must be 1 (mono) or 2 (stereo).")
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean.")
        if type(self.local_original_enabled) is not bool:
            raise ValueError("local_original_enabled must be a boolean.")

    def __repr__(self) -> str:
        return "InputMapBinding(private=[redacted])"


@dataclass(frozen=True, repr=False)
class GuestLocalOriginalBinding:
    """One authenticated guest's exact path-free pre-take inventory."""

    participant_id: str
    track_count: int
    map_fingerprint_sha256: str
    presence_generation: int
    channel_counts: tuple[int, ...] = ()
    logical_source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "participant_id",
            _clean_identity(self.participant_id, "participant_id"),
        )
        if (
            isinstance(self.track_count, bool)
            or not isinstance(self.track_count, int)
            or not 0 <= self.track_count <= _MAX_INPUT_TRACKS
        ):
            raise ValueError("guest track_count is outside the supported limits.")
        fingerprint = str(self.map_fingerprint_sha256 or "").lower()
        if not _SHA256_RE.fullmatch(fingerprint):
            raise ValueError("guest map fingerprint must be a SHA-256 digest.")
        object.__setattr__(self, "map_fingerprint_sha256", fingerprint)
        if (
            isinstance(self.presence_generation, bool)
            or not isinstance(self.presence_generation, int)
            or not 0 <= self.presence_generation <= _MAX_GENERATION
        ):
            raise ValueError("guest presence_generation is outside the limit.")
        channel_counts = tuple(self.channel_counts)
        logical_source_ids = tuple(self.logical_source_ids)
        if bool(channel_counts) != bool(logical_source_ids):
            raise ValueError(
                "guest topology widths and logical source IDs must be declared together."
            )
        if channel_counts:
            if len(channel_counts) != self.track_count:
                raise ValueError("guest topology must describe every logical track.")
            if any(
                isinstance(width, bool) or width not in (1, 2)
                for width in channel_counts
            ):
                raise ValueError("guest logical tracks must be mono or stereo.")
            logical_source_ids = tuple(
                canonical_logical_source_id(value) for value in logical_source_ids
            )
            if len(set(logical_source_ids)) != len(logical_source_ids):
                raise ValueError("guest logical source IDs must be unique.")
        object.__setattr__(self, "channel_counts", channel_counts)
        object.__setattr__(self, "logical_source_ids", logical_source_ids)

    @property
    def exact_topology(self) -> bool:
        return len(self.channel_counts) == self.track_count and (
            self.track_count == 0 or bool(self.logical_source_ids)
        )

    def __repr__(self) -> str:
        return "GuestLocalOriginalBinding(private=[redacted])"


LEGACY_CAPTURE_TRACKS: tuple[LocalCaptureTrack, ...] = (
    LocalCaptureTrack("host-guitar", (0,)),
    LocalCaptureTrack("host-vocal", (1,)),
)
_CAPTURE_STEM_SAFE_RE = re.compile(r"[^A-Za-z0-9 _-]+")


def _capture_stem(name: str, index: int) -> str:
    """Deterministically sanitize one configured name into a capture stem."""

    if str(name or "") in {track.stem for track in LEGACY_CAPTURE_TRACKS}:
        return str(name)
    cleaned = _CAPTURE_STEM_SAFE_RE.sub("-", str(name or ""))
    cleaned = " ".join(cleaned.split())[:58].strip(" -_")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = f"track-{index + 1}"
    return f"local-{cleaned}"[:_MAX_CAPTURE_STEM_CHARS].rstrip(" -_")


def resolve_capture_tracks(settings: object) -> tuple[LocalCaptureTrack, ...]:
    """The logical mono/stereo capture-track list for one take.

    Configured, enabled Local-Original entries map onto sequential device
    channels in list order. A stereo entry remains one logical track mapped
    to two adjacent channels and therefore becomes one true two-channel WAV.
    Configured stems carry the ``local-`` prefix so take classification
    recognizes them. With local capture enabled but no valid configuration,
    the legacy fixed pair applies unchanged; with local capture disabled,
    nothing is captured. Entries that are enabled but not Local Originals are
    skipped without consuming channels.
    """

    if not bool(getattr(settings, "local_capture_enabled", False)):
        return ()
    raw = getattr(settings, "input_maps", None)
    bindings = configured_input_map_bindings(settings)
    if not bindings:
        # Only the genuinely empty/default configuration retains the legacy
        # pair. A malformed non-empty map fails closed instead of unexpectedly
        # recording the first two inputs.
        return LEGACY_CAPTURE_TRACKS if raw in (None, []) else ()
    tracks: list[LocalCaptureTrack] = []
    seen: set[str] = set()
    channel = 0
    for index, binding in enumerate(bindings):
        if not binding.enabled or not binding.local_original_enabled:
            continue
        base = _capture_stem(binding.track_name, index)
        if channel + binding.channel_count > _MAX_CAPTURE_CHANNELS:
            return ()
        unique = base
        suffix = 2
        while unique.casefold() in seen:
            suffix_text = f"-{suffix}"
            unique = (
                base[: _MAX_CAPTURE_STEM_CHARS - len(suffix_text)].rstrip(" -_")
                + suffix_text
            )
            suffix += 1
        seen.add(unique.casefold())
        tracks.append(
            LocalCaptureTrack(
                stem=unique,
                source_channels=tuple(range(channel, channel + binding.channel_count)),
                logical_source_ordinal=index,
            )
        )
        channel += binding.channel_count
    # A valid map with every row disabled or opted out means no Local Original
    # capture. It must never fall back to the legacy pair.
    return tuple(tracks)


def configured_input_map_bindings(
    settings: object,
) -> tuple[InputMapBinding, ...]:
    """Parse the musician's configured input maps from settings.

    Returns only strictly valid bindings (the settings loader already
    fail-safes malformed lists to empty). The capture resolver consumes these
    bindings directly and enforces the separate 32-channel capture ceiling.
    """

    raw = getattr(settings, "input_maps", None)
    if not isinstance(raw, list):
        return ()
    if len(raw) > _MAX_INPUT_TRACKS:
        return ()
    bindings: list[InputMapBinding] = []
    try:
        for entry in raw:
            if not isinstance(entry, dict):
                return ()
            bindings.append(
                InputMapBinding(
                    track_name=entry.get("name", ""),
                    channel_count=entry.get("channels", 0),
                    enabled=entry.get("enabled", True),
                    local_original_enabled=entry.get("local_original_enabled", False),
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
    creator_profile_key: str = "music"
    guest_local_originals: tuple[GuestLocalOriginalBinding, ...] = field(
        default_factory=tuple
    )
    # Ordered widths aligned with expected_server_stems. Empty is a legacy or
    # currently-unproven recorder contract and must not be treated as exact.
    server_channel_counts: tuple[int, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "session_id", _clean_identity(self.session_id, "session_id")
        )
        object.__setattr__(self, "take_id", _clean_identity(self.take_id, "take_id"))
        if isinstance(self.plan_generation, bool) or not isinstance(
            self.plan_generation, int
        ):
            raise ValueError("plan_generation must be an integer.")
        if not 1 <= self.plan_generation <= _MAX_GENERATION:
            raise ValueError("plan_generation is outside the supported limits.")

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
        if len({participant_id for participant_id, _name in roster}) != len(roster):
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
        server_channel_counts = tuple(self.server_channel_counts)
        if server_channel_counts:
            if len(server_channel_counts) != len(stems):
                raise ValueError(
                    "server_channel_counts must describe every planned server source."
                )
            if any(
                isinstance(width, bool) or width not in (1, 2)
                for width in server_channel_counts
            ):
                raise ValueError("planned server sources must be mono or stereo.")
        object.__setattr__(self, "server_channel_counts", server_channel_counts)

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
        if not isinstance(self.storage.status, RecordingStorageStatus):
            raise ValueError("storage.status must be a RecordingStorageStatus.")
        _strict_storage_detail(self.storage.detail)
        _strict_storage_bytes(
            self.storage.free_bytes,
            "storage.free_bytes",
            optional=True,
        )
        _strict_storage_bytes(self.storage.required_bytes, "storage.required_bytes")
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
        if not 1 <= self.expected_source_count <= _MAX_EXPECTED_SOURCES:
            raise ValueError("expected_source_count is outside the supported limits.")

        created = _clean_identity(self.created_at_utc, "created_at_utc")
        object.__setattr__(self, "created_at_utc", created)

        if type(self.shared_track_planned) is not bool:
            raise ValueError("shared_track_planned must be a boolean.")
        if self.shared_track is not None:
            if not isinstance(self.shared_track, SharedTrackBinding):
                raise ValueError("shared_track must be a SharedTrackBinding.")
            if not self.shared_track_planned:
                raise ValueError("a bound shared_track requires shared_track_planned.")

        input_maps = tuple(self.input_maps)
        if len(input_maps) > _MAX_INPUT_TRACKS:
            raise ValueError("input_maps exceeds the 32-track limit.")
        for entry in input_maps:
            if not isinstance(entry, InputMapBinding):
                raise ValueError("input_maps entries must be InputMapBinding.")
        capture_channels = sum(
            entry.channel_count
            for entry in input_maps
            if entry.enabled and entry.local_original_enabled
        )
        if capture_channels > _MAX_CAPTURE_CHANNELS:
            raise ValueError("input_maps exceeds the 32-channel capture limit.")
        names = [entry.track_name for entry in input_maps]
        if len(set(names)) != len(names):
            raise ValueError("input map track names must be unique.")
        object.__setattr__(self, "input_maps", input_maps)
        creator_profile_key = canonical_creator_profile_key(self.creator_profile_key)
        if creator_profile_key is None:
            raise ValueError("creator_profile_key is unsupported.")
        object.__setattr__(
            self,
            "creator_profile_key",
            creator_profile_key,
        )
        guest_local_originals = tuple(self.guest_local_originals)
        if len(guest_local_originals) > MAX_JAMULUS_ROSTER_ROWS:
            raise ValueError("guest_local_originals exceeds the participant limit.")
        if any(
            not isinstance(item, GuestLocalOriginalBinding)
            for item in guest_local_originals
        ):
            raise ValueError(
                "guest_local_originals entries must be GuestLocalOriginalBinding."
            )
        guest_ids = [item.participant_id for item in guest_local_originals]
        if len(set(guest_ids)) != len(guest_ids):
            raise ValueError("guest Local Original participant ids must be unique.")
        if not set(guest_ids).issubset(set(stems)):
            raise ValueError(
                "guest Local Original participants must be planned server sources."
            )
        object.__setattr__(self, "guest_local_originals", guest_local_originals)

        host_local_source_ids = tuple(
            self.input_map_logical_source_ids[ordinal]
            for ordinal, entry in enumerate(input_maps)
            if entry.enabled and entry.local_original_enabled
        )
        all_logical_source_ids = (
            *self.server_logical_source_ids,
            *host_local_source_ids,
            *(
                source_id
                for guest in guest_local_originals
                for source_id in guest.logical_source_ids
            ),
        )
        if len(set(all_logical_source_ids)) != len(all_logical_source_ids):
            raise ValueError("planned logical source IDs must be globally unique.")

        host_local_count = sum(
            1 for entry in input_maps if entry.enabled and entry.local_original_enabled
        )
        exact_source_count = (
            len(stems)
            + host_local_count
            + sum(item.track_count for item in guest_local_originals)
        )
        if self.expected_source_count != exact_source_count:
            raise ValueError(
                "expected_source_count must exactly match every planned source."
            )

    def __repr__(self) -> str:
        return "SessionRecordingPlan(private=[redacted])"

    @property
    def server_logical_source_ids(self) -> tuple[str, ...]:
        """Stable IDs aligned one-for-one with ``expected_server_stems``."""

        return tuple(
            derive_logical_source_id(
                self.session_id,
                participant_id,
                "jamulus_server",
            )
            for participant_id in self.expected_server_stems
        )

    @property
    def input_map_logical_source_ids(self) -> tuple[str, ...]:
        """Stable host input-slot IDs aligned with every configured map row."""

        return tuple(
            derive_logical_source_id(
                self.session_id,
                "host-local-original",
                "local_original",
                ordinal,
            )
            for ordinal, _entry in enumerate(self.input_maps)
        )

    def logical_source_id_for_server(self, participant_id: str) -> str:
        """Return the planned server source ID, or empty for an unplanned ID."""

        try:
            ordinal = self.expected_server_stems.index(str(participant_id))
        except ValueError:
            return ""
        return self.server_logical_source_ids[ordinal]

    @property
    def server_topology_exact(self) -> bool:
        return len(self.server_channel_counts) == len(self.expected_server_stems)

    def channel_count_for_server(self, participant_id: str) -> int | None:
        """Return a proven planned width, never an inferred default."""

        if not self.server_topology_exact:
            return None
        try:
            ordinal = self.expected_server_stems.index(str(participant_id))
        except ValueError:
            return None
        return self.server_channel_counts[ordinal]

    def resolved_capture_tracks(self) -> tuple[LocalCaptureTrack, ...]:
        """Build the exact typed host capture map bound by this plan.

        Controllers should use this after planning instead of resolving mutable
        settings a second time.
        """

        tracks: list[LocalCaptureTrack] = []
        channel = 0
        seen: set[str] = set()
        for ordinal, entry in enumerate(self.input_maps):
            if not entry.enabled or not entry.local_original_enabled:
                continue
            base = _capture_stem(entry.track_name, ordinal)
            unique = base
            suffix = 2
            while unique.casefold() in seen:
                suffix_text = f"-{suffix}"
                unique = (
                    base[: _MAX_CAPTURE_STEM_CHARS - len(suffix_text)].rstrip(" -_")
                    + suffix_text
                )
                suffix += 1
            seen.add(unique.casefold())
            tracks.append(
                LocalCaptureTrack(
                    unique,
                    tuple(range(channel, channel + entry.channel_count)),
                    logical_source_id=self.input_map_logical_source_ids[ordinal],
                    logical_source_ordinal=ordinal,
                )
            )
            channel += entry.channel_count
        return tuple(tracks)

    def to_public_dict(self) -> dict[str, object]:
        """A bounded, path-free projection for diagnostics and manifests."""

        return {
            "schema_version": 2,
            "session_id": self.session_id,
            "take_id": self.take_id,
            "plan_generation": self.plan_generation,
            "roster_count": len(self.roster),
            "expected_server_stem_count": len(self.expected_server_stems),
            "server_topology_bound": self.server_topology_exact,
            "count_in_frames": self.count_in_frames,
            "pre_roll_frames": self.pre_roll_frames,
            "storage_status": self.storage.status.value,
            "storage_required_bytes": self.storage.required_bytes,
            "expected_source_count": self.expected_source_count,
            "shared_track_planned": self.shared_track_planned,
            "shared_track_bound": self.shared_track is not None,
            "input_map_count": len(self.input_maps),
            "guest_local_original_participant_count": len(self.guest_local_originals),
            "guest_local_original_track_count": sum(
                item.track_count for item in self.guest_local_originals
            ),
            "creator_profile_key": self.creator_profile_key,
            "created_at_utc": self.created_at_utc,
        }

    def _private_facts(self) -> dict[str, object]:
        """Return the canonical full-fidelity facts covered by the digest."""

        return {
            "schema_version": SESSION_RECORDING_PLAN_PRIVATE_SCHEMA_VERSION,
            "session_id": self.session_id,
            "take_id": self.take_id,
            "plan_generation": self.plan_generation,
            "roster": [
                {
                    "participant_id": participant_id,
                    "display_name": display_name,
                }
                for participant_id, display_name in self.roster
            ],
            "expected_server_stems": list(self.expected_server_stems),
            "server_logical_source_ids": list(self.server_logical_source_ids),
            "server_channel_counts": list(self.server_channel_counts),
            "count_in_frames": self.count_in_frames,
            "pre_roll_frames": self.pre_roll_frames,
            "storage": {
                "status": self.storage.status.value,
                "detail": self.storage.detail,
                "free_bytes": self.storage.free_bytes,
                "required_bytes": self.storage.required_bytes,
            },
            "expected_source_count": self.expected_source_count,
            "created_at_utc": self.created_at_utc,
            "shared_track": (
                None
                if self.shared_track is None
                else {
                    "source_fingerprint_sha256": (
                        self.shared_track.source_fingerprint_sha256
                    ),
                    "playback_generation": self.shared_track.playback_generation,
                }
            ),
            "shared_track_planned": self.shared_track_planned,
            "input_maps": [
                {
                    "track_name": entry.track_name,
                    "channel_count": entry.channel_count,
                    "enabled": entry.enabled,
                    "local_original_enabled": entry.local_original_enabled,
                }
                for entry in self.input_maps
            ],
            "input_map_logical_source_ids": list(self.input_map_logical_source_ids),
            "guest_local_originals": [
                {
                    "participant_id": entry.participant_id,
                    "track_count": entry.track_count,
                    "map_fingerprint_sha256": entry.map_fingerprint_sha256,
                    "presence_generation": entry.presence_generation,
                    "channel_counts": list(entry.channel_counts),
                    "logical_source_ids": list(entry.logical_source_ids),
                }
                for entry in self.guest_local_originals
            ],
            "creator_profile_key": self.creator_profile_key,
        }

    def to_private_dict(self) -> dict[str, object]:
        """Serialize every plan fact for private, permission-protected storage.

        Unlike :meth:`to_public_dict`, this contains participant names, input
        labels, and the Shared Track source digest. Callers must never expose
        it through diagnostics or UI. The embedded fingerprint covers the
        complete canonical payload and is revalidated during deserialization.
        """

        payload = self._private_facts()
        payload["plan_fingerprint_sha256"] = self.plan_fingerprint()
        return payload

    @classmethod
    def from_private_dict(
        cls,
        value: Mapping[str, Any],
        *,
        expected_take_id: str | None = None,
        expected_fingerprint_sha256: str | None = None,
    ) -> SessionRecordingPlan:
        """Rebuild and authenticate a private plan payload.

        The wire shape is exact: missing, additional, incorrectly typed, or
        non-canonical values fail closed. The supplied fingerprint and any
        caller-provided take/fingerprint binding are checked only after all
        nested values have been reconstructed through the typed dataclasses.
        """

        payload = _strict_mapping(value, "recording plan", _PRIVATE_PLAN_KEYS)
        schema_version = _strict_int(
            payload["schema_version"],
            "recording plan schema_version",
            minimum=1,
        )
        if schema_version != SESSION_RECORDING_PLAN_PRIVATE_SCHEMA_VERSION:
            raise ValueError("Unsupported private recording plan schema.")

        raw_roster = payload["roster"]
        if not isinstance(raw_roster, list):
            raise ValueError("recording plan roster must be a list.")
        roster: list[tuple[str, str]] = []
        for raw_entry in raw_roster:
            entry = _strict_mapping(
                raw_entry,
                "recording plan roster entry",
                {"participant_id", "display_name"},
            )
            roster.append(
                (
                    _strict_text(entry["participant_id"], "participant_id"),
                    _strict_text(entry["display_name"], "display_name"),
                )
            )

        raw_stems = payload["expected_server_stems"]
        if not isinstance(raw_stems, list):
            raise ValueError("expected_server_stems must be a list.")
        stems = tuple(_strict_text(stem, "expected_server_stem") for stem in raw_stems)

        raw_server_source_ids = payload["server_logical_source_ids"]
        if not isinstance(raw_server_source_ids, list):
            raise ValueError("server_logical_source_ids must be a list.")
        server_source_ids = tuple(
            canonical_logical_source_id(value) for value in raw_server_source_ids
        )
        raw_server_channel_counts = payload["server_channel_counts"]
        if not isinstance(raw_server_channel_counts, list):
            raise ValueError("server_channel_counts must be a list.")
        server_channel_counts = tuple(raw_server_channel_counts)

        raw_storage = _strict_mapping(
            payload["storage"],
            "recording plan storage",
            {"status", "detail", "free_bytes", "required_bytes"},
        )
        raw_status = raw_storage["status"]
        if not isinstance(raw_status, str):
            raise ValueError("storage.status must be text.")
        try:
            storage_status = RecordingStorageStatus(raw_status)
        except ValueError as exc:
            raise ValueError("storage.status is unsupported.") from exc
        storage = RecordingStorageCheck(
            status=storage_status,
            detail=_strict_storage_detail(raw_storage["detail"]),
            free_bytes=_strict_storage_bytes(
                raw_storage["free_bytes"],
                "storage.free_bytes",
                optional=True,
            ),
            required_bytes=_strict_storage_bytes(
                raw_storage["required_bytes"],
                "storage.required_bytes",
            ),
        )

        raw_shared_track = payload["shared_track"]
        shared_track: SharedTrackBinding | None
        if raw_shared_track is None:
            shared_track = None
        else:
            shared_payload = _strict_mapping(
                raw_shared_track,
                "recording plan shared_track",
                {"source_fingerprint_sha256", "playback_generation"},
            )
            source_fingerprint = shared_payload["source_fingerprint_sha256"]
            if not isinstance(source_fingerprint, str) or not _SHA256_RE.fullmatch(
                source_fingerprint
            ):
                raise ValueError("Shared Track fingerprint is invalid.")
            shared_track = SharedTrackBinding(
                source_fingerprint_sha256=source_fingerprint,
                playback_generation=_strict_int(
                    shared_payload["playback_generation"],
                    "playback_generation",
                    minimum=1,
                ),
            )

        raw_input_maps = payload["input_maps"]
        if not isinstance(raw_input_maps, list):
            raise ValueError("recording plan input_maps must be a list.")
        input_maps: list[InputMapBinding] = []
        for raw_entry in raw_input_maps:
            entry = _strict_mapping(
                raw_entry,
                "recording plan input map",
                {
                    "track_name",
                    "channel_count",
                    "enabled",
                    "local_original_enabled",
                },
            )
            input_maps.append(
                InputMapBinding(
                    track_name=_strict_text(entry["track_name"], "track_name"),
                    channel_count=_strict_int(
                        entry["channel_count"],
                        "channel_count",
                        minimum=1,
                    ),
                    enabled=_strict_bool(entry["enabled"], "enabled"),
                    local_original_enabled=_strict_bool(
                        entry["local_original_enabled"],
                        "local_original_enabled",
                    ),
                )
            )

        raw_input_source_ids = payload["input_map_logical_source_ids"]
        if not isinstance(raw_input_source_ids, list):
            raise ValueError("input_map_logical_source_ids must be a list.")
        input_source_ids = tuple(
            canonical_logical_source_id(value) for value in raw_input_source_ids
        )

        raw_guest_local_originals = payload["guest_local_originals"]
        if not isinstance(raw_guest_local_originals, list):
            raise ValueError("recording plan guest_local_originals must be a list.")
        guest_local_originals: list[GuestLocalOriginalBinding] = []
        for raw_entry in raw_guest_local_originals:
            entry = _strict_mapping(
                raw_entry,
                "recording plan guest Local Original",
                {
                    "participant_id",
                    "track_count",
                    "map_fingerprint_sha256",
                    "presence_generation",
                    "channel_counts",
                    "logical_source_ids",
                },
            )
            map_fingerprint = entry["map_fingerprint_sha256"]
            if not isinstance(map_fingerprint, str) or not _SHA256_RE.fullmatch(
                map_fingerprint
            ):
                raise ValueError("guest map fingerprint is invalid.")
            raw_channel_counts = entry["channel_counts"]
            raw_logical_source_ids = entry["logical_source_ids"]
            if not isinstance(raw_channel_counts, list):
                raise ValueError("guest channel_counts must be a list.")
            if not isinstance(raw_logical_source_ids, list):
                raise ValueError("guest logical_source_ids must be a list.")
            guest_local_originals.append(
                GuestLocalOriginalBinding(
                    participant_id=_strict_text(
                        entry["participant_id"], "participant_id"
                    ),
                    track_count=_strict_int(entry["track_count"], "track_count"),
                    map_fingerprint_sha256=map_fingerprint,
                    presence_generation=_strict_int(
                        entry["presence_generation"], "presence_generation"
                    ),
                    channel_counts=tuple(raw_channel_counts),
                    logical_source_ids=tuple(raw_logical_source_ids),
                )
            )

        serialized_fingerprint = payload["plan_fingerprint_sha256"]
        if not isinstance(serialized_fingerprint, str) or not _SHA256_RE.fullmatch(
            serialized_fingerprint
        ):
            raise ValueError("recording plan fingerprint is invalid.")

        plan = cls(
            session_id=_strict_text(payload["session_id"], "session_id"),
            take_id=_strict_text(payload["take_id"], "take_id"),
            plan_generation=_strict_int(
                payload["plan_generation"],
                "plan_generation",
                minimum=1,
            ),
            roster=tuple(roster),
            expected_server_stems=stems,
            count_in_frames=_bounded_frames(
                payload["count_in_frames"], "count_in_frames"
            ),
            pre_roll_frames=_bounded_frames(
                payload["pre_roll_frames"], "pre_roll_frames"
            ),
            storage=storage,
            expected_source_count=_strict_int(
                payload["expected_source_count"],
                "expected_source_count",
                minimum=1,
            ),
            created_at_utc=_strict_text(payload["created_at_utc"], "created_at_utc"),
            shared_track=shared_track,
            shared_track_planned=_strict_bool(
                payload["shared_track_planned"], "shared_track_planned"
            ),
            input_maps=tuple(input_maps),
            creator_profile_key=_strict_creator_profile_key(
                payload["creator_profile_key"]
            ),
            guest_local_originals=tuple(guest_local_originals),
            server_channel_counts=server_channel_counts,
        )
        actual_fingerprint = plan.plan_fingerprint()
        if server_source_ids != plan.server_logical_source_ids:
            raise ValueError("server logical source IDs do not match the plan.")
        if input_source_ids != plan.input_map_logical_source_ids:
            raise ValueError("input-map logical source IDs do not match the plan.")
        if not hmac.compare_digest(serialized_fingerprint, actual_fingerprint):
            raise ValueError("recording plan fingerprint does not match its facts.")
        if expected_take_id is not None and (
            not isinstance(expected_take_id, str)
            or plan.take_id != expected_take_id
        ):
            raise ValueError("recording plan take identity does not match.")
        if expected_fingerprint_sha256 is not None:
            if not isinstance(
                expected_fingerprint_sha256, str
            ) or not _SHA256_RE.fullmatch(expected_fingerprint_sha256):
                raise ValueError("expected recording plan fingerprint is invalid.")
            if not hmac.compare_digest(expected_fingerprint_sha256, actual_fingerprint):
                raise ValueError("recording plan fingerprint binding does not match.")
        return plan

    def plan_fingerprint(self) -> str:
        """A stable digest binding every planned fact for this take.

        The finalization gate and the take manifest record this value so a
        result produced under different facts can never masquerade as this
        plan's outcome.
        """

        payload = json.dumps(
            self._private_facts(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
