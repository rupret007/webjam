"""Deterministic musical-time primitives for WebJam Studio.

The arrangement and audio engines use integer project frames.  This module
adds a musical projection without making floating-point tempo state another
timeline authority:

* tempo is stored as integer micro-BPM;
* frame, quarter-note beat, and tick conversion uses :class:`Fraction`;
* tempo changes are constant segments beginning at exact integer frames;
* time-signature changes are accepted only on an exact tick and bar boundary;
* conversion back to a frame has an explicit deterministic tie policy.

Constant tempo segments are deliberate.  A ramp would need an exactly
specified integration and inverse (plus a stable serialized curve contract).
Until that contract exists, accepting a ramp would make playback, snapping,
and export disagree by construction.
"""

from __future__ import annotations

import itertools
import math
import threading
import uuid
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from enum import Enum
from fractions import Fraction
from typing import Any

TEMPO_MAP_SCHEMA_VERSION = 1
TEMPO_ANALYSIS_SCHEMA_VERSION = 1
TICKS_PER_QUARTER = 960
MICRO_BPM_PER_BPM = 1_000_000
CONFIDENCE_UNITS = 1_000_000
MAX_PROJECT_FRAMES = (1 << 62) - 1
MAX_TEMPO_POINTS = 10_000
MAX_TIME_SIGNATURE_POINTS = 10_000
MIN_BPM_MICROS = 20 * MICRO_BPM_PER_BPM
MAX_BPM_MICROS = 400 * MICRO_BPM_PER_BPM
MAX_SAMPLE_RATE = 768_000
MAX_TIME_SIGNATURE_NUMERATOR = 64
MAX_TIME_SIGNATURE_DENOMINATOR = 64

_DEFAULT_NAMESPACE = uuid.UUID("ad556b8e-ee0b-4984-93ef-a251d1e270e8")


class StudioTempoError(ValueError):
    """Raised when musical-time state or a conversion is invalid."""


class TempoAnalysisCancelled(RuntimeError):
    """Raised when tempo analysis work no longer owns the active generation."""


class FrameRounding(str, Enum):
    """How an exact fractional frame is converted to the project frame grid."""

    FLOOR = "floor"
    CEIL = "ceil"
    NEAREST_EARLIER = "nearest_earlier"
    NEAREST_LATER = "nearest_later"


class MusicalSnapMode(str, Enum):
    OFF = "off"
    BEAT = "beat"
    BAR = "bar"
    SUBDIVISION = "subdivision"


class SnapTiePolicy(str, Enum):
    EARLIER = "earlier"
    LATER = "later"


def _strict_keys(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    required: set[str],
    field_name: str,
) -> None:
    unknown = set(value).difference(allowed)
    if unknown:
        raise StudioTempoError(
            f"{field_name} contains unsupported fields: "
            + ", ".join(sorted(str(item) for item in unknown))
            + "."
        )
    missing = required.difference(value)
    if missing:
        raise StudioTempoError(
            f"{field_name} is missing required fields: "
            + ", ".join(sorted(missing))
            + "."
        )


