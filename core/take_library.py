"""
Take library — discover recorded sessions and read their track layout.

A "take" is one Jamulus multitrack recording: a folder of per-musician WAV
files plus a Reaper ``.rpp`` and/or an Audacity ``.lof`` that carry each
track's *start offset* (musicians connect at different moments, so tracks
don't all begin at t=0). This module turns a directory of such folders into
structured ``TakeInfo`` objects for the Take Deck to play back.

Deliberately dependency-light: only ``soundfile`` (for durations/samplerate),
and even that is imported lazily so the module stays importable in
environments without libsndfile.
"""

from __future__ import annotations

import logging
import json
import hashlib
import ipaddress
import math
import os
import re
import stat
import time
import uuid
import wave
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, List, Mapping, Optional

from core.creative_modes import canonical_creator_profile_key

if TYPE_CHECKING:
    from core.take_project import SessionEvidence

_logger = logging.getLogger("webjam.take_library")

_AUDIO_EXTS = {".wav", ".flac", ".ogg", ".aiff", ".aif"}
# Pinned Jamulus 3.12.2 ``MAX_NUM_CHANNELS`` (global.h).
_RECORDER_MAX_CLIENTS = 150
_RECORDER_MAX_TEXT = 512
_RECORDER_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_RECORDING_STAGING_NAME = ".webjam-recording-staging.json"
_RECORDING_STAGING_MAX_BYTES = 1024 * 1024
_RECORDING_SIDECAR_MAX_BYTES = 8 * 1024 * 1024
_RECORDING_SIDECAR_TOTAL_MAX_BYTES = 16 * 1024 * 1024
_RECORDING_LOF_MAX_FILES = 1
_RECORDING_RPP_MAX_FILES = 4
_OPAQUE_SERVER_MEDIA_RE = re.compile(r"^server-media-[0-9]{3}\.wav$")
_INTERRUPTED_PUBLICATION_ERROR = (
    "Recording publication was interrupted. Source audio was preserved for "
    "review."
)
_REQUIRED_REFERENCE_TRACK_ERROR = (
    "The Shared Track was part of this Record Session, but its exact "
    "band-server stem could not be verified. The take was preserved for review."
)
_UNSUPPORTED_CREATOR_PROFILE_ERROR = (
    "The take's creator profile is unsupported. Studio is using generic "
    "review labels."
)

EVIDENCE_ONLY_EXPORT_BLOCK_REASON = (
    "No audio media was preserved. This recovery project is review-only and "
    "cannot be exported."
)

_SAFE_CAPTURE_ERRORS = frozenset({
    "Interrupted recording evidence recovered.",
    "No new Jamulus take folder appeared after recording stopped.",
    "Take files did not become stable in time.",
    "Local capture was already finalized.",
    EVIDENCE_ONLY_EXPORT_BLOCK_REASON,
})


def _safe_capture_error(value: object) -> str:
    """Reduce path/exception-bearing capture text to a useful safe category."""

    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if text in _SAFE_CAPTURE_ERRORS:
        return text
    lowered = text.casefold()
    if "audio device" in lowered:
        return "The local recording device reported an error."
    if "writer" in lowered or "wav" in lowered:
        return "The local recording writer needs attention."
    if "attach" in lowered or "already existed" in lowered:
        return "A local recording stem could not be attached normally."
    if "protect" in lowered or "permission" in lowered:
        return "Local recording file permissions need attention."
    if "recover" in lowered or "partial" in lowered:
        return "Local recording recovery needs attention."
    if "gap" in lowered or "overflow" in lowered or "underflow" in lowered:
        return "The local recording contains a captured audio gap."
    return "Local recording capture needs attention."


def _safe_identity_error(value: object) -> str:
    """Collapse caller text so endpoints, secrets, and paths never persist."""

    if not " ".join(str(value or "").split()):
        return ""
    return "Authenticated recording identity evidence needs attention."


def _is_visible_take_directory(path: Path) -> bool:
    """Return whether an immediate child may represent a musician-facing take.

    The Takes root also holds private WebJam working folders: local-capture
    recovery, export staging, and the in-progress recording-evidence journal.
    None is a Jamulus take, and choosing one by mtime can hide the real server
    folder immediately after Record stops.  Keep this boundary name-based and
    dependency-free so discovery and the recorder snapshot agree.
    """
    return not path.name.startswith(".") and path.is_dir()


@dataclass
class TrackSegmentInfo:
    """One explicit media/configuration interval on the project timeline."""

    path: Path
    project_start_frame: int
    frame_count: int
    samplerate: int
    channels: int = 1
    media_status: str = "available"
    segment_id: str = ""
    sha256: str = ""
    gaps: tuple[tuple[int, int, tuple[int, ...], str], ...] = ()

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.samplerate if self.samplerate > 0 else 0.0


@dataclass
class TrackInfo:
    """One audio track within a take."""
    path: Path
    name: str
    offset_s: float = 0.0          # start offset within the take timeline
    duration_s: float = 0.0        # audio length (0 if unknown)
    samplerate: int = 0
    source: str = "jamulus_server"
    # Runtime source truth.  Manifest-declared tracks stay in the project even
    # when their file has moved or disappeared, so Studio can show the problem
    # instead of silently presenting a smaller take as verified.
    media_status: str = "available"
    track_id: str = ""
    source_id: str = ""
    participant_id: str = ""
    instrument: str = ""
    quality: str = "unverified"
    segments: tuple[TrackSegmentInfo, ...] = ()
    drift_ppm: float = 0.0
    alignment_confidence: float = 0.0
    alignment_method: str = "unverified"

    @property
    def end_s(self) -> float:
        return self.offset_s + self.duration_s

    @property
    def channel_count(self) -> int:
        """Return one truthful logical-track width, or zero when it changes.

        A schema-v2 reconnect may retain multiple immutable media segments.
        Studio and export can only treat those segments as one logical source
        when every segment has the same supported mono/stereo topology.  Zero
        deliberately exposes an ambiguous/unsupported layout instead of
        silently borrowing the primary segment's channel count.
        """

        channels = {int(segment.channels) for segment in self.segments}
        if len(channels) == 1 and channels.issubset({1, 2}):
            return channels.pop()
        return 0

    @property
    def has_supported_channel_topology(self) -> bool:
        """Whether every retained segment is consistently mono or stereo."""

        return self.channel_count in {1, 2}


@dataclass
class TakeInfo:
    """One recorded session: a folder of tracks + timing metadata."""
    path: Path
    name: str
    tracks: List[TrackInfo] = field(default_factory=list)
    reaper_project: Optional[Path] = None
    validation_status: str = "unchecked"
    manifest_path: Optional[Path] = None
    # Findings recorded at validation time, so reviewing a finished take
    # never needs to re-probe its audio files.
    manifest_errors: tuple[str, ...] = ()
    manifest_warnings: tuple[str, ...] = ()
    session_title: str = ""
    session_id: str = ""
    take_id: str = ""
    project_samplerate: int = 0
    # Preserve the parsed manifest generation so UI boundaries can distinguish
    # a genuine schema-v2 project from a legacy/synthetic take that happens to
    # carry stable-looking track IDs.
    manifest_schema_version: int = 0
    # A recovery journal can prove that a recording was interrupted even when
    # no source media survived. Keep that truth visible without inventing a
    # placeholder WAV or offering audio actions that cannot be truthful.
    review_only: bool = False
    export_block_reason: str = ""
    # Appended for positional compatibility. Missing historical evidence is
    # Music; an empty value means an explicit manifest value was unsupported
    # and callers must use generic, fail-closed presentation.
    creator_profile_key: str = "music"

    @property
    def track_count(self) -> int:
        return len(self.tracks)

    @property
    def duration_s(self) -> float:
        """Wall-clock length of the take: the latest track end."""
        return max((t.end_s for t in self.tracks), default=0.0)

    @property
    def display_name(self) -> str:
        """Musician-facing name, falling back to the recorder folder name."""
        return self.session_title.strip() or self.name

    @property
    def is_exportable(self) -> bool:
        """Whether this take has media for an export attempt.

        The exporter still revalidates source integrity, alignment, and the
        selected tracks. This property exposes the earlier evidence-only gate.
        """
        return (
            bool(self.tracks)
            and not self.review_only
            and not self.export_block_reason
        )


@dataclass(frozen=True)
class TakeValidationResult:
    """Post-recording confidence report for one take directory."""

    take: Optional[TakeInfo]
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    manifest_path: Optional[Path] = None

    @property
    def ok(self) -> bool:
        return self.take is not None and not self.errors

    @property
    def summary(self) -> str:
        if self.take is None:
            return "No completed take was found."
        if self.take.review_only:
            return "Review only · no audio preserved"
        rate_values = {t.samplerate for t in self.take.tracks if t.samplerate > 0}
        rate = (
            f"{next(iter(rate_values)) / 1000:g} kHz"
            if len(rate_values) == 1 else "mixed rate"
        )
        duration = int(round(self.take.duration_s))
        return (
            f"{self.take.track_count} track{'s' if self.take.track_count != 1 else ''}"
            f" · {duration // 60}:{duration % 60:02d} · {rate}"
        )


def _enforce_required_reference_track(
    result: TakeValidationResult,
    *,
    required: bool,
) -> TakeValidationResult:
    """Fail this validation without rewriting immutable published evidence."""

    if (
        not required
        or result.take is None
        or any(track.source == "live_reference" for track in result.take.tracks)
    ):
        return result
    return TakeValidationResult(
        result.take,
        tuple(dict.fromkeys((*result.errors, _REQUIRED_REFERENCE_TRACK_ERROR))),
        result.warnings,
        result.manifest_path,
    )


