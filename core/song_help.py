"""Writing help for the song a live session is already playing.

Everything here is computed on this computer from :class:`~core.song_form.SongForm`
— the key, tempo, sections, chords, and lines the room already has. No audio
leaves the machine, no text is sent to a language model, and nothing is
uploaded to run any of it. That is a deliberate boundary, not an omission: a
musician asking "what could the bridge do?" mid-jam should not cause WebJam to
stream the band to a third party.

The cost of that boundary is that these are *suggestions*, and they say so.
Every result carries the reasoning that produced it and the basis for the key
it assumed, so a musician can tell an idea from a measurement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.song_form import ROLE_ORDER, SongForm, SongSection

_CHROMATIC_SHARP = (
    "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
)
_CHROMATIC_FLAT = (
    "C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B",
)
_PITCH_CLASSES = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "Fb": 4,
    "F": 5, "E#": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
    "A#": 10, "Bb": 10, "B": 11, "Cb": 11,
}
_FLAT_KEYS = frozenset({"F", "Bb", "Eb", "Ab", "Db", "Gb", "D", "G", "C"})

_MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)
_MINOR_STEPS = (0, 2, 3, 5, 7, 8, 10)
_MAJOR_QUALITIES = ("", "m", "m", "", "", "m", "dim")
_MINOR_QUALITIES = ("m", "dim", "", "m", "m", "", "")
_MAJOR_NUMERALS = ("I", "ii", "iii", "IV", "V", "vi", "vii°")
_MINOR_NUMERALS = ("i", "ii°", "III", "iv", "v", "VI", "VII")

# Progressions are stored as scale degrees (1-indexed) so they render into any
# key, and are grouped by what the part has to *do*: a chorus has to land, a
# bridge has to leave home, a pre-chorus has to lean on the dominant.
_MAJOR_MOVES: dict[str, tuple[tuple[tuple[int, ...], str], ...]] = {
    "intro": (
        ((1, 5), "Two chords, so the room can find the pulse before anyone sings."),
        ((1, 4), "Stays home; lets the top line enter without a surprise."),
        ((6, 4, 1, 5), "Starts on the relative minor so the first verse feels like a lift."),
    ),
    "verse": (
        ((1, 5, 6, 4), "The plainest four; leaves room for the words to carry it."),
        ((1, 4, 1, 5), "Older, more stubborn. Good under a story lyric."),
        ((6, 4, 1, 5), "Starts minor, so the chorus can arrive brighter."),
        ((1, 3, 4, 4), "The iii adds shade without leaving the key."),
    ),
    "prechorus": (
        ((4, 5), "Two bars of push. Ends unresolved so the chorus has somewhere to go."),
        ((2, 5), "The classic set-up; the ii pulls hard into V."),
        ((4, 5, 6, 5), "Delays the landing one more bar for a bigger chorus."),
    ),
    "chorus": (
        ((1, 5, 6, 4), "Lands on the tonic first — the hook gets the strongest chord."),
        ((4, 1, 5, 6), "Starts off the tonic, so the chorus feels like a door opening."),
        ((1, 4, 5, 4), "Three chords, loud. Hard to lose in a live room."),
        ((6, 4, 1, 5), "Keeps the minor colour but resolves; works if the verse is major."),
    ),
    "bridge": (
        ((4, 5, 3, 6), "Leaves the tonic entirely, then hands you back to the chorus."),
        ((6, 3, 4, 1), "Sits on the relative minor — the most common way out of a chorus."),
        ((2, 5, 1, 6), "A turnaround; useful if the bridge is short."),
        ((4, 4, 5, 5), "Two chords held long. Space for a key change or a drop."),
    ),
    "solo": (
        ((1, 5, 6, 4), "Reuse the chorus changes so the soloist has a shape to play."),
        ((1, 4, 5, 5), "Ends on V, which makes the last time round obvious."),
    ),
    "breakdown": (
        ((6, 6, 4, 4), "Two chords, half the density. Lets one instrument speak."),
        ((1, 1, 5, 5), "Holds the tonic; good for a vocal-only bar."),
    ),
    "outro": (
        ((4, 1), "Plagal ending; stops without a question mark."),
        ((1, 5, 6, 4), "Loop the chorus changes and fade the arrangement out."),
    ),
    "part": (
        ((1, 5, 6, 4), "A neutral four that will sit against most of the song."),
        ((6, 4, 1, 5), "Same four chords, started from the minor."),
    ),
}

_MINOR_MOVES: dict[str, tuple[tuple[tuple[int, ...], str], ...]] = {
    "intro": (
        ((1, 7), "Two chords; the VII keeps it modal rather than classical."),
        ((1, 6), "Holds the minor colour before anything else happens."),
    ),
    "verse": (
        ((1, 7, 6, 7), "Modal and circular. Carries a lot of words."),
        ((1, 4, 5, 1), "Closes each time round; good for a verse that repeats."),
        ((1, 6, 3, 7), "The most common minor four."),
    ),
    "prechorus": (
        ((4, 5), "Ends unresolved so the chorus has a landing."),
        ((6, 7), "Steps up into the chorus without touching the tonic."),
    ),
    "chorus": (
        ((6, 7, 1, 1), "Arrives on the tonic minor — heavier than the verse."),
        ((3, 7, 1, 6), "Starts on the relative major, so the chorus reads brighter."),
        ((1, 6, 3, 7), "Keeps the verse colour but with a stronger rhythm."),
    ),
    "bridge": (
        ((3, 7, 6, 4), "Leans on the relative major, then falls back to minor."),
        ((4, 1, 5, 1), "Uses the minor v, which the rest of the song probably has not."),
        ((6, 3, 7, 7), "Leaves the tonic for the whole section."),
    ),
    "solo": (
        ((1, 7, 6, 7), "Reuse the verse loop; easiest thing to solo over."),
        ((1, 6, 3, 7), "Four chords, one per bar."),
    ),
    "breakdown": (
        ((1, 1, 6, 6), "Two chords held. Drop the drums here."),
    ),
    "outro": (
        ((6, 7, 1), "Steps up and stops on the tonic."),
        ((1, 7, 6, 7), "Loop and fade."),
    ),
    "part": (
        ((1, 6, 3, 7), "A neutral minor four."),
        ((1, 7, 6, 7), "Same colour, more modal."),
    ),
}

# What usually comes next, given what a song already has. These are common
# forms, not rules, and the copy says so.
_NEXT_SECTION: tuple[tuple[frozenset[str], str, str], ...] = (
    (
        frozenset(),
        "Verse",
        "Start with one verse. It sets the key and the tempo for everything else.",
    ),
    (
        frozenset({"verse"}),
        "Chorus",
        "You have a verse. A chorus gives it somewhere to go and gives the room a hook.",
    ),
    (
        frozenset({"chorus"}),
        "Verse",
        "You have a chorus. A verse before it makes the chorus feel like an arrival.",
    ),
    (
        frozenset({"verse", "chorus"}),
        "Bridge",
        "Verse and chorus are down. A bridge is the usual third idea — "
        "it leaves the tonic so the last chorus lands harder.",
    ),
    (
        frozenset({"verse", "chorus", "bridge"}),
        "Intro",
        "The song has its three ideas. An intro tells the band how to start it "
        "the same way twice.",
    ),
    (
        frozenset({"intro", "verse", "chorus", "bridge"}),
        "Outro",
        "Everything but an ending. Decide how it stops before the take, not during it.",
    ),
)

_STOPWORDS = frozenset(
    """
    a an and are as at be been but by can did do for from get got had has have
    he her him his how i if in is it its just like me my no not now of on one
    or our out so than that the their them then there they this to too up us
    was we were what when where which who will with you your
    """.split()
)

_VOWEL_RUN_RE = re.compile(r"[aeiouy]+[^aeiouy]*$")
_WORD_RE = re.compile(r"[A-Za-z']+")

_MAX_OPTIONS = 3


@dataclass(frozen=True, slots=True)
class ChordSuggestion:
    """One suggested progression, in the room's key, with its reasoning."""

    role: str
    label: str
    chords: tuple[str, ...]
    numerals: tuple[str, ...]
    reason: str
    key: str
    key_basis: str

    @property
    def chord_line(self) -> str:
        return " ".join(self.chords)

    @property
    def numeral_line(self) -> str:
        return " ".join(self.numerals)

    def describe(self) -> str:
        return f"{self.chord_line}  ({self.numeral_line}) — {self.reason}"


