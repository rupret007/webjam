"""Immutable, frame-domain arrangement model for WebJam Studio.

The recording manifest and its WAV files are evidence, not an edit surface.
This module describes Studio choices using durable take/track/segment IDs and
integer project frames only.  It deliberately persists no source paths and
performs no file I/O; persistence, recovery, and history live at higher
layers.

Schema 2 is the first arrangement-capable Studio schema.  A default document
contains one region for every immutable source segment.  Region IDs are UUIDv5
values derived from the take, track, and segment identities, so rebuilding or
reconciling the same take is deterministic.  User-created IDs are ordinary
UUIDs and remain stable through immutable edits and undo/redo snapshots.
"""

from __future__ import annotations

import itertools
import math
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import TYPE_CHECKING, Any

from core.take_project import TakeProject

if TYPE_CHECKING:
    from core.song_project import SongProject


STUDIO_PROJECT_SCHEMA_VERSION = 2
STUDIO_SONG_PROJECT_SCHEMA_VERSION = 3
MAX_STUDIO_TRACKS = 512
MAX_STUDIO_REGIONS = 50_000
MAX_STUDIO_TAKE_LANES = 4_096
MAX_STUDIO_COMP_RANGES = 50_000
MAX_STUDIO_MARKERS = 10_000
MAX_STUDIO_CROSSFADES = 50_000
MAX_PROJECT_FRAMES = (1 << 62) - 1
MAX_GAIN = 4.0
MAX_STUDIO_TRACK_CHANNELS = 64
MAX_STUDIO_SENDS_PER_TRACK = 16
MAX_STUDIO_AUTOMATION_LANES_PER_TRACK = 3
MAX_STUDIO_AUTOMATION_POINTS_PER_LANE = 100_000
MAX_STUDIO_AUTOMATION_POINTS = 1_000_000
MAX_STUDIO_EFFECTS_PER_TRACK = 8
MAX_STUDIO_BUS_TRACKS = 64

_DEFAULT_ID_NAMESPACE = uuid.UUID("d1a65b4c-9f50-4f6b-9599-b117fa27572d")


class StudioProjectError(ValueError):
    """Raised when arrangement state is invalid or an edit is unsafe."""


class SnapMode(str, Enum):
    OFF = "off"
    TIME = "time"
    MARKERS = "markers"


class FadeCurve(str, Enum):
    LINEAR = "linear"
    EQUAL_POWER = "equal_power"
    S_CURVE = "s_curve"


class MarkerKind(str, Enum):
    MARKER = "marker"
    SECTION = "section"


class StudioTrackKind(str, Enum):
    """Signal-flow role for a standalone song-project track."""

    BACKING = "backing"
    AUDIO = "audio"
    BUS = "bus"
    MASTER = "master"


class StudioAutomationParameter(str, Enum):
    """A bounded set of sample-accurate channel-strip automation targets."""

    VOLUME = "volume"
    PAN = "pan"
    MUTE = "mute"


class StudioAutomationInterpolation(str, Enum):
    """How values change between exact integer-frame breakpoints."""

    LINEAR = "linear"
    HOLD = "hold"


class StudioEffectKind(str, Enum):
    """Small built-in effects implemented by WebJam's deterministic mixer."""

    HPF = "hpf"
    EQ = "eq"
    COMPRESSOR = "compressor"
    GATE = "gate"
    REVERB = "reverb"


def _canonical_uuid(value: object, field_name: str, *, optional: bool = False) -> str:
    text = str(value or "").strip()
    if optional and not text:
        return ""
    try:
        return str(uuid.UUID(text))
    except (AttributeError, TypeError, ValueError) as exc:
        raise StudioProjectError(f"{field_name} must be a UUID.") from exc