@dataclass(frozen=True, slots=True)
class RecordingStagingIdentity:
    """Opaque journal linkage retained during crash-safe media publication."""

    session_id: str
    take_id: str

    def __post_init__(self) -> None:
        try:
            session_id = str(uuid.UUID(str(self.session_id)))
            take_id = str(uuid.UUID(str(self.take_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("recording staging IDs must be UUIDs") from exc
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "take_id", take_id)


@dataclass(frozen=True, slots=True)
class JamulusRecordingFilename:
    """Privacy-safe facts parsed from a pinned Jamulus 3.12.2 WAV name.

    Jamulus writes ``<recorder-key>-<startFrame>-<channels>.wav``.  The
    penultimate number is timeline metadata, never a participant/channel ID.
    Only a digest of the address-bearing recorder key leaves the parser.
    """

    recorder_key_sha256: str
    start_frame: int
    channels: int
    collision_index: int = 0

    def __post_init__(self) -> None:
        digest = str(self.recorder_key_sha256 or "").strip().lower()
        if _RECORDER_KEY_RE.fullmatch(digest) is None:
            raise ValueError("recorder_key_sha256 must be a SHA-256 digest")
        if (
            isinstance(self.start_frame, bool)
            or not 0 <= int(self.start_frame) <= (2**63 - 1)
        ):
            raise ValueError("start_frame is out of range")
        if isinstance(self.channels, bool) or int(self.channels) not in {1, 2}:
            raise ValueError("channels must be one or two")
        if (
            isinstance(self.collision_index, bool)
            or not 0 <= int(self.collision_index) <= 1_000_000
        ):
            raise ValueError("collision_index is out of range")
        object.__setattr__(self, "recorder_key_sha256", digest)
        object.__setattr__(self, "start_frame", int(self.start_frame))
        object.__setattr__(self, "channels", int(self.channels))
        object.__setattr__(self, "collision_index", int(self.collision_index))


@dataclass(frozen=True, slots=True)
class RecorderClientObservation:
    """Address-erased facts from one authenticated server roster row."""

    server_channel_id: int
    display_name: str
    channels: int
    recorder_key_sha256: str
    matches_owned_reference: bool = False

    def __post_init__(self) -> None:
        if (
            isinstance(self.server_channel_id, bool)
            or not 0 <= int(self.server_channel_id) < _RECORDER_MAX_CLIENTS
        ):
            raise ValueError("server_channel_id is out of range")
        name = " ".join(str(self.display_name or "").split())[:120] or "Musician"
        if isinstance(self.channels, bool) or int(self.channels) not in {1, 2}:
            raise ValueError("channels must be one or two")
        digest = str(self.recorder_key_sha256 or "").strip().lower()
        if _RECORDER_KEY_RE.fullmatch(digest) is None:
            raise ValueError("recorder_key_sha256 must be a SHA-256 digest")
        object.__setattr__(self, "server_channel_id", int(self.server_channel_id))
        object.__setattr__(self, "display_name", name)
        object.__setattr__(self, "channels", int(self.channels))
        object.__setattr__(self, "recorder_key_sha256", digest)
        object.__setattr__(
            self, "matches_owned_reference", bool(self.matches_owned_reference)
        )


@dataclass(frozen=True, slots=True)
class RecorderClientReceipt:
    """Take-local, address-free binding from recorder key to durable source."""

    server_channel_id: int
    display_name: str
    participant_id: str
    recorder_key_sha256: str
    channels: int
    source_kind: str = "musician"
    source_fingerprint_sha256: str = ""
    playback_generation: int = 0

    def __post_init__(self) -> None:
        observation = RecorderClientObservation(
            self.server_channel_id,
            self.display_name,
            self.channels,
            self.recorder_key_sha256,
        )
        try:
            participant_id = str(uuid.UUID(str(self.participant_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("participant_id must be a UUID") from exc
        source_kind = str(self.source_kind or "").strip()
        if source_kind not in {"musician", "reference_track"}:
            raise ValueError("source_kind is unsupported")
        source_fingerprint = str(
            self.source_fingerprint_sha256 or ""
        ).strip().lower()
        if source_fingerprint and _RECORDER_KEY_RE.fullmatch(
            source_fingerprint
        ) is None:
            raise ValueError("source_fingerprint_sha256 must be a SHA-256 digest")
        if source_fingerprint and source_kind != "reference_track":
            raise ValueError(
                "source_fingerprint_sha256 is only valid for a reference track"
            )
        playback_generation = self.playback_generation
        if (
            isinstance(playback_generation, bool)
            or not isinstance(playback_generation, int)
            or not 0 <= playback_generation <= (1 << 63) - 1
        ):
            raise ValueError("playback_generation is outside the supported range")
        if playback_generation and source_kind != "reference_track":
            raise ValueError(
                "playback_generation is only valid for a reference track"
            )
        object.__setattr__(self, "server_channel_id", observation.server_channel_id)
        object.__setattr__(self, "display_name", observation.display_name)
        object.__setattr__(self, "channels", observation.channels)
        object.__setattr__(
            self, "recorder_key_sha256", observation.recorder_key_sha256
        )
        object.__setattr__(self, "participant_id", participant_id)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(
            self,
            "source_fingerprint_sha256",
            source_fingerprint,
        )
        object.__setattr__(self, "playback_generation", playback_generation)


class RecorderRosterError(ValueError):
    """Authenticated roster shape or recorder-key ambiguity was unsafe."""

    def __init__(self, message: str, *, conflicted_keys: tuple[str, ...] = ()):
        super().__init__(message)
        self.conflicted_keys = tuple(
            digest
            for digest in conflicted_keys
            if _RECORDER_KEY_RE.fullmatch(str(digest)) is not None
        )


def _jamulus_translate_recorder_text(value: str) -> str:
    """Reproduce Jamulus 3.12.2 ``CJamClient::TranslateChars`` exactly."""

    charmap = ["_"] * 256
    for character in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz":
        charmap[ord(character)] = character
    charmap_updates = {
        0x8A: "S", 0x8C: "O", 0x8E: "Z", 0x9A: "s", 0x9C: "o",
        0x9E: "z", 0x9F: "Y", 0xB2: "2", 0xB3: "3", 0xB5: "u",
        0xB9: "1", 0xC0: "A", 0xC1: "A", 0xC2: "A", 0xC3: "A",
        0xC4: "A", 0xC5: "A", 0xC6: "A", 0xC7: "C", 0xC8: "E",
        0xC9: "E", 0xCA: "E", 0xCB: "E", 0xCC: "I", 0xCD: "I",
        0xCE: "I", 0xCF: "I", 0xD0: "D", 0xD1: "N", 0xD2: "O",
        0xD3: "O", 0xD4: "O", 0xD5: "O", 0xD6: "O", 0xD7: "x",
        0xD8: "O", 0xD9: "U", 0xDA: "U", 0xDB: "U", 0xDC: "U",
        0xDD: "Y", 0xDE: "P", 0xDF: "S", 0xE0: "a", 0xE1: "a",
        0xE2: "a", 0xE3: "a", 0xE4: "a", 0xE5: "a", 0xE6: "a",
        0xE7: "c", 0xE8: "e", 0xE9: "e", 0xEA: "e", 0xEB: "e",
        0xEC: "i", 0xED: "i", 0xEE: "i", 0xEF: "i", 0xF0: "d",
        0xF1: "n", 0xF2: "o", 0xF3: "o", 0xF4: "o", 0xF5: "o",
        0xF6: "o", 0xF8: "o", 0xF9: "u", 0xFA: "u", 0xFB: "u",
        0xFC: "u", 0xFD: "y", 0xFE: "p", 0xFF: "y",
    }
    for index, character in charmap_updates.items():
        charmap[index] = character
    # QString is a sequence of UTF-16 QChars. ``toLatin1`` replaces each QChar
    # above U+00FF separately, so one astral code point becomes two ``?``
    # bytes (its surrogate pair), not the single replacement produced by
    # Python's ordinary ``latin-1`` error handler. The pinned table then turns
    # each replacement into an underscore.
    utf16 = str(value).encode("utf-16-le", errors="surrogatepass")
    latin1 = bytes(
        code_unit if code_unit <= 0xFF else ord("?")
        for index in range(0, len(utf16), 2)
        for code_unit in (int.from_bytes(utf16[index : index + 2], "little"),)
    )
    return "".join(charmap[byte] for byte in latin1)


def _jamulus_recorder_address(value: str) -> tuple[str, int, bool]:
    """Return pinned masked address text, source port, and loopback truth."""

    text = str(value or "").strip()
    if (
        not text
        or len(text) > _RECORDER_MAX_TEXT
        or any(character in text for character in ("\0", "\r", "\n"))
    ):
        raise RecorderRosterError("server roster is invalid")
    if text.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)\]:(\d+)", text)
        if match is None:
            raise RecorderRosterError("server roster is invalid")
        host_text, port_text = match.groups()
    else:
        if ":" not in text:
            raise RecorderRosterError("server roster is invalid")
        host_text, port_text = text.rsplit(":", 1)
    try:
        ipaddress.ip_address(host_text)
        port = int(port_text)
    except (ValueError, TypeError) as exc:
        raise RecorderRosterError("server roster is invalid") from exc
    if not 1 <= port <= 65_535:
        raise RecorderRosterError("server roster is invalid")
    # Preserve the authenticated server's QHostAddress text. Python's
    # canonical form converts IPv4-mapped IPv6 (``::ffff:192.0.2.1``) to hex,
    # but pinned Jamulus decides how to mask and bracket from the dotted text
    # returned by Qt. Re-canonicalizing it would produce a different filename.
    host = host_text
    # Jamulus special-cases exactly QHostAddress::LocalHost/LocalHostIPv6,
    # not the wider address-library loopback ranges.
    loopback = host in {"127.0.0.1", "::1"}
    if not loopback:
        dotted = "." in host
        host = host.rsplit("." if dotted else ":", 1)[0]
        host += ".x" if dotted else ":x"
    # CHostAddress brackets only pure IPv6 text; IPv4-mapped strings retain
    # dotted notation and therefore follow the unbracketed Qt branch.
    masked = f"{host}:{port}" if "." in host else f"[{host}]:{port}"
    return masked, port, loopback


def _jamulus_recorder_key_digest(name: str, masked_address: str) -> str:
    translated_name = _jamulus_translate_recorder_text(name).ljust(4, "_")
    translated_address = _jamulus_translate_recorder_text(masked_address)
    recorder_key = f"{translated_name}-{translated_address}"
    return hashlib.sha256(recorder_key.encode("ascii")).hexdigest()


def recorder_client_observations(
    payload: Mapping[str, object],
    *,
    owned_reference_udp_port: int | None = None,
) -> tuple[RecorderClientObservation, ...]:
    """Erase addresses from one authenticated ``getClients`` response."""

    raw_clients = payload.get("clients")
    connections = payload.get("connections")
    if (
        not isinstance(raw_clients, list)
        or len(raw_clients) > _RECORDER_MAX_CLIENTS
        or isinstance(connections, bool)
        or not isinstance(connections, int)
        or connections != len(raw_clients)
    ):
        raise RecorderRosterError("server roster is invalid")
    if owned_reference_udp_port is not None and (
        isinstance(owned_reference_udp_port, bool)
        or not 1 <= int(owned_reference_udp_port) <= 65_535
    ):
        raise ValueError("owned_reference_udp_port is invalid")

    observations: list[RecorderClientObservation] = []
    seen_channels: set[int] = set()
    seen_keys: set[str] = set()
    for raw in raw_clients:
        if not isinstance(raw, Mapping):
            raise RecorderRosterError("server roster is invalid")
        channel_id = raw.get("id")
        name = raw.get("name")
        channels = raw.get("channels")
        address = raw.get("address")
        if (
            isinstance(channel_id, bool)
            or not isinstance(channel_id, int)
            or not 0 <= channel_id < _RECORDER_MAX_CLIENTS
            or not isinstance(name, str)
            or len(name) > _RECORDER_MAX_TEXT
            or any(character in name for character in ("\0", "\r", "\n"))
            or isinstance(channels, bool)
            or not isinstance(channels, int)
            or channels not in {1, 2}
            or not isinstance(address, str)
        ):
            raise RecorderRosterError("server roster is invalid")
        masked, source_port, loopback = _jamulus_recorder_address(address)
        digest = _jamulus_recorder_key_digest(name, masked)
        if channel_id in seen_channels:
            raise RecorderRosterError("server roster is ambiguous")
        if digest in seen_keys:
            raise RecorderRosterError(
                "server roster has a recorder-key collision",
                conflicted_keys=(digest,),
            )
        seen_channels.add(channel_id)
        seen_keys.add(digest)
        observations.append(
            RecorderClientObservation(
                channel_id,
                name if name.strip() else "Musician",
                channels,
                digest,
                matches_owned_reference=bool(
                    owned_reference_udp_port is not None
                    and loopback
                    and source_port == int(owned_reference_udp_port)
                ),
            )
        )
    return tuple(observations)


_JAMULUS_RECORDING_FILENAME_RE = re.compile(
    r"^(?P<key>[^/\\\0]{1,768})-"
    r"(?P<start>0|[1-9]\d*)-"
    r"(?P<channels>[12])"
    r"(?:_(?P<collision>[1-9]\d*))?\.wav$",
    re.IGNORECASE,
)


def parse_jamulus_recording_filename(
    filename: str,
) -> JamulusRecordingFilename | None:
    """Parse the exact recorder suffix without exposing its recorder key."""

    text = str(filename or "")
    if len(text) > 1_024 or Path(text).name != text:
        return None
    match = _JAMULUS_RECORDING_FILENAME_RE.fullmatch(text)
    if match is None:
        return None
    recorder_key = match.group("key")
    # TranslateChars removes every user/address hyphen. Exactly one structural
    # separator remains between translated name and translated address.
    if recorder_key.count("-") != 1:
        return None
    try:
        return JamulusRecordingFilename(
            recorder_key_sha256=hashlib.sha256(
                recorder_key.encode("ascii")
            ).hexdigest(),
            start_frame=int(match.group("start")),
            channels=int(match.group("channels")),
            collision_index=int(match.group("collision") or 0),
        )
    except (UnicodeEncodeError, ValueError):
        return None


_STRICT_RECORDING_LOF_LINE_RE = re.compile(
    r'^\s*file\s+"(?P<name>[^"\r\n]+)"\s+offset\s+'
    r"(?P<offset>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*$",
    re.IGNORECASE,
)
_RPP_FILE_LINE_RE = re.compile(
    r'^(?P<indent>\s*)FILE\s+"(?P<path>[^"\r\n]*)"(?P<suffix>[^\r\n]*)$',
    re.IGNORECASE,
)
_RPP_NAME_LINE_RE = re.compile(
    r"^(?P<indent>\s*)NAME(?:\s+.*)?$",
    re.IGNORECASE,
)


def _strict_recording_lof_offsets(
    data: bytes,
    *,
    expected_names: set[str],
) -> tuple[dict[str, float], bool]:
    """Parse pinned Jamulus LOF evidence without defaulting or last-wins.

    The public parser remains intentionally lenient for old user-created takes.
    Recording publication is stricter: every native file directive must have a
    finite, non-negative offset and appear exactly once. The boolean reports
    any malformed, duplicate, unexpected, or missing evidence without exposing
    a musician-selected filename in an error.
    """

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return {}, True
    offsets: dict[str, float] = {}
    conflicted: set[str] = set()
    invalid = False
    for line in text.splitlines():
        match = _STRICT_RECORDING_LOF_LINE_RE.fullmatch(line)
        if match is None:
            if line.strip():
                invalid = True
            continue
        raw_name = match.group("name")
        name = Path(raw_name).name
        if name != raw_name or name not in expected_names:
            invalid = True
            continue
        try:
            offset = float(match.group("offset"))
        except (TypeError, ValueError):
            invalid = True
            conflicted.add(name)
            offsets.pop(name, None)
            continue
        if not math.isfinite(offset) or offset < 0.0 or name in offsets:
            invalid = True
            conflicted.add(name)
            offsets.pop(name, None)
            continue
        if name not in conflicted:
            offsets[name] = offset
    if set(offsets) != expected_names:
        invalid = True
    return offsets, invalid


def _canonical_recording_lof(
    offsets: Mapping[str, float],
    renamed_names: Mapping[str, str],
) -> str:
    lines = [
        f'file "{renamed_names[name]}" offset {offsets[name]:.14f}'
        for name in sorted(offsets)
        if name in renamed_names
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def _privacy_safe_recording_rpp(
    text: str,
    *,
    renamed_names: Mapping[str, str],
    recorder_keys: set[str],
) -> tuple[str, bool]:
    """Remove generated recorder identities and absolute FILE paths from RPP."""

    lines: list[str] = []
    unknown_file = False
    unknown_index = 0
    for line in text.splitlines(keepends=True):
        ending = ""
        body = line
        if body.endswith("\r\n"):
            body, ending = body[:-2], "\r\n"
        elif body.endswith("\n"):
            body, ending = body[:-1], "\n"
        file_match = _RPP_FILE_LINE_RE.fullmatch(body)
        if file_match is not None:
            raw_path = file_match.group("path")
            basename = raw_path.replace("\\", "/").rsplit("/", 1)[-1]
            safe_name = renamed_names.get(basename)
            if safe_name is None:
                unknown_file = True
                unknown_index += 1
                safe_name = f"unverified-server-media-{unknown_index:03d}.wav"
            lines.append(
                f'{file_match.group("indent")}FILE "{safe_name}"'
                f'{file_match.group("suffix")}{ending}'
            )
            continue
        name_match = _RPP_NAME_LINE_RE.fullmatch(body)
        if name_match is not None:
            lines.append(
                f'{name_match.group("indent")}NAME WebJam recorded source{ending}'
            )
            continue
        safe_body = body
        for old_name, new_name in renamed_names.items():
            safe_body = safe_body.replace(old_name, new_name)
        for recorder_key in recorder_keys:
            safe_body = safe_body.replace(recorder_key, "WebJam_recorded_source")
        lines.append(safe_body + ending)
    return "".join(lines), unknown_file


def _streaming_file_identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    descriptor: int | None = None
    try:
        initial = path.lstat()
        if not stat.S_ISREG(initial.st_mode):
            raise OSError("media is not a regular file")
        flags = os.O_RDONLY
        for flag_name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= int(getattr(os, flag_name, 0))
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)
        ):
            raise OSError("media changed during validation")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
            finished = os.fstat(source.fileno())
        if (
            (finished.st_dev, finished.st_ino, finished.st_size, finished.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            or size != finished.st_size
        ):
            raise OSError("media changed during validation")
        current = path.lstat()
        if (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
        ) != (
            finished.st_dev,
            finished.st_ino,
            finished.st_size,
            finished.st_mtime_ns,
        ):
            raise OSError("media changed during validation")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    return size, digest.hexdigest()


def _bounded_recording_sidecars(
    take_dir: Path, suffix: str, *, max_files: int
) -> list[Path]:
    """List a small set of regular Jamulus project sidecars."""

    matches: list[Path] = []
    try:
        try:
            directory_info = take_dir.lstat()
        except FileNotFoundError:
            return []
        if not stat.S_ISDIR(directory_info.st_mode):
            raise OSError
        for item in take_dir.iterdir():
            if item.suffix.lower() != suffix:
                continue
            info = item.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > _RECORDING_SIDECAR_MAX_BYTES
            ):
                raise OSError
            matches.append(item)
            if len(matches) > max_files:
                raise OSError
    except OSError:
        raise OSError("Server recording project evidence was unsafe.") from None
    return sorted(matches, key=lambda item: item.name)


def _read_bounded_recording_sidecar(path: Path) -> bytes:
    """Read one pinned regular sidecar with strict per-file bounds."""

    descriptor: int | None = None
    try:
        initial = path.lstat()
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_size > _RECORDING_SIDECAR_MAX_BYTES
        ):
            raise OSError
        flags = os.O_RDONLY
        for flag_name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= int(getattr(os, flag_name, 0))
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > _RECORDING_SIDECAR_MAX_BYTES
            or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)
        ):
            raise OSError
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            raw = source.read(_RECORDING_SIDECAR_MAX_BYTES + 1)
        if len(raw) > _RECORDING_SIDECAR_MAX_BYTES:
            raise OSError
        return raw
    except OSError:
        raise OSError("Server recording project evidence was unsafe.") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _read_recording_staging_payload(staging_path: Path) -> Mapping | None:
    """Read one small regular staging receipt without retaining raw paths."""

    descriptor: int | None = None
    try:
        initial = staging_path.lstat()
        if not stat.S_ISREG(initial.st_mode):
            return None
        if initial.st_size > _RECORDING_STAGING_MAX_BYTES:
            return None
        flags = os.O_RDONLY
        for flag_name in ("O_BINARY", "O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= int(getattr(os, flag_name, 0))
        descriptor = os.open(staging_path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > _RECORDING_STAGING_MAX_BYTES
            or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)
        ):
            return None
        with os.fdopen(descriptor, "rb") as source:
            descriptor = None
            raw = source.read(_RECORDING_STAGING_MAX_BYTES + 1)
        if len(raw) > _RECORDING_STAGING_MAX_BYTES:
            return None
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    return payload if isinstance(payload, Mapping) else None


