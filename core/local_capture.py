"""Failure-safe supplemental capture for isolated host inputs.

Jamulus remains the live-audio authority. This service records an explicit map
of up to 32 logical local tracks / 32 unique device channels as isolated mono
or stereo stems without changing the network path. The historical default
remains two mono inputs only when no map was configured.

Real-time layout: the sounddevice callback copies each block into a fixed,
preallocated SPSC ring; a dedicated writer thread does every disk write,
status aggregation, and gap materialization. The callback never allocates a
block, logs, waits, performs I/O, or acquires a lock. This is a separate
PortAudio capture path. WebJam records the selected device metadata, but
cannot prove that Jamulus is using the same physical input or that both
applications share an identical route.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.project_audio import CaptureBlockRing
from core.logical_sources import canonical_logical_source_id, derive_logical_source_id

LOGGER = logging.getLogger("webjam.local_capture")

_MAX_CAPTURE_TRACKS = 32
_MAX_CAPTURE_CHANNELS = 32
_CAPTURE_TRACK_MAP_FINGERPRINT_SCHEMA = 1
_TRACK_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,63}$")


class LocalCaptureError(RuntimeError):
    """Raised when supplemental capture cannot start or finish safely."""


@dataclass(frozen=True, slots=True, eq=False)
class LocalCaptureTrack:
    """One stable logical WAV mapped to one mono or adjacent stereo input.

    ``stem`` is the path-safe identity used for the WAV filename.
    ``source_channels`` are zero-based PortAudio device-channel indices. A
    stereo track always keeps its adjacent pair together in one two-channel
    file; it is never expanded into independent left/right stems.

    Iteration exposes the historical ``(stem, first_channel)`` shape so older
    mono-only callers that unpack resolver results continue to work. New code
    must use :attr:`source_channels` to retain stereo truth.
    """

    stem: str
    source_channels: tuple[int, ...]
    # Empty only for legacy callers/recovery records. New session plans bind a
    # stable, session-scoped logical source UUID before capture begins.
    logical_source_id: str = ""
    # Optional configured-map slot retained before a guest can bind its
    # session-scoped ID. This is deliberately not a filename/device fact. It
    # prevents an earlier opted-out row from renumbering later sources across
    # repeat takes. Legacy tuple callers leave it unset and retain list order.
    logical_source_ordinal: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stem, str):
            raise LocalCaptureError("A local capture track name must be text.")
        stem = self.stem.strip()
        if stem != self.stem or not _TRACK_STEM_RE.fullmatch(stem):
            raise LocalCaptureError(
                "A local capture track name is not filesystem-safe."
            )
        try:
            channels = tuple(self.source_channels)
        except TypeError as exc:
            raise LocalCaptureError(
                "A local capture track channel map must be a sequence."
            ) from exc
        if len(channels) not in (1, 2):
            raise LocalCaptureError("A local capture track must be mono or stereo.")
        if any(
            isinstance(channel, bool)
            or not isinstance(channel, int)
            or not 0 <= channel < _MAX_CAPTURE_CHANNELS
            for channel in channels
        ):
            raise LocalCaptureError("A local capture channel index is out of range.")
        if len(channels) == 2 and channels[1] != channels[0] + 1:
            raise LocalCaptureError(
                "A stereo local capture track requires adjacent input channels."
            )
        object.__setattr__(self, "stem", stem)
        object.__setattr__(self, "source_channels", channels)
        try:
            logical_source_id = canonical_logical_source_id(
                self.logical_source_id, optional=True
            )
        except ValueError as exc:
            raise LocalCaptureError(str(exc)) from exc
        object.__setattr__(self, "logical_source_id", logical_source_id)
        ordinal = self.logical_source_ordinal
        if ordinal is not None and (
            isinstance(ordinal, bool)
            or not isinstance(ordinal, int)
            or not 0 <= ordinal < _MAX_CAPTURE_TRACKS
        ):
            raise LocalCaptureError(
                "A local capture logical source ordinal is out of range."
            )

    @property
    def channel_count(self) -> int:
        return len(self.source_channels)

    @property
    def first_source_channel(self) -> int:
        return self.source_channels[0]

    def __iter__(self):
        """Yield the legacy mono tuple projection for unpacking compatibility."""

        yield self.stem
        yield self.first_source_channel

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LocalCaptureTrack):
            return (
                self.stem == other.stem
                and self.source_channels == other.source_channels
                and self.logical_source_id == other.logical_source_id
                and self.logical_source_ordinal == other.logical_source_ordinal
            )
        if isinstance(other, tuple) and len(other) == 2:
            # Only an actually legacy track can equal the historical tuple
            # projection. Once a stable source slot or ID exists, dropping it
            # from equality would make distinct logical sources compare equal.
            if self.logical_source_id or self.logical_source_ordinal is not None:
                return False
            other_stem, other_channels = other
            if isinstance(other_channels, int) and not isinstance(other_channels, bool):
                return (
                    self.channel_count == 1
                    and self.stem == other_stem
                    and self.first_source_channel == other_channels
                )
            if isinstance(other_channels, (list, tuple)):
                return self.stem == other_stem and self.source_channels == tuple(
                    other_channels
                )
        return False

    def __hash__(self) -> int:
        legacy_channels: int | tuple[int, ...] = (
            self.first_source_channel
            if self.channel_count == 1
            else self.source_channels
        )
        if self.logical_source_id or self.logical_source_ordinal is not None:
            return hash(
                (
                    self.stem,
                    self.source_channels,
                    self.logical_source_id,
                    self.logical_source_ordinal,
                )
            )
        return hash((self.stem, legacy_channels))


# The default remains the historical fixed pair. The typed representation is
# internal-compatible with legacy tuple inputs accepted by the constructor.
_DEFAULT_CAPTURE_TRACKS: tuple[LocalCaptureTrack, ...] = (
    LocalCaptureTrack("host-guitar", (0,)),
    LocalCaptureTrack("host-vocal", (1,)),
)


def _validated_capture_tracks(
    tracks: object,
) -> tuple[LocalCaptureTrack, ...]:
    if tracks is None:
        return _DEFAULT_CAPTURE_TRACKS
    try:
        entries = tuple(tracks)
    except TypeError as exc:
        raise LocalCaptureError("Local capture tracks must be a sequence.") from exc
    if not 1 <= len(entries) <= _MAX_CAPTURE_TRACKS:
        raise LocalCaptureError("Local capture supports between 1 and 32 input tracks.")
    cleaned: list[LocalCaptureTrack] = []
    stems: set[str] = set()
    channels: set[int] = set()
    for entry in entries:
        if isinstance(entry, LocalCaptureTrack):
            track = entry
        else:
            try:
                stem, channel_spec = entry
            except (TypeError, ValueError) as exc:
                raise LocalCaptureError(
                    "A local capture track specification is invalid."
                ) from exc
            raw_stem = str(stem or "")
            if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw_stem):
                raise LocalCaptureError(
                    "A local capture track name is not filesystem-safe."
                )
            source_channels = (
                tuple(channel_spec)
                if isinstance(channel_spec, (list, tuple))
                else (channel_spec,)
            )
            track = LocalCaptureTrack(raw_stem.strip(), source_channels)
        stem_key = track.stem.casefold()
        if stem_key in stems or channels.intersection(track.source_channels):
            raise LocalCaptureError(
                "Local capture tracks must use unique names and channels."
            )
        stems.add(stem_key)
        channels.update(track.source_channels)
        if len(channels) > _MAX_CAPTURE_CHANNELS:
            raise LocalCaptureError(
                "Local capture supports at most 32 unique input channels."
            )
        cleaned.append(track)
    logical_ids = [track.logical_source_id for track in cleaned]
    if any(logical_ids) and (
        not all(logical_ids) or len(set(logical_ids)) != len(logical_ids)
    ):
        raise LocalCaptureError(
            "Local capture tracks must use one complete, unique logical-source map."
        )
    return tuple(cleaned)


def local_capture_track_map_fingerprint(tracks: object) -> str:
    """Bind an exact logical input topology without exposing track names.

    The ordered map includes each logical ordinal, mono/stereo width, and
    zero-based source-channel indices. Stable stems are still validated so the
    same value can safely be passed to :class:`LocalInputCapture`, but neither
    stems nor any path/device fact enter the digest.
    """

    validated = _validated_capture_tracks(tracks)
    exact_logical_sources = bool(validated) and all(
        track.logical_source_id for track in validated
    )
    payload = {
        "schema": (
            _CAPTURE_TRACK_MAP_FINGERPRINT_SCHEMA + 1
            if exact_logical_sources
            else _CAPTURE_TRACK_MAP_FINGERPRINT_SCHEMA
        ),
        "tracks": [
            {
                "ordinal": ordinal,
                "channel_count": track.channel_count,
                "source_channels": list(track.source_channels),
                **(
                    {"logical_source_id": track.logical_source_id}
                    if exact_logical_sources
                    else {}
                ),
            }
            for ordinal, track in enumerate(validated)
        ],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def bind_local_capture_logical_sources(
    tracks: object,
    *,
    session_id: str,
    participant_id: str,
) -> tuple[LocalCaptureTrack, ...]:
    """Bind one complete capture map to stable repeated-take source IDs.

    The ordinal is part of the musician's immutable pre-take input map. Names,
    device paths, and take IDs never enter the identity. Existing non-empty IDs
    must match the deterministic contract instead of being silently replaced.
    """

    validated = _validated_capture_tracks(tracks)
    bound: list[LocalCaptureTrack] = []
    ordinals: set[int] = set()
    for active_ordinal, track in enumerate(validated):
        ordinal = (
            active_ordinal
            if track.logical_source_ordinal is None
            else track.logical_source_ordinal
        )
        if ordinal in ordinals:
            raise LocalCaptureError(
                "Local capture logical source ordinals must be unique."
            )
        ordinals.add(ordinal)
        expected = derive_logical_source_id(
            session_id,
            participant_id,
            "local_original",
            ordinal,
        )
        if track.logical_source_id and track.logical_source_id != expected:
            raise LocalCaptureError(
                "A local capture track contradicted its planned logical source ID."
            )
        bound.append(
            LocalCaptureTrack(
                track.stem,
                track.source_channels,
                logical_source_id=expected,
                logical_source_ordinal=ordinal,
            )
        )
    return tuple(bound)


# The default ring accepts callback blocks through 8,192 frames without asking
# PortAudio to use a fixed block size. At two float32 channels, 512 slots stay
# within a 32 MiB preallocated budget. Explicit larger block sizes reduce the
# slot count to preserve the same hard memory bound.
_CAPTURE_RING_MAX_BLOCKS = 512
_CAPTURE_RING_DEFAULT_BLOCK_FRAMES = 8_192
_CAPTURE_RING_MAX_BYTES = 32 * 1024 * 1024
_CAPTURE_RING_GAP_CAPACITY = 1_024
_WRITER_POLL_S = 0.002
# Manifests embed capture errors verbatim; keep the list bounded.
_ERROR_CAP = 20
# Finalization must never take ownership of libsndfile handles away from a
# writer thread that may still be inside ``write``.  Tests patch this short;
# production allows slow storage a generous drain window.
_WRITER_JOIN_TIMEOUT_S = 10.0
_SILENCE_CHUNK_FRAMES = 48_000
_DEFERRED_RECOVERY_GRACE_S = 0.25
_RECOVERY_METADATA = "webjam-local-capture.json"
_RECOVERY_REPORT = "RECOVERY.json"
_LEGACY_RECOVERY_SCHEMA = 1
_RECOVERY_SCHEMA = 2
# A real local take must survive more than an in-memory capture ring. One
# second at the fixed capture rate is frequent enough to make a sudden process
# exit recoverable without putting any I/O on PortAudio's callback thread.
_DURABLE_CHECKPOINT_FRAMES = 48_000
_RECOVERY_GAP_CAP = 128
# Wall-clock is not sample-accurate, so tolerate a bounded scheduling/device
# tail. A larger deficit means callbacks silently ceased while Record stayed
# active; preserve the timeline with disclosed silence and fail completion.
_DEVICE_STALL_TOLERANCE_S = 0.25


@dataclass(frozen=True)
class LocalCaptureGap:
    """A half-open interval where source audio was replaced by silence.

    ``channels`` are zero-based logical-track ordinals in
    :attr:`LocalCaptureResult.tracks`, not physical source-channel indices.
    They remain stable even when final attachment fails and
    ``LocalCaptureResult.files`` is empty. The interval is expressed on the
    capture's absolute 48 kHz frame timeline, so callers can disclose and
    align discontinuities precisely.
    """

    start_frame: int
    frame_count: int
    channels: tuple[int, ...]
    reason: str

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count


@dataclass(frozen=True)
class LocalCaptureResult:
    files: tuple[Path, ...]
    started_utc: str
    started_monotonic: float
    duration_s: float
    errors: tuple[str, ...] = ()
    gaps: tuple[LocalCaptureGap, ...] = ()
    total_frames: int = 0
    recovery_dir: Path | None = None
    capture_device: object | None = None
    durable_frames: int = 0
    tracks: tuple[LocalCaptureTrack, ...] = ()

    @property
    def gap_count(self) -> int:
        return len(self.gaps)


@dataclass(frozen=True, slots=True)
class LocalCapturePreflight:
    """Path/name-free capability result for one exact typed capture map."""

    ready: bool
    errors: tuple[str, ...]
    track_count: int
    required_input_channels: int
    channel_counts: tuple[int, ...]
    samplerate: int


def check_local_capture_preflight(
    *,
    tracks: object,
    device: int = -1,
    samplerate: int = 48_000,
    blocksize: int = 0,
    sounddevice_module: object | None = None,
) -> LocalCapturePreflight:
    """Validate capture capability without opening a stream or creating files.

    Native/library exception text may include a device name or path, so the
    returned errors are fixed bounded codes suitable for readiness UI/logs.
    """

    errors: list[str] = []
    try:
        typed_tracks = _validated_capture_tracks(tracks)
    except (LocalCaptureError, TypeError, ValueError):
        typed_tracks = ()
        errors.append("invalid_track_map")
    try:
        rate = int(samplerate)
        block = int(blocksize)
        selected_device = int(device)
    except (TypeError, ValueError):
        rate = 0
        block = -1
        selected_device = -1
        errors.append("invalid_capture_settings")
    if rate != 48_000:
        errors.append("unsupported_sample_rate")
    if block < 0:
        errors.append("invalid_block_size")
    required_channels = (
        max(channel for track in typed_tracks for channel in track.source_channels) + 1
        if typed_tracks
        else 0
    )
    if not errors:
        try:
            sd = sounddevice_module
            if sd is None:
                import sounddevice as sd  # type: ignore

            native_device = None if selected_device < 0 else selected_device
            details = sd.query_devices(native_device, "input")
            if not isinstance(details, dict):
                raise ValueError("unavailable")
            maximum = int(details.get("max_input_channels", 0))
            if maximum < required_channels:
                errors.append("insufficient_input_channels")
            else:
                sd.check_input_settings(
                    device=native_device,
                    channels=required_channels,
                    samplerate=rate,
                    dtype="float32",
                )
        except Exception:  # noqa: BLE001 - deliberately discard private native text
            if "insufficient_input_channels" not in errors:
                errors.append("input_device_or_format_unavailable")
    return LocalCapturePreflight(
        ready=not errors,
        errors=tuple(dict.fromkeys(errors)),
        track_count=len(typed_tracks),
        required_input_channels=required_channels,
        channel_counts=tuple(track.channel_count for track in typed_tracks),
        samplerate=rate,
    )


@dataclass(frozen=True)
class RecoveredLocalCapture:
    """A crashed/abandoned capture promoted to visible user-owned media."""

    source_dir: Path
    recovery_dir: Path
    files: tuple[Path, ...]
    errors: tuple[str, ...] = ()
    take_id: str = ""
    session_id: str = ""
    started_utc: str = ""
    total_frames: int = 0
    durable_frames: int = 0
    sample_rate: int = 0
    gaps: tuple[LocalCaptureGap, ...] = ()
    capture_device: object | None = None
    tracks: tuple[LocalCaptureTrack, ...] = ()


class LocalInputCapture:
    """Record mapped logical inputs to atomic mono or stereo WAV files.

    Defaults to the historical fixed pair (host-guitar on channel 0,
    host-vocal on channel 1). Callers may supply :class:`LocalCaptureTrack`
    values, while historical ``(stem, channel)`` tuples remain mono tracks.
    One input stream carries every mapped channel; the writer thread owns all
    file publication.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        device: int = -1,
        samplerate: int = 48000,
        blocksize: int = 0,
        take_id: str = "",
        session_id: str = "",
        tracks: object = None,
    ) -> None:
        self.root = Path(root).expanduser()
        self.device = None if device < 0 else device
        self.samplerate = int(samplerate)
        self.blocksize = max(0, int(blocksize))
        # These opaque IDs bind a recovered local capture to the matching
        # recording-evidence journal.  Invalid values are discarded rather
        # than copied into durable recovery metadata.
        self.take_id = _canonical_optional_uuid(take_id)
        self.session_id = _canonical_optional_uuid(session_id)
        self._stream = None
        self._writers: list[object] = []
        self._temp_dir: Path | None = None
        self._parts: list[Path] = []
        self._ring_capacity = _CAPTURE_RING_MAX_BLOCKS
        self._ring_block_frames = self.blocksize or _CAPTURE_RING_DEFAULT_BLOCK_FRAMES
        self._capture_ring: CaptureBlockRing | None = None
        self._writer_scratch: np.ndarray | None = None
        self._generation = 0
        self._active_generation = 0
        self._writer_thread: threading.Thread | None = None
        self._stop_requested = False
        self._dropped_blocks = 0
        self._callback_status_events = 0
        self._callback_overflow_events = 0
        self._callback_format_events = 0
        self._error_counts: dict[str, int] = {}
        self._gaps: list[LocalCaptureGap] = []
        self._diagnostics_lock = threading.Lock()
        self._next_input_frame = 0
        self._final_input_frame: int | None = None
        self._tracks = _validated_capture_tracks(tracks)
        self._track_channels = tuple(
            channel for track in self._tracks for channel in track.source_channels
        )
        scratch_offset = 0
        track_scratch_slices: list[tuple[int, int]] = []
        for track in self._tracks:
            next_offset = scratch_offset + track.channel_count
            track_scratch_slices.append((scratch_offset, next_offset))
            scratch_offset = next_offset
        self._track_scratch_slices = tuple(track_scratch_slices)
        self._required_input_channels = max(self._track_channels) + 1
        self._all_writer_channels = tuple(range(len(self._tracks)))
        self._writer_frames = [0] * len(self._tracks)
        self._writer_incomplete = False
        self._finalize_lock = threading.Lock()
        self._finalized = False
        self._started_monotonic = 0.0
        self._stopped_monotonic = 0.0
        self._started_utc = ""
        self._capture_device = None
        self._recovery_thread: threading.Thread | None = None
        self._durable_frames = 0
        self._durability_failed = False

    def start(self) -> None:
        if self.samplerate != 48000:
            raise LocalCaptureError("Isolated host capture requires 48 kHz audio.")
        try:
            import sounddevice as sd  # type: ignore
            import soundfile as sf  # type: ignore

            self.root.mkdir(parents=True, exist_ok=True)
            self._temp_dir = self.root / f".webjam-capture-{uuid.uuid4().hex}"
            self._temp_dir.mkdir(mode=0o700)
            self._parts = [
                self._temp_dir / f"{track.stem}.wav.part" for track in self._tracks
            ]
            self._writers = [
                sf.SoundFile(
                    str(path),
                    mode="w",
                    samplerate=self.samplerate,
                    channels=track.channel_count,
                    format="WAV",
                    subtype="PCM_24",
                )
                for path, track in zip(self._parts, self._tracks)
            ]

            sd.check_input_settings(
                device=self.device,
                channels=self._required_input_channels,
                samplerate=self.samplerate,
                dtype="float32",
            )
            ring_block_frames = int(self._ring_block_frames)
            bytes_per_slot = (
                ring_block_frames
                * len(self._track_channels)
                * np.dtype(np.float32).itemsize
            )
            memory_bounded_capacity = max(
                1,
                _CAPTURE_RING_MAX_BYTES // max(1, bytes_per_slot),
            )
            ring_capacity = min(
                max(1, int(self._ring_capacity)),
                memory_bounded_capacity,
            )
            ring = CaptureBlockRing(
                ring_capacity,
                ring_block_frames,
                input_channels=self._required_input_channels,
                channel_map=self._track_channels,
                gap_capacity=_CAPTURE_RING_GAP_CAPACITY,
            )
            self._capture_ring = ring
            self._writer_scratch = np.empty(
                (ring.block_frames, ring.channels),
                dtype=np.float32,
            )
            self._generation += 1
            generation = self._generation
            self._active_generation = generation

            def callback(indata, frames, _time_info, status) -> None:
                # Audio thread: scalar counters and one bounded copy into the
                # preallocated SPSC ring. It performs no block-sized allocation,
                # wait, lock, logging, or filesystem operation; the writer owns
                # all durable I/O and diagnostic formatting.
                if self._active_generation != generation:
                    return
                if status:
                    self._callback_status_events += 1
                    if getattr(status, "input_overflow", False):
                        self._callback_overflow_events += 1
                if (
                    isinstance(frames, bool)
                    or not isinstance(frames, int)
                    or frames <= 0
                ):
                    self._callback_format_events += 1
                    return
                frame_count = frames
                start_frame = self._next_input_frame
                # Advance the source timeline even when storage is full or a
                # malformed block is rejected. The writer then emits exact
                # silence rather than pulling later audio earlier in time.
                self._next_input_frame = start_frame + frame_count
                if (
                    not isinstance(indata, np.ndarray)
                    or indata.dtype != np.float32
                    or indata.ndim != 2
                    or indata.shape[0] != frame_count
                    or indata.shape[1] < self._required_input_channels
                    or frame_count > ring.block_frames
                ):
                    self._callback_format_events += 1
                    return
                if not ring.push_from(
                    indata,
                    start_frame=start_frame,
                    generation=generation,
                ):
                    self._dropped_blocks += 1

            self._capture_device = self._describe_capture_device(sd)
            self._started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._write_recovery_checkpoint()
            self._stream = sd.InputStream(
                device=self.device,
                channels=self._required_input_channels,
                samplerate=self.samplerate,
                blocksize=self.blocksize,
                dtype="float32",
                callback=callback,
            )
            self._writer_thread = threading.Thread(
                target=self._writer_loop,
                daemon=True,
                name="local-capture-writer",
            )
            self._writer_thread.start()
            self._started_monotonic = time.monotonic()
            self._stream.start()
        except Exception:  # noqa: BLE001 - native errors may contain device paths
            self.abort()
            raise LocalCaptureError(
                "Could not open the isolated host inputs at 48 kHz. Check "
                "the selected input and folder access, then try again."
            ) from None

    def _write_recovery_checkpoint(self) -> None:
        """Atomically record what media is safe to recover after interruption.

        This is called before the stream starts and again only from the writer
        thread after the WAV data has been flushed and fsynced.  The checkpoint
        never claims that frames beyond ``durable_frames`` survived a crash.
        """
        if self._temp_dir is None:
            return
        from core.file_io import atomic_write_text

        device_payload = _capture_device_payload(self._capture_device)
        gaps = self._snapshot_gaps()[-_RECOVERY_GAP_CAP:]
        payload = {
            "schema": _RECOVERY_SCHEMA,
            "pid": os.getpid(),
            "started_utc": self._started_utc,
            "sample_rate": self.samplerate,
            "channels": self._required_input_channels,
            "tracks": _capture_tracks_payload(self._tracks),
            "parts": [path.name for path in self._parts],
            "take_id": self.take_id,
            "session_id": self.session_id,
            "total_frames": max(0, int(self._next_input_frame)),
            "durable_frames": max(0, int(self._durable_frames)),
            "writer_frames": [max(0, int(value)) for value in self._writer_frames],
            "gaps": _capture_gaps_payload(gaps),
        }
        if device_payload is not None:
            payload["capture_device"] = device_payload
        atomic_write_text(
            self._temp_dir / _RECOVERY_METADATA,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )

    def _checkpoint_audio_durability(self, *, force: bool = False) -> bool:
        """Flush and fsync every stem before advancing recovery metadata.

        ``soundfile.flush`` commits libsndfile's buffered frames; an explicit
        file fsync then commits the resulting WAV bytes.  Both happen on the
        dedicated writer thread, never the audio callback.  A failure leaves
        the audio in place but records a bounded recovery-needed fact so the
        final manifest cannot pretend the local original was crash-durable.
        """
        durable_frames = min(self._writer_frames, default=0)
        if (
            not force
            and durable_frames - self._durable_frames < _DURABLE_CHECKPOINT_FRAMES
        ):
            return True
        try:
            for path, writer in zip(self._parts, self._writers):
                flush = getattr(writer, "flush", None)
                if not callable(flush):
                    raise LocalCaptureError("writer does not support flush")
                flush()
                _fsync_regular_file(path)
            self._durable_frames = max(0, int(durable_frames))
            self._write_recovery_checkpoint()
            return True
        except Exception:  # noqa: BLE001 - preserve media and surface safe truth
            self._durability_failed = True
            self._record_error(
                "Local capture could not save a durable audio checkpoint; "
                "this take needs recovery review."
            )
            return False

    def _describe_capture_device(self, sounddevice):
        """Snapshot the source configuration once, before recording starts."""
        from core.take_project import CaptureDevice

        index = -1 if self.device is None else int(self.device)
        name = "System default input"
        backend = "PortAudio"
        try:
            raw = sounddevice.query_devices(self.device, "input")
            if isinstance(raw, dict):
                name = str(raw.get("name") or name)
                hostapi = raw.get("hostapi")
                try:
                    api = sounddevice.query_hostapis(hostapi)
                    if isinstance(api, dict):
                        backend = str(api.get("name") or backend)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
        identity = f"portaudio:{backend}:{index}:{name}"[:256]
        return CaptureDevice(
            device_id=identity,
            display_name=name,
            backend=backend,
            sample_rate=self.samplerate,
            channel_indices=self._track_channels,
            channel_labels=tuple(
                f"Input {channel + 1}" for channel in self._track_channels
            ),
        )

    def _writer_loop(self) -> None:
        ring = self._capture_ring
        scratch = self._writer_scratch
        # The writer drains the generation it was prepared for even after the
        # control plane sets ``_active_generation`` to zero to fence callbacks.
        generation = self._generation
        if ring is None or scratch is None or generation <= 0:
            self._writer_incomplete = True
            self._record_error("Local capture buffer was not prepared.")
            return
        timeline_frame = 0
        while True:
            frame_count = ring.pop_into(
                scratch,
                generation=generation,
            )
            if frame_count <= 0:
                if self._stop_requested:
                    break
                time.sleep(_WRITER_POLL_S)
                continue

            start_frame = ring.last_popped_start_frame
            end_frame = start_frame + frame_count
            if start_frame > timeline_frame:
                self._record_gap(
                    timeline_frame,
                    start_frame - timeline_frame,
                    self._all_writer_channels,
                    "queue_overflow",
                )
                for track_index in range(len(self._writers)):
                    self._pad_writer_to(track_index, start_frame)
            elif start_frame < timeline_frame:
                self._record_error(
                    "Local capture received an out-of-order audio block."
                )

            for track_index, (first, stop) in enumerate(self._track_scratch_slices):
                samples = (
                    scratch[:frame_count, first]
                    if stop - first == 1
                    else scratch[:frame_count, first:stop]
                )
                self._write_track_block(track_index, start_frame, samples)
            timeline_frame = max(timeline_frame, end_frame)
            self._checkpoint_audio_durability()

        target = self._final_input_frame
        if target is None:
            target = self._next_input_frame
        if target > timeline_frame:
            disclosed = any(
                gap.reason == "device_stall"
                and gap.start_frame <= timeline_frame
                and gap.end_frame >= target
                and gap.channels == self._all_writer_channels
                for gap in self._snapshot_gaps()
            )
            if not disclosed:
                self._record_gap(
                    timeline_frame,
                    target - timeline_frame,
                    self._all_writer_channels,
                    "queue_overflow",
                )
        for track_index in range(len(self._writers)):
            if not self._pad_writer_to(track_index, target):
                self._writer_incomplete = True
            if self._writer_frames[track_index] != target:
                self._writer_incomplete = True
                self._record_error(
                    f"Local capture channel {track_index + 1} ended at frame "
                    f"{self._writer_frames[track_index]} instead of {target}."
                )
        self._checkpoint_audio_durability(force=True)

    def _writer_position(self, track_index: int, *, expected: int | None = None) -> int:
        """Refresh a writer's position when its implementation exposes it."""
        writer = self._writers[track_index]
        position = expected
        tell = getattr(writer, "tell", None)
        if callable(tell):
            try:
                position = int(tell())
            except Exception:  # noqa: BLE001
                pass
        if position is not None:
            self._writer_frames[track_index] = max(0, position)
        return self._writer_frames[track_index]

    def _pad_writer_to(self, track_index: int, target_frame: int) -> bool:
        """Write silence until one stem reaches ``target_frame``."""
        writer = self._writers[track_index]
        track = self._tracks[track_index]
        while self._writer_frames[track_index] < target_frame:
            frame_count = min(
                _SILENCE_CHUNK_FRAMES,
                target_frame - self._writer_frames[track_index],
            )
            expected = self._writer_frames[track_index] + frame_count
            try:
                shape = (
                    frame_count
                    if track.channel_count == 1
                    else (frame_count, track.channel_count)
                )
                writer.write(np.zeros(shape, dtype="float32"))
            except Exception:  # noqa: BLE001 - writer details may contain paths
                self._record_error(
                    f"Local capture silence write failed on channel {track_index + 1}."
                )
                self._writer_position(track_index)
                return False
            self._writer_position(track_index, expected=expected)
        return self._writer_frames[track_index] == target_frame

    def _write_track_block(
        self, track_index: int, start_frame: int, samples: np.ndarray
    ) -> None:
        """Place a source block on its absolute timeline for one stem."""
        end_frame = start_frame + len(samples)
        if not self._pad_writer_to(track_index, start_frame):
            self._record_gap(
                start_frame,
                end_frame - start_frame,
                (track_index,),
                "write_failure",
            )
            return

        position = self._writer_frames[track_index]
        if position >= end_frame:
            return
        offset = max(0, position - start_frame)
        expected = end_frame
        try:
            self._writers[track_index].write(samples[offset:])
        except Exception:  # noqa: BLE001 - writer details may contain paths
            self._record_error(
                f"Local capture write failed on channel {track_index + 1}."
            )
            position = self._writer_position(track_index)
            gap_start = min(end_frame, max(start_frame, position))
            if gap_start < end_frame:
                self._record_gap(
                    gap_start,
                    end_frame - gap_start,
                    (track_index,),
                    "write_failure",
                )
            if not self._pad_writer_to(track_index, end_frame):
                self._writer_incomplete = True
            return
        self._writer_position(track_index, expected=expected)

    def _record_gap(
        self,
        start_frame: int,
        frame_count: int,
        channels: tuple[int, ...],
        reason: str,
    ) -> None:
        if frame_count <= 0:
            return
        gap = LocalCaptureGap(start_frame, frame_count, channels, reason)
        with self._diagnostics_lock:
            if self._gaps:
                previous = self._gaps[-1]
                if (
                    previous.end_frame == gap.start_frame
                    and previous.channels == gap.channels
                    and previous.reason == gap.reason
                ):
                    self._gaps[-1] = LocalCaptureGap(
                        previous.start_frame,
                        previous.frame_count + gap.frame_count,
                        previous.channels,
                        previous.reason,
                    )
                    return
            self._gaps.append(gap)

    def _snapshot_gaps(self) -> tuple[LocalCaptureGap, ...]:
        with self._diagnostics_lock:
            return tuple(self._gaps)

    def _record_error(self, message: str) -> None:
        with self._diagnostics_lock:
            count = self._error_counts.get(message, 0)
            self._error_counts[message] = count + 1
        if count == 0:
            LOGGER.error("%s", message)

    def _drain_writer(self) -> bool:
        """Stop the stream and return whether the writer released ownership."""
        # Invalidate the callback generation before asking PortAudio to stop.
        # A delayed callback from this stream can no longer append to the ring
        # or advance the source timeline while finalization drains it.
        self._active_generation = 0
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:  # noqa: BLE001 - native errors may contain device paths
            self._record_error("Local capture did not close cleanly.")
        finally:
            self._stream = None
        self._reconcile_wall_clock_timeline()
        self._final_input_frame = self._next_input_frame
        self._stop_requested = True
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=_WRITER_JOIN_TIMEOUT_S)
            if self._writer_thread.is_alive():
                self._record_error(
                    "Local capture writer did not finish in time; open .part "
                    "files were retained and were not flushed, closed, or moved."
                )
                self._schedule_deferred_recovery()
                return False
            self._writer_thread = None
        return True

    def _reconcile_wall_clock_timeline(self) -> None:
        """Materialize a silent tail when the device stopped calling back.

        This runs only during finalization after callback generation has been
        fenced and the native stream is closed. It performs no real-time work.
        """

        if (
            self._started_monotonic <= 0.0
            or self._stopped_monotonic <= self._started_monotonic
        ):
            return
        elapsed = self._stopped_monotonic - self._started_monotonic
        wall_frames = max(0, int(round(elapsed * self.samplerate)))
        callback_frames = max(0, int(self._next_input_frame))
        block_tolerance = max(
            self._ring_block_frames * 2,
            int(round(_DEVICE_STALL_TOLERANCE_S * self.samplerate)),
        )
        if wall_frames - callback_frames <= block_tolerance:
            return
        missing_frames = wall_frames - callback_frames
        self._record_gap(
            callback_frames,
            missing_frames,
            self._all_writer_channels,
            "device_stall",
        )
        self._record_error(
            "The audio device stopped delivering input before recording stopped; "
            "the missing tail was preserved as silence."
        )
        self._next_input_frame = wall_frames

    def _collect_errors(self) -> list[str]:
        errors: list[str] = []
        if self._callback_overflow_events:
            count = self._callback_overflow_events
            suffix = f" (×{count})" if count > 1 else ""
            errors.append(f"Audio device reported: input overflow{suffix}")
        other_status_events = max(
            0,
            self._callback_status_events - self._callback_overflow_events,
        )
        if other_status_events:
            suffix = f" (×{other_status_events})" if other_status_events > 1 else ""
            errors.append(f"Audio device reported an input status{suffix}")
        if self._callback_format_events:
            count = self._callback_format_events
            suffix = f" (×{count})" if count > 1 else ""
            errors.append(
                "Audio input delivered a block outside the fixed capture "
                f"buffer{suffix}."
            )
        with self._diagnostics_lock:
            error_counts = tuple(self._error_counts.items())
        for message, count in error_counts:
            suffix = f" (×{count})" if count > 1 else ""
            errors.append(f"{message}{suffix}")
        if self._dropped_blocks:
            errors.append(
                f"Recording buffer overflowed; {self._dropped_blocks} audio "
                "blocks were dropped."
            )
        if len(errors) > _ERROR_CAP:
            suppressed = len(errors) - _ERROR_CAP
            errors = errors[:_ERROR_CAP]
            errors.append(f"…{suppressed} further capture errors suppressed.")
        return errors

    def _schedule_deferred_recovery(self) -> None:
        """Publish stalled media after its writer eventually releases handles."""
        if self._recovery_thread is not None and self._recovery_thread.is_alive():
            return
        writer_thread = self._writer_thread
        if writer_thread is None:
            return

        def recover_when_released() -> None:
            writer_thread.join()
            # Give a caller that retained the capture a brief chance to retry
            # normal attachment before falling back to visible recovery.
            time.sleep(_DEFERRED_RECOVERY_GRACE_S)
            with self._finalize_lock:
                if self._finalized:
                    return
                for writer in self._writers:
                    try:
                        writer.flush()
                        writer.close()
                    except Exception:  # noqa: BLE001 - never persist raw paths
                        self._record_error(
                            "A local recovery WAV could not be finalized."
                        )
                self._writers.clear()
                self._writer_thread = None
                errors = self._collect_errors()
                self._promote_recovery_parts(reason="writer_timeout", errors=errors)
                self._finalized = True

        self._recovery_thread = threading.Thread(
            target=recover_when_released,
            name="local-capture-recovery",
            daemon=True,
        )
        self._recovery_thread.start()

    def _promote_recovery_parts(self, *, reason: str, errors: list[str]) -> Path | None:
        """Move closed partial media out of a hidden working directory."""
        if self._temp_dir is None or not self._temp_dir.exists():
            return self._temp_dir
        stamp = time.strftime("%Y%m%d-%H%M%S")
        recovered = self.root / f"Recovered-local-{stamp}"
        if recovered.exists():
            recovered = self.root / f"Recovered-local-{stamp}-{uuid.uuid4().hex[:8]}"
        source_dir = self._temp_dir
        try:
            source_dir.replace(recovered)
        except OSError:
            errors.append(
                "Recoverable recording files remain in the private working "
                "folder because recovery publication failed."
            )
            return source_dir
        try:
            os.chmod(recovered, 0o700)
        except OSError:
            errors.append("Could not protect the private recovery folder.")

        promoted: list[Path] = []
        retained: list[Path] = []
        try:
            import soundfile as sf  # type: ignore
        except ImportError:  # pragma: no cover - runtime dependency guard
            sf = None
        for old_part in self._parts:
            part = recovered / old_part.name
            if not part.is_file():
                continue
            target = part
            if sf is not None:
                try:
                    info = sf.info(str(part))
                    if int(info.frames) > 0 and int(info.samplerate) > 0:
                        stem = part.name.removesuffix(".wav.part")
                        target = recovered / f"{stem}.recovered-partial.wav"
                        part.replace(target)
                        promoted.append(target)
                    else:
                        retained.append(part)
                except (OSError, RuntimeError):
                    retained.append(part)
            else:
                retained.append(part)
            if target.is_file() and not target.is_symlink():
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    errors.append("Could not protect one recovered local-audio file.")
        self._temp_dir = recovered
        self._parts = [*promoted, *retained]
        recovery_payload = {
            "schema": _RECOVERY_SCHEMA,
            "status": "recovered_partial",
            "reason": reason,
            "started_utc": self._started_utc,
            "sample_rate": self.samplerate,
            "total_frames_expected": self._next_input_frame,
            "total_frames": self._next_input_frame,
            "durable_frames": self._durable_frames,
            "take_id": self.take_id,
            "session_id": self.session_id,
            "tracks": _capture_tracks_payload(self._tracks),
            "gaps": _capture_gaps_payload(self._snapshot_gaps()),
            "files": [path.name for path in self._parts],
            "errors": list(errors),
        }
        device_payload = _capture_device_payload(self._capture_device)
        if device_payload is not None:
            recovery_payload["capture_device"] = device_payload
        try:
            from core.file_io import atomic_write_text

            atomic_write_text(
                recovered / _RECOVERY_REPORT,
                json.dumps(recovery_payload, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )
        except OSError:
            errors.append("Could not write the private recovery report.")
        errors.append(
            "Incomplete local audio was preserved in the visible recovery area."
        )
        return recovered

    def stop_into(self, take_dir: str | Path) -> LocalCaptureResult:
        with self._finalize_lock:
            if self._finalized:
                return LocalCaptureResult(
                    (),
                    self._started_utc,
                    self._started_monotonic,
                    0.0,
                    ("Local capture was already finalized.",),
                    tracks=self._tracks,
                )
            if not self._stopped_monotonic:
                self._stopped_monotonic = time.monotonic()
            if not self._drain_writer():
                errors = self._collect_errors()
                recovery_dir = self._temp_dir
                if recovery_dir is not None:
                    errors.append(
                        "Recoverable capture parts remain in private staging; "
                        "finalization may be retried after the writer stops."
                    )
                return LocalCaptureResult(
                    (),
                    self._started_utc,
                    self._started_monotonic,
                    max(0.0, self._stopped_monotonic - self._started_monotonic),
                    tuple(errors),
                    self._snapshot_gaps(),
                    self._next_input_frame,
                    recovery_dir,
                    self._capture_device,
                    self._durable_frames,
                    self._tracks,
                )

            self._finalized = True
            for writer in self._writers:
                try:
                    writer.flush()
                    writer.close()
                except Exception:  # noqa: BLE001 - never persist raw paths
                    self._record_error("A local WAV could not be finalized.")
            self._writers.clear()
            errors = self._collect_errors()

            if self._writer_incomplete:
                recovery_dir = self._promote_recovery_parts(
                    reason="incomplete_writer", errors=errors
                )
                return LocalCaptureResult(
                    (),
                    self._started_utc,
                    self._started_monotonic,
                    max(0.0, self._stopped_monotonic - self._started_monotonic),
                    tuple(errors),
                    self._snapshot_gaps(),
                    self._next_input_frame,
                    recovery_dir,
                    self._capture_device,
                    self._durable_frames,
                    self._tracks,
                )

            destination = Path(take_dir)
            destination.mkdir(parents=True, exist_ok=True, mode=0o700)
            final_files: list[Path] = []
            attach_failed = False
            for part in self._parts:
                base = part.name.removesuffix(".part")
                final = destination / base
                if final.exists():
                    # Never overwrite an existing take file (a server track
                    # could carry the same name); attach under a suffix that
                    # still classifies as a local stem.
                    stem = base.removesuffix(".wav")
                    counter = 1
                    while final.exists():
                        counter += 1
                        suffix = "-local" if counter == 2 else f"-local-{counter}"
                        final = destination / f"{stem}{suffix}.wav"
                    errors.append(
                        f"{base} already existed in the take; the isolated "
                        f"stem was attached as {final.name}."
                    )
                try:
                    part.replace(final)
                except OSError:
                    attach_failed = True
                    errors.append(
                        "Could not attach one isolated local stem to the take."
                    )
                    continue
                try:
                    os.chmod(final, 0o600)
                except OSError:
                    errors.append("Could not protect isolated stem.")
                final_files.append(final)
            self._cleanup_temp_dir(preserve=attach_failed, errors=errors)
            return LocalCaptureResult(
                tuple(final_files),
                self._started_utc,
                self._started_monotonic,
                max(0.0, self._stopped_monotonic - self._started_monotonic),
                tuple(errors),
                self._snapshot_gaps(),
                self._next_input_frame,
                None,
                self._capture_device,
                self._durable_frames,
                self._tracks,
            )

    def _cleanup_temp_dir(self, *, preserve: bool, errors: list[str]) -> None:
        if self._temp_dir is None:
            return
        if preserve and any(part.exists() for part in self._parts):
            self._promote_recovery_parts(reason="attachment_failed", errors=errors)
        else:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self._temp_dir = None

    def abort(self) -> None:
        """Discard the capture. Only for start-failure cleanup — anything
        that may hold real audio goes through ``stop_into`` so it is kept."""
        with self._finalize_lock:
            if self._finalized:
                return
            self._finalized = True
            try:
                writer_released = self._drain_writer()
            except Exception:  # noqa: BLE001
                LOGGER.debug("Local capture abort failed", exc_info=True)
                writer_released = False
            if not writer_released:
                # A writer may still be inside libsndfile.  Never close its
                # handles or remove/move the files from underneath it.
                LOGGER.error(
                    "Local capture abort retained open parts in %s because "
                    "the writer thread still owns them.",
                    self._temp_dir,
                )
                return
            for writer in self._writers:
                try:
                    writer.close()
                except Exception:  # noqa: BLE001
                    pass
            self._writers.clear()
            if self._temp_dir is not None:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
                self._temp_dir = None


def _process_may_be_alive(pid: object) -> bool:
    if isinstance(pid, bool):
        return False
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _canonical_optional_uuid(value: object) -> str:
    """Return a canonical opaque UUID or an empty value for recovery metadata."""
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return ""


def _metadata_nonnegative_int(value: object) -> int:
    try:
        if isinstance(value, bool):
            return 0
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _capture_device_payload(device: object | None) -> dict | None:
    """Return bounded, serializable device facts for private recovery state."""
    to_dict = getattr(device, "to_dict", None)
    if not callable(to_dict):
        return None
    try:
        candidate = to_dict()
    except Exception:  # noqa: BLE001 - recovery metadata is optional
        return None
    return candidate if isinstance(candidate, dict) else None


def _capture_tracks_payload(
    tracks: tuple[LocalCaptureTrack, ...] | list[LocalCaptureTrack],
) -> list[dict[str, object]]:
    """Serialize the exact logical-to-device channel map for recovery."""

    return [
        {
            "stem": track.stem,
            "source_channels": list(track.source_channels),
            **(
                {"logical_source_id": track.logical_source_id}
                if track.logical_source_id
                else {}
            ),
            **(
                {"logical_source_ordinal": track.logical_source_ordinal}
                if track.logical_source_ordinal is not None
                else {}
            ),
        }
        for track in tuple(tracks)[:_MAX_CAPTURE_TRACKS]
    ]


def _capture_gaps_payload(
    gaps: tuple[LocalCaptureGap, ...] | list[LocalCaptureGap],
) -> list[dict]:
    """Serialize only bounded, frame-exact local-capture gap evidence."""
    return [
        {
            "start_frame": item.start_frame,
            "frame_count": item.frame_count,
            "channels": list(item.channels),
            "reason": item.reason,
        }
        for item in tuple(gaps)[-_RECOVERY_GAP_CAP:]
    ]


def _fsync_regular_file(path: Path) -> None:
    """Durably flush one capture part without following an unexpected link."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise LocalCaptureError("capture part is not a regular file")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _recovered_capture_device(metadata: dict) -> object | None:
    """Restore bounded device facts only when the stored shape is trustworthy."""
    value = metadata.get("capture_device")
    if not isinstance(value, dict):
        return None
    try:
        from core.take_project import CaptureDevice

        return CaptureDevice.from_dict(value)
    except Exception:  # noqa: BLE001 - old/corrupt metadata remains recoverable
        return None


def _recovered_capture_tracks(metadata: dict) -> tuple[LocalCaptureTrack, ...]:
    """Restore schema-v2 channel maps and schema-v1 mono track records."""

    value = metadata.get("tracks")
    if not isinstance(value, list) or not value:
        return ()
    recovered: list[LocalCaptureTrack] = []
    try:
        for item in value[: _MAX_CAPTURE_TRACKS + 1]:
            if not isinstance(item, dict):
                return ()
            if "source_channels" in item:
                raw_channels = item.get("source_channels")
                if not isinstance(raw_channels, list):
                    return ()
                channels = tuple(raw_channels)
            elif "channel" in item:
                # Schema v1 stored one mono device channel per file.
                channels = (item.get("channel"),)
            else:
                return ()
            recovered.append(
                LocalCaptureTrack(
                    stem=item.get("stem"),
                    source_channels=channels,
                    logical_source_id=item.get("logical_source_id", ""),
                    logical_source_ordinal=item.get("logical_source_ordinal"),
                )
            )
        return _validated_capture_tracks(recovered)
    except (LocalCaptureError, TypeError, ValueError):
        return ()


def _recovered_capture_gaps(metadata: dict) -> tuple[LocalCaptureGap, ...]:
    """Parse bounded interval facts without trusting malformed checkpoint data."""
    value = metadata.get("gaps")
    if not isinstance(value, list):
        return ()
    recovered: list[LocalCaptureGap] = []
    for item in value[:_RECOVERY_GAP_CAP]:
        if not isinstance(item, dict):
            continue
        try:
            channels_raw = item.get("channels", ())
            if not isinstance(channels_raw, (list, tuple)):
                continue
            channels = tuple(int(channel) for channel in channels_raw)
            recovered.append(
                LocalCaptureGap(
                    start_frame=int(item.get("start_frame")),
                    frame_count=int(item.get("frame_count")),
                    channels=channels,
                    reason=str(item.get("reason") or "recovery_gap"),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(recovered)


def _read_recovery_metadata(path: Path, errors: list[str], *, label: str) -> dict:
    """Read one private recovery record without reflecting untrusted content."""
    if not path.is_file() or path.is_symlink():
        errors.append(f"The {label} was missing.")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        errors.append(f"The {label} was unreadable.")
        return {}
    if not isinstance(value, dict) or value.get("schema") not in {
        _LEGACY_RECOVERY_SCHEMA,
        _RECOVERY_SCHEMA,
    }:
        errors.append(f"The {label} was malformed.")
        return {}
    return value


def _recovery_audio_files(directory: Path) -> tuple[Path, ...]:
    """List direct recovery audio only; never follow links or nested paths."""
    try:
        entries = tuple(sorted(directory.iterdir(), key=lambda item: item.name))
    except OSError:
        return ()
    files: list[Path] = []
    for path in entries:
        if path.is_symlink() or not path.is_file():
            continue
        name = path.name.lower()
        if name.endswith(".recovered-partial.wav") or name.endswith(".wav.part"):
            files.append(path)
    return tuple(files)


def _has_final_recovery_project(directory: Path) -> bool:
    """Return true only after the atomic schema-v2 project was published."""
    manifest = directory / "webjam-take.json"
    if manifest.is_symlink() or not manifest.is_file():
        return False
    try:
        value = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and value.get("schema_version") == 2


def _visible_recovery_candidate(directory: Path) -> RecoveredLocalCapture | None:
    """Return an unmanifested visible recovery folder for a safe reattempt."""
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or _has_final_recovery_project(directory)
    ):
        return None
    files = _recovery_audio_files(directory)
    if not files:
        return None
    errors: list[str] = []
    metadata = _read_recovery_metadata(
        directory / _RECOVERY_METADATA,
        errors,
        label="capture checkpoint",
    )
    if not metadata:
        # The human report can still retain canonical IDs after a partial
        # promotion. Its free-text errors are intentionally not re-published.
        metadata = _read_recovery_metadata(
            directory / _RECOVERY_REPORT,
            errors,
            label="recovery report",
        )
    errors.append("Recovered local media is awaiting manifest reconciliation.")
    return RecoveredLocalCapture(
        source_dir=directory,
        recovery_dir=directory,
        files=files,
        errors=tuple(dict.fromkeys(errors)),
        take_id=_canonical_optional_uuid(metadata.get("take_id")),
        session_id=_canonical_optional_uuid(metadata.get("session_id")),
        started_utc=str(metadata.get("started_utc", ""))[:64],
        total_frames=_metadata_nonnegative_int(metadata.get("total_frames")),
        durable_frames=_metadata_nonnegative_int(metadata.get("durable_frames")),
        sample_rate=_metadata_nonnegative_int(metadata.get("sample_rate")),
        gaps=_recovered_capture_gaps(metadata),
        capture_device=_recovered_capture_device(metadata),
        tracks=_recovered_capture_tracks(metadata),
    )


def recover_stale_local_captures(
    root: str | Path,
    *,
    minimum_age_s: float = 5.0,
) -> tuple[RecoveredLocalCapture, ...]:
    """Promote abandoned hidden capture folders without deleting any media.

    Call this once before starting a new recorder. A folder whose checkpoint
    PID may still be alive is left untouched. Unknown/malformed checkpoints
    fail toward preservation: once old enough, their regular ``*.wav.part``
    files move to a visible recovery folder and are renamed to playable WAVs
    only when libsndfile can reopen them.
    """
    base = Path(root).expanduser()
    try:
        candidates = sorted(base.glob(".webjam-capture-*"))
    except OSError:
        return ()
    recovered_items: list[RecoveredLocalCapture] = []
    now = time.time()
    for source in candidates:
        if source.is_symlink() or not source.is_dir():
            continue
        try:
            if now - source.stat().st_mtime < max(0.0, float(minimum_age_s)):
                continue
        except OSError:
            continue
        errors: list[str] = []
        metadata = _read_recovery_metadata(
            source / _RECOVERY_METADATA,
            errors,
            label="capture checkpoint",
        )
        if metadata and _process_may_be_alive(metadata.get("pid")):
            continue

        stamp = time.strftime("%Y%m%d-%H%M%S")
        destination = base / f"Recovered-local-{stamp}"
        if destination.exists():
            destination = base / (f"Recovered-local-{stamp}-{uuid.uuid4().hex[:8]}")
        try:
            source.replace(destination)
        except OSError:
            continue
        try:
            os.chmod(destination, 0o700)
        except OSError:
            # The media has already moved successfully. Keep recovering it and
            # disclose the permission-hardening failure in RECOVERY.json.
            errors.append("Could not protect the recovered capture folder.")

        files: list[Path] = []
        try:
            import soundfile as sf  # type: ignore
        except ImportError:  # pragma: no cover - runtime dependency guard
            sf = None
        for part in sorted(destination.glob("*.wav.part")):
            if part.is_symlink() or not part.is_file():
                errors.append(f"Skipped unsafe recovery entry {part.name}.")
                continue
            output = part
            if sf is not None:
                try:
                    info = sf.info(str(part))
                    if int(info.frames) > 0 and int(info.samplerate) > 0:
                        output = destination / (
                            part.name.removesuffix(".wav.part")
                            + ".recovered-partial.wav"
                        )
                        part.replace(output)
                    else:
                        errors.append(f"{part.name} contains no readable frames.")
                except (OSError, RuntimeError):
                    errors.append(f"{part.name} could not be reopened as audio.")
            files.append(output)
            if output.is_file() and not output.is_symlink():
                try:
                    os.chmod(output, 0o600)
                except OSError:
                    errors.append("Could not protect one recovered local-audio file.")
        payload = {
            "schema": _RECOVERY_SCHEMA,
            "status": "recovered_partial",
            "reason": "startup_recovery",
            "source_working_folder": source.name,
            "started_utc": str(metadata.get("started_utc", ""))[:64],
            "sample_rate": metadata.get("sample_rate"),
            "take_id": _canonical_optional_uuid(metadata.get("take_id")),
            "session_id": _canonical_optional_uuid(metadata.get("session_id")),
            "total_frames": _metadata_nonnegative_int(metadata.get("total_frames")),
            "durable_frames": _metadata_nonnegative_int(metadata.get("durable_frames")),
            "tracks": _capture_tracks_payload(_recovered_capture_tracks(metadata)),
            "gaps": _capture_gaps_payload(_recovered_capture_gaps(metadata)),
            "files": [path.name for path in files],
            "errors": errors,
        }
        device_payload = _capture_device_payload(_recovered_capture_device(metadata))
        if device_payload is not None:
            payload["capture_device"] = device_payload
        try:
            from core.file_io import atomic_write_text

            atomic_write_text(
                destination / _RECOVERY_REPORT,
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )
        except OSError:
            errors.append("Could not write the private recovery report.")
        recovered_items.append(
            RecoveredLocalCapture(
                source_dir=source,
                recovery_dir=destination,
                files=tuple(files),
                errors=tuple(errors),
                take_id=_canonical_optional_uuid(metadata.get("take_id")),
                session_id=_canonical_optional_uuid(metadata.get("session_id")),
                started_utc=str(metadata.get("started_utc", ""))[:64],
                total_frames=_metadata_nonnegative_int(metadata.get("total_frames")),
                durable_frames=_metadata_nonnegative_int(
                    metadata.get("durable_frames")
                ),
                sample_rate=_metadata_nonnegative_int(metadata.get("sample_rate")),
                gaps=_recovered_capture_gaps(metadata),
                capture_device=_recovered_capture_device(metadata),
                tracks=_recovered_capture_tracks(metadata),
            )
        )
    # A process can die after promoting a hidden capture but before the
    # coordinator publishes its schema-v2 recovery project. Revisit those
    # visible folders on every startup until final publication proves the
    # media is attached to durable project truth.
    already_recovered = {item.recovery_dir.name for item in recovered_items}
    try:
        visible_directories = tuple(sorted(base.glob("Recovered-local-*")))
    except OSError:
        visible_directories = ()
    for directory in visible_directories:
        if directory.name in already_recovered:
            continue
        candidate = _visible_recovery_candidate(directory)
        if candidate is not None:
            recovered_items.append(candidate)
    return tuple(recovered_items)
