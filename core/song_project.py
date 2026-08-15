"""Strict, framework-neutral project model for WebJam Reference Studio.

Unlike :mod:`core.take_project`, a :class:`SongProject` is not evidence from a
completed rehearsal.  It is a durable songwriting document which can exist
before a take, collect immutable copies of source media, and later refer to
recordings and arrangement state by stable IDs.

The schema deliberately stores no absolute media paths.  Every media object is
an independently checksummed file below ``Media/`` and records only the
original file's basename.  ``original_read_only`` is an invariant, not a UI
preference: importing a file grants WebJam permission to read it and make a
project-owned copy, never to edit the original.
"""

from __future__ import annotations

import math
import re
import unicodedata
import uuid
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from core.creative_modes import canonical_creator_profile_key

SONG_PROJECT_SCHEMA_VERSION = 2
LEGACY_SONG_PROJECT_SCHEMA_VERSION = 1
DEFAULT_CREATOR_PROFILE_KEY = "music"
DEFAULT_PROJECT_SAMPLE_RATE = 48_000
MIN_PROJECT_SAMPLE_RATE = 8_000
MAX_PROJECT_SAMPLE_RATE = 384_000
MIN_TEMPO_BPM = 20.0
MAX_TEMPO_BPM = 400.0
MAX_PROJECT_TRACKS = 512
MAX_PROJECT_MEDIA = 20_000
MAX_MEDIA_FILE_BYTES = 512 * 1024 * 1024 * 1024
MAX_PROJECT_MEDIA_BYTES = 4 * 1024 * 1024 * 1024 * 1024
MAX_MEDIA_FRAMES = (1 << 63) - 1
MAX_TRACK_INPUT_CHANNELS = 64
MAX_PROJECT_REVISION = (1 << 63) - 1
MAX_PROJECT_NAME_BYTES = 512
MAX_TRACK_NAME_BYTES = 512
MAX_DEVICE_KEY_BYTES = 1_024
MAX_ORIGINAL_BASENAME_BYTES = 1_024
MAX_PROVENANCE_DETAIL_BYTES = 2_048

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FORMAT_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.+-]{0,31}$")


class SongProjectError(ValueError):
    """Raised when a project document violates the schema or an invariant."""


class MediaProvenance(str, Enum):
    """The user-visible origin category for one project-owned media copy."""

    LOCAL_FILE = "local_file"
    COMPLETED_TAKE = "completed_take"
    LOCAL_RECORDING = "local_recording"
    GENERATED = "generated"


class MediaImportMethod(str, Enum):
    """How bytes entered this bundle without describing an external path."""

    COPY = "copy"
    COLLECT_COPY = "collect_copy"
    RELINK_COPY = "relink_copy"
    RECORDING = "recording"
    RENDER = "render"


def _strict_keys(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    required: set[str],
    label: str,
) -> None:
    unknown = set(value).difference(allowed)
    if unknown:
        raise SongProjectError(
            f"{label} contains unsupported fields: "
            + ", ".join(sorted(str(item) for item in unknown))
            + "."
        )
    missing = required.difference(value)
    if missing:
        raise SongProjectError(
            f"{label} is missing required fields: "
            + ", ".join(sorted(missing))
            + "."
        )


