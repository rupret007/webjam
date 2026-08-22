"""One section vocabulary across the live jam and the Studio timeline.

Master has two genuinely different representations of a song's parts: the
notes-derived form that exists while the band plays, and ``MarkerKind.SECTION``
frame ranges on a Studio take. This pins that they share one set of names and
that the live form converts into real Studio markers rather than being retyped.
"""

from __future__ import annotations

import pytest

from core.song_form import parse_song_form
from core.song_sections import (
    CANONICAL_LABELS,
    DEFAULT_SECTION_BARS,
    ROLE_ORDER,
    canonical_label,
    form_labels_from_markers,
    next_section_label,
    normalize_role,
    section_markers_from_form,
    section_spans,
)
from core.studio_project import MarkerKind, StudioMarker

SONG = """Key: G major
Tempo: 120
[Intro x4]
G D
[Verse x8]
G D Em C
[Chorus x8]
C G D G
"""


def _ids():
    counter = {"value": 0}

    def factory() -> str:
        counter["value"] += 1
        return f"00000000-0000-4000-8000-{counter['value']:012d}"

    return factory


# ----------------------------------------------------------------------
# One vocabulary
# ----------------------------------------------------------------------
def test_the_live_form_parser_uses_the_shared_vocabulary():
    """song_form must not keep its own copy of the section names."""

    import core.song_form as song_form
    import core.song_sections as song_sections

    assert song_form.normalize_role is song_sections.normalize_role
    assert song_form.ROLE_ORDER is song_sections.ROLE_ORDER


@pytest.mark.parametrize(
    ("label", "role"),
    [
        ("Intro", "intro"),
        ("Verse", "verse"),
        ("Verse 2", "verse"),
        ("Pre-Chorus", "prechorus"),
        ("Chorus", "chorus"),
        ("Hook", "chorus"),
        ("Bridge", "bridge"),
        ("Middle 8", "bridge"),
        ("Guitar Solo", "solo"),
        ("Outro", "outro"),
        ("Something else", "part"),
    ],
)
def test_studio_labels_and_lyric_sheet_labels_resolve_the_same(label, role):
    """A label typed into Studio's name dialog means what it means in the jam."""

    assert normalize_role(label) == role


def test_every_role_has_one_display_name():
    for role in ROLE_ORDER:
        assert canonical_label(role) == CANONICAL_LABELS[role]
    assert canonical_label("nonsense") == "Section"


def test_the_next_section_follows_a_common_form():
    """Better than Studio's flat "Section N" default, and in the same words."""

    assert next_section_label([]) == "Verse"
    assert next_section_label(["Verse"]) == "Chorus"
    assert next_section_label(["Verse", "Chorus"]) == "Bridge"
    assert next_section_label(["Intro", "Verse", "Chorus", "Bridge"]) == "Outro"


def test_the_next_section_reads_studio_labels_too():
    markers = section_markers_from_form(
        parse_song_form("Tempo: 120\n[Verse x8]\nG D\n").sections,
        tempo_bpm=120,
        marker_id_factory=_ids(),
    )
    assert next_section_label(form_labels_from_markers(markers)) == "Chorus"


# ----------------------------------------------------------------------
# Laying the form onto a timeline
# ----------------------------------------------------------------------
def test_the_written_form_lays_out_at_the_stated_tempo():
    form = parse_song_form(SONG)
    spans = section_spans(
        form.sections, tempo_bpm=120, beats_per_bar=4, sample_rate=48000
    )

    # 120 BPM, 4/4 -> two seconds a bar -> 96000 frames a bar.
    assert [span.label for span in spans] == ["Intro", "Verse", "Chorus"]
    assert spans[0].start_frame == 0
    assert spans[0].end_frame == 4 * 96000
    assert spans[1].start_frame == spans[0].end_frame
    assert all(span.bars_stated for span in spans)


def test_a_part_with_no_stated_length_uses_the_documented_default():
    spans = section_spans(
        parse_song_form("Tempo: 120\n[Verse]\nG D\n").sections, tempo_bpm=120
    )
    assert spans[0].bars == DEFAULT_SECTION_BARS
    assert spans[0].bars_stated is False


def test_a_meter_other_than_four_four_changes_the_bar_length():
    form = parse_song_form("Tempo: 120\nTime: 3/4\n[Verse x4]\nG D\n")
    spans = section_spans(
        form.sections,
        tempo_bpm=120,
        beats_per_bar=form.beats_per_bar,
        sample_rate=48000,
    )
    assert spans[0].end_frame == 4 * 3 * 24000


