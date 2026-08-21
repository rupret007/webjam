"""One host clock over one song form, readable by every creator profile.

This is the spine of the shared room. A live session already has a host, a
form, and a tempo; what it has never had is one agreed answer to *where are
we* that anything other than the mixer can read. That answer is this module.

What it is
----------
A conductor's clock. The host starts it, and it counts beats, bars, and
sections from a stated or detected tempo across the form the room wrote. It is
a **shared reference**, exactly like a click track: everyone reads the same
position because everyone reads the same clock.

What it is not
--------------
It does not listen. WebJam performs no beat tracking on the live Jamulus mix,
so this clock never claims to follow what the band is actually playing. If the
band rushes, the clock does not. That limit is deliberate and is why
:class:`SongClockSnapshot` reports ``following_audio = False`` — a subscriber
must be able to tell a reference from a measurement.

Why it is separate from Music
-----------------------------
The snapshot carries section, bar, beat, key, tempo, and position and nothing
about audio. A painter in another creator profile can subscribe to it and mark
up a canvas in bars without importing anything from Music, because there is
nothing musical to import — it is a timeline with names on it. That is the
whole point of publishing it here rather than inside the Song panel.

Threading
---------
:class:`SongClockPublisher` is safe to read from any thread. Subscribers are
called on whichever thread published, so a UI subscriber must marshal.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from core.song_form import DETECTED, STATED, SongForm

# Used when the room has not said how long a part runs. Eight bars is the most
# common pop section length; the snapshot reports which sections were stated so
# a subscriber can tell an assumption from a decision.
DEFAULT_SECTION_BARS = 8
DEFAULT_BEATS_PER_BAR = 4
DEFAULT_TEMPO_BPM = 100.0

MIN_TEMPO_BPM = 20.0
MAX_TEMPO_BPM = 300.0

STATE_STOPPED = "stopped"
STATE_RUNNING = "running"
STATE_PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class FormSection:
    """One part of the form as the clock sees it: a name and a length."""

    name: str
    role: str
    bars: int
    bars_stated: bool
    chords: tuple[str, ...] = ()

    @property
    def chord_line(self) -> str:
        return " ".join(self.chords)


@dataclass(frozen=True, slots=True)
class SongClockSnapshot:
    """Where the room is, as anything in the app may read it.

    This is the published contract. Every field is either a plain number, a
    plain string, or a tuple of them, so a subscriber in another creator
    profile needs no Music types to use it.
    """

    generation: int = 0
    state: str = STATE_STOPPED
    position_s: float = 0.0

    # Musical position. ``bar`` and ``beat`` are 1-indexed the way a musician
    # counts them; ``section_index`` is 0-indexed the way a list is.
    section_index: int = -1
    section_label: str = ""
    section_role: str = ""
    bar: int = 0
    bar_in_section: int = 0
    beat: int = 0
    bars_total: int = 0

    # Song truth, each with where it came from.
    key: str = ""
    key_source: str = ""
    tempo_bpm: float = 0.0
    tempo_source: str = ""
    beats_per_bar: int = DEFAULT_BEATS_PER_BAR

    chords_now: tuple[str, ...] = ()
    sections: tuple[FormSection, ...] = ()

    # A reference, never a measurement. See the module docstring.
    following_audio: bool = False
    section_lengths_assumed: bool = False

    @property
    def running(self) -> bool:
        return self.state == STATE_RUNNING

    @property
    def has_form(self) -> bool:
        return bool(self.sections)

    @property
    def position_label(self) -> str:
        """Return "Chorus · bar 3 of 8" style text, or an honest blank."""

        if not self.section_label:
            return ""
        section = next(
            (item for item in self.sections if item.name == self.section_label),
            None,
        )
        if section is None or section.bars <= 0:
            return self.section_label
        return f"{self.section_label} · bar {self.bar_in_section} of {section.bars}"

    def describe(self) -> str:
        """Return one line a conductor surface can show without decoration."""

        if not self.has_form:
            return "No song form yet."
        where = self.position_label or "Not started"
        parts = [where]
        if self.tempo_bpm:
            parts.append(f"{int(round(self.tempo_bpm))} BPM")
        if self.key:
            parts.append(self.key)
        if self.chords_now:
            parts.append(" ".join(self.chords_now[:4]))
        return " · ".join(parts)

    def to_public_dict(self) -> dict:
        """Return the cross-profile read model as plain JSON-ready values.

        Other creator profiles consume this rather than the dataclass, so the
        contract stays explicit and additive. No audio, no file paths, no
        participant identity ever enters it.
        """

        return {
            "generation": self.generation,
            "state": self.state,
            "position_s": round(self.position_s, 3),
            "section": self.section_label,
            "section_index": self.section_index,
            "section_role": self.section_role,
            "bar": self.bar,
            "bar_in_section": self.bar_in_section,
            "beat": self.beat,
            "bars_total": self.bars_total,
            "beats_per_bar": self.beats_per_bar,
            "key": self.key,
            "key_source": self.key_source,
            "bpm": round(self.tempo_bpm, 3) if self.tempo_bpm else 0.0,
            "bpm_source": self.tempo_source,
            "chords_now": list(self.chords_now),
            "sections": [
                {
                    "name": section.name,
                    "role": section.role,
                    "bars": section.bars,
                    "bars_stated": section.bars_stated,
                }
                for section in self.sections
            ],
            "following_audio": self.following_audio,
            "section_lengths_assumed": self.section_lengths_assumed,
        }


def form_sections(form: SongForm) -> tuple[FormSection, ...]:
    """Return the clock's view of a song form."""

    return tuple(
        FormSection(
            name=section.label,
            role=section.role,
            bars=section.bars if section.bars_stated else DEFAULT_SECTION_BARS,
            bars_stated=section.bars_stated,
            chords=section.chords,
        )
        for section in form.sections
    )


