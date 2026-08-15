"""Host-controlled, Jamulus-routed reference-track primitives.

This module deliberately has no Qt dependency and never persists a selected
media path.  Source decoding runs on a bounded producer thread; a real-time
audio callback only consumes already-decoded float32 blocks and emits silence
on underrun.

The platform boundary lives behind :class:`ReferenceAudioBridgeBackend`.
Production currently supplies a capability-gated macOS BlackHole backend.
Windows and Linux remain truthfully unavailable until equivalent routing
isolation can be proved there.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path
import re
import stat
import threading
from typing import Callable, Protocol

import numpy as np

from core.project_audio import (
    RealtimeBlockPool,
    ProjectAudioDecoder,
    ProjectAudioError,
    project_audio_mp3_available,
)


# The Jamulus display name of the dedicated backing participant. It lives in
# core because the take builder has to recognise that stem when a recording is
# assembled, and core must not import from services to do it.
REFERENCE_PARTICIPANT_NAME = "WebJam Track"

REFERENCE_SAMPLE_RATE = 48_000
REFERENCE_BLOCK_FRAMES = 1_024
REFERENCE_QUEUE_BLOCKS = 12
REFERENCE_MAX_DECODE_FRAMES = 4_096
REFERENCE_MAX_SOURCE_RATE = 384_000
REFERENCE_MIN_TRIM_DB = -60.0
REFERENCE_MAX_TRIM_DB = 12.0
REFERENCE_MAX_COUNT_IN_BEATS = 16
REFERENCE_MIN_BPM = 20.0
REFERENCE_MAX_BPM = 400.0
REFERENCE_WAVEFORM_BINS = 128
REFERENCE_MAX_DIAGNOSTIC_COUNTER = (1 << 63) - 1
_BASE_SUPPORTED_EXTENSIONS = (
    ".wav",
    ".wave",
    ".aif",
    ".aiff",
    ".flac",
)
_ROUTE_WARNING = (
    "Jamulus-routed: this travels like another musician, with the session's "
    "normal buffering, jitter handling, and network latency. "
    "A server recording captures it as a separate stem."
)


def _bounded_diagnostic_counter(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return min(REFERENCE_MAX_DIAGNOSTIC_COUNTER, max(0, number))


def reference_track_supported_extensions() -> tuple[str, ...]:
    """Return the file-picker contract proved by the runtime decoder."""

    if project_audio_mp3_available():
        return (*_BASE_SUPPORTED_EXTENSIONS, ".mp3")
    return _BASE_SUPPORTED_EXTENSIONS


def reference_track_file_filter() -> str:
    """Return a truthful Qt-style filter without importing Qt into core."""

    patterns = " ".join(
        f"*{extension}" for extension in reference_track_supported_extensions()
    )
    return f"Audio files ({patterns})"


class ReferenceTrackError(RuntimeError):
    """A path-free, musician-safe reference-track failure."""


class ReferenceTrackState(str, Enum):
    UNAVAILABLE = "unavailable"
    IDLE = "idle"
    LOADING = "loading"
    READY = "ready"
    ROUTING = "routing"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPING = "stopping"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ReferenceTrackCapability:
    available: bool
    platform: str
    detail: str
    route_name: str = ""
    backend: str = ""
    reason_code: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "available", bool(self.available))
        for name, maximum in (("platform", 32), ("detail", 512), ("route_name", 128)):
            value = str(getattr(self, name) or "").strip()
            if len(value) > maximum:
                raise ValueError(f"{name} is too long")
            object.__setattr__(self, name, value)
        if self.available and not self.route_name:
            raise ValueError("an available route requires route_name")
        platform = self.platform.casefold()
        backend = str(self.backend or "").strip().casefold()
        if not backend:
            backend = {
                "macos": "blackhole",
                "windows": "vb-cable-jack",
                "linux": "jack",
            }.get(platform, "unavailable")
        if backend not in {
            "blackhole",
            "vb-cable-jack",
            "jack",
            "unavailable",
        }:
            backend = "unavailable"
        object.__setattr__(self, "backend", backend)
        reason = str(self.reason_code or "").strip().casefold()
        if not reason:
            reason = "ready" if self.available else "unavailable"
        if reason not in {
            "ready",
            "unavailable",
            "audience_bridge_conflict",
            "physical_certification_required",
            "cleanup_pending",
            "blackhole_unavailable",
            "windows_backend_unavailable",
            "linux_backend_unavailable",
            "live_route_unavailable",
            "unsupported_platform",
        }:
            reason = "unavailable"
        object.__setattr__(self, "reason_code", reason)


@dataclass(frozen=True, slots=True)
class ReferenceTrackLaunchContext:
    """Ephemeral facts needed to launch one separately-owned Jamulus client."""

    server_address: str
    jamulus_binary: str
    primary_udp_port: int
    primary_rpc_port: int
    primary_process_id: int
    primary_input_device_name: str = ""
    primary_output_device_name: str = ""
    audience_bridge_active: bool = False

    def __post_init__(self) -> None:
        server = str(self.server_address or "").strip()
        binary = str(self.jamulus_binary or "").strip()
        if not server:
            raise ValueError("server_address must not be empty")
        if not binary:
            raise ValueError("jamulus_binary must not be empty")
        object.__setattr__(self, "server_address", server)
        object.__setattr__(self, "jamulus_binary", binary)
        for field_name in ("primary_udp_port", "primary_rpc_port"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not 1 <= int(value) <= 65_535:
                raise ValueError(f"{field_name} must be between 1 and 65535")
            object.__setattr__(self, field_name, int(value))
        if (
            isinstance(self.primary_process_id, bool)
            or int(self.primary_process_id) <= 0
        ):
            raise ValueError("primary_process_id must be a positive integer")
        object.__setattr__(
            self, "primary_process_id", int(self.primary_process_id)
        )
        for field_name in (
            "primary_input_device_name",
            "primary_output_device_name",
        ):
            value = str(getattr(self, field_name) or "").strip()
            if (
                len(value) > 512
                or any(character in value for character in ("\0", "\r", "\n"))
            ):
                raise ValueError(f"{field_name} is invalid")
            object.__setattr__(self, field_name, value)
        object.__setattr__(
            self, "audience_bridge_active", bool(self.audience_bridge_active)
        )


@dataclass(frozen=True, slots=True, repr=False)
class ReferenceTrackOwnershipClaim:
    """Ephemeral process proof used only while finalizing a recording.

    ``udp_port`` is the exact live socket proved to belong to ``process_id``;
    it is not the Jamulus ``--port`` allocation base.  The private port and
    process identity deliberately never appear in a public snapshot,
    diagnostic, take manifest, or support bundle. The optional source digest
    is path-free content identity that the controller adds only after hashing
    the exact validated local source off the real-time path.
    """

    udp_port: int
    process_id: int
    generation: str
    source_fingerprint_sha256: str = ""
    playback_generation: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.udp_port, bool) or not 1 <= int(self.udp_port) <= 65_535:
            raise ValueError("udp_port must be between 1 and 65535")
        if isinstance(self.process_id, bool) or int(self.process_id) <= 0:
            raise ValueError("process_id must be a positive integer")
        generation = str(self.generation or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", generation) is None:
            raise ValueError("generation must be a private 128-bit token")
        source_fingerprint = str(
            self.source_fingerprint_sha256 or ""
        ).strip().lower()
        if source_fingerprint and re.fullmatch(
            r"[0-9a-f]{64}", source_fingerprint
        ) is None:
            raise ValueError("source_fingerprint_sha256 must be a SHA-256 digest")
        playback_generation = self.playback_generation
        if (
            isinstance(playback_generation, bool)
            or not isinstance(playback_generation, int)
            or not 0 <= playback_generation <= (1 << 63) - 1
        ):
            raise ValueError("playback_generation is outside the supported range")
        object.__setattr__(self, "udp_port", int(self.udp_port))
        object.__setattr__(self, "process_id", int(self.process_id))
        object.__setattr__(self, "generation", generation)
        object.__setattr__(
            self,
            "source_fingerprint_sha256",
            source_fingerprint,
        )
        object.__setattr__(self, "playback_generation", playback_generation)

    def __repr__(self) -> str:
        return "ReferenceTrackOwnershipClaim(<redacted>)"


@dataclass(frozen=True, slots=True)
class ReferenceTrackSnapshot:
    state: ReferenceTrackState
    capability: ReferenceTrackCapability
    source_name: str = ""
    duration_s: float = 0.0
    position_s: float = 0.0
    loop_start_s: float = 0.0
    loop_end_s: float | None = None
    trim_db: float = 0.0
    count_in_beats: int = 0
    count_in_bpm: float = 120.0
    source_format: str = ""
    source_samplerate: int = 0
    source_channels: int = 0
    route_detail: str = ""
    error: str = ""
    warning: str = _ROUTE_WARNING
    cleanup_pending: bool = False
    underrun_frames: int = 0
    count_in_active: bool = False
    waveform_peaks: tuple[float, ...] = ()
    waveform_progress: float = 0.0
    # Monotonic, controller-local identity for one playback attempt.  It is
    # deliberately path-free and lets Record Session bind a take to the exact
    # play/restart generation it planned instead of accepting any later song
    # playback as equivalent evidence.
    playback_generation: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", ReferenceTrackState(self.state))
        if not isinstance(self.capability, ReferenceTrackCapability):
            raise ValueError("capability must be a ReferenceTrackCapability")
        source_name = str(self.source_name or "").strip()
        if len(source_name) > 255 or any(
            c in source_name for c in ("\0", "\r", "\n", "/", "\\")
        ):
            source_name = "Selected song"
        object.__setattr__(self, "source_name", source_name)
        for name in ("duration_s", "position_s", "loop_start_s"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)
        if self.loop_end_s is not None:
            loop_end = float(self.loop_end_s)
            if not math.isfinite(loop_end) or loop_end <= self.loop_start_s:
                raise ValueError("loop_end_s must be after loop_start_s")
            object.__setattr__(self, "loop_end_s", loop_end)
        trim = float(self.trim_db)
        if not REFERENCE_MIN_TRIM_DB <= trim <= REFERENCE_MAX_TRIM_DB:
            raise ValueError("trim_db is out of range")
        object.__setattr__(self, "trim_db", trim)
        beats = int(self.count_in_beats)
        if isinstance(self.count_in_beats, bool) or not 0 <= beats <= REFERENCE_MAX_COUNT_IN_BEATS:
            raise ValueError("count_in_beats is out of range")
        object.__setattr__(self, "count_in_beats", beats)
        bpm = float(self.count_in_bpm)
        if not REFERENCE_MIN_BPM <= bpm <= REFERENCE_MAX_BPM:
            raise ValueError("count_in_bpm is out of range")
        object.__setattr__(self, "count_in_bpm", bpm)
        source_format = str(self.source_format or "").strip().upper()
        if source_format not in {
            "",
            "WAV",
            "WAVEX",
            "RF64",
            "AIFF",
            "FLAC",
            "MP3",
        }:
            source_format = "UNKNOWN" if source_name else ""
        object.__setattr__(self, "source_format", source_format)
        samplerate = int(self.source_samplerate)
        if isinstance(self.source_samplerate, bool) or not 0 <= samplerate <= 384_000:
            raise ValueError("source_samplerate is out of range")
        object.__setattr__(self, "source_samplerate", samplerate)
        channels = int(self.source_channels)
        if isinstance(self.source_channels, bool) or not 0 <= channels <= 2:
            raise ValueError("source_channels is out of range")
        object.__setattr__(self, "source_channels", channels)
        for name in ("route_detail", "error", "warning"):
            value = str(getattr(self, name) or "").strip()
            if len(value) > 1_024:
                raise ValueError(f"{name} is too long")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "cleanup_pending", bool(self.cleanup_pending))
        object.__setattr__(
            self,
            "underrun_frames",
            _bounded_diagnostic_counter(self.underrun_frames),
        )
        object.__setattr__(self, "count_in_active", bool(self.count_in_active))
        peaks = tuple(float(value) for value in self.waveform_peaks)
        if len(peaks) > REFERENCE_WAVEFORM_BINS or any(
            not math.isfinite(value) or not 0.0 <= value <= 1.0
            for value in peaks
        ):
            raise ValueError("waveform_peaks must be bounded normalized values")
        object.__setattr__(self, "waveform_peaks", peaks)
        progress = float(self.waveform_progress)
        if not math.isfinite(progress) or not 0.0 <= progress <= 1.0:
            raise ValueError("waveform_progress must be between zero and one")
        object.__setattr__(self, "waveform_progress", progress)
        playback_generation = self.playback_generation
        if (
            isinstance(playback_generation, bool)
            or not isinstance(playback_generation, int)
            or not 0 <= playback_generation <= (1 << 63) - 1
        ):
            raise ValueError("playback_generation is outside the supported range")
        object.__setattr__(self, "playback_generation", playback_generation)

    @property
    def loaded(self) -> bool:
        return bool(self.source_name)

    @property
    def can_play(self) -> bool:
        return bool(
            self.loaded
            and self.capability.available
            and self.state
            in {
                ReferenceTrackState.READY,
                ReferenceTrackState.PAUSED,
            }
        )

    @property
    def active(self) -> bool:
        return bool(
            self.cleanup_pending
            or self.state
            in {
                ReferenceTrackState.ROUTING,
                ReferenceTrackState.PLAYING,
                ReferenceTrackState.PAUSED,
                ReferenceTrackState.STOPPING,
            }
        )

    def public_diagnostics(self) -> dict[str, object]:
        """Return strict path- and filename-free Shared Track facts."""

        raw_platform = self.capability.platform.casefold()
        platform = (
            raw_platform
            if raw_platform in {"macos", "windows", "linux"}
            else "unknown"
        )
        return {
            "playback_state": self.state.value,
            "source_state": (
                "loading"
                if self.state is ReferenceTrackState.LOADING
                else (
                    "loaded"
                    if self.loaded
                    else (
                        "failed"
                        if self.state is ReferenceTrackState.FAILED
                        else "not_loaded"
                    )
                )
            ),
            "source_format": (
                self.source_format
                if self.source_format in {
                    "WAV",
                    "WAVEX",
                    "RF64",
                    "AIFF",
                    "FLAC",
                    "MP3",
                }
                else "unknown"
            ),
            "source_sample_rate_hz": self.source_samplerate,
            "source_channels": self.source_channels,
            "source_duration_s": round(self.duration_s, 3),
            "route_available": self.capability.available,
            "route_platform": platform or "unknown",
            "route_backend": self.capability.backend,
            "route_reason": self.capability.reason_code,
            "route_active": self.active,
            "cleanup_pending": self.cleanup_pending,
            "count_in_active": self.count_in_active,
        }


class ReferenceAudioBridgeSession(Protocol):
    @property
    def route_name(self) -> str: ...

    def start(self, pull_into: Callable[[np.ndarray], int]) -> None: ...

    def health_error(self) -> str: ...

    def recording_ownership_claim(self) -> ReferenceTrackOwnershipClaim | None: ...

    def stop(self) -> None: ...


class ReferenceAudioBridgeBackend(Protocol):
    def capability(
        self, audience_bridge_active: bool = False
    ) -> ReferenceTrackCapability: ...

    def prepare(
        self, context: ReferenceTrackLaunchContext
    ) -> ReferenceAudioBridgeSession: ...

    def retry_cleanup(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ReferenceTrackSourceInfo:
    name: str
    duration_s: float
    source_samplerate: int
    channels: int
    output_frames: int
    container: str
    initial_decode_frames: int
    source_fingerprint_sha256: str


class ReferenceTrackDecoder:
    """Shared Track adapter over the hardened project-audio decoder."""

    def __init__(self, path: str | Path) -> None:
        candidate = Path(path).expanduser()
        suffix = candidate.suffix.casefold()
        if suffix == ".mp3" and not project_audio_mp3_available():
            raise ReferenceTrackError(
                "MP3 decoding is unavailable in this build. Convert the song "
                "to WAV, AIFF, or FLAC and try again."
            ) from None
        if suffix not in reference_track_supported_extensions():
            raise ReferenceTrackError(
                "Choose a local WAV, WAVE, AIFF, FLAC, or supported MP3 audio file."
            )
        try:
            metadata = candidate.lstat()
        except OSError:
            raise ReferenceTrackError(
                "That song is unavailable. Choose a local audio file and try again."
            ) from None
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ReferenceTrackError(
                "Choose a regular local audio file instead of a link or folder."
            )

        try:
            decoder = ProjectAudioDecoder(candidate)
        except ProjectAudioError as exc:
            # ProjectAudioError messages are fixed, bounded, path-free user
            # sentences; passing the exact one through tells the musician
            # what to do (e.g. which MP3 structural check failed) instead of
            # a single opaque rejection.
            detail = str(exc).strip()
            if not detail:
                detail = (
                    "WebJam couldn't safely read that song. Check that it is "
                    "a valid one- or two-channel local audio file whose "
                    "format matches its filename."
                )
            raise ReferenceTrackError(detail) from None

        probe = decoder.probe
        source_rate = int(probe.source_sample_rate)
        channels = int(probe.channels)
        output_frames = int(probe.output_frames)
        initial_decode_frames = min(REFERENCE_BLOCK_FRAMES, output_frames)
        initial_audio = np.empty((initial_decode_frames, 2), dtype=np.float32)
        try:
            decoded_frames = decoder.read_into(0, initial_audio)
        except ProjectAudioError:
            decoder.close()
            initial_audio.fill(0.0)
            raise ReferenceTrackError(
                "WebJam couldn't safely decode the beginning of that song. "
                "Check that the local audio file is complete and try again."
            ) from None
        if decoded_frames != initial_decode_frames:
            decoder.close()
            initial_audio.fill(0.0)
            raise ReferenceTrackError(
                "WebJam couldn't safely decode the beginning of that song. "
                "Check that the local audio file is complete and try again."
            )
        initial_audio.fill(0.0)
        try:
            source_fingerprint = decoder.source_sha256()
        except ProjectAudioError:
            decoder.close()
            raise ReferenceTrackError(
                "That song changed while WebJam was loading it. Choose the "
                "local audio file again."
            ) from None

        safe_name = candidate.name.strip()
        if (
            not safe_name
            or len(safe_name) > 255
            or any(c in safe_name for c in ("\0", "\r", "\n"))
        ):
            safe_name = "Selected song"
        self._decoder = decoder
        self._source_rate = source_rate
        self._channels = channels
        self._output_frames = output_frames
        self._closed = False
        self.info = ReferenceTrackSourceInfo(
            name=safe_name,
            duration_s=output_frames / REFERENCE_SAMPLE_RATE,
            source_samplerate=source_rate,
            channels=channels,
            output_frames=output_frames,
            container=str(probe.container or "").upper(),
            initial_decode_frames=initial_decode_frames,
            source_fingerprint_sha256=source_fingerprint,
        )

    def __repr__(self) -> str:
        return (
            "ReferenceTrackDecoder("
            f"container={self.info.container!r}, rate={self._source_rate}, "
            f"channels={self._channels}, "
            f"output_frames={self._output_frames})"
        )

    @property
    def output_frames(self) -> int:
        return self._output_frames

    def read_48k(self, start_frame: int, frames: int) -> np.ndarray:
        """Allocate and decode one bounded worker-thread output window."""

        requested = int(frames)
        if not 0 <= requested <= REFERENCE_MAX_DECODE_FRAMES:
            raise ValueError("frames exceeds the bounded decoder limit")
        output = np.zeros((requested, 2), dtype=np.float32)
        self.read_48k_into(start_frame, output)
        return output

    def read_48k_into(self, start_frame: int, output: np.ndarray) -> int:
        """Decode into caller-owned storage on the producer thread."""

        start = int(start_frame)
        if start < 0:
            raise ValueError("start_frame must be non-negative")
        if (
            not isinstance(output, np.ndarray)
            or output.dtype != np.float32
            or output.ndim != 2
            or output.shape[1] != 2
            or not output.flags.c_contiguous
            or not 0 <= output.shape[0] <= REFERENCE_MAX_DECODE_FRAMES
        ):
            raise ValueError(
                "output must be a bounded C-contiguous float32 stereo array"
            )
        if self._closed:
            raise ReferenceTrackError("The selected song was already closed.")
        try:
            return self._decoder.read_into(start, output)
        except ProjectAudioError:
            output.fill(0.0)
            raise ReferenceTrackError(
                "WebJam lost access to the selected song. Load it again."
            ) from None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._decoder.close()


class _ReferencePlaybackRing:
    """Preallocated SPSC handoff from decoder worker to audio callback.

    The producer owns ``_write_sequence`` and the callback owns
    ``_read_sequence``. Both sides only read the other sequence, so neither
    needs a mutex or a shared read/modify/write counter. Control-plane changes
    advance a generation rather than clearing storage underneath the callback.
    """

    def __init__(self, capacity: int, block_frames: int) -> None:
        self.pool = RealtimeBlockPool(capacity, block_frames, 2)
        self.capacity = self.pool.capacity
        self.block_frames = self.pool.block_frames
        self._frame_counts = np.zeros(self.capacity, dtype=np.int32)
        self._start_frames = np.zeros(self.capacity, dtype=np.int64)
        self._end_frames = np.zeros(self.capacity, dtype=np.int64)
        self._generations = np.zeros(self.capacity, dtype=np.int64)
        self._count_in = np.zeros(self.capacity, dtype=np.bool_)
        self._finish_after = np.zeros(self.capacity, dtype=np.bool_)
        self._write_sequence = 0
        self._read_sequence = 0
        self._front_offset = 0
        self.position_frame = 0
        self.position_generation = -1
        self.finished_generation = -1
        self._pending_generation = -1
        self._pending_position_frame = -1
        self._pending_finished = False
        self.callback_calls = 0
        self.requested_frames = 0
        self.delivered_frames = 0
        self.underrun_frames = 0
        # Callback-owned count-in progress. These scalar writes remain inside
        # the lock-free consumer path; UI/control readers only observe a
        # bounded snapshot and never coordinate the callback with a lock.
        self.count_in_delivered_generation = -1
        self.count_in_delivered_frames = 0

    @property
    def has_write_capacity(self) -> bool:
        return self._write_sequence - self._read_sequence < self.capacity

    def acquire_write_buffer(self) -> np.ndarray | None:
        if not self.has_write_capacity:
            return None
        return self.pool.buffer(self._write_sequence % self.capacity)

    def commit_write(
        self,
        frame_count: int,
        *,
        song_start_frame: int,
        song_end_frame: int,
        generation: int,
        count_in: bool,
        finish_after: bool,
    ) -> bool:
        if not self.has_write_capacity:
            return False
        slot = self._write_sequence % self.capacity
        self._frame_counts[slot] = int(frame_count)
        self._start_frames[slot] = int(song_start_frame)
        self._end_frames[slot] = int(song_end_frame)
        self._generations[slot] = int(generation)
        self._count_in[slot] = bool(count_in)
        self._finish_after[slot] = bool(finish_after)
        # Publishing the sequence is deliberately the final producer write.
        self._write_sequence += 1
        return True

    def pull_into(self, output: np.ndarray, *, generation: int) -> int:
        """Fill callback-owned storage without waiting or allocating audio."""

        if (
            not isinstance(output, np.ndarray)
            or output.dtype != np.float32
            or output.ndim != 2
            or output.shape[1] != 2
            or output.shape[0] > REFERENCE_MAX_DECODE_FRAMES
        ):
            raise ValueError("output does not fit the Shared Track ring")
        output.fill(0.0)
        requested = int(output.shape[0])
        self._pending_generation = generation
        self._pending_position_frame = -1
        self._pending_finished = False
        self.callback_calls = min(
            REFERENCE_MAX_DIAGNOSTIC_COUNTER,
            self.callback_calls + 1,
        )
        self.requested_frames = min(
            REFERENCE_MAX_DIAGNOSTIC_COUNTER,
            self.requested_frames + requested,
        )
        written = 0
        while written < requested:
            if self._read_sequence >= self._write_sequence:
                break
            slot = self._read_sequence % self.capacity
            frame_count = int(self._frame_counts[slot])
            slot_generation = int(self._generations[slot])
            if slot_generation < generation:
                self._read_sequence += 1
                self._front_offset = 0
                continue
            if slot_generation > generation:
                # An obsolete callback must never consume blocks already
                # prepared for a newer play/restart generation.
                break
            available = frame_count - self._front_offset
            if available <= 0:
                self._read_sequence += 1
                self._front_offset = 0
                continue
            amount = min(requested - written, available)
            source = self.pool.buffer(slot)
            begin = self._front_offset
            np.copyto(
                output[written : written + amount],
                source[begin : begin + amount],
            )
            if bool(self._count_in[slot]):
                if self.count_in_delivered_generation != generation:
                    self.count_in_delivered_generation = generation
                    self.count_in_delivered_frames = 0
                self.count_in_delivered_frames += amount
            self._front_offset += amount
            written += amount
            if not bool(self._count_in[slot]):
                self._pending_position_frame = min(
                    int(self._end_frames[slot]),
                    int(self._start_frames[slot]) + self._front_offset,
                )
            if self._front_offset >= frame_count:
                finish_after = bool(self._finish_after[slot])
                self._read_sequence += 1
                self._front_offset = 0
                if finish_after:
                    self._pending_finished = True
                    break
        self.delivered_frames = min(
            REFERENCE_MAX_DIAGNOSTIC_COUNTER,
            self.delivered_frames + written,
        )
        self.underrun_frames = min(
            REFERENCE_MAX_DIAGNOSTIC_COUNTER,
            self.underrun_frames + requested - written,
        )
        return written

    def commit_pull_metadata(self, generation: int) -> None:
        """Publish cursor/EOF facts only for audio accepted by the callback."""

        if self._pending_generation != generation:
            return
        if self._pending_position_frame >= 0:
            self.position_frame = self._pending_position_frame
            self.position_generation = generation
        if self._pending_finished:
            self.finished_generation = generation
        self._pending_generation = -1
        self._pending_position_frame = -1
        self._pending_finished = False

    def discard_pull_metadata(self, generation: int) -> None:
        """Discard cursor/EOF facts for a callback whose output was silenced."""

        if self._pending_generation != generation:
            return
        self._pending_generation = -1
        self._pending_position_frame = -1
        self._pending_finished = False


class ReferenceTrackStream:
    """Bounded producer with a preallocated, lock-free callback handoff."""

    def __init__(
        self,
        decoder: ReferenceTrackDecoder,
        *,
        block_frames: int = REFERENCE_BLOCK_FRAMES,
        queue_blocks: int = REFERENCE_QUEUE_BLOCKS,
    ) -> None:
        block_frames = int(block_frames)
        queue_blocks = int(queue_blocks)
        if not 1 <= block_frames <= REFERENCE_MAX_DECODE_FRAMES:
            raise ValueError("block_frames is out of range")
        if not 2 <= queue_blocks <= 128:
            raise ValueError("queue_blocks is out of range")
        self._decoder = decoder
        self._block_frames = block_frames
        self._queue_blocks = queue_blocks
        self._condition = threading.Condition(threading.Lock())
        self._ring = _ReferencePlaybackRing(queue_blocks, block_frames)
        self._playing = False
        self._closed = False
        self._finished = False
        self._error = ""
        self._generation = 0
        # Callback-visible latches. Reads and single-object assignments are
        # atomic under CPython's GIL; no callback path acquires _condition.
        self._realtime_generation = 0
        self._producer_position = 0
        self._consumer_position = 0
        self._loop_start = 0
        self._loop_end: int | None = None
        self._trim_db = 0.0
        self._trim_gain = 1.0
        self._count_in_beats = 0
        self._count_in_bpm = 120.0
        self._count_in_total = 0
        self._count_in_cursor = 0
        # Waveform analysis is owned by the existing decoder producer thread,
        # never the real-time callback. It scans in bounded blocks while idle
        # and yields immediately to playback work.
        self._waveform_peaks = np.zeros(
            REFERENCE_WAVEFORM_BINS, dtype=np.float32
        )
        self._waveform_scan_frame = 0
        self._waveform_buffer = np.empty(
            (REFERENCE_MAX_DECODE_FRAMES, 2), dtype=np.float32
        )
        self._thread = threading.Thread(
            target=self._run,
            name="WebJam reference-track decoder",
            daemon=True,
        )
        self._thread.start()

    @property
    def duration_s(self) -> float:
        return self._decoder.output_frames / REFERENCE_SAMPLE_RATE

    @property
    def position_s(self) -> float:
        with self._condition:
            if self._ring.position_generation == self._generation:
                position = self._ring.position_frame
            else:
                position = self._consumer_position
            return position / REFERENCE_SAMPLE_RATE

    @property
    def finished(self) -> bool:
        with self._condition:
            return bool(
                self._finished
                or self._ring.finished_generation == self._generation
            )

    @property
    def error(self) -> str:
        with self._condition:
            return self._error

    def realtime_stats(self) -> dict[str, int]:
        """Return path-free SPSC counters outside the callback thread."""

        return {
            "callback_calls": _bounded_diagnostic_counter(
                self._ring.callback_calls
            ),
            "requested_frames": _bounded_diagnostic_counter(
                self._ring.requested_frames
            ),
            "delivered_frames": _bounded_diagnostic_counter(
                self._ring.delivered_frames
            ),
            "underrun_frames": _bounded_diagnostic_counter(
                self._ring.underrun_frames
            ),
        }

    @property
    def count_in_active(self) -> bool:
        """Return bounded consumer truth for the audible pre-roll."""

        with self._condition:
            if not self._playing or self._count_in_total <= 0:
                return False
            delivered = (
                self._ring.count_in_delivered_frames
                if self._ring.count_in_delivered_generation == self._generation
                else 0
            )
            return delivered < self._count_in_total

    @property
    def waveform_summary(self) -> tuple[tuple[float, ...], float]:
        """Return a path-free, normalized progressive waveform snapshot."""

        with self._condition:
            peaks = tuple(float(value) for value in self._waveform_peaks)
            total = max(1, self._decoder.output_frames)
            progress = min(1.0, self._waveform_scan_frame / total)
        return peaks, progress

    def configure_loop(self, start_s: float, end_s: float | None) -> None:
        start = self._seconds_to_frame(start_s)
        end = None if end_s is None else self._seconds_to_frame(end_s)
        if end is not None and (
            end <= start or end > self._decoder.output_frames
        ):
            raise ReferenceTrackError(
                "The loop end must be after its start and inside the song."
            )
        with self._condition:
            self._loop_start = start
            self._loop_end = end
            if end is not None and self._consumer_position >= end:
                self._consumer_position = start
            self._reset_producer_locked()

    def configure_trim(self, trim_db: float) -> None:
        value = float(trim_db)
        if (
            not math.isfinite(value)
            or not REFERENCE_MIN_TRIM_DB <= value <= REFERENCE_MAX_TRIM_DB
        ):
            raise ReferenceTrackError(
                f"Song trim must be between {REFERENCE_MIN_TRIM_DB:g} and "
                f"+{REFERENCE_MAX_TRIM_DB:g} dB."
            )
        with self._condition:
            self._trim_db = value
            self._trim_gain = 10.0 ** (value / 20.0)
            self._reset_producer_locked()

    def configure_count_in(self, beats: int, bpm: float = 120.0) -> None:
        if (
            isinstance(beats, bool)
            or not 0 <= int(beats) <= REFERENCE_MAX_COUNT_IN_BEATS
        ):
            raise ReferenceTrackError(
                f"Count-in must be 0 to {REFERENCE_MAX_COUNT_IN_BEATS} beats."
            )
        tempo = float(bpm)
        if not math.isfinite(tempo) or not REFERENCE_MIN_BPM <= tempo <= REFERENCE_MAX_BPM:
            raise ReferenceTrackError(
                f"Count-in tempo must be {REFERENCE_MIN_BPM:g} to "
                f"{REFERENCE_MAX_BPM:g} BPM."
            )
        with self._condition:
            self._count_in_beats = int(beats)
            self._count_in_bpm = tempo

    def play(self, *, count_in: bool = False) -> None:
        with self._condition:
            if self._closed:
                raise ReferenceTrackError("The selected song was already closed.")
            if self._consumer_position >= self._decoder.output_frames:
                self._consumer_position = (
                    self._loop_start if self._loop_end is not None else 0
                )
            self._finished = False
            self._error = ""
            self._playing = True
            self._reset_producer_locked()
            if count_in and self._count_in_beats:
                frames_per_beat = round(
                    REFERENCE_SAMPLE_RATE * 60.0 / self._count_in_bpm
                )
                self._count_in_total = frames_per_beat * self._count_in_beats
            else:
                self._count_in_total = 0
            self._count_in_cursor = 0
            self._realtime_generation = self._generation
            self._condition.notify_all()

    def pause(self) -> None:
        with self._condition:
            self._playing = False
            self._realtime_generation = 0
            self._count_in_total = 0
            self._count_in_cursor = 0
            self._reset_producer_locked()

    def seek(self, seconds: float) -> None:
        frame = self._seconds_to_frame(seconds)
        if frame > self._decoder.output_frames:
            frame = self._decoder.output_frames
        with self._condition:
            self._consumer_position = frame
            self._finished = False
            self._error = ""
            self._count_in_total = 0
            self._count_in_cursor = 0
            # An explicit seek is authoritative.  In particular, Restart
            # seeks while playback is live, when the callback may still have
            # a committed position for the current generation.  Reconciling
            # that stale position here would silently replace the requested
            # frame and turn Restart into a no-op.
            self._reset_producer_locked(reconcile_callback_position=False)

    def restart(self, *, count_in: bool = True) -> None:
        with self._condition:
            if self._closed:
                raise ReferenceTrackError("The selected song was already closed.")
            # Quiesce the callback before publishing one atomic beginning-of-
            # song generation.  A separate seek()/play() pair would leave a
            # window where the callback could consume song frames before the
            # restart count-in and advance the new cursor away from 0:00.
            self._realtime_generation = 0
            self._playing = False
            self._consumer_position = 0
            self._finished = False
            self._error = ""
            if count_in and self._count_in_beats:
                frames_per_beat = round(
                    REFERENCE_SAMPLE_RATE * 60.0 / self._count_in_bpm
                )
                self._count_in_total = frames_per_beat * self._count_in_beats
            else:
                self._count_in_total = 0
            self._count_in_cursor = 0
            self._playing = True
            self._reset_producer_locked(reconcile_callback_position=False)

    def pull(self, frames: int) -> np.ndarray:
        """Allocate a convenience result outside the real-time callback."""

        requested = int(frames)
        if requested <= 0 or requested > REFERENCE_MAX_DECODE_FRAMES:
            raise ValueError("pull frames is out of range")
        output = np.zeros((requested, 2), dtype=np.float32)
        self.pull_into(output)
        return output

    def pull_into(self, output: np.ndarray) -> int:
        """Fill callback-owned storage without locks, I/O, or audio allocation."""

        generation = self._realtime_generation
        if generation <= 0 or self._closed:
            output.fill(0.0)
            return 0
        delivered = self._ring.pull_into(output, generation=generation)
        # Pause/restart/control can publish a new generation while the callback
        # is copying an older block. Never let obsolete audio escape and never
        # mutate the newer control generation from this callback.
        if self._realtime_generation != generation or self._closed:
            self._ring.discard_pull_metadata(generation)
            output.fill(0.0)
            return 0
        self._ring.commit_pull_metadata(generation)
        return delivered

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._playing = False
            self._realtime_generation = 0
            self._condition.notify_all()
        self._thread.join(timeout=2.0)
        self._decoder.close()

    def _reset_producer_locked(
        self,
        *,
        reconcile_callback_position: bool = True,
    ) -> None:
        if (
            reconcile_callback_position
            and self._ring.position_generation == self._generation
        ):
            self._consumer_position = self._ring.position_frame
        self._generation += 1
        self._realtime_generation = self._generation if self._playing else 0
        self._producer_position = self._consumer_position
        self._condition.notify_all()

    def _seconds_to_frame(self, seconds: float) -> int:
        value = float(seconds)
        if not math.isfinite(value) or value < 0:
            raise ReferenceTrackError("Song position must be finite and non-negative.")
        return int(round(value * REFERENCE_SAMPLE_RATE))

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._closed:
                    at_end = (
                        self._producer_position >= self._decoder.output_frames
                        and self._loop_end is None
                        and self._count_in_cursor >= self._count_in_total
                    )
                    if (
                        self._playing
                        and self._ring.has_write_capacity
                        and not at_end
                    ):
                        work_kind = "playback"
                        break
                    if (
                        not self._playing
                        and self._waveform_scan_frame
                        < self._decoder.output_frames
                    ):
                        work_kind = "waveform"
                        break
                    # The callback never enters this condition merely to wake
                    # the producer. Poll a full ring well inside one 1024-frame
                    # period; idle/control waits can remain relaxed.
                    timeout = (
                        0.005
                        if self._playing
                        and not self._ring.has_write_capacity
                        else 0.05
                        if self._playing
                        else 0.25
                    )
                    self._condition.wait(timeout=timeout)
                if self._closed:
                    return
                if work_kind == "waveform":
                    scan_start = self._waveform_scan_frame
                    scan_amount = min(
                        REFERENCE_MAX_DECODE_FRAMES,
                        self._decoder.output_frames - scan_start,
                    )
                    target = self._waveform_buffer[:scan_amount]
                    generation = self._generation
                else:
                    scan_start = 0
                    scan_amount = 0
                    target = self._ring.acquire_write_buffer()
                    generation = self._generation
                    if target is None:
                        self._condition.wait(timeout=0.005)
                        continue
            if work_kind == "waveform":
                try:
                    decoded_frames = self._decoder.read_48k_into(
                        scan_start,
                        target,
                    )
                    if decoded_frames != scan_amount:
                        raise ReferenceTrackError(
                            "WebJam couldn't decode the complete song preview. "
                            "Load the song again."
                        )
                    self._merge_waveform_block(
                        scan_start,
                        target[:scan_amount],
                    )
                except ReferenceTrackError as exc:
                    with self._condition:
                        if generation == self._generation:
                            self._error = str(exc)
                            self._waveform_scan_frame = self._decoder.output_frames
                    continue
                with self._condition:
                    if generation == self._generation:
                        self._waveform_scan_frame = max(
                            self._waveform_scan_frame,
                            scan_start + scan_amount,
                        )
                continue

            with self._condition:
                # Playback may have been paused while the worker crossed the
                # bounded decoder boundary above. Re-check before publishing.
                if self._closed:
                    return
                generation = self._generation
                count_remaining = self._count_in_total - self._count_in_cursor
                if count_remaining > 0:
                    amount = min(self._block_frames, count_remaining)
                    count_start = self._count_in_cursor
                    self._count_in_cursor += amount
                    song_start = self._producer_position
                    count_config = (
                        self._count_in_bpm,
                        self._count_in_beats,
                        self._count_in_total,
                    )
                else:
                    loop_end = self._loop_end
                    if loop_end is not None and self._producer_position >= loop_end:
                        self._producer_position = self._loop_start
                    song_start = self._producer_position
                    boundary = (
                        loop_end
                        if loop_end is not None
                        else self._decoder.output_frames
                    )
                    amount = min(self._block_frames, boundary - song_start)
                    if amount <= 0:
                        self._condition.wait(timeout=0.05)
                        continue
                    self._producer_position += amount
                    finish_after = (
                        loop_end is None
                        and self._producer_position >= self._decoder.output_frames
                    )
                    trim_gain = self._trim_gain

            if count_remaining > 0:
                self._render_count_in_into(
                    target[:amount],
                    count_start,
                    bpm=count_config[0],
                    total_beats=count_config[1],
                    total_frames=count_config[2],
                )
                # Source trim is intentionally not a count-in gain. The cue
                # remains audible even when a hot song is trimmed far down.
                np.clip(target[:amount], -1.0, 1.0, out=target[:amount])
                song_end = song_start
                count_in = True
                finish_after = False
            else:
                try:
                    decoded_frames = self._decoder.read_48k_into(
                        song_start,
                        target[:amount],
                    )
                    if decoded_frames != amount:
                        target[:amount].fill(0.0)
                        raise ReferenceTrackError(
                            "WebJam couldn't decode the complete song block. "
                            "Load the song again."
                        )
                except ReferenceTrackError as exc:
                    with self._condition:
                        if generation == self._generation:
                            self._error = str(exc)
                            self._finished = True
                            self._playing = False
                            self._realtime_generation = 0
                    continue
                np.multiply(
                    target[:amount],
                    np.float32(trim_gain),
                    out=target[:amount],
                )
                np.clip(target[:amount], -1.0, 1.0, out=target[:amount])
                song_end = song_start + amount
                count_in = False
                self._merge_waveform_block(song_start, target[:amount])
            with self._condition:
                if (
                    not self._closed
                    and self._playing
                    and generation == self._generation
                ):
                    self._ring.commit_write(
                        amount,
                        song_start_frame=song_start,
                        song_end_frame=song_end,
                        generation=generation,
                        count_in=count_in,
                        finish_after=finish_after,
                    )

    def _merge_waveform_block(
        self,
        start_frame: int,
        audio: np.ndarray,
    ) -> None:
        """Merge one producer-thread block into fixed normalized peak bins."""

        frames = int(audio.shape[0])
        if frames <= 0:
            return
        total = max(1, self._decoder.output_frames)
        first = min(
            REFERENCE_WAVEFORM_BINS - 1,
            int(start_frame) * REFERENCE_WAVEFORM_BINS // total,
        )
        last = min(
            REFERENCE_WAVEFORM_BINS - 1,
            max(first, (int(start_frame) + frames - 1) * REFERENCE_WAVEFORM_BINS // total),
        )
        peak = min(1.0, float(np.max(np.abs(audio))))
        with self._condition:
            np.maximum(
                self._waveform_peaks[first : last + 1],
                np.float32(peak),
                out=self._waveform_peaks[first : last + 1],
            )

    @staticmethod
    def _render_count_in_into(
        output: np.ndarray,
        start: int,
        *,
        bpm: float,
        total_beats: int,
        total_frames: int,
    ) -> None:
        del total_beats
        frames = int(output.shape[0])
        per_beat = max(1, round(REFERENCE_SAMPLE_RATE * 60.0 / bpm))
        indexes = start + np.arange(frames, dtype=np.int64)
        phase_in_beat = indexes % per_beat
        click_frames = min(per_beat, round(REFERENCE_SAMPLE_RATE * 0.04))
        active = phase_in_beat < click_frames
        beat_number = indexes // per_beat
        last_beat = max(0, (total_frames - 1) // per_beat)
        frequency = np.where(beat_number == last_beat, 1_400.0, 950.0)
        envelope = np.maximum(
            0.0, 1.0 - phase_in_beat.astype(np.float32) / max(1, click_frames)
        )
        tone = (
            np.sin(
                2.0
                * np.pi
                * frequency
                * phase_in_beat.astype(np.float64)
                / REFERENCE_SAMPLE_RATE
            )
            * envelope
            * active
            * 0.55
        ).astype(np.float32)
        output[:, 0] = tone
        output[:, 1] = tone


class ReferenceTrackController:
    """Thread-safe host authority for one ephemeral reference-track route."""

    def __init__(
        self,
        backend: ReferenceAudioBridgeBackend,
        *,
        is_host: Callable[[], bool],
        on_snapshot: Callable[[ReferenceTrackSnapshot], None] | None = None,
    ) -> None:
        if not callable(is_host):
            raise TypeError("is_host must be callable")
        if on_snapshot is not None and not callable(on_snapshot):
            raise TypeError("on_snapshot must be callable or None")
        self._backend = backend
        self._is_host = is_host
        self._on_snapshot = on_snapshot
        self._lock = threading.RLock()
        # Serializes the blocking backend ownership boundary.  State updates
        # still use ``_lock`` and ``cancel_pending_start`` remains an immediate
        # non-blocking revocation, but Stop/Close may not report completion
        # while a prepare/start call can still create cleanup work.
        self._route_lifecycle_lock = threading.RLock()
        self._stream: ReferenceTrackStream | None = None
        self._session: ReferenceAudioBridgeSession | None = None
        self._state = ReferenceTrackState.IDLE
        self._source_name = ""
        self._duration_s = 0.0
        self._loop_start_s = 0.0
        self._loop_end_s: float | None = None
        self._trim_db = 0.0
        self._count_in_beats = 0
        self._count_in_bpm = 120.0
        self._source_format = ""
        self._source_samplerate = 0
        self._source_channels = 0
        self._source_fingerprint_sha256 = ""
        self._route_detail = ""
        self._error = ""
        # True only when FAILED means a pre-playback route/capability start
        # failure and no owned Jamulus client remains. A successful explicit
        # capability recheck may then return the loaded source to READY.
        self._recoverable_route_failure = False
        # Incrementing this token cancels a prepare/start operation that has
        # temporarily released the controller lock. Stop, close, and an
        # incompatible capability refresh all use it so a stale worker cannot
        # resurrect an owned Jamulus client.
        self._launch_generation = 0
        self._source_generation = 0
        self._playback_generation = 0
        self._capability = self._safe_capability(False)

    @property
    def snapshot(self) -> ReferenceTrackSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def public_diagnostics(self) -> dict[str, object]:
        """Return strict source/route facts suitable for a support bundle."""

        with self._lock:
            snapshot = self._snapshot_locked()
            stream = self._stream
            session = self._session
        diagnostics = snapshot.public_diagnostics()
        stream_stats = stream.realtime_stats() if stream is not None else {}
        session_stats: object = {}
        if session is not None:
            reader = getattr(session, "realtime_stats", None)
            if callable(reader):
                try:
                    session_stats = reader()
                except Exception:  # noqa: BLE001 - diagnostics are advisory
                    session_stats = {}
        if not isinstance(session_stats, dict):
            session_stats = {}
        diagnostics.update(
            {
                "audio_callback_calls": _bounded_diagnostic_counter(
                    stream_stats.get("callback_calls", 0)
                ),
                "audio_requested_frames": _bounded_diagnostic_counter(
                    stream_stats.get("requested_frames", 0)
                ),
                "audio_delivered_frames": _bounded_diagnostic_counter(
                    stream_stats.get("delivered_frames", 0)
                ),
                "audio_underrun_frames": _bounded_diagnostic_counter(
                    stream_stats.get("underrun_frames", 0)
                ),
                "audio_callback_faults": _bounded_diagnostic_counter(
                    session_stats.get("callback_faults", 0)
                ),
            }
        )
        return diagnostics

    def recording_ownership_claim(self) -> ReferenceTrackOwnershipClaim | None:
        """Return a claim only while the exact backend session is published."""

        with self._lock:
            session = self._session
        if session is None:
            return None
        reader = getattr(session, "recording_ownership_claim", None)
        if not callable(reader):
            return None
        try:
            claim = reader()
        except Exception:  # noqa: BLE001 - private backend evidence boundary
            return None
        if not isinstance(claim, ReferenceTrackOwnershipClaim):
            return None
        with self._lock:
            if (
                self._session is not session
                or self._stream is None
                or not self._source_fingerprint_sha256
            ):
                return None
            source_fingerprint = self._source_fingerprint_sha256
        return ReferenceTrackOwnershipClaim(
            udp_port=claim.udp_port,
            process_id=claim.process_id,
            generation=claim.generation,
            source_fingerprint_sha256=source_fingerprint,
            playback_generation=self._playback_generation,
        )

    def recording_source_fingerprint(self) -> str:
        """Return the exact loaded source digest for pre-record planning.

        The digest is content identity, not a filename or path.  It remains a
        controller-to-recorder seam and is intentionally absent from public
        snapshots, diagnostics, peer state, and support bundles.
        """

        with self._lock:
            if self._stream is None or not self._source_fingerprint_sha256:
                return ""
            return self._source_fingerprint_sha256

    def refresh_capability(
        self, audience_bridge_active: bool = False
    ) -> ReferenceTrackSnapshot:
        capability = self._safe_capability(audience_bridge_active)
        with self._lock:
            self._require_open_locked()
            # Publish incompatibility before observing session ownership. A
            # concurrent play that has prepared but not yet published then
            # sees either the changed generation or unavailable capability;
            # a play already published is captured for immediate teardown.
            self._capability = capability
            backend_cleanup_pending = (
                capability.reason_code == "cleanup_pending"
            )
            if (
                not capability.available
                and self._state is ReferenceTrackState.ROUTING
            ):
                self._launch_generation += 1
                if self._session is None:
                    self._state = (
                        ReferenceTrackState.READY
                        if self._stream is not None
                        else ReferenceTrackState.IDLE
                    )
                    self._error = ""
                    self._recoverable_route_failure = False
            if backend_cleanup_pending:
                self._state = ReferenceTrackState.FAILED
                self._error = capability.detail
                self._recoverable_route_failure = False
            active_session = self._session is not None
        stopped: ReferenceTrackSnapshot | None = None
        if active_session and not capability.available:
            # A route that becomes incompatible (most importantly because the
            # audience bridge claimed the same device) must stop before the UI
            # can publish the new capability. Merely greying out controls
            # would leave an unsafe second client alive.
            stopped = self.stop()
        with self._lock:
            self._require_open_locked()
            self._capability = capability
            if backend_cleanup_pending:
                self._state = ReferenceTrackState.FAILED
                self._error = capability.detail
                self._recoverable_route_failure = False
            elif (
                stopped is not None
                and stopped.state is ReferenceTrackState.FAILED
                and self._session is not None
            ):
                # Preserve teardown truth. The capability is unavailable, but
                # the more urgent fact is that owned-process death is unproved.
                pass
            elif not capability.available and self._session is None:
                if self._state is ReferenceTrackState.UNAVAILABLE:
                    self._state = (
                        ReferenceTrackState.READY
                        if self._stream is not None
                        else ReferenceTrackState.IDLE
                    )
            elif capability.available and self._state is ReferenceTrackState.UNAVAILABLE:
                self._state = (
                    ReferenceTrackState.READY
                    if self._stream is not None
                    else ReferenceTrackState.IDLE
                )
            elif (
                capability.available
                and self._state is ReferenceTrackState.FAILED
                and self._recoverable_route_failure
                and self._session is None
                and self._stream is not None
            ):
                self._state = ReferenceTrackState.READY
                self._error = ""
                self._recoverable_route_failure = False
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def load(self, path: str | Path) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            if self._session is not None or self._state in {
                ReferenceTrackState.ROUTING,
                ReferenceTrackState.PLAYING,
                ReferenceTrackState.PAUSED,
                ReferenceTrackState.STOPPING,
            }:
                raise ReferenceTrackError(
                    "Stop the Shared Track before replacing it."
                )
        stopped = self.stop()
        with self._lock:
            self._require_open_locked()
            if (
                stopped.state is ReferenceTrackState.FAILED
                and (
                    self._session is not None
                    or stopped.cleanup_pending
                )
            ):
                # Never replace the source (or hide the failure state) while
                # an older owned Jamulus process may still be alive.
                return stopped
            self._state = ReferenceTrackState.LOADING
            self._error = ""
            self._recoverable_route_failure = False
            self._source_generation += 1
            source_generation = self._source_generation
            loading = self._snapshot_locked()
        self._notify(loading)
        try:
            decoder = ReferenceTrackDecoder(path)
            stream = ReferenceTrackStream(decoder)
        except ReferenceTrackError as exc:
            with self._lock:
                if (
                    source_generation != self._source_generation
                    or self._state is ReferenceTrackState.CLOSED
                ):
                    return self._snapshot_locked()
                self._state = ReferenceTrackState.FAILED
                self._error = str(exc)
                self._recoverable_route_failure = False
                failed = self._snapshot_locked()
            self._notify(failed)
            return failed

        old_stream: ReferenceTrackStream | None
        with self._lock:
            if (
                source_generation != self._source_generation
                or self._state is ReferenceTrackState.CLOSED
            ):
                stale = self._snapshot_locked()
                install_stream = False
            else:
                install_stream = True
            if not install_stream:
                old_stream = None
            else:
                old_stream = self._stream
                self._stream = stream
                self._source_name = decoder.info.name
                self._duration_s = decoder.info.duration_s
                self._loop_start_s = 0.0
                self._loop_end_s = None
                self._trim_db = 0.0
                self._source_format = decoder.info.container
                self._source_samplerate = decoder.info.source_samplerate
                self._source_channels = decoder.info.channels
                self._source_fingerprint_sha256 = (
                    decoder.info.source_fingerprint_sha256
                )
                self._error = ""
                self._recoverable_route_failure = False
                self._state = ReferenceTrackState.READY
                snapshot = self._snapshot_locked()
        if not install_stream:
            stream.close()
            return stale
        if old_stream is not None:
            old_stream.close()
        self._notify(snapshot)
        return snapshot

    def unload(self) -> ReferenceTrackSnapshot:
        """Remove the selected source after proving owned playback stopped.

        The imported file is never modified. If private route cleanup cannot
        be confirmed, retain the old source and owner so Stop can be retried
        without presenting a false empty state.
        """

        with self._lock:
            self._require_open_locked()
            if self._session is not None or self._state in {
                ReferenceTrackState.ROUTING,
                ReferenceTrackState.PLAYING,
                ReferenceTrackState.PAUSED,
                ReferenceTrackState.STOPPING,
            }:
                raise ReferenceTrackError(
                    "Stop the Shared Track before removing it."
                )
        stopped = self.stop()
        with self._lock:
            self._require_open_locked()
            if stopped.cleanup_pending or (
                stopped.state is ReferenceTrackState.FAILED
                and self._session is not None
            ):
                return stopped
            stream = self._stream
            self._source_generation += 1
            self._stream = None
            self._source_name = ""
            self._duration_s = 0.0
            self._loop_start_s = 0.0
            self._loop_end_s = None
            self._trim_db = 0.0
            self._count_in_beats = 0
            self._count_in_bpm = 120.0
            self._source_format = ""
            self._source_samplerate = 0
            self._source_channels = 0
            self._source_fingerprint_sha256 = ""
            self._route_detail = ""
            self._error = ""
            self._recoverable_route_failure = False
            self._state = ReferenceTrackState.IDLE
            snapshot = self._snapshot_locked()
        if stream is not None:
            stream.close()
        self._notify(snapshot)
        return snapshot

    def play(self, context: ReferenceTrackLaunchContext) -> ReferenceTrackSnapshot:
        with self._route_lifecycle_lock:
            return self._play_route(context)

    def _play_route(
        self,
        context: ReferenceTrackLaunchContext,
    ) -> ReferenceTrackSnapshot:
        resume_session: ReferenceAudioBridgeSession | None = None
        with self._lock:
            self._require_open_locked()
            if not self._is_host():
                return self._fail_locked(
                    "Only the session host can control the Shared Track."
                )
            if context.audience_bridge_active:
                return self._fail_locked(
                    "Shared Track can't share BlackHole with the Conversation "
                    "audience bridge. Switch Conversation to talkback or "
                    "video-only first.",
                    recoverable_route=True,
                )
            if self._stream is None:
                return self._fail_locked("Load a song before starting Shared Track.")
            if self._state is ReferenceTrackState.PLAYING:
                return self._snapshot_locked()
            if self._state is ReferenceTrackState.ROUTING:
                return self._snapshot_locked()
            if (
                self._state is ReferenceTrackState.PAUSED
                and self._session is not None
            ):
                resume_session = self._session
        if resume_session is not None:
            checked = self.refresh_health()
            with self._lock:
                if (
                    checked.state is ReferenceTrackState.FAILED
                    or self._session is not resume_session
                ):
                    return checked
                if self._state is not ReferenceTrackState.PAUSED:
                    return self._snapshot_locked()
                self._stream.play(count_in=False)
                self._state = ReferenceTrackState.PLAYING
                self._error = ""
                resumed = self._snapshot_locked()
            self._notify(resumed)
            return resumed
        capability = self._safe_capability(context.audience_bridge_active)
        if capability.reason_code == "cleanup_pending":
            cleanup_error = self._retry_backend_cleanup()
            if cleanup_error:
                with self._lock:
                    return self._fail_locked(
                        cleanup_error,
                        recoverable_route=False,
                    )
            capability = self._safe_capability(
                context.audience_bridge_active
            )
        with self._lock:
            self._capability = capability
            if not self._capability.available:
                return self._fail_locked(
                    self._capability.detail,
                    recoverable_route=True,
                )
            self._state = ReferenceTrackState.ROUTING
            self._error = ""
            self._recoverable_route_failure = False
            self._launch_generation += 1
            self._playback_generation += 1
            launch_generation = self._launch_generation
            routing = self._snapshot_locked()
        self._notify(routing)

        session: ReferenceAudioBridgeSession | None = None
        try:
            session = self._backend.prepare(context)
            with self._lock:
                launch_is_current = (
                    launch_generation == self._launch_generation
                    and self._state is ReferenceTrackState.ROUTING
                    and self._capability.available
                )
            if not launch_is_current:
                return self._retire_unpublished_session(session)
            session.start(self._stream.pull_into)
            with self._lock:
                launch_is_current = (
                    launch_generation == self._launch_generation
                    and self._state is ReferenceTrackState.ROUTING
                    and self._capability.available
                )
                if launch_is_current:
                    self._stream.play(count_in=True)
                    self._session = session
                    self._route_detail = session.route_name
                    self._state = ReferenceTrackState.PLAYING
                    self._error = ""
                    self._recoverable_route_failure = False
                    snapshot = self._snapshot_locked()
        except Exception as exc:  # noqa: BLE001 - backend boundary
            failure_capability = self._safe_capability(
                context.audience_bridge_active
            )
            with self._lock:
                self._capability = failure_capability
                backend_cleanup_pending = (
                    failure_capability.reason_code == "cleanup_pending"
                )
                launch_is_current = (
                    launch_generation == self._launch_generation
                    and self._state is ReferenceTrackState.ROUTING
                )
            if not launch_is_current:
                if session is None:
                    if backend_cleanup_pending:
                        with self._lock:
                            self._state = ReferenceTrackState.FAILED
                            self._error = failure_capability.detail
                            self._recoverable_route_failure = False
                            pending_snapshot = self._snapshot_locked()
                        self._notify(pending_snapshot)
                        return pending_snapshot
                    return self.snapshot
                return self._retire_unpublished_session(session)
            teardown_error = ""
            if session is not None:
                try:
                    session.stop()
                except ReferenceTrackError as stop_exc:
                    teardown_error = str(stop_exc)
                except Exception:  # noqa: BLE001
                    teardown_error = (
                        "Shared Track couldn't confirm that its owned "
                        "Jamulus client stopped."
                    )
                if teardown_error:
                    with self._lock:
                        # Retain the exact session owner so Stop/Close/Load can
                        # retry cleanup instead of hiding a surviving client.
                        self._session = session
                        self._route_detail = str(
                            getattr(session, "route_name", "") or ""
                        )
            message = (
                teardown_error
                or (
                    failure_capability.detail
                    if backend_cleanup_pending
                    else str(exc)
                    if isinstance(exc, ReferenceTrackError)
                    else "WebJam couldn't prove a safe Shared Track route."
                )
            )
            with self._lock:
                return self._fail_locked(
                    message,
                    recoverable_route=not backend_cleanup_pending
                    and not teardown_error
                    and self._session is None,
                )

        if not launch_is_current:
            return self._retire_unpublished_session(session)
        self._notify(snapshot)
        return snapshot

    def pause(self) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            if self._state is not ReferenceTrackState.PLAYING or self._stream is None:
                raise ReferenceTrackError("Shared Track is not playing.")
            self._stream.pause()
            self._state = ReferenceTrackState.PAUSED
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def restart(self) -> ReferenceTrackSnapshot:
        checked = self.refresh_health()
        if checked.state is ReferenceTrackState.FAILED:
            return checked
        with self._lock:
            self._require_open_locked()
            if self._stream is None or self._session is None:
                raise ReferenceTrackError("Start Shared Track before restarting it.")
            self._playback_generation += 1
            self._stream.restart(count_in=True)
            self._state = ReferenceTrackState.PLAYING
            self._error = ""
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def seek(self, seconds: float) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            if self._stream is None:
                raise ReferenceTrackError("Load a song before seeking.")
            if self._state not in {
                ReferenceTrackState.PAUSED,
                ReferenceTrackState.READY,
            }:
                raise ReferenceTrackError("Pause Shared Track before seeking.")
            self._stream.seek(seconds)
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def set_loop(
        self, start_s: float, end_s: float | None
    ) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            if self._stream is None:
                raise ReferenceTrackError("Load a song before setting a loop.")
            self._stream.configure_loop(start_s, end_s)
            self._loop_start_s = float(start_s)
            self._loop_end_s = None if end_s is None else float(end_s)
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def set_trim_db(self, trim_db: float) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            if self._stream is None:
                raise ReferenceTrackError("Load a song before changing its trim.")
            self._stream.configure_trim(trim_db)
            self._trim_db = float(trim_db)
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def set_count_in(
        self, beats: int, bpm: float = 120.0
    ) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            if self._stream is None:
                raise ReferenceTrackError("Load a song before setting its count-in.")
            self._stream.configure_count_in(beats, bpm)
            self._count_in_beats = int(beats)
            self._count_in_bpm = float(bpm)
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def refresh_health(self) -> ReferenceTrackSnapshot:
        with self._lock:
            self._require_open_locked()
            session = self._session
            stream = self._stream
        if session is not None:
            try:
                error = session.health_error().strip()
            except Exception:  # noqa: BLE001
                error = "Shared Track couldn't verify its owned route."
            if error:
                teardown_error = self._stop_session()
                with self._lock:
                    self._state = ReferenceTrackState.FAILED
                    self._error = teardown_error or error
                    self._recoverable_route_failure = False
                    snapshot = self._snapshot_locked()
                self._notify(snapshot)
                return snapshot
        if stream is not None and stream.error:
            teardown_error = self._stop_session()
            with self._lock:
                self._state = ReferenceTrackState.FAILED
                self._error = teardown_error or stream.error
                self._recoverable_route_failure = False
                snapshot = self._snapshot_locked()
            self._notify(snapshot)
            return snapshot
        if stream is not None and stream.finished and session is not None:
            self.stop()
        return self.snapshot

    def handle_session_end(self) -> ReferenceTrackSnapshot:
        return self.stop()

    def cancel_pending_start(self) -> ReferenceTrackSnapshot:
        """Synchronously revoke an unpublished route start without blocking."""

        with self._lock:
            if self._state is ReferenceTrackState.CLOSED:
                return self._snapshot_locked()
            if (
                self._state is not ReferenceTrackState.ROUTING
                or self._session is not None
            ):
                return self._snapshot_locked()
            self._launch_generation += 1
            self._state = (
                ReferenceTrackState.READY
                if self._stream is not None
                else ReferenceTrackState.IDLE
            )
            self._error = ""
            self._recoverable_route_failure = False
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def stop(self) -> ReferenceTrackSnapshot:
        with self._route_lifecycle_lock:
            return self._stop_route()

    def _stop_route(self) -> ReferenceTrackSnapshot:
        with self._lock:
            if self._state is ReferenceTrackState.CLOSED:
                return self._snapshot_locked()
            self._launch_generation += 1
            has_active = self._session is not None
            if has_active:
                self._state = ReferenceTrackState.STOPPING
                stopping = self._snapshot_locked()
            else:
                stopping = None
            stream = self._stream
        if stopping is not None:
            self._notify(stopping)
        if stream is not None:
            stream.pause()
            stream.seek(0.0)
        teardown_error = self._stop_session()
        if not teardown_error:
            latest_capability = self._safe_capability(False)
            with self._lock:
                self._capability = latest_capability
            if latest_capability.reason_code == "cleanup_pending":
                teardown_error = self._retry_backend_cleanup()
        with self._lock:
            self._route_detail = ""
            if teardown_error:
                self._error = teardown_error
                self._state = ReferenceTrackState.FAILED
                self._recoverable_route_failure = False
            else:
                self._error = ""
                self._recoverable_route_failure = False
                self._state = (
                    ReferenceTrackState.READY
                    if self._stream is not None
                    else ReferenceTrackState.IDLE
                )
            snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def close(self) -> ReferenceTrackSnapshot:
        with self._route_lifecycle_lock:
            stopped = self.stop()
            with self._lock:
                if stopped.cleanup_pending or (
                    stopped.state is ReferenceTrackState.FAILED
                    and self._session is not None
                ):
                    return stopped
            with self._lock:
                stream = self._stream
                self._source_generation += 1
                self._stream = None
                self._state = ReferenceTrackState.CLOSED
                self._source_name = ""
                self._duration_s = 0.0
                self._source_format = ""
                self._source_samplerate = 0
                self._source_channels = 0
                self._source_fingerprint_sha256 = ""
                self._recoverable_route_failure = False
                snapshot = self._snapshot_locked()
            if stream is not None:
                stream.close()
            self._notify(snapshot)
            return snapshot

    def _stop_session(self) -> str:
        with self._lock:
            session = self._session
        if session is None:
            return ""
        try:
            session.stop()
        except ReferenceTrackError as exc:
            return str(exc)
        except Exception:  # noqa: BLE001
            return (
                "Shared Track couldn't confirm that its owned Jamulus "
                "client stopped."
            )
        with self._lock:
            if self._session is session:
                self._session = None
        return ""

    def _retire_unpublished_session(
        self,
        session: ReferenceAudioBridgeSession,
    ) -> ReferenceTrackSnapshot:
        """Stop a prepared session whose launch authority was superseded."""

        try:
            session.stop()
        except ReferenceTrackError as exc:
            teardown_error = str(exc)
        except Exception:  # noqa: BLE001
            teardown_error = (
                "Shared Track couldn't confirm that its owned Jamulus "
                "client stopped."
            )
        else:
            teardown_error = ""
        if teardown_error:
            with self._lock:
                self._session = session
                self._route_detail = str(
                    getattr(session, "route_name", "") or ""
                )
                self._state = ReferenceTrackState.FAILED
                self._error = teardown_error
                self._recoverable_route_failure = False
                snapshot = self._snapshot_locked()
            self._notify(snapshot)
            return snapshot
        return self.snapshot

    def _safe_capability(self, audience_bridge_active: bool) -> ReferenceTrackCapability:
        try:
            capability = self._backend.capability(audience_bridge_active)
        except Exception:  # noqa: BLE001
            capability = ReferenceTrackCapability(
                False,
                "unknown",
                "Shared Track routing could not be inspected safely.",
            )
        if not isinstance(capability, ReferenceTrackCapability):
            return ReferenceTrackCapability(
                False,
                "unknown",
                "Shared Track routing returned invalid capability evidence.",
            )
        return capability

    def _retry_backend_cleanup(self) -> str:
        retry = getattr(self._backend, "retry_cleanup", None)
        if not callable(retry):
            return (
                "Shared Track private cleanup is still pending. Restart "
                "WebJam before trying the route again."
            )
        try:
            retry()
        except ReferenceTrackError as exc:
            message = str(exc).strip()
        except Exception:  # noqa: BLE001 - backend recovery boundary
            message = (
                "Shared Track private cleanup could not be confirmed. "
                "Choose Stop again."
            )
        else:
            message = ""
        capability = self._safe_capability(False)
        with self._lock:
            self._capability = capability
        if capability.reason_code == "cleanup_pending":
            return message or capability.detail
        return ""

    def _snapshot_locked(self) -> ReferenceTrackSnapshot:
        position = self._stream.position_s if self._stream is not None else 0.0
        underruns = 0
        count_in_active = False
        waveform_peaks: tuple[float, ...] = ()
        waveform_progress = 0.0
        if self._stream is not None:
            underruns = self._stream.realtime_stats().get("underrun_frames", 0)
            count_in_active = self._stream.count_in_active
            waveform_peaks, waveform_progress = self._stream.waveform_summary
        return ReferenceTrackSnapshot(
            state=self._state,
            capability=self._capability,
            source_name=self._source_name,
            duration_s=self._duration_s,
            position_s=position,
            loop_start_s=self._loop_start_s,
            loop_end_s=self._loop_end_s,
            trim_db=self._trim_db,
            count_in_beats=self._count_in_beats,
            count_in_bpm=self._count_in_bpm,
            source_format=self._source_format,
            source_samplerate=self._source_samplerate,
            source_channels=self._source_channels,
            route_detail=self._route_detail,
            error=self._error,
            cleanup_pending=(
                (
                    self._session is not None
                    and self._state is ReferenceTrackState.FAILED
                )
                or self._capability.reason_code == "cleanup_pending"
            ),
            underrun_frames=underruns,
            count_in_active=count_in_active,
            waveform_peaks=waveform_peaks,
            waveform_progress=waveform_progress,
            playback_generation=self._playback_generation,
        )

    def _fail_locked(
        self,
        message: str,
        *,
        recoverable_route: bool = False,
    ) -> ReferenceTrackSnapshot:
        self._state = ReferenceTrackState.FAILED
        self._error = str(message or "Shared Track couldn't continue.").strip()
        self._recoverable_route_failure = bool(recoverable_route)
        snapshot = self._snapshot_locked()
        self._notify(snapshot)
        return snapshot

    def _require_open_locked(self) -> None:
        if self._state is ReferenceTrackState.CLOSED:
            raise ReferenceTrackError("Shared Track was already closed.")

    def _notify(self, snapshot: ReferenceTrackSnapshot) -> None:
        callback = self._on_snapshot
        if callback is None:
            return
        try:
            callback(snapshot)
        except Exception:  # noqa: BLE001 - observers cannot own transport
            pass


__all__ = [
    "REFERENCE_BLOCK_FRAMES",
    "REFERENCE_MAX_DIAGNOSTIC_COUNTER",
    "REFERENCE_MAX_DECODE_FRAMES",
    "REFERENCE_QUEUE_BLOCKS",
    "REFERENCE_SAMPLE_RATE",
    "REFERENCE_WAVEFORM_BINS",
    "ReferenceAudioBridgeBackend",
    "ReferenceAudioBridgeSession",
    "ReferenceTrackCapability",
    "ReferenceTrackController",
    "ReferenceTrackDecoder",
    "ReferenceTrackError",
    "ReferenceTrackLaunchContext",
    "ReferenceTrackOwnershipClaim",
    "ReferenceTrackSnapshot",
    "ReferenceTrackSourceInfo",
    "ReferenceTrackState",
    "ReferenceTrackStream",
    "reference_track_file_filter",
    "reference_track_supported_extensions",
]
