"""Local rehearsal order and notes, independent of the live song/audio clock.

SongClock is a musical reference and SessionStrip is a UI timer. Neither is
recording evidence. A moment carries a take position only when its caller
supplies verified timing and a take identity that can be checked again on open.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
from uuid import uuid4

from core.song_clock import MAX_TEMPO_BPM, MIN_TEMPO_BPM

PLAN_VERSION = 1
MAX_SONGS = 200
MAX_BOOKMARKS = 500
MAX_PLAN_FILE_BYTES = 2 * 1024 * 1024


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _id(value: object = None) -> str:
    return value if isinstance(value, str) and 0 < len(value) <= 100 else uuid4().hex


def _tempo(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if math.isfinite(value) and MIN_TEMPO_BPM <= value <= MAX_TEMPO_BPM else None


def make_bookmark(
    note: str, *, take_id: str | None = None, take_path: str | None = None,
    source_identity: str | None = None, position_seconds: float | None = None,
    timing_verified: bool = False,
) -> dict:
    """Keep a plain note unless the caller supplies complete timing evidence."""
    timed = (
        timing_verified is True
        and all(isinstance(value, str) and value.strip()
                for value in (take_id, take_path, source_identity))
        and isinstance(position_seconds, (int, float))
        and not isinstance(position_seconds, bool)
        and math.isfinite(position_seconds)
        and position_seconds >= 0
    )
    return {
        "id": _id(), "note": _text(note).strip() or "Moment",
        "take_id": take_id if timed else None,
        "take_path": take_path if timed else None,
        "source_identity": source_identity if timed else None,
        "position_seconds": float(position_seconds) if timed else None,
    }


def _bookmark(payload: dict) -> dict:
    result = make_bookmark(
        _text(payload.get("note")), take_id=payload.get("take_id"),
        take_path=payload.get("take_path"), source_identity=payload.get("source_identity"),
        position_seconds=payload.get("position_seconds"), timing_verified=True,
    )
    # Imported evidence is retained for a later current-file validation; this
    # does not assert that a file still exists or still contains the same take.
    result["id"] = _id(payload.get("id"))
    return result


def make_song(title: str = "New song", **values) -> dict:
    bookmarks = values.get("bookmarks", [])
    if not isinstance(bookmarks, list) or len(bookmarks) > MAX_BOOKMARKS:
        raise ValueError("A song can contain up to 500 moments.")
    return {
        "id": _id(values.get("id")), "title": _text(title),
        "key": _text(values.get("key")), "tempo": _tempo(values.get("tempo")),
        "goals": _text(values.get("goals")), "notes": _text(values.get("notes")),
        "next_steps": _text(values.get("next_steps")),
        "moment_draft": _text(values.get("moment_draft")),
        "completed": values.get("completed") is True,
        "bookmarks": [_bookmark(item) for item in bookmarks if isinstance(item, dict)],
    }


@dataclass
class RehearsalPlan:
    title: str = "Rehearsal plan"
    songs: list[dict] = field(default_factory=list)
    active_song_id: str = ""

    @classmethod
    def from_payload(cls, payload: dict) -> RehearsalPlan:
        if not isinstance(payload, dict):
            raise ValueError("This file does not contain a rehearsal plan.")
        if payload.get("version", PLAN_VERSION) != PLAN_VERSION:
            raise ValueError("This rehearsal plan uses an unsupported version.")
        raw = payload.get("songs", [])
        if not isinstance(raw, list) or len(raw) > MAX_SONGS:
            raise ValueError("A rehearsal plan can contain up to 200 songs.")
        songs = []
        seen = set()
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("Each song must be a song entry.")
            song = make_song(**{k: v for k, v in item.items() if k != "title"},
                             title=_text(item.get("title")))
            if song["id"] in seen:
                song["id"] = _id()
            seen.add(song["id"])
            songs.append(song)
        active = _text(payload.get("active_song_id"))
        if active not in seen:
            active = songs[0]["id"] if songs else ""
        return cls(_text(payload.get("title")) or "Rehearsal plan", songs, active)

    def payload(self) -> dict:
        return deepcopy({"version": PLAN_VERSION, "title": self.title,
                         "active_song_id": self.active_song_id, "songs": self.songs})

    @property
    def current(self) -> dict | None:
        return next((song for song in self.songs if song["id"] == self.active_song_id), None)

    @property
    def index(self) -> int:
        return next((i for i, song in enumerate(self.songs)
                     if song["id"] == self.active_song_id), -1)

    def add_song(self, title: str = "New song") -> dict:
        if len(self.songs) >= MAX_SONGS:
            raise ValueError("This plan already has 200 songs.")
        song = make_song(title)
        self.songs.append(song)
        self.active_song_id = song["id"]
        return song

    def select(self, song_id: str) -> bool:
        if not any(song["id"] == song_id for song in self.songs):
            return False
        changed = self.active_song_id != song_id
        self.active_song_id = song_id
        return changed

    def advance(self, delta: int) -> bool:
        index = self.index + delta
        return 0 <= index < len(self.songs) and self.select(self.songs[index]["id"])

    def move(self, delta: int) -> bool:
        index, target = self.index, self.index + delta
        if index < 0 or not 0 <= target < len(self.songs):
            return False
        self.songs.insert(target, self.songs.pop(index))
        return True

    def remove_current(self) -> dict | None:
        index = self.index
        if index < 0:
            return None
        removed = self.songs.pop(index)
        self.active_song_id = self.songs[min(index, len(self.songs) - 1)]["id"] if self.songs else ""
        return removed

    def template_payload(self) -> dict:
        """Reusable order/goals only; existing sessions retain their own work."""
        songs = [make_song(song["title"], key=song["key"], tempo=song["tempo"],
                           goals=song["goals"]) for song in self.songs]
        return RehearsalPlan(self.title, songs, songs[0]["id"] if songs else "").payload()

    def append_template(self, payload: dict) -> int:
        template = self.from_payload(payload)
        if len(self.songs) + len(template.songs) > MAX_SONGS:
            raise ValueError("The combined plan would exceed 200 songs.")
        fresh = self.from_payload(template.template_payload())
        self.songs.extend(fresh.songs)
        if not self.active_song_id and fresh.songs:
            self.active_song_id = fresh.songs[0]["id"]
        return len(fresh.songs)

    def summary(self) -> str:
        if not self.songs:
            return "No songs planned yet."
        complete = sum(song["completed"] for song in self.songs)
        lines = [f"{self.title}: {complete} of {len(self.songs)} songs marked complete."]
        for index, song in enumerate(self.songs, 1):
            name = song["title"].strip() or "Untitled song"
            status = "complete" if song["completed"] else "to revisit"
            details = [value for value in (song["key"],
                       f'{song["tempo"]} BPM' if song["tempo"] else "") if value]
            lines.append(f"{index}. {name} — {status}" + (f" ({', '.join(details)})" if details else ""))
            for label, key in (("Goal", "goals"), ("Notes", "notes"), ("Next", "next_steps")):
                if song[key].strip():
                    lines.append(f"   {label}: {song[key].strip()}")
            for moment in song["bookmarks"]:
                position = moment["position_seconds"]
                at = f"Take {int(position) // 60}:{int(position) % 60:02d}" if position is not None else "Note"
                lines.append(f"   {at}: {moment['note']}")
        return "\n".join(lines)


def summarize_plan(payload: dict) -> str:
    return RehearsalPlan.from_payload(payload).summary()
