"""Failure-safe supplemental capture for isolated host inputs.

Jamulus remains the live-audio authority.  This service records the selected
Core Audio device's first two inputs as local mono stems so a host can retain
separate instrument and vocal tracks without changing the network path.

Real-time layout: the sounddevice callback only copies each block into a
bounded queue; a dedicated writer thread does every disk write, so the audio
thread never touches libsndfile, the logger, or a lock shared with
finalization.  This is a separate PortAudio capture path.  WebJam records the
selected device metadata, but cannot prove that Jamulus is using the same
physical input or that both applications share an identical route.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np

LOGGER = logging.getLogger("webjam.local_capture")

# ~4-11 s of audio at typical Core Audio block sizes; overflow drops blocks
# and is reported once with a count instead of stalling the callback.
_QUEUE_MAX_BLOCKS = 512
# Manifests embed capture errors verbatim; keep the list bounded.
_ERROR_CAP = 20
# Finalization must never take ownership of libsndfile handles away from a
# writer thread that may still be inside ``write``.  Tests patch this short;
# production allows slow storage a generous drain window.
_WRITER_JOIN_TIMEOUT_S = 10.0
_SILENCE_CHUNK_FRAMES = 48_000
_DEFERRED_RECOVERY_GRACE_S = 0.25
_RECOVERY_METADATA = "webjam-local-capture.json"
_RECOVERY_SCHEMA = 1


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


@dataclass(frozen=True)
class _QueuedBlock:
    start_frame: int
    samples: np.ndarray

    @property
    def frame_count(self) -> int:
        return len(self.samples)


class LocalInputCapture:
    """Record two device channels to atomic mono WAV files."""

    def __init__(self, root: str | Path, *, device: int = -1,
                 samplerate: int = 48000, blocksize: int = 0) -> None:
        self.root = Path(root).expanduser()
        self.device = None if device < 0 else device
        self.samplerate = int(samplerate)
        self.blocksize = max(0, int(blocksize))
        self._stream = None
        self._writers: list[object] = []
        self._temp_dir: Path | None = None
        self._parts: list[Path] = []
        self._queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAX_BLOCKS)
        self._writer_thread: threading.Thread | None = None
        self._stop_requested = False
        self._dropped_blocks = 0
        self._status_counts: dict[str, int] = {}
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

            def callback(indata, _frames, _time_info, status) -> None:
                # Audio thread: bounded dict/int bookkeeping and one block
                # copy only — no disk, no logging, no finalization lock.
                if status:
                    key = str(status)
                    self._status_counts[key] = self._status_counts.get(key, 0) + 1
                frame_count = len(indata)
                start_frame = self._next_input_frame
                # Advance the source timeline even when the queue is full.
                # The writer can then replace the exact missing interval with
                # silence instead of pulling all later audio earlier in time.
                self._next_input_frame += frame_count
                try:
                    self._queue.put_nowait(
                        _QueuedBlock(start_frame, indata.copy())
                    )
                except queue.Full:
                    self._dropped_blocks += 1

            sd.check_input_settings(
                device=self.device, channels=2, samplerate=self.samplerate,
                dtype="float32",
            )
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
        except Exception as exc:  # noqa: BLE001
            self.abort()
            raise LocalCaptureError(
                f"Could not open two isolated host inputs at 48 kHz: {exc}"
            ) from exc

    def _write_recovery_checkpoint(self) -> None:
        if self._temp_dir is None:
            return
        from core.file_io import atomic_write_text

        payload = {
            "schema": _RECOVERY_SCHEMA,
            "pid": os.getpid(),
            "started_utc": self._started_utc,
            "sample_rate": self.samplerate,
            "channels": 2,
            "parts": [path.name for path in self._parts],
        }
        atomic_write_text(
            self._temp_dir / _RECOVERY_METADATA,
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )

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
        timeline_frame = 0
        while True:
            try:
                queued = self._queue.get(timeout=0.25)
            except queue.Empty:
                if self._stop_requested:
                    break
                continue
            if queued is None:
                break

            start_frame = queued.start_frame
            end_frame = start_frame + queued.frame_count
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
                    channel, start_frame, queued.samples[:, channel]
                )
            timeline_frame = max(timeline_frame, end_frame)

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
            except Exception as exc:  # noqa: BLE001
                self._record_error(
                    f"Local capture silence write failed on channel "
                    f"{channel + 1}: {exc}"
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
        except Exception as exc:  # noqa: BLE001
            self._record_error(
                f"Local capture write failed on channel {channel + 1}: {exc}"
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
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception as exc:  # noqa: BLE001
            self._record_error(f"Local capture did not close cleanly: {exc}")
        finally:
            self._stream = None
        self._final_input_frame = self._next_input_frame
        self._stop_requested = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass  # the timeout-get in the writer loop still sees the flag
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
        for message, count in self._status_counts.items():
            suffix = f" (×{count})" if count > 1 else ""
            errors.append(f"Audio device reported: {message}{suffix}")
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
                    except Exception as exc:  # noqa: BLE001
                        self._record_error(
                            f"Local recovery WAV could not be finalized: {exc}"
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
        except OSError as exc:
            errors.append(
                f"Recoverable recording files remain in {source_dir}: {exc}"
            )
            return source_dir
        try:
            os.chmod(recovered, 0o700)
        except OSError as exc:
            errors.append(f"Could not protect the recovery folder: {exc}")

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
                except OSError as exc:
                    errors.append(
                        f"Could not protect recovered audio {target.name}: {exc}"
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
            "files": [path.name for path in self._parts],
            "errors": list(errors),
        }
        try:
            from core.file_io import atomic_write_text

            atomic_write_text(
                recovered / "RECOVERY.json",
                json.dumps(recovery_payload, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )
        except OSError as exc:
            errors.append(f"Could not write the recovery report: {exc}")
        errors.append(
            f"Incomplete local audio was preserved visibly in {recovered}."
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
                        f"Recoverable capture parts remain in {recovery_dir}; "
                        "finalization may be retried after the writer stops."
                    )
                return LocalCaptureResult(
                    (), self._started_utc, self._started_monotonic,
                    max(0.0, self._stopped_monotonic - self._started_monotonic),
                    tuple(errors), self._snapshot_gaps(), self._next_input_frame,
                    recovery_dir, self._capture_device,
                )

            self._finalized = True
            for writer in self._writers:
                try:
                    writer.flush()
                    writer.close()
                except Exception as exc:  # noqa: BLE001
                    self._record_error(f"Local WAV could not be finalized: {exc}")
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
                    recovery_dir, self._capture_device,
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
                except OSError as exc:
                    attach_failed = True
                    errors.append(
                        f"Could not attach {final.name} to the take: {exc}"
                    )
                    continue
                try:
                    os.chmod(final, 0o600)
                except OSError as exc:
                    errors.append(
                        f"Could not protect isolated stem {final.name}: {exc}"
                    )
                final_files.append(final)
            self._cleanup_temp_dir(preserve=attach_failed, errors=errors)
            return LocalCaptureResult(
                tuple(final_files), self._started_utc, self._started_monotonic,
                max(0.0, self._stopped_monotonic - self._started_monotonic),
                tuple(errors), self._snapshot_gaps(), self._next_input_frame,
                None, self._capture_device,
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
        metadata: dict = {}
        errors: list[str] = []
        checkpoint = source / _RECOVERY_METADATA
        if checkpoint.is_file() and not checkpoint.is_symlink():
            try:
                value = json.loads(checkpoint.read_text(encoding="utf-8"))
                if isinstance(value, dict) and value.get("schema") == _RECOVERY_SCHEMA:
                    metadata = value
                else:
                    errors.append("The capture checkpoint was malformed.")
            except (OSError, json.JSONDecodeError):
                errors.append("The capture checkpoint was unreadable.")
        else:
            errors.append("The capture checkpoint was missing.")
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
                except OSError as exc:
                    errors.append(
                        f"Could not protect recovered audio {output.name}: {exc}"
                    )
        payload = {
            "schema": _RECOVERY_SCHEMA,
            "status": "recovered_partial",
            "reason": "startup_recovery",
            "source_working_folder": source.name,
            "started_utc": str(metadata.get("started_utc", ""))[:64],
            "sample_rate": metadata.get("sample_rate"),
            "files": [path.name for path in files],
            "errors": errors,
        }
        try:
            from core.file_io import atomic_write_text

            atomic_write_text(
                destination / "RECOVERY.json",
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                mode=0o600,
            )
        except OSError as exc:
            errors.append(f"Could not write the recovery report: {exc}")
        recovered_items.append(
            RecoveredLocalCapture(
                source_dir=source,
                recovery_dir=destination,
                files=tuple(files),
                errors=tuple(errors),
            )
        )
    return tuple(recovered_items)
