"""One pulse the whole room can read, whatever kind of maker you are.

This is the small load-bearing piece of the idea that a band playing, a painter
on the shared canvas, and someone watching a reference still can all be in the
*same* room at the same moment. Each of those surfaces already knows its own
state. What none of them had was a shared answer to "where are we right now",
stated once, by whoever actually owns the pulse.

A room clock has exactly one owner at a time, and its source is always named:

* ``SONG_FORM`` -- something in the room owns a song: a bar, a beat, a section,
  maybe a tempo and a meter. A painter can ride that.
* ``REFERENCE_VIDEO`` -- Art's host-clocked reference video is running, so the
  pulse is a position in that file.
* ``NONE`` -- there is no pulse. This is a first-class answer, not a
  degraded one; a room where people just talk and work has no clock, and
  saying so plainly is the honest thing.

Two rules keep this from quietly becoming a music engine, which it must not
be:

1. **Art never invents a musical position.** Bars, beats, sections, tempo, and
   meter are rendered exactly as the owner published them and are never
   advanced, interpolated, or derived here. A video position is a position in a
   file and can never carry a bar number -- the wire schema refuses that
   combination outright, so it is not a matter of discipline.
2. **Only elapsed time is extrapolated**, and only for a running clock, by the
   locally measured age of the projection. That is the same bounded trick the
   reference video follower already uses, and it needs no clock
   synchronization between computers.

Nothing in this module talks to a network or a UI toolkit. It turns one
published fact into one honest line, and it is the seam a music surface can
publish into later without any of the painting surfaces changing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

MAX_ROOM_CLOCK_POSITION_S = 24.0 * 60.0 * 60.0
MAX_ROOM_CLOCK_BAR = 100_000
MAX_ROOM_CLOCK_BEAT = 64
MAX_SECTION_LABEL_CHARS = 48
MIN_TEMPO_BPM = 20.0
MAX_TEMPO_BPM = 400.0

#: Six missed polls of the private peer plane, matching the reference video's
#: bound. Past that a follower says the pulse is out of date instead of
#: advancing a number nobody is holding.
DEFAULT_STALE_AFTER_S = 5.0

NO_CLOCK_HEADLINE = "No shared clock"
NO_CLOCK_DETAIL = (
    "This room has no song form and no reference video running. Work freely."
)
VIDEO_DETAIL = "Following the host's reference video."
SONG_DETAIL = "Following the room's song form."
STALE_DETAIL = (
    "The room clock is out of date, so WebJam stopped advancing it. It picks "
    "up again when the owner is heard from."
)
PAUSED_SUFFIX = "Holding."


class RoomClockSource(str, Enum):
    """Who owns the pulse. Never guessed; always stated by the publisher."""

    NONE = "none"
    REFERENCE_VIDEO = "reference_video"
    SONG_FORM = "song_form"


@dataclass(frozen=True, slots=True)
class RoomClockFacts:
    """What an owner states about the room's pulse.

    A ``SONG_FORM`` owner may state musical position; a ``REFERENCE_VIDEO``
    owner may not, and the wire schema enforces that rather than trusting it.
    Every field left at its default means "not stated", which is different
    from zero: bar 0 does not exist, and a tempo of 0 is not a tempo.
    """

    source: RoomClockSource = RoomClockSource.NONE
    running: bool = False
    position_s: float = 0.0
    duration_s: float = 0.0
    bar: int = 0
    beat: int = 0
    section_label: str = ""
    tempo_bpm: float = 0.0
    meter_numerator: int = 0
    meter_denominator: int = 0

    @property
    def states_music(self) -> bool:
        return bool(
            self.bar
            or self.beat
            or self.section_label
            or self.tempo_bpm
            or self.meter_numerator
            or self.meter_denominator
        )


@runtime_checkable
class RoomClockProjection(Protocol):
    """Owner-published pulse a surface may render.

    ``core.session_transfer.RoomClockSessionSnapshot`` satisfies this
    structurally, which keeps the wire schema out of this module's import
    graph and this module out of the transfer layer's.
    """

    @property
    def source(self) -> object: ...

    @property
    def running(self) -> bool: ...

    @property
    def position_s(self) -> float: ...

    @property
    def duration_s(self) -> float: ...

    @property
    def bar(self) -> int: ...

    @property
    def beat(self) -> int: ...

    @property
    def section_label(self) -> str: ...

    @property
    def tempo_bpm(self) -> float: ...

    @property
    def meter_numerator(self) -> int: ...

    @property
    def meter_denominator(self) -> int: ...


@dataclass(frozen=True, slots=True)
class RoomClockView:
    """One calm line, plus enough truth to explain it.

    ``headline`` is what a person glances at. ``detail`` says where the pulse
    came from, or why it stopped. ``musical`` is the honest marker that a bar
    or a section in ``headline`` was published by a song owner rather than
    computed here.
    """

    source: RoomClockSource = RoomClockSource.NONE
    headline: str = NO_CLOCK_HEADLINE
    detail: str = NO_CLOCK_DETAIL
    running: bool = False
    stale: bool = False
    musical: bool = False

    @property
    def present(self) -> bool:
        return self.source is not RoomClockSource.NONE


def format_clock(seconds: object) -> str:
    """Render a bounded, non-negative position as ``H:MM:SS`` or ``M:SS``."""

    try:
        value = float(seconds)
    except (TypeError, ValueError):
        value = 0.0
    if not math.isfinite(value) or value < 0.0:
        value = 0.0
    bounded = int(min(value, MAX_ROOM_CLOCK_POSITION_S))
    hours, remainder = divmod(bounded, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def _positive(value: object, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(parsed) or parsed <= 0.0:
        return 0.0
    return min(parsed, maximum)


def _counted(value: object, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value if 1 <= value <= maximum else 0


def _label(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split())
    if not cleaned or not all(character.isprintable() for character in cleaned):
        return ""
    return cleaned[:MAX_SECTION_LABEL_CHARS]


def _source(value: object) -> RoomClockSource:
    try:
        return RoomClockSource(value)
    except ValueError:
        # The projection came from another computer. An unknown source is no
        # clock rather than a guess about which kind it might be.
        return RoomClockSource.NONE


def render_room_clock(
    projection: object,
    *,
    age_s: float = 0.0,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
) -> RoomClockView:
    """Turn one published projection into the line a surface shows.

    ``age_s`` is measured locally -- the time since this computer received the
    projection -- so no clock is shared between machines. A running clock
    advances by that age; a musical position never does.
    """

    if projection is None:
        return RoomClockView()
    source = _source(getattr(projection, "source", RoomClockSource.NONE))
    if source is RoomClockSource.NONE:
        return RoomClockView()

    running = bool(getattr(projection, "running", False))
    age = _positive(age_s, MAX_ROOM_CLOCK_POSITION_S)
    stale = bool(running and stale_after_s > 0.0 and age > float(stale_after_s))

    if source is RoomClockSource.REFERENCE_VIDEO:
        return _video_view(projection, running=running, age=age, stale=stale)
    return _song_view(projection, running=running, stale=stale)


def _video_view(
    projection: object, *, running: bool, age: float, stale: bool
) -> RoomClockView:
    duration = _positive(getattr(projection, "duration_s", 0.0), MAX_ROOM_CLOCK_POSITION_S)
    position = _positive(
        getattr(projection, "position_s", 0.0), MAX_ROOM_CLOCK_POSITION_S
    )
    if running and not stale:
        # Elapsed time is the only thing extrapolated anywhere in this module.
        position += age
    if duration > 0.0:
        position = min(position, duration)
        headline = f"{format_clock(position)} / {format_clock(duration)}"
    else:
        headline = format_clock(position)

    detail = VIDEO_DETAIL
    if stale:
        detail = STALE_DETAIL
    elif not running:
        detail = f"{VIDEO_DETAIL} {PAUSED_SUFFIX}"
    return RoomClockView(
        source=RoomClockSource.REFERENCE_VIDEO,
        headline=headline,
        detail=detail,
        running=running and not stale,
        stale=stale,
        musical=False,
    )


def _song_view(projection: object, *, running: bool, stale: bool) -> RoomClockView:
    bar = _counted(getattr(projection, "bar", 0), MAX_ROOM_CLOCK_BAR)
    beat = _counted(getattr(projection, "beat", 0), MAX_ROOM_CLOCK_BEAT)
    section = _label(getattr(projection, "section_label", ""))
    tempo = _positive(getattr(projection, "tempo_bpm", 0.0), MAX_TEMPO_BPM)
    numerator = _counted(getattr(projection, "meter_numerator", 0), MAX_ROOM_CLOCK_BEAT)
    denominator = _counted(
        getattr(projection, "meter_denominator", 0), MAX_ROOM_CLOCK_BEAT
    )

    parts: list[str] = []
    if bar:
        # Beat is only meaningful beside a bar, so it never appears alone.
        parts.append(f"Bar {bar}.{beat}" if beat else f"Bar {bar}")
    if section:
        parts.append(section)
    if not parts:
        position = _positive(
            getattr(projection, "position_s", 0.0), MAX_ROOM_CLOCK_POSITION_S
        )
        parts.append(format_clock(position))
    headline = " · ".join(parts)

    detail = SONG_DETAIL
    if stale:
        detail = STALE_DETAIL
    elif not running:
        detail = f"{SONG_DETAIL} {PAUSED_SUFFIX}"
    if not stale and (tempo or (numerator and denominator)):
        stated: list[str] = []
        if tempo:
            stated.append(f"{tempo:g} BPM")
        if numerator and denominator:
            stated.append(f"{numerator}/{denominator}")
        detail = f"{detail} {' · '.join(stated)}."
    return RoomClockView(
        source=RoomClockSource.SONG_FORM,
        headline=headline,
        detail=detail,
        running=running and not stale,
        stale=stale,
        musical=bool(bar or section),
    )


def reference_video_facts(
    snapshot: object, *, playing_state: object = "playing"
) -> RoomClockFacts | None:
    """Read a reference video host snapshot as room-clock facts, or ``None``.

    This is the only translation Art performs, and it deliberately carries no
    musical position: a file offset is not a bar, and pretending otherwise is
    exactly the line this module refuses to cross.
    """

    if snapshot is None or not bool(getattr(snapshot, "shared", False)):
        return None
    state = getattr(snapshot, "state", None)
    running = str(getattr(state, "value", state) or "") == str(
        getattr(playing_state, "value", playing_state)
    )
    return RoomClockFacts(
        source=RoomClockSource.REFERENCE_VIDEO,
        running=running,
        position_s=_positive(
            getattr(snapshot, "position_s", 0.0), MAX_ROOM_CLOCK_POSITION_S
        ),
        duration_s=_positive(
            getattr(snapshot, "duration_s", 0.0), MAX_ROOM_CLOCK_POSITION_S
        ),
    )


def stronger_facts(
    song_form: RoomClockFacts | None, video: RoomClockFacts | None
) -> RoomClockFacts:
    """Choose which owner speaks for the room.

    A song in the room is the stronger pulse: when a band is playing, the
    painter should be riding bars, not a file offset. A reference video speaks
    only when no song does, and when neither does the room honestly has no
    clock.
    """

    for candidate in (song_form, video):
        if isinstance(candidate, RoomClockFacts) and (
            candidate.source is not RoomClockSource.NONE
        ):
            return candidate
    return RoomClockFacts()


__all__ = [
    "DEFAULT_STALE_AFTER_S",
    "MAX_ROOM_CLOCK_BAR",
    "MAX_ROOM_CLOCK_BEAT",
    "MAX_ROOM_CLOCK_POSITION_S",
    "MAX_SECTION_LABEL_CHARS",
    "MAX_TEMPO_BPM",
    "MIN_TEMPO_BPM",
    "NO_CLOCK_DETAIL",
    "NO_CLOCK_HEADLINE",
    "SONG_DETAIL",
    "STALE_DETAIL",
    "VIDEO_DETAIL",
    "RoomClockFacts",
    "RoomClockProjection",
    "RoomClockSource",
    "RoomClockView",
    "format_clock",
    "reference_video_facts",
    "render_room_clock",
    "stronger_facts",
]