def _strict_int(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SongProjectError(f"{label} must be an integer.")
    if not minimum <= value <= maximum:
        raise SongProjectError(f"{label} must be between {minimum} and {maximum}.")
    return value


def _strict_float(
    value: object,
    label: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SongProjectError(f"{label} must be a finite number.")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise SongProjectError(
            f"{label} must be between {minimum:g} and {maximum:g}."
        )
    return result


def _strict_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise SongProjectError(f"{label} must be true or false.")
    return value


def _uuid(value: object, label: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if not isinstance(value, str):
        raise SongProjectError(f"{label} must be a UUID.")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise SongProjectError(f"{label} must be a UUID.") from exc
    canonical = str(parsed)
    if value != canonical:
        raise SongProjectError(f"{label} must use canonical lowercase UUID text.")
    return canonical


def _text(
    value: object,
    label: str,
    *,
    maximum_bytes: int,
    required: bool = False,
    collapse_whitespace: bool = False,
) -> str:
    if not isinstance(value, str):
        raise SongProjectError(f"{label} must be text.")
    result = unicodedata.normalize("NFC", value)
    if any(ord(character) < 32 or ord(character) == 127 for character in result):
        raise SongProjectError(f"{label} must not contain control characters.")
    if collapse_whitespace:
        result = " ".join(result.split())
    if required and not result:
        raise SongProjectError(f"{label} is required.")
    if len(result.encode("utf-8")) > maximum_bytes:
        raise SongProjectError(
            f"{label} cannot exceed {maximum_bytes} UTF-8 bytes."
        )
    return result


def _enum(enum_type, value: object, label: str):
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise SongProjectError(f"{label} must be text.")
    try:
        return enum_type(value)
    except ValueError as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise SongProjectError(f"{label} must be one of: {choices}.") from exc


def _mapping_list(
    value: object,
    label: str,
    *,
    maximum: int,
) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        raise SongProjectError(f"{label} must be a list.")
    if len(value) > maximum:
        raise SongProjectError(f"{label} exceeds the limit of {maximum}.")
    result: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise SongProjectError(f"{label} may contain only objects.")
        result.append(item)
    return tuple(result)


def _unique(values: Iterable[str], label: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise SongProjectError(f"Project contains duplicate {label}.")


def normalize_media_relative_path(value: object) -> str:
    """Return one canonical ``Media/<filename>`` path or raise.

    Nested folders are intentionally not part of schema 1.  This keeps relink,
    collection, and portable bundle validation small and prevents traversal,
    platform-separator, and case-folding ambiguity.
    """

    if not isinstance(value, str):
        raise SongProjectError("media.path must be text.")
    if not value or "\\" in value or "\x00" in value:
        raise SongProjectError("media.path must be a safe relative Media path.")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or len(path.parts) != 2
        or path.parts[0] != "Media"
        or path.parts[1] in {"", ".", ".."}
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SongProjectError("media.path must be a file directly below Media/.")
    filename = _text(
        path.parts[1],
        "media.path filename",
        maximum_bytes=1_024,
        required=True,
    )
    return f"Media/{filename}"


@dataclass(frozen=True)
class TimeSignature:
    numerator: int = 4
    denominator: int = 4

    def __post_init__(self) -> None:
        numerator = _strict_int(
            self.numerator,
            "time_signature.numerator",
            minimum=1,
            maximum=32,
        )
        denominator = _strict_int(
            self.denominator,
            "time_signature.denominator",
            minimum=1,
            maximum=32,
        )
        if denominator not in {1, 2, 4, 8, 16, 32}:
            raise SongProjectError(
                "time_signature.denominator must be 1, 2, 4, 8, 16, or 32."
            )
        object.__setattr__(self, "numerator", numerator)
        object.__setattr__(self, "denominator", denominator)

    def to_dict(self) -> dict[str, int]:
        return {"numerator": self.numerator, "denominator": self.denominator}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TimeSignature":
        if not isinstance(value, Mapping):
            raise SongProjectError("time_signature must be an object.")
        _strict_keys(
            value,
            allowed={"numerator", "denominator"},
            required={"numerator", "denominator"},
            label="time_signature",
        )
        return cls(
            numerator=value["numerator"],
            denominator=value["denominator"],
        )


@dataclass(frozen=True)
class InputMapping:
    """Portable recording intent; runtime device handles never enter a project."""

    device_key: str
    channels: tuple[int, ...] = (1,)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "device_key",
            _text(
                self.device_key,
                "input_mapping.device_key",
                maximum_bytes=MAX_DEVICE_KEY_BYTES,
                required=True,
            ),
        )
        if not isinstance(self.channels, tuple):
            raise SongProjectError("input_mapping.channels must be a tuple.")
        if not 1 <= len(self.channels) <= MAX_TRACK_INPUT_CHANNELS:
            raise SongProjectError(
                "input_mapping.channels must contain 1 to "
                f"{MAX_TRACK_INPUT_CHANNELS} channels."
            )
        normalized = tuple(
            _strict_int(
                channel,
                "input_mapping channel",
                minimum=1,
                maximum=MAX_TRACK_INPUT_CHANNELS,
            )
            for channel in self.channels
        )
        if len(normalized) != len(set(normalized)):
            raise SongProjectError("input_mapping.channels contains duplicates.")
        object.__setattr__(self, "channels", normalized)

    def to_dict(self) -> dict[str, object]:
        return {"device_key": self.device_key, "channels": list(self.channels)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InputMapping":
        if not isinstance(value, Mapping):
            raise SongProjectError("input_mapping must be an object.")
        _strict_keys(
            value,
            allowed={"device_key", "channels"},
            required={"device_key", "channels"},
            label="input_mapping",
        )
        channels = value["channels"]
        if not isinstance(channels, list):
            raise SongProjectError("input_mapping.channels must be a list.")
        return cls(device_key=value["device_key"], channels=tuple(channels))


@dataclass(frozen=True)
class SongTrack:
    track_id: str
    name: str
    order: int
    input_mapping: InputMapping | None = None
    armed: bool = False
    input_monitoring: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "track_id", _uuid(self.track_id, "track.track_id"))
        object.__setattr__(
            self,
            "name",
            _text(
                self.name,
                "track.name",
                maximum_bytes=MAX_TRACK_NAME_BYTES,
                required=True,
                collapse_whitespace=True,
            ),
        )
        object.__setattr__(
            self,
            "order",
            _strict_int(
                self.order,
                "track.order",
                minimum=0,
                maximum=MAX_PROJECT_TRACKS - 1,
            ),
        )
        if self.input_mapping is not None and not isinstance(
            self.input_mapping, InputMapping
        ):
            raise SongProjectError(
                "track.input_mapping must be an InputMapping or null."
            )
        object.__setattr__(self, "armed", _strict_bool(self.armed, "track.armed"))
        object.__setattr__(
            self,
            "input_monitoring",
            _strict_bool(self.input_monitoring, "track.input_monitoring"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "track_id": self.track_id,
            "name": self.name,
            "order": self.order,
            "input_mapping": (
                self.input_mapping.to_dict()
                if self.input_mapping is not None
                else None
            ),
            "armed": self.armed,
            "input_monitoring": self.input_monitoring,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SongTrack":
        _strict_keys(
            value,
            allowed={
                "track_id",
                "name",
                "order",
                "input_mapping",
                "armed",
                "input_monitoring",
            },
            required={
                "track_id",
                "name",
                "order",
                "input_mapping",
                "armed",
                "input_monitoring",
            },
            label="track",
        )
        raw_mapping = value["input_mapping"]
        if raw_mapping is not None and not isinstance(raw_mapping, Mapping):
            raise SongProjectError("track.input_mapping must be an object or null.")
        return cls(
            track_id=value["track_id"],
            name=value["name"],
            order=value["order"],
            input_mapping=(
                InputMapping.from_dict(raw_mapping)
                if raw_mapping is not None
                else None
            ),
            armed=value["armed"],
            input_monitoring=value["input_monitoring"],
        )


@dataclass(frozen=True)
class SongMedia:
    media_id: str
    path: str
    sha256: str
    size_bytes: int
    sample_rate: int
    channels: int
    frame_count: int
    format: str
    original_basename: str
    provenance: MediaProvenance
    import_method: MediaImportMethod
    provenance_detail: str = ""
    original_read_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "media_id", _uuid(self.media_id, "media.media_id"))
        object.__setattr__(self, "path", normalize_media_relative_path(self.path))
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(self.sha256):
            raise SongProjectError("media.sha256 must be a lowercase SHA-256.")
        object.__setattr__(
            self,
            "size_bytes",
            _strict_int(
                self.size_bytes,
                "media.size_bytes",
                minimum=1,
                maximum=MAX_MEDIA_FILE_BYTES,
            ),
        )
        object.__setattr__(
            self,
            "sample_rate",
            _strict_int(
                self.sample_rate,
                "media.sample_rate",
                minimum=MIN_PROJECT_SAMPLE_RATE,
                maximum=MAX_PROJECT_SAMPLE_RATE,
            ),
        )
        object.__setattr__(
            self,
            "channels",
            _strict_int(
                self.channels,
                "media.channels",
                minimum=1,
                maximum=MAX_TRACK_INPUT_CHANNELS,
            ),
        )
        object.__setattr__(
            self,
            "frame_count",
            _strict_int(
                self.frame_count,
                "media.frame_count",
                minimum=1,
                maximum=MAX_MEDIA_FRAMES,
            ),
        )
        if not isinstance(self.format, str):
            raise SongProjectError("media.format must be text.")
        media_format = self.format.upper()
        if not _FORMAT_RE.fullmatch(media_format):
            raise SongProjectError("media.format is not a supported format label.")
        object.__setattr__(self, "format", media_format)
        basename = _text(
            self.original_basename,
            "media.original_basename",
            maximum_bytes=MAX_ORIGINAL_BASENAME_BYTES,
            required=True,
        )
        if basename in {".", ".."} or "/" in basename or "\\" in basename:
            raise SongProjectError(
                "media.original_basename must not contain a path."
            )
        object.__setattr__(self, "original_basename", basename)
        object.__setattr__(
            self,
            "provenance",
            _enum(MediaProvenance, self.provenance, "media.provenance"),
        )
        object.__setattr__(
            self,
            "import_method",
            _enum(MediaImportMethod, self.import_method, "media.import_method"),
        )
        object.__setattr__(
            self,
            "provenance_detail",
            _text(
                self.provenance_detail,
                "media.provenance_detail",
                maximum_bytes=MAX_PROVENANCE_DETAIL_BYTES,
            ),
        )
        if "/" in self.provenance_detail or "\\" in self.provenance_detail:
            raise SongProjectError(
                "media.provenance_detail must be a path-free label or durable ID."
            )
        original_read_only = _strict_bool(
            self.original_read_only, "media.original_read_only"
        )
        if not original_read_only:
            raise SongProjectError(
                "media.original_read_only must remain true; WebJam never edits "
                "an imported original."
            )
        object.__setattr__(self, "original_read_only", True)

    def to_dict(self) -> dict[str, object]:
        return {
            "media_id": self.media_id,
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "frame_count": self.frame_count,
            "format": self.format,
            "original_basename": self.original_basename,
            "provenance": self.provenance.value,
            "import_method": self.import_method.value,
            "provenance_detail": self.provenance_detail,
            "original_read_only": self.original_read_only,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SongMedia":
        _strict_keys(
            value,
            allowed={
                "media_id",
                "path",
                "sha256",
                "size_bytes",
                "sample_rate",
                "channels",
                "frame_count",
                "format",
                "original_basename",
                "provenance",
                "import_method",
                "provenance_detail",
                "original_read_only",
            },
            required={
                "media_id",
                "path",
                "sha256",
                "size_bytes",
                "sample_rate",
                "channels",
                "frame_count",
                "format",
                "original_basename",
                "provenance",
                "import_method",
                "provenance_detail",
                "original_read_only",
            },
            label="media",
        )
        return cls(
            media_id=value["media_id"],
            path=value["path"],
            sha256=value["sha256"],
            size_bytes=value["size_bytes"],
            sample_rate=value["sample_rate"],
            channels=value["channels"],
            frame_count=value["frame_count"],
            format=value["format"],
            original_basename=value["original_basename"],
            provenance=value["provenance"],
            import_method=value["import_method"],
            provenance_detail=value["provenance_detail"],
            original_read_only=value["original_read_only"],
        )


@dataclass(frozen=True)
class SongProject:
    project_id: str
    name: str
    project_sample_rate: int = DEFAULT_PROJECT_SAMPLE_RATE
    tempo_bpm: float = 120.0
    time_signature: TimeSignature = TimeSignature()
    tracks: tuple[SongTrack, ...] = ()
    media: tuple[SongMedia, ...] = ()
    backing_media_id: str | None = None
    revision: int = 0
    schema_version: int = SONG_PROJECT_SCHEMA_VERSION
    creator_profile_key: str = DEFAULT_CREATOR_PROFILE_KEY

    def __post_init__(self) -> None:
        if (
            type(self.schema_version) is not int
            or self.schema_version != SONG_PROJECT_SCHEMA_VERSION
        ):
            raise SongProjectError(
                f"Unsupported song project schema: {self.schema_version!r}."
            )
        creator_profile_key = canonical_creator_profile_key(self.creator_profile_key)
        if creator_profile_key is None:
            raise SongProjectError(
                "creator_profile_key must identify a supported creator profile."
            )
        object.__setattr__(
            self,
            "creator_profile_key",
            creator_profile_key,
        )
        object.__setattr__(self, "project_id", _uuid(self.project_id, "project_id"))
        object.__setattr__(
            self,
            "name",
            _text(
                self.name,
                "project name",
                maximum_bytes=MAX_PROJECT_NAME_BYTES,
                required=True,
                collapse_whitespace=True,
            ),
        )
        object.__setattr__(
            self,
            "project_sample_rate",
            _strict_int(
                self.project_sample_rate,
                "project_sample_rate",
                minimum=MIN_PROJECT_SAMPLE_RATE,
                maximum=MAX_PROJECT_SAMPLE_RATE,
            ),
        )
        object.__setattr__(
            self,
            "tempo_bpm",
            _strict_float(
                self.tempo_bpm,
                "tempo_bpm",
                minimum=MIN_TEMPO_BPM,
                maximum=MAX_TEMPO_BPM,
            ),
        )
        if not isinstance(self.time_signature, TimeSignature):
            raise SongProjectError("time_signature must be a TimeSignature.")
        if not isinstance(self.tracks, tuple):
            raise SongProjectError("tracks must be a tuple.")
        if not isinstance(self.media, tuple):
            raise SongProjectError("media must be a tuple.")
        if len(self.tracks) > MAX_PROJECT_TRACKS:
            raise SongProjectError(
                f"tracks exceeds the limit of {MAX_PROJECT_TRACKS}."
            )
        if len(self.media) > MAX_PROJECT_MEDIA:
            raise SongProjectError(f"media exceeds the limit of {MAX_PROJECT_MEDIA}.")
        if any(not isinstance(track, SongTrack) for track in self.tracks):
            raise SongProjectError("tracks may contain only SongTrack values.")
        if any(not isinstance(media, SongMedia) for media in self.media):
            raise SongProjectError("media may contain only SongMedia values.")
        _unique((track.track_id for track in self.tracks), "track IDs")
        _unique((media.media_id for media in self.media), "media IDs")
        _unique((media.path for media in self.media), "media paths")
        orders = tuple(track.order for track in self.tracks)
        if orders != tuple(range(len(self.tracks))):
            raise SongProjectError(
                "Tracks must be stored in contiguous order starting at zero."
            )
        total_bytes = sum(media.size_bytes for media in self.media)
        if total_bytes > MAX_PROJECT_MEDIA_BYTES:
            raise SongProjectError(
                "Project media exceeds the aggregate bundle size limit."
            )
        backing = _uuid(
            self.backing_media_id,
            "backing_media_id",
            optional=True,
        )
        if backing is not None and backing not in {
            media.media_id for media in self.media
        }:
            raise SongProjectError("backing_media_id does not identify project media.")
        object.__setattr__(self, "backing_media_id", backing)
        object.__setattr__(
            self,
            "revision",
            _strict_int(
                self.revision,
                "revision",
                minimum=0,
                maximum=MAX_PROJECT_REVISION,
            ),
        )

    @classmethod
    def new(
        cls,
        name: str,
        *,
        project_sample_rate: int = DEFAULT_PROJECT_SAMPLE_RATE,
        tempo_bpm: float = 120.0,
        time_signature: TimeSignature | None = None,
        project_id: str | None = None,
        creator_profile_key: str = DEFAULT_CREATOR_PROFILE_KEY,
    ) -> "SongProject":
        return cls(
            project_id=project_id or str(uuid.uuid4()),
            name=name,
            project_sample_rate=project_sample_rate,
            tempo_bpm=tempo_bpm,
            time_signature=time_signature or TimeSignature(),
            creator_profile_key=creator_profile_key,
        )

    def _bumped(self, **changes: object) -> "SongProject":
        if self.revision >= MAX_PROJECT_REVISION:
            raise SongProjectError("Project revision cannot be incremented.")
        return replace(self, revision=self.revision + 1, **changes)

    def add_track(
        self,
        name: str,
        *,
        input_mapping: InputMapping | None = None,
        track_id: str | None = None,
    ) -> "SongProject":
        if len(self.tracks) >= MAX_PROJECT_TRACKS:
            raise SongProjectError("Project track limit reached.")
        track = SongTrack(
            track_id=track_id or str(uuid.uuid4()),
            name=name,
            order=len(self.tracks),
            input_mapping=input_mapping,
        )
        return self._bumped(tracks=(*self.tracks, track))

    def add_media(
        self,
        media: SongMedia,
        *,
        designate_backing: bool = False,
    ) -> "SongProject":
        if not isinstance(media, SongMedia):
            raise SongProjectError("media must be a SongMedia value.")
        if len(self.media) >= MAX_PROJECT_MEDIA:
            raise SongProjectError("Project media limit reached.")
        backing = media.media_id if designate_backing else self.backing_media_id
        return self._bumped(
            media=(*self.media, media),
            backing_media_id=backing,
        )

    def remove_track(self, track_id: str) -> "SongProject":
        """Remove one manifest track and compact the remaining display order."""

        canonical = _uuid(track_id, "track_id")
        if canonical not in {item.track_id for item in self.tracks}:
            raise SongProjectError("Project track ID was not found.")
        tracks = tuple(
            replace(item, order=index)
            for index, item in enumerate(
                item for item in self.tracks if item.track_id != canonical
            )
        )
        return self._bumped(tracks=tracks)

    def designate_backing_media(self, media_id: str | None) -> "SongProject":
        normalized = _uuid(media_id, "backing_media_id", optional=True)
        if normalized is not None and normalized not in {
            media.media_id for media in self.media
        }:
            raise SongProjectError("backing_media_id does not identify project media.")
        return self._bumped(backing_media_id=normalized)

    def rename(self, name: str) -> "SongProject":
        """Return one validated project-name edit."""

        return self._bumped(name=name)

    def set_tempo(self, tempo_bpm: float) -> "SongProject":
        """Return one constant project-tempo edit."""

        return self._bumped(tempo_bpm=tempo_bpm)

    def set_time_signature(
        self,
        numerator: int,
        denominator: int,
    ) -> "SongProject":
        """Return one constant project time-signature edit."""

        return self._bumped(
            time_signature=TimeSignature(
                numerator=numerator,
                denominator=denominator,
            )
        )

    def media_by_id(self, media_id: str) -> SongMedia:
        normalized = _uuid(media_id, "media_id")
        for item in self.media:
            if item.media_id == normalized:
                return item
        raise SongProjectError("Project media ID was not found.")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "creator_profile_key": self.creator_profile_key,
            "project_id": self.project_id,
            "revision": self.revision,
            "name": self.name,
            "project_sample_rate": self.project_sample_rate,
            "tempo_bpm": self.tempo_bpm,
            "time_signature": self.time_signature.to_dict(),
            "backing_media_id": self.backing_media_id,
            "tracks": [track.to_dict() for track in self.tracks],
            "media": [media.to_dict() for media in self.media],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SongProject":
        if not isinstance(value, Mapping):
            raise SongProjectError("Project manifest root must be an object.")
        schema_version = value.get("schema_version")
        if type(schema_version) is not int:
            raise SongProjectError(
                f"Unsupported song project schema: {schema_version!r}."
            )
        if schema_version == LEGACY_SONG_PROJECT_SCHEMA_VERSION:
            # Schema 1 predates creator profiles and therefore has exactly one
            # truthful interpretation. Keep its old shape strict so a hidden
            # profile field cannot bypass the schema-2 contract.
            allowed = {
                "schema_version",
                "project_id",
                "revision",
                "name",
                "project_sample_rate",
                "tempo_bpm",
                "time_signature",
                "backing_media_id",
                "tracks",
                "media",
            }
            creator_profile_key = DEFAULT_CREATOR_PROFILE_KEY
        elif schema_version == SONG_PROJECT_SCHEMA_VERSION:
            allowed = {
                "schema_version",
                "creator_profile_key",
                "project_id",
                "revision",
                "name",
                "project_sample_rate",
                "tempo_bpm",
                "time_signature",
                "backing_media_id",
                "tracks",
                "media",
            }
            creator_profile_key = value.get("creator_profile_key")
        else:
            raise SongProjectError(
                f"Unsupported song project schema: {schema_version!r}."
            )
        _strict_keys(
            value,
            allowed=allowed,
            required=allowed,
            label="Project manifest",
        )
        signature = value["time_signature"]
        if not isinstance(signature, Mapping):
            raise SongProjectError("time_signature must be an object.")
        return cls(
            schema_version=SONG_PROJECT_SCHEMA_VERSION,
            creator_profile_key=creator_profile_key,
            project_id=value["project_id"],
            revision=value["revision"],
            name=value["name"],
            project_sample_rate=value["project_sample_rate"],
            tempo_bpm=value["tempo_bpm"],
            time_signature=TimeSignature.from_dict(signature),
            backing_media_id=value["backing_media_id"],
            tracks=tuple(
                SongTrack.from_dict(item)
                for item in _mapping_list(
                    value["tracks"],
                    "tracks",
                    maximum=MAX_PROJECT_TRACKS,
                )
            ),
            media=tuple(
                SongMedia.from_dict(item)
                for item in _mapping_list(
                    value["media"],
                    "media",
                    maximum=MAX_PROJECT_MEDIA,
                )
            ),
        )


def song_project_from_dict(value: Mapping[str, Any]) -> SongProject:
    """Compatibility-friendly functional parser for a supported manifest."""

    return SongProject.from_dict(value)


__all__ = [
    "DEFAULT_CREATOR_PROFILE_KEY",
    "DEFAULT_PROJECT_SAMPLE_RATE",
    "InputMapping",
    "LEGACY_SONG_PROJECT_SCHEMA_VERSION",
    "MAX_MEDIA_FILE_BYTES",
    "MAX_PROJECT_MEDIA",
    "MAX_PROJECT_MEDIA_BYTES",
    "MAX_PROJECT_TRACKS",
    "MAX_TEMPO_BPM",
    "MIN_TEMPO_BPM",
    "MediaImportMethod",
    "MediaProvenance",
    "SONG_PROJECT_SCHEMA_VERSION",
    "SongMedia",
    "SongProject",
    "SongProjectError",
    "SongTrack",
    "TimeSignature",
    "normalize_media_relative_path",
    "song_project_from_dict",
]