def _strict_int(
    value: object,
    field_name: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_PROJECT_FRAMES,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StudioTempoError(f"{field_name} must be an integer.")
    if value < minimum or value > maximum:
        raise StudioTempoError(
            f"{field_name} must be between {minimum} and {maximum}."
        )
    return value


def _canonical_uuid(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise StudioTempoError(f"{field_name} must be a UUID.")
    try:
        return str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise StudioTempoError(f"{field_name} must be a UUID.") from exc


def _enum_value(enum_type, value: object, field_name: str):
    if isinstance(value, enum_type):
        return value
    if not isinstance(value, str):
        raise StudioTempoError(f"{field_name} must be text.")
    try:
        return enum_type(value)
    except ValueError as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise StudioTempoError(f"{field_name} must be one of: {choices}.") from exc


def _fraction(value: object, field_name: str) -> Fraction:
    """Return a finite exact fraction from bounded human-facing numeric input."""

    if isinstance(value, bool):
        raise StudioTempoError(f"{field_name} must be a finite number.")
    if isinstance(value, Fraction):
        result = value
    elif isinstance(value, int):
        result = Fraction(value, 1)
    elif isinstance(value, Decimal):
        if not value.is_finite():
            raise StudioTempoError(f"{field_name} must be a finite number.")
        if abs(value.adjusted()) > 80 or abs(value.as_tuple().exponent) > 80:
            raise StudioTempoError(f"{field_name} is too precise or too large.")
        result = Fraction(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise StudioTempoError(f"{field_name} must be a finite number.")
        # ``str`` is the stable user-facing decimal form, avoiding adoption of
        # the float's binary expansion as persisted musical truth.
        result = Fraction(Decimal(str(value)))
    elif isinstance(value, str):
        if not value or len(value) > 80 or value.strip() != value:
            raise StudioTempoError(f"{field_name} must be a finite number.")
        try:
            decimal = Decimal(value)
        except InvalidOperation as exc:
            raise StudioTempoError(f"{field_name} must be a finite number.") from exc
        if not decimal.is_finite():
            raise StudioTempoError(f"{field_name} must be a finite number.")
        if abs(decimal.adjusted()) > 80 or abs(decimal.as_tuple().exponent) > 80:
            raise StudioTempoError(f"{field_name} is too precise or too large.")
        result = Fraction(decimal)
    else:
        raise StudioTempoError(f"{field_name} must be a finite number.")
    if abs(result.numerator).bit_length() > 256 or result.denominator.bit_length() > 256:
        raise StudioTempoError(f"{field_name} is too precise or too large.")
    return result


def bpm_to_micros(value: object) -> int:
    """Convert BPM with at most six decimal places to exact micro-BPM."""

    scaled = _fraction(value, "bpm") * MICRO_BPM_PER_BPM
    if scaled.denominator != 1:
        raise StudioTempoError("bpm supports at most six decimal places.")
    return _strict_int(
        scaled.numerator,
        "bpm_micros",
        minimum=MIN_BPM_MICROS,
        maximum=MAX_BPM_MICROS,
    )


def _validate_bpm_micros(value: object, field_name: str = "bpm_micros") -> int:
    return _strict_int(
        value,
        field_name,
        minimum=MIN_BPM_MICROS,
        maximum=MAX_BPM_MICROS,
    )


def _validate_time_signature(numerator: object, denominator: object) -> tuple[int, int]:
    top = _strict_int(
        numerator,
        "time_signature.numerator",
        minimum=1,
        maximum=MAX_TIME_SIGNATURE_NUMERATOR,
    )
    bottom = _strict_int(
        denominator,
        "time_signature.denominator",
        minimum=1,
        maximum=MAX_TIME_SIGNATURE_DENOMINATOR,
    )
    if bottom & (bottom - 1):
        raise StudioTempoError(
            "time_signature.denominator must be a power of two."
        )
    if (TICKS_PER_QUARTER * 4) % bottom:
        raise StudioTempoError(
            "time_signature.denominator cannot be represented on the tick grid."
        )
    return top, bottom


def _floor_fraction(value: Fraction) -> int:
    return value.numerator // value.denominator


def _ceil_fraction(value: Fraction) -> int:
    return -((-value.numerator) // value.denominator)


def _round_fraction(value: Fraction, rounding: FrameRounding | str) -> int:
    mode = _enum_value(FrameRounding, rounding, "rounding")
    lower = _floor_fraction(value)
    if mode is FrameRounding.FLOOR:
        return lower
    upper = _ceil_fraction(value)
    if mode is FrameRounding.CEIL:
        return upper
    lower_distance = value - lower
    upper_distance = upper - value
    if lower_distance < upper_distance:
        return lower
    if upper_distance < lower_distance:
        return upper
    return upper if mode is FrameRounding.NEAREST_LATER else lower


@dataclass(frozen=True)
class TempoPoint:
    """One constant-tempo segment beginning at an integer project frame."""

    point_id: str
    frame: int
    bpm_micros: int
    curve: str = "constant"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "point_id", _canonical_uuid(self.point_id, "tempo_point.point_id")
        )
        object.__setattr__(
            self,
            "frame",
            _strict_int(self.frame, "tempo_point.frame"),
        )
        object.__setattr__(
            self,
            "bpm_micros",
            _validate_bpm_micros(
                self.bpm_micros,
                "tempo_point.bpm_micros",
            ),
        )
        if self.curve != "constant":
            raise StudioTempoError(
                "Only deterministic constant tempo segments are supported."
            )

    @property
    def bpm(self) -> Fraction:
        return Fraction(self.bpm_micros, MICRO_BPM_PER_BPM)

    @classmethod
    def from_bpm(
        cls,
        frame: int,
        bpm: object,
        *,
        point_id: str | None = None,
    ) -> TempoPoint:
        return cls(
            point_id=point_id or str(uuid.uuid4()),
            frame=frame,
            bpm_micros=bpm_to_micros(bpm),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "point_id": self.point_id,
            "frame": self.frame,
            "bpm_micros": self.bpm_micros,
            "curve": self.curve,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TempoPoint:
        if not isinstance(value, Mapping):
            raise StudioTempoError("Tempo point must be an object.")
        _strict_keys(
            value,
            allowed={"point_id", "frame", "bpm_micros", "curve"},
            required={"point_id", "frame", "bpm_micros", "curve"},
            field_name="Tempo point",
        )
        return cls(
            point_id=value["point_id"],
            frame=value["frame"],
            bpm_micros=value["bpm_micros"],
            curve=value["curve"],
        )


@dataclass(frozen=True)
class TimeSignaturePoint:
    """One time signature beginning at an exact bar boundary."""

    point_id: str
    frame: int
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "point_id",
            _canonical_uuid(self.point_id, "time_signature_point.point_id"),
        )
        object.__setattr__(
            self,
            "frame",
            _strict_int(self.frame, "time_signature_point.frame"),
        )
        top, bottom = _validate_time_signature(self.numerator, self.denominator)
        object.__setattr__(self, "numerator", top)
        object.__setattr__(self, "denominator", bottom)

    @property
    def beat_ticks(self) -> int:
        return TICKS_PER_QUARTER * 4 // self.denominator

    @property
    def bar_ticks(self) -> int:
        return self.numerator * self.beat_ticks

    def to_dict(self) -> dict[str, object]:
        return {
            "point_id": self.point_id,
            "frame": self.frame,
            "numerator": self.numerator,
            "denominator": self.denominator,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TimeSignaturePoint:
        if not isinstance(value, Mapping):
            raise StudioTempoError("Time-signature point must be an object.")
        _strict_keys(
            value,
            allowed={"point_id", "frame", "numerator", "denominator"},
            required={"point_id", "frame", "numerator", "denominator"},
            field_name="Time-signature point",
        )
        return cls(
            point_id=value["point_id"],
            frame=value["frame"],
            numerator=value["numerator"],
            denominator=value["denominator"],
        )


@dataclass(frozen=True)
class BarPosition:
    """One 1-based bar/beat location with an exact tick offset."""

    bar_number: int
    beat_number: int
    tick_in_beat: Fraction = Fraction(0, 1)
    numerator: int = 4
    denominator: int = 4

    def __post_init__(self) -> None:
        bar = _strict_int(
            self.bar_number,
            "bar_position.bar_number",
            minimum=1,
            maximum=MAX_PROJECT_FRAMES,
        )
        top, bottom = _validate_time_signature(self.numerator, self.denominator)
        beat = _strict_int(
            self.beat_number,
            "bar_position.beat_number",
            minimum=1,
            maximum=top,
        )
        tick = _fraction(self.tick_in_beat, "bar_position.tick_in_beat")
        beat_ticks = TICKS_PER_QUARTER * 4 // bottom
        if tick < 0 or tick >= beat_ticks:
            raise StudioTempoError(
                "bar_position.tick_in_beat must stay inside its beat."
            )
        object.__setattr__(self, "bar_number", bar)
        object.__setattr__(self, "beat_number", beat)
        object.__setattr__(self, "tick_in_beat", tick)
        object.__setattr__(self, "numerator", top)
        object.__setattr__(self, "denominator", bottom)


@dataclass(frozen=True)
class TempoMap:
    """Immutable project-frame musical timeline."""

    sample_rate: int
    tempo_points: tuple[TempoPoint, ...]
    time_signature_points: tuple[TimeSignaturePoint, ...]
    ticks_per_quarter: int = TICKS_PER_QUARTER
    schema_version: int = TEMPO_MAP_SCHEMA_VERSION
    _tempo_frames: tuple[int, ...] = field(
        init=False, repr=False, compare=False, default=()
    )
    _tempo_start_beats: tuple[Fraction, ...] = field(
        init=False, repr=False, compare=False, default=()
    )
    _signature_frames: tuple[int, ...] = field(
        init=False, repr=False, compare=False, default=()
    )
    _signature_start_ticks: tuple[int, ...] = field(
        init=False, repr=False, compare=False, default=()
    )
    _signature_start_bars: tuple[int, ...] = field(
        init=False, repr=False, compare=False, default=()
    )

    def __post_init__(self) -> None:
        rate = _strict_int(
            self.sample_rate,
            "sample_rate",
            minimum=1,
            maximum=MAX_SAMPLE_RATE,
        )
        schema = _strict_int(
            self.schema_version,
            "schema_version",
            minimum=TEMPO_MAP_SCHEMA_VERSION,
            maximum=TEMPO_MAP_SCHEMA_VERSION,
        )
        ticks = _strict_int(
            self.ticks_per_quarter,
            "ticks_per_quarter",
            minimum=TICKS_PER_QUARTER,
            maximum=TICKS_PER_QUARTER,
        )
        try:
            tempos = tuple(self.tempo_points)
            signatures = tuple(self.time_signature_points)
        except TypeError as exc:
            raise StudioTempoError("Tempo-map points must be sequences.") from exc
        if not tempos or len(tempos) > MAX_TEMPO_POINTS:
            raise StudioTempoError(
                f"Tempo map requires 1 to {MAX_TEMPO_POINTS} tempo points."
            )
        if not signatures or len(signatures) > MAX_TIME_SIGNATURE_POINTS:
            raise StudioTempoError(
                "Tempo map requires 1 to "
                f"{MAX_TIME_SIGNATURE_POINTS} time-signature points."
            )
        if any(not isinstance(item, TempoPoint) for item in tempos):
            raise StudioTempoError("tempo_points must contain TempoPoint values.")
        if any(not isinstance(item, TimeSignaturePoint) for item in signatures):
            raise StudioTempoError(
                "time_signature_points must contain TimeSignaturePoint values."
            )
        if tempos[0].frame != 0:
            raise StudioTempoError("The first tempo point must begin at frame zero.")
        if signatures[0].frame != 0:
            raise StudioTempoError(
                "The first time-signature point must begin at frame zero."
            )
        for values, label in (
            (tempos, "tempo"),
            (signatures, "time-signature"),
        ):
            frames = tuple(item.frame for item in values)
            if any(right <= left for left, right in itertools.pairwise(frames)):
                raise StudioTempoError(
                    f"{label.capitalize()} points must use strictly increasing frames."
                )
            identifiers = tuple(item.point_id for item in values)
            if len(identifiers) != len(set(identifiers)):
                raise StudioTempoError(
                    f"Tempo map contains duplicate {label} point IDs."
                )
        all_ids = {item.point_id for item in tempos}
        if all_ids.intersection(item.point_id for item in signatures):
            raise StudioTempoError("Tempo-map point IDs must be globally unique.")

        object.__setattr__(self, "sample_rate", rate)
        object.__setattr__(self, "schema_version", schema)
        object.__setattr__(self, "ticks_per_quarter", ticks)
        object.__setattr__(self, "tempo_points", tempos)
        object.__setattr__(self, "time_signature_points", signatures)

        tempo_frames = tuple(item.frame for item in tempos)
        tempo_start_beats: list[Fraction] = [Fraction(0, 1)]
        for prior, following in itertools.pairwise(tempos):
            tempo_start_beats.append(
                tempo_start_beats[-1]
                + Fraction(
                    (following.frame - prior.frame) * prior.bpm_micros,
                    60 * MICRO_BPM_PER_BPM * rate,
                )
            )
        object.__setattr__(self, "_tempo_frames", tempo_frames)
        object.__setattr__(self, "_tempo_start_beats", tuple(tempo_start_beats))

        signature_frames = tuple(item.frame for item in signatures)
        signature_start_ticks: list[int] = []
        signature_start_bars: list[int] = []
        for index, point in enumerate(signatures):
            exact_tick = self._beat_at_frame(point.frame) * ticks
            if exact_tick.denominator != 1:
                raise StudioTempoError(
                    "A time-signature change must fall on an exact musical tick."
                )
            tick = exact_tick.numerator
            if index == 0:
                signature_start_ticks.append(tick)
                signature_start_bars.append(1)
                continue
            previous = signatures[index - 1]
            previous_tick = signature_start_ticks[-1]
            delta = tick - previous_tick
            if delta <= 0 or delta % previous.bar_ticks:
                raise StudioTempoError(
                    "A time-signature change must begin on a bar boundary."
                )
            signature_start_ticks.append(tick)
            signature_start_bars.append(
                signature_start_bars[-1] + delta // previous.bar_ticks
            )
        object.__setattr__(self, "_signature_frames", signature_frames)
        object.__setattr__(
            self, "_signature_start_ticks", tuple(signature_start_ticks)
        )
        object.__setattr__(
            self, "_signature_start_bars", tuple(signature_start_bars)
        )

    @classmethod
    def default(cls, sample_rate: int) -> TempoMap:
        return cls(
            sample_rate=sample_rate,
            tempo_points=(
                TempoPoint(
                    point_id=str(
                        uuid.uuid5(_DEFAULT_NAMESPACE, "default-tempo-120")
                    ),
                    frame=0,
                    bpm_micros=120 * MICRO_BPM_PER_BPM,
                ),
            ),
            time_signature_points=(
                TimeSignaturePoint(
                    point_id=str(
                        uuid.uuid5(_DEFAULT_NAMESPACE, "default-signature-4-4")
                    ),
                    frame=0,
                    numerator=4,
                    denominator=4,
                ),
            ),
        )

    def _beat_at_frame(self, frame: int) -> Fraction:
        index = bisect_right(self._tempo_frames, frame) - 1
        point = self.tempo_points[index]
        return self._tempo_start_beats[index] + Fraction(
            (frame - point.frame) * point.bpm_micros,
            60 * MICRO_BPM_PER_BPM * self.sample_rate,
        )

    def frame_to_beat(self, frame: int) -> Fraction:
        """Return exact absolute quarter-note beats from project frame zero."""

        project_frame = _strict_int(frame, "frame")
        return self._beat_at_frame(project_frame)

    def frame_to_tick(self, frame: int) -> Fraction:
        """Return exact absolute ticks; a frame may lie between tick boundaries."""

        return self.frame_to_beat(frame) * self.ticks_per_quarter

    def beat_to_frame(
        self,
        beat: object,
        *,
        rounding: FrameRounding | str = FrameRounding.NEAREST_EARLIER,
    ) -> int:
        target = _fraction(beat, "beat")
        if target < 0:
            raise StudioTempoError("beat must not be negative.")
        index = bisect_right(self._tempo_start_beats, target) - 1
        point = self.tempo_points[index]
        exact_frame = Fraction(point.frame, 1) + (
            target - self._tempo_start_beats[index]
        ) * Fraction(
            60 * MICRO_BPM_PER_BPM * self.sample_rate,
            point.bpm_micros,
        )
        result = _round_fraction(exact_frame, rounding)
        if result < 0 or result > MAX_PROJECT_FRAMES:
            raise StudioTempoError("beat falls outside the supported project timeline.")
        return result

    def tick_to_frame(
        self,
        tick: object,
        *,
        rounding: FrameRounding | str = FrameRounding.NEAREST_EARLIER,
    ) -> int:
        target = _fraction(tick, "tick")
        if target < 0:
            raise StudioTempoError("tick must not be negative.")
        return self.beat_to_frame(
            target / self.ticks_per_quarter,
            rounding=rounding,
        )

    def _signature_index_for_tick(self, tick: Fraction) -> int:
        return bisect_right(self._signature_start_ticks, tick) - 1

    def frame_to_bar_position(self, frame: int) -> BarPosition:
        absolute_tick = self.frame_to_tick(frame)
        index = self._signature_index_for_tick(absolute_tick)
        signature = self.time_signature_points[index]
        delta = absolute_tick - self._signature_start_ticks[index]
        bar_offset = _floor_fraction(delta / signature.bar_ticks)
        in_bar = delta - bar_offset * signature.bar_ticks
        beat_offset = _floor_fraction(in_bar / signature.beat_ticks)
        tick_in_beat = in_bar - beat_offset * signature.beat_ticks
        return BarPosition(
            bar_number=self._signature_start_bars[index] + bar_offset,
            beat_number=beat_offset + 1,
            tick_in_beat=tick_in_beat,
            numerator=signature.numerator,
            denominator=signature.denominator,
        )

    def frame_to_bar_fraction(self, frame: int) -> Fraction:
        """Return a 1-based exact fractional bar position."""

        absolute_tick = self.frame_to_tick(frame)
        index = self._signature_index_for_tick(absolute_tick)
        signature = self.time_signature_points[index]
        delta = absolute_tick - self._signature_start_ticks[index]
        bar_offset = _floor_fraction(delta / signature.bar_ticks)
        in_bar = delta - bar_offset * signature.bar_ticks
        return (
            self._signature_start_bars[index]
            + bar_offset
            + in_bar / signature.bar_ticks
        )

    def _signature_index_for_bar(self, bar_number: int) -> int:
        return bisect_right(self._signature_start_bars, bar_number) - 1

    def bar_position_to_tick(
        self,
        bar_number: int,
        beat_number: int = 1,
        tick_in_beat: object = 0,
    ) -> Fraction:
        bar = _strict_int(
            bar_number,
            "bar_number",
            minimum=1,
            maximum=MAX_PROJECT_FRAMES,
        )
        index = self._signature_index_for_bar(bar)
        signature = self.time_signature_points[index]
        beat = _strict_int(
            beat_number,
            "beat_number",
            minimum=1,
            maximum=signature.numerator,
        )
        tick = _fraction(tick_in_beat, "tick_in_beat")
        if tick < 0 or tick >= signature.beat_ticks:
            raise StudioTempoError("tick_in_beat must stay inside its beat.")
        return (
            self._signature_start_ticks[index]
            + (bar - self._signature_start_bars[index]) * signature.bar_ticks
            + (beat - 1) * signature.beat_ticks
            + tick
        )

    def bar_position_to_frame(
        self,
        bar_number: int,
        beat_number: int = 1,
        tick_in_beat: object = 0,
        *,
        rounding: FrameRounding | str = FrameRounding.NEAREST_EARLIER,
    ) -> int:
        return self.tick_to_frame(
            self.bar_position_to_tick(bar_number, beat_number, tick_in_beat),
            rounding=rounding,
        )

    def _snap_tick_candidates(
        self,
        tick: Fraction,
        mode: MusicalSnapMode,
        subdivision: int,
    ) -> set[Fraction]:
        current = self._signature_index_for_tick(tick)
        candidates: set[Fraction] = set()
        for index in range(
            max(0, current - 1),
            min(len(self.time_signature_points), current + 2),
        ):
            signature = self.time_signature_points[index]
            start = Fraction(self._signature_start_ticks[index], 1)
            end = (
                Fraction(self._signature_start_ticks[index + 1], 1)
                if index + 1 < len(self._signature_start_ticks)
                else None
            )
            if mode is MusicalSnapMode.BAR:
                step = Fraction(signature.bar_ticks, 1)
            elif mode is MusicalSnapMode.BEAT:
                step = Fraction(signature.beat_ticks, 1)
            else:
                step = Fraction(signature.beat_ticks, subdivision)
            relative = (tick - start) / step
            lower_index = _floor_fraction(relative)
            for grid_index in (lower_index, lower_index + 1):
                candidate = start + grid_index * step
                if candidate < start or (end is not None and candidate > end):
                    continue
                candidates.add(candidate)
            candidates.add(start)
            if end is not None:
                candidates.add(end)
        return candidates

    def snap_frame(
        self,
        frame: int,
        mode: MusicalSnapMode | str,
        *,
        subdivision: int = 2,
        tie_policy: SnapTiePolicy | str = SnapTiePolicy.EARLIER,
    ) -> int:
        """Snap a frame to the nearest musical grid point.

        ``subdivision`` means equal divisions of the active signature beat:
        two gives eighth notes in 4/4, four gives sixteenths, and two gives
        sixteenth notes in 6/8.  Equal frame-distance ties use ``tie_policy``.
        """

        project_frame = _strict_int(frame, "frame")
        snap_mode = _enum_value(MusicalSnapMode, mode, "snap_mode")
        ties = _enum_value(SnapTiePolicy, tie_policy, "tie_policy")
        if snap_mode is MusicalSnapMode.OFF:
            return project_frame
        divisions = _strict_int(
            subdivision,
            "subdivision",
            minimum=1,
            maximum=TICKS_PER_QUARTER,
        )
        tick = self.frame_to_tick(project_frame)
        candidates = self._snap_tick_candidates(tick, snap_mode, divisions)
        if not candidates:
            return project_frame
        rounding = (
            FrameRounding.NEAREST_LATER
            if ties is SnapTiePolicy.LATER
            else FrameRounding.NEAREST_EARLIER
        )
        frame_candidates = {
            self.tick_to_frame(candidate, rounding=rounding)
            for candidate in candidates
        }
        distances = {
            candidate: abs(candidate - project_frame)
            for candidate in frame_candidates
        }
        minimum = min(distances.values())
        nearest = [item for item, distance in distances.items() if distance == minimum]
        return max(nearest) if ties is SnapTiePolicy.LATER else min(nearest)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "sample_rate": self.sample_rate,
            "ticks_per_quarter": self.ticks_per_quarter,
            "tempo_points": [item.to_dict() for item in self.tempo_points],
            "time_signature_points": [
                item.to_dict() for item in self.time_signature_points
            ],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TempoMap:
        if not isinstance(value, Mapping):
            raise StudioTempoError("Tempo map must be an object.")
        _strict_keys(
            value,
            allowed={
                "schema_version",
                "sample_rate",
                "ticks_per_quarter",
                "tempo_points",
                "time_signature_points",
            },
            required={
                "schema_version",
                "sample_rate",
                "ticks_per_quarter",
                "tempo_points",
                "time_signature_points",
            },
            field_name="Tempo map",
        )
        if value["schema_version"] != TEMPO_MAP_SCHEMA_VERSION:
            raise StudioTempoError("Tempo map has an unsupported schema.")
        tempo_values = value["tempo_points"]
        signature_values = value["time_signature_points"]
        if not isinstance(tempo_values, list) or not isinstance(
            signature_values, list
        ):
            raise StudioTempoError("Tempo-map point collections must be lists.")
        return cls(
            sample_rate=value["sample_rate"],
            tempo_points=tuple(TempoPoint.from_dict(item) for item in tempo_values),
            time_signature_points=tuple(
                TimeSignaturePoint.from_dict(item) for item in signature_values
            ),
            ticks_per_quarter=value["ticks_per_quarter"],
            schema_version=value["schema_version"],
        )


def load_tempo_map(
    value: Mapping[str, Any] | None,
    *,
    sample_rate: int,
) -> TempoMap:
    """Load schema 1, migrate bounded legacy timing, or return 120 BPM 4/4."""

    expected_rate = _strict_int(
        sample_rate,
        "sample_rate",
        minimum=1,
        maximum=MAX_SAMPLE_RATE,
    )
    if value is None:
        return TempoMap.default(expected_rate)
    if not isinstance(value, Mapping):
        raise StudioTempoError("Tempo-map state must be an object or null.")
    if value.get("schema_version") == TEMPO_MAP_SCHEMA_VERSION:
        loaded = TempoMap.from_dict(value)
        if loaded.sample_rate != expected_rate:
            raise StudioTempoError(
                "Tempo-map sample rate does not match the project sample rate."
            )
        return loaded

    # Legacy TakeProject timing is one fixed BPM/signature.  The migration is
    # strict and read-only: callers decide when to publish the schema-1 map.
    allowed = {
        "schema_version",
        "sample_rate",
        "tempo_bpm",
        "bpm",
        "time_signature_numerator",
        "time_signature_denominator",
    }
    _strict_keys(
        value,
        allowed=allowed,
        required=set(),
        field_name="Legacy tempo state",
    )
    if value.get("schema_version") not in {None, 0}:
        raise StudioTempoError("Tempo-map state has an unsupported schema.")
    legacy_rate = value.get("sample_rate", expected_rate)
    if (
        _strict_int(
            legacy_rate,
            "legacy.sample_rate",
            minimum=1,
            maximum=MAX_SAMPLE_RATE,
        )
        != expected_rate
    ):
        raise StudioTempoError(
            "Legacy tempo sample rate does not match the project sample rate."
        )
    if "tempo_bpm" in value and "bpm" in value:
        raise StudioTempoError("Legacy tempo state must specify tempo_bpm or bpm.")
    bpm = value.get("tempo_bpm", value.get("bpm", 120))
    numerator = value.get("time_signature_numerator", 4)
    denominator = value.get("time_signature_denominator", 4)
    top, bottom = _validate_time_signature(numerator, denominator)
    return TempoMap(
        sample_rate=expected_rate,
        tempo_points=(
            TempoPoint(
                point_id=str(
                    uuid.uuid5(
                        _DEFAULT_NAMESPACE,
                        f"legacy-tempo:{bpm_to_micros(bpm)}",
                    )
                ),
                frame=0,
                bpm_micros=bpm_to_micros(bpm),
            ),
        ),
        time_signature_points=(
            TimeSignaturePoint(
                point_id=str(
                    uuid.uuid5(
                        _DEFAULT_NAMESPACE,
                        f"legacy-signature:{top}:{bottom}",
                    )
                ),
                frame=0,
                numerator=top,
                denominator=bottom,
            ),
        ),
    )


@dataclass(frozen=True)
class TempoAnalysisResult:
    """Path-free result of one offline tempo-analysis generation."""

    analysis_id: str
    generation: int
    detected_bpm_micros: int
    confidence_millionths: int
    detected_numerator: int = 4
    detected_denominator: int = 4
    manual_bpm_micros: int | None = None
    manual_numerator: int | None = None
    manual_denominator: int | None = None
    schema_version: int = TEMPO_ANALYSIS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "analysis_id",
            _canonical_uuid(self.analysis_id, "analysis.analysis_id"),
        )
        object.__setattr__(
            self,
            "generation",
            _strict_int(
                self.generation,
                "analysis.generation",
                minimum=1,
                maximum=MAX_PROJECT_FRAMES,
            ),
        )
        object.__setattr__(
            self,
            "detected_bpm_micros",
            _validate_bpm_micros(
                self.detected_bpm_micros,
                "analysis.detected_bpm_micros",
            ),
        )
        object.__setattr__(
            self,
            "confidence_millionths",
            _strict_int(
                self.confidence_millionths,
                "analysis.confidence_millionths",
                minimum=0,
                maximum=CONFIDENCE_UNITS,
            ),
        )
        detected_top, detected_bottom = _validate_time_signature(
            self.detected_numerator,
            self.detected_denominator,
        )
        object.__setattr__(self, "detected_numerator", detected_top)
        object.__setattr__(self, "detected_denominator", detected_bottom)
        manual_values = (
            self.manual_bpm_micros,
            self.manual_numerator,
            self.manual_denominator,
        )
        if any(item is not None for item in manual_values):
            if self.manual_bpm_micros is None:
                manual_bpm = self.detected_bpm_micros
            else:
                manual_bpm = _validate_bpm_micros(
                    self.manual_bpm_micros,
                    "analysis.manual_bpm_micros",
                )
            if (self.manual_numerator is None) != (
                self.manual_denominator is None
            ):
                raise StudioTempoError(
                    "Manual time-signature numerator and denominator are paired."
                )
            if self.manual_numerator is None:
                manual_top = None
                manual_bottom = None
            else:
                manual_top, manual_bottom = _validate_time_signature(
                    self.manual_numerator,
                    self.manual_denominator,
                )
            object.__setattr__(self, "manual_bpm_micros", manual_bpm)
            object.__setattr__(self, "manual_numerator", manual_top)
            object.__setattr__(self, "manual_denominator", manual_bottom)
        object.__setattr__(
            self,
            "schema_version",
            _strict_int(
                self.schema_version,
                "analysis.schema_version",
                minimum=TEMPO_ANALYSIS_SCHEMA_VERSION,
                maximum=TEMPO_ANALYSIS_SCHEMA_VERSION,
            ),
        )

    @property
    def confidence(self) -> Fraction:
        return Fraction(self.confidence_millionths, CONFIDENCE_UNITS)

    @property
    def has_manual_override(self) -> bool:
        return self.manual_bpm_micros is not None

    @property
    def effective_bpm_micros(self) -> int:
        return self.manual_bpm_micros or self.detected_bpm_micros

    @property
    def effective_time_signature(self) -> tuple[int, int]:
        if self.manual_numerator is not None:
            assert self.manual_denominator is not None
            return self.manual_numerator, self.manual_denominator
        return self.detected_numerator, self.detected_denominator

    def with_manual_override(
        self,
        *,
        bpm: object | None = None,
        numerator: int | None = None,
        denominator: int | None = None,
    ) -> TempoAnalysisResult:
        if (numerator is None) != (denominator is None):
            raise StudioTempoError(
                "Manual time-signature numerator and denominator are paired."
            )
        return replace(
            self,
            manual_bpm_micros=(
                self.effective_bpm_micros if bpm is None else bpm_to_micros(bpm)
            ),
            manual_numerator=numerator,
            manual_denominator=denominator,
        )

    def clear_manual_override(self) -> TempoAnalysisResult:
        return replace(
            self,
            manual_bpm_micros=None,
            manual_numerator=None,
            manual_denominator=None,
        )

    def to_tempo_map(self, sample_rate: int) -> TempoMap:
        top, bottom = self.effective_time_signature
        return TempoMap(
            sample_rate=sample_rate,
            tempo_points=(
                TempoPoint(
                    point_id=str(
                        uuid.uuid5(
                            uuid.UUID(self.analysis_id),
                            "accepted-tempo",
                        )
                    ),
                    frame=0,
                    bpm_micros=self.effective_bpm_micros,
                ),
            ),
            time_signature_points=(
                TimeSignaturePoint(
                    point_id=str(
                        uuid.uuid5(
                            uuid.UUID(self.analysis_id),
                            "accepted-time-signature",
                        )
                    ),
                    frame=0,
                    numerator=top,
                    denominator=bottom,
                ),
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "analysis_id": self.analysis_id,
            "generation": self.generation,
            "detected_bpm_micros": self.detected_bpm_micros,
            "confidence_millionths": self.confidence_millionths,
            "detected_numerator": self.detected_numerator,
            "detected_denominator": self.detected_denominator,
            "manual_bpm_micros": self.manual_bpm_micros,
            "manual_numerator": self.manual_numerator,
            "manual_denominator": self.manual_denominator,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TempoAnalysisResult:
        if not isinstance(value, Mapping):
            raise StudioTempoError("Tempo analysis result must be an object.")
        keys = {
            "schema_version",
            "analysis_id",
            "generation",
            "detected_bpm_micros",
            "confidence_millionths",
            "detected_numerator",
            "detected_denominator",
            "manual_bpm_micros",
            "manual_numerator",
            "manual_denominator",
        }
        _strict_keys(
            value,
            allowed=keys,
            required=keys,
            field_name="Tempo analysis result",
        )
        if value["schema_version"] != TEMPO_ANALYSIS_SCHEMA_VERSION:
            raise StudioTempoError("Tempo analysis result has an unsupported schema.")
        return cls(
            schema_version=value["schema_version"],
            analysis_id=value["analysis_id"],
            generation=value["generation"],
            detected_bpm_micros=value["detected_bpm_micros"],
            confidence_millionths=value["confidence_millionths"],
            detected_numerator=value["detected_numerator"],
            detected_denominator=value["detected_denominator"],
            manual_bpm_micros=value["manual_bpm_micros"],
            manual_numerator=value["manual_numerator"],
            manual_denominator=value["manual_denominator"],
        )


class TempoAnalysisToken:
    """Cancellation state for one offline analysis generation."""

    __slots__ = ("_cancelled", "_generation")

    def __init__(self, generation: int) -> None:
        self._generation = generation
        self._cancelled = threading.Event()

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TempoAnalysisCancelled("Tempo analysis was cancelled or superseded.")

    def _cancel(self) -> None:
        self._cancelled.set()


class TempoAnalysisGuard:
    """Latest-generation-wins guard for cancellable offline analysis."""

    def __init__(self) -> None:
        self._generation = 0
        self._current: TempoAnalysisToken | None = None
        self._shutdown = False
        self._lock = threading.Lock()

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def begin_generation(self) -> TempoAnalysisToken:
        with self._lock:
            if self._shutdown:
                raise TempoAnalysisCancelled("Tempo analysis guard is shut down.")
            if self._current is not None:
                self._current._cancel()
            self._generation += 1
            token = TempoAnalysisToken(self._generation)
            self._current = token
            return token

    def is_current(self, token: TempoAnalysisToken) -> bool:
        with self._lock:
            return (
                isinstance(token, TempoAnalysisToken)
                and not self._shutdown
                and token is self._current
                and token.generation == self._generation
                and not token.cancelled
            )

    def require_current(self, token: TempoAnalysisToken) -> None:
        if not self.is_current(token):
            raise TempoAnalysisCancelled(
                "Tempo analysis result belongs to a stale generation."
            )

    def accept(
        self,
        token: TempoAnalysisToken,
        result: TempoAnalysisResult,
    ) -> TempoAnalysisResult:
        if not isinstance(result, TempoAnalysisResult):
            raise StudioTempoError(
                "Tempo analysis acceptance requires a TempoAnalysisResult."
            )
        # Keep both checks in one critical section.  Otherwise a newer worker
        # could supersede ``token`` after ``require_current`` returned but
        # before the result-generation check completed.
        with self._lock:
            if (
                self._shutdown
                or token is not self._current
                or token.generation != self._generation
                or token.cancelled
            ):
                raise TempoAnalysisCancelled(
                    "Tempo analysis result belongs to a stale generation."
                )
            if result.generation != token.generation:
                raise TempoAnalysisCancelled(
                    "Tempo analysis result generation does not match its token."
                )
            return result

    def cancel_current(self) -> None:
        with self._lock:
            if self._current is not None:
                self._current._cancel()
                self._current = None

    def shutdown(self) -> None:
        with self._lock:
            if self._current is not None:
                self._current._cancel()
                self._current = None
            self._shutdown = True


__all__ = [
    "CONFIDENCE_UNITS",
    "MICRO_BPM_PER_BPM",
    "TEMPO_ANALYSIS_SCHEMA_VERSION",
    "TEMPO_MAP_SCHEMA_VERSION",
    "TICKS_PER_QUARTER",
    "BarPosition",
    "FrameRounding",
    "MusicalSnapMode",
    "SnapTiePolicy",
    "StudioTempoError",
    "TempoAnalysisCancelled",
    "TempoAnalysisGuard",
    "TempoAnalysisResult",
    "TempoAnalysisToken",
    "TempoMap",
    "TempoPoint",
    "TimeSignaturePoint",
    "bpm_to_micros",
    "load_tempo_map",
]