@dataclass(frozen=True, slots=True)
class ChordAdvice:
    """The answer to "give me changes for a different part"."""

    section_label: str
    key: str
    key_basis: str
    suggestions: tuple[ChordSuggestion, ...] = ()
    blocked_reason: str = ""

    @property
    def available(self) -> bool:
        return bool(self.suggestions)

    def headline(self) -> str:
        if not self.available:
            return self.blocked_reason or "No chord suggestion is available."
        return (
            f"Suggestions for {self.section_label} in {self.key} "
            f"(key {self.key_basis})"
        )


@dataclass(frozen=True, slots=True)
class WritingIdea:
    """One concrete next move, plus why WebJam is proposing it."""

    headline: str
    detail: str


@dataclass(frozen=True, slots=True)
class WritingAdvice:
    """Structure, next-section, and lyric help drawn from the room's own song."""

    summary: str
    next_section: str = ""
    ideas: tuple[WritingIdea, ...] = ()
    rhymes: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.ideas)


def suggest_chords(
    form: SongForm,
    *,
    role: str = "",
    limit: int = _MAX_OPTIONS,
) -> ChordAdvice:
    """Suggest changes for a part the song does not have yet.

    ``role`` names the part being written. When it is empty the next missing
    part in a common song form is chosen, which is what "a progression for a
    different part" usually means mid-jam.
    """

    target_role = role or _next_missing_role(form)
    label = _role_label(form, target_role)
    key_name, key_basis = resolve_key(form)
    if not key_name:
        return ChordAdvice(
            section_label=label,
            key="",
            key_basis="",
            blocked_reason=(
                "WebJam does not know this song's key yet. Write a line like "
                "\"Key: G major\" in the notes, or run Chords in Song tools on "
                "a file you own."
            ),
        )

    tonic, is_minor = _split_key(key_name)
    moves = (_MINOR_MOVES if is_minor else _MAJOR_MOVES).get(
        target_role,
        (_MINOR_MOVES if is_minor else _MAJOR_MOVES)["part"],
    )
    used = _existing_chord_sets(form)
    suggestions: list[ChordSuggestion] = []
    fallback: list[ChordSuggestion] = []
    for degrees, reason in moves:
        chords = tuple(
            _degree_chord(tonic, degree, is_minor) for degree in degrees
        )
        numerals = tuple(_degree_numeral(degree, is_minor) for degree in degrees)
        suggestion = ChordSuggestion(
            role=target_role,
            label=label,
            chords=chords,
            numerals=numerals,
            reason=reason,
            key=key_name,
            key_basis=key_basis,
        )
        # "A different part" has to actually sound different, so a progression
        # the song already uses somewhere else is demoted rather than offered.
        if frozenset(chords) in used:
            fallback.append(suggestion)
        else:
            suggestions.append(suggestion)

    ordered = (suggestions + fallback)[: max(1, int(limit))]
    return ChordAdvice(
        section_label=label,
        key=key_name,
        key_basis=key_basis,
        suggestions=tuple(ordered),
    )