class SongClock:
    """A host-run clock over one song form.

    The clock owns no timer. It computes position from the instant it was
    started and the tempo it was given, so it is exact, testable, and cheap to
    poll from a UI repaint.
    """

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._sections: tuple[FormSection, ...] = ()
        self._tempo = 0.0
        self._tempo_source = ""
        self._key = ""
        self._key_source = ""
        self._beats_per_bar = DEFAULT_BEATS_PER_BAR
        self._state = STATE_STOPPED
        self._started_at = 0.0
        self._offset_s = 0.0
        self._generation = 0

    # ------------------------------------------------------------------
    # Form and tempo
    # ------------------------------------------------------------------
    def set_form(self, form: SongForm) -> None:
        """Adopt the room's current song. Position is kept where possible."""

        with self._lock:
            self._sections = form_sections(form)
            self._beats_per_bar = form.beats_per_bar
            if form.key is not None and form.key.value:
                self._key = form.key.value
                self._key_source = form.key.source
            else:
                self._key = ""
                self._key_source = ""
            if form.tempo is not None and form.tempo.value:
                try:
                    tempo = float(form.tempo.value)
                except (TypeError, ValueError):
                    tempo = 0.0
                if MIN_TEMPO_BPM <= tempo <= MAX_TEMPO_BPM:
                    self._tempo = tempo
                    self._tempo_source = form.tempo.source
            self._clamp_offset()
            self._generation += 1

    def set_tempo(self, bpm: float, *, source: str = STATED) -> bool:
        """Override the tempo the clock counts at. Returns whether it took."""

        try:
            value = float(bpm)
        except (TypeError, ValueError):
            return False
        if not MIN_TEMPO_BPM <= value <= MAX_TEMPO_BPM:
            return False
        with self._lock:
            # Keep the musical position across a tempo change rather than
            # teleporting: the room is still in the same bar.
            position = self._position_seconds_locked()
            beats = self._beats_at_locked(position)
            self._tempo = value
            self._tempo_source = source if source in {STATED, DETECTED} else STATED
            self._offset_s = beats * (60.0 / value)
            if self._state == STATE_RUNNING:
                self._started_at = self._monotonic()
            self._generation += 1
        return True

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def start(self) -> bool:
        """Start counting from the current position."""

        with self._lock:
            if not self._sections or self._effective_tempo() <= 0:
                return False
            if self._state != STATE_RUNNING:
                self._started_at = self._monotonic()
                self._state = STATE_RUNNING
                self._generation += 1
            return True

    def pause(self) -> None:
        with self._lock:
            if self._state == STATE_RUNNING:
                self._offset_s = self._position_seconds_locked()
                self._state = STATE_PAUSED
                self._generation += 1

    def stop(self) -> None:
        with self._lock:
            if self._state != STATE_STOPPED or self._offset_s:
                self._state = STATE_STOPPED
                self._offset_s = 0.0
                self._generation += 1

    def locate_section(self, name: str) -> bool:
        """Jump the clock to the top of a named part."""

        target = str(name or "").strip().lower()
        with self._lock:
            elapsed_bars = 0
            for section in self._sections:
                if section.name.lower() == target:
                    self._offset_s = elapsed_bars * self._seconds_per_bar()
                    if self._state == STATE_RUNNING:
                        self._started_at = self._monotonic()
                    self._generation += 1
                    return True
                elapsed_bars += max(1, section.bars)
        return False

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def snapshot(self) -> SongClockSnapshot:
        """Return the current position. Safe to call from any thread."""

        with self._lock:
            position = self._position_seconds_locked()
            tempo = self._effective_tempo()
            beats_per_bar = max(1, self._beats_per_bar)
            total_bars = sum(max(1, item.bars) for item in self._sections)

            if not self._sections or tempo <= 0:
                return SongClockSnapshot(
                    generation=self._generation,
                    state=self._state,
                    position_s=position,
                    key=self._key,
                    key_source=self._key_source,
                    tempo_bpm=tempo,
                    tempo_source=self._tempo_source,
                    beats_per_bar=beats_per_bar,
                    sections=self._sections,
                    bars_total=total_bars,
                    section_lengths_assumed=self._lengths_assumed(),
                )

            beats = self._beats_at_locked(position)
            bar_index = int(beats // beats_per_bar)
            beat = int(beats % beats_per_bar) + 1
            index, bar_in_section = self._locate_bar(bar_index)
            section = self._sections[index] if index >= 0 else None
            # Past the written end the clock holds on the last part, so the
            # reported bar holds too rather than counting into bars nobody
            # wrote.
            bar_index = min(bar_index, max(0, total_bars - 1))

            return SongClockSnapshot(
                generation=self._generation,
                state=self._state,
                position_s=position,
                section_index=index,
                section_label=section.name if section is not None else "",
                section_role=section.role if section is not None else "",
                bar=bar_index + 1,
                bar_in_section=bar_in_section,
                beat=beat,
                bars_total=total_bars,
                key=self._key,
                key_source=self._key_source,
                tempo_bpm=tempo,
                tempo_source=self._tempo_source,
                beats_per_bar=beats_per_bar,
                chords_now=section.chords if section is not None else (),
                sections=self._sections,
                section_lengths_assumed=self._lengths_assumed(),
            )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _effective_tempo(self) -> float:
        return self._tempo if self._tempo > 0 else 0.0

    def _seconds_per_bar(self) -> float:
        tempo = self._effective_tempo() or DEFAULT_TEMPO_BPM
        return (60.0 / tempo) * max(1, self._beats_per_bar)

    def _position_seconds_locked(self) -> float:
        if self._state != STATE_RUNNING:
            return self._offset_s
        return self._offset_s + max(0.0, self._monotonic() - self._started_at)

    def _beats_at_locked(self, position_s: float) -> float:
        tempo = self._effective_tempo()
        if tempo <= 0:
            return 0.0
        return max(0.0, position_s) * (tempo / 60.0)

    def _locate_bar(self, bar_index: int) -> tuple[int, int]:
        """Return the section holding ``bar_index`` and the bar within it."""

        remaining = max(0, bar_index)
        for index, section in enumerate(self._sections):
            length = max(1, section.bars)
            if remaining < length:
                return index, remaining + 1
            remaining -= length
        if not self._sections:
            return -1, 0
        # Past the end of the written form: hold on the last part rather than
        # inventing an arrangement the room never wrote.
        last = len(self._sections) - 1
        return last, max(1, self._sections[last].bars)

    def _clamp_offset(self) -> None:
        total_bars = sum(max(1, item.bars) for item in self._sections)
        if total_bars <= 0:
            self._offset_s = 0.0
            return
        limit = total_bars * self._seconds_per_bar()
        self._offset_s = min(self._offset_s, limit)

    def _lengths_assumed(self) -> bool:
        return any(not section.bars_stated for section in self._sections)


class SongClockPublisher:
    """Publishes one clock's snapshot to any profile that subscribes.

    Subscribers receive :class:`SongClockSnapshot` objects. A subscriber that
    raises is dropped rather than allowed to break the room; a broken canvas
    must never stop a jam.
    """

    def __init__(self, clock: SongClock) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._subscribers: list[Callable[[SongClockSnapshot], None]] = []
        self._last_generation = -1

    @property
    def clock(self) -> SongClock:
        return self._clock

    def subscribe(
        self,
        callback: Callable[[SongClockSnapshot], None],
    ) -> Callable[[], None]:
        """Register ``callback`` and return the function that unsubscribes it."""

        if not callable(callback):
            raise TypeError("a song clock subscriber must be callable")
        with self._lock:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)

        return unsubscribe

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def snapshot(self) -> SongClockSnapshot:
        return self._clock.snapshot()

    def publish(self, *, force: bool = False) -> SongClockSnapshot:
        """Push the current snapshot to subscribers and return it."""

        snapshot = self._clock.snapshot()
        with self._lock:
            changed = force or snapshot.generation != self._last_generation
            self._last_generation = snapshot.generation
            listeners = list(self._subscribers)
        if not changed and not snapshot.running:
            return snapshot
        for callback in listeners:
            try:
                callback(snapshot)
            except Exception:  # noqa: BLE001 - a subscriber cannot break the room
                self._drop(callback)
        return snapshot

    def _drop(self, callback: Callable[[SongClockSnapshot], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)


def describe_contract() -> dict:
    """Return the published field contract, for docs and cross-profile tests.

    Keeping this beside the model means a profile that reads the clock can
    assert the fields it depends on still exist, instead of discovering a
    rename at runtime.
    """

    return {
        "version": 1,
        "fields": tuple(SongClockSnapshot().to_public_dict().keys()),
        "guarantees": (
            "position is a host-run reference, not audio-followed",
            "every musical fact carries its source",
            "no audio, file path, or participant identity is published",
        ),
    }


__all__ = [
    "DEFAULT_BEATS_PER_BAR",
    "DEFAULT_SECTION_BARS",
    "MAX_TEMPO_BPM",
    "MIN_TEMPO_BPM",
    "STATE_PAUSED",
    "STATE_RUNNING",
    "STATE_STOPPED",
    "FormSection",
    "SongClock",
    "SongClockPublisher",
    "SongClockSnapshot",
    "describe_contract",
    "form_sections",
]
