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


# Cap on per-channel level cache — Jamulus tops out around 100 channels in
# practice, so 1024 is generous.  Without this cap, a long-running session
# that sees many participants join+leave could grow the dict to thousands
# of stale entries.  Module-level so tests can import it.
_MAX_LEVEL_ENTRIES = 1024


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

    On ``start()``:
      1. Scans for a virtual loopback device (VB-CABLE / BlackHole) and opens
         it as the input stream if found.  Loopback metering gives accurate
         per-mix levels reflecting the Jamulus output.
      2. Falls back to the system default input device if no loopback is found.
      3. Falls back to deterministic synthetic pulses if sounddevice / numpy
         are unavailable or the stream fails to open.

    Per-channel level overrides (from JSON-RPC or UDP) are stored via
    ``set_level_override()`` and returned by ``get_level()`` for any channel
    that has a known RPC value.  Channel -1 is the global mix (from the
    hardware stream).
    """

    def __init__(
        self,
        settings: AppSettings,
        logger: Optional[logging.Logger] = None,
        *,
        device_index: Optional[int] = None,
    ):
        self.settings = settings
        self.logger = logger or logging.getLogger("webjam.audio")
        self._preferred_device_index = device_index  # explicit override
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
        # Populated once start() resolves a loopback device
        from core.audio_routing import AudioRoutingStatus
        self._routing: AudioRoutingStatus = AudioRoutingStatus()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        self.running = True

        if sd is not None and np is not None:
            device_idx = self._resolve_device()
            try:
                self._stream = sd.InputStream(
                    device=device_idx,
                    callback=self._audio_callback,
                    channels=1,
                    samplerate=self.settings.audio_samplerate,
                    blocksize=self.settings.audio_blocksize,
                    latency=self.settings.audio_latency,
                )
                self._stream.start()
                dev_name = self._diagnostics.input_device
                self._diagnostics.backend = "sounddevice"
                self._diagnostics.active = True
                self._diagnostics.message = f"live metering: {dev_name}"
                self.logger.info("Audio engine started — device: %s", dev_name)
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
            self._thread.join(timeout=3.0)

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
            # Cheap LRU-style trim: when we exceed the cap, drop the
            # oldest insertion (Python dicts preserve insertion order).
            if len(self._levels) > _MAX_LEVEL_ENTRIES:
                drop_keys = list(self._levels)[: -_MAX_LEVEL_ENTRIES]
                for k in drop_keys:
                    self._levels.pop(k, None)

    def clear_level_overrides(self) -> None:
        """Drop all per-channel level overrides (e.g. on Jamulus disconnect)."""
        with self._lock:
            self._levels.clear()

    def diagnostics(self) -> AudioDiagnostics:
        return self._diagnostics

    @property
    def routing_status(self):
        """Return the :class:`~core.audio_routing.AudioRoutingStatus` from startup."""
        return self._routing

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _resolve_device(self) -> Optional[int]:
        """Pick the best input device index, updating diagnostics in-place."""
        from core.audio_routing import scan_loopback_devices

        # Explicit override from constructor wins
        if self._preferred_device_index is not None:
            try:
                dev = sd.query_devices(self._preferred_device_index)
                self._diagnostics.input_device = dev["name"]
                return self._preferred_device_index
            except Exception:
                pass

        # User-chosen device from settings (set via the setup wizard) wins
        # over auto-detection.  -1 means "auto / system default".
        configured = getattr(self.settings, "audio_input_device_index", -1)
        if configured is not None and configured >= 0:
            try:
                dev = sd.query_devices(configured)
                self._diagnostics.input_device = dev["name"]
                return configured
            except Exception:
                self.logger.warning(
                    "Configured audio_input_device_index=%d not available; "
                    "falling back to auto-detect", configured
                )

        # A virtual loopback is meaningful only for the advanced audience
        # bridge. Talkback and video-only must respect the actual system
        # default instead of silently metering BlackHole/VB-CABLE.
        if getattr(self.settings, "webex_audio_mode", "talkback") == "audience_bridge":
            self._routing = scan_loopback_devices()
            if self._routing.ok and self._routing.loopback_device.has_input:
                dev = self._routing.loopback_device
                self._diagnostics.input_device = dev.name
                return dev.index

        # Fall back to system default
        self._diagnostics.input_device = "system default"
        return None

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
                self._levels[-1] = AudioLevel(
                    channel_id=-1, rms=rms, peak=peak, clipped=peak > 0.95
                )
            time.sleep(0.05)
