"""One section vocabulary, shared by the live jam and the Studio timeline.

WebJam ends up describing the same song twice, because the two moments are
genuinely different:

* **In the jam** there is no ``StudioDocument`` at all — ``RecordingStudio``
  runs with ``_studio_state = None`` while the band plays — so the only form
  that exists is the one musicians typed into the session notes.
* **In Studio** a section is a ``StudioMarker`` with ``MarkerKind.SECTION``: a
  frame range on a take, which is what ``core.studio_sections.reorder_section``
  permutes.

Two *representations* is unavoidable. Two *vocabularies* is not, and that is
what this module prevents. The role names here are the single list both sides
use, and :func:`section_markers_from_form` carries the form the room wrote in
the jam onto a take as real section markers, so the arrangement a band played
is the arrangement they edit afterwards rather than something re-typed.

Nothing here imports Qt, and ``core.studio_project`` is imported lazily inside
the one function that needs it, so a live session never pays for the Studio
document model to describe its own form.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

# The canonical parts of a song, in the order they conventionally appear.
# Studio's "＋ Section" default and the live form parser both resolve to these.
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

CANONICAL_LABELS: dict[str, str] = {
    "intro": "Intro",
    "verse": "Verse",
    "prechorus": "Pre-Chorus",
    "chorus": "Chorus",
    "bridge": "Bridge",
    "solo": "Solo",
    "breakdown": "Breakdown",
    "outro": "Outro",
    "part": "Section",
}

# Section labels people actually type on a lyric sheet or into Studio's name
# dialog, mapped to the role that decides how a part behaves harmonically.
# Unknown labels keep their text and take the neutral "part" role.
ROLE_ALIASES: dict[str, str] = {
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

# Studio's ``_add_marker`` opens an 8-second section from the playhead; the
# live form's default part is 8 bars. Both mean "one normal-length part".
DEFAULT_SECTION_BARS = 8


@dataclass(frozen=True, slots=True)
class SectionSpan:
    """One part of the song placed on a timeline, in frames."""

    label: str
    role: str
    start_frame: int
    end_frame: int
    bars: int
    bars_stated: bool

    @property
    def frames(self) -> int:
        return max(0, self.end_frame - self.start_frame)


def normalize_role(label: str) -> str:
    """Return the harmonic role a written section label implies."""

    cleaned = re.sub(r"[^a-z0-9 ]+", " ", str(label or "").lower())
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return "part"
    if cleaned in ROLE_ALIASES:
        return ROLE_ALIASES[cleaned]
    # "Verse 2", "Chorus (last)" and "Guitar solo" should all still route to a
    # role; match on the words present rather than requiring an exact label.
    words = cleaned.split()
    for alias, role in ROLE_ALIASES.items():
        alias_words = alias.split()
        if len(alias_words) > 1 and alias in cleaned:
            return role
        if len(alias_words) == 1 and alias in words:
            return role
    return "part"


def canonical_label(role: str) -> str:
    """Return the display name WebJam uses for a role, everywhere."""

    return CANONICAL_LABELS.get(str(role or "").strip().lower(), "Section")


def next_section_label(existing: Iterable[str]) -> str:
    """Return the next part a song of this shape usually gets.

    Studio's ``＋ Section`` currently defaults to "Section N" or a flat
    "Verse". Reading the labels already on the timeline gives a better answer
    and keeps Studio naming inside the same vocabulary the jam uses.
    """

    present = {normalize_role(label) for label in existing}
    for role in ("verse", "chorus", "bridge", "intro", "outro"):
        if role not in present:
            return canonical_label(role)
    numbered = sum(1 for role in present if role != "part") + 1
    return f"Verse {numbered}"


def section_spans(
    sections: Sequence[object],
    *,
    tempo_bpm: float,
    beats_per_bar: int = 4,
    sample_rate: int = 48000,
    start_frame: int = 0,
) -> tuple[SectionSpan, ...]:
    """Lay a written form onto a timeline at the room's stated tempo.

    ``sections`` is any sequence exposing ``label``/``role``/``bars``, which
    both :class:`core.song_form.SongSection` and the clock's ``FormSection``
    already do.
    """

    tempo = float(tempo_bpm or 0.0)
    beats = max(1, int(beats_per_bar or 4))
    rate = max(1, int(sample_rate or 48000))
    if tempo <= 0:
        return ()

    frames_per_bar = int(round((60.0 / tempo) * beats * rate))
    if frames_per_bar <= 0:
        return ()

    spans: list[SectionSpan] = []
    cursor = max(0, int(start_frame))
    for section in sections:
        stated = int(getattr(section, "bars", 0) or 0)
        bars = stated if stated > 0 else DEFAULT_SECTION_BARS
        length = frames_per_bar * bars
        label = str(getattr(section, "label", "") or "Section")
        spans.append(
            SectionSpan(
                label=label,
                role=str(getattr(section, "role", "") or normalize_role(label)),
                start_frame=cursor,
                end_frame=cursor + length,
                bars=bars,
                bars_stated=stated > 0,
            )
        )
        cursor += length
    return tuple(spans)


def section_markers_from_form(
    sections: Sequence[object],
    *,
    tempo_bpm: float,
    beats_per_bar: int = 4,
    sample_rate: int = 48000,
    total_frames: int | None = None,
    marker_id_factory=None,
):
    """Return ``StudioMarker`` sections for the form the room played.

    This is the bridge between the two representations: the parts written in
    the jam become the arrangement markers ``core.studio_sections`` reorders,
    so nobody re-types the form after the take.

    Spans past ``total_frames`` are dropped and the last surviving span is
    clipped, because a section marker that runs past the end of the take is not
    something Studio will accept.
    """

    # Imported here so a live session never loads the Studio document model
    # just to describe its own form.
    import uuid

    from core.studio_project import MarkerKind, StudioMarker

    factory = marker_id_factory or (lambda: str(uuid.uuid4()))
    limit = None if total_frames is None else max(0, int(total_frames))

    markers = []
    for span in section_spans(
        sections,
        tempo_bpm=tempo_bpm,
        beats_per_bar=beats_per_bar,
        sample_rate=sample_rate,
    ):
        start = span.start_frame
        end = span.end_frame
        if limit is not None:
            if start >= limit:
                break
            end = min(end, limit)
        if end <= start:
            continue
        markers.append(
            StudioMarker(
                marker_id=factory(),
                start_frame=start,
                end_frame=end,
                label=span.label,
                kind=MarkerKind.SECTION,
            )
        )
    return tuple(markers)


def form_labels_from_markers(markers: Iterable[object]) -> tuple[str, ...]:
    """Return the section labels on a Studio timeline, in play order.

    The inverse direction: a take that already has an arrangement can tell the
    live surface what this song's parts are called.
    """

    sections = [
        marker
        for marker in markers
        if not bool(getattr(marker, "deleted", False))
        and str(getattr(getattr(marker, "kind", ""), "value", "")) == "section"
    ]
    sections.sort(key=lambda marker: int(getattr(marker, "start_frame", 0)))
    return tuple(
        str(getattr(marker, "label", "") or "").strip()
        for marker in sections
        if str(getattr(marker, "label", "") or "").strip()
    )


__all__ = [
    "CANONICAL_LABELS",
    "DEFAULT_SECTION_BARS",
    "ROLE_ALIASES",
    "ROLE_ORDER",
    "SectionSpan",
    "canonical_label",
    "form_labels_from_markers",
    "next_section_label",
    "normalize_role",
    "section_markers_from_form",
    "section_spans",
]
