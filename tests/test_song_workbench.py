"""Session song state, the host-confirmed upload gate, and late-join catch-up."""

from __future__ import annotations

import pytest

from core.music_ai_catalog import resolve_song_tools
from core.music_ai_client import MusicAIWorkflow
from core.music_ai_results import SongArtifact, SongToolRun
from core.song_workbench import (
    LIVE_MIX_SOURCE,
    SOURCE_PICKED_FILE,
    SOURCE_SHARED_TRACK,
    SharedTrackView,
    SongWorkbench,
    evaluate_upload,
)

ACCOUNT = [
    MusicAIWorkflow("1", "Stem Separation", "stems", "isolate vocals"),
    MusicAIWorkflow("2", "Chord and beat detection", "chords", "detect chords"),
]
CATALOG = resolve_song_tools(ACCOUNT)
STEMS = CATALOG.capability("stems")
SECTIONS = CATALOG.capability("sections")

SHEET = """Key: A minor
Tempo: 104
[Verse]
Am F C G
Driving through the same town twice
[Chorus]
F C G Am
"""


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "mix.wav"
    path.write_bytes(b"RIFF" + b"0" * 4096)
    return str(path)


# ----------------------------------------------------------------------
# Upload gate
# ----------------------------------------------------------------------
def test_the_host_may_send_a_file_they_chose_after_confirming_it(audio_file):
    decision = evaluate_upload(
        capability=STEMS,
        source_kind=SOURCE_PICKED_FILE,
        path=audio_file,
        is_host=True,
        has_api_key=True,
    )

    assert decision.allowed
    assert "mix.wav" in decision.confirmation_body
    assert "leaves this computer" in decision.confirmation_body
    assert "rights to" in decision.confirmation_body
    assert "live jam is never uploaded" in decision.confirmation_body


def test_no_api_key_means_nothing_is_attempted(audio_file):
    decision = evaluate_upload(
        capability=STEMS,
        source_kind=SOURCE_PICKED_FILE,
        path=audio_file,
        is_host=True,
        has_api_key=False,
    )
    assert decision.blocked
    assert "music.ai/dash" in decision.reason


def test_a_guest_is_told_to_ask_the_host_rather_than_being_allowed(audio_file):
    decision = evaluate_upload(
        capability=STEMS,
        source_kind=SOURCE_PICKED_FILE,
        path=audio_file,
        is_host=False,
        has_api_key=True,
    )
    assert decision.blocked
    assert "Only the host" in decision.reason
    assert "Split stems" in decision.reason


def test_the_live_jam_is_never_an_upload_candidate():
    """Named and always refused, so a future caller fails against this test."""

    for source, path in (
        (LIVE_MIX_SOURCE, LIVE_MIX_SOURCE),
        (SOURCE_PICKED_FILE, LIVE_MIX_SOURCE),
    ):
        decision = evaluate_upload(
            capability=STEMS,
            source_kind=source,
            path=path,
            is_host=True,
            has_api_key=True,
        )
        assert decision.blocked
        assert "never uploads the live jam" in decision.reason


def test_an_unsupported_verb_cannot_be_run_even_by_the_host(audio_file):
    decision = evaluate_upload(
        capability=SECTIONS,
        source_kind=SOURCE_PICKED_FILE,
        path=audio_file,
        is_host=True,
        has_api_key=True,
    )
    assert decision.blocked
    assert "section" in decision.reason.lower()


def test_a_missing_verb_is_refused_rather_than_defaulted(audio_file):
    decision = evaluate_upload(
        capability=None,
        source_kind=SOURCE_PICKED_FILE,
        path=audio_file,
        is_host=True,
        has_api_key=True,
    )
    assert decision.blocked


@pytest.mark.parametrize("source", ["", "discovered", "auto"])
def test_only_an_explicit_source_kind_is_accepted(source, audio_file):
    decision = evaluate_upload(
        capability=STEMS,
        source_kind=source,
        path=audio_file,
        is_host=True,
        has_api_key=True,
    )
    assert decision.blocked
    assert "Pick a file" in decision.reason


def test_an_unreadable_or_empty_file_is_refused(tmp_path):
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")

    for path, fragment in (
        (str(tmp_path / "missing.wav"), "cannot read"),
        (str(tmp_path), "cannot read"),
        (str(empty), "empty"),
        ("", "Pick a file"),
    ):
        decision = evaluate_upload(
            capability=STEMS,
            source_kind=SOURCE_PICKED_FILE,
            path=path,
            is_host=True,
            has_api_key=True,
        )
        assert decision.blocked
        assert fragment in decision.reason


def test_the_session_shared_track_is_a_legitimate_explicit_source(audio_file):
    decision = evaluate_upload(
        capability=STEMS,
        source_kind=SOURCE_SHARED_TRACK,
        path=audio_file,
        is_host=True,
        has_api_key=True,
    )
    assert decision.allowed
    assert "Shared Track" in decision.confirmation_body


# ----------------------------------------------------------------------
# Song state
# ----------------------------------------------------------------------
def test_the_workbench_reads_the_song_from_the_session_notes():
    workbench = SongWorkbench(title="Tuesday", notes=SHEET)

    assert "A minor" in workbench.conductor_line()
    assert "104" in workbench.conductor_line()
    assert workbench.chord_advice().available
    assert workbench.writing_advice().available


def test_an_empty_session_has_no_conductor_line_rather_than_a_placeholder():
    assert SongWorkbench().conductor_line() == ""


