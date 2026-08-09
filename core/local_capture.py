"""Failure-safe supplemental capture for isolated host inputs.

Jamulus remains the live-audio authority.  This service records the selected
Core Audio device's first two inputs as local mono stems so a host can retain
separate instrument and vocal tracks without changing the network path.

Real-time layout: the sounddevice callback copies each block into a fixed,
preallocated SPSC ring; a dedicated writer thread does every disk write,
status aggregation, and gap materialization. The callback never allocates a
block, logs, waits, performs I/O, or acquires a lock. This is a separate
PortAudio capture path. WebJam records the selected device metadata, but
cannot prove that Jamulus is using the same physical input or that both
applications share an identical route.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.project_audio import CaptureBlockRing

LOGGER = logging.getLogger("webjam.local_capture")

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
_RECOVERY_SCHEMA = 1
# A real local take must survive more than an in-memory capture ring. One
# second at the fixed capture rate is frequent enough to make a sudden process
# exit recoverable without putting any I/O on PortAudio's callback thread.
_DURABLE_CHECKPOINT_FRAMES = 48_000
_RECOVERY_GAP_CAP = 128


class LocalCaptureError(RuntimeError):
    """Raised when supplemental capture cannot start or finish safely."""


@dataclass(frozen=True)
class LocalCaptureGap:
    """A half-open interval where source audio was replaced by silence.

    ``channels`` follow the fixed local-stem order (host guitar, host vocal),
    even when final attachment fails and ``LocalCaptureResult.files`` is
    empty.  The interval is expressed on the capture's absolute 48 kHz frame
    timeline, so callers can disclose and align discontinuities precisely.
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

    @property
    def gap_count(self) -> int:
        return len(self.gaps)


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