def recording_staging_identity(
    take_dir: str | Path,
) -> RecordingStagingIdentity | None:
    """Return only canonical session/take IDs from a v2 staging receipt."""

    payload = _read_recording_staging_payload(
        Path(take_dir).expanduser() / _RECORDING_STAGING_NAME
    )
    if payload is None or payload.get("schema") != 2:
        return None
    try:
        return RecordingStagingIdentity(
            session_id=payload.get("session_id", ""),
            take_id=payload.get("take_id", ""),
        )
    except ValueError:
        return None


def _load_recording_staging_evidence(
    staging_path: Path,
    server_wavs: list[Path],
) -> tuple[
    dict[str, JamulusRecordingFilename | None],
    dict[str, float | None],
    dict[str, str],
    RecordingStagingIdentity | None,
] | None:
    """Load only address-free, media-bound crash recovery evidence."""

    payload = _read_recording_staging_payload(staging_path)
    if payload is None:
        return None
    if payload.get("schema") not in {1, 2}:
        return None
    identity = None
    if payload.get("schema") == 2:
        try:
            identity = RecordingStagingIdentity(
                session_id=payload.get("session_id", ""),
                take_id=payload.get("take_id", ""),
            )
        except ValueError:
            return None
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) != len(server_wavs):
        return None
    wavs_by_name = {item.name: item for item in server_wavs}
    if len(wavs_by_name) != len(server_wavs):
        return None
    parsed_entries: list[
        tuple[str, JamulusRecordingFilename | None, float | None, int, str]
    ] = []
    target_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, Mapping):
            return None
        filename = entry.get("filename")
        if (
            not isinstance(filename, str)
            or _OPAQUE_SERVER_MEDIA_RE.fullmatch(filename) is None
            or filename in target_names
        ):
            return None
        target_names.add(filename)
        size = entry.get("size_bytes")
        checksum = entry.get("sha256")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(checksum, str)
            or _RECORDER_KEY_RE.fullmatch(checksum) is None
        ):
            return None
        digest = entry.get("recorder_key_sha256")
        if digest is None:
            if any(
                entry.get(key) is not None
                for key in ("start_frame", "channels", "collision_index")
            ):
                return None
            parsed = None
        else:
            try:
                parsed = JamulusRecordingFilename(
                    recorder_key_sha256=digest,
                    start_frame=entry.get("start_frame"),
                    channels=entry.get("channels"),
                    collision_index=entry.get("collision_index", 0),
                )
            except (TypeError, ValueError):
                return None
        raw_offset = entry.get("offset_s")
        if raw_offset is None:
            offset = None
        elif isinstance(raw_offset, bool) or not isinstance(raw_offset, (int, float)):
            return None
        else:
            offset = float(raw_offset)
            if not math.isfinite(offset) or offset < 0.0:
                return None
        parsed_entries.append((filename, parsed, offset, size, checksum))

    current_identities: dict[str, tuple[int, str]] = {}
    current_facts: dict[str, JamulusRecordingFilename | None] = {}
    try:
        for name, media in wavs_by_name.items():
            if media.is_symlink() or not media.is_file():
                return None
            current_identities[name] = _streaming_file_identity(media)
            current_facts[name] = parse_jamulus_recording_filename(name)
    except OSError:
        return None

    facts: dict[str, JamulusRecordingFilename | None] = {}
    offsets: dict[str, float | None] = {}
    targets: dict[str, str] = {}
    unmatched = set(wavs_by_name)
    for target, parsed, offset, size, checksum in parsed_entries:
        candidates = [
            name
            for name in unmatched
            if current_identities[name] == (size, checksum)
        ]
        # A completed partial rename keeps its intended opaque target, which
        # is stronger than content identity alone when silent segments happen
        # to be byte-identical.
        if target in candidates:
            source_name = target
        else:
            native_matches = [
                name for name in candidates if current_facts[name] == parsed
            ]
            if len(native_matches) != 1:
                return None
            source_name = native_matches[0]
        unmatched.remove(source_name)
        facts[source_name] = parsed
        offsets[source_name] = offset
        targets[source_name] = target
    if unmatched or set(targets.values()) != target_names:
        return None
    return facts, offsets, targets, identity


def snapshot_take_directories(root: str | Path) -> dict[Path, int]:
    """Return immediate take directories and mtimes without ever raising."""
    path = Path(root).expanduser()
    try:
        if not path.is_dir():
            return {}
        return {
            child: child.stat().st_mtime_ns
            for child in path.iterdir()
            if _is_visible_take_directory(child)
        }
    except OSError:
        return {}


def find_changed_take(root: str | Path, before: dict[Path, int]) -> Optional[Path]:
    """Return the sole changed take directory, or fail closed on ambiguity."""
    path = Path(root).expanduser()
    candidates: list[tuple[int, Path]] = []
    try:
        for child in path.iterdir():
            if not _is_visible_take_directory(child):
                continue
            stamp = child.stat().st_mtime_ns
            if child not in before or stamp > before[child]:
                candidates.append((stamp, child))
    except OSError:
        return None
    if not candidates:
        return None
    if len(candidates) != 1:
        return None
    return candidates[0][1]


def wait_for_take_files_stable(
    take_dir: Path,
    *,
    polls: int = 8,
    interval_s: float = 0.25,
) -> bool:
    """Wait until audio file sizes stop changing for two consecutive polls."""
    previous: Optional[tuple[tuple[str, int], ...]] = None
    stable = 0
    for _ in range(max(1, polls)):
        try:
            current = tuple(sorted(
                (p.name, p.stat().st_size)
                for p in take_dir.iterdir()
                if p.is_file() and p.suffix.lower() in _AUDIO_EXTS
            ))
        except OSError:
            current = ()
        if current and current == previous:
            stable += 1
            if stable >= 2:
                return True
        else:
            stable = 0
        previous = current
        time.sleep(max(0.0, interval_s))
    return False