def suggest_writing(form: SongForm) -> WritingAdvice:
    """Return structure and lyric help built only from what the room has."""

    ideas: list[WritingIdea] = []
    next_section = _next_section_advice(form)
    if next_section is not None:
        label, reason = next_section
        ideas.append(WritingIdea(f"Write a {label}", reason))
    else:
        label = ""

    if form.sections:
        ideas.append(_arrangement_idea(form))

    lyric_idea, rhymes = _lyric_idea(form)
    if lyric_idea is not None:
        ideas.append(lyric_idea)

    if not form.has_content:
        return WritingAdvice(
            summary=(
                "Nothing is written down yet. Put the key, the tempo, and one "
                "section header in the notes and this fills in."
            ),
            ideas=(
                WritingIdea(
                    "Start the sheet",
                    "Two lines is enough: \"Key: G major\" and \"[Verse]\". "
                    "Chords written under a header become the song's form.",
                ),
            ),
        )

    return WritingAdvice(
        summary=form.summary_line(),
        next_section=label,
        ideas=tuple(ideas[:_MAX_OPTIONS]),
        rhymes=rhymes,
    )


def resolve_key(form: SongForm) -> tuple[str, str]:
    """Return the key to write in and where it came from.

    A stated or detected key is used as-is. Failing that, a key is read off the
    chords already written — which is an assumption, so it is reported as one
    and never written back into the song form as a fact.
    """

    if form.key is not None and form.key.value:
        basis = (
            f"detected by {form.key.detail}"
            if form.key.is_detected and form.key.detail
            else "detected" if form.key.is_detected
            else "from your notes"
        )
        return form.key.value, basis

    inferred = infer_key_from_chords(form.known_chords())
    if inferred:
        return inferred, "assumed from the chords you have written"
    return "", ""


