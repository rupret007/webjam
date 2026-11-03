"""Jamulus-native adaptive latency defaults and policy (pinned 3.12.2).

Jamulus owns live audio. WebJam only fills **unset** client ini keys documented in
``jamulussoftware/jamulus`` ``src/settings.cpp`` and adjusts policy from stats that
Jamulus actually exposes (see ``docs/adaptive-latency.md``).

Ini keys (client section):
    autojitbuf       — automatic network jitter buffer (flag 0/1)
    jitbuf           — network buffer size in blocks (1..20, ``MIN_NET_BUF_SIZE_NUM_BL``)
    prefsndcrdbufidx — sound-card frame factor 1/2/4 → 64/128/256 samples
                       (``FRAME_SIZE_FACTOR_*`` in ``src/global.h``)
    audioquality     — encoder quality 0..2 (``AQ_*``)

JSON-RPC (3.12.2 ``docs/JSON-RPC.md``):
    Notification ``jamulusclient/serverInfoReceived`` → ``params.pingtime`` (ms)
    Server method ``jamulusserver/getClients`` → ``clients[*].jitterBufferSize`` (blocks)

There is **no** JSON-RPC method to read overall delay or dropouts, and **no** RPC to
change buffer settings at runtime.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping
from xml.etree import ElementTree

from core.file_io import atomic_write_text

_logger = logging.getLogger("webjam.adaptive_latency")

PROVENANCE_FILENAME = "WebJam-adaptive-latency-provenance-v1.json"
CLIENT_SECTION = "client"

KEY_AUTOJITBUF = "autojitbuf"
KEY_JITBUF = "jitbuf"
KEY_PREFSNDCRDBUFIDX = "prefsndcrdbufidx"
KEY_AUDIOQUALITY = "audioquality"

# Jamulus 3.12.2 — src/global.h
FRAME_FACTOR_SAMPLES = {1: 64, 2: 128, 4: 256}
DEFAULT_FRAME_FACTOR = 2  # 128 samples
BUFFER_STEPS = (1, 2, 4)  # 64 / 128 / 256 samples

MIN_JITBUF_BLOCKS = 1
MAX_JITBUF_BLOCKS = 20
MIN_AUDIO_QUALITY = 0
MAX_AUDIO_QUALITY = 2

SOURCE_WEBJAM_DEFAULT = "webjam_default"
SOURCE_WEBJAM_ADAPTIVE = "webjam_adaptive"
SOURCE_MUSICIAN = "musician"


class DelayStatus(str, Enum):
    """Traffic-light band for estimated overall delay."""

    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LatencyIniDefaults:
    """Launch-time defaults WebJam may apply when keys are absent."""

    autojitbuf: bool = True
    prefsndcrdbufidx: int = DEFAULT_FRAME_FACTOR
    audioquality: int = 1
    jitbuf: int | None = None  # omitted when autojitbuf is on


@dataclass(frozen=True, slots=True)
class LatencyObservation:
    """One policy tick from measurable Jamulus signals."""

    ping_ms: float | None = None
    jitter_buffer_blocks: int | None = None
    dropout_hint: bool = False
    monotonic_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True, slots=True)
class ParticipantDelayPresentation:
    channel_id: int
    overall_delay_ms: float | None
    status: DelayStatus
    detail: str = ""


@dataclass
class AdaptiveLatencyPolicy:
    """Hysteresis policy for sound-card frame factors (64/128/256 samples)."""

    enabled: bool = True
    player_override: bool = False
    current_factor: int = DEFAULT_FRAME_FACTOR
    _high_ping_streak: int = 0
    _low_ping_streak: int = 0
    _last_change_at: float = 0.0
    min_change_interval_s: float = 30.0
    ping_raise_ms: float = 35.0
    ping_lower_ms: float = 18.0
    streak_required: int = 3

    def decide(self, observation: LatencyObservation) -> int | None:
        """Return a new ``prefsndcrdbufidx`` factor or ``None`` to hold."""

        if not self.enabled or self.player_override:
            return None
        ping = observation.ping_ms
        if ping is None or not _finite_positive(ping):
            self._high_ping_streak = 0
            self._low_ping_streak = 0
            return None
        if observation.dropout_hint:
            self._high_ping_streak = self.streak_required
            self._low_ping_streak = 0
        elif ping >= self.ping_raise_ms:
            self._high_ping_streak += 1
            self._low_ping_streak = 0
        elif ping <= self.ping_lower_ms:
            self._low_ping_streak += 1
            self._high_ping_streak = 0
        else:
            self._high_ping_streak = 0
            self._low_ping_streak = 0

        now = observation.monotonic_at
        if now - self._last_change_at < self.min_change_interval_s:
            return None

        idx = BUFFER_STEPS.index(self.current_factor)
        new_factor: int | None = None
        if self._high_ping_streak >= self.streak_required and idx < len(BUFFER_STEPS) - 1:
            new_factor = BUFFER_STEPS[idx + 1]
        elif self._low_ping_streak >= self.streak_required and idx > 0:
            new_factor = BUFFER_STEPS[idx - 1]

        if new_factor is None or new_factor == self.current_factor:
            return None
        self.current_factor = new_factor
        self._last_change_at = now
        self._high_ping_streak = 0
        self._low_ping_streak = 0
        return new_factor


def latency_defaults_for_settings(settings: object | None) -> LatencyIniDefaults:
    """Map WebJam settings to Jamulus ini defaults."""

    latency_hint = str(getattr(settings, "audio_latency", "low") or "low").strip().lower()
    if latency_hint in {"safe", "high", "stable"}:
        return LatencyIniDefaults(prefsndcrdbufidx=4, audioquality=0)
    if latency_hint in {"low", "fast"}:
        return LatencyIniDefaults(prefsndcrdbufidx=1, audioquality=2)
    return LatencyIniDefaults()


def read_client_ini_values(profile_xml: bytes) -> dict[str, str]:
    """Return raw client-section keys present in a Jamulus profile."""

    if not profile_xml.strip():
        return {}
    root = ElementTree.fromstring(profile_xml)
    if root.tag != CLIENT_SECTION:
        return {}
    return {
        child.tag: (child.text or "").strip()
        for child in root
        if child.tag and not list(child)
    }


def _ensure_client_root(profile_path: Path) -> ElementTree.ElementTree:
    if profile_path.exists():
        raw = profile_path.read_bytes()
        if raw.strip():
            root = ElementTree.fromstring(raw)
            if root.tag != CLIENT_SECTION:
                root = ElementTree.Element(CLIENT_SECTION)
            tree = ElementTree.ElementTree(root)
        else:
            tree = ElementTree.ElementTree(ElementTree.Element(CLIENT_SECTION))
    else:
        tree = ElementTree.ElementTree(ElementTree.Element(CLIENT_SECTION))
    return tree


def _set_client_key(root: ElementTree.Element, key: str, value: str) -> None:
    node = root.find(key)
    if node is None:
        node = ElementTree.SubElement(root, key)
    node.text = value


def apply_latency_defaults_to_profile(
    profile_path: Path,
    *,
    defaults: LatencyIniDefaults,
    provenance_path: Path | None = None,
) -> list[str]:
    """Fill only missing latency keys; return keys written."""

    profile_path = Path(profile_path)
    tree = _ensure_client_root(profile_path)
    root = tree.getroot()
    existing = read_client_ini_values(
        ElementTree.tostring(root, encoding="unicode").encode("utf-8")
    )
    provenance = _load_provenance(provenance_path or profile_path.parent / PROVENANCE_FILENAME)
    written: list[str] = []

    def maybe_set(key: str, value: str, source: str) -> None:
        if key in existing and existing[key] != "":
            if key not in provenance.get("keys", {}):
                provenance.setdefault("keys", {})[key] = {
                    "source": SOURCE_MUSICIAN,
                    "value": existing[key],
                }
            return
        _set_client_key(root, key, value)
        provenance.setdefault("keys", {})[key] = {
            "source": source,
            "value": value,
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        written.append(key)

    maybe_set(KEY_AUTOJITBUF, "1" if defaults.autojitbuf else "0", SOURCE_WEBJAM_DEFAULT)
    maybe_set(
        KEY_PREFSNDCRDBUFIDX,
        str(int(defaults.prefsndcrdbufidx)),
        SOURCE_WEBJAM_DEFAULT,
    )
    maybe_set(
        KEY_AUDIOQUALITY,
        str(int(defaults.audioquality)),
        SOURCE_WEBJAM_DEFAULT,
    )
    if defaults.jitbuf is not None and not defaults.autojitbuf:
        maybe_set(KEY_JITBUF, str(int(defaults.jitbuf)), SOURCE_WEBJAM_DEFAULT)

    payload = ElementTree.tostring(root, encoding="unicode")
    atomic_write_text(profile_path, payload, mode=0o600)
    _save_provenance(provenance_path or profile_path.parent / PROVENANCE_FILENAME, provenance)
    return written


def apply_frame_factor_to_profile(
    profile_path: Path,
    factor: int,
    *,
    provenance_path: Path | None = None,
) -> bool:
    """Persist a policy-driven frame factor; returns True if written."""

    if factor not in FRAME_FACTOR_SAMPLES:
        raise ValueError("factor must be 1, 2, or 4")
    profile_path = Path(profile_path)
    tree = _ensure_client_root(profile_path)
    root = tree.getroot()
    existing = read_client_ini_values(
        ElementTree.tostring(root, encoding="unicode").encode("utf-8")
    )
    provenance = _load_provenance(provenance_path or profile_path.parent / PROVENANCE_FILENAME)
    key = KEY_PREFSNDCRDBUFIDX
    prior = provenance.get("keys", {}).get(key, {})
    if prior.get("source") == SOURCE_MUSICIAN and existing.get(key):
        return False
    if str(existing.get(key, "")) == str(factor):
        return False
    _set_client_key(root, key, str(factor))
    provenance.setdefault("keys", {})[key] = {
        "source": SOURCE_WEBJAM_ADAPTIVE,
        "value": str(factor),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    atomic_write_text(
        profile_path,
        ElementTree.tostring(root, encoding="unicode"),
        mode=0o600,
    )
    _save_provenance(provenance_path or profile_path.parent / PROVENANCE_FILENAME, provenance)
    return True


def estimate_overall_delay_ms(
    *,
    ping_ms: float | None,
    frame_factor: int = DEFAULT_FRAME_FACTOR,
    jitter_buffer_blocks: int | None = None,
) -> float | None:
    """Estimate end-to-end delay when Jamulus does not publish overall delay."""

    if ping_ms is None or not _finite_positive(ping_ms):
        return None
    samples = FRAME_FACTOR_SAMPLES.get(frame_factor, 128)
    frame_ms = samples / 48.0  # 48 kHz, see SYSTEM_SAMPLE_RATE_HZ
    net_ms = 0.0
    if jitter_buffer_blocks is not None and jitter_buffer_blocks > 0:
        net_ms = (jitter_buffer_blocks * 64) / 48.0
    return float(ping_ms + frame_ms + net_ms)


def classify_delay(overall_delay_ms: float | None) -> DelayStatus:
    if overall_delay_ms is None:
        return DelayStatus.UNKNOWN
    if overall_delay_ms < 40.0:
        return DelayStatus.GOOD
    if overall_delay_ms <= 80.0:
        return DelayStatus.FAIR
    return DelayStatus.POOR


def _load_provenance(path: Path) -> dict[str, object]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"keys": {}}
    except (OSError, json.JSONDecodeError):
        return {"keys": {}}


def _save_provenance(path: Path, payload: Mapping[str, object]) -> None:
    atomic_write_text(
        Path(path),
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        mode=0o600,
    )


def _finite_positive(value: float) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number > 0 and number == number  # noqa: PLR0124 — NaN check
