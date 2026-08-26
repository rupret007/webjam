"""Session song state, the host-confirmed upload gate, and late-join catch-up."""

from __future__ import annotations

import pytest

from core.music_ai_catalog import resolve_song_tools
from core.music_ai_client import MusicAIWorkflow
from core.music_ai_results import SongArtifact, SongToolRun
from core.song_workbench import (
    JobBudget,
    LIVE_MIX_SOURCE,
    SOURCE_PICKED_FILE,
    SOURCE_SHARED_TRACK,
    SharedTrackView,
    SongWorkbench,
    evaluate_upload,
    evaluate_upload_preconditions,
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
    # Leads with what happens, then the file, then the boundaries.
    assert decision.confirmation_body.startswith(
        "This uploads the file you picked to Music AI."
    )
    assert "mix.wav" in decision.confirmation_body
    assert "rights to" in decision.confirmation_body
    assert "live jam is never uploaded" in decision.confirmation_body
    assert "neither is a meeting or its recording" in decision.confirmation_body


def test_the_shared_track_confirmation_names_the_track_the_host_chose():
    """Jeff's wording: consent is about this file, not Song tools in general."""

    import tempfile
    from pathlib import Path as _Path

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        handle.write(b"RIFF" + b"0" * 4096)
        path = handle.name
    try:
        decision = evaluate_upload(
            capability=STEMS,
            source_kind=SOURCE_SHARED_TRACK,
            path=path,
            is_host=True,
            has_api_key=True,
        )
        assert decision.confirmation_body.startswith(
            "This uploads the Shared Track file you already chose to Music AI."
        )
        assert decision.confirmation_title == "Run Split stems?"
    finally:
        _Path(path).unlink()


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
    workbench = SongWorkbench(notes=SHEET)
    catch_up = workbench.catch_up(elapsed_seconds=10)
    assert not catch_up.joined_late
    assert workbench.clock_snapshot().states_place is False
    assert catch_up.headline == ""
    assert "Where the session is" not in catch_up.headline
    assert all("Verse" not in line for line in catch_up.lines)


def test_a_parked_sheet_is_not_dressed_as_where_the_session_is():
    """Writing [Verse] [Chorus] is the sheet. It is not a place."""

    workbench = SongWorkbench(notes=SHEET)
    catch_up = workbench.catch_up(elapsed_seconds=10)

    assert workbench.clock_snapshot().parked is True
    assert workbench.clock_snapshot().states_place is False
    assert not catch_up.has_content
    assert catch_up.headline != "Where the session is"
    assert catch_up.sheet_available


def test_a_count_in_catch_up_does_not_claim_a_where():
    """The click before the song must not headline Where the session is."""

    workbench = SongWorkbench(notes=SHEET)
    workbench.clock.follow_shared_track(
        loaded=True, position_s=0.0, playing=False, count_in=True
    )
    catch_up = workbench.catch_up(
        shared_track=SharedTrackView(
            loaded=True,
            playing=True,
            source_name="demo.wav",
            count_in=True,
        )
    )

    assert workbench.clock_snapshot().states_place is False
    assert catch_up.headline == ""
    assert "Where the session is" not in catch_up.headline
    assert "demo.wav — counting in" in catch_up.lines
    assert all("Verse" not in line for line in catch_up.lines)


def test_a_stated_place_still_names_where_the_session_is():
    workbench = SongWorkbench(notes=SHEET)
    workbench.clock.start()
    catch_up = workbench.catch_up(elapsed_seconds=10)

    assert workbench.clock_snapshot().states_place is True
    assert catch_up.headline == "Where the session is"
    assert any("Verse" in line for line in catch_up.lines)


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


# ----------------------------------------------------------------------
# Chord overlay on the form (the Chordify pattern, without the scrape)
# ----------------------------------------------------------------------
def test_the_form_overlay_shows_the_shape_with_its_changes():
    rows = SongWorkbench(title="Tuesday", notes=SHEET).form_overlay()

    assert [row.label for row in rows] == ["Verse", "Chorus"]
    assert rows[0].chords == "Am F C G"
    # The lyric written under the part rides with it, so the form reads like a
    # chart rather than chords and words in separate places.
    assert rows[0].lyric == "Driving through the same town twice"
    assert rows[0].describe() == (
        "Verse: Am F C G\n    Driving through the same town twice"
    )
    assert not any(row.detected for row in rows)


def test_a_part_with_no_lyric_reads_as_just_its_chords():
    rows = SongWorkbench(notes="[Verse]\nAm F C G\n").form_overlay()
    assert rows[0].lyric == ""
    assert rows[0].describe() == "Verse: Am F C G"


def test_a_section_without_chords_still_appears_in_the_form():
    rows = SongWorkbench(notes="[Verse]\nAm F\n[Bridge]\n").form_overlay()
    assert rows[-1].describe() == "Bridge:"


def test_detected_chords_get_their_own_row_rather_than_being_spread_around():
    """Music AI returns chords for a whole file, not per section."""

    workbench = SongWorkbench(notes=SHEET)
    workbench.attach_run(
        SongToolRun(
            verb_key="chords",
            label="Chords & key",
            workflow_slug="chords",
            job_id="j9",
            source_name="mix.wav",
            chord_symbols=("Am", "F", "C", "G", "Dm"),
        )
    )
    rows = workbench.form_overlay()
    detection = rows[-1]

    assert detection.is_detection_row
    assert detection.detected
    assert detection.label == "Heard on the file"
    assert "·detected" in detection.describe()
    assert [row.label for row in rows[:-1]] == ["Verse", "Chorus"]
    assert not any(row.detected for row in rows[:-1])


def test_the_overlay_is_bounded_for_a_long_song():
    notes = "\n".join(f"[Part {index}]\nC G" for index in range(20))
    assert len(SongWorkbench(notes=notes).form_overlay(max_rows=6)) == 6


def test_the_parts_a_musician_can_pick_are_the_songs_own():
    assert SongWorkbench(notes=SHEET).section_names() == ("Verse", "Chorus")
    assert SongWorkbench().section_names() == ()


def test_the_workbench_answers_next_chord_for_a_named_part():
    advice = SongWorkbench(notes=SHEET).next_chord_advice(section_name="Verse")
    assert advice.available
    assert advice.from_chords == ("Am", "F", "C", "G")


def test_the_workbench_scopes_chord_help_to_a_named_part():
    advice = SongWorkbench(notes=SHEET).chord_advice(section_name="Chorus")
    assert advice.section_label == "Chorus"
    assert advice.neighbours.previous_label == "Verse"


# ----------------------------------------------------------------------
# The shared clock, owned by the session
# ----------------------------------------------------------------------
CLOCKED = """Key: G major
Tempo: 120
[Intro x4]
G D
[Verse x8]
G D Em C
"""


def _clocked_workbench():
    now = {"value": 0.0}
    workbench = SongWorkbench(
        title="Tuesday", notes=CLOCKED, monotonic=lambda: now["value"]
    )
    return workbench, now


def test_the_session_owns_a_clock_over_its_own_form():
    workbench, _now = _clocked_workbench()
    snapshot = workbench.clock_snapshot()

    assert snapshot.section_label == "Intro"
    assert snapshot.bars_total == 12
    assert snapshot.tempo_bpm == 120


def test_editing_the_notes_moves_the_clock_onto_the_new_form():
    workbench, _now = _clocked_workbench()
    workbench.set_notes(CLOCKED + "[Chorus x8]\nC G D G\n")

    assert workbench.clock_snapshot().bars_total == 20


def test_re_reading_identical_notes_does_not_disturb_a_running_clock():
    workbench, now = _clocked_workbench()
    workbench.clock.start()
    now["value"] = 6.0
    before = workbench.clock_snapshot().bar

    workbench.set_notes(CLOCKED)

    assert workbench.clock_snapshot().bar == before
    assert workbench.clock_snapshot().running


def test_the_conductor_line_leads_with_position_once_the_clock_runs():
    workbench, now = _clocked_workbench()
    assert "from your notes" in workbench.conductor_line()
    assert not workbench.conductor_line().startswith("Verse ·")
    assert not workbench.conductor_line().startswith("Intro ·")

    workbench.clock.start()
    now["value"] = 10.0
    assert workbench.conductor_line().startswith("Verse · bar")


def test_another_profile_can_subscribe_to_the_sessions_clock():
    """The cross-profile seam: bars without importing anything musical."""

    workbench, now = _clocked_workbench()
    seen: list[dict] = []
    workbench.clock_publisher.subscribe(
        lambda snapshot: seen.append(snapshot.to_public_dict())
    )

    workbench.clock.start()
    now["value"] = 10.0
    workbench.clock_publisher.publish(force=True)

    assert seen[-1]["section"] == "Verse"
    assert seen[-1]["bar"] == 6
    assert seen[-1]["following_audio"] is False


# ----------------------------------------------------------------------
# Stems arriving from a finished separation
# ----------------------------------------------------------------------
def test_a_finished_separation_becomes_faders_immediately():
    workbench = SongWorkbench(notes=SHEET)
    workbench.attach_run(
        SongToolRun(
            verb_key="stems",
            label="Split stems",
            workflow_slug="stems",
            job_id="j10",
            source_name="demo_mix.wav",
            artifacts=(
                SongArtifact("vocals", "audio", local_path="/tmp/v.wav"),
                SongArtifact("drums", "audio", local_path="/tmp/d.wav"),
                SongArtifact("chords", "text", local_path="/tmp/c.json"),
            ),
        )
    )

    bench = workbench.stem_bench
    assert [stem.label for stem in bench.stems] == ["Vocals", "Drums"]
    assert bench.source_name == "demo_mix.wav"
    assert bench.sing_this_one()


def test_a_run_with_no_downloaded_audio_leaves_the_bench_empty():
    workbench = SongWorkbench()
    workbench.attach_run(
        SongToolRun(
            verb_key="stems",
            label="Split stems",
            workflow_slug="stems",
            job_id="j11",
            source_name="demo.wav",
            artifacts=(SongArtifact("vocals", "audio", url="https://cdn.music.ai/v"),),
        )
    )
    assert not workbench.stem_bench.loaded


def test_clearing_runs_clears_the_bench_too():
    workbench = SongWorkbench()
    workbench.attach_run(
        SongToolRun(
            verb_key="stems",
            label="Split stems",
            workflow_slug="stems",
            job_id="j12",
            source_name="demo.wav",
            artifacts=(SongArtifact("vocals", "audio", local_path="/tmp/v.wav"),),
        )
    )
    workbench.clear_runs()

    assert not workbench.stem_bench.loaded
    assert workbench.runs == ()


# ----------------------------------------------------------------------
# Quota: one at a time, and not twenty in an evening
# ----------------------------------------------------------------------
def test_a_budget_allows_a_normal_evening_then_pauses():
    budget = JobBudget(limit=3, window_s=3600.0)

    for index in range(3):
        assert budget.allows(index * 60.0)
        budget.record(index * 60.0)

    assert not budget.allows(180.0)
    assert budget.remaining(180.0) == 0


def test_the_budget_says_why_and_when_to_try_again():
    budget = JobBudget(limit=2, window_s=3600.0)
    budget.record(0.0)
    budget.record(0.0)

    reason = budget.reason(600.0)
    assert "2 Music AI jobs this hour" in reason
    assert "spends your account's credits" in reason
    assert "50 minutes" in reason
    assert 0 < budget.retry_after_s(600.0) <= 3600.0


def test_the_window_slides_rather_than_locking_the_session_out():
    budget = JobBudget(limit=2, window_s=3600.0)
    budget.record(0.0)
    budget.record(10.0)

    assert not budget.allows(100.0)
    assert budget.allows(3601.0)
    assert budget.retry_after_s(3601.0) == 0.0


def test_the_budget_refuses_uploads_before_a_file_is_ever_chosen():
    budget = JobBudget(limit=1, window_s=3600.0)
    budget.record(0.0)

    decision = evaluate_upload_preconditions(
        capability=STEMS, is_host=True, has_api_key=True, budget=budget, now=60.0
    )

    assert decision.blocked
    assert "Music AI jobs this hour" in decision.reason


def test_without_a_budget_nothing_changes():
    assert evaluate_upload_preconditions(
        capability=STEMS, is_host=True, has_api_key=True
    ).allowed


# ----------------------------------------------------------------------
# Host leaves: local facts stay, new uploads wait
# ----------------------------------------------------------------------
def test_a_session_with_no_host_waits_rather_than_failing(audio_file):
    decision = evaluate_upload(
        capability=STEMS,
        source_kind=SOURCE_PICKED_FILE,
        path=audio_file,
        is_host=False,
        has_api_key=True,
    )

    assert decision.blocked
    assert "waits for a host" in decision.reason
    assert "Chords and lyrics already here stay" in decision.reason


def test_the_song_the_room_wrote_survives_losing_the_host():
    """Overlays are local facts; they do not belong to whoever is hosting."""

    workbench = SongWorkbench(title="Tuesday", notes=SHEET)
    before = workbench.form_overlay()

    catch_up = workbench.catch_up(is_host=False)

    assert workbench.form_overlay() == before
    assert workbench.conductor_line()
    assert catch_up.sheet_available


# ----------------------------------------------------------------------
# The click stays the clock
# ----------------------------------------------------------------------
def test_a_count_in_is_not_the_song():
    counting = SharedTrackView(
        loaded=True, playing=True, source_name="demo.wav", count_in=True
    )
    playing = SharedTrackView(loaded=True, playing=True, source_name="demo.wav")

    assert not counting.carries_the_form
    assert playing.carries_the_form
    assert "counting in" in counting.status_line()


def test_a_loaded_but_stopped_track_carries_nothing():
    assert not SharedTrackView(loaded=True, source_name="a.wav").carries_the_form
    assert not SharedTrackView().carries_the_form


# ----------------------------------------------------------------------
# A very long song cannot flood any surface
# ----------------------------------------------------------------------
def _huge_song(parts: int = 40) -> str:
    body = "".join(
        f"[Part {index} x8]\n"
        + ("G D Em C " * 12)
        + "\n"
        + ("a very long lyric line " * 20)
        + "\n"
        for index in range(parts)
    )
    return "Key: G major\nTempo: 120\n" + body


def test_a_very_long_song_is_bounded_everywhere_it_is_shown():
    workbench = SongWorkbench(title="Epic", notes=_huge_song())
    form = workbench.form

    assert len(form.sections) <= 16
    assert all(len(section.chords) <= 32 for section in form.sections)
    assert all(len(line) <= 120 for section in form.sections for line in section.lyrics)
    assert len(workbench.form_overlay()) <= 8
    assert len(workbench.shareable_sheet()) <= 900
    assert len(workbench.section_names()) <= 16


def test_a_very_long_song_does_not_flood_a_companion():
    import json

    from core.music_companion import build_snapshot

    workbench = SongWorkbench(notes=_huge_song())
    published = build_snapshot(
        is_music_session=True,
        clock=workbench.clock_snapshot(),
        form_rows=workbench.form_overlay(),
        lyric_line="x" * 5000,
    ).to_public_dict()

    assert len(json.dumps(published)) < 8192
    assert len(published["chord_overlay"]) <= 8
    assert len(published["lyric_line"]) <= 120


def test_a_long_song_still_produces_a_usable_clock():
    workbench = SongWorkbench(notes=_huge_song())
    snapshot = workbench.clock_snapshot()

    assert snapshot.bars_total > 0
    assert snapshot.section_label
    assert workbench.clock.locate_section(snapshot.sections[-1].name)


def test_pathological_notes_do_not_break_the_form():
    """Nothing here should raise, however odd the text is."""

    for notes in (
        "",
        "\n\n\n",
        "[" * 500,
        "Key: " + "G" * 500,
        "Tempo: 999999",
        "[Verse x99999]\n" + "G " * 500,
        "\x00\x01 [Verse]\nG D\n",
        "Key: G major\n" + "\n".join(str(index) for index in range(2000)),
    ):
        workbench = SongWorkbench(notes=notes)
        assert len(workbench.form.sections) <= 16
        assert isinstance(workbench.conductor_line(), str)
        assert isinstance(workbench.shareable_sheet(), str)
        workbench.clock_snapshot()
