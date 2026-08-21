"""The song WebJam reads from the notes, and the help it offers about it.

Two properties matter more than any individual suggestion: a fact and a
suggestion must stay distinguishable, and nothing here may reach the network.
"""

from __future__ import annotations

import pytest

from core.song_form import (
    DETECTED,
    STATED,
    SongFact,
    SongForm,
    SongSection,
    extract_chords,
    is_chord_symbol,
    merge_sections,
    normalize_role,
    parse_song_form,
)
from core.song_help import (
    infer_key_from_chords,
    resolve_key,
    suggest_chords,
    suggest_writing,
)

SHEET = """
## 14:02
Key: G major
Tempo: 96 bpm
decision: keep the half-time feel

[Intro]
G D

[Verse]
G D Em C
Walking out the back door again
Nothing in my hands but rain

Chorus: C G D
Hold on to the wheel and steer

[Guitar Solo]
Em C G D
"""


# ----------------------------------------------------------------------
# Parsing
# ----------------------------------------------------------------------
def test_the_notes_are_the_song_sheet():
    form = parse_song_form(SHEET, title="Back Door")

    assert form.title == "Back Door"
    assert form.key == SongFact("G major", STATED)
    assert form.tempo == SongFact("96", STATED)
    assert [section.label for section in form.sections] == [
        "Intro",
        "Verse",
        "Chorus",
        "Guitar Solo",
    ]
    assert form.section_for_role("verse").chords == ("G", "D", "Em", "C")
    assert form.known_chords() == ("G", "D", "Em", "C")


@pytest.mark.parametrize(
    ("label", "role"),
    [
        ("Verse", "verse"),
        ("Verse 2", "verse"),
        ("Pre-Chorus", "prechorus"),
        ("Middle 8", "bridge"),
        ("Guitar Solo", "solo"),
        ("Hook", "chorus"),
        ("Coda", "outro"),
        ("Something else", "part"),
    ],
)
def test_written_section_labels_route_to_a_harmonic_role(label, role):
    assert normalize_role(label) == role


@pytest.mark.parametrize(
    ("token", "is_chord"),
    [
        ("G", True),
        ("Am", True),
        ("F#m7", True),
        ("Cmaj7", True),
        ("D/F#", True),
        ("Bsus4", True),
        ("rain", False),
        ("A bad end", False),
        ("", False),
    ],
)
def test_chord_symbols_are_told_apart_from_words(token, is_chord):
    assert is_chord_symbol(token) is is_chord


def test_a_lyric_line_starting_with_a_note_name_is_not_a_chord_line():
    """"A bad end" must not become an A chord."""

    assert extract_chords("A bad end is still an end") == ()
    assert extract_chords("Am F C G") == ("Am", "F", "C", "G")


def test_session_note_markers_stay_with_the_session_pulse():
    form = parse_song_form(SHEET)
    joined = " ".join(form.lyric_lines)
    assert "keep the half-time feel" not in joined
    assert "Walking out the back door again" in joined


def test_re_reading_a_repeated_section_does_not_duplicate_its_chords():
    """A sheet quoted back from chat must not double every progression."""

    once = parse_song_form("[Verse]\nAm F C G\n")
    twice = parse_song_form("[Verse]\nAm F C G\n[Verse]\nAm F C G\n")
    assert once.section_for_role("verse").chords == ("Am", "F", "C", "G")
    assert twice.section_for_role("verse").chords == ("Am", "F", "C", "G")


def test_a_second_different_progression_under_one_header_is_kept():
    form = parse_song_form("[Verse]\nAm F\nC G\n")
    assert form.section_for_role("verse").chords == ("Am", "F", "C", "G")


def test_empty_notes_produce_an_empty_form_rather_than_a_default_song():
    form = parse_song_form("")
    assert not form.has_content
    assert form.key is None and form.tempo is None
    assert "No key, tempo, or sections captured yet." in form.summary_line()


def test_urls_do_not_become_lyrics():
    form = parse_song_form("[Verse]\nsee https://example.com/demo\n")
    assert form.lyric_lines == ()


# ----------------------------------------------------------------------
# Provenance
# ----------------------------------------------------------------------
def test_a_stated_fact_reads_differently_from_a_detected_one():
    stated = SongFact("G major", STATED)
    detected = SongFact("G major", DETECTED, "Chords & key")

    assert stated.describe() == "G major (from your notes)"
    assert detected.describe() == "G major (detected by Chords & key)"
    assert detected.is_detected and not stated.is_detected


def test_an_unknown_provenance_is_rejected():
    with pytest.raises(ValueError):
        SongFact("G major", "guessed")


def test_a_musician_decision_is_not_overwritten_by_a_detector():
    stated = parse_song_form("Key: G major\nTempo: 96\n")
    folded = stated.with_detected(key="B minor", tempo="140", detail="Chords & key")

    assert folded.key == SongFact("G major", STATED)
    assert folded.tempo == SongFact("96", STATED)


def test_detection_fills_a_gap_the_room_left():
    folded = parse_song_form("[Verse]\nAm F\n").with_detected(
        key="A minor", tempo="104", detail="Chords & key"
    )
    assert folded.key == SongFact("A minor", DETECTED, "Chords & key")
    assert "detected by Chords & key" in folded.summary_line()


