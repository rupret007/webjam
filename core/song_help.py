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

# Where a new part conventionally sits when the song does not have one yet.
_ROLE_WEIGHT: dict[str, int] = {
    "intro": 0,
    "verse": 1,
    "prechorus": 2,
    "chorus": 3,
    "solo": 4,
    "bridge": 5,
    "breakdown": 6,
    "part": 7,
    "outro": 8,
}

# Where each diatonic chord usually goes next, and why. Ordinary functional
# harmony, kept as data so the explanation ships with the suggestion.
_MAJOR_NEXT: dict[int, tuple[tuple[int, str], ...]] = {
    1: (
        (4, "IV opens it up without leaving home."),
        (5, "V sets up a return to the tonic."),
        (6, "vi darkens it while staying in the key."),
    ),
    2: (
        (5, "ii into V is the strongest pull in the key."),
        (1, "Back to I if the phrase is ending."),
    ),
    3: (
        (6, "iii to vi keeps the line falling."),
        (4, "iii to IV lifts it again."),
    ),
    4: (
        (1, "IV back to I is a plagal landing — an ending without a question."),
        (5, "IV to V builds toward a resolution."),
    ),
    5: (
        (1, "V to I is the resolution the ear is waiting for."),
        (6, "V to vi is the deceptive move — it delays the landing."),
    ),
    6: (
        (4, "vi to IV is the most common turn in pop."),
        (2, "vi to ii keeps the harmony moving downward."),
    ),
    7: ((1, "vii° resolves up to I."),),
}

_MINOR_NEXT: dict[int, tuple[tuple[int, str], ...]] = {
    1: (
        (6, "VI opens it out from the tonic minor."),
        (7, "VII is the modal step that keeps it from sounding classical."),
        (4, "iv darkens it further."),
    ),
    2: ((5, "ii° into v, the minor-key set-up."),),
    3: (
        (7, "III to VII keeps the brighter colour going."),
        (6, "III to VI steps back toward the minor."),
    ),
    4: (
        (1, "iv back to i closes the phrase."),
        (5, "iv to v builds toward the tonic."),
    ),
    5: ((1, "v to i resolves, more gently than a major V."),),
    6: (
        (7, "VI to VII steps up into the tonic."),
        (3, "VI to III brightens it."),
    ),
    7: ((1, "VII back to i is the modal resolution."),),
}

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
class SectionNeighbours:
    """What sits on either side of the part being written.

    A suggestion for a bridge is only useful if it knows what the chorus
    before it did. This is the difference between filling in a region of an
    existing song and generating an unrelated one.
    """

    previous_label: str = ""
    previous_last_chord: str = ""
    following_label: str = ""
    following_first_chord: str = ""

    @property
    def has_context(self) -> bool:
        return bool(self.previous_label or self.following_label)

    def describe(self) -> str:
        parts = []
        if self.previous_label:
            parts.append(f"after {self.previous_label}")
        if self.following_label:
            parts.append(f"before {self.following_label}")
        return " ".join(parts)


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
    context: str = ""

    @property
    def chord_line(self) -> str:
        return " ".join(self.chords)

    @property
    def numeral_line(self) -> str:
        return " ".join(self.numerals)

    def describe(self) -> str:
        reason = f"{self.reason} {self.context}".strip()
        return f"{self.chord_line}  ({self.numeral_line}) — {reason}"


@dataclass(frozen=True, slots=True)
class ChordAdvice:
    """The answer to "give me changes for this part of the song"."""

    section_label: str
    key: str
    key_basis: str
    suggestions: tuple[ChordSuggestion, ...] = ()
    blocked_reason: str = ""
    neighbours: SectionNeighbours = SectionNeighbours()
    existing_chords: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return bool(self.suggestions)

    @property
    def rewrites_existing(self) -> bool:
        """Whether this part already has changes the suggestions would replace."""

        return bool(self.existing_chords)

    def headline(self) -> str:
        if not self.available:
            return self.blocked_reason or "No chord suggestion is available."
        where = self.neighbours.describe()
        placement = f", {where}" if where else ""
        return (
            f"Suggestions for {self.section_label} in {self.key} "
            f"(key {self.key_basis}){placement}"
        )


@dataclass(frozen=True, slots=True)
class NextChord:
    """One chord that could follow what a section already has."""

    chord: str
    numeral: str
    reason: str

    def describe(self) -> str:
        return f"{self.chord} ({self.numeral}) — {self.reason}"


