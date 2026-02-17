from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

from core.models import AudioLevel
from core.settings import AppSettings

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    np = None

try:
    import sounddevice as sd  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    sd = None


@dataclass
class AudioDiagnostics:
    backend: str = "synthetic"
    samplerate: int = 48000
    blocksize: int = 0
    latency_mode: str = "low"
    input_device: str = "default"
    active: bool = False
    message: str = "synthetic fallback enabled"


class RealAudioEngine:
    """
    Real-time metering engine with deterministic fallback.

    - Uses sounddevice + numpy when available.
    - Falls back to synthetic low-amplitude sine pulses without randomness.
    """

    def __init__(self, settings: AppSettings, logger: Optional[logging.Logger] = None):
        self.settings = settings
        self.logger = logger or logging.getLogger("webjam.audio")
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self._levels: Dict[int, AudioLevel] = {}
        self._lock = threading.Lock()
        self._phase = 0.0
        self._diagnostics = AudioDiagnostics(
            samplerate=settings.audio_samplerate,
            blocksize=settings.audio_blocksize,
            latency_mode=settings.audio_latency,
        )

    def start(self) -> None:
        if self.running:
            return
        self.running = True

        if sd is not None and np is not None:
            try:
                self._stream = sd.InputStream(
                    callback=self._audio_callback,
                    channels=1,
                    samplerate=self.settings.audio_samplerate,
                    blocksize=self.settings.audio_blocksize,
                    latency=self.settings.audio_latency,
                )
                self._stream.start()
                self._diagnostics.backend = "sounddevice"
                self._diagnostics.active = True
                self._diagnostics.message = "live metering active"
                self.logger.info("Audio engine started with sounddevice backend")
                return
            except Exception as exc:  # pragma: no cover - hardware-dependent
                self.logger.warning("Falling back to synthetic metering: %s", exc)

        self._thread = threading.Thread(target=self._synthetic_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._thread:
            self._thread.join(timeout=1.5)

    def _audio_callback(self, indata, frames, time_info, status) -> None:  # pragma: no cover
        if np is None:
            return
        data = indata[:, 0]
        rms = float(np.sqrt(np.mean(np.square(data)))) if len(data) else 0.0
        peak = float(np.max(np.abs(data))) if len(data) else 0.0
        level = AudioLevel(channel_id=-1, rms=rms, peak=peak, clipped=peak >= 0.99)
        with self._lock:
            self._levels[-1] = level

    def _synthetic_loop(self) -> None:
        self._diagnostics.backend = "synthetic"
        self._diagnostics.active = True
        self._diagnostics.message = "deterministic fallback metering active"
        while self.running:
            self._phase += 0.15
            synthetic = (math.sin(self._phase) + 1.0) / 2.0
            rms = max(0.0, min(1.0, synthetic * 0.35))
            peak = max(0.0, min(1.0, rms + 0.1))
            with self._lock:
                self._levels[-1] = AudioLevel(channel_id=-1, rms=rms, peak=peak, clipped=peak > 0.95)
            time.sleep(0.05)

    def get_level(self, channel_id: int) -> float:
        with self._lock:
            if channel_id in self._levels:
                return self._levels[channel_id].rms
            return self._levels.get(-1, AudioLevel(channel_id=-1)).rms

    def set_level_override(self, channel_id: int, value: float) -> None:
        with self._lock:
            value = max(0.0, min(1.0, float(value)))
            self._levels[channel_id] = AudioLevel(
                channel_id=channel_id,
                rms=value,
                peak=min(1.0, value + 0.12),
                clipped=value > 0.9,
            )

    def diagnostics(self) -> AudioDiagnostics:
        return self._diagnostics