class LocalInputCapture:
    """Record two device channels to atomic mono WAV files."""

    def __init__(
        self,
        root: str | Path,
        *,
        device: int = -1,
        samplerate: int = 48000,
        blocksize: int = 0,
        take_id: str = "",
        session_id: str = "",
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
        self._ring_block_frames = (
            self.blocksize or _CAPTURE_RING_DEFAULT_BLOCK_FRAMES
        )
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
        self._writer_frames = [0, 0]
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
                self._temp_dir / "host-guitar.wav.part",
                self._temp_dir / "host-vocal.wav.part",
            ]
            self._writers = [
                sf.SoundFile(str(path), mode="w", samplerate=self.samplerate,
                             channels=1, format="WAV", subtype="PCM_24")
                for path in self._parts
            ]

            sd.check_input_settings(
                device=self.device, channels=2, samplerate=self.samplerate,
                dtype="float32",
            )
            ring_block_frames = int(self._ring_block_frames)
            bytes_per_slot = ring_block_frames * 2 * np.dtype(np.float32).itemsize
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
                input_channels=2,
                channel_map=(0, 1),
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
                    or indata.shape[1] < 2
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
                device=self.device, channels=2, samplerate=self.samplerate,
                blocksize=self.blocksize, dtype="float32", callback=callback,
            )
            self._writer_thread = threading.Thread(
                target=self._writer_loop, daemon=True, name="local-capture-writer",
            )
            self._writer_thread.start()
            self._started_monotonic = time.monotonic()
            self._stream.start()
        except Exception:  # noqa: BLE001 - native errors may contain device paths
            self.abort()
            raise LocalCaptureError(
                "Could not open two isolated host inputs at 48 kHz. Check the "
                "selected input and folder access, then try again."
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
            "channels": 2,
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
        """Flush and fsync both stems before advancing recovery metadata.

        ``soundfile.flush`` commits libsndfile's buffered frames; an explicit
        file fsync then commits the resulting WAV bytes.  Both happen on the
        dedicated writer thread, never the audio callback.  A failure leaves
        the audio in place but records a bounded recovery-needed fact so the
        final manifest cannot pretend the local original was crash-durable.
        """
        durable_frames = min(self._writer_frames, default=0)
        if not force and durable_frames - self._durable_frames < _DURABLE_CHECKPOINT_FRAMES:
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
            channel_indices=(0, 1),
            channel_labels=("Input 1", "Input 2"),
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
                    (0, 1),
                    "queue_overflow",
                )
                for channel in range(len(self._writers)):
                    self._pad_writer_to(channel, start_frame)
            elif start_frame < timeline_frame:
                self._record_error(
                    "Local capture received an out-of-order audio block."
                )

            for channel in range(len(self._writers)):
                self._write_channel_block(
                    channel,
                    start_frame,
                    scratch[:frame_count, channel],
                )
            timeline_frame = max(timeline_frame, end_frame)
            self._checkpoint_audio_durability()

        target = self._final_input_frame
        if target is None:
            target = self._next_input_frame
        if target > timeline_frame:
            self._record_gap(
                timeline_frame,
                target - timeline_frame,
                (0, 1),
                "queue_overflow",
            )
        for channel in range(len(self._writers)):
            if not self._pad_writer_to(channel, target):
                self._writer_incomplete = True
            if self._writer_frames[channel] != target:
                self._writer_incomplete = True
                self._record_error(
                    f"Local capture channel {channel + 1} ended at frame "
                    f"{self._writer_frames[channel]} instead of {target}."
                )
        self._checkpoint_audio_durability(force=True)

    def _writer_position(self, channel: int, *, expected: int | None = None) -> int:
        """Refresh a writer's position when its implementation exposes it."""
        writer = self._writers[channel]
        position = expected
        tell = getattr(writer, "tell", None)
        if callable(tell):
            try:
                position = int(tell())
            except Exception:  # noqa: BLE001
                pass
        if position is not None:
            self._writer_frames[channel] = max(0, position)
        return self._writer_frames[channel]

    def _pad_writer_to(self, channel: int, target_frame: int) -> bool:
        """Write silence until one stem reaches ``target_frame``."""
        writer = self._writers[channel]
        while self._writer_frames[channel] < target_frame:
            frame_count = min(
                _SILENCE_CHUNK_FRAMES,
                target_frame - self._writer_frames[channel],
            )
            expected = self._writer_frames[channel] + frame_count
            try:
                writer.write(np.zeros(frame_count, dtype="float32"))
            except Exception:  # noqa: BLE001 - writer details may contain paths
                self._record_error(
                    f"Local capture silence write failed on channel "
                    f"{channel + 1}."
                )
                self._writer_position(channel)
                return False
            self._writer_position(channel, expected=expected)
        return self._writer_frames[channel] == target_frame

    def _write_channel_block(
        self, channel: int, start_frame: int, samples: np.ndarray
    ) -> None:
        """Place a source block on its absolute timeline for one stem."""
        end_frame = start_frame + len(samples)
        if not self._pad_writer_to(channel, start_frame):
            self._record_gap(
                start_frame, end_frame - start_frame, (channel,), "write_failure"
            )
            return

        position = self._writer_frames[channel]
        if position >= end_frame:
            return
        offset = max(0, position - start_frame)
        expected = end_frame
        try:
            self._writers[channel].write(samples[offset:])
        except Exception:  # noqa: BLE001 - writer details may contain paths
            self._record_error(
                f"Local capture write failed on channel {channel + 1}."
            )
            position = self._writer_position(channel)
            gap_start = min(end_frame, max(start_frame, position))
            if gap_start < end_frame:
                self._record_gap(
                    gap_start, end_frame - gap_start, (channel,), "write_failure"
                )
            if not self._pad_writer_to(channel, end_frame):
                self._writer_incomplete = True
            return
        self._writer_position(channel, expected=expected)

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
            suffix = (
                f" (×{other_status_events})"
                if other_status_events > 1
                else ""
            )
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
                self._promote_recovery_parts(
                    reason="writer_timeout", errors=errors
                )
                self._finalized = True

        self._recovery_thread = threading.Thread(
            target=recover_when_released,
            name="local-capture-recovery",
            daemon=True,
        )
        self._recovery_thread.start()

    def _promote_recovery_parts(
        self, *, reason: str, errors: list[str]
    ) -> Path | None:
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
                    errors.append(
                        "Could not protect one recovered local-audio file."
                    )
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
                    (), self._started_utc, self._started_monotonic, 0.0,
                    ("Local capture was already finalized.",),
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
                    (), self._started_utc, self._started_monotonic,
                    max(0.0, self._stopped_monotonic - self._started_monotonic),
                    tuple(errors), self._snapshot_gaps(), self._next_input_frame,
                    recovery_dir, self._capture_device, self._durable_frames,
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
                    (), self._started_utc, self._started_monotonic,
                    max(0.0, self._stopped_monotonic - self._started_monotonic),
                    tuple(errors), self._snapshot_gaps(), self._next_input_frame,
                    recovery_dir, self._capture_device, self._durable_frames,
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
                    errors.append(
                        "Could not protect isolated stem."
                    )
                final_files.append(final)
            self._cleanup_temp_dir(preserve=attach_failed, errors=errors)
            return LocalCaptureResult(
                tuple(final_files), self._started_utc, self._started_monotonic,
                max(0.0, self._stopped_monotonic - self._started_monotonic),
                tuple(errors), self._snapshot_gaps(), self._next_input_frame,
                None, self._capture_device, self._durable_frames,
            )

    def _cleanup_temp_dir(self, *, preserve: bool, errors: list[str]) -> None:
        if self._temp_dir is None:
            return
        if preserve and any(part.exists() for part in self._parts):
            self._promote_recovery_parts(
                reason="attachment_failed", errors=errors
            )
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


def _capture_gaps_payload(gaps: tuple[LocalCaptureGap, ...] | list[LocalCaptureGap]) -> list[dict]:
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
    if not isinstance(value, dict) or value.get("schema") != _RECOVERY_SCHEMA:
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
    if directory.is_symlink() or not directory.is_dir() or _has_final_recovery_project(directory):
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
            destination = base / (
                f"Recovered-local-{stamp}-{uuid.uuid4().hex[:8]}"
            )
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
                    errors.append(
                        "Could not protect one recovered local-audio file."
                    )
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
                durable_frames=_metadata_nonnegative_int(metadata.get("durable_frames")),
                sample_rate=_metadata_nonnegative_int(metadata.get("sample_rate")),
                gaps=_recovered_capture_gaps(metadata),
                capture_device=_recovered_capture_device(metadata),
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