@dataclass(frozen=True, slots=True)
class NextChordAdvice:
    """The answer to "we're on this — what comes next?"."""

    section_label: str
    key: str
    key_basis: str
    from_chords: tuple[str, ...] = ()
    candidates: tuple[NextChord, ...] = ()
    blocked_reason: str = ""

    @property
    def available(self) -> bool:
        return bool(self.candidates)

    def headline(self) -> str:
        if not self.available:
            return self.blocked_reason or "No next chord is available."
        return (
            f"After {' '.join(self.from_chords)} in {self.section_label}, "
            f"try:"
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
    section_name: str = "",
    limit: int = _MAX_OPTIONS,
) -> ChordAdvice:
    """Suggest changes for one part of the song the room already has.

    ``section_name`` selects a written part by name; ``role`` selects one by
    kind. With neither, the next missing part in a common song form is chosen,
    which is what "a progression for a different part" usually means mid-jam.

    Suggestions are scored against the parts on either side, so a bridge is
    offered because of what the chorus before it did — not in isolation. This
    fills a region of an existing song rather than producing an unrelated one.
    """

    target = _resolve_target(form, role=role, section_name=section_name)
    label = target.label
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
        target.role,
        (_MINOR_MOVES if is_minor else _MAJOR_MOVES)["part"],
    )
    used = _existing_chord_sets(form, excluding=target.name)
    scored: list[tuple[int, ChordSuggestion]] = []
    for degrees, reason in moves:
        chords = tuple(
            _degree_chord(tonic, degree, is_minor) for degree in degrees
        )
        numerals = tuple(_degree_numeral(degree, is_minor) for degree in degrees)
        score, context = _seam_fit(chords, degrees, target.neighbours, is_minor)
        # "A different part" has to actually sound different, so a progression
        # the song already uses elsewhere is demoted rather than offered.
        if frozenset(chords) in used:
            score -= 10
        scored.append(
            (
                score,
                ChordSuggestion(
                    role=target.role,
                    label=label,
                    chords=chords,
                    numerals=numerals,
                    reason=reason,
                    key=key_name,
                    key_basis=key_basis,
                    context=context,
                ),
            )
        )

    scored.sort(key=lambda item: -item[0])
    ordered = [suggestion for _score, suggestion in scored][: max(1, int(limit))]
    return ChordAdvice(
        section_label=label,
        key=key_name,
        key_basis=key_basis,
        suggestions=tuple(ordered),
        neighbours=target.neighbours,
        existing_chords=target.existing_chords,
    )