def infer_key_from_chords(chords: tuple[str, ...]) -> str:
    """Return the diatonic key that best explains ``chords``, or ``""``.

    This is ordinary music theory, not detection: it scores each candidate key
    by how many of the written chords belong to it. Ties resolve toward the
    first chord written, which is usually the tonic in a live sketch.
    """

    roots = [
        parsed for parsed in (_parse_chord(chord) for chord in chords) if parsed
    ]
    if len(roots) < 2:
        return ""

    best_name = ""
    best_score = 0.0
    for tonic_class in range(12):
        for is_minor in (False, True):
            triads = _diatonic_triads(tonic_class, is_minor)
            matched = sum(1 for root in roots if root in triads)
            if matched < len(roots):
                continue
            score = float(matched)
            if roots[0] == (tonic_class, is_minor):
                score += 0.5
            elif roots[0][0] == tonic_class:
                score += 0.25
            if score > best_score:
                best_score = score
                spelling = _spell(tonic_class, prefer_flat=not is_minor)
                best_name = f"{spelling} {'minor' if is_minor else 'major'}"
    return best_name


def _next_missing_role(form: SongForm) -> str:
    present = set(form.roles)
    for _required, label, _reason in _NEXT_SECTION:
        role = label.lower()
        if role not in present:
            return role
    for role in ROLE_ORDER:
        if role not in present and role != "part":
            return role
    return "part"


def _next_section_advice(form: SongForm) -> tuple[str, str] | None:
    present = frozenset(form.roles)
    best: tuple[str, str] | None = None
    for required, label, reason in _NEXT_SECTION:
        if required <= present and label.lower() not in present:
            best = (label, reason)
    return best


def _arrangement_idea(form: SongForm) -> WritingIdea:
    counts: dict[str, int] = {}
    for section in form.sections:
        counts[section.role] = counts.get(section.role, 0) + 1
    repeated = [role for role, count in counts.items() if count > 1]
    if repeated:
        return WritingIdea(
            "Vary the repeat",
            f"There is more than one {repeated[0]}. Changing one thing on the "
            "second pass — drop an instrument, add a bar, hold the last chord — "
            "is cheaper than writing a new section.",
        )

    thin = [
        section.label for section in form.sections if not section.chords
    ]
    if thin:
        return WritingIdea(
            f"Put chords under {thin[0]}",
            f"{thin[0]} has a header but no changes written under it, so nobody "
            "can play it from the sheet.",
        )

    longest = max(form.sections, key=lambda section: len(section.chords))
    return WritingIdea(
        "Check the shape",
        f"{longest.label} carries {len(longest.chords)} chords. If the song "
        "drags there, cutting it in half is usually the fix.",
    )


def _lyric_idea(form: SongForm) -> tuple[WritingIdea | None, tuple[str, ...]]:
    lines = [line for line in form.lyric_lines if len(line.split()) > 2]
    if not lines:
        return (
            WritingIdea(
                "Write one line",
                "There are no lyrics on the sheet yet. One concrete line — a "
                "place, an object, a time of day — gives the rest something to "
                "answer.",
            ),
            (),
        )

    open_word, partners = _rhyme_partners(lines)
    keywords = _keywords(lines)
    if open_word and partners:
        return (
            WritingIdea(
                "Answer the open line",
                f"\"{open_word}\" has no rhyme partner yet. These words from "
                "your own lines land on the same sound.",
            ),
            partners,
        )
    if open_word:
        return (
            WritingIdea(
                "Answer the open line",
                f"\"{open_word}\" ends a line and nothing rhymes with it yet. "
                "Either answer it or move it off the line ending.",
            ),
            (),
        )
    if keywords:
        return (
            WritingIdea(
                "Push the image further",
                f"Your lines keep returning to {', '.join(keywords[:3])}. The "
                "next line usually works better if it changes the picture "
                "rather than restating it.",
            ),
            (),
        )
    return (None, ())


def _rhyme_partners(lines: list[str]) -> tuple[str, tuple[str, ...]]:
    """Return the first unrhymed line ending and any near-rhymes for it."""

    endings: dict[str, list[str]] = {}
    order: list[str] = []
    for line in lines:
        words = _WORD_RE.findall(line)
        if not words:
            continue
        last = words[-1].lower()
        key = _rhyme_key(last)
        if not key:
            continue
        endings.setdefault(key, [])
        if last not in endings[key]:
            endings[key].append(last)
        order.append(last)

    for word in order:
        key = _rhyme_key(word)
        if key and len(endings.get(key, [])) == 1:
            partners = [
                other
                for other_key, words in endings.items()
                for other in words
                if other_key != key
                and other != word
                and other_key[-2:] == key[-2:]
            ]
            return word, tuple(partners[:_MAX_OPTIONS])
    return "", ()


