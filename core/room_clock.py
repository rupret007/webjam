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
    "This room has no bar, section, or reference video to follow. Work freely."
)
NO_PLACE_DETAIL = "Nobody has said where we are."
VIDEO_DETAIL = "Following the host's reference video."
SONG_DETAIL = "Following the song the room wrote."
SONG_COUNT_LIMIT = "This is a count, not what anyone is playing."
SONG_ASSUMED = "Section lengths are assumed."
SONG_WITH_TRACK = "Counted with the song playing in the room."
STALE_DETAIL = (
    "The room clock is out of date, so WebJam stopped advancing it. It picks "
    "up again when the owner is heard from."
)
PAUSED_SUFFIX = "Holding."
MAX_FORM_SHAPE_CHARS = 80


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
    # Honesty the Music overlay already states. A painter riding this pulse
    # must see the same limits: a written form, assumed lengths, and whether
    # position is coming from the song playing in the room.
    follows_shared_track: bool = False
    section_lengths_assumed: bool = False
    form_shape: str = ""

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

    @property
    def states_place(self) -> bool:
        """Whether a bar or named section was actually stated.

        A list of parts is the song's shape, not where we are. Painters ride
        a place; they do not ride an outline.
        """

        return bool(self.bar or self.section_label)


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


def _shape_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = " ".join(value.split())
    if not cleaned or not all(character.isprintable() for character in cleaned):
        return ""
    return cleaned[:MAX_FORM_SHAPE_CHARS]


def _form_shape(snapshot: object) -> str:
    """Name the written parts, or stay blank when nobody wrote any."""

    existing = _shape_text(getattr(snapshot, "form_shape", ""))
    if existing:
        return existing
    sections = getattr(snapshot, "sections", ()) or ()
    names: list[str] = []
    for section in sections:
        if isinstance(section, str):
            name = _label(section)
        else:
            name = _label(getattr(section, "name", ""))
        if name and name not in names:
            names.append(name)
        if len(names) >= 5:
            break
    if not names:
        return ""
    return " → ".join(names)[:MAX_FORM_SHAPE_CHARS]


def _stated_meter(snapshot: object) -> tuple[int, int]:
    """Return a meter only when both numbers were stated.

    The song clock counts in four when nobody wrote a time signature. That
    default is a counting assumption, not a 4/4 the room decided, so it must
    not be published as a meter. A 4-over-0 pair also cannot go on the wire.
    """

    numerator = _counted(
        getattr(snapshot, "meter_numerator", 0), MAX_ROOM_CLOCK_BEAT
    )
    denominator = _counted(
        getattr(snapshot, "meter_denominator", 0), MAX_ROOM_CLOCK_BEAT
    )
    if numerator and denominator:
        return numerator, denominator
    return 0, 0


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
    numerator, denominator = _stated_meter(projection)
    follows_track = bool(getattr(projection, "follows_shared_track", False))
    assumed = bool(getattr(projection, "section_lengths_assumed", False))
    form_shape = _form_shape(projection)

    parts: list[str] = []
    if bar:
        # Beat is only meaningful beside a bar, so it never appears alone.
        parts.append(f"Bar {bar}.{beat}" if beat else f"Bar {bar}")
    if section:
        parts.append(section)
    if not parts:
        # A written shape without a stated bar or section is still no
        # clock. Name the outline so a painter is not told the song is
        # absent; never put a part, a bar, or elapsed time in the headline.
        if form_shape:
            return RoomClockView(
                source=RoomClockSource.SONG_FORM,
                headline=NO_CLOCK_HEADLINE,
                detail=f"{form_shape} is written. {NO_PLACE_DETAIL}",
                running=False,
                stale=False,
                musical=False,
            )
        return RoomClockView()
    headline = " · ".join(parts)

    if stale:
        detail = STALE_DETAIL
    else:
        clauses = [SONG_DETAIL]
        if form_shape and form_shape != section:
            clauses.append(form_shape + ".")
        if follows_track:
            clauses.append(SONG_WITH_TRACK)
        if assumed:
            clauses.append(SONG_ASSUMED)
        if not running:
            clauses.append(PAUSED_SUFFIX)
        clauses.append(SONG_COUNT_LIMIT)
        if tempo or (numerator and denominator):
            stated: list[str] = []
            if tempo:
                stated.append(f"{tempo:g} BPM")
            if numerator and denominator:
                stated.append(f"{numerator}/{denominator}")
            clauses.append(" · ".join(stated) + ".")
        detail = " ".join(clauses)
    return RoomClockView(
        source=RoomClockSource.SONG_FORM,
        headline=headline,
        detail=detail,
        running=running and not stale,
        stale=stale,
        musical=True,
    )