def _track_has_signal(path: Path) -> Optional[bool]:
    """Sample a few short windows; return None when the file cannot be read."""
    try:
        import numpy as np
        import soundfile as sf  # type: ignore

        with sf.SoundFile(str(path)) as audio:
            if len(audio) <= 0:
                return False
            window = min(4096, len(audio))
            starts = {0, max(0, len(audio) // 2 - window // 2), max(0, len(audio) - window)}
            for start in starts:
                audio.seek(start)
                block = audio.read(window, dtype="float32", always_2d=True)
                if block.size and float(np.max(np.abs(block))) > 1e-5:
                    return True
            return False
    except Exception:  # noqa: BLE001
        return None


def validate_take(take_dir: str | Path, *, expected_tracks: int = 0,
                  require_48k: bool = True,
                  required_local_stems: int = 0) -> TakeValidationResult:
    """Validate a completed Jamulus take and report errors separately from warnings."""
    path = Path(take_dir).expanduser()
    take = load_take(path)
    if take is None:
        return TakeValidationResult(None, ("No readable audio tracks were created.",))

    if take.review_only:
        errors = take.manifest_errors or (
            take.export_block_reason or EVIDENCE_ONLY_EXPORT_BLOCK_REASON,
        )
        return TakeValidationResult(
            take,
            tuple(errors),
            take.manifest_warnings,
            take.manifest_path,
        )

    # Reconciliation performed while loading is part of validation truth.  In
    # particular, an immutable schema-v2 manifest must not remain ``ok`` after
    # one of its declared media objects has disappeared or changed.
    errors: list[str] = list(take.manifest_errors)
    warnings: list[str] = list(take.manifest_warnings)
    if expected_tracks > 0 and take.track_count < expected_tracks:
        errors.append(
            f"Expected at least {expected_tracks} tracks but found {take.track_count}."
        )
    rates = {track.samplerate for track in take.tracks if track.samplerate > 0}
    if len(rates) > 1:
        errors.append(f"Tracks use different sample rates: {sorted(rates)}.")
    if require_48k and any(rate != 48000 for rate in rates):
        errors.append(f"All tracks must be 48 kHz; found {sorted(rates)}.")
    local_tracks = [
        track for track in take.tracks
        if track.source in {"local_ssl", "local_isolated"}
    ]
    if required_local_stems and len(local_tracks) < required_local_stems:
        errors.append(
            f"Expected {required_local_stems} isolated host stems but found "
            f"{len(local_tracks)}."
        )
    for track in take.tracks:
        if track.segments and not track.has_supported_channel_topology:
            errors.append(
                f"{track.name} does not have one consistent mono/stereo "
                "channel layout across its segments."
            )
        # Loading already records the exact reason a missing/damaged manifest
        # segment is blocked. Do not follow or probe that path again here: an
        # unsafe take-local symlink must remain inert through validation.
        if track.media_status in {"missing", "damaged", "transfer_failed"}:
            continue
        try:
            size = track.path.stat().st_size
        except OSError:
            size = 0
        if size <= 44 or track.duration_s <= 0 or track.samplerate <= 0:
            errors.append(f"{track.name} is empty or unreadable.")
            continue
        signal = _track_has_signal(track.path)
        if signal is False:
            warnings.append(f"{track.name} appears silent.")
        elif signal is None:
            warnings.append(f"{track.name} could not be checked for audible signal.")
    return TakeValidationResult(
        take,
        tuple(dict.fromkeys(errors)),
        tuple(dict.fromkeys(warnings)),
        take.manifest_path,
    )


_LOCAL_STEM_PREFIXES = ("host-guitar", "host-vocal")
_ENVELOPE_BLOCK = 480  # 10 ms at 48 kHz → a true 100 Hz amplitude envelope

# Confidence is the normalized full-rate correlation at the refined lag: the
# same performance through the local and server paths scores near 1.0 even
# after the Opus codec, while unrelated audio stays near zero (≈0.01 for a
# five-second window), so 0.15 is a conservative floor rather than a tuned
# value.
ALIGNMENT_CONFIDENCE_MIN = 0.15
ALIGNMENT_METHOD = "envelope+refine-v2"


@dataclass(frozen=True, slots=True)
class _LocalCaptureTopology:
    """Path-free logical input identity supplied by LocalCaptureResult."""

    ordinal: int
    stem: str
    source_channels: tuple[int, ...]

    @property
    def channel_count(self) -> int:
        return len(self.source_channels)

    @property
    def display_name(self) -> str:
        if self.stem in _LOCAL_STEM_PREFIXES:
            return ""
        stem = self.stem[6:] if self.stem.casefold().startswith("local-") else self.stem
        return _prettify(stem)


def _validated_local_capture_topology(
    value: object,
) -> tuple[_LocalCaptureTopology, ...] | None:
    """Reduce typed capture tracks to one exact, bounded manifest binding.

    ``None`` means an older caller supplied no topology evidence. An explicit
    empty tuple instead means the capture contract expected no local files.
    """

    if value is None:
        return None
    try:
        from core.local_capture import LocalCaptureTrack

        entries = tuple(value)
    except (ImportError, TypeError) as exc:
        raise TypeError(
            "local_capture_tracks must be LocalCaptureTrack values."
        ) from exc
    if len(entries) > 32 or any(
        not isinstance(item, LocalCaptureTrack) for item in entries
    ):
        raise TypeError(
            "local_capture_tracks must contain at most 32 LocalCaptureTrack values."
        )
    stems: set[str] = set()
    source_channels: set[int] = set()
    topology: list[_LocalCaptureTopology] = []
    for ordinal, item in enumerate(entries):
        stem_key = item.stem.casefold()
        item_channels = tuple(item.source_channels)
        if stem_key in stems or source_channels.intersection(item_channels):
            raise ValueError(
                "local_capture_tracks contains duplicate track or channel identity."
            )
        stems.add(stem_key)
        source_channels.update(item_channels)
        topology.append(
            _LocalCaptureTopology(ordinal, item.stem, item_channels)
        )
    if len(source_channels) > 32:
        raise ValueError("local_capture_tracks exceeds the 32-channel limit.")
    return tuple(topology)


def _local_filename_matches_topology(name: str, stem: str) -> bool:
    """Match normal, collision-safe, and recovered names for one exact stem."""

    lowered = name.casefold()
    escaped = re.escape(stem.casefold())
    return bool(
        re.fullmatch(
            rf"{escaped}(?:\.recovered-partial|-local(?:-[0-9]+)?)?\.wav",
            lowered,
        )
    )


def is_local_stem_name(name: str) -> bool:
    """True for supplemental host stems.

    Recognizes the legacy fixed pair (host-guitar*/host-vocal*) and every
    configured input-map stem, which carries the ``local-`` prefix.
    """
    lowered = name.lower()
    return lowered.endswith(".wav") and lowered.startswith(
        (*_LOCAL_STEM_PREFIXES, "local-")
    )


def _envelope_100hz(signal):
    """Rectified block-mean envelope, mean-subtracted for correlation.

    Block averaging (unlike stride decimation) is alias-free, so the peak and
    the confidence it feeds reflect real amplitude agreement.
    """
    import numpy as np

    usable = (signal.size // _ENVELOPE_BLOCK) * _ENVELOPE_BLOCK
    if usable == 0:
        return np.zeros(0, dtype="float32")
    env = np.abs(signal[:usable]).reshape(-1, _ENVELOPE_BLOCK).mean(axis=1)
    return env - float(np.mean(env))


def _refine_lag(server_sig, local_sig, coarse_lag: int, anchor: int) -> tuple[int, float]:
    """Sample-accurate lag within ±one envelope block of the coarse peak.

    Sweeps a bounded normalized correlation of the raw 48 kHz signals around
    the loudest local passage, removing the 10 ms envelope quantization that
    would otherwise land in the manifest offset.  Returns ``(lag, value)``;
    the value doubles as the alignment confidence because raw-sample
    correlation separates a genuine match from unrelated audio far more
    sharply than the coarse envelope does.
    """
    import numpy as np

    half = 48000 * 5 // 2
    best_val = 0.0
    best_lag = coarse_lag
    for lag in range(coarse_lag - _ENVELOPE_BLOCK, coarse_lag + _ENVELOPE_BLOCK + 1):
        start = max(0, anchor - half, -lag)
        stop = min(len(local_sig), anchor + half, len(server_sig) - lag)
        if stop - start < 4800:  # need at least 100 ms of overlap
            continue
        local_part = local_sig[start:stop]
        server_part = server_sig[start + lag:stop + lag]
        denom = float(np.linalg.norm(local_part) * np.linalg.norm(server_part))
        if denom <= 0.0:
            continue
        value = abs(float(np.dot(local_part, server_part))) / denom
        if value > best_val:
            best_val = value
            best_lag = lag
    return best_lag, best_val


def estimate_local_alignment(
    take_dir: str | Path,
    *,
    server_candidates: tuple[Path, ...] | None = None,
) -> tuple[float, float]:
    """Estimate local-stem offset against bounded Jamulus server media.

    Returns ``(offset_seconds, confidence)``.  The offset is signed: it is
    negative when the local stems start before the server take, which is the
    normal case because supplemental capture arms before the server recorder
    acknowledges the RPC start.  Correlation is bounded to keep post-record
    validation responsive and never runs on the audio thread. Callers with
    authenticated identity evidence pass an exact same-participant candidate;
    the legacy all-server scan remains only for schema-v1 compatibility.
    """
    path = Path(take_dir)
    wavs = sorted(p for p in path.glob("*.wav") if p.is_file())
    local = [p for p in wavs if is_local_stem_name(p.name)]
    if server_candidates is None:
        server = [p for p in wavs if not is_local_stem_name(p.name)]
    else:
        server = [
            Path(candidate)
            for candidate in server_candidates
            if Path(candidate).parent == path
            and Path(candidate).is_file()
            and not Path(candidate).is_symlink()
            and not is_local_stem_name(Path(candidate).name)
        ]
    if len(local) < 2 or not server:
        return (0.0, 0.0)
    try:
        import numpy as np
        import soundfile as sf  # type: ignore

        limit = 48000 * 60
        first, rate = sf.read(str(local[0]), frames=limit, dtype="float32")
        second, second_rate = sf.read(str(local[1]), frames=limit, dtype="float32")
        if rate != 48000 or second_rate != rate:
            return (0.0, 0.0)
        length = min(len(first), len(second))
        local_mix = np.asarray(first)[:length] + np.asarray(second)[:length]
        if float(np.max(np.abs(local_mix))) < 1e-5:
            return (0.0, 0.0)
        local_env = _envelope_100hz(local_mix)
        env_norm = float(np.linalg.norm(local_env))
        if local_env.size < 256 or env_norm <= 0.0:
            return (0.0, 0.0)
        # Refine around the loudest local passage, not the take start, so a
        # quiet count-in doesn't starve the fine correlation of signal.
        anchor = int(np.argmax(np.abs(local_env))) * _ENVELOPE_BLOCK
        best_confidence = 0.0
        best_lag_samples = 0
        for candidate in server:
            audio, candidate_rate = sf.read(
                str(candidate), frames=limit, dtype="float32", always_2d=True
            )
            if candidate_rate != rate or audio.size == 0:
                continue
            mono = np.mean(audio, axis=1)
            candidate_env = _envelope_100hz(mono)
            denom = float(np.linalg.norm(candidate_env)) * env_norm
            if candidate_env.size == 0 or denom <= 0.0:
                continue
            correlation = np.correlate(candidate_env, local_env, mode="full")
            index = int(np.argmax(correlation))
            coarse = (index - (len(local_env) - 1)) * _ENVELOPE_BLOCK
            lag, confidence = _refine_lag(mono, local_mix, coarse, anchor)
            if confidence > best_confidence:
                best_lag_samples = lag
                best_confidence = confidence
        return (best_lag_samples / rate, best_confidence)
    except Exception:  # noqa: BLE001 - decoder errors can contain private paths
        _logger.error("Could not align isolated host stems")
        return (0.0, 0.0)


def write_take_manifest(
    take_dir: str | Path, *, expected_tracks: int, required_local_stems: int,
    local_started_utc: str = "", local_duration_s: float = 0.0,
    capture_errors: tuple[str, ...] = (), app_version: str = "",
    participant_names: Optional[dict[int, str]] = None,
    session_title: str = "",
    session_id: str = "", take_id: str = "",
    participant_ids: Optional[dict[int, str]] = None,
    local_participant_id: str = "", local_participant_name: str = "Host",
    capture_device=None, capture_gaps: tuple[object, ...] = (),
    local_capture_tracks: object = None,
    local_total_frames: int = 0,
    local_durable_frames: int | None = None,
    session_evidence: "SessionEvidence | None" = None,
    recording_receipts: tuple[RecorderClientReceipt, ...] | None = None,
    recording_identity_errors: tuple[str, ...] = (),
    required_reference_track: bool = False,
) -> TakeValidationResult:
    """Validate a take and atomically publish schema-v2 project truth.

    The preliminary receipt exists only long enough to classify legacy local
    filenames during validation.  The final file carries durable IDs, exact
    format/frame/hash evidence, explicit local-capture gaps, and
    non-destructive alignment metadata.
    """
    from core.file_io import atomic_write_bytes, atomic_write_text
    from core.take_project import (
        AlignmentState,
        GapInterval,
        MediaSegment,
        MediaStatus,
        Participant,
        ProjectStatus,
        ProjectTrack,
        RecoveryStatus,
        SessionEvidence,
        SessionTimelineEvent,
        SourceQuality,
        SourceType,
        TakeProject,
        TakeProjectError,
        new_project_id,
        take_project_manifest_lock,
        write_take_project,
    )

    path = Path(take_dir)
    manifest_path = path / "webjam-take.json"
    staging_path = path / _RECORDING_STAGING_NAME
    # A published complete schema-v2 take is immutable. Re-running validation
    # must verify it, never reinterpret already-opaque media and erase the
    # durable participant/segment truth from the first successful publication.
    try:
        existing_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        existing_payload = None
    if (
        isinstance(existing_payload, Mapping)
        and existing_payload.get("schema_version") == 2
        and existing_payload.get("status") == "complete"
    ):
        existing_result = validate_take(
            path,
            expected_tracks=expected_tracks + required_local_stems,
            required_local_stems=required_local_stems,
        )
        non_staging_errors = tuple(
            error
            for error in existing_result.errors
            if error != _INTERRUPTED_PUBLICATION_ERROR
        )
        if existing_result.take is not None and not non_staging_errors:
            try:
                staging_path.unlink(missing_ok=True)
            except OSError:
                _logger.warning(
                    "A stale recording staging receipt could not be retired."
                )
            else:
                return _enforce_required_reference_track(
                    validate_take(
                        path,
                        expected_tracks=expected_tracks + required_local_stems,
                        required_local_stems=required_local_stems,
                    ),
                    required=required_reference_track,
                )
        return _enforce_required_reference_track(
            existing_result,
            required=required_reference_track,
        )
    local_topology = _validated_local_capture_topology(local_capture_tracks)
    offset_s = 0.0
    confidence = 0.0
    # The old channel-keyed maps remain accepted for source compatibility but
    # can never identify recorder media: Jamulus writes startFrame in the
    # numeric filename position those maps previously consumed.
    del participant_names, participant_ids
    capture_errors = tuple(dict.fromkeys(
        safe
        for item in capture_errors
        if (safe := _safe_capture_error(item))
    ))
    strict_recording_identity = recording_receipts is not None
    identity_errors = list(dict.fromkeys(
        safe
        for item in recording_identity_errors
        if (safe := _safe_identity_error(item))
    ))
    receipts_by_key: dict[tuple[str, int], RecorderClientReceipt] = {}
    conflicted_keys: set[str] = set()
    for receipt in tuple(recording_receipts or ()):
        if not isinstance(receipt, RecorderClientReceipt):
            raise TypeError(
                "recording_receipts must contain RecorderClientReceipt values."
            )
        digest = receipt.recorder_key_sha256
        same_digest = tuple(
            existing
            for (existing_digest, _channels), existing in receipts_by_key.items()
            if existing_digest == digest
        )
        if any(
            existing.participant_id != receipt.participant_id
            or existing.source_kind != receipt.source_kind
            or existing.source_fingerprint_sha256
            != receipt.source_fingerprint_sha256
            or existing.playback_generation != receipt.playback_generation
            for existing in same_digest
        ):
            conflicted_keys.add(digest)
            for key in tuple(receipts_by_key):
                if key[0] == digest:
                    receipts_by_key.pop(key, None)
            continue
        if digest not in conflicted_keys:
            receipts_by_key[(digest, receipt.channels)] = receipt
    if conflicted_keys:
        identity_errors.append(
            "Authenticated recording identity evidence conflicted for a "
            "Jamulus source. Its audio was preserved for review."
        )

    if session_evidence is None:
        final_session_evidence = SessionEvidence()
    elif isinstance(session_evidence, SessionEvidence):
        # It is deliberately copied below rather than mutated: callers may
        # retain the evidence object while the writer adds file-backed facts.
        final_session_evidence = session_evidence
    else:
        raise TypeError("session_evidence must be a SessionEvidence instance.")

    def _id_or_new(value: str) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            return new_project_id()

    original_offsets: dict[str, float] = {}
    lofs = _bounded_recording_sidecars(
        path, ".lof", max_files=_RECORDING_LOF_MAX_FILES
    )
    rpps = _bounded_recording_sidecars(
        path, ".rpp", max_files=_RECORDING_RPP_MAX_FILES
    )
    try:
        sidecar_bytes = sum(item.lstat().st_size for item in (*lofs, *rpps))
    except OSError:
        raise OSError("Server recording project evidence was unsafe.") from None
    if sidecar_bytes > _RECORDING_SIDECAR_TOTAL_MAX_BYTES:
        raise OSError("Server recording project evidence was unsafe.")
    recording_filename_facts: dict[str, JamulusRecordingFilename | None] = {}
    recording_offsets: dict[str, float | None] = {}

    # Jamulus's native names contain a masked address and port. Once the
    # authenticated evidence has been reduced to digests, replace those names
    # with opaque take-local media names before writing WebJam's manifest.
    server_wavs = [
        item
        for item in sorted(path.glob("*.wav"))
        if not is_local_stem_name(item.name)
    ]
    native_filename_facts = {
        item.name: parse_jamulus_recording_filename(item.name)
        for item in server_wavs
    }
    try:
        staging_path.lstat()
        staging_present = True
    except FileNotFoundError:
        staging_present = False
    except OSError:
        raise OSError("Server recording privacy staging failed.") from None
    resumed_staging = _load_recording_staging_evidence(
        staging_path,
        server_wavs,
    )
    if staging_present and resumed_staging is None:
        # Never reinterpret media beneath an invalid/copy-pasted receipt. Its
        # UUIDs and checksums are a single media binding; accepting only the
        # UUID portion could attach another take and retire the wrong journal.
        raise OSError("Server recording privacy staging failed.")
    resumed_offsets: dict[str, float | None] = {}
    resumed_targets: dict[str, str] = {}
    resumed_identity: RecordingStagingIdentity | None = None
    if resumed_staging is not None:
        (
            native_filename_facts,
            resumed_offsets,
            resumed_targets,
            resumed_identity,
        ) = resumed_staging
    if resumed_identity is not None:
        try:
            supplied_session_id = (
                str(uuid.UUID(str(session_id))) if session_id else ""
            )
            supplied_take_id = str(uuid.UUID(str(take_id))) if take_id else ""
        except (TypeError, ValueError, AttributeError):
            raise OSError("Server recording privacy staging failed.") from None
        if (
            supplied_session_id
            and supplied_session_id != resumed_identity.session_id
        ) or (
            supplied_take_id and supplied_take_id != resumed_identity.take_id
        ):
            raise OSError("Server recording privacy staging failed.")
        stable_session_id = resumed_identity.session_id
        stable_take_id = resumed_identity.take_id
    else:
        stable_session_id = _id_or_new(session_id)
        stable_take_id = _id_or_new(take_id)
    stable_local_participant_id = (
        _id_or_new(local_participant_id)
        if local_participant_id
        else str(uuid.uuid5(
            uuid.UUID(stable_session_id), "participant:local-recorder"
        ))
    )

    def _child_id(label: str) -> str:
        return str(uuid.uuid5(uuid.UUID(stable_take_id), label))

    privacy_stage = strict_recording_identity or any(
        value is not None for value in native_filename_facts.values()
    ) or staging_path.exists()
    if privacy_stage:
        renames: list[tuple[Path, Path, str, str]] = []
        for index, source in enumerate(server_wavs, start=1):
            if source.is_symlink() or not source.is_file():
                raise OSError("Server recording privacy staging failed.")
            target = path / resumed_targets.get(
                source.name,
                f"server-media-{index:03d}.wav",
            )
            if target != source and target.exists():
                raise OSError("Server recording privacy staging failed.")
            parsed = native_filename_facts[source.name]
            recording_filename_facts[target.name] = parsed
            renames.append((source, target, source.name, target.name))

        original_names = {old_name for *_prefix, old_name, _new_name in renames}
        renamed_names = {
            old_name: new_name for *_prefix, old_name, new_name in renames
        }
        sidecars = (*lofs, *rpps)
        original_sidecars: dict[Path, bytes] = {}
        try:
            for sidecar in sidecars:
                original_sidecars[sidecar] = _read_bounded_recording_sidecar(
                    sidecar
                )
            if sum(map(len, original_sidecars.values())) > (
                _RECORDING_SIDECAR_TOTAL_MAX_BYTES
            ):
                raise OSError
        except OSError:
            raise OSError("Server recording privacy staging failed.") from None

        if resumed_staging is not None:
            original_offsets = {
                name: value
                for name, value in resumed_offsets.items()
                if value is not None
            }
        elif len(lofs) == 1:
            original_offsets, invalid_lof = _strict_recording_lof_offsets(
                original_sidecars[lofs[0]],
                expected_names=original_names,
            )
            if invalid_lof:
                identity_errors.append(
                    "Jamulus LOF timing evidence was incomplete or ambiguous. "
                    "Affected audio was preserved at the project origin for review."
                )
        elif len(lofs) > 1:
            identity_errors.append(
                "Multiple Jamulus LOF timing files were ambiguous. Source audio "
                "was preserved at the project origin for review."
            )

        rewritten_sidecars: dict[Path, str] = {}
        for lof in lofs:
            if resumed_staging is not None:
                offsets = original_offsets
            else:
                offsets, _invalid = _strict_recording_lof_offsets(
                    original_sidecars[lof],
                    expected_names=original_names,
                )
            rewritten_sidecars[lof] = _canonical_recording_lof(
                offsets,
                renamed_names,
            )
        recorder_keys = {
            match.group("key")
            for old_name in original_names
            if (match := _JAMULUS_RECORDING_FILENAME_RE.fullmatch(old_name))
            is not None
        }
        for rpp in rpps:
            try:
                rpp_text = original_sidecars[rpp].decode(
                    "utf-8", errors="strict"
                )
            except UnicodeDecodeError:
                raise OSError("Server recording privacy staging failed.") from None
            rpp_names = dict(renamed_names)
            targets_by_fact: dict[JamulusRecordingFilename, list[str]] = {}
            for new_name, fact in recording_filename_facts.items():
                if fact is not None:
                    targets_by_fact.setdefault(fact, []).append(new_name)
            for line in rpp_text.splitlines():
                file_match = _RPP_FILE_LINE_RE.fullmatch(line)
                if file_match is None:
                    continue
                basename = file_match.group("path").replace("\\", "/").rsplit(
                    "/", 1
                )[-1]
                parsed_basename = parse_jamulus_recording_filename(basename)
                matches = (
                    targets_by_fact.get(parsed_basename, [])
                    if parsed_basename is not None
                    else []
                )
                if len(matches) == 1:
                    rpp_names[basename] = matches[0]
            rewritten, unknown_file = _privacy_safe_recording_rpp(
                rpp_text,
                renamed_names=rpp_names,
                recorder_keys=recorder_keys,
            )
            rewritten_sidecars[rpp] = rewritten
            if unknown_file:
                identity_errors.append(
                    "A Jamulus project file referenced unexpected media. Source "
                    "audio was preserved for review."
                )

        for _source, _target, old_name, new_name in renames:
            recording_offsets[new_name] = original_offsets.get(old_name)

        staging_entries: list[dict[str, object]] = []
        try:
            for source, _target, _old_name, new_name in renames:
                size_bytes, checksum = _streaming_file_identity(source)
                parsed = recording_filename_facts[new_name]
                staging_entries.append({
                    "filename": new_name,
                    "recorder_key_sha256": (
                        parsed.recorder_key_sha256 if parsed is not None else None
                    ),
                    "start_frame": (
                        parsed.start_frame if parsed is not None else None
                    ),
                    "channels": parsed.channels if parsed is not None else None,
                    "collision_index": (
                        parsed.collision_index if parsed is not None else None
                    ),
                    "offset_s": recording_offsets[new_name],
                    "size_bytes": size_bytes,
                    "sha256": checksum,
                })
        except OSError:
            raise OSError("Server recording privacy staging failed.") from None
        staging_text = json.dumps(
            {
                "schema": 2,
                "session_id": stable_session_id,
                "take_id": stable_take_id,
                "entries": staging_entries,
            },
            indent=2,
            sort_keys=True,
        ) + "\n"

        completed: list[tuple[Path, Path]] = []
        rewritten_paths: list[Path] = []
        try:
            original_staging = staging_path.read_bytes()
            staging_preexisted = True
        except FileNotFoundError:
            original_staging = b""
            staging_preexisted = False
        except OSError:
            raise OSError("Server recording privacy staging failed.") from None
        staging_attempted = False
        try:
            staging_attempted = True
            atomic_write_text(staging_path, staging_text, mode=0o600)
            for source, target, _old_name, _new_name in renames:
                if source != target:
                    source.replace(target)
                    completed.append((target, source))
            for sidecar, rewritten in rewritten_sidecars.items():
                # Restoration is safe even if the attempted atomic write fails
                # before replacement, and necessary if parent fsync fails after
                # replacement. Record the attempt before entering the helper.
                rewritten_paths.append(sidecar)
                atomic_write_text(
                    sidecar,
                    rewritten,
                    mode=0o600,
                )
        except Exception:
            rollback_failed = False
            for sidecar in reversed(rewritten_paths):
                try:
                    atomic_write_bytes(
                        sidecar,
                        original_sidecars[sidecar],
                        mode=0o600,
                    )
                except OSError:
                    rollback_failed = True
            for current, original in reversed(completed):
                try:
                    if current.exists() and not original.exists():
                        current.replace(original)
                except OSError:
                    rollback_failed = True
            if staging_attempted:
                try:
                    if staging_preexisted:
                        atomic_write_bytes(
                            staging_path,
                            original_staging,
                            mode=0o600,
                        )
                    else:
                        staging_path.unlink(missing_ok=True)
                except OSError:
                    rollback_failed = True
            if rollback_failed:
                _logger.error(
                    "Recording privacy staging rollback needs attention"
                )
            raise OSError("Server recording privacy staging failed.") from None
    else:
        if lofs:
            original_offsets = parse_lof_offsets(lofs[0])
        for item in server_wavs:
            recording_filename_facts[item.name] = native_filename_facts[item.name]
            recording_offsets[item.name] = original_offsets.get(item.name)

    def _receipt_for_filename(filename: str) -> RecorderClientReceipt | None:
        parsed = recording_filename_facts.get(filename)
        if parsed is None or parsed.recorder_key_sha256 in conflicted_keys:
            return None
        return receipts_by_key.get((parsed.recorder_key_sha256, parsed.channels))

    if required_local_stems:
        alignment_candidates: tuple[Path, ...] | None = None
        if strict_recording_identity:
            proven_host_media = tuple(
                path / filename
                for filename in recording_filename_facts
                if (
                    (receipt := _receipt_for_filename(filename)) is not None
                    and receipt.source_kind == "musician"
                    and receipt.participant_id == stable_local_participant_id
                )
            )
            # Never choose the most-correlated participant. Exactly one proven
            # same-host server stem is required for automatic Local Original
            # alignment; reconnect ambiguity remains review-only.
            alignment_candidates = (
                proven_host_media if len(proven_host_media) == 1 else ()
            )
        offset_s, confidence = estimate_local_alignment(
            path,
            server_candidates=alignment_candidates,
        )
    # A trusted journal can outlive every media writer. Publish its recovery
    # truth directly as schema v2 instead of fabricating a zero-frame WAV just
    # to pass the legacy discovery path. This branch is intentionally narrow:
    # any real media, expected track, or ordinary session uses the normal
    # validation/inventory flow below unchanged.
    try:
        has_audio_media = path.is_dir() and any(
            item.is_file() and item.suffix.lower() in _AUDIO_EXTS
            for item in path.iterdir()
        )
    except OSError:
        has_audio_media = False
    evidence_only = (
        expected_tracks == 0
        and required_local_stems == 0
        and not has_audio_media
        and final_session_evidence.recovery_status
        is RecoveryStatus.NEEDS_ATTENTION
    )
    if evidence_only:
        evidence_errors = list(dict.fromkeys(capture_errors))
        if not any(
            EVIDENCE_ONLY_EXPORT_BLOCK_REASON in item
            for item in evidence_errors
        ):
            evidence_errors.append(EVIDENCE_ONLY_EXPORT_BLOCK_REASON)
        errors = tuple(evidence_errors)
        participants = ()
        if final_session_evidence.host.participant_id:
            participants = (
                Participant(
                    final_session_evidence.host.participant_id,
                    final_session_evidence.host.display_name or "Recovered host",
                ),
            )
        project = TakeProject(
            session_id=stable_session_id,
            take_id=stable_take_id,
            session_title=str(session_title or "").strip(),
            take_name=path.name or "Recovered recording evidence",
            status=ProjectStatus.NEEDS_ATTENTION,
            # An evidence-only project has no audio rate to infer. 48 kHz is
            # the project timeline convention, not a claim about missing media.
            project_sample_rate=48000,
            participants=participants,
            tracks=(),
            app_version=app_version,
            created_utc=local_started_utc,
            errors=errors,
            session_evidence=final_session_evidence,
        )
        manifest_path = write_take_project(path, project)
        loaded = load_take(path)
        if loaded is None or not loaded.review_only:
            raise RuntimeError(
                "The evidence-only recovery project could not be reopened safely."
            )
        return TakeValidationResult(loaded, errors, (), manifest_path)

    durable_frame_limit: int | None = None
    if local_durable_frames is not None:
        try:
            durable_frame_limit = (
                0
                if isinstance(local_durable_frames, bool)
                else max(0, int(local_durable_frames))
            )
        except (TypeError, ValueError):
            durable_frame_limit = 0

    # Write a preliminary manifest so load_take can classify supplemental files.
    preliminary = {
        "schema_version": 1,
        "app_version": app_version,
        "session_title": str(session_title or "").strip(),
        "status": "validating",
        "expected_server_tracks": expected_tracks,
        "required_local_stems": required_local_stems,
        "local_capture": {
            "started_utc": local_started_utc,
            "duration_s": round(local_duration_s, 6),
            "offset_s": round(offset_s, 6),
            "alignment_confidence": round(confidence, 6),
            "alignment_method": ALIGNMENT_METHOD,
            "errors": list(capture_errors),
        },
        "tracks": [
            {"filename": p.name,
             "name": (
                 (
                     _receipt_for_filename(p.name).display_name
                     if _receipt_for_filename(p.name) is not None
                     else "Unverified Jamulus source"
                 )
                 if not is_local_stem_name(p.name)
                 else None
             ),
             "source": (
                 "local_ssl"
                 if is_local_stem_name(p.name)
                 else "live_reference"
                 if (
                     _receipt_for_filename(p.name) is not None
                     and _receipt_for_filename(p.name).source_kind
                     == "reference_track"
                 )
                 else "jamulus_server"
             ),
             "offset_s": round(offset_s, 6) if is_local_stem_name(p.name) else None}
            for p in sorted(path.glob("*.wav"))
        ],
    }
    if durable_frame_limit is not None:
        preliminary["local_capture"]["durable_frames"] = durable_frame_limit
    if not final_session_evidence.is_empty:
        # A crash before final classification still leaves bounded recording
        # provenance beside the source media. This is a receipt, not a claim
        # that the incomplete folder has become a verified project.
        preliminary["session"] = final_session_evidence.to_dict()
    with take_project_manifest_lock(path):
        atomic_write_text(manifest_path, json.dumps(preliminary, indent=2), mode=0o600)
    result = validate_take(
        path, expected_tracks=expected_tracks + required_local_stems,
        required_local_stems=required_local_stems,
    )
    errors = [
        error
        for error in result.errors
        if error != _INTERRUPTED_PUBLICATION_ERROR
    ] + list(capture_errors) + identity_errors
    if required_local_stems and confidence < ALIGNMENT_CONFIDENCE_MIN:
        errors.append("Isolated host stems could not be aligned confidently.")
    take = result.take
    if take is None:
        preliminary.update({
            "status": "needs_attention",
            "errors": errors,
            "warnings": list(result.warnings),
            "tracks": [],
        })
        with take_project_manifest_lock(path):
            atomic_write_text(
                manifest_path, json.dumps(preliminary, indent=2), mode=0o600
            )
        return TakeValidationResult(
            None, tuple(errors), result.warnings, manifest_path,
        )

    participants_by_id: dict[str, Participant] = {}
    project_sample_rate = 48_000
    grouped_tracks: dict[str, dict[str, object]] = {}
    local_tracks = tuple(
        track
        for track in take.tracks
        if track.source in {"local_ssl", "local_isolated"}
    )
    local_topology_by_name: dict[str, _LocalCaptureTopology] = {}
    if local_topology is not None:
        if required_local_stems != len(local_topology):
            errors.append(
                "The required local-original count did not match the bound "
                "logical capture topology."
            )
        if capture_device is not None:
            planned_source_channels = tuple(
                channel
                for item in local_topology
                for channel in item.source_channels
            )
            observed_source_channels = tuple(
                getattr(capture_device, "channel_indices", ()) or ()
            )
            if observed_source_channels != planned_source_channels:
                errors.append(
                    "The capture device channel map did not match the bound "
                    "logical local-original topology."
                )
        claimed_names: set[str] = set()
        for item in local_topology:
            candidates = tuple(
                track
                for track in local_tracks
                if track.path.name.casefold() not in claimed_names
                and _local_filename_matches_topology(track.path.name, item.stem)
            )
            if len(candidates) != 1:
                errors.append(
                    "A bound logical local-original track could not be matched "
                    "to exactly one captured WAV."
                )
                continue
            name_key = candidates[0].path.name.casefold()
            claimed_names.add(name_key)
            local_topology_by_name[name_key] = item
        if len(claimed_names) != len(local_tracks):
            errors.append(
                "The captured local-original WAV inventory did not exactly "
                "match its bound logical track map."
            )
        for gap in capture_gaps:
            try:
                gap_channels = tuple(getattr(gap, "channels", ()) or ())
                if any(
                    isinstance(channel, bool)
                    or not isinstance(channel, int)
                    or not 0 <= channel < len(local_topology)
                    for channel in gap_channels
                ):
                    errors.append(
                        "Local capture gap evidence referenced an unavailable "
                        "logical track."
                    )
            except TypeError:
                errors.append("Local capture gap evidence was unreadable.")

    # Older callers have no typed topology evidence. Preserve their stable
    # alphabetical mapping (including 0=host-guitar, 1=host-vocal) while new
    # captures use the exact LocalCaptureResult order above.
    local_channel_by_name = {
        name: index
        for index, name in enumerate(
            sorted(
                track.path.name.casefold()
                for track in local_tracks
            )
        )
    }
    for order, track in enumerate(take.tracks):
        local = track.source in {"local_ssl", "local_isolated"}
        topology_item: _LocalCaptureTopology | None = None
        source_fingerprint = ""
        playback_generation = 0
        if local:
            participant_id: str | None = stable_local_participant_id
            participant_name = (
                " ".join(str(local_participant_name or "Host").split()) or "Host"
            )
            source_type = SourceType.LOCAL_ISOLATED
            quality = SourceQuality.UNVERIFIED
            topology_item = local_topology_by_name.get(track.path.name.casefold())
            local_channel = (
                topology_item.ordinal
                if topology_item is not None
                else local_channel_by_name.get(track.path.name.casefold(), 0)
            )
            group_key = (
                f"local:{topology_item.ordinal}:{topology_item.stem.casefold()}"
                if topology_item is not None
                else f"local:{order}"
            )
            project_start_frame = 0
        else:
            parsed = recording_filename_facts.get(track.path.name)
            receipt = _receipt_for_filename(track.path.name)
            if parsed is None:
                errors.append(
                    "A Jamulus recording filename did not match the pinned "
                    "3.12.2 recorder contract. Its audio was preserved for review."
                )
            if receipt is None:
                errors.append(
                    "WebJam could not prove the participant identity for a "
                    "Jamulus recording. Its audio was preserved for review."
                )
                participant_id = None
                participant_name = "Unverified Jamulus source"
                source_type = SourceType.JAMULUS_SERVER
                group_key = f"unverified:{order}"
            else:
                participant_id = receipt.participant_id
                participant_name = receipt.display_name
                source_fingerprint = (
                    receipt.source_fingerprint_sha256
                    if receipt.source_kind == "reference_track"
                    else ""
                )
                playback_generation = (
                    receipt.playback_generation
                    if receipt.source_kind == "reference_track"
                    else 0
                )
                source_type = (
                    SourceType.LIVE_REFERENCE
                    if receipt.source_kind == "reference_track"
                    else SourceType.JAMULUS_SERVER
                )
                group_key = f"proved:{receipt.source_kind}:{receipt.participant_id}"
                if source_fingerprint:
                    group_key = f"{group_key}:{source_fingerprint}"
                if playback_generation:
                    group_key = f"{group_key}:{playback_generation}"
            quality = (
                SourceQuality.REFERENCE
                if source_type is SourceType.LIVE_REFERENCE
                else SourceQuality.NETWORK_TRACK
            )
            local_channel = -1
            lof_offset = recording_offsets.get(track.path.name)
            if (
                lof_offset is None
                or not math.isfinite(float(lof_offset))
                or float(lof_offset) < 0.0
            ):
                errors.append(
                    "A Jamulus recording had no trustworthy LOF timeline "
                    "position. Its audio was preserved at the project origin "
                    "for review."
                )
                project_start_frame = 0
            else:
                project_start_frame = int(
                    round(float(lof_offset) * project_sample_rate)
                )

        if participant_id is not None:
            participants_by_id.setdefault(
                participant_id,
                Participant(participant_id, participant_name),
            )

        evidence = _audio_file_evidence(track.path)
        frame_count = int(evidence["frame_count"])
        observed_channel_count = int(evidence["channels"] or 0)
        if observed_channel_count not in {1, 2}:
            errors.append(
                f"{track.name} does not contain a supported mono/stereo "
                "channel layout."
            )
        if (
            local
            and topology_item is not None
            and observed_channel_count != topology_item.channel_count
        ):
            errors.append(
                "A captured local-original WAV did not match its bound mono/stereo "
                "track layout."
            )
        if (
            not local
            and parsed is not None
            and observed_channel_count != parsed.channels
        ):
            errors.append(
                "A Jamulus recording's channel metadata did not match its audio. "
                "The source was preserved for review."
            )
        if local and local_total_frames > 0 and frame_count != int(local_total_frames):
            errors.append(
                f"{track.name} contains {frame_count} frames but local "
                f"capture reported {int(local_total_frames)}."
            )
        gaps: list[GapInterval] = []
        if local:
            for item in capture_gaps:
                try:
                    channels = tuple(getattr(item, "channels", ()) or ())
                    if channels and local_channel not in channels:
                        continue
                    gap_start = int(getattr(item, "start_frame"))
                    gap_frames = int(getattr(item, "frame_count"))
                    if (
                        gap_start < 0
                        or gap_frames <= 0
                        or gap_start + gap_frames > frame_count
                    ):
                        errors.append(
                            f"{track.name} has gap metadata outside its audio frame range."
                        )
                        continue
                    gaps.append(GapInterval(
                        start_frame=gap_start,
                        frame_count=gap_frames,
                        reason=str(getattr(item, "reason")),
                        # Capture gaps select logical WAVs. Once selected, the
                        # unavailable interval covers every channel in that
                        # mono/stereo logical track.
                        channels=tuple(range(max(1, observed_channel_count))),
                    ))
                except (TypeError, ValueError):
                    errors.append(f"{track.name} has unreadable local gap metadata.")
            if durable_frame_limit is not None:
                durable_for_track = min(frame_count, durable_frame_limit)
                if durable_for_track < frame_count:
                    gaps.append(GapInterval(
                        start_frame=durable_for_track,
                        frame_count=frame_count - durable_for_track,
                        reason="unverified_after_crash_checkpoint",
                        channels=tuple(range(max(1, observed_channel_count))),
                    ))
                    errors.append(
                        f"{track.name} contains {frame_count} frames, but only the "
                        f"first {durable_for_track} were durably checkpointed before "
                        "interruption."
                    )
        segment_status = (
            MediaStatus.AVAILABLE
            if track.path.is_file() and evidence["sample_rate"] > 0
            else MediaStatus.DAMAGED
        )
        if (
            local
            and durable_frame_limit is not None
            and durable_frame_limit < frame_count
            and segment_status is MediaStatus.AVAILABLE
        ):
            segment_status = MediaStatus.PARTIAL
        segment = MediaSegment(
            segment_id=_child_id(f"segment:{order}:{track.path.name}:0"),
            path=track.path.name,
            project_start_frame=project_start_frame,
            frame_count=frame_count,
            sample_rate=max(1, int(evidence["sample_rate"] or track.samplerate)),
            channels=max(1, int(evidence["channels"])),
            sample_format=str(evidence["sample_format"] or "UNKNOWN"),
            media_status=segment_status,
            sha256=str(evidence["sha256"]),
            device_id=(
                str(getattr(capture_device, "device_id", "")) if local else ""
            ),
            gaps=tuple(gaps),
            size_bytes=int(evidence["size_bytes"]),
            has_signal=evidence["has_signal"],
        )
        group = grouped_tracks.get(group_key)
        if group is None:
            grouped_tracks[group_key] = {
                "first_order": order,
                "participant_id": participant_id,
                "name": (
                    (
                        f"{participant_name} — {topology_item.display_name}"
                        if topology_item is not None
                        and topology_item.display_name
                        else f"{participant_name} Input {local_channel + 1}"
                    )
                    if local
                    else participant_name
                ),
                "source_type": source_type,
                "quality": quality,
                "media_status": segment_status,
                "segments": [segment],
                "alignment": AlignmentState(
                    automatic_offset_s=track.offset_s if local else 0.0,
                    confidence=confidence if local else 1.0,
                    method=ALIGNMENT_METHOD if local else "jamulus-lof-v1",
                    reference_fingerprint_sha256=source_fingerprint,
                    reference_playback_generation=playback_generation,
                ),
            }
        else:
            segments = group["segments"]
            assert isinstance(segments, list)
            segments.append(segment)
            if segment_status is not MediaStatus.AVAILABLE:
                group["media_status"] = segment_status

    project_tracks: list[ProjectTrack] = []
    for project_order, (group_key, group) in enumerate(
        sorted(grouped_tracks.items(), key=lambda item: int(item[1]["first_order"]))
    ):
        raw_segments = group["segments"]
        assert isinstance(raw_segments, list)
        segments = tuple(sorted(
            raw_segments,
            key=lambda item: (item.project_start_frame, item.segment_id),
        ))
        first_order = int(group["first_order"])
        project_tracks.append(ProjectTrack(
            track_id=_child_id(f"track-group:{first_order}:{group_key}"),
            source_id=_child_id(f"source-group:{first_order}:{group_key}"),
            participant_id=group["participant_id"],
            name=str(group["name"]),
            instrument="",
            source_type=group["source_type"],
            quality=group["quality"],
            media_status=group["media_status"],
            order=project_order,
            segments=segments,
            alignment=group["alignment"],
        ))

    for project_track in project_tracks:
        try:
            project_track.channel_count
        except TakeProjectError:
            errors.append(
                f"{project_track.name} changes or exceeds the supported "
                "mono/stereo channel layout across reconnect segments."
            )

    grouped_server_tracks = sum(
        track.source_type is not SourceType.LOCAL_ISOLATED
        for track in project_tracks
    )
    if expected_tracks > 0 and grouped_server_tracks < expected_tracks:
        errors.append(
            f"Expected at least {expected_tracks} tracks but found "
            f"{grouped_server_tracks}."
        )

    # A host can be a durable session participant even when this take has no
    # host-owned stem (for example, a host records only the server mix).
    # Keep that identity in the manifest so session evidence never points at
    # an unresolvable participant.
    if final_session_evidence.host.participant_id:
        participants_by_id.setdefault(
            final_session_evidence.host.participant_id,
            Participant(
                final_session_evidence.host.participant_id,
                final_session_evidence.host.display_name or "Host",
            ),
        )

    errors = list(dict.fromkeys(errors))
    # Segment gaps remain the source-of-truth, frame-exact records.  The
    # timeline adds a human-reviewable, project-time index without inventing
    # a wall-clock timestamp or changing any source audio.
    timeline = list(final_session_evidence.timeline)
    for project_track in project_tracks:
        for segment in project_track.segments:
            for gap in segment.gaps:
                event = SessionTimelineEvent(
                    event="media_gap",
                    at_s=(
                        segment.project_start_frame / project_sample_rate
                        + gap.start_frame / segment.sample_rate
                    ),
                    participant_id=project_track.participant_id or "",
                    detail=(
                        f"Segment {segment.segment_id}: {gap.frame_count} source "
                        f"frames unavailable ({gap.reason})."
                    ),
                )
                if event not in timeline:
                    timeline.append(event)
    final_session_evidence = replace(
        final_session_evidence,
        timeline=tuple(timeline),
    )

    if required_reference_track and not any(
        track.source_type is SourceType.LIVE_REFERENCE
        for track in project_tracks
    ):
        errors.append(_REQUIRED_REFERENCE_TRACK_ERROR)

    project = TakeProject(
        session_id=stable_session_id,
        take_id=stable_take_id,
        session_title=str(session_title or "").strip(),
        take_name=path.name or "Take",
        status=ProjectStatus.COMPLETE if not errors else ProjectStatus.NEEDS_ATTENTION,
        project_sample_rate=project_sample_rate,
        participants=tuple(participants_by_id.values()),
        tracks=tuple(project_tracks),
        app_version=app_version,
        created_utc=local_started_utc,
        devices=(capture_device,) if capture_device is not None else (),
        errors=tuple(errors),
        warnings=result.warnings,
        session_evidence=final_session_evidence,
    )
    write_take_project(path, project)
    staging_retire_error = ""
    try:
        staging_path.unlink(missing_ok=True)
    except OSError:
        _logger.warning("A recording staging receipt could not be retired.")
        staging_retire_error = (
            "Recording privacy staging could not be finalized. Source audio "
            "was preserved for review."
        )
    loaded = load_take(path)
    if staging_retire_error:
        errors.append(staging_retire_error)
    return TakeValidationResult(
        loaded, tuple(errors), result.warnings, manifest_path,
    )


def _audio_file_evidence(
    path: Path, *, inspect_signal: bool = True
) -> dict[str, object]:
    """Return exact, streaming evidence without retaining source handles."""
    frame_count = 0
    sample_rate = 0
    channels = 0
    sample_format = ""
    try:
        # Hash through a no-follow descriptor before any codec probe. Besides
        # binding the exact bytes, this prevents a take-local symlink or special
        # file from being opened as audio evidence.
        size, checksum = _streaming_file_identity(path)
    except OSError:
        return {
            "frame_count": 0,
            "sample_rate": 0,
            "channels": 0,
            "sample_format": "",
            "size_bytes": 0,
            "sha256": "",
            "has_signal": None,
        }
    try:
        import soundfile as sf  # type: ignore

        info = sf.info(str(path))
        frame_count = int(info.frames)
        sample_rate = int(info.samplerate)
        channels = int(info.channels)
        sample_format = str(info.subtype or info.format or "")
    except Exception:  # noqa: BLE001
        _logger.debug("A soundfile evidence probe failed")
        if path.suffix.lower() == ".wav":
            try:
                with wave.open(str(path), "rb") as source:
                    frame_count = int(source.getnframes())
                    sample_rate = int(source.getframerate())
                    channels = int(source.getnchannels())
                    sample_format = f"PCM_{source.getsampwidth() * 8}"
            except Exception:  # noqa: BLE001
                _logger.debug("A WAV evidence probe failed")
    return {
        "frame_count": frame_count,
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_format": sample_format,
        "size_bytes": size,
        "sha256": checksum,
        "has_signal": _track_has_signal(path) if inspect_signal else None,
    }


def _probe_audio(path: Path) -> tuple[float, int]:
    """Return (duration_s, samplerate). Tries soundfile, falls back to the
    stdlib ``wave`` module for plain WAVs, returns (0, 0) if unreadable."""
    try:
        import soundfile as sf  # type: ignore
        info = sf.info(str(path))
        if info.samplerate > 0:
            return (info.frames / info.samplerate, int(info.samplerate))
    except Exception:  # noqa: BLE001
        _logger.debug("A soundfile duration probe failed")
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                rate = w.getframerate()
                if rate > 0:
                    return (w.getnframes() / rate, rate)
        except Exception:  # noqa: BLE001
            _logger.debug("A WAV duration probe failed")
    return (0.0, 0)


def parse_lof_offsets(lof_path: Path) -> dict[str, float]:
    """Parse an Audacity ``.lof`` file into {filename: offset_seconds}.

    Lines look like:  file "guitar.wav" offset 3.5
    Only ``file`` directives are honoured; malformed lines are skipped.
    Filenames are keyed by basename so they match regardless of quoting or
    any path prefix the recorder wrote.
    """
    offsets: dict[str, float] = {}
    try:
        text = lof_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        _logger.debug("A LOF timing file could not be read")
        return offsets
    # file "name" [offset N]
    pattern = re.compile(
        r'^\s*file\s+"([^"]+)"(?:.*?\boffset\s+([-+]?\d+(?:\.\d+)?))?',
        re.IGNORECASE,
    )
    for line in text.splitlines():
        m = pattern.match(line)
        if not m:
            continue
        name = Path(m.group(1)).name
        try:
            offsets[name] = float(m.group(2)) if m.group(2) is not None else 0.0
        except (TypeError, ValueError):
            offsets[name] = 0.0
    return offsets


def _prettify(stem: str) -> str:
    """Turn a track filename stem into a display name."""
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    return cleaned.title() if cleaned else stem


def _safe_manifest_audio_path(value: object) -> str | None:
    text = str(value or "").strip()
    if not text or "\\" in text:
        return None
    path = Path(text)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix.lower() not in _AUDIO_EXTS
    ):
        return None
    return path.as_posix()


_SCHEMA_V2_INVALID_MEDIA_INVENTORY = (
    "A completed take has an invalid media inventory."
)
_SCHEMA_V2_UNLISTED_MEDIA = (
    "A completed take contains audio outside its verified media inventory."
)
_SCHEMA_V2_UNSAFE_MEDIA = (
    "A recorded segment was not a regular file inside the take."
)


def _schema_v2_segment_shape_valid(value: object) -> bool:
    """Require the exact media facts needed to verify one v2 segment."""

    if not isinstance(value, Mapping):
        return False
    if _safe_manifest_audio_path(value.get("path")) is None:
        return False
    try:
        uuid.UUID(str(value.get("segment_id", "")))
    except (TypeError, ValueError, AttributeError):
        return False
    integer_fields = {
        "project_start_frame": 0,
        "frame_count": 0,
        "sample_rate": 1,
        "channels": 1,
        "size_bytes": 0,
    }
    for field_name, minimum in integer_fields.items():
        field_value = value.get(field_name)
        if (
            isinstance(field_value, bool)
            or not isinstance(field_value, int)
            or field_value < minimum
        ):
            return False
    if not str(value.get("sample_format") or "").strip():
        return False
    if re.fullmatch(r"[0-9a-f]{64}", str(value.get("sha256") or "")) is None:
        return False
    if str(value.get("media_status") or "available") not in {
        "available",
        "recovered",
        "partial",
        "missing",
        "damaged",
        "transferring",
        "transfer_failed",
    }:
        return False
    return True


def _take_media_entry_state(take_dir: Path, relative: str) -> str:
    """Classify a manifest media path without following any take-local symlink."""

    current = take_dir
    parts = Path(relative).parts
    try:
        for index, part in enumerate(parts):
            current = current / part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode):
                return "unsafe"
            if index + 1 < len(parts):
                if not stat.S_ISDIR(info.st_mode):
                    return "unsafe"
            elif not stat.S_ISREG(info.st_mode):
                return "unsafe"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unsafe"
    return "regular"


def _safe_finite_float(value: object, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _manifest_creator_profile_key(
    manifest: Mapping[str, object],
) -> tuple[str, str]:
    """Return historical creator truth plus a safe reconciliation finding.

    Schema-v1 takes and early schema-v2 takes have no creator-profile field;
    they migrate to Music. An explicitly unsupported value must not silently
    acquire Music terminology, so it yields an empty key for generic review
    presentation and a validation error that keeps export fail-closed.
    """

    if "session" not in manifest or manifest.get("session") in (None, ""):
        return "music", ""
    session = manifest.get("session")
    if not isinstance(session, Mapping):
        return "", _UNSUPPORTED_CREATOR_PROFILE_ERROR
    if "creator_profile_key" not in session:
        return "music", ""
    canonical = canonical_creator_profile_key(session.get("creator_profile_key"))
    if canonical is None:
        return "", _UNSUPPORTED_CREATOR_PROFILE_ERROR
    return canonical, ""


def load_take(take_dir: Path) -> Optional[TakeInfo]:
    """Build a TakeInfo from a single take folder.

    A completed manifest is the take's expected-media inventory, not merely a
    source of labels for whichever files still happen to exist.  Preserve
    declared missing tracks in the returned model and downgrade stale
    ``complete`` state so review/export can never hide the loss. A strictly
    valid schema-v2 recovery project may also contain zero tracks when only an
    interrupted-session evidence journal survived; ordinary empty folders and
    empty legacy manifests remain invisible.
    """
    take_dir = Path(take_dir)
    if not take_dir.is_dir():
        return None

    manifest_path = take_dir / "webjam-take.json"
    manifest: dict = {}
    try:
        loaded_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded_manifest, dict):
            manifest = loaded_manifest
    except (OSError, ValueError):
        manifest_path = None

    manifest_tracks: dict[str, dict] = {}
    schema_v2 = manifest.get("schema_version") == 2
    top_level_audio_entries = sorted(
        p for p in take_dir.iterdir() if p.suffix.lower() in _AUDIO_EXTS
    )
    audio_files = (
        [
            item
            for item in top_level_audio_entries
            if _take_media_entry_state(take_dir, item.name) == "regular"
        ]
        if schema_v2
        else [item for item in top_level_audio_entries if item.is_file()]
    )
    declared_filenames: list[str] = []
    declared_segment_paths: set[str] = set()
    invalid_schema_v2_tracks: set[str] = set()
    reconciliation_errors: list[str] = []
    creator_profile_key, creator_profile_error = _manifest_creator_profile_key(
        manifest
    )
    if creator_profile_error:
        reconciliation_errors.append(creator_profile_error)
    if (take_dir / _RECORDING_STAGING_NAME).exists():
        reconciliation_errors.append(_INTERRUPTED_PUBLICATION_ERROR)
    raw_tracks = manifest.get("tracks", [])
    if isinstance(raw_tracks, list):
        for item in raw_tracks:
            if not isinstance(item, dict) or not isinstance(item.get("filename"), str):
                continue
            filename = _safe_manifest_audio_path(item["filename"])
            # Manifests describe media inside the take directory.  Never turn
            # an untrusted/hand-edited path into a probe outside that boundary.
            if (
                not filename
            ):
                reconciliation_errors.append(
                    "The take manifest contains an invalid audio filename."
                )
                continue
            if filename in manifest_tracks:
                reconciliation_errors.append(
                    f"The take manifest lists {filename} more than once."
                )
                continue
            manifest_tracks[filename] = item
            declared_filenames.append(filename)
            if schema_v2:
                raw_segments = item.get("segments")
                inventory_valid = bool(
                    isinstance(raw_segments, list) and raw_segments
                )
                if isinstance(raw_segments, list):
                    for raw_segment in raw_segments:
                        if not _schema_v2_segment_shape_valid(raw_segment):
                            inventory_valid = False
                            continue
                        relative = _safe_manifest_audio_path(raw_segment.get("path"))
                        assert relative is not None
                        declared_segment_paths.add(relative)
                if not inventory_valid:
                    invalid_schema_v2_tracks.add(filename)
                    reconciliation_errors.append(
                        _SCHEMA_V2_INVALID_MEDIA_INVENTORY
                    )

    if schema_v2 and any(
        item.name not in declared_segment_paths
        for item in top_level_audio_entries
    ):
        reconciliation_errors.append(_SCHEMA_V2_UNLISTED_MEDIA)

    evidence_only = False
    if not top_level_audio_entries and not declared_filenames:
        try:
            from core.take_project import RecoveryStatus, TakeProject

            empty_project = TakeProject.from_dict(manifest)
        except (TypeError, ValueError):
            return None
        evidence_only = (
            not empty_project.tracks
            and empty_project.session_evidence.recovery_status
            is RecoveryStatus.NEEDS_ATTENTION
        )
        if not evidence_only:
            return None

    # Offsets: prefer a .lof if present, then manifest overrides for local stems.
    offsets: dict[str, float] = {}
    lofs = list(take_dir.glob("*.lof"))
    if lofs:
        offsets = parse_lof_offsets(lofs[0])

    reaper = next(iter(take_dir.glob("*.rpp")), None)

    tracks: List[TrackInfo] = []
    # Manifest order is stable project order.  Legacy/unlisted files follow in
    # their prior deterministic filename order.
    candidates: list[tuple[Path, dict]] = [
        (take_dir / filename, manifest_tracks[filename])
        for filename in declared_filenames
    ]
    if not schema_v2:
        candidates.extend(
            (audio, {}) for audio in audio_files if audio.name not in manifest_tracks
        )
    project_rate_raw = manifest.get("project_sample_rate", 0)
    project_rate = (
        int(project_rate_raw)
        if isinstance(project_rate_raw, (int, float))
        and not isinstance(project_rate_raw, bool)
        and math.isfinite(float(project_rate_raw))
        and int(project_rate_raw) > 0
        else 0
    )
    for audio, evidence in candidates:
        manifest_filename = _safe_manifest_audio_path(evidence.get("filename"))
        inventory_invalid = bool(
            schema_v2 and manifest_filename in invalid_schema_v2_tracks
        )
        media_entry_state = (
            _take_media_entry_state(take_dir, manifest_filename)
            if schema_v2 and manifest_filename is not None
            else "regular" if audio.is_file() else "missing"
        )
        available = media_entry_state == "regular"
        if available and not schema_v2:
            duration, rate = _probe_audio(audio)
        else:
            # Final manifests retain the last verified format/duration.  Keep
            # that non-audio evidence so losing the longest file cannot also
            # collapse or shift the take timeline in Studio.
            manifest_duration = evidence.get("duration_s")
            duration = (
                float(manifest_duration)
                if isinstance(manifest_duration, (int, float))
                and math.isfinite(float(manifest_duration))
                and float(manifest_duration) >= 0.0
                else 0.0
            )
            manifest_rate = evidence.get("sample_rate")
            rate = (
                int(manifest_rate)
                if isinstance(manifest_rate, (int, float))
                and math.isfinite(float(manifest_rate))
                and float(manifest_rate) > 0.0
                else 0
            )
        alignment = evidence.get("alignment", {})
        if not isinstance(alignment, dict):
            alignment = {}
        alignment_drift = _safe_finite_float(alignment.get("drift_ppm", 0.0))
        if 1.0 + alignment_drift / 1_000_000.0 <= 0.0:
            alignment_drift = 0.0
        manifest_offset = alignment.get(
            "effective_offset_s", evidence.get("offset_s")
        )
        offset = offsets.get(audio.name, 0.0)
        if isinstance(manifest_offset, (int, float)):
            offset = float(manifest_offset)
        declared_status = str(evidence.get("media_status") or "available")
        allowed_media_statuses = {
            "available",
            "recovered",
            "partial",
            "missing",
            "damaged",
            "transferring",
            "transfer_failed",
        }
        media_status = (
            "damaged"
            if inventory_invalid
            else "missing"
            if not available and not schema_v2
            else declared_status
            if declared_status in allowed_media_statuses
            else "damaged" if schema_v2 else "available"
        )
        native_recorder_media = (
            parse_jamulus_recording_filename(audio.name) is not None
        )
        name = str(
            evidence.get("name")
            or (
                "Unverified Jamulus source"
                if native_recorder_media
                else _prettify(audio.stem)
            )
        )
        segment_infos: list[TrackSegmentInfo] = []
        raw_segments = evidence.get("segments", [])
        if isinstance(raw_segments, list) and raw_segments:
            for raw_segment in raw_segments:
                if not isinstance(raw_segment, dict):
                    continue
                if schema_v2 and not _schema_v2_segment_shape_valid(raw_segment):
                    continue
                relative = _safe_manifest_audio_path(raw_segment.get("path"))
                if relative is None:
                    reconciliation_errors.append(
                        f"{name} contains an invalid segment path."
                    )
                    continue
                segment_path = take_dir / relative
                segment_status = str(
                    raw_segment.get("media_status") or "available"
                )
                if segment_status not in {
                    "available",
                    "recovered",
                    "partial",
                    "missing",
                    "damaged",
                    "transferring",
                    "transfer_failed",
                }:
                    segment_status = "damaged"
                try:
                    segment_rate = int(raw_segment.get("sample_rate", 0))
                    frame_count = int(raw_segment.get("frame_count", 0))
                    channels = int(raw_segment.get("channels", 1))
                    start_frame = int(raw_segment.get("project_start_frame", 0))
                except (TypeError, ValueError):
                    segment_rate = frame_count = start_frame = 0
                    channels = 1
                if (
                    segment_rate <= 0
                    or frame_count < 0
                    or channels <= 0
                    or start_frame < 0
                ):
                    segment_status = "damaged"
                    reconciliation_errors.append(
                        f"{name} contains invalid segment audio facts."
                    )
                segment_entry_state = (
                    _take_media_entry_state(take_dir, relative)
                    if schema_v2
                    else "regular" if segment_path.is_file() else "missing"
                )
                if segment_entry_state == "missing":
                    segment_status = "missing"
                    reconciliation_errors.append(
                        f"{name} is missing segment {relative}."
                    )
                elif segment_entry_state != "regular":
                    segment_status = "damaged"
                    reconciliation_errors.append(_SCHEMA_V2_UNSAFE_MEDIA)
                else:
                    changed = False
                    if schema_v2:
                        observed = _audio_file_evidence(
                            segment_path, inspect_signal=False
                        )
                        declared_hash = str(raw_segment.get("sha256") or "")
                        declared_format = str(
                            raw_segment.get("sample_format") or ""
                        ).strip().upper()
                        declared_size = raw_segment.get("size_bytes")
                        valid_size = (
                            isinstance(declared_size, int)
                            and not isinstance(declared_size, bool)
                            and declared_size >= 0
                        )
                        valid_hash = bool(
                            re.fullmatch(r"[0-9a-f]{64}", declared_hash)
                        )
                        changed = (
                            int(observed["sample_rate"] or 0) != segment_rate
                            or int(observed["frame_count"] or 0) != frame_count
                            or int(observed["channels"] or 0) != channels
                            or not declared_format
                            or str(observed["sample_format"] or "").upper()
                            != declared_format
                            or not valid_size
                            or int(observed["size_bytes"] or 0) != declared_size
                            or not valid_hash
                            or str(observed["sha256"] or "") != declared_hash
                        )
                    else:
                        observed_duration, observed_rate = _probe_audio(segment_path)
                        observed_frames = int(
                            round(observed_duration * observed_rate)
                        )
                        changed = (
                            observed_rate != segment_rate
                            or abs(observed_frames - frame_count) > 1
                        )
                    if changed:
                        segment_status = "damaged"
                        reconciliation_errors.append(
                            "A recorded segment changed after validation."
                        )
                gaps: list[tuple[int, int, tuple[int, ...], str]] = []
                raw_gaps = raw_segment.get("gaps", [])
                if isinstance(raw_gaps, list):
                    for gap in raw_gaps:
                        if not isinstance(gap, dict):
                            continue
                        try:
                            gap_start = int(gap.get("start_frame", -1))
                            gap_count = int(gap.get("frame_count", 0))
                            gap_channels = tuple(
                                int(item) for item in gap.get("channels", [])
                            )
                        except (TypeError, ValueError):
                            continue
                        if gap_start >= 0 and gap_count > 0:
                            gaps.append(
                                (
                                    gap_start,
                                    gap_count,
                                    gap_channels,
                                    str(gap.get("reason") or "disclosed_gap")[:120],
                                )
                            )
                segment_infos.append(
                    TrackSegmentInfo(
                        path=segment_path,
                        project_start_frame=start_frame,
                        frame_count=max(0, frame_count),
                        samplerate=max(0, segment_rate),
                        channels=max(1, channels),
                        media_status=segment_status,
                        segment_id=str(raw_segment.get("segment_id") or ""),
                        sha256=str(raw_segment.get("sha256") or ""),
                        gaps=tuple(gaps),
                    )
                )
            if inventory_invalid:
                media_status = "damaged"
            if segment_infos:
                audio = segment_infos[0].path
                rate = segment_infos[0].samplerate
                drift_scale = 1.0 + alignment_drift / 1_000_000.0
                timeline_rate = project_rate or rate
                duration = max(
                    (
                        segment.project_start_frame / timeline_rate
                        + segment.duration_s * drift_scale
                        for segment in segment_infos
                    ),
                    default=0.0,
                )
                blocked_statuses = {
                    segment.media_status
                    for segment in segment_infos
                    if segment.media_status not in {"available", "recovered"}
                }
                if blocked_statuses:
                    media_status = (
                        "missing" if "missing" in blocked_statuses else sorted(blocked_statuses)[0]
                    )
        if schema_v2 and not segment_infos:
            media_status = "damaged"
        if not schema_v2 and not segment_infos and available and rate > 0:
            try:
                import soundfile as sf  # type: ignore

                info = sf.info(str(audio))
                channels = int(info.channels)
                frames = int(info.frames)
            except Exception:  # noqa: BLE001
                channels = 1
                frames = int(round(duration * rate))
            segment_infos.append(
                TrackSegmentInfo(
                    path=audio,
                    project_start_frame=0,
                    frame_count=frames,
                    samplerate=rate,
                    channels=channels,
                    media_status=media_status,
                )
            )
        tracks.append(TrackInfo(
            path=audio,
            name=name,
            # Signed: local stems normally start before the server take, so a
            # negative offset here is valid alignment, not an error.
            offset_s=offset,
            duration_s=duration,
            samplerate=rate,
            source=str(evidence.get("source") or "jamulus_server"),
            media_status=media_status,
            track_id=str(evidence.get("track_id") or ""),
            source_id=str(evidence.get("source_id") or ""),
            participant_id=str(evidence.get("participant_id") or ""),
            instrument=str(evidence.get("instrument") or ""),
            quality=str(evidence.get("quality") or "unverified"),
            segments=tuple(segment_infos),
            drift_ppm=alignment_drift,
            alignment_confidence=max(
                0.0,
                min(1.0, _safe_finite_float(alignment.get("confidence", 0.0))),
            ),
            alignment_method=str(alignment.get("method") or "unverified"),
        ))
        if media_status == "missing":
            reconciliation_errors.append(
                f"{name} is missing from this take ({audio.name})."
            )

    def _string_items(key: str) -> tuple[str, ...]:
        value = manifest.get(key)
        if not isinstance(value, list):
            return ()
        return tuple(item for item in value if isinstance(item, str))

    manifest_errors = list(_string_items("errors"))
    if evidence_only and EVIDENCE_ONLY_EXPORT_BLOCK_REASON not in manifest_errors:
        manifest_errors.append(EVIDENCE_ONLY_EXPORT_BLOCK_REASON)
    for error in reconciliation_errors:
        if error not in manifest_errors:
            manifest_errors.append(error)
    validation_status = str(manifest.get("status") or "unchecked")
    if evidence_only or reconciliation_errors:
        validation_status = "needs_attention"

    return TakeInfo(
        path=take_dir,
        name=take_dir.name,
        tracks=tracks,
        reaper_project=reaper,
        validation_status=validation_status,
        manifest_path=manifest_path,
        manifest_errors=tuple(manifest_errors),
        manifest_warnings=_string_items("warnings"),
        session_title=str(manifest.get("session_title") or "").strip(),
        session_id=str(manifest.get("session_id") or ""),
        take_id=str(manifest.get("take_id") or ""),
        project_samplerate=project_rate,
        manifest_schema_version=(
            int(manifest.get("schema_version"))
            if isinstance(manifest.get("schema_version"), int)
            and not isinstance(manifest.get("schema_version"), bool)
            else 0
        ),
        review_only=evidence_only,
        export_block_reason=(
            EVIDENCE_ONLY_EXPORT_BLOCK_REASON if evidence_only else ""
        ),
        creator_profile_key=creator_profile_key,
    )


def discover_takes(root: str | Path) -> List[TakeInfo]:
    """Scan ``root`` for take folders, newest first.

    A take folder is an immediate subdirectory containing audio files or a
    strictly valid evidence-only schema-v2 recovery manifest. If ``root``
    itself is a take it is included too. Never raises — an unreadable root
    yields an empty list.
    """
    root = Path(root).expanduser()
    if not root.is_dir():
        return []

    takes: List[TakeInfo] = []
    try:
        # root-as-single-take
        direct = load_take(root)
        if direct is not None:
            takes.append(direct)
        for child in root.iterdir():
            if _is_visible_take_directory(child):
                take = load_take(child)
                if take is not None:
                    takes.append(take)
    except OSError:
        _logger.warning("The takes library could not be scanned.")
        return takes

    def _mtime(take: TakeInfo) -> float:
        # exists()+stat() would TOCTOU-race a deleted folder; stat() alone,
        # guarded, keeps the documented "never raises" contract.
        try:
            return take.path.stat().st_mtime
        except OSError:
            return 0.0

    takes.sort(key=_mtime, reverse=True)
    return takes
