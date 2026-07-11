"""Failure-safe supplemental capture for isolated host inputs.

Jamulus remains the live-audio authority.  This service records the selected
Core Audio device's first two inputs as local mono stems so a host can retain
separate instrument and vocal tracks without changing the network path.
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger("webjam.local_capture")


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
        self._lock = threading.Lock()
        self._errors: list[str] = []
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
                if status:
                    self._errors.append(str(status))
                try:
                    with self._lock:
                        for channel, writer in enumerate(self._writers):
                            writer.write(indata[:, channel])
                except Exception as exc:  # noqa: BLE001
                    LOGGER.exception("Local input capture callback failed")
                    self._errors.append(f"Local capture write failed: {exc}")

            sd.check_input_settings(
                device=self.device, channels=2, samplerate=self.samplerate,
                dtype="float32",
            )
            self._stream = sd.InputStream(
                device=self.device, channels=2, samplerate=self.samplerate,
                blocksize=self.blocksize, dtype="float32", callback=callback,
            )
            self._started_monotonic = time.monotonic()
            self._started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._stream.start()
        except Exception as exc:  # noqa: BLE001
            self.abort()
            raise LocalCaptureError(
                f"Could not open two isolated host inputs at 48 kHz: {exc}"
            ) from exc

    def stop_into(self, take_dir: str | Path) -> LocalCaptureResult:
        stopped = time.monotonic()
        errors = list(self._errors)
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Local capture did not close cleanly: {exc}")
        finally:
            self._stream = None
        with self._lock:
            for writer in self._writers:
                try:
                    writer.flush()
                    writer.close()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Local WAV could not be finalized: {exc}")
            self._writers.clear()

        destination = Path(take_dir)
        destination.mkdir(parents=True, exist_ok=True)
        final_files: list[Path] = []
        for part in self._parts:
            final = destination / part.name.removesuffix(".part")
            try:
                part.replace(final)
                final_files.append(final)
            except OSError as exc:
                errors.append(f"Could not attach {final.name} to the take: {exc}")
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        return LocalCaptureResult(
            tuple(final_files), self._started_utc, self._started_monotonic,
            max(0.0, stopped - self._started_monotonic), tuple(errors),
        )

    def abort(self) -> None:
        try:
            if self._stream is not None:
                self._stream.abort()
                self._stream.close()
        except Exception:  # noqa: BLE001
            LOGGER.debug("Local capture abort failed", exc_info=True)
        self._stream = None
        with self._lock:
            for writer in self._writers:
                try:
                    writer.close()
                except Exception:  # noqa: BLE001
                    pass
            self._writers.clear()
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