def _rhyme_key(word: str) -> str:
    match = _VOWEL_RUN_RE.search(word.lower())
    return match.group(0) if match else ""


def _keywords(lines: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    for line in lines:
        for word in _WORD_RE.findall(line.lower()):
            if len(word) > 3 and word not in _STOPWORDS:
                counts[word] = counts.get(word, 0) + 1
    repeated = sorted(
        (word for word, count in counts.items() if count > 1),
        key=lambda word: (-counts[word], word),
    )
    return repeated


def _existing_chord_sets(form: SongForm) -> set[frozenset[str]]:
    return {
        frozenset(section.chords)
        for section in form.sections
        if len(section.chords) > 1
    }


def _role_label(form: SongForm, role: str) -> str:
    existing = form.section_for_role(role)
    if existing is not None and existing.name:
        return existing.name
    return {
        "prechorus": "Pre-Chorus",
        "breakdown": "Breakdown",
        "part": "the next part",
    }.get(role, role.title())


def _split_key(key_name: str) -> tuple[int, bool]:
    parts = key_name.split()
    tonic = _PITCH_CLASSES.get(parts[0], 0) if parts else 0
    is_minor = len(parts) > 1 and parts[1].lower().startswith("min")
    return tonic, is_minor


def _degree_chord(tonic: int, degree: int, is_minor: bool) -> str:
    steps = _MINOR_STEPS if is_minor else _MAJOR_STEPS
    qualities = _MINOR_QUALITIES if is_minor else _MAJOR_QUALITIES
    index = (int(degree) - 1) % 7
    root = (tonic + steps[index]) % 12
    return f"{_spell(root, prefer_flat=_prefers_flats(tonic, is_minor))}{qualities[index]}"


def _degree_numeral(degree: int, is_minor: bool) -> str:
    numerals = _MINOR_NUMERALS if is_minor else _MAJOR_NUMERALS
    return numerals[(int(degree) - 1) % 7]


def _diatonic_triads(tonic: int, is_minor: bool) -> set[tuple[int, bool]]:
    steps = _MINOR_STEPS if is_minor else _MAJOR_STEPS
    qualities = _MINOR_QUALITIES if is_minor else _MAJOR_QUALITIES
    triads: set[tuple[int, bool]] = set()
    for index, step in enumerate(steps):
        quality = qualities[index]
        if quality == "dim":
            continue
        triads.add(((tonic + step) % 12, quality == "m"))
    return triads


def _prefers_flats(tonic: int, is_minor: bool) -> bool:
    return _spell(tonic, prefer_flat=True) in _FLAT_KEYS and not is_minor


def _spell(pitch_class: int, *, prefer_flat: bool) -> str:
    table = _CHROMATIC_FLAT if prefer_flat else _CHROMATIC_SHARP
    return table[pitch_class % 12]


def _parse_chord(chord: str) -> tuple[int, bool] | None:
    text = str(chord or "").strip()
    if not text:
        return None
    root = text[:2] if len(text) > 1 and text[1] in "#b" else text[:1]
    pitch = _PITCH_CLASSES.get(root)
    if pitch is None:
        return None
    remainder = text[len(root):]
    if remainder.startswith(("dim", "°", "ø", "aug", "+")):
        return None
    is_minor = remainder.startswith("m") and not remainder.startswith("maj")
    return pitch, is_minor


def detected_sections(labels: tuple[str, ...], detail: str) -> tuple[SongSection, ...]:
    """Return sections a Music AI job reported, tagged as detected."""

    from core.song_form import DETECTED, normalize_role

    return tuple(
        SongSection(
            name=str(label).strip()[:40],
            role=normalize_role(label),
            source=DETECTED,
            detail=detail,
        )
        for label in labels
        if str(label).strip()
    )


__all__ = [
    "ChordAdvice",
    "ChordSuggestion",
    "WritingAdvice",
    "WritingIdea",
    "detected_sections",
    "infer_key_from_chords",
    "resolve_key",
    "suggest_chords",
    "suggest_writing",
]