def suggest_next_chords(
    form: SongForm,
    *,
    section_name: str = "",
    role: str = "",
    limit: int = _MAX_OPTIONS,
) -> NextChordAdvice:
    """Answer "we're on this — what comes next?" for a part that has chords.

    This is ordinary functional harmony, explained. It reads the chord the
    section currently ends on and offers the moves that chord usually makes in
    this key, so a musician can see why rather than just what.
    """

    target = _resolve_target(form, role=role, section_name=section_name)
    key_name, key_basis = resolve_key(form)
    if not key_name:
        return NextChordAdvice(
            section_label=target.label,
            key="",
            key_basis="",
            blocked_reason=(
                "WebJam does not know this song's key yet. Write a line like "
                "\"Key: G major\" in the notes first."
            ),
        )
    if not target.existing_chords:
        return NextChordAdvice(
            section_label=target.label,
            key=key_name,
            key_basis=key_basis,
            blocked_reason=(
                f"{target.label} has no chords written under it yet. Add one "
                "and this will suggest where it goes."
            ),
        )

    tonic, is_minor = _split_key(key_name)
    last = target.existing_chords[-1]
    degree = _degree_of(last, tonic, is_minor)
    if degree is None:
        return NextChordAdvice(
            section_label=target.label,
            key=key_name,
            key_basis=key_basis,
            from_chords=target.existing_chords,
            blocked_reason=(
                f"{last} is outside {key_name}, so WebJam will not guess what "
                "follows it."
            ),
        )

    table = _MINOR_NEXT if is_minor else _MAJOR_NEXT
    candidates = [
        NextChord(
            chord=_degree_chord(tonic, next_degree, is_minor),
            numeral=_degree_numeral(next_degree, is_minor),
            reason=reason,
        )
        for next_degree, reason in table.get(degree, ())
    ]
    return NextChordAdvice(
        section_label=target.label,
        key=key_name,
        key_basis=key_basis,
        from_chords=target.existing_chords,
        candidates=tuple(candidates[: max(1, int(limit))]),
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


def resolve_section_label(
    form: SongForm,
    *,
    role: str = "",
    section_name: str = "",
) -> str:
    """Return the part a request is actually about.

    Selecting nothing means "the next part the song is missing", which is what
    a musician means when they ask for help without pointing at anything. Every
    surface that has to *name* that part — the panel, the companion projection,
    a model prompt — has to agree on it, so they all ask here.
    """

    return _resolve_target(form, role=role, section_name=section_name).label


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


@dataclass(frozen=True, slots=True)
class _Target:
    """The part being written, and what surrounds it."""

    role: str
    name: str
    label: str
    neighbours: SectionNeighbours
    existing_chords: tuple[str, ...] = ()


def _resolve_target(
    form: SongForm,
    *,
    role: str = "",
    section_name: str = "",
) -> _Target:
    """Locate the part being written and read the parts on either side."""

    sections = list(form.sections)
    index: int | None = None

    wanted = " ".join(str(section_name or "").split()).lower()
    if wanted:
        for position, section in enumerate(sections):
            if section.name.lower() == wanted:
                index = position
                break
    if index is None and role:
        for position, section in enumerate(sections):
            if section.role == role:
                index = position
                break

    if index is not None:
        section = sections[index]
        return _Target(
            role=section.role,
            name=section.name,
            label=section.label,
            neighbours=_neighbours(sections, index, replacing=True),
            existing_chords=section.chords,
        )

    target_role = role or _next_missing_role(form)
    insert_at = _insertion_point(sections, target_role)
    return _Target(
        role=target_role,
        name="",
        label=_role_label(form, target_role),
        neighbours=_neighbours(sections, insert_at, replacing=False),
    )


def _neighbours(
    sections: list[SongSection],
    index: int,
    *,
    replacing: bool,
) -> SectionNeighbours:
    previous = sections[index - 1] if index > 0 else None
    following_index = index + 1 if replacing else index
    following = (
        sections[following_index] if 0 <= following_index < len(sections) else None
    )
    return SectionNeighbours(
        previous_label=previous.label if previous is not None else "",
        previous_last_chord=(
            previous.chords[-1] if previous is not None and previous.chords else ""
        ),
        following_label=following.label if following is not None else "",
        following_first_chord=(
            following.chords[0] if following is not None and following.chords else ""
        ),
    )


def _insertion_point(sections: list[SongSection], role: str) -> int:
    """Return where a new part of ``role`` conventionally sits in the form."""

    weight = _ROLE_WEIGHT.get(role, len(_ROLE_WEIGHT))
    for position, section in enumerate(sections):
        if _ROLE_WEIGHT.get(section.role, len(_ROLE_WEIGHT)) > weight:
            return position
    return len(sections)


def _seam_fit(
    chords: tuple[str, ...],
    degrees: tuple[int, ...],
    neighbours: SectionNeighbours,
    is_minor: bool,
) -> tuple[int, str]:
    """Score how well a progression joins the parts around it, and say why."""

    if not chords or not neighbours.has_context:
        return 0, ""

    score = 0
    notes: list[str] = []

    previous = neighbours.previous_last_chord
    if previous:
        if chords[0] != previous:
            score += 2
            notes.append(
                f"{neighbours.previous_label} ends on {previous}, so starting "
                f"on {chords[0]} moves off it."
            )
        else:
            score -= 2
            notes.append(
                f"Starts on {chords[0]}, the same chord {neighbours.previous_label} "
                "ends on."
            )

    following = neighbours.following_first_chord
    if following:
        dominant_degree = 5
        if degrees[-1] == dominant_degree:
            score += 3
            notes.append(
                f"Ending on {chords[-1]} leads back into {neighbours.following_label}."
            )
        elif chords[-1] == following:
            score -= 1
            notes.append(
                f"Ends on {chords[-1]}, which {neighbours.following_label} also "
                "starts on."
            )
    del is_minor
    return score, " ".join(notes)


def _degree_of(chord: str, tonic: int, is_minor: bool) -> int | None:
    """Return the 1-indexed scale degree of ``chord``, or ``None`` if outside."""

    parsed = _parse_chord(chord)
    if parsed is None:
        return None
    pitch, minor_quality = parsed
    steps = _MINOR_STEPS if is_minor else _MAJOR_STEPS
    qualities = _MINOR_QUALITIES if is_minor else _MAJOR_QUALITIES
    for index, step in enumerate(steps):
        if (tonic + step) % 12 != pitch:
            continue
        if qualities[index] == "dim":
            continue
        if (qualities[index] == "m") == minor_quality:
            return index + 1
    return None


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


def _existing_chord_sets(
    form: SongForm,
    *,
    excluding: str = "",
) -> set[frozenset[str]]:
    """Return progressions already in use, ignoring the part being rewritten."""

    skip = str(excluding or "").lower()
    return {
        frozenset(section.chords)
        for section in form.sections
        if len(section.chords) > 1 and section.name.lower() != skip
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
    "NextChord",
    "NextChordAdvice",
    "SectionNeighbours",
    "WritingAdvice",
    "WritingIdea",
    "detected_sections",
    "infer_key_from_chords",
    "resolve_key",
    "resolve_section_label",
    "suggest_chords",
    "suggest_next_chords",
    "suggest_writing",
]
