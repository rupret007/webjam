"""Conservative, privacy-safe classification for obvious feedback routes.

The guard deliberately consumes transient display names and returns no names.
It warns only for a clearly built-in microphone plus clearly built-in speaker
output. Unknown evidence never becomes a claim that a route is safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class AudioFeedbackRisk(str, Enum):
    """What WebJam can honestly infer before Jamulus opens the route."""

    UNKNOWN = "unknown"
    NOT_DETECTED = "not_detected"
    BUILTIN_MIC_AND_SPEAKERS = "builtin_mic_and_speakers"


@dataclass(frozen=True, slots=True)
class AudioFeedbackAssessment:
    risk: AudioFeedbackRisk = AudioFeedbackRisk.UNKNOWN

    @property
    def should_warn(self) -> bool:
        return self.risk is AudioFeedbackRisk.BUILTIN_MIC_AND_SPEAKERS


_MACHINE_PREFIXES = (
    "macbook air ",
    "macbook pro ",
    "mac mini ",
    "mac pro ",
    "imac ",
    "studio display ",
)
_ISOLATED_MARKERS = (
    "headphone",
    "headset",
    "airpods",
    "bluetooth",
    " usb ",
    " hdmi ",
    "displayport",
    "blackhole",
    "webjam bridge",
    "virtual",
    "vb cable",
    "audio interface",
    "thunderbolt",
)


def _normalized(value: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    return f" {words} " if words else ""


def _is_isolated_or_external(value: str) -> bool:
    return any(marker in value for marker in _ISOLATED_MARKERS)


def _is_builtin_input(value: str) -> bool:
    compact = value.strip()
    return (
        compact in {"built in microphone", "internal microphone"}
        or any(
            compact == f"{prefix}microphone".strip()
            for prefix in _MACHINE_PREFIXES
        )
    )


def _is_builtin_output(value: str) -> bool:
    compact = value.strip()
    return (
        compact
        in {
            "built in output",
            "built in speakers",
            "internal output",
            "internal speakers",
        }
        or any(
            compact in {
                f"{prefix}output".strip(),
                f"{prefix}speakers".strip(),
            }
            for prefix in _MACHINE_PREFIXES
        )
    )


def assess_audio_feedback_risk(
    input_name: str,
    output_name: str,
) -> AudioFeedbackAssessment:
    """Classify a transient Jamulus route without retaining device names."""

    input_value = _normalized(input_name)
    output_value = _normalized(output_name)
    if not input_value or not output_value:
        return AudioFeedbackAssessment()
    input_builtin = _is_builtin_input(input_value)
    output_builtin = _is_builtin_output(output_value)
    if input_builtin and output_builtin:
        return AudioFeedbackAssessment(
            AudioFeedbackRisk.BUILTIN_MIC_AND_SPEAKERS
        )
    if (
        _is_isolated_or_external(input_value)
        or _is_isolated_or_external(output_value)
    ):
        return AudioFeedbackAssessment(AudioFeedbackRisk.NOT_DETECTED)
    return AudioFeedbackAssessment()