def test_a_finished_run_attaches_to_the_session_and_fills_gaps():
    workbench = SongWorkbench(notes="[Verse]\nAm F\n")
    workbench.attach_run(
        SongToolRun(
            verb_key="chords",
            label="Chords & key",
            workflow_slug="chords",
            job_id="j1",
            source_name="mix.wav",
            detected_key="A minor",
            detected_tempo="104",
            chord_symbols=("Am", "F", "C", "G"),
        )
    )

    form = workbench.form
    assert form.key.is_detected
    assert "detected by Chords & key" in form.summary_line()
    assert workbench.detected_chords() == ("Am", "F", "C", "G")


def test_detected_sections_appear_beside_written_ones():
    workbench = SongWorkbench(notes="[Verse]\nAm F\n")
    workbench.attach_run(
        SongToolRun(
            verb_key="sections",
            label="Sections",
            workflow_slug="sections",
            job_id="j2",
            source_name="mix.wav",
            detected_sections=("Chorus", "Bridge"),
        )
    )
    labels = [section.label for section in workbench.form.sections]
    assert labels[0] == "Verse"
    assert "Chorus" in labels and "Bridge" in labels


def test_stems_are_listed_for_the_session_once_downloaded():
    workbench = SongWorkbench()
    workbench.attach_run(
        SongToolRun(
            verb_key="stems",
            label="Split stems",
            workflow_slug="stems",
            job_id="j3",
            source_name="mix.wav",
            artifacts=(
                SongArtifact("vocals", "audio", local_path="/tmp/vocals.wav"),
                SongArtifact("drums", "audio", local_path="/tmp/drums.wav"),
                SongArtifact("notes", "text", local_path="/tmp/notes.json"),
            ),
        )
    )
    assert workbench.stems() == ("/tmp/vocals.wav", "/tmp/drums.wav")


def test_re_running_a_job_replaces_rather_than_duplicates_it():
    workbench = SongWorkbench()
    for tempo in ("100", "104"):
        workbench.attach_run(
            SongToolRun(
                verb_key="chords",
                label="Chords & key",
                workflow_slug="chords",
                job_id="same",
                source_name="mix.wav",
                detected_tempo=tempo,
            )
        )
    assert len(workbench.runs) == 1
    assert workbench.form.tempo.value == "104"


def test_attaching_a_non_run_is_a_programming_error():
    with pytest.raises(TypeError):
        SongWorkbench().attach_run({"job_id": "x"})


# ----------------------------------------------------------------------
# Late join
# ----------------------------------------------------------------------
def test_a_late_arrival_is_told_how_far_in_they_joined():
    workbench = SongWorkbench(notes=SHEET)
    catch_up = workbench.catch_up(
        shared_track=SharedTrackView(
            loaded=True, playing=True, source_name="demo.wav", position_s=95
        ),
        elapsed_seconds=1320,
    )

    assert catch_up.joined_late
    assert catch_up.headline == "You joined 22 minutes in"
    assert "demo.wav — playing 1:35" in catch_up.lines
    assert catch_up.sheet_available


def test_arriving_at_the_start_is_not_reported_as_a_late_join():
    catch_up = SongWorkbench(notes=SHEET).catch_up(elapsed_seconds=10)
    assert not catch_up.joined_late
    assert catch_up.headline == "Where the session is"


def test_a_guest_with_no_local_sheet_is_told_why_it_is_empty():
    """Notes never leave the computer that typed them; say so."""

    catch_up = SongWorkbench().catch_up(
        shared_track=SharedTrackView(loaded=True, source_name="demo.wav"),
        elapsed_seconds=900,
        is_host=False,
    )
    joined = " ".join(catch_up.lines)
    assert "Session notes stay local" in joined
    assert "ask the host to share theirs to chat" in joined
    assert not catch_up.sheet_available


def test_recent_tool_results_appear_in_the_catch_up():
    workbench = SongWorkbench(notes=SHEET)
    workbench.attach_run(
        SongToolRun(
            verb_key="stems",
            label="Split stems",
            workflow_slug="stems",
            job_id="j4",
            source_name="mix.wav",
            artifacts=(SongArtifact("vocals", "audio", local_path="/tmp/v.wav"),),
        )
    )
    assert any("Split stems" in line for line in workbench.catch_up().lines)


def test_an_unloaded_shared_track_makes_no_claim():
    assert SharedTrackView().status_line() == "No Shared Track loaded."
    assert "paused" in SharedTrackView(
        loaded=True, source_name="a.wav", position_s=61
    ).status_line()


# ----------------------------------------------------------------------
# Sharing the sheet
# ----------------------------------------------------------------------
def test_the_shareable_sheet_is_compact_and_carries_no_paths():
    sheet = SongWorkbench(title="Tuesday", notes=SHEET).shareable_sheet()

    assert "Tuesday · Key A minor · 104 BPM" in sheet
    assert "Verse: Am F C G" in sheet
    assert "/" not in sheet
    assert len(sheet) < 900


def test_there_is_nothing_to_share_from_an_empty_session():
    assert SongWorkbench().shareable_sheet() == ""


def test_a_shared_sheet_pasted_back_into_notes_is_idempotent():
    """A bandmate copying the sheet into their own notes must not double it."""

    original = SongWorkbench(title="Tuesday", notes=SHEET)
    receiver = SongWorkbench(notes=original.shareable_sheet())

    assert receiver.form.section_for_role("verse").chords == ("Am", "F", "C", "G")
    doubled = SongWorkbench(
        notes=original.shareable_sheet() + "\n" + original.shareable_sheet()
    )
    assert doubled.form.section_for_role("verse").chords == ("Am", "F", "C", "G")
