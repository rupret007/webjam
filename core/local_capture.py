"""Failure-safe supplemental capture for isolated host inputs.

Jamulus remains the live-audio authority.  This service records the selected
Core Audio device's first two inputs as local mono stems so a host can retain
separate instrument and vocal tracks without changing the network path.

Real-time layout: the sounddevice callback only copies each block into a
bounded queue; a dedicated writer thread does every disk write, so the audio
thread never touches libsndfile, the logger, or a lock shared with
finalization.  Jamulus opens the same SSL device concurrently, so a stalled
callback here would surface as crackle in the live session.
"""
from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger("webjam.local_capture")

# ~4-11 s of audio at typical Core Audio block sizes; overflow drops blocks
# and is reported once with a count instead of stalling the callback.
_QUEUE_MAX_BLOCKS = 512
# Manifests embed capture errors verbatim; keep the list bounded.
_ERROR_CAP = 20


class LocalCaptureError(RuntimeError):
    """Raised when supplemental capture cannot start or finish safely."""


@dataclass(frozen=True)
class LocalCaptureResult:
    files: tuple[Path, ...]
    started_utc: str
    started_monotonic: float
    duration_s: float
    errors: tuple[str, ...] = ()


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
        self._finalize_lock = threading.Lock()
        self._finalized = False
        self._started_monotonic = 0.0
        self._started_utc = ""

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
                try:
                    self._queue.put_nowait(indata.copy())
                except queue.Full:
                    self._dropped_blocks += 1

            sd.check_input_settings(
                device=self.device, channels=2, samplerate=self.samplerate,
                dtype="float32",
            )
            self._stream = sd.InputStream(
                device=self.device, channels=2, samplerate=self.samplerate,
                blocksize=self.blocksize, dtype="float32", callback=callback,
            )
            self._writer_thread = threading.Thread(
                target=self._writer_loop, daemon=True, name="local-capture-writer",
            )
            self._writer_thread.start()
            self._started_monotonic = time.monotonic()
            self._started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._stream.start()
        except Exception as exc:  # noqa: BLE001
            self.abort()
            raise LocalCaptureError(
                f"Could not open two isolated host inputs at 48 kHz: {exc}"
            ) from exc

    def _writer_loop(self) -> None:
        while True:
            try:
                block = self._queue.get(timeout=0.25)
            except queue.Empty:
                if self._stop_requested:
                    return
                continue
            if block is None:
                return
            try:
                for channel, writer in enumerate(self._writers):
                    writer.write(block[:, channel])
            except Exception as exc:  # noqa: BLE001
                self._record_error(f"Local capture write failed: {exc}")

    def _record_error(self, message: str) -> None:
        count = self._error_counts.get(message, 0)
        self._error_counts[message] = count + 1
        if count == 0:
            LOGGER.error("%s", message)

    def _drain_writer(self) -> None:
        """Stop the stream, let the writer flush the queue, and join it."""
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception as exc:  # noqa: BLE001
            self._record_error(f"Local capture did not close cleanly: {exc}")
        finally:
            self._stream = None
        self._stop_requested = True
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass  # the timeout-get in the writer loop still sees the flag
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=10.0)
            if self._writer_thread.is_alive():
                self._record_error(
                    "Local capture writer did not finish flushing in time."
                )
            self._writer_thread = None

    def _collect_errors(self) -> list[str]:
        errors: list[str] = []
        for message, count in self._status_counts.items():
            suffix = f" (×{count})" if count > 1 else ""
            errors.append(f"Audio device reported: {message}{suffix}")
        for message, count in self._error_counts.items():
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

    def stop_into(self, take_dir: str | Path) -> LocalCaptureResult:
        stopped = time.monotonic()
        with self._finalize_lock:
            if self._finalized:
                return LocalCaptureResult(
                    (), self._started_utc, self._started_monotonic, 0.0,
                    ("Local capture was already finalized.",),
                )
            self._finalized = True
            self._drain_writer()
            for writer in self._writers:
                try:
                    writer.flush()
                    writer.close()
                except Exception as exc:  # noqa: BLE001
                    self._record_error(f"Local WAV could not be finalized: {exc}")
            self._writers.clear()
            errors = self._collect_errors()

            destination = Path(take_dir)
            destination.mkdir(parents=True, exist_ok=True)
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
                    final_files.append(final)
                except OSError as exc:
                    attach_failed = True
                    errors.append(
                        f"Could not attach {final.name} to the take: {exc}"
                    )
            self._cleanup_temp_dir(preserve=attach_failed, errors=errors)
            return LocalCaptureResult(
                tuple(final_files), self._started_utc, self._started_monotonic,
                max(0.0, stopped - self._started_monotonic), tuple(errors),
            )

    def _cleanup_temp_dir(self, *, preserve: bool, errors: list[str]) -> None:
        if self._temp_dir is None:
            return
        if preserve and any(part.exists() for part in self._parts):
            # Keep partial recordings: promote the hidden temp folder to a
            # visible recovery folder instead of deleting the audio.
            recovered = self.root / f"Recovered-local-{time.strftime('%Y%m%d-%H%M%S')}"
            try:
                self._temp_dir.replace(recovered)
                errors.append(
                    f"Unattached recording files were preserved in {recovered}."
                )
            except OSError:
                errors.append(
                    f"Unattached recording files remain in {self._temp_dir}."
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
                self._drain_writer()
            except Exception:  # noqa: BLE001
                LOGGER.debug("Local capture abort failed", exc_info=True)
            for writer in self._writers:
                try:
                    writer.close()
                except Exception:  # noqa: BLE001
                    pass
            self._writers.clear()
            if self._temp_dir is not None:
                shutil.rmtree(self._temp_dir, ignore_errors=True)
                self._temp_dir = None