def _parked_count_is_not_a_place(snapshot: object) -> bool:
    """Return whether the clock is sitting at the top without a stated place.

    A live ``SongClock`` with a tempo always reports bar 1 and the first
    part while it is stopped. That is where the count parks, not a place
    anyone said. Starting, pausing, locating a part, or following Shared
    Track still states a where. Snapshots that do not carry ``state``
    (legacy test seams and already-published pulses) are left alone.
    """

    if str(getattr(snapshot, "state", "") or "") != "stopped":
        return False
    if bool(getattr(snapshot, "follows_shared_track", False)):
        return False
    return (
        _positive(getattr(snapshot, "position_s", 0.0), MAX_ROOM_CLOCK_POSITION_S)
        <= 0.0
    )


def song_form_facts(snapshot: object) -> RoomClockFacts | None:
    """Read a song-clock snapshot as room-clock facts, or ``None``.

    This is the seam a music surface publishes into. It translates only a
    written position: the bar or section the owner already stated. A file
    playing with no written parts is not a song form. A written outline
    with no current bar or section is not a position either — dressing the
    first part, or the file's elapsed time, up as form would be the lie
    this module exists to prevent. A stopped count that only has a tempo
    still parks on that first part; publishing it as Verse would be the
    same lie. That outline is still named, so the room does not claim the
    song is absent. Art never calls this with invented bars.
    """

    if snapshot is None:
        return None
    sections = getattr(snapshot, "sections", ()) or ()
    has_form = bool(getattr(snapshot, "has_form", False) or sections)
    if not has_form:
        return None
    bar = _counted(getattr(snapshot, "bar", 0), MAX_ROOM_CLOCK_BAR)
    beat = _counted(getattr(snapshot, "beat", 0), MAX_ROOM_CLOCK_BEAT)
    section = _label(getattr(snapshot, "section_label", ""))
    form_shape = _form_shape(snapshot)
    # A stopped clock with a tempo still reports bar 1 and the first
    # part. That parked count is not a place. Drop it so the publish
    # path names the outline the same way a tempo-less outline does.
    if _parked_count_is_not_a_place(snapshot):
        bar, beat, section = 0, 0, ""
    # A list of parts is the song's shape, not where we are. Do not
    # invent Verse from the outline, and do not carry the file's timer
    # as if it were a running clock.
    if not bar and not section:
        if not form_shape:
            return None
        return RoomClockFacts(
            source=RoomClockSource.SONG_FORM,
            form_shape=form_shape,
        )
    numerator, denominator = _stated_meter(snapshot)
    return RoomClockFacts(
        source=RoomClockSource.SONG_FORM,
        running=bool(getattr(snapshot, "running", False)),
        position_s=_positive(
            getattr(snapshot, "position_s", 0.0), MAX_ROOM_CLOCK_POSITION_S
        ),
        bar=bar,
        beat=beat if bar else 0,
        section_label=section,
        tempo_bpm=_positive(getattr(snapshot, "tempo_bpm", 0.0), MAX_TEMPO_BPM),
        meter_numerator=numerator,
        meter_denominator=denominator,
        follows_shared_track=bool(
            getattr(snapshot, "follows_shared_track", False)
        ),
        section_lengths_assumed=bool(
            getattr(snapshot, "section_lengths_assumed", False)
        ),
        form_shape=form_shape,
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

    A stated place in a song is the stronger pulse: when a band is playing,
    the painter should be riding bars, not a file offset. A written outline
    without a bar or section is not a place — a reference video still speaks
    then, because a file offset is a where and an outline is only a shape.
    When neither a place nor a video exists, the outline may still be named
    so the room does not claim the song is absent.
    """

    if isinstance(song_form, RoomClockFacts) and song_form.states_place:
        return song_form
    if isinstance(video, RoomClockFacts) and (
        video.source is RoomClockSource.REFERENCE_VIDEO
    ):
        return video
    if isinstance(song_form, RoomClockFacts) and (
        song_form.source is RoomClockSource.SONG_FORM
    ):
        return song_form
    return RoomClockFacts()


__all__ = [
    "DEFAULT_STALE_AFTER_S",
    "MAX_ROOM_CLOCK_BAR",
    "MAX_ROOM_CLOCK_BEAT",
    "MAX_ROOM_CLOCK_POSITION_S",
    "MAX_SECTION_LABEL_CHARS",
    "MAX_TEMPO_BPM",
    "MIN_TEMPO_BPM",
    "MAX_FORM_SHAPE_CHARS",
    "NO_CLOCK_DETAIL",
    "NO_CLOCK_HEADLINE",
    "NO_PLACE_DETAIL",
    "SONG_ASSUMED",
    "SONG_COUNT_LIMIT",
    "SONG_DETAIL",
    "SONG_WITH_TRACK",
    "STALE_DETAIL",
    "VIDEO_DETAIL",
    "RoomClockFacts",
    "RoomClockProjection",
    "RoomClockSource",
    "RoomClockView",
    "format_clock",
    "reference_video_facts",
    "render_room_clock",
    "song_form_facts",
    "stronger_facts",
]