def test_detected_sections_never_displace_written_ones():
    written = (SongSection(name="Verse", role="verse"),)
    detected = (SongSection(name="Chorus", role="chorus", source=DETECTED),)
    merged = merge_sections(written, detected)
    assert [item.name for item in merged] == ["Verse", "Chorus"]
    assert merge_sections(written, written) == written


# ----------------------------------------------------------------------
# Chord suggestions
# ----------------------------------------------------------------------
def test_chords_are_suggested_for_a_part_the_song_does_not_have():
    advice = suggest_chords(parse_song_form(SHEET))

    assert advice.available
    assert advice.section_label == "Bridge"
    assert advice.key == "G major"
    assert "from your notes" in advice.headline()
    for suggestion in advice.suggestions:
        assert len(suggestion.chords) == len(suggestion.numerals)
        assert suggestion.reason


def test_a_named_role_is_honoured():
    advice = suggest_chords(parse_song_form("Key: C major\n[Verse]\nC F\n"), role="chorus")
    assert advice.section_label == "Chorus"


def test_suggestions_render_into_the_stated_key():
    advice = suggest_chords(
        parse_song_form("Key: D major\n[Verse]\nD A\n"), role="chorus"
    )
    roots = {chord.rstrip("m") for item in advice.suggestions for chord in item.chords}
    assert roots <= {"D", "E", "F#", "G", "A", "B", "C#"}


def test_a_minor_key_gets_minor_shapes():
    advice = suggest_chords(
        parse_song_form("Key: A minor\n[Verse]\nAm F\n"), role="bridge"
    )
    assert advice.key == "A minor"
    assert any("i" in item.numeral_line for item in advice.suggestions)


def test_a_progression_the_song_already_uses_is_not_offered_first():
    """"A different part" has to actually sound different."""

    form = parse_song_form("Key: C major\n[Verse]\nC G Am F\n")
    advice = suggest_chords(form, role="chorus")

    assert advice.suggestions[0].chords != ("C", "G", "Am", "F")


def test_without_any_key_the_answer_is_an_honest_refusal():
    advice = suggest_chords(parse_song_form("[Verse]\nla la la\n"))

    assert not advice.available
    assert not advice.suggestions
    assert "does not know this song's key" in advice.headline()
    assert "Key: G major" in advice.headline()


def test_a_key_read_off_the_chords_is_labelled_as_an_assumption():
    advice = suggest_chords(parse_song_form("[Verse]\nAm F C G\n"), role="bridge")

    assert advice.available
    assert advice.key_basis == "assumed from the chords you have written"
    assert "assumed" in advice.headline()


@pytest.mark.parametrize(
    ("chords", "expected"),
    [
        (("Am", "F", "C", "G"), "A minor"),
        (("C", "F", "G", "Am"), "C major"),
        (("G",), ""),
        ((), ""),
        (("C#", "Fb", "Bbb"), ""),
    ],
)
def test_key_inference_is_theory_not_detection(chords, expected):
    assert infer_key_from_chords(chords) == expected


def test_resolve_key_reports_where_the_key_came_from():
    assert resolve_key(parse_song_form("Key: E minor\n")) == (
        "E minor",
        "from your notes",
    )
    detected = SongForm(key=SongFact("F major", DETECTED, "Chords & key"))
    assert resolve_key(detected)[1] == "detected by Chords & key"
    assert resolve_key(SongForm()) == ("", "")


# ----------------------------------------------------------------------
# Writing help
# ----------------------------------------------------------------------
def test_writing_help_names_the_next_section_for_a_verse_and_chorus():
    advice = suggest_writing(parse_song_form(SHEET))

    assert advice.available
    assert advice.next_section == "Bridge"
    assert any("Bridge" in idea.headline for idea in advice.ideas)


def test_an_empty_sheet_gets_a_way_to_start_rather_than_nothing():
    advice = suggest_writing(parse_song_form(""))
    assert advice.available
    assert "Start the sheet" in advice.ideas[0].headline


def test_a_section_with_no_chords_is_pointed_out():
    advice = suggest_writing(parse_song_form("Key: C major\n[Verse]\n[Chorus]\nC G\n"))
    assert any("Put chords under" in idea.headline for idea in advice.ideas)


def test_an_unrhymed_line_ending_is_reported_without_promising_words():
    """The copy must not offer rhymes it did not find."""

    advice = suggest_writing(parse_song_form("[Verse]\nC G\nnothing rhymes with orange\n"))
    rhyme_ideas = [
        idea for idea in advice.ideas if "Answer the open line" in idea.headline
    ]
    if rhyme_ideas and not advice.rhymes:
        assert "same sound" not in rhyme_ideas[0].detail


def test_rhyme_partners_come_from_the_rooms_own_lines():
    advice = suggest_writing(
        parse_song_form(
            "[Verse]\nC G\nout in the pouring rain\n"
            "walking down the lane\nI could not explain\nnothing at all here\n"
        )
    )
    for word in advice.rhymes:
        assert word in (
            "rain lane explain here".split()
        )


def test_writing_help_never_reaches_the_network(monkeypatch):
    """Songwriting help is local by design, not by accident."""

    import socket

    def forbidden(*_args, **_kwargs):  # pragma: no cover - must never run
        raise AssertionError("song help opened a socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    form = parse_song_form(SHEET)
    assert suggest_writing(form).available
    assert suggest_chords(form).available