def _integer(
    value: object,
    field_name: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_PROJECT_FRAMES,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StudioProjectError(f"{field_name} must be an integer.")
    if value < minimum or value > maximum:
        raise StudioProjectError(
            f"{field_name} must be between {minimum} and {maximum}."
        )
    return value


def _positive_frames(value: object, field_name: str) -> int:
    return _integer(value, field_name, minimum=1)


def _timeline_frame(value: object, field_name: str) -> int:
    return _integer(
        value,
        field_name,
        minimum=-MAX_PROJECT_FRAMES,
        maximum=MAX_PROJECT_FRAMES,
    )


def _bounded_float(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StudioProjectError(f"{field_name} must be a finite number.")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise StudioProjectError(
            f"{field_name} must be between {minimum:g} and {maximum:g}."
        )
    return result


def _strict_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise StudioProjectError(f"{field_name} must be true or false.")
    return value


def _enum_value(enum_type, value: object, field_name: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise StudioProjectError(f"{field_name} must be one of: {choices}.") from exc


def _label(value: object, field_name: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise StudioProjectError(f"{field_name} must be text.")
    result = " ".join(value.split())
    if required and not result:
        raise StudioProjectError(f"{field_name} is required.")
    if len(result) > 160:
        raise StudioProjectError(f"{field_name} cannot exceed 160 characters.")
    return result


def _strict_keys(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    required: set[str],
    field_name: str,
) -> None:
    unknown = set(value).difference(allowed)
    if unknown:
        raise StudioProjectError(
            f"{field_name} contains unsupported fields: "
            + ", ".join(sorted(str(item) for item in unknown))
            + "."
        )
    missing = required.difference(value)
    if missing:
        raise StudioProjectError(
            f"{field_name} is missing required fields: "
            + ", ".join(sorted(missing))
            + "."
        )


def _mapping_items(value: object, field_name: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        raise StudioProjectError(f"{field_name} must be a list.")
    result: list[Mapping[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise StudioProjectError(f"{field_name} may contain only objects.")
        result.append(item)
    return tuple(result)


def _uuid_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise StudioProjectError(f"{field_name} must be a list.")
    result = tuple(_canonical_uuid(item, field_name) for item in value)
    if len(result) != len(set(result)):
        raise StudioProjectError(f"{field_name} contains duplicate IDs.")
    return result


def _ensure_unique(values: Iterable[str], field_name: str) -> None:
    items = tuple(values)
    if len(items) != len(set(items)):
        raise StudioProjectError(f"Studio document contains duplicate {field_name}.")


def _next_uuid(value: str | None, field_name: str) -> str:
    return _canonical_uuid(value or uuid.uuid4(), field_name)


def fade_gain(
    position: int,
    length: int,
    curve: FadeCurve | str,
    fade_in: bool = True,
) -> float:
    """Return a deterministic gain for an integer-frame fade position.

    ``length`` is the number of rendered frames, so a half-open frame range
    ``[0, length)`` reaches both exact endpoints.  Positions at or before zero
    return the starting gain and positions at or beyond ``length - 1`` return
    the ending gain.  A one-frame fade is the ending value.  This definition
    avoids a residual step at a region or crossfade boundary.
    """

    frame = _timeline_frame(position, "fade.position")
    frame_count = _positive_frames(length, "fade.length")
    mode = _enum_value(FadeCurve, curve, "fade.curve")
    is_fade_in = _strict_bool(fade_in, "fade.fade_in")
    if frame_count == 1:
        return 1.0 if is_fade_in else 0.0
    if frame <= 0:
        return 0.0 if is_fade_in else 1.0
    if frame >= frame_count - 1:
        return 1.0 if is_fade_in else 0.0

    progress = frame / (frame_count - 1)
    if mode is FadeCurve.LINEAR:
        return progress if is_fade_in else 1.0 - progress
    if mode is FadeCurve.EQUAL_POWER:
        angle = progress * math.pi / 2.0
        return math.sin(angle) if is_fade_in else math.cos(angle)
    smooth = progress * progress * (3.0 - 2.0 * progress)
    return smooth if is_fade_in else 1.0 - smooth


def crossfade_gains(
    position: int,
    length: int,
    curve: FadeCurve | str = FadeCurve.EQUAL_POWER,
) -> tuple[float, float]:
    """Return ``(outgoing, incoming)`` gains for one crossfade frame."""

    mode = _enum_value(FadeCurve, curve, "crossfade.curve")
    return (
        fade_gain(position, length, mode, fade_in=False),
        fade_gain(position, length, mode, fade_in=True),
    )


def _rounded_ratio(value: int, numerator: int, denominator: int) -> int:
    """Round one non-negative rational product without binary float drift."""

    if value < 0:
        return -_rounded_ratio(-value, numerator, denominator)
    quotient, remainder = divmod(value * numerator, denominator)
    # Deterministic half-up rounding is appropriate for frame boundaries and
    # does not inherit Python's alternating banker-rounding phase.
    return quotient + int(remainder * 2 >= denominator)


def _interval_is_covered(
    start_frame: int,
    end_frame: int,
    intervals: Iterable[tuple[int, int]],
) -> bool:
    """Return whether sorted/unsorted half-open intervals cover one range."""

    cursor = start_frame
    for start, end in sorted(intervals):
        if end <= cursor:
            continue
        if start > cursor:
            return False
        cursor = max(cursor, end)
        if cursor >= end_frame:
            return True
    return False


@dataclass(frozen=True)
class StudioAutomationPoint:
    """One exact integer-frame automation value."""

    frame: int
    value: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "frame",
            _timeline_frame(self.frame, "automation_point.frame"),
        )
        object.__setattr__(
            self,
            "value",
            _bounded_float(
                self.value,
                "automation_point.value",
                minimum=-MAX_GAIN,
                maximum=MAX_GAIN,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {"frame": self.frame, "value": self.value}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StudioAutomationPoint:
        _strict_keys(
            value,
            allowed={"frame", "value"},
            required={"frame", "value"},
            field_name="Studio automation point",
        )
        return cls(frame=value["frame"], value=value["value"])


@dataclass(frozen=True)
class StudioAutomationLane:
    """One parameter lane with deterministic interpolation.

    An enabled lane owns its parameter for the whole timeline: its first value
    extends backward and its last value extends forward. Volume replaces the
    static fader (trim remains separate), pan replaces static pan, and mute
    replaces the static mute switch.
    """

    lane_id: str
    parameter: StudioAutomationParameter
    points: tuple[StudioAutomationPoint, ...]
    interpolation: StudioAutomationInterpolation = (
        StudioAutomationInterpolation.LINEAR
    )
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "lane_id",
            _canonical_uuid(self.lane_id, "automation.lane_id"),
        )
        parameter = _enum_value(
            StudioAutomationParameter,
            self.parameter,
            "automation.parameter",
        )
        interpolation = _enum_value(
            StudioAutomationInterpolation,
            self.interpolation,
            "automation.interpolation",
        )
        try:
            points = tuple(self.points)
        except TypeError as exc:
            raise StudioProjectError(
                "Studio automation points must be a sequence."
            ) from exc
        if not points:
            raise StudioProjectError(
                "Studio automation requires at least one breakpoint."
            )
        if len(points) > MAX_STUDIO_AUTOMATION_POINTS_PER_LANE:
            raise StudioProjectError(
                "Studio automation contains too many breakpoints."
            )
        if any(not isinstance(item, StudioAutomationPoint) for item in points):
            raise StudioProjectError(
                "Studio automation points must be StudioAutomationPoint values."
            )
        frames = tuple(item.frame for item in points)
        if tuple(sorted(frames)) != frames or len(frames) != len(set(frames)):
            raise StudioProjectError(
                "Studio automation breakpoints must have unique ascending frames."
            )
        if parameter is StudioAutomationParameter.VOLUME:
            if any(not 0.0 <= item.value <= MAX_GAIN for item in points):
                raise StudioProjectError(
                    "Volume automation values must be between 0 and 4."
                )
        elif parameter is StudioAutomationParameter.PAN:
            if any(not -1.0 <= item.value <= 1.0 for item in points):
                raise StudioProjectError(
                    "Pan automation values must be between -1 and 1."
                )
        else:
            if interpolation is not StudioAutomationInterpolation.HOLD:
                raise StudioProjectError("Mute automation must use hold interpolation.")
            if any(item.value not in {0.0, 1.0} for item in points):
                raise StudioProjectError("Mute automation values must be 0 or 1.")
        object.__setattr__(self, "parameter", parameter)
        object.__setattr__(self, "interpolation", interpolation)
        object.__setattr__(self, "points", points)
        object.__setattr__(
            self,
            "enabled",
            _strict_bool(self.enabled, "automation.enabled"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "lane_id": self.lane_id,
            "parameter": self.parameter.value,
            "interpolation": self.interpolation.value,
            "enabled": self.enabled,
            "points": [item.to_dict() for item in self.points],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StudioAutomationLane:
        _strict_keys(
            value,
            allowed={
                "lane_id",
                "parameter",
                "interpolation",
                "enabled",
                "points",
            },
            required={
                "lane_id",
                "parameter",
                "interpolation",
                "enabled",
                "points",
            },
            field_name="Studio automation lane",
        )
        return cls(
            lane_id=value["lane_id"],
            parameter=value["parameter"],
            interpolation=value["interpolation"],
            enabled=value["enabled"],
            points=tuple(
                StudioAutomationPoint.from_dict(item)
                for item in _mapping_items(value["points"], "automation.points")
            ),
        )


@dataclass(frozen=True)
class StudioSend:
    """One bounded pre- or post-fader send to a Studio bus.

    The pre-fader tap follows trim and built-in inserts but precedes automated
    volume, pan, and mute. The post-fader tap follows the complete strip.
    """

    send_id: str
    target_bus_id: str
    gain: float = 1.0
    pre_fader: bool = False
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "send_id",
            _canonical_uuid(self.send_id, "send.send_id"),
        )
        object.__setattr__(
            self,
            "target_bus_id",
            _canonical_uuid(self.target_bus_id, "send.target_bus_id"),
        )
        object.__setattr__(
            self,
            "gain",
            _bounded_float(
                self.gain,
                "send.gain",
                minimum=0.0,
                maximum=MAX_GAIN,
            ),
        )
        object.__setattr__(
            self,
            "pre_fader",
            _strict_bool(self.pre_fader, "send.pre_fader"),
        )
        object.__setattr__(
            self,
            "enabled",
            _strict_bool(self.enabled, "send.enabled"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "send_id": self.send_id,
            "target_bus_id": self.target_bus_id,
            "gain": self.gain,
            "pre_fader": self.pre_fader,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StudioSend:
        _strict_keys(
            value,
            allowed={"send_id", "target_bus_id", "gain", "pre_fader", "enabled"},
            required={"send_id", "target_bus_id", "gain", "pre_fader", "enabled"},
            field_name="Studio send",
        )
        return cls(
            send_id=value["send_id"],
            target_bus_id=value["target_bus_id"],
            gain=value["gain"],
            pre_fader=value["pre_fader"],
            enabled=value["enabled"],
        )


@dataclass(frozen=True)
class StudioEffect:
    """One parameter-complete built-in effect in channel-strip order."""

    effect_id: str
    kind: StudioEffectKind
    enabled: bool = True
    hpf_frequency_hz: float = 80.0
    eq_frequency_hz: float = 1_000.0
    eq_gain_db: float = 0.0
    eq_q: float = 0.707
    compressor_threshold_db: float = -18.0
    compressor_ratio: float = 3.0
    compressor_attack_ms: float = 10.0
    compressor_release_ms: float = 100.0
    compressor_makeup_db: float = 0.0
    gate_threshold_db: float = -55.0
    gate_attack_ms: float = 2.0
    gate_release_ms: float = 120.0
    reverb_mix: float = 0.2
    reverb_decay: float = 0.4
    reverb_delay_ms: float = 45.0
    reverb_damping: float = 0.35

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "effect_id",
            _canonical_uuid(self.effect_id, "effect.effect_id"),
        )
        object.__setattr__(
            self,
            "kind",
            _enum_value(StudioEffectKind, self.kind, "effect.kind"),
        )
        object.__setattr__(
            self,
            "enabled",
            _strict_bool(self.enabled, "effect.enabled"),
        )
        bounds = {
            "hpf_frequency_hz": (10.0, 96_000.0),
            "eq_frequency_hz": (10.0, 96_000.0),
            "eq_gain_db": (-18.0, 18.0),
            "eq_q": (0.1, 12.0),
            "compressor_threshold_db": (-80.0, 0.0),
            "compressor_ratio": (1.0, 20.0),
            "compressor_attack_ms": (0.1, 500.0),
            "compressor_release_ms": (1.0, 5_000.0),
            "compressor_makeup_db": (-12.0, 24.0),
            "gate_threshold_db": (-100.0, 0.0),
            "gate_attack_ms": (0.1, 500.0),
            "gate_release_ms": (1.0, 5_000.0),
            "reverb_mix": (0.0, 1.0),
            "reverb_decay": (0.0, 0.95),
            "reverb_delay_ms": (5.0, 250.0),
            "reverb_damping": (0.0, 0.99),
        }
        for attribute, (minimum, maximum) in bounds.items():
            object.__setattr__(
                self,
                attribute,
                _bounded_float(
                    getattr(self, attribute),
                    f"effect.{attribute}",
                    minimum=minimum,
                    maximum=maximum,
                ),
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "effect_id": self.effect_id,
            "kind": self.kind.value,
            "enabled": self.enabled,
            "hpf_frequency_hz": self.hpf_frequency_hz,
            "eq_frequency_hz": self.eq_frequency_hz,
            "eq_gain_db": self.eq_gain_db,
            "eq_q": self.eq_q,
            "compressor_threshold_db": self.compressor_threshold_db,
            "compressor_ratio": self.compressor_ratio,
            "compressor_attack_ms": self.compressor_attack_ms,
            "compressor_release_ms": self.compressor_release_ms,
            "compressor_makeup_db": self.compressor_makeup_db,
            "gate_threshold_db": self.gate_threshold_db,
            "gate_attack_ms": self.gate_attack_ms,
            "gate_release_ms": self.gate_release_ms,
            "reverb_mix": self.reverb_mix,
            "reverb_decay": self.reverb_decay,
            "reverb_delay_ms": self.reverb_delay_ms,
            "reverb_damping": self.reverb_damping,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StudioEffect:
        expected = {
            "effect_id",
            "kind",
            "enabled",
            "hpf_frequency_hz",
            "eq_frequency_hz",
            "eq_gain_db",
            "eq_q",
            "compressor_threshold_db",
            "compressor_ratio",
            "compressor_attack_ms",
            "compressor_release_ms",
            "compressor_makeup_db",
            "gate_threshold_db",
            "gate_attack_ms",
            "gate_release_ms",
            "reverb_mix",
            "reverb_decay",
            "reverb_delay_ms",
            "reverb_damping",
        }
        _strict_keys(
            value,
            allowed=expected,
            required=expected,
            field_name="Studio effect",
        )
        return cls(**{key: value[key] for key in expected})


@dataclass(frozen=True)
class StudioTrack:
    """Persistent mix state for one durable project track."""

    track_id: str
    order: int = 0
    trim_gain: float = 1.0
    fader_gain: float = 1.0
    pan: float = 0.0
    muted: bool = False
    solo: bool = False
    export_included: bool = True
    name: str = ""
    kind: StudioTrackKind = StudioTrackKind.AUDIO
    channel_count: int = 1
    armed: bool = False
    input_monitoring: bool = False
    output_bus_id: str = ""
    sends: tuple[StudioSend, ...] = ()
    automation: tuple[StudioAutomationLane, ...] = ()
    effects: tuple[StudioEffect, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "track_id", _canonical_uuid(self.track_id, "track.track_id")
        )
        object.__setattr__(self, "order", _integer(self.order, "track.order"))
        object.__setattr__(
            self,
            "trim_gain",
            _bounded_float(
                self.trim_gain,
                "track.trim_gain",
                minimum=0.0,
                maximum=MAX_GAIN,
            ),
        )
        object.__setattr__(
            self,
            "fader_gain",
            _bounded_float(
                self.fader_gain,
                "track.fader_gain",
                minimum=0.0,
                maximum=MAX_GAIN,
            ),
        )
        object.__setattr__(
            self,
            "pan",
            _bounded_float(self.pan, "track.pan", minimum=-1.0, maximum=1.0),
        )
        object.__setattr__(self, "muted", _strict_bool(self.muted, "track.muted"))
        object.__setattr__(self, "solo", _strict_bool(self.solo, "track.solo"))
        object.__setattr__(
            self,
            "export_included",
            _strict_bool(self.export_included, "track.export_included"),
        )
        object.__setattr__(self, "name", _label(self.name, "track.name"))
        kind = _enum_value(StudioTrackKind, self.kind, "track.kind")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "channel_count",
            _integer(
                self.channel_count,
                "track.channel_count",
                minimum=1,
                maximum=MAX_STUDIO_TRACK_CHANNELS,
            ),
        )
        armed = _strict_bool(self.armed, "track.armed")
        monitoring = _strict_bool(
            self.input_monitoring,
            "track.input_monitoring",
        )
        if kind is not StudioTrackKind.AUDIO and (armed or monitoring):
            raise StudioProjectError(
                "Only audio tracks can be armed or input-monitored."
            )
        object.__setattr__(self, "armed", armed)
        object.__setattr__(self, "input_monitoring", monitoring)
        object.__setattr__(
            self,
            "output_bus_id",
            _canonical_uuid(
                self.output_bus_id,
                "track.output_bus_id",
                optional=True,
            ),
        )
        try:
            sends = tuple(self.sends)
            automation = tuple(self.automation)
            effects = tuple(self.effects)
        except TypeError as exc:
            raise StudioProjectError(
                "Studio track mixer settings must be sequences."
            ) from exc
        if len(sends) > MAX_STUDIO_SENDS_PER_TRACK:
            raise StudioProjectError("A Studio track contains too many sends.")
        if len(automation) > MAX_STUDIO_AUTOMATION_LANES_PER_TRACK:
            raise StudioProjectError(
                "A Studio track contains too many automation lanes."
            )
        if len(effects) > MAX_STUDIO_EFFECTS_PER_TRACK:
            raise StudioProjectError("A Studio track contains too many effects.")
        if any(not isinstance(item, StudioSend) for item in sends):
            raise StudioProjectError("Studio track sends contain an invalid value.")
        if any(not isinstance(item, StudioAutomationLane) for item in automation):
            raise StudioProjectError(
                "Studio track automation contains an invalid value."
            )
        if any(not isinstance(item, StudioEffect) for item in effects):
            raise StudioProjectError("Studio track effects contain an invalid value.")
        _ensure_unique((item.send_id for item in sends), "send IDs")
        _ensure_unique((item.lane_id for item in automation), "automation lane IDs")
        _ensure_unique((item.effect_id for item in effects), "effect IDs")
        _ensure_unique(
            (item.parameter.value for item in automation),
            "automation parameters",
        )
        _ensure_unique((item.kind.value for item in effects), "effect kinds")
        object.__setattr__(self, "sends", sends)
        object.__setattr__(self, "automation", automation)
        object.__setattr__(self, "effects", effects)

    @property
    def gain(self) -> float:
        """Compatibility alias for the Studio fader gain."""

        return self.fader_gain

    def to_dict(
        self,
        *,
        schema_version: int = STUDIO_PROJECT_SCHEMA_VERSION,
    ) -> dict[str, object]:
        schema = _integer(
            schema_version,
            "schema_version",
            minimum=STUDIO_PROJECT_SCHEMA_VERSION,
            maximum=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )
        result: dict[str, object] = {
            "track_id": self.track_id,
            "order": self.order,
            "trim_gain": self.trim_gain,
            "fader_gain": self.fader_gain,
            "pan": self.pan,
            "muted": self.muted,
            "solo": self.solo,
            "export_included": self.export_included,
        }
        if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION:
            result.update(
                {
                    "name": self.name,
                    "kind": self.kind.value,
                    "channel_count": self.channel_count,
                    "armed": self.armed,
                    "input_monitoring": self.input_monitoring,
                    "output_bus_id": self.output_bus_id,
                    "sends": [item.to_dict() for item in self.sends],
                    "automation": [item.to_dict() for item in self.automation],
                    "effects": [item.to_dict() for item in self.effects],
                }
            )
        return result

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        schema_version: int = STUDIO_PROJECT_SCHEMA_VERSION,
    ) -> StudioTrack:
        schema = _integer(
            schema_version,
            "schema_version",
            minimum=STUDIO_PROJECT_SCHEMA_VERSION,
            maximum=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )
        legacy_fields = {
            "track_id",
            "order",
            "trim_gain",
            "fader_gain",
            "pan",
            "muted",
            "solo",
            "export_included",
        }
        song_fields = {
            "name",
            "kind",
            "channel_count",
            "armed",
            "input_monitoring",
        }
        mixer_fields = {
            "output_bus_id",
            "sends",
            "automation",
            "effects",
        }
        expected = (
            legacy_fields | song_fields | mixer_fields
            if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
            else legacy_fields
        )
        required = (
            legacy_fields | song_fields
            if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
            else legacy_fields
        )
        _strict_keys(
            value,
            allowed=expected,
            required=required,
            field_name="Studio track",
        )
        return cls(
            track_id=value["track_id"],
            order=value["order"],
            trim_gain=value["trim_gain"],
            fader_gain=value["fader_gain"],
            pan=value["pan"],
            muted=value["muted"],
            solo=value["solo"],
            export_included=value["export_included"],
            name=(
                value["name"]
                if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                else ""
            ),
            kind=(
                value["kind"]
                if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                else StudioTrackKind.AUDIO
            ),
            channel_count=(
                value["channel_count"]
                if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                else 1
            ),
            armed=(
                value["armed"]
                if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                else False
            ),
            input_monitoring=(
                value["input_monitoring"]
                if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                else False
            ),
            output_bus_id=(
                value.get("output_bus_id", "")
                if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                else ""
            ),
            sends=(
                tuple(
                    StudioSend.from_dict(item)
                    for item in _mapping_items(
                        value.get("sends", ()),
                        "track.sends",
                    )
                )
                if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                else ()
            ),
            automation=(
                tuple(
                    StudioAutomationLane.from_dict(item)
                    for item in _mapping_items(
                        value.get("automation", ()),
                        "track.automation",
                    )
                )
                if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                else ()
            ),
            effects=(
                tuple(
                    StudioEffect.from_dict(item)
                    for item in _mapping_items(
                        value.get("effects", ()),
                        "track.effects",
                    )
                )
                if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                else ()
            ),
        )


@dataclass(frozen=True)
class StudioRegion:
    """One non-destructive reference to an immutable source segment."""

    region_id: str
    track_id: str
    source_take_id: str = ""
    source_track_id: str = ""
    source_segment_id: str = ""
    source_start_frame: int = 0
    source_frame_count: int = 0
    timeline_start_frame: int = 0
    timeline_frame_count: int = 0
    mapping_source_start_frame: int | None = None
    mapping_timeline_start_frame: int | None = None
    mapping_source_frame_count: int | None = None
    mapping_timeline_frame_count: int | None = None
    enabled: bool = True
    deleted: bool = False
    fade_in_frames: int = 0
    fade_out_frames: int = 0
    fade_in_curve: FadeCurve = FadeCurve.LINEAR
    fade_out_curve: FadeCurve = FadeCurve.LINEAR
    source_media_id: str = ""

    def __post_init__(self) -> None:
        for attribute in ("region_id", "track_id"):
            object.__setattr__(
                self,
                attribute,
                _canonical_uuid(getattr(self, attribute), f"region.{attribute}"),
            )
        for attribute in (
            "source_take_id",
            "source_track_id",
            "source_segment_id",
            "source_media_id",
        ):
            object.__setattr__(
                self,
                attribute,
                _canonical_uuid(
                    getattr(self, attribute),
                    f"region.{attribute}",
                    optional=True,
                ),
            )
        legacy_identity = (
            self.source_take_id,
            self.source_track_id,
            self.source_segment_id,
        )
        if any(legacy_identity) and not all(legacy_identity):
            raise StudioProjectError(
                "Region take, track, and segment source IDs must be provided together."
            )
        if bool(self.source_media_id) == bool(all(legacy_identity)):
            raise StudioProjectError(
                "Region requires exactly one media ID or take/track/segment source."
            )
        object.__setattr__(
            self,
            "source_start_frame",
            _integer(self.source_start_frame, "region.source_start_frame"),
        )
        object.__setattr__(
            self,
            "source_frame_count",
            _positive_frames(self.source_frame_count, "region.source_frame_count"),
        )
        object.__setattr__(
            self,
            "timeline_start_frame",
            _timeline_frame(self.timeline_start_frame, "region.timeline_start_frame"),
        )
        object.__setattr__(
            self,
            "timeline_frame_count",
            _positive_frames(self.timeline_frame_count, "region.timeline_frame_count"),
        )
        mapping_source_start = (
            self.source_start_frame
            if self.mapping_source_start_frame is None
            else _integer(
                self.mapping_source_start_frame,
                "region.mapping_source_start_frame",
            )
        )
        mapping_timeline_start = (
            self.timeline_start_frame
            if self.mapping_timeline_start_frame is None
            else _timeline_frame(
                self.mapping_timeline_start_frame,
                "region.mapping_timeline_start_frame",
            )
        )
        mapping_source_count = (
            self.source_frame_count
            if self.mapping_source_frame_count is None
            else _positive_frames(
                self.mapping_source_frame_count,
                "region.mapping_source_frame_count",
            )
        )
        mapping_timeline_count = (
            self.timeline_frame_count
            if self.mapping_timeline_frame_count is None
            else _positive_frames(
                self.mapping_timeline_frame_count,
                "region.mapping_timeline_frame_count",
            )
        )
        object.__setattr__(self, "mapping_source_start_frame", mapping_source_start)
        object.__setattr__(self, "mapping_timeline_start_frame", mapping_timeline_start)
        object.__setattr__(self, "mapping_source_frame_count", mapping_source_count)
        object.__setattr__(self, "mapping_timeline_frame_count", mapping_timeline_count)
        object.__setattr__(
            self, "enabled", _strict_bool(self.enabled, "region.enabled")
        )
        object.__setattr__(
            self, "deleted", _strict_bool(self.deleted, "region.deleted")
        )
        if self.deleted and self.enabled:
            raise StudioProjectError("A deleted Studio region cannot be enabled.")
        object.__setattr__(
            self,
            "fade_in_frames",
            _integer(self.fade_in_frames, "region.fade_in_frames"),
        )
        object.__setattr__(
            self,
            "fade_out_frames",
            _integer(self.fade_out_frames, "region.fade_out_frames"),
        )
        if self.fade_in_frames > self.timeline_frame_count:
            raise StudioProjectError("Region fade-in extends beyond the region.")
        if self.fade_out_frames > self.timeline_frame_count:
            raise StudioProjectError("Region fade-out extends beyond the region.")
        object.__setattr__(
            self,
            "fade_in_curve",
            _enum_value(FadeCurve, self.fade_in_curve, "region.fade_in_curve"),
        )
        object.__setattr__(
            self,
            "fade_out_curve",
            _enum_value(FadeCurve, self.fade_out_curve, "region.fade_out_curve"),
        )
        if self.source_start_frame + self.source_frame_count > MAX_PROJECT_FRAMES:
            raise StudioProjectError("Region source range is too large.")
        if mapping_source_start + mapping_source_count > MAX_PROJECT_FRAMES:
            raise StudioProjectError("Region source mapping is too large.")
        if self.timeline_start_frame + self.timeline_frame_count > MAX_PROJECT_FRAMES:
            raise StudioProjectError("Region timeline range is too large.")
        if mapping_timeline_start + mapping_timeline_count > MAX_PROJECT_FRAMES:
            raise StudioProjectError("Region timeline mapping is too large.")
        mapped_start = mapping_source_start + _rounded_ratio(
            self.timeline_start_frame - mapping_timeline_start,
            mapping_source_count,
            mapping_timeline_count,
        )
        mapped_end = mapping_source_start + _rounded_ratio(
            self.timeline_start_frame
            + self.timeline_frame_count
            - mapping_timeline_start,
            mapping_source_count,
            mapping_timeline_count,
        )
        if (
            abs(mapped_start - self.source_start_frame) > 1
            or abs(mapped_end - self.source_end_frame) > 1
        ):
            raise StudioProjectError(
                "Region trim boundaries do not follow its affine source mapping."
            )

    @property
    def timeline_end_frame(self) -> int:
        return self.timeline_start_frame + self.timeline_frame_count

    @property
    def source_end_frame(self) -> int:
        return self.source_start_frame + self.source_frame_count

    def source_boundary_for_timeline(self, timeline_frame: int) -> int:
        """Map an integer timeline boundary through the region's affine map."""

        frame = _timeline_frame(timeline_frame, "timeline_frame")
        delta = frame - int(self.mapping_timeline_start_frame)
        return int(self.mapping_source_start_frame) + _rounded_ratio(
            delta,
            int(self.mapping_source_frame_count),
            int(self.mapping_timeline_frame_count),
        )

    def timeline_boundary_for_source(self, source_frame: int) -> int:
        """Map an integer source boundary through the inverse affine map."""

        frame = _integer(source_frame, "source_frame")
        delta = frame - int(self.mapping_source_start_frame)
        return int(self.mapping_timeline_start_frame) + _rounded_ratio(
            delta,
            int(self.mapping_timeline_frame_count),
            int(self.mapping_source_frame_count),
        )

    def to_dict(
        self,
        *,
        schema_version: int = STUDIO_PROJECT_SCHEMA_VERSION,
    ) -> dict[str, object]:
        schema = _integer(
            schema_version,
            "schema_version",
            minimum=STUDIO_PROJECT_SCHEMA_VERSION,
            maximum=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )
        result: dict[str, object] = {
            "region_id": self.region_id,
            "track_id": self.track_id,
            "source_start_frame": self.source_start_frame,
            "source_frame_count": self.source_frame_count,
            "timeline_start_frame": self.timeline_start_frame,
            "timeline_frame_count": self.timeline_frame_count,
            "mapping_source_start_frame": self.mapping_source_start_frame,
            "mapping_timeline_start_frame": self.mapping_timeline_start_frame,
            "mapping_source_frame_count": self.mapping_source_frame_count,
            "mapping_timeline_frame_count": self.mapping_timeline_frame_count,
            "enabled": self.enabled,
            "deleted": self.deleted,
            "fade_in_frames": self.fade_in_frames,
            "fade_out_frames": self.fade_out_frames,
            "fade_in_curve": self.fade_in_curve.value,
            "fade_out_curve": self.fade_out_curve.value,
        }
        if schema == STUDIO_PROJECT_SCHEMA_VERSION:
            result.update(
                {
                    "source_take_id": self.source_take_id,
                    "source_track_id": self.source_track_id,
                    "source_segment_id": self.source_segment_id,
                }
            )
            # Preserve the exact schema-2 key insertion order.
            return {
                "region_id": result["region_id"],
                "track_id": result["track_id"],
                "source_take_id": result["source_take_id"],
                "source_track_id": result["source_track_id"],
                "source_segment_id": result["source_segment_id"],
                **{
                    key: item
                    for key, item in result.items()
                    if key
                    not in {
                        "region_id",
                        "track_id",
                        "source_take_id",
                        "source_track_id",
                        "source_segment_id",
                    }
                },
            }
        result["source_media_id"] = self.source_media_id
        return result

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        schema_version: int = STUDIO_PROJECT_SCHEMA_VERSION,
    ) -> StudioRegion:
        schema = _integer(
            schema_version,
            "schema_version",
            minimum=STUDIO_PROJECT_SCHEMA_VERSION,
            maximum=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )
        common = {
            "region_id",
            "track_id",
            "source_start_frame",
            "source_frame_count",
            "timeline_start_frame",
            "timeline_frame_count",
            "mapping_source_start_frame",
            "mapping_timeline_start_frame",
            "mapping_source_frame_count",
            "mapping_timeline_frame_count",
            "enabled",
            "deleted",
            "fade_in_frames",
            "fade_out_frames",
            "fade_in_curve",
            "fade_out_curve",
        }
        identity = (
            {"source_media_id"}
            if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
            else {"source_take_id", "source_track_id", "source_segment_id"}
        )
        expected = common | identity
        _strict_keys(
            value,
            allowed=expected,
            required=expected,
            field_name="Studio region",
        )
        return cls(
            region_id=value["region_id"],
            track_id=value["track_id"],
            source_take_id=(
                value["source_take_id"]
                if schema == STUDIO_PROJECT_SCHEMA_VERSION
                else ""
            ),
            source_track_id=(
                value["source_track_id"]
                if schema == STUDIO_PROJECT_SCHEMA_VERSION
                else ""
            ),
            source_segment_id=(
                value["source_segment_id"]
                if schema == STUDIO_PROJECT_SCHEMA_VERSION
                else ""
            ),
            source_start_frame=value["source_start_frame"],
            source_frame_count=value["source_frame_count"],
            timeline_start_frame=value["timeline_start_frame"],
            timeline_frame_count=value["timeline_frame_count"],
            mapping_source_start_frame=value["mapping_source_start_frame"],
            mapping_timeline_start_frame=value["mapping_timeline_start_frame"],
            mapping_source_frame_count=value["mapping_source_frame_count"],
            mapping_timeline_frame_count=value["mapping_timeline_frame_count"],
            enabled=value["enabled"],
            deleted=value["deleted"],
            fade_in_frames=value["fade_in_frames"],
            fade_out_frames=value["fade_out_frames"],
            fade_in_curve=value["fade_in_curve"],
            fade_out_curve=value["fade_out_curve"],
            source_media_id=(
                value["source_media_id"]
                if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                else ""
            ),
        )


@dataclass(frozen=True)
class StudioTakeLane:
    """One alternate performance source available to a destination track."""

    lane_id: str
    track_id: str
    source_take_id: str = ""
    source_track_id: str = ""
    name: str = ""
    order: int = 0
    region_ids: tuple[str, ...] = ()
    enabled: bool = True
    deleted: bool = False
    source_media_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "lane_id", _canonical_uuid(self.lane_id, "take_lane.lane_id")
        )
        object.__setattr__(
            self, "track_id", _canonical_uuid(self.track_id, "take_lane.track_id")
        )
        object.__setattr__(
            self,
            "source_take_id",
            _canonical_uuid(
                self.source_take_id,
                "take_lane.source_take_id",
                optional=True,
            ),
        )
        object.__setattr__(
            self,
            "source_track_id",
            _canonical_uuid(
                self.source_track_id,
                "take_lane.source_track_id",
                optional=True,
            ),
        )
        if bool(self.source_take_id) != bool(self.source_track_id):
            raise StudioProjectError(
                "Take-lane source take and track IDs must be provided together."
            )
        object.__setattr__(
            self,
            "source_media_id",
            _canonical_uuid(
                self.source_media_id,
                "take_lane.source_media_id",
                optional=True,
            ),
        )
        if self.source_media_id and self.source_take_id:
            raise StudioProjectError(
                "Take lane cannot mix media and take/track source identity."
            )
        object.__setattr__(self, "name", _label(self.name, "take_lane.name"))
        object.__setattr__(self, "order", _integer(self.order, "take_lane.order"))
        object.__setattr__(
            self,
            "region_ids",
            _uuid_tuple(self.region_ids, "take_lane.region_ids"),
        )
        object.__setattr__(
            self, "enabled", _strict_bool(self.enabled, "take_lane.enabled")
        )
        object.__setattr__(
            self, "deleted", _strict_bool(self.deleted, "take_lane.deleted")
        )
        if self.deleted and self.enabled:
            raise StudioProjectError("A deleted take lane cannot be enabled.")

    def to_dict(
        self,
        *,
        schema_version: int = STUDIO_PROJECT_SCHEMA_VERSION,
    ) -> dict[str, object]:
        schema = _integer(
            schema_version,
            "schema_version",
            minimum=STUDIO_PROJECT_SCHEMA_VERSION,
            maximum=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )
        result: dict[str, object] = {
            "lane_id": self.lane_id,
            "track_id": self.track_id,
            "name": self.name,
            "order": self.order,
            "region_ids": list(self.region_ids),
            "enabled": self.enabled,
            "deleted": self.deleted,
        }
        if schema == STUDIO_PROJECT_SCHEMA_VERSION:
            return {
                "lane_id": result["lane_id"],
                "track_id": result["track_id"],
                "source_take_id": self.source_take_id,
                "source_track_id": self.source_track_id,
                "name": result["name"],
                "order": result["order"],
                "region_ids": result["region_ids"],
                "enabled": result["enabled"],
                "deleted": result["deleted"],
            }
        result["source_media_id"] = self.source_media_id
        return result

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
        *,
        schema_version: int = STUDIO_PROJECT_SCHEMA_VERSION,
    ) -> StudioTakeLane:
        schema = _integer(
            schema_version,
            "schema_version",
            minimum=STUDIO_PROJECT_SCHEMA_VERSION,
            maximum=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )
        common = {
            "lane_id",
            "track_id",
            "name",
            "order",
            "region_ids",
            "enabled",
            "deleted",
        }
        identity = (
            {"source_media_id"}
            if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
            else {"source_take_id", "source_track_id"}
        )
        expected = common | identity
        _strict_keys(
            value,
            allowed=expected,
            required=expected,
            field_name="Studio take lane",
        )
        return cls(
            lane_id=value["lane_id"],
            track_id=value["track_id"],
            source_take_id=(
                value["source_take_id"]
                if schema == STUDIO_PROJECT_SCHEMA_VERSION
                else ""
            ),
            source_track_id=(
                value["source_track_id"]
                if schema == STUDIO_PROJECT_SCHEMA_VERSION
                else ""
            ),
            name=value["name"],
            order=value["order"],
            region_ids=_uuid_tuple(value["region_ids"], "take_lane.region_ids"),
            enabled=value["enabled"],
            deleted=value["deleted"],
            source_media_id=(
                value["source_media_id"]
                if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                else ""
            ),
        )


@dataclass(frozen=True)
class StudioCompRange:
    """A half-open project-frame selection from one take lane."""

    comp_range_id: str
    track_id: str
    lane_id: str
    timeline_start_frame: int
    frame_count: int
    fade_in_frames: int = 0
    fade_out_frames: int = 0
    enabled: bool = True
    deleted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "comp_range_id",
            _canonical_uuid(self.comp_range_id, "comp_range.comp_range_id"),
        )
        object.__setattr__(
            self, "track_id", _canonical_uuid(self.track_id, "comp_range.track_id")
        )
        object.__setattr__(
            self, "lane_id", _canonical_uuid(self.lane_id, "comp_range.lane_id")
        )
        object.__setattr__(
            self,
            "timeline_start_frame",
            _integer(self.timeline_start_frame, "comp_range.timeline_start_frame"),
        )
        object.__setattr__(
            self,
            "frame_count",
            _positive_frames(self.frame_count, "comp_range.frame_count"),
        )
        object.__setattr__(
            self,
            "fade_in_frames",
            _integer(self.fade_in_frames, "comp_range.fade_in_frames"),
        )
        object.__setattr__(
            self,
            "fade_out_frames",
            _integer(self.fade_out_frames, "comp_range.fade_out_frames"),
        )
        if self.fade_in_frames > self.frame_count:
            raise StudioProjectError("Comp fade-in extends beyond its range.")
        if self.fade_out_frames > self.frame_count:
            raise StudioProjectError("Comp fade-out extends beyond its range.")
        object.__setattr__(
            self, "enabled", _strict_bool(self.enabled, "comp_range.enabled")
        )
        object.__setattr__(
            self, "deleted", _strict_bool(self.deleted, "comp_range.deleted")
        )
        if self.deleted and self.enabled:
            raise StudioProjectError("A deleted comp range cannot be enabled.")
        if self.timeline_start_frame + self.frame_count > MAX_PROJECT_FRAMES:
            raise StudioProjectError("Comp range is too large.")

    @property
    def timeline_end_frame(self) -> int:
        return self.timeline_start_frame + self.frame_count

    def to_dict(self) -> dict[str, object]:
        return {
            "comp_range_id": self.comp_range_id,
            "track_id": self.track_id,
            "lane_id": self.lane_id,
            "timeline_start_frame": self.timeline_start_frame,
            "frame_count": self.frame_count,
            "fade_in_frames": self.fade_in_frames,
            "fade_out_frames": self.fade_out_frames,
            "enabled": self.enabled,
            "deleted": self.deleted,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StudioCompRange:
        _strict_keys(
            value,
            allowed={
                "comp_range_id",
                "track_id",
                "lane_id",
                "timeline_start_frame",
                "frame_count",
                "fade_in_frames",
                "fade_out_frames",
                "enabled",
                "deleted",
            },
            required={
                "comp_range_id",
                "track_id",
                "lane_id",
                "timeline_start_frame",
                "frame_count",
                "fade_in_frames",
                "fade_out_frames",
                "enabled",
                "deleted",
            },
            field_name="Studio comp range",
        )
        return cls(
            comp_range_id=value["comp_range_id"],
            track_id=value["track_id"],
            lane_id=value["lane_id"],
            timeline_start_frame=value["timeline_start_frame"],
            frame_count=value["frame_count"],
            fade_in_frames=value["fade_in_frames"],
            fade_out_frames=value["fade_out_frames"],
            enabled=value["enabled"],
            deleted=value["deleted"],
        )


@dataclass(frozen=True)
class StudioMarker:
    """An editable arrangement marker or named section."""

    marker_id: str
    start_frame: int
    label: str
    kind: MarkerKind = MarkerKind.MARKER
    end_frame: int | None = None
    deleted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "marker_id", _canonical_uuid(self.marker_id, "marker.marker_id")
        )
        object.__setattr__(
            self, "start_frame", _integer(self.start_frame, "marker.start_frame")
        )
        object.__setattr__(
            self, "label", _label(self.label, "marker.label", required=True)
        )
        kind = _enum_value(MarkerKind, self.kind, "marker.kind")
        object.__setattr__(self, "kind", kind)
        if self.end_frame is not None:
            object.__setattr__(
                self, "end_frame", _integer(self.end_frame, "marker.end_frame")
            )
        if kind is MarkerKind.MARKER and self.end_frame is not None:
            raise StudioProjectError("A point marker cannot have an end frame.")
        if kind is MarkerKind.SECTION and (
            self.end_frame is None or self.end_frame <= self.start_frame
        ):
            raise StudioProjectError("A section marker requires a later end frame.")
        object.__setattr__(
            self, "deleted", _strict_bool(self.deleted, "marker.deleted")
        )

    @property
    def name(self) -> str:
        return self.label

    @property
    def position_frame(self) -> int:
        return self.start_frame

    def to_dict(self) -> dict[str, object]:
        return {
            "marker_id": self.marker_id,
            "start_frame": self.start_frame,
            "label": self.label,
            "kind": self.kind.value,
            "end_frame": self.end_frame,
            "deleted": self.deleted,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StudioMarker:
        _strict_keys(
            value,
            allowed={
                "marker_id",
                "start_frame",
                "label",
                "kind",
                "end_frame",
                "deleted",
            },
            required={
                "marker_id",
                "start_frame",
                "label",
                "kind",
                "end_frame",
                "deleted",
            },
            field_name="Studio marker",
        )
        return cls(
            marker_id=value["marker_id"],
            start_frame=value["start_frame"],
            label=value["label"],
            kind=value["kind"],
            end_frame=value["end_frame"],
            deleted=value["deleted"],
        )


@dataclass(frozen=True)
class StudioCrossfade:
    """A validated overlap blend between two regions on one track."""

    crossfade_id: str
    left_region_id: str
    right_region_id: str
    start_frame: int
    frame_count: int
    curve: FadeCurve = FadeCurve.EQUAL_POWER
    deleted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "crossfade_id",
            _canonical_uuid(self.crossfade_id, "crossfade.crossfade_id"),
        )
        object.__setattr__(
            self,
            "left_region_id",
            _canonical_uuid(self.left_region_id, "crossfade.left_region_id"),
        )
        object.__setattr__(
            self,
            "right_region_id",
            _canonical_uuid(self.right_region_id, "crossfade.right_region_id"),
        )
        if self.left_region_id == self.right_region_id:
            raise StudioProjectError("A crossfade requires two different regions.")
        object.__setattr__(
            self,
            "start_frame",
            _timeline_frame(self.start_frame, "crossfade.start_frame"),
        )
        object.__setattr__(
            self,
            "frame_count",
            _positive_frames(self.frame_count, "crossfade.frame_count"),
        )
        object.__setattr__(
            self, "curve", _enum_value(FadeCurve, self.curve, "crossfade.curve")
        )
        object.__setattr__(
            self, "deleted", _strict_bool(self.deleted, "crossfade.deleted")
        )
        if self.start_frame + self.frame_count > MAX_PROJECT_FRAMES:
            raise StudioProjectError("Crossfade range is too large.")

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frame_count

    def to_dict(self) -> dict[str, object]:
        return {
            "crossfade_id": self.crossfade_id,
            "left_region_id": self.left_region_id,
            "right_region_id": self.right_region_id,
            "start_frame": self.start_frame,
            "frame_count": self.frame_count,
            "curve": self.curve.value,
            "deleted": self.deleted,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StudioCrossfade:
        _strict_keys(
            value,
            allowed={
                "crossfade_id",
                "left_region_id",
                "right_region_id",
                "start_frame",
                "frame_count",
                "curve",
                "deleted",
            },
            required={
                "crossfade_id",
                "left_region_id",
                "right_region_id",
                "start_frame",
                "frame_count",
                "curve",
                "deleted",
            },
            field_name="Studio crossfade",
        )
        return cls(
            crossfade_id=value["crossfade_id"],
            left_region_id=value["left_region_id"],
            right_region_id=value["right_region_id"],
            start_frame=value["start_frame"],
            frame_count=value["frame_count"],
            curve=value["curve"],
            deleted=value["deleted"],
        )


@dataclass(frozen=True)
class StudioCycleRange:
    start_frame: int
    end_frame: int
    enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "start_frame", _integer(self.start_frame, "cycle.start_frame")
        )
        object.__setattr__(
            self, "end_frame", _positive_frames(self.end_frame, "cycle.end_frame")
        )
        if self.end_frame <= self.start_frame:
            raise StudioProjectError("Cycle end must be later than its start.")
        object.__setattr__(self, "enabled", _strict_bool(self.enabled, "cycle.enabled"))

    def to_dict(self) -> dict[str, object]:
        return {
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StudioCycleRange:
        _strict_keys(
            value,
            allowed={"start_frame", "end_frame", "enabled"},
            required={"start_frame", "end_frame", "enabled"},
            field_name="Studio cycle range",
        )
        return cls(
            start_frame=value["start_frame"],
            end_frame=value["end_frame"],
            enabled=value["enabled"],
        )


@dataclass(frozen=True)
class StudioMaster:
    gain: float = 1.0
    limiter_enabled: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "gain",
            _bounded_float(self.gain, "master.gain", minimum=0.0, maximum=MAX_GAIN),
        )
        object.__setattr__(
            self,
            "limiter_enabled",
            _strict_bool(self.limiter_enabled, "master.limiter_enabled"),
        )

    def to_dict(self) -> dict[str, object]:
        return {"gain": self.gain, "limiter_enabled": self.limiter_enabled}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StudioMaster:
        _strict_keys(
            value,
            allowed={"gain", "limiter_enabled"},
            required={"gain", "limiter_enabled"},
            field_name="Studio master",
        )
        return cls(
            gain=value["gain"],
            limiter_enabled=value["limiter_enabled"],
        )


@dataclass(frozen=True)
class StudioDocument:
    """A complete immutable arrangement for a take or standalone song project."""

    session_id: str = ""
    take_id: str = ""
    project_sample_rate: int = 48_000
    tracks: tuple[StudioTrack, ...] = ()
    regions: tuple[StudioRegion, ...] = ()
    take_lanes: tuple[StudioTakeLane, ...] = ()
    comp_ranges: tuple[StudioCompRange, ...] = ()
    markers: tuple[StudioMarker, ...] = ()
    crossfades: tuple[StudioCrossfade, ...] = ()
    cycle_range: StudioCycleRange | None = None
    snap_mode: SnapMode = SnapMode.OFF
    master: StudioMaster = field(default_factory=StudioMaster)
    revision: int = 1
    schema_version: int = STUDIO_PROJECT_SCHEMA_VERSION
    project_id: str = ""
    _store_token: str | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        schema_version = _integer(
            self.schema_version,
            "schema_version",
            minimum=STUDIO_PROJECT_SCHEMA_VERSION,
            maximum=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
        )
        object.__setattr__(self, "schema_version", schema_version)
        if schema_version == STUDIO_PROJECT_SCHEMA_VERSION:
            object.__setattr__(
                self,
                "session_id",
                _canonical_uuid(self.session_id, "session_id"),
            )
            object.__setattr__(
                self,
                "take_id",
                _canonical_uuid(self.take_id, "take_id"),
            )
            project_id = _canonical_uuid(
                self.project_id,
                "project_id",
                optional=True,
            )
            if project_id:
                raise StudioProjectError(
                    "Schema-2 Studio documents cannot contain project_id."
                )
            object.__setattr__(self, "project_id", "")
        else:
            if self.session_id or self.take_id:
                raise StudioProjectError(
                    "Schema-3 Studio documents use project_id, not session/take IDs."
                )
            object.__setattr__(self, "session_id", "")
            object.__setattr__(self, "take_id", "")
            object.__setattr__(
                self,
                "project_id",
                _canonical_uuid(self.project_id, "project_id"),
            )
        rate = _positive_frames(self.project_sample_rate, "project_sample_rate")
        if rate > 768_000:
            raise StudioProjectError(
                "project_sample_rate is outside the supported range."
            )
        object.__setattr__(self, "project_sample_rate", rate)
        object.__setattr__(
            self, "revision", _positive_frames(self.revision, "revision")
        )
        if self._store_token is not None and (
            not isinstance(self._store_token, str)
            or len(self._store_token) != 64
            or any(
                character not in "0123456789abcdef" for character in self._store_token
            )
        ):
            raise StudioProjectError("Studio store token must be a lowercase SHA-256.")

        tracks = self._typed_tuple(
            self.tracks, StudioTrack, "tracks", MAX_STUDIO_TRACKS
        )
        regions = self._typed_tuple(
            self.regions, StudioRegion, "regions", MAX_STUDIO_REGIONS
        )
        lanes = self._typed_tuple(
            self.take_lanes,
            StudioTakeLane,
            "take lanes",
            MAX_STUDIO_TAKE_LANES,
        )
        comps = self._typed_tuple(
            self.comp_ranges,
            StudioCompRange,
            "comp ranges",
            MAX_STUDIO_COMP_RANGES,
        )
        markers = self._typed_tuple(
            self.markers, StudioMarker, "markers", MAX_STUDIO_MARKERS
        )
        crossfades = self._typed_tuple(
            self.crossfades,
            StudioCrossfade,
            "crossfades",
            MAX_STUDIO_CROSSFADES,
        )
        for name, values, identifier in (
            ("track IDs", tracks, "track_id"),
            ("region IDs", regions, "region_id"),
            ("take-lane IDs", lanes, "lane_id"),
            ("comp-range IDs", comps, "comp_range_id"),
            ("marker IDs", markers, "marker_id"),
            ("crossfade IDs", crossfades, "crossfade_id"),
        ):
            _ensure_unique((getattr(item, identifier) for item in values), name)

        track_by_id = {track.track_id: track for track in tracks}
        track_ids = set(track_by_id)
        if schema_version == STUDIO_PROJECT_SCHEMA_VERSION:
            for track in tracks:
                if (
                    track.name
                    or track.kind is not StudioTrackKind.AUDIO
                    or track.channel_count != 1
                    or track.armed
                    or track.input_monitoring
                    or track.output_bus_id
                    or track.sends
                    or track.automation
                    or track.effects
                ):
                    raise StudioProjectError(
                        "Schema-2 Studio tracks cannot contain song-track fields."
                    )
            if any(region.source_media_id for region in regions):
                raise StudioProjectError(
                    "Schema-2 Studio regions require take/track/segment sources."
                )
            if any(lane.source_media_id for lane in lanes):
                raise StudioProjectError(
                    "Schema-2 take lanes cannot contain media source IDs."
                )
        else:
            if any(not track.name for track in tracks):
                raise StudioProjectError(
                    "Schema-3 Studio tracks require a name."
                )
            orders = tuple(track.order for track in tracks)
            if len(orders) != len(set(orders)):
                raise StudioProjectError(
                    "Schema-3 Studio tracks require unique order values."
                )
            if any(
                region.source_take_id
                or region.source_track_id
                or region.source_segment_id
                or not region.source_media_id
                for region in regions
            ):
                raise StudioProjectError(
                    "Schema-3 Studio regions require only source_media_id."
                )
            if any(
                lane.source_take_id
                or lane.source_track_id
                or not lane.source_media_id
                for lane in lanes
            ):
                raise StudioProjectError(
                    "Schema-3 take lanes require only source_media_id."
                )
            bus_ids = {
                track.track_id
                for track in tracks
                if track.kind is StudioTrackKind.BUS
            }
            if len(bus_ids) > MAX_STUDIO_BUS_TRACKS:
                raise StudioProjectError(
                    "A Studio document contains too many bus tracks."
                )
            if (
                sum(
                    len(lane.points)
                    for track in tracks
                    for lane in track.automation
                )
                > MAX_STUDIO_AUTOMATION_POINTS
            ):
                raise StudioProjectError(
                    "A Studio document contains too many automation breakpoints."
                )
            masters = tuple(
                track
                for track in tracks
                if track.kind is StudioTrackKind.MASTER
            )
            if len(masters) > 1:
                raise StudioProjectError(
                    "Schema-3 Studio supports at most one master track."
                )
            nyquist = self.project_sample_rate / 2.0
            routing_edges: dict[str, set[str]] = {
                track.track_id: set() for track in tracks
            }
            for track in tracks:
                if (
                    track.kind in {StudioTrackKind.BUS, StudioTrackKind.MASTER}
                    and track.channel_count != 2
                ):
                    raise StudioProjectError(
                        "Studio bus and master tracks must be stereo."
                    )
                if track.kind is StudioTrackKind.MASTER:
                    if track.output_bus_id or track.sends:
                        raise StudioProjectError(
                            "The Studio master track cannot route to a bus or send."
                        )
                    if track.solo:
                        raise StudioProjectError(
                            "The Studio master track cannot be soloed."
                        )
                elif track.kind is StudioTrackKind.BUS and track.solo:
                    raise StudioProjectError(
                        "Studio bus solo is not available; solo source tracks."
                    )
                elif track.output_bus_id:
                    target = track_by_id.get(track.output_bus_id)
                    if target is None or target.kind is not StudioTrackKind.BUS:
                        raise StudioProjectError(
                            "A Studio output route must target a bus track."
                        )
                    if target.track_id == track.track_id:
                        raise StudioProjectError(
                            "A Studio track cannot route to itself."
                        )
                    routing_edges[track.track_id].add(target.track_id)
                for send in track.sends:
                    if send.target_bus_id not in bus_ids:
                        raise StudioProjectError(
                            "A Studio send must target a bus track."
                        )
                    if send.target_bus_id == track.track_id:
                        raise StudioProjectError(
                            "A Studio track cannot send to itself."
                        )
                    routing_edges[track.track_id].add(send.target_bus_id)
                if (
                    track.kind is not StudioTrackKind.BUS
                    and any(
                        effect.kind is StudioEffectKind.REVERB
                        for effect in track.effects
                    )
                ):
                    raise StudioProjectError(
                        "Shared reverb is available only on a Studio bus."
                    )
                for effect in track.effects:
                    if (
                        (
                            effect.kind is StudioEffectKind.HPF
                            and effect.hpf_frequency_hz >= nyquist
                        )
                        or (
                            effect.kind is StudioEffectKind.EQ
                            and effect.eq_frequency_hz >= nyquist
                        )
                    ):
                        raise StudioProjectError(
                            "Studio filter frequencies must stay below Nyquist."
                        )

            visiting: set[str] = set()
            visited: set[str] = set()

            def visit(track_id: str) -> None:
                if track_id in visiting:
                    raise StudioProjectError(
                        "Studio bus routing must not contain a cycle."
                    )
                if track_id in visited:
                    return
                visiting.add(track_id)
                for target_id in sorted(routing_edges[track_id]):
                    visit(target_id)
                visiting.remove(track_id)
                visited.add(track_id)

            for track_id in sorted(track_ids):
                visit(track_id)

        region_map = {region.region_id: region for region in regions}
        lane_map = {lane.lane_id: lane for lane in lanes}
        for region in regions:
            if region.track_id not in track_ids:
                raise StudioProjectError("Region references an unknown Studio track.")
            if (
                schema_version == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                and track_by_id[region.track_id].kind
                in {StudioTrackKind.BUS, StudioTrackKind.MASTER}
            ):
                raise StudioProjectError(
                    "Studio bus and master tracks cannot contain source regions."
                )
            if (
                schema_version == STUDIO_PROJECT_SCHEMA_VERSION
                and
                region.source_take_id == self.take_id
                and region.source_track_id not in track_ids
            ):
                raise StudioProjectError(
                    "Region references an unknown source track in this take."
                )
        owned_lane_regions: dict[str, str] = {}
        for lane in lanes:
            if lane.track_id not in track_ids:
                raise StudioProjectError(
                    "Take lane references an unknown Studio track."
                )
            if (
                schema_version == STUDIO_SONG_PROJECT_SCHEMA_VERSION
                and track_by_id[lane.track_id].kind
                in {StudioTrackKind.BUS, StudioTrackKind.MASTER}
            ):
                raise StudioProjectError(
                    "Studio bus and master tracks cannot contain take lanes."
                )
            if lane.region_ids and (
                schema_version == STUDIO_PROJECT_SCHEMA_VERSION
                and not lane.source_take_id
            ):
                raise StudioProjectError(
                    "A take lane with regions requires source take and track IDs."
                )
            for region_id in lane.region_ids:
                region = region_map.get(region_id)
                if region is None:
                    raise StudioProjectError("Take lane references an unknown region.")
                if region.track_id != lane.track_id:
                    raise StudioProjectError(
                        "Take lane region belongs to a different Studio track."
                    )
                if lane.source_take_id and (
                    region.source_take_id != lane.source_take_id
                    or region.source_track_id != lane.source_track_id
                ):
                    raise StudioProjectError(
                        "Take lane source IDs do not match its regions."
                    )
                if (
                    lane.source_media_id
                    and region.source_media_id != lane.source_media_id
                ):
                    raise StudioProjectError(
                        "Take lane media ID does not match its regions."
                    )
                if not lane.deleted:
                    owner = owned_lane_regions.setdefault(region_id, lane.lane_id)
                    if owner != lane.lane_id:
                        raise StudioProjectError(
                            "A region cannot belong to more than one active take lane."
                        )
        for region in regions:
            if (
                schema_version == STUDIO_PROJECT_SCHEMA_VERSION
                and
                not region.deleted
                and region.enabled
                and region.source_take_id != self.take_id
                and region.region_id not in owned_lane_regions
            ):
                raise StudioProjectError(
                    "An active cross-take region must belong to an active take lane."
                )
        active_comp_ranges: dict[str, list[StudioCompRange]] = {}
        for comp_range in comps:
            lane = lane_map.get(comp_range.lane_id)
            if comp_range.track_id not in track_ids or lane is None:
                raise StudioProjectError(
                    "Comp range references an unknown track or take lane."
                )
            if lane.track_id != comp_range.track_id:
                raise StudioProjectError(
                    "Comp range and take lane belong to different tracks."
                )
            if not comp_range.deleted and comp_range.enabled:
                if lane.deleted or not lane.enabled:
                    raise StudioProjectError(
                        "An active comp range requires an active take lane."
                    )
                coverage = (
                    (
                        region_map[region_id].timeline_start_frame,
                        region_map[region_id].timeline_end_frame,
                    )
                    for region_id in lane.region_ids
                    if not region_map[region_id].deleted
                    and region_map[region_id].enabled
                )
                if not _interval_is_covered(
                    comp_range.timeline_start_frame,
                    comp_range.timeline_end_frame,
                    coverage,
                ):
                    raise StudioProjectError(
                        "An active comp range must be covered by active lane regions."
                    )
                active_comp_ranges.setdefault(comp_range.track_id, []).append(
                    comp_range
                )
        for values in active_comp_ranges.values():
            ordered = sorted(
                values,
                key=lambda item: (
                    item.timeline_start_frame,
                    item.timeline_end_frame,
                    item.comp_range_id,
                ),
            )
            for left, right in itertools.pairwise(ordered):
                if right.timeline_start_frame < left.timeline_end_frame:
                    raise StudioProjectError("Active comp ranges cannot overlap.")

        for crossfade in crossfades:
            left = region_map.get(crossfade.left_region_id)
            right = region_map.get(crossfade.right_region_id)
            if left is None or right is None:
                raise StudioProjectError("Crossfade references an unknown region.")
            if crossfade.deleted:
                continue
            if left.deleted or right.deleted or not left.enabled or not right.enabled:
                raise StudioProjectError("An active crossfade requires active regions.")
            if left.track_id != right.track_id:
                raise StudioProjectError("Crossfade regions must share a track.")
            overlap_start = max(left.timeline_start_frame, right.timeline_start_frame)
            overlap_end = min(left.timeline_end_frame, right.timeline_end_frame)
            if (
                overlap_end <= overlap_start
                or crossfade.start_frame < overlap_start
                or crossfade.end_frame > overlap_end
            ):
                raise StudioProjectError(
                    "Crossfade must stay inside the regions' overlap."
                )

        if self.cycle_range is not None and not isinstance(
            self.cycle_range, StudioCycleRange
        ):
            raise StudioProjectError("cycle_range must be a StudioCycleRange or null.")
        snap_mode = _enum_value(SnapMode, self.snap_mode, "snap_mode")
        if not isinstance(self.master, StudioMaster):
            raise StudioProjectError("master must be a StudioMaster.")

        object.__setattr__(self, "tracks", tracks)
        object.__setattr__(self, "regions", regions)
        object.__setattr__(self, "take_lanes", lanes)
        object.__setattr__(self, "comp_ranges", comps)
        object.__setattr__(self, "markers", markers)
        object.__setattr__(self, "crossfades", crossfades)
        object.__setattr__(self, "snap_mode", snap_mode)

    @property
    def store_token(self) -> str | None:
        """Exact primary snapshot token retained by compatibility callers."""

        return self._store_token

    @staticmethod
    def _typed_tuple(values, expected_type, field_name: str, maximum: int):
        try:
            result = tuple(values)
        except TypeError as exc:
            raise StudioProjectError(
                f"Studio document {field_name} must be a sequence."
            ) from exc
        if len(result) > maximum:
            raise StudioProjectError(
                f"Studio document cannot contain more than {maximum} {field_name}."
            )
        if any(not isinstance(item, expected_type) for item in result):
            raise StudioProjectError(
                f"Studio document {field_name} contain an invalid value."
            )
        return result

    def state_for(self, track_id: str) -> StudioTrack:
        canonical = _canonical_uuid(track_id, "track_id")
        for track in self.tracks:
            if track.track_id == canonical:
                return track
        raise StudioProjectError("Track is not part of this Studio document.")

    def region_for(self, region_id: str) -> StudioRegion:
        canonical = _canonical_uuid(region_id, "region_id")
        for region in self.regions:
            if region.region_id == canonical:
                return region
        raise StudioProjectError("Region is not part of this Studio document.")

    def lane_for(self, lane_id: str) -> StudioTakeLane:
        canonical = _canonical_uuid(lane_id, "lane_id")
        for lane in self.take_lanes:
            if lane.lane_id == canonical:
                return lane
        raise StudioProjectError("Take lane is not part of this Studio document.")

    def _bumped(self, **changes: object) -> StudioDocument:
        if self.revision >= MAX_PROJECT_FRAMES:
            raise StudioProjectError("Studio document revision is exhausted.")
        return replace(self, revision=self.revision + 1, **changes)

    def update_track(self, track_id: str, **changes: object) -> StudioDocument:
        """Return a copy with one track's mix state changed.

        ``gain`` is accepted as a compatibility alias for ``fader_gain``.
        """

        allowed = {
            "order",
            "trim_gain",
            "fader_gain",
            "gain",
            "pan",
            "muted",
            "solo",
            "export_included",
        }
        if self.schema_version == STUDIO_SONG_PROJECT_SCHEMA_VERSION:
            allowed.update(
                {
                    "name",
                    "kind",
                    "channel_count",
                    "armed",
                    "input_monitoring",
                    "output_bus_id",
                    "sends",
                    "automation",
                    "effects",
                }
            )
        unknown = set(changes).difference(allowed)
        if unknown:
            raise StudioProjectError(
                "Unsupported Studio track setting: "
                + ", ".join(sorted(str(item) for item in unknown))
                + "."
            )
        if "gain" in changes and "fader_gain" in changes:
            raise StudioProjectError("Specify gain or fader_gain, not both.")
        values = dict(changes)
        if "gain" in values:
            values["fader_gain"] = values.pop("gain")
        original = self.state_for(track_id)
        updated = replace(original, **values)
        if updated == original:
            return self
        return self._bumped(
            tracks=tuple(
                updated if item.track_id == original.track_id else item
                for item in self.tracks
            )
        )

    def move_region(
        self, region_id: str, timeline_start_frame: int
    ) -> StudioDocument:
        original = self._editable_region(region_id)
        new_start = _timeline_frame(timeline_start_frame, "region.timeline_start_frame")
        delta = new_start - original.timeline_start_frame
        updated = replace(
            original,
            timeline_start_frame=new_start,
            mapping_timeline_start_frame=(
                int(original.mapping_timeline_start_frame) + delta
            ),
        )
        return self._replace_region(original, updated)

    def trim_region(
        self,
        region_id: str,
        *,
        source_start_frame: int | None = None,
        source_frame_count: int | None = None,
        timeline_start_frame: int | None = None,
        timeline_frame_count: int | None = None,
    ) -> StudioDocument:
        original = self._editable_region(region_id)
        source_changed = (
            source_start_frame is not None or source_frame_count is not None
        )
        timeline_changed = (
            timeline_start_frame is not None or timeline_frame_count is not None
        )
        if not source_changed and not timeline_changed:
            return self

        new_timeline_start = (
            original.timeline_start_frame
            if timeline_start_frame is None
            else _timeline_frame(timeline_start_frame, "region.timeline_start_frame")
        )
        if timeline_frame_count is None:
            new_timeline_end = original.timeline_end_frame
        else:
            new_timeline_end = new_timeline_start + _positive_frames(
                timeline_frame_count, "region.timeline_frame_count"
            )

        new_source_start = (
            original.source_start_frame
            if source_start_frame is None
            else _integer(source_start_frame, "region.source_start_frame")
        )
        if source_frame_count is None:
            new_source_end = original.source_end_frame
        else:
            new_source_end = new_source_start + _positive_frames(
                source_frame_count, "region.source_frame_count"
            )

        if timeline_changed and not source_changed:
            new_source_start = original.source_boundary_for_timeline(new_timeline_start)
            new_source_end = original.source_boundary_for_timeline(new_timeline_end)
        elif source_changed and not timeline_changed:
            new_timeline_start = original.timeline_boundary_for_source(new_source_start)
            new_timeline_end = original.timeline_boundary_for_source(new_source_end)

        updated = replace(
            original,
            source_start_frame=new_source_start,
            source_frame_count=new_source_end - new_source_start,
            timeline_start_frame=new_timeline_start,
            timeline_frame_count=new_timeline_end - new_timeline_start,
            fade_in_frames=min(
                original.fade_in_frames,
                new_timeline_end - new_timeline_start,
            ),
            fade_out_frames=min(
                original.fade_out_frames,
                new_timeline_end - new_timeline_start,
            ),
        )
        return self._replace_region(original, updated)

    def split_region(
        self,
        region_id: str,
        at_frame: int,
        *,
        right_region_id: str | None = None,
    ) -> StudioDocument:
        original = self._editable_region(region_id)
        split_frame = _timeline_frame(at_frame, "split.at_frame")
        if (
            not original.timeline_start_frame
            < split_frame
            < original.timeline_end_frame
        ):
            raise StudioProjectError("Split must be strictly inside the region.")
        left_timeline_count = split_frame - original.timeline_start_frame
        right_timeline_count = original.timeline_end_frame - split_frame
        source_boundary = original.source_boundary_for_timeline(split_frame)
        source_left_count = source_boundary - original.source_start_frame
        if not 0 < source_left_count < original.source_frame_count:
            raise StudioProjectError(
                "Split does not map to an interior source-frame boundary."
            )
        new_id = _next_uuid(right_region_id, "region.region_id")
        if any(item.region_id == new_id for item in self.regions):
            raise StudioProjectError("Split region ID is already in use.")
        left = replace(
            original,
            source_frame_count=source_left_count,
            timeline_frame_count=left_timeline_count,
            fade_in_frames=min(original.fade_in_frames, left_timeline_count),
            fade_out_frames=0,
            fade_out_curve=FadeCurve.LINEAR,
        )
        right = replace(
            original,
            region_id=new_id,
            source_start_frame=original.source_start_frame + source_left_count,
            source_frame_count=original.source_frame_count - source_left_count,
            timeline_start_frame=split_frame,
            timeline_frame_count=right_timeline_count,
            fade_in_frames=0,
            fade_in_curve=FadeCurve.LINEAR,
            fade_out_frames=min(original.fade_out_frames, right_timeline_count),
        )
        regions: list[StudioRegion] = []
        for item in self.regions:
            regions.append(left if item.region_id == original.region_id else item)
            if item.region_id == original.region_id:
                regions.append(right)
        lanes = tuple(
            replace(
                lane,
                region_ids=tuple(
                    child
                    for item in lane.region_ids
                    for child in (
                        (item, new_id) if item == original.region_id else (item,)
                    )
                ),
            )
            if original.region_id in lane.region_ids
            else lane
            for lane in self.take_lanes
        )
        crossfades = tuple(
            replace(
                crossfade,
                deleted=(
                    True
                    if not crossfade.deleted
                    and original.region_id
                    in {crossfade.left_region_id, crossfade.right_region_id}
                    and crossfade.start_frame < split_frame < crossfade.end_frame
                    else crossfade.deleted
                ),
                left_region_id=(
                    new_id
                    if crossfade.left_region_id == original.region_id
                    and crossfade.start_frame >= split_frame
                    else crossfade.left_region_id
                ),
                right_region_id=(
                    new_id
                    if crossfade.right_region_id == original.region_id
                    and crossfade.start_frame >= split_frame
                    else crossfade.right_region_id
                ),
            )
            for crossfade in self.crossfades
        )
        return self._bumped(
            regions=tuple(regions), take_lanes=lanes, crossfades=crossfades
        )

    def duplicate_region(
        self,
        region_id: str,
        *,
        new_region_id: str | None = None,
        timeline_start_frame: int | None = None,
    ) -> StudioDocument:
        original = self._editable_region(region_id)
        duplicate_id = _next_uuid(new_region_id, "region.region_id")
        if any(item.region_id == duplicate_id for item in self.regions):
            raise StudioProjectError("Duplicate region ID is already in use.")
        duplicate = replace(
            original,
            region_id=duplicate_id,
            timeline_start_frame=(
                original.timeline_start_frame
                if timeline_start_frame is None
                else _timeline_frame(
                    timeline_start_frame, "region.timeline_start_frame"
                )
            ),
            mapping_timeline_start_frame=(
                int(original.mapping_timeline_start_frame)
                if timeline_start_frame is None
                else int(original.mapping_timeline_start_frame)
                + _timeline_frame(timeline_start_frame, "region.timeline_start_frame")
                - original.timeline_start_frame
            ),
            enabled=True,
            deleted=False,
        )
        return self._bumped(regions=(*self.regions, duplicate))

    def set_region_enabled(self, region_id: str, enabled: bool) -> StudioDocument:
        original = self.region_for(region_id)
        if original.deleted:
            raise StudioProjectError("A deleted region cannot be enabled or disabled.")
        updated = replace(original, enabled=_strict_bool(enabled, "region.enabled"))
        if updated == original:
            return self
        crossfades = self.crossfades
        if not updated.enabled:
            crossfades = tuple(
                replace(item, deleted=True)
                if not item.deleted
                and original.region_id in {item.left_region_id, item.right_region_id}
                else item
                for item in crossfades
            )
        return self._replace_region(original, updated, crossfades=crossfades)

    def delete_region(self, region_id: str) -> StudioDocument:
        """Tombstone a region so reconciliation never recreates its source."""

        original = self.region_for(region_id)
        if original.deleted:
            return self
        updated = replace(original, enabled=False, deleted=True)
        crossfades = tuple(
            replace(item, deleted=True)
            if not item.deleted
            and original.region_id in {item.left_region_id, item.right_region_id}
            else item
            for item in self.crossfades
        )
        return self._replace_region(original, updated, crossfades=crossfades)

    def set_region_fades(
        self,
        region_id: str,
        *,
        fade_in_frames: int,
        fade_out_frames: int,
        fade_in_curve: FadeCurve | str | None = None,
        fade_out_curve: FadeCurve | str | None = None,
    ) -> StudioDocument:
        original = self._editable_region(region_id)
        updated = replace(
            original,
            fade_in_frames=fade_in_frames,
            fade_out_frames=fade_out_frames,
            fade_in_curve=(
                original.fade_in_curve if fade_in_curve is None else fade_in_curve
            ),
            fade_out_curve=(
                original.fade_out_curve if fade_out_curve is None else fade_out_curve
            ),
        )
        return self._replace_region(original, updated)

    def _editable_region(self, region_id: str) -> StudioRegion:
        region = self.region_for(region_id)
        if region.deleted:
            raise StudioProjectError("A deleted region cannot be edited.")
        return region

    def _replace_region(
        self,
        original: StudioRegion,
        updated: StudioRegion,
        *,
        crossfades: tuple[StudioCrossfade, ...] | None = None,
    ) -> StudioDocument:
        if updated == original and crossfades is None:
            return self
        return self._bumped(
            regions=tuple(
                updated if item.region_id == original.region_id else item
                for item in self.regions
            ),
            crossfades=self.crossfades if crossfades is None else crossfades,
        )

    def set_crossfade(
        self,
        left_region_id: str,
        right_region_id: str,
        *,
        start_frame: int,
        frame_count: int,
        curve: FadeCurve | str = FadeCurve.EQUAL_POWER,
        crossfade_id: str | None = None,
    ) -> StudioDocument:
        crossfade = StudioCrossfade(
            crossfade_id=_next_uuid(crossfade_id, "crossfade.crossfade_id"),
            left_region_id=left_region_id,
            right_region_id=right_region_id,
            start_frame=start_frame,
            frame_count=frame_count,
            curve=curve,
        )
        return self.upsert_crossfade(crossfade)

    def upsert_crossfade(self, crossfade: StudioCrossfade) -> StudioDocument:
        if not isinstance(crossfade, StudioCrossfade):
            raise StudioProjectError("crossfade must be a StudioCrossfade.")
        found = False
        values: list[StudioCrossfade] = []
        for item in self.crossfades:
            if item.crossfade_id == crossfade.crossfade_id:
                found = True
                values.append(crossfade)
            else:
                values.append(item)
        if not found:
            values.append(crossfade)
        if found and tuple(values) == self.crossfades:
            return self
        return self._bumped(crossfades=tuple(values))

    def remove_crossfade(self, crossfade_id: str) -> StudioDocument:
        canonical = _canonical_uuid(crossfade_id, "crossfade_id")
        for item in self.crossfades:
            if item.crossfade_id == canonical:
                if item.deleted:
                    return self
                return self._bumped(
                    crossfades=tuple(
                        replace(value, deleted=True)
                        if value.crossfade_id == canonical
                        else value
                        for value in self.crossfades
                    )
                )
        raise StudioProjectError("Crossfade is not part of this Studio document.")

    def upsert_marker(self, marker: StudioMarker) -> StudioDocument:
        if not isinstance(marker, StudioMarker):
            raise StudioProjectError("marker must be a StudioMarker.")
        found = False
        values: list[StudioMarker] = []
        for item in self.markers:
            if item.marker_id == marker.marker_id:
                found = True
                values.append(marker)
            else:
                values.append(item)
        if not found:
            values.append(marker)
        if found and tuple(values) == self.markers:
            return self
        return self._bumped(markers=tuple(values))

    def remove_marker(self, marker_id: str) -> StudioDocument:
        canonical = _canonical_uuid(marker_id, "marker_id")
        for marker in self.markers:
            if marker.marker_id == canonical:
                if marker.deleted:
                    return self
                return self._bumped(
                    markers=tuple(
                        replace(item, deleted=True)
                        if item.marker_id == canonical
                        else item
                        for item in self.markers
                    )
                )
        raise StudioProjectError("Marker is not part of this Studio document.")

    def set_cycle_range(self, cycle_range: StudioCycleRange | None) -> StudioDocument:
        if cycle_range is not None and not isinstance(cycle_range, StudioCycleRange):
            raise StudioProjectError("cycle_range must be a StudioCycleRange or null.")
        if cycle_range == self.cycle_range:
            return self
        return self._bumped(cycle_range=cycle_range)

    def set_snap_mode(self, snap_mode: SnapMode | str) -> StudioDocument:
        mode = _enum_value(SnapMode, snap_mode, "snap_mode")
        if mode is self.snap_mode:
            return self
        return self._bumped(snap_mode=mode)

    def set_master(self, master: StudioMaster) -> StudioDocument:
        if not isinstance(master, StudioMaster):
            raise StudioProjectError("master must be a StudioMaster.")
        if master == self.master:
            return self
        return self._bumped(master=master)

    def upsert_take_lane(self, lane: StudioTakeLane) -> StudioDocument:
        if not isinstance(lane, StudioTakeLane):
            raise StudioProjectError("lane must be a StudioTakeLane.")
        found = False
        values: list[StudioTakeLane] = []
        for item in self.take_lanes:
            if item.lane_id == lane.lane_id:
                found = True
                values.append(lane)
            else:
                values.append(item)
        if not found:
            values.append(lane)
        if found and tuple(values) == self.take_lanes:
            return self
        return self._bumped(take_lanes=tuple(values))

    def upsert_take_lane_with_regions(
        self,
        lane: StudioTakeLane,
        regions: Iterable[StudioRegion],
    ) -> StudioDocument:
        """Atomically add or restore one take lane and all regions it owns.

        A lane cannot be published before its referenced regions exist because
        every :class:`StudioDocument` snapshot validates as a whole.  Keeping
        this operation on the immutable model prevents UI code from creating
        a transient half-lane or bypassing durable-ID/source ownership checks.
        """

        if not isinstance(lane, StudioTakeLane):
            raise StudioProjectError("lane must be a StudioTakeLane.")
        incoming = tuple(regions)
        if any(not isinstance(item, StudioRegion) for item in incoming):
            raise StudioProjectError("Take-lane regions must be StudioRegion values.")
        incoming_by_id = {item.region_id: item for item in incoming}
        if len(incoming_by_id) != len(incoming):
            raise StudioProjectError("Take-lane regions contain duplicate IDs.")
        if set(lane.region_ids) != set(incoming_by_id):
            raise StudioProjectError(
                "Take-lane region IDs must exactly match the supplied regions."
            )
        if any(item.track_id != lane.track_id for item in incoming):
            raise StudioProjectError(
                "Take-lane regions do not match the lane's destination/source IDs."
            )
        if self.schema_version == STUDIO_PROJECT_SCHEMA_VERSION and any(
            item.source_take_id != lane.source_take_id
            or item.source_track_id != lane.source_track_id
            for item in incoming
        ):
            raise StudioProjectError(
                "Take-lane regions do not match the lane's destination/source IDs."
            )
        if self.schema_version == STUDIO_SONG_PROJECT_SCHEMA_VERSION and any(
            item.source_media_id != lane.source_media_id for item in incoming
        ):
            raise StudioProjectError(
                "Take-lane regions do not match the lane's destination/source IDs."
            )

        region_values: list[StudioRegion] = []
        found_region_ids: set[str] = set()
        for item in self.regions:
            replacement = incoming_by_id.get(item.region_id)
            if replacement is None:
                region_values.append(item)
            else:
                region_values.append(replacement)
                found_region_ids.add(item.region_id)
        region_values.extend(
            item for item in incoming if item.region_id not in found_region_ids
        )

        lane_values: list[StudioTakeLane] = []
        found_lane = False
        for item in self.take_lanes:
            if item.lane_id == lane.lane_id:
                lane_values.append(lane)
                found_lane = True
            else:
                lane_values.append(item)
        if not found_lane:
            lane_values.append(lane)

        if (
            tuple(region_values) == self.regions
            and tuple(lane_values) == self.take_lanes
        ):
            return self
        return self._bumped(
            regions=tuple(region_values),
            take_lanes=tuple(lane_values),
        )

    def remove_take_lane(self, lane_id: str) -> StudioDocument:
        lane = self.lane_for(lane_id)
        if lane.deleted:
            return self
        lanes = tuple(
            replace(item, enabled=False, deleted=True)
            if item.lane_id == lane.lane_id
            else item
            for item in self.take_lanes
        )
        comps = tuple(
            replace(item, enabled=False, deleted=True)
            if item.lane_id == lane.lane_id and not item.deleted
            else item
            for item in self.comp_ranges
        )
        owned_region_ids = set(lane.region_ids)
        regions = tuple(
            replace(item, enabled=False, deleted=True)
            if item.region_id in owned_region_ids and not item.deleted
            else item
            for item in self.regions
        )
        return self._bumped(
            regions=regions,
            take_lanes=lanes,
            comp_ranges=comps,
        )

    def select_comp_range(self, comp_range: StudioCompRange) -> StudioDocument:
        if not isinstance(comp_range, StudioCompRange):
            raise StudioProjectError("comp_range must be a StudioCompRange.")
        found = False
        values: list[StudioCompRange] = []
        for item in self.comp_ranges:
            if item.comp_range_id == comp_range.comp_range_id:
                found = True
                values.append(comp_range)
            else:
                values.append(item)
        if not found:
            values.append(comp_range)
        if found and tuple(values) == self.comp_ranges:
            return self
        return self._bumped(comp_ranges=tuple(values))

    upsert_comp_range = select_comp_range

    def remove_comp_range(self, comp_range_id: str) -> StudioDocument:
        canonical = _canonical_uuid(comp_range_id, "comp_range_id")
        for comp_range in self.comp_ranges:
            if comp_range.comp_range_id == canonical:
                if comp_range.deleted:
                    return self
                return self._bumped(
                    comp_ranges=tuple(
                        replace(item, enabled=False, deleted=True)
                        if item.comp_range_id == canonical
                        else item
                        for item in self.comp_ranges
                    )
                )
        raise StudioProjectError("Comp range is not part of this Studio document.")

    def set_comp_ranges(
        self,
        track_id: str,
        ranges: Iterable[StudioCompRange],
    ) -> StudioDocument:
        canonical = self.state_for(track_id).track_id
        incoming = tuple(ranges)
        if any(not isinstance(item, StudioCompRange) for item in incoming):
            raise StudioProjectError("Comp ranges must be StudioCompRange values.")
        if any(item.track_id != canonical for item in incoming):
            raise StudioProjectError("Comp range belongs to a different track.")
        incoming_by_id = {item.comp_range_id: item for item in incoming}
        if len(incoming_by_id) != len(incoming):
            raise StudioProjectError("Comp ranges contain duplicate IDs.")
        values: list[StudioCompRange] = []
        for item in self.comp_ranges:
            if item.track_id != canonical:
                values.append(item)
                continue
            replacement = incoming_by_id.get(item.comp_range_id)
            if replacement is not None:
                values.append(replacement)
            elif not item.deleted:
                values.append(replace(item, enabled=False, deleted=True))
            else:
                values.append(item)
        existing_ids = {item.comp_range_id for item in self.comp_ranges}
        values.extend(
            item for item in incoming if item.comp_range_id not in existing_ids
        )
        if tuple(values) == self.comp_ranges:
            return self
        return self._bumped(comp_ranges=tuple(values))

    def to_dict(self) -> dict[str, object]:
        common: dict[str, object] = {
            "schema_version": self.schema_version,
            "revision": self.revision,
            "project_sample_rate": self.project_sample_rate,
            "snap_mode": self.snap_mode.value,
            "tracks": [
                item.to_dict(schema_version=self.schema_version)
                for item in self.tracks
            ],
            "regions": [
                item.to_dict(schema_version=self.schema_version)
                for item in self.regions
            ],
            "take_lanes": [
                item.to_dict(schema_version=self.schema_version)
                for item in self.take_lanes
            ],
            "comp_ranges": [item.to_dict() for item in self.comp_ranges],
            "markers": [item.to_dict() for item in self.markers],
            "crossfades": [item.to_dict() for item in self.crossfades],
            "cycle_range": (
                self.cycle_range.to_dict() if self.cycle_range is not None else None
            ),
            "master": self.master.to_dict(),
        }
        if self.schema_version == STUDIO_PROJECT_SCHEMA_VERSION:
            # Keep the schema-2 key set and insertion order exactly stable.
            return {
                "schema_version": common["schema_version"],
                "revision": common["revision"],
                "session_id": self.session_id,
                "take_id": self.take_id,
                "project_sample_rate": common["project_sample_rate"],
                "snap_mode": common["snap_mode"],
                "tracks": common["tracks"],
                "regions": common["regions"],
                "take_lanes": common["take_lanes"],
                "comp_ranges": common["comp_ranges"],
                "markers": common["markers"],
                "crossfades": common["crossfades"],
                "cycle_range": common["cycle_range"],
                "master": common["master"],
            }
        return {
            "schema_version": common["schema_version"],
            "revision": common["revision"],
            "project_id": self.project_id,
            "project_sample_rate": common["project_sample_rate"],
            "snap_mode": common["snap_mode"],
            "tracks": common["tracks"],
            "regions": common["regions"],
            "take_lanes": common["take_lanes"],
            "comp_ranges": common["comp_ranges"],
            "markers": common["markers"],
            "crossfades": common["crossfades"],
            "cycle_range": common["cycle_range"],
            "master": common["master"],
        }


def _default_region_id(take_id: str, track_id: str, segment_id: str) -> str:
    return str(
        uuid.uuid5(
            _DEFAULT_ID_NAMESPACE,
            f"base-region:{take_id}:{track_id}:{segment_id}",
        )
    )


def _default_marker(project, marker) -> StudioMarker:
    return StudioMarker(
        marker_id=marker.marker_id,
        start_frame=round(marker.position_s * project.project_sample_rate),
        label=marker.label,
    )


def _default_region(project: TakeProject, track, segment) -> StudioRegion:
    drift_scale = 1.0 + float(track.alignment.drift_ppm) / 1_000_000.0
    if not math.isfinite(drift_scale) or drift_scale <= 0.0:
        raise StudioProjectError(
            f"Track {track.track_id} has an invalid drift transform."
        )
    timeline_count = round(
            segment.frame_count
            / segment.sample_rate
            * drift_scale
            * project.project_sample_rate
        )
    if timeline_count <= 0:
        raise StudioProjectError(
            f"Segment {segment.segment_id} has no project-frame duration."
        )
    offset_frames = round(track.alignment.effective_offset_s * project.project_sample_rate)
    return StudioRegion(
        region_id=_default_region_id(
            project.take_id, track.track_id, segment.segment_id
        ),
        track_id=track.track_id,
        source_take_id=project.take_id,
        source_track_id=track.track_id,
        source_segment_id=segment.segment_id,
        source_start_frame=0,
        source_frame_count=segment.frame_count,
        timeline_start_frame=segment.project_start_frame + offset_frames,
        timeline_frame_count=timeline_count,
    )


def default_studio_document(project: TakeProject) -> StudioDocument:
    """Return deterministic, non-destructive defaults for a schema-v2 take."""

    if not isinstance(project, TakeProject):
        raise StudioProjectError("Default Studio state requires a TakeProject.")
    ordered_tracks = sorted(
        project.tracks, key=lambda item: (item.order, item.track_id)
    )
    tracks = tuple(
        StudioTrack(
            track_id=track.track_id,
            order=index,
            export_included=track.selected_for_export,
        )
        for index, track in enumerate(ordered_tracks)
    )
    regions = tuple(
        _default_region(project, track, segment)
        for track in ordered_tracks
        for segment in sorted(
            track.segments,
            key=lambda item: (item.project_start_frame, item.segment_id),
        )
        if segment.frame_count > 0
    )
    markers = tuple(
        _default_marker(project, marker)
        for marker in sorted(
            project.markers, key=lambda item: (item.position_s, item.marker_id)
        )
    )
    return StudioDocument(
        session_id=project.session_id,
        take_id=project.take_id,
        project_sample_rate=project.project_sample_rate,
        tracks=tracks,
        regions=regions,
        markers=markers,
    )


def _song_backing_track_id(project_id: str) -> str:
    return str(
        uuid.uuid5(
            _DEFAULT_ID_NAMESPACE,
            f"song-backing-track:{project_id}",
        )
    )


def _song_backing_region_id(project_id: str, media_id: str) -> str:
    return str(
        uuid.uuid5(
            _DEFAULT_ID_NAMESPACE,
            f"song-backing-region:{project_id}:{media_id}",
        )
    )


def default_song_studio_document(project: SongProject) -> StudioDocument:
    """Return deterministic schema-3 arrangement defaults for a song project.

    The Studio snapshot deliberately retains only durable project/media IDs
    and exact frame mappings.  Checksums, relative bundle names, and all other
    media inventory remain owned by :class:`core.song_project.SongProject`.
    """

    # Keep the legacy take model import surface independent from song projects.
    # The local import also prevents an accidental circular dependency if the
    # song-project persistence layer later refers to Studio documents.
    from core.song_project import SongProject

    if not isinstance(project, SongProject):
        raise StudioProjectError(
            "Default song Studio state requires a SongProject."
        )

    backing = (
        project.media_by_id(project.backing_media_id)
        if project.backing_media_id is not None
        else None
    )
    track_offset = 1 if backing is not None else 0
    tracks: list[StudioTrack] = []
    regions: list[StudioRegion] = []
    if backing is not None:
        backing_track_id = _song_backing_track_id(project.project_id)
        if any(item.track_id == backing_track_id for item in project.tracks):
            raise StudioProjectError(
                "Backing-track ID collides with a project track ID."
            )
        timeline_count = _rounded_ratio(
            backing.frame_count,
            project.project_sample_rate,
            backing.sample_rate,
        )
        if timeline_count <= 0:
            raise StudioProjectError(
                "Backing media has no project-frame duration."
            )
        tracks.append(
            StudioTrack(
                track_id=backing_track_id,
                order=0,
                name="Backing Track",
                kind=StudioTrackKind.BACKING,
                channel_count=backing.channels,
            )
        )
        regions.append(
            StudioRegion(
                region_id=_song_backing_region_id(
                    project.project_id,
                    backing.media_id,
                ),
                track_id=backing_track_id,
                source_media_id=backing.media_id,
                source_start_frame=0,
                source_frame_count=backing.frame_count,
                timeline_start_frame=0,
                timeline_frame_count=timeline_count,
            )
        )

    tracks.extend(
        StudioTrack(
            track_id=track.track_id,
            order=track.order + track_offset,
            name=track.name,
            kind=StudioTrackKind.AUDIO,
            channel_count=(
                len(track.input_mapping.channels)
                if track.input_mapping is not None
                else 1
            ),
            armed=track.armed,
            input_monitoring=track.input_monitoring,
        )
        for track in project.tracks
    )
    return StudioDocument(
        project_id=project.project_id,
        project_sample_rate=project.project_sample_rate,
        tracks=tuple(tracks),
        regions=tuple(regions),
        schema_version=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    )


def reconcile_studio_document(
    project: TakeProject,
    document: StudioDocument,
) -> StudioDocument:
    """Merge newly observed project inventory without erasing Studio edits.

    Existing values follow durable IDs.  A segment is considered intentionally
    handled even when every region that references it is a deleted tombstone;
    consequently reconciliation never resurrects a user's delete.
    """

    if not isinstance(project, TakeProject) or not isinstance(document, StudioDocument):
        raise StudioProjectError(
            "Studio reconciliation requires a TakeProject and StudioDocument."
        )
    if project.session_id != document.session_id or project.take_id != document.take_id:
        raise StudioProjectError("Studio document belongs to a different take.")
    if project.project_sample_rate != document.project_sample_rate:
        raise StudioProjectError(
            "Studio document uses a different project sample rate."
        )

    baseline = default_studio_document(project)
    saved_tracks = {item.track_id: item for item in document.tracks}
    # Recording truth owns track inventory/order; mix choices follow durable
    # IDs.  Normalizing the saved order also drops stale lanes without ever
    # carrying a fader position to a different musician by list position.
    project_tracks = {item.track_id: item for item in project.tracks}
    tracks = []
    for track in baseline.tracks:
        reconciled = replace(
            saved_tracks.get(track.track_id, track),
            order=track.order,
        )
        if not project_tracks[track.track_id].selected_for_export:
            reconciled = replace(reconciled, export_included=False)
        tracks.append(reconciled)
    project_track_ids = {item.track_id for item in baseline.tracks}
    project_sources = {
        (project.take_id, track.track_id, segment.segment_id): segment
        for track in project.tracks
        for segment in track.segments
    }

    regions: list[StudioRegion] = []
    for region in document.regions:
        if region.track_id not in project_track_ids:
            continue
        if region.source_take_id == project.take_id:
            source_key = (
                region.source_take_id,
                region.source_track_id,
                region.source_segment_id,
            )
            segment = project_sources.get(source_key)
            if segment is None:
                # Recording truth may legitimately remove stale inventory; do
                # not resurrect or redirect a region by display position.
                continue
            if region.source_end_frame > segment.frame_count:
                raise StudioProjectError(
                    "Studio region extends beyond its immutable source segment."
                )
            if (
                int(region.mapping_source_start_frame)
                + int(region.mapping_source_frame_count)
                > segment.frame_count
            ):
                raise StudioProjectError(
                    "Studio region mapping extends beyond its immutable source segment."
                )
        regions.append(region)

    represented_sources = {
        (region.source_take_id, region.source_track_id, region.source_segment_id)
        for region in regions
    }
    for region in baseline.regions:
        source_key = (
            region.source_take_id,
            region.source_track_id,
            region.source_segment_id,
        )
        if source_key not in represented_sources:
            regions.append(region)
            represented_sources.add(source_key)

    region_ids = {item.region_id for item in regions}
    lanes = [
        replace(
            lane,
            region_ids=tuple(
                region_id for region_id in lane.region_ids if region_id in region_ids
            ),
        )
        for lane in document.take_lanes
        if lane.track_id in project_track_ids
    ]
    lane_ids = {item.lane_id for item in lanes}
    region_map = {item.region_id: item for item in regions}
    lane_map = {item.lane_id: item for item in lanes}
    comp_ranges: list[StudioCompRange] = []
    for item in document.comp_ranges:
        if item.track_id not in project_track_ids or item.lane_id not in lane_ids:
            continue
        lane = lane_map[item.lane_id]
        coverage = (
            (
                region_map[region_id].timeline_start_frame,
                region_map[region_id].timeline_end_frame,
            )
            for region_id in lane.region_ids
            if not region_map[region_id].deleted and region_map[region_id].enabled
        )
        if (
            item.enabled
            and not item.deleted
            and (
                lane.deleted
                or not lane.enabled
                or not _interval_is_covered(
                    item.timeline_start_frame,
                    item.timeline_end_frame,
                    coverage,
                )
            )
        ):
            item = replace(item, enabled=False, deleted=True)
        comp_ranges.append(item)
    crossfades = [
        item
        for item in document.crossfades
        if item.left_region_id in region_ids and item.right_region_id in region_ids
    ]

    marker_ids = {item.marker_id for item in document.markers}
    markers = list(document.markers)
    markers.extend(
        item for item in baseline.markers if item.marker_id not in marker_ids
    )

    if (
        tuple(tracks) == document.tracks
        and tuple(regions) == document.regions
        and tuple(lanes) == document.take_lanes
        and tuple(comp_ranges) == document.comp_ranges
        and tuple(markers) == document.markers
        and tuple(crossfades) == document.crossfades
    ):
        return document
    return document._bumped(
        tracks=tuple(tracks),
        regions=tuple(regions),
        take_lanes=tuple(lanes),
        comp_ranges=tuple(comp_ranges),
        markers=tuple(markers),
        crossfades=tuple(crossfades),
    )


def studio_document_from_dict(value: Mapping[str, Any]) -> StudioDocument:
    """Strictly parse a legacy take or standalone song Studio document."""

    if not isinstance(value, Mapping):
        raise StudioProjectError("Studio document root must be an object.")
    if "schema_version" not in value:
        raise StudioProjectError(
            "Studio document is missing required fields: schema_version."
        )
    raw_schema = value["schema_version"]
    if (
        raw_schema != STUDIO_PROJECT_SCHEMA_VERSION
        and raw_schema != STUDIO_SONG_PROJECT_SCHEMA_VERSION
    ):
        raise StudioProjectError(
            f"Unsupported Studio schema: {raw_schema!r}."
        )
    schema = _integer(
        raw_schema,
        "schema_version",
        minimum=STUDIO_PROJECT_SCHEMA_VERSION,
        maximum=STUDIO_SONG_PROJECT_SCHEMA_VERSION,
    )
    common_fields = {
        "schema_version",
        "revision",
        "project_sample_rate",
        "snap_mode",
        "tracks",
        "regions",
        "take_lanes",
        "comp_ranges",
        "markers",
        "crossfades",
        "cycle_range",
        "master",
    }
    identity_fields = (
        {"project_id"}
        if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
        else {"session_id", "take_id"}
    )
    expected_fields = common_fields | identity_fields
    _strict_keys(
        value,
        allowed=expected_fields,
        required=expected_fields,
        field_name="Studio document",
    )
    cycle_value = value["cycle_range"]
    if cycle_value is not None and not isinstance(cycle_value, Mapping):
        raise StudioProjectError("Studio cycle range must be an object or null.")
    master_value = value["master"]
    if not isinstance(master_value, Mapping):
        raise StudioProjectError("Studio master must be an object.")
    return StudioDocument(
        session_id=(
            value["session_id"]
            if schema == STUDIO_PROJECT_SCHEMA_VERSION
            else ""
        ),
        take_id=(
            value["take_id"]
            if schema == STUDIO_PROJECT_SCHEMA_VERSION
            else ""
        ),
        project_id=(
            value["project_id"]
            if schema == STUDIO_SONG_PROJECT_SCHEMA_VERSION
            else ""
        ),
        project_sample_rate=value["project_sample_rate"],
        tracks=tuple(
            StudioTrack.from_dict(item, schema_version=schema)
            for item in _mapping_items(value["tracks"], "tracks")
        ),
        regions=tuple(
            StudioRegion.from_dict(item, schema_version=schema)
            for item in _mapping_items(value["regions"], "regions")
        ),
        take_lanes=tuple(
            StudioTakeLane.from_dict(item, schema_version=schema)
            for item in _mapping_items(value["take_lanes"], "take_lanes")
        ),
        comp_ranges=tuple(
            StudioCompRange.from_dict(item)
            for item in _mapping_items(value["comp_ranges"], "comp_ranges")
        ),
        markers=tuple(
            StudioMarker.from_dict(item)
            for item in _mapping_items(value["markers"], "markers")
        ),
        crossfades=tuple(
            StudioCrossfade.from_dict(item)
            for item in _mapping_items(value["crossfades"], "crossfades")
        ),
        cycle_range=(
            StudioCycleRange.from_dict(cycle_value)
            if isinstance(cycle_value, Mapping)
            else None
        ),
        snap_mode=value["snap_mode"],
        master=StudioMaster.from_dict(master_value),
        revision=value["revision"],
        schema_version=schema,
    )


__all__ = [
    "STUDIO_PROJECT_SCHEMA_VERSION",
    "STUDIO_SONG_PROJECT_SCHEMA_VERSION",
    "FadeCurve",
    "MarkerKind",
    "SnapMode",
    "StudioAutomationInterpolation",
    "StudioAutomationLane",
    "StudioAutomationParameter",
    "StudioAutomationPoint",
    "StudioCompRange",
    "StudioCrossfade",
    "StudioCycleRange",
    "StudioDocument",
    "StudioEffect",
    "StudioEffectKind",
    "StudioMarker",
    "StudioMaster",
    "StudioProjectError",
    "StudioRegion",
    "StudioSend",
    "StudioTakeLane",
    "StudioTrack",
    "StudioTrackKind",
    "crossfade_gains",
    "default_song_studio_document",
    "default_studio_document",
    "fade_gain",
    "reconcile_studio_document",
    "studio_document_from_dict",
]
