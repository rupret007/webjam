"""
Audio routing detection and readiness status.

Detects virtual loopback audio devices (VB-CABLE on Windows, BlackHole on
macOS) that can be used to route Jamulus's output mix back to WebJam for
metering and to Webex for conference participants to hear the band. Device
detection does not configure operating-system, Jamulus, or Webex routing.

Detection is platform-aware and falls back gracefully when sounddevice is
unavailable or no loopback device is installed.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from typing import Optional

_logger = logging.getLogger("webjam.audio_routing")


# ---------------------------------------------------------------------------
# Known loopback device name fragments (case-insensitive substring match)
# ---------------------------------------------------------------------------
# Priority order — more specific names first so we prefer them when multiple
# virtual devices are installed.
_LOOPBACK_PATTERNS: list[str] = [
    # VB-Audio Virtual Cable (Windows) — very common
    "vb-audio virtual cable",
    "cable output",
    "cable input",
    "vb-audio",
    # BlackHole (macOS, open source)
    "blackhole 2ch",
    "blackhole 16ch",
    "blackhole 64ch",
    "blackhole",
    # Rogue Amoeba Loopback (macOS, paid)
    "loopback audio",
    "loopback",
    # JACK (Linux / macOS advanced users)
    "jack audio",
    "jackaudio",
    # Soundflower (older macOS, mostly replaced by BlackHole)
    "soundflower",
]


@dataclass
class LoopbackDevice:
    """A discovered virtual loopback audio device."""
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float

    @property
    def has_input(self) -> bool:
        return self.max_input_channels > 0

    @property
    def has_output(self) -> bool:
        return self.max_output_channels > 0


@dataclass
class AudioRoutingStatus:
    """Result of a loopback device scan."""
    loopback_device: Optional[LoopbackDevice] = None
    all_devices: list[LoopbackDevice] = field(default_factory=list)
    scan_error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.loopback_device is not None

    @property
    def device_name(self) -> str:
        if self.loopback_device:
            return self.loopback_device.name
        return ""

    @property
    def install_url(self) -> str:
        """Platform-appropriate installation URL for the user."""
        if sys.platform == "darwin":
            return "https://existential.audio/blackhole/"
        if sys.platform.startswith("win"):
            return "https://vb-audio.com/Cable/"
        return "https://jackaudio.org/"

    @property
    def install_hint(self) -> str:
        if sys.platform == "darwin":
            return "Install BlackHole (free) from existential.audio/blackhole"
        if sys.platform.startswith("win"):
            return "Install VB-CABLE (free) from vb-audio.com/Cable"
        return "Install JACK audio for loopback routing"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
def scan_loopback_devices() -> AudioRoutingStatus:
    """
    Query sounddevice for all connected audio devices and identify those that
    look like virtual loopback cables.

    Returns an :class:`AudioRoutingStatus` describing the best loopback
    device found (or none).  Never raises — all errors are captured in
    ``status.scan_error``.
    """
    try:
        import sounddevice as sd  # type: ignore
    except ImportError:
        _logger.debug("sounddevice not available — audio routing scan skipped")
        return AudioRoutingStatus(scan_error="sounddevice not installed")
    except Exception as exc:  # noqa: BLE001
        # sounddevice is installed but its native backend failed to load —
        # e.g. "PortAudio library not found" (OSError) on a box without the
        # PortAudio system lib.  The docstring promises this never raises, so
        # capture it as a scan error instead of letting it kill the caller's
        # background thread.
        _logger.warning("sounddevice import failed: %s", exc)
        return AudioRoutingStatus(scan_error=f"audio backend unavailable: {exc}")

    try:
        raw_devices = sd.query_devices()
    except Exception as exc:
        _logger.warning("audio device query failed: %s", exc)
        return AudioRoutingStatus(scan_error=str(exc))

    candidates: list[LoopbackDevice] = []
    for idx, dev in enumerate(raw_devices):
        name: str = dev.get("name", "")
        name_lower = name.lower()
        if any(pattern in name_lower for pattern in _LOOPBACK_PATTERNS):
            candidates.append(
                LoopbackDevice(
                    index=idx,
                    name=name,
                    max_input_channels=int(dev.get("max_input_channels", 0)),
                    max_output_channels=int(dev.get("max_output_channels", 0)),
                    default_samplerate=float(dev.get("default_samplerate", 48000)),
                )
            )

    # Prefer a device that has an input side (needed for metering)
    best: Optional[LoopbackDevice] = None
    for cand in candidates:
        if cand.has_input:
            best = cand
            break
    if best is None and candidates:
        best = candidates[0]

    if best:
        _logger.info("Loopback device: [%d] %s (in=%d out=%d)",
                     best.index, best.name, best.max_input_channels, best.max_output_channels)
    else:
        _logger.info("No loopback device found (checked %d total devices)", len(raw_devices))

    return AudioRoutingStatus(loopback_device=best, all_devices=candidates)


def find_device_index(name_fragment: str) -> Optional[int]:
    """Return the sounddevice index for a device whose name contains ``name_fragment``."""
    try:
        import sounddevice as sd  # type: ignore
        for idx, dev in enumerate(sd.query_devices()):
            if name_fragment.lower() in dev.get("name", "").lower():
                return idx
    except Exception:
        pass
    return None


def list_input_devices() -> list[dict]:
    """Return all input-capable audio devices as a list of dicts with index+name."""
    try:
        import sounddevice as sd  # type: ignore
        return [
            {
                "index": i,
                "name": d["name"],
                "channels": d.get("max_input_channels", 0),
                "default_samplerate": int(d.get("default_samplerate", 0) or 0),
            }
            for i, d in enumerate(sd.query_devices())
            if d.get("max_input_channels", 0) > 0
        ]
    except Exception:
        return []


def list_output_devices() -> list[dict]:
    """Return output-capable audio devices using stable names plus live indices."""
    try:
        import sounddevice as sd  # type: ignore
        return [
            {
                "index": i,
                "name": d["name"],
                "channels": d.get("max_output_channels", 0),
                "default_samplerate": int(d.get("default_samplerate", 0) or 0),
            }
            for i, d in enumerate(sd.query_devices())
            if d.get("max_output_channels", 0) > 0
        ]
    except Exception:
        return []
