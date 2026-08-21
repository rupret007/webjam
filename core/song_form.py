"""The song a live session is already working on, read from what it recorded.

WebJam has no music-information-retrieval engine. The facts in this module come
from exactly two places, and every one of them carries which:

* ``STATED``   — a musician typed it into the session notes.
* ``DETECTED`` — a Music AI job returned it for a file the user chose.

Nothing here listens to the jam, and nothing here guesses. A field WebJam does
not know stays empty rather than becoming a plausible-looking default, because
the writing help in :mod:`core.song_help` is only honest if it can tell a fact
it was given from a suggestion it made.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable, Sequence

STATED = "stated"
DETECTED = "detected"

_SOURCES = frozenset({STATED, DETECTED})

# Section labels people actually type on a lyric sheet, mapped to the role that
# decides how a part should behave harmonically. Unknown labels keep their text
# and fall back to the neutral "part" role rather than being dropped.
_ROLE_ALIASES: dict[str, str] = {
    "intro": "intro",
    "introduction": "intro",
    "count in": "intro",
    "verse": "verse",
    "v": "verse",
    "prechorus": "prechorus",
    "pre chorus": "prechorus",
    "pre-chorus": "prechorus",
    "rise": "prechorus",
    "lift": "prechorus",
    "build": "prechorus",
    "chorus": "chorus",
    "hook": "chorus",
    "refrain": "chorus",
    "drop": "chorus",
    "bridge": "bridge",
    "middle eight": "bridge",
    "middle 8": "bridge",
    "b section": "bridge",
    "solo": "solo",
    "lead": "solo",
    "instrumental": "solo",
    "break": "breakdown",
    "breakdown": "breakdown",
    "vamp": "breakdown",
    "interlude": "breakdown",
    "outro": "outro",
    "coda": "outro",
    "ending": "outro",
    "tag": "outro",
}

ROLE_ORDER: tuple[str, ...] = (
    "intro",
    "verse",
    "prechorus",
    "chorus",
    "bridge",
    "solo",
    "breakdown",
    "outro",
    "part",
)

_NOTE_NAMES = "A-G"
_CHORD_BODY = (
    rf"[{_NOTE_NAMES}][#b]?"
    r"(?:maj|min|dim|aug|sus|add|m|M|\+|°|ø)?"
    r"(?:2|4|5|6|7|9|11|13)?"
    r"(?:(?:maj|add|sus|b|#)(?:2|4|5|6|7|9|11|13))*"
    rf"(?:/[{_NOTE_NAMES}][#b]?)?"
)
_CHORD_RE = re.compile(rf"^{_CHORD_BODY}$")
_CHORD_TOKEN_RE = re.compile(rf"(?<![\w#/]){_CHORD_BODY}(?![\w])")

# ``[Chorus]``, ``## Chorus``, ``Chorus:`` and ``Chorus -`` all mean the same
# thing on a lyric sheet, so accept the shapes people already write.
_BRACKET_SECTION_RE = re.compile(r"^[\[(]\s*([^\]\)]{1,40})\s*[\])]\s*(.*)$")
_LABELLED_SECTION_RE = re.compile(r"^([A-Za-z][A-Za-z0-9 '\-]{0,39})\s*[:\-–]\s*(.*)$")

_KEY_RE = re.compile(
    rf"^key\s*[:\-]?\s*([{_NOTE_NAMES}][#b]?)\s*"
    r"(maj|major|min|minor|m)?\s*$",
    re.IGNORECASE,
)
_TEMPO_RE = re.compile(
    r"^(?:tempo|bpm)\s*[:\-]?\s*(\d{2,3})(?:\s*bpm)?\s*$",
    re.IGNORECASE,
)
_TRAILING_TEMPO_RE = re.compile(r"\b(\d{2,3})\s*bpm\b", re.IGNORECASE)
_METER_RE = re.compile(
    r"^(?:time|meter)\s*[:\-]?\s*(\d{1,2})\s*/\s*(1|2|4|8|16)\s*$",
    re.IGNORECASE,
)
# "[Verse x8]", "[Verse 8]", "[Chorus x16]" -- how long a part runs.
_SECTION_BARS_RE = re.compile(r"^(.*?)\s*[x×]?\s*(\d{1,3})\s*$", re.IGNORECASE)
_TIMESTAMP_RE = re.compile(r"^#{1,6}\s+\d{1,2}:\d{2}(?::\d{2})?\b")
# Lines that belong to the session pulse in ``core.session_intelligence``.
# They share the notes field with the song sheet but are not lyrics, so this
# parser leaves them to the parser that already owns them.
_NOTE_MARKER_RE = re.compile(
    r"^(decision|decided|action|todo|next|blocker|blocked|risk|question|q)\b\s*[:\-]",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://")

_MAX_SECTIONS = 16
_MAX_CHORDS_PER_SECTION = 32
_MAX_LYRIC_LINES = 40


def is_chord_symbol(token: str) -> bool:
    """Return whether ``token`` reads as a chord rather than a word."""

    candidate = str(token or "").strip().strip(",|")
    if not candidate or len(candidate) > 12:
        return False
    return bool(_CHORD_RE.match(candidate))


@dataclass(frozen=True, slots=True)
class SongFact:
    """One musical fact plus the reason WebJam is allowed to state it."""

    value: str
    source: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.source not in _SOURCES:
            raise ValueError(f"unknown song fact source: {self.source!r}")

    @property
    def is_detected(self) -> bool:
        return self.source == DETECTED

    def describe(self) -> str:
        """Return a phrase that never lets a guess read as a measurement."""

        if not self.value:
            return ""
        if self.source == DETECTED:
            origin = f"detected by {self.detail}" if self.detail else "detected"
        else:
            origin = "from your notes"
        return f"{self.value} ({origin})"


@dataclass(frozen=True, slots=True)
class SongSection:
    """One named part of the song and whatever chords were written under it."""

    name: str
    role: str
    chords: tuple[str, ...] = ()
    source: str = STATED
    detail: str = ""
    # Written as ``[Verse x8]`` when the room has decided how long a part is.
    # Zero means nobody said, and the clock uses its documented default rather
    # than pretending to know.
    bars: int = 0

    @property
    def label(self) -> str:
        return self.name or self.role.title()

    @property
    def chord_line(self) -> str:
        return " ".join(self.chords)

    @property
    def bars_stated(self) -> bool:
        return self.bars > 0


@dataclass(frozen=True, slots=True)
class SongForm:
    """Everything the room has actually said or measured about its song."""

    title: str = ""
    key: SongFact | None = None
    tempo: SongFact | None = None
    meter: SongFact | None = None
    sections: tuple[SongSection, ...] = ()
    lyric_lines: tuple[str, ...] = ()

    @property
    def has_content(self) -> bool:
        return bool(self.key or self.tempo or self.sections or self.lyric_lines)

    @property
    def beats_per_bar(self) -> int:
        """Return the stated top number of the time signature, or four."""

        if self.meter is None:
            return 4
        top = self.meter.value.split("/")[0].strip()
        return int(top) if top.isdigit() and 1 <= int(top) <= 16 else 4

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(section.role for section in self.sections)

    def section_for_role(self, role: str) -> SongSection | None:
        for section in self.sections:
            if section.role == role:
                return section
        return None

    def known_chords(self) -> tuple[str, ...]:
        """Return every distinct chord written anywhere in the song."""

        seen: list[str] = []
        for section in self.sections:
            for chord in section.chords:
                if chord not in seen:
                    seen.append(chord)
        return tuple(seen)

    def summary_line(self) -> str:
        """Return a short, provenance-honest description for the jam surface."""

        parts: list[str] = []
        if self.key is not None:
            parts.append(f"Key {self.key.describe()}")
        if self.tempo is not None:
            parts.append(f"{self.tempo.describe()} BPM")
        if self.sections:
            parts.append(
                " → ".join(section.label for section in self.sections[:5])
            )
        if not parts:
            return "No key, tempo, or sections captured yet."
        return " · ".join(parts)

    def with_detected(
        self,
        *,
        key: str = "",
        tempo: str = "",
        sections: Sequence[SongSection] = (),
        detail: str = "",
    ) -> "SongForm":
        """Return a copy that folds in facts a Music AI job actually returned.

        Stated facts win. A musician who wrote ``Key: G`` is making a decision
        about the song, not a claim that a detector will agree, so a detected
        key is recorded beside it rather than quietly overwriting it.
        """

        updated = self
        if key and updated.key is None:
            updated = replace(
                updated, key=SongFact(key, DETECTED, detail)
            )
        if tempo and updated.tempo is None:
            updated = replace(
                updated, tempo=SongFact(tempo, DETECTED, detail)
            )
        if sections and not updated.sections:
            updated = replace(updated, sections=tuple(sections)[:_MAX_SECTIONS])
        return updated


def parse_song_form(notes: str, *, title: str = "") -> SongForm:
    """Read the song out of the notes the room is already keeping.

    The session canvas has always invited musicians to write "chord
    progressions / lyrics" there, so that text is the song sheet. This reads
    it; it never writes to it and never infers a fact that was not typed.
    """

    key: SongFact | None = None
    tempo: SongFact | None = None
    meter: SongFact | None = None
    sections: list[SongSection] = []
    lyric_lines: list[str] = []
    current: str | None = None

    for raw_line in str(notes or "").splitlines():
        line = raw_line.strip()
        if not line or _TIMESTAMP_RE.match(line):
            continue

        key_match = _KEY_RE.match(line)
        if key_match is not None:
            if key is None:
                key = SongFact(_format_key(*key_match.groups()), STATED)
            continue

        tempo_match = _TEMPO_RE.match(line)
        if tempo_match is not None:
            if tempo is None:
                tempo = SongFact(tempo_match.group(1), STATED)
            continue

        meter_match = _METER_RE.match(line)
        if meter_match is not None:
            if meter is None:
                meter = SongFact(
                    f"{meter_match.group(1)}/{meter_match.group(2)}", STATED
                )
            continue
        if tempo is None:
            trailing = _TRAILING_TEMPO_RE.search(line)
            if trailing is not None:
                tempo = SongFact(trailing.group(1), STATED)

        heading, remainder = _split_section_heading(line)
        if heading is not None:
            current = _add_section(sections, heading)
            if remainder:
                _extend_section(sections, current, remainder, lyric_lines)
            continue

        _extend_section(sections, current, line, lyric_lines)

    return SongForm(
        title=" ".join(str(title or "").split()),
        key=key,
        tempo=tempo,
        meter=meter,
        sections=tuple(sections[:_MAX_SECTIONS]),
        lyric_lines=tuple(lyric_lines[:_MAX_LYRIC_LINES]),
    )


def normalize_role(label: str) -> str:
    """Return the harmonic role a written section label implies."""

    cleaned = re.sub(r"[^a-z0-9 ]+", " ", str(label or "").lower())
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return "part"
    if cleaned in _ROLE_ALIASES:
        return _ROLE_ALIASES[cleaned]
    # "Verse 2", "Chorus (last)" and "Guitar solo" should all still route to a
    # role; match on the words present rather than requiring an exact label.
    words = cleaned.split()
    for alias, role in _ROLE_ALIASES.items():
        alias_words = alias.split()
        if len(alias_words) > 1 and alias in cleaned:
            return role
        if len(alias_words) == 1 and alias in words:
            return role
    return "part"


def extract_chords(text: str) -> tuple[str, ...]:
    """Return the chord symbols on a line, ignoring ordinary words.

    A line only counts as a chord line when chords dominate it. That keeps
    lyrics such as "A bad end" from being read as an A chord.
    """

    candidate = str(text or "").replace("|", " ")
    tokens = [token for token in candidate.split() if token.strip(",.")]
    if not tokens:
        return ()
    chords = [
        token.strip(",.")
        for token in tokens
        if is_chord_symbol(token.strip(",."))
    ]
    if len(chords) * 2 < len(tokens):
        return ()
    return tuple(chords[:_MAX_CHORDS_PER_SECTION])


def _split_section_heading(line: str) -> tuple[str | None, str]:
    bracket = _BRACKET_SECTION_RE.match(line)
    if bracket is not None:
        return bracket.group(1).strip(), bracket.group(2).strip()

    if line.startswith("#"):
        heading = line.lstrip("#").strip().rstrip(":").strip()
        if heading and len(heading) <= 40:
            return heading, ""
        return None, ""

    labelled = _LABELLED_SECTION_RE.match(line)
    if labelled is not None:
        label = labelled.group(1).strip()
        if normalize_role(label) != "part":
            return label, labelled.group(2).strip()
    return None, ""


def split_section_bars(label: str) -> tuple[str, int]:
    """Split ``"Verse x8"`` into its name and its stated length in bars."""

    cleaned = " ".join(str(label or "").split())[:44]
    match = _SECTION_BARS_RE.match(cleaned)
    if match is None:
        return cleaned, 0
    name, count = match.group(1).strip(), int(match.group(2))
    # "Verse 2" is a second verse, not a two-bar one. Only an explicit "x"
    # or a plausible bar count on a bare role name is read as a length.
    explicit = "x" in cleaned.lower().rsplit(str(count), 1)[0][-2:]
    if not name:
        return cleaned, 0
    if not explicit and not (count >= 4 and count % 2 == 0):
        return cleaned, 0
    if not 1 <= count <= 512:
        return name, 0
    return name, count


def _add_section(sections: list[SongSection], label: str) -> str:
    name, bars = split_section_bars(label)
    name = name[:40]
    for section in sections:
        if section.name.lower() == name.lower():
            return section.name
    if len(sections) < _MAX_SECTIONS:
        sections.append(
            SongSection(name=name, role=normalize_role(name), bars=bars)
        )
    return name


def _extend_section(
    sections: list[SongSection],
    current: str | None,
    line: str,
    lyric_lines: list[str],
) -> None:
    chords = extract_chords(line)
    if not chords:
        if (
            len(lyric_lines) < _MAX_LYRIC_LINES
            and not line.startswith(("-", "*"))
            and not _NOTE_MARKER_RE.match(line)
            and not _URL_RE.search(line)
        ):
            lyric_lines.append(line[:120])
        return
    if current is None:
        return
    for index, section in enumerate(sections):
        if section.name != current:
            continue
        merged = list(section.chords)
        # A section header can legitimately appear twice — "[Verse]" before
        # verse one and again before verse two — and the sheet may be quoted
        # back into the notes from chat. Re-reading the same run must not turn
        # "Am F C G" into "Am F C G Am F C G".
        if merged[-len(chords):] != list(chords):
            for chord in chords:
                if len(merged) < _MAX_CHORDS_PER_SECTION:
                    merged.append(chord)
        sections[index] = replace(section, chords=tuple(merged))
        return


def _format_key(root: str, quality: str | None) -> str:
    tonic = root[0].upper() + root[1:].replace("B", "b")
    mode = (quality or "").lower()
    if mode in {"m", "min", "minor"}:
        return f"{tonic} minor"
    return f"{tonic} major"


def merge_sections(
    existing: Iterable[SongSection],
    detected: Iterable[SongSection],
) -> tuple[SongSection, ...]:
    """Return stated sections first, then detected ones not already named."""

    merged = list(existing)
    names = {section.name.lower() for section in merged}
    for section in detected:
        if section.name.lower() not in names and len(merged) < _MAX_SECTIONS:
            merged.append(section)
            names.add(section.name.lower())
    return tuple(merged)


__all__ = [
    "DETECTED",
    "ROLE_ORDER",
    "STATED",
    "SongFact",
    "SongForm",
    "SongSection",
    "extract_chords",
    "is_chord_symbol",
    "merge_sections",
    "normalize_role",
    "parse_song_form",
]