def test_without_a_tempo_nothing_is_placed_rather_than_guessed():
    assert section_spans(parse_song_form("[Verse]\nG D\n").sections, tempo_bpm=0) == ()


# ----------------------------------------------------------------------
# The bridge into Studio
# ----------------------------------------------------------------------
def test_the_form_the_room_played_becomes_real_studio_sections():
    markers = section_markers_from_form(
        parse_song_form(SONG).sections,
        tempo_bpm=120,
        beats_per_bar=4,
        sample_rate=48000,
        marker_id_factory=_ids(),
    )

    assert len(markers) == 3
    assert all(isinstance(marker, StudioMarker) for marker in markers)
    assert all(marker.kind is MarkerKind.SECTION for marker in markers)
    assert [marker.label for marker in markers] == ["Intro", "Verse", "Chorus"]
    # Studio requires a real span on a section marker; a point marker is a
    # different kind and would be rejected.
    assert all(marker.end_frame > marker.start_frame for marker in markers)


def test_sections_are_contiguous_so_reordering_has_no_gaps():
    """core.studio_sections.reorder_section permutes time; gaps would lie."""

    markers = section_markers_from_form(
        parse_song_form(SONG).sections, tempo_bpm=120, marker_id_factory=_ids()
    )
    for earlier, later in zip(markers, markers[1:]):
        assert earlier.end_frame == later.start_frame


def test_a_form_longer_than_the_take_is_clipped_not_invented():
    markers = section_markers_from_form(
        parse_song_form(SONG).sections,
        tempo_bpm=120,
        sample_rate=48000,
        total_frames=48000 * 20,
        marker_id_factory=_ids(),
    )

    assert [marker.label for marker in markers] == ["Intro", "Verse"]
    assert markers[-1].end_frame == 48000 * 20


def test_a_take_shorter_than_the_first_part_yields_one_clipped_section():
    markers = section_markers_from_form(
        parse_song_form(SONG).sections,
        tempo_bpm=120,
        sample_rate=48000,
        total_frames=48000,
        marker_id_factory=_ids(),
    )
    assert len(markers) == 1
    assert markers[0].end_frame == 48000


def test_no_sections_are_produced_without_a_tempo():
    assert (
        section_markers_from_form(
            parse_song_form("[Verse]\nG D\n").sections, tempo_bpm=0
        )
        == ()
    )


def test_studio_sections_can_tell_the_live_surface_what_the_parts_are_called():
    """The inverse direction: a take with an arrangement names the form."""

    markers = section_markers_from_form(
        parse_song_form(SONG).sections, tempo_bpm=120, marker_id_factory=_ids()
    )
    assert form_labels_from_markers(markers) == ("Intro", "Verse", "Chorus")


def test_point_markers_and_deleted_sections_are_not_read_as_form():
    markers = (
        StudioMarker(
            marker_id="00000000-0000-4000-8000-000000000001",
            start_frame=0,
            label="Take start",
        ),
        StudioMarker(
            marker_id="00000000-0000-4000-8000-000000000002",
            start_frame=0,
            end_frame=1000,
            label="Deleted verse",
            kind=MarkerKind.SECTION,
            deleted=True,
        ),
        StudioMarker(
            marker_id="00000000-0000-4000-8000-000000000003",
            start_frame=1000,
            end_frame=2000,
            label="Chorus",
            kind=MarkerKind.SECTION,
        ),
    )
    assert form_labels_from_markers(markers) == ("Chorus",)


def test_sections_are_returned_in_play_order_not_creation_order():
    markers = (
        StudioMarker(
            marker_id="00000000-0000-4000-8000-000000000010",
            start_frame=5000,
            end_frame=6000,
            label="Chorus",
            kind=MarkerKind.SECTION,
        ),
        StudioMarker(
            marker_id="00000000-0000-4000-8000-000000000011",
            start_frame=0,
            end_frame=5000,
            label="Verse",
            kind=MarkerKind.SECTION,
        ),
    )
    assert form_labels_from_markers(markers) == ("Verse", "Chorus")


def test_the_bridge_does_not_load_the_studio_model_until_it_is_used():
    """A live session must not pay for the Studio document model."""

    import ast
    from pathlib import Path

    tree = ast.parse(
        (Path(__file__).resolve().parent.parent / "core" / "song_sections.py")
        .read_text()
    )
    module_level = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "core.studio_project" not in module_level
