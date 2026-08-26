"""The companion contract: what Music publishes, and what it will accept.

The load-bearing property is asymmetry. Outward, the projection carries the
song and nothing else — no path, no key, no URL. Inward, a command has no field
in which to name a file, so a companion cannot point Song tools at anything the
host did not already choose on the desktop.
"""

from __future__ import annotations

import json

import pytest

from core.music_companion import (
    COMMAND_RUN_TOOL,
    COMMAND_SUGGEST_CHORDS,
    COMMAND_WRITE_HELP,
    CONTRACT_VERSION,
    JOB_IDLE,
    JOB_RUNNING,
    MAX_LYRIC_CHARS,
    MAX_OVERLAY_ROWS,
    REQUESTABLE_VERBS,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    STATUS_UNSUPPORTED,
    MusicCompanionCommand,
    build_snapshot,
    describe_contract,
    evaluate_command,
    parse_command,
)
from core.song_workbench import SongWorkbench

SONG = """Key: G major
Tempo: 120
[Verse x8]
G D Em C
Walking out the back door again
[Chorus x8]
C G D G
Hold on to the wheel and steer
"""


def _workbench():
    now = {"value": 0.0}
    workbench = SongWorkbench(
        title="Tuesday", notes=SONG, monotonic=lambda: now["value"]
    )
    return workbench, now


def _live_snapshot(**overrides):
    workbench, _now = _workbench()
    workbench.clock.start()
    defaults = dict(
        revision=1,
        is_music_session=True,
        clock=workbench.clock_snapshot(),
        form_rows=workbench.form_overlay(),
        lyric_line="Walking out the back door again",
        shared_track_loaded=True,
        is_host=True,
        tools_available=("stems", "chords", "lyrics"),
    )
    defaults.update(overrides)
    return build_snapshot(**defaults)


# ----------------------------------------------------------------------
# What the projection carries
# ----------------------------------------------------------------------
def test_the_projection_carries_the_song():
    snapshot = _live_snapshot()

    assert snapshot.is_music_session
    assert snapshot.section == "Verse"
    assert snapshot.section_index == 0
    assert snapshot.bar == 1
    assert snapshot.bars_total == 16
    assert snapshot.key == "G major"
    assert snapshot.bpm == 120.0
    assert snapshot.chords_now == ("G", "D", "Em", "C")
    assert snapshot.chord_overlay[0] == "Verse: G D Em C"
    assert snapshot.lyric_line == "Walking out the back door again"


def test_every_musical_fact_carries_its_source():
    snapshot = _live_snapshot()
    assert snapshot.key_source == "stated"
    assert snapshot.bpm_source == "stated"
    assert snapshot.following_audio is False


def test_a_session_with_no_song_publishes_no_position():
    snapshot = build_snapshot(is_music_session=True)
    assert snapshot.position_known is False
    assert snapshot.section == ""
    assert snapshot.bar == 0


def test_a_parked_clock_with_a_tempo_does_not_publish_verse():
    """Writing [Verse] [Chorus] and 120 BPM is not a place until someone starts."""

    workbench, _now = _workbench()
    clock = workbench.clock_snapshot()
    snapshot = build_snapshot(
        is_music_session=True,
        clock=clock,
        form_rows=workbench.form_overlay(),
    )

    assert clock.parked is True
    assert clock.section_label == "Verse"
    assert clock.bar == 1
    assert snapshot.position_known is False
    assert snapshot.section == ""
    assert snapshot.bar == 0
    assert snapshot.chords_now == ()
    assert snapshot.key == "G major"
    assert snapshot.bpm == 120.0
    assert snapshot.chord_overlay[0].startswith("Verse")


def test_a_non_music_session_publishes_nothing_about_a_song():
    snapshot = build_snapshot(is_music_session=False)

    assert not snapshot.is_music_session
    assert snapshot.chord_overlay == ()
    assert snapshot.lyric_line == ""
    assert snapshot.tools_available == ()


def test_a_running_job_is_visible_without_naming_a_file():
    snapshot = _live_snapshot(job_verb="stems", job_label="Split stems")

    assert snapshot.job.state == JOB_RUNNING
    assert snapshot.job.label == "Split stems"
    assert "wav" not in json.dumps(snapshot.to_public_dict()).lower()


def test_an_idle_session_carries_no_job_label():
    snapshot = _live_snapshot()
    assert snapshot.job.state == JOB_IDLE
    assert snapshot.job.label == ""


def test_a_suggestion_is_published_labelled_as_one():
    workbench, _now = _workbench()
    advice = workbench.chord_advice(section_name="Chorus")
    snapshot = _live_snapshot(
        suggestion=advice.suggestions[0], suggestion_section="Chorus"
    )

    assert snapshot.suggestion is not None
    assert snapshot.suggestion.is_suggestion
    assert snapshot.suggestion.section == "Chorus"
    assert snapshot.suggestion.chords
    assert snapshot.to_public_dict()["suggestion"]["label"] == "suggestion"


def test_the_wire_form_is_plain_json():
    published = _live_snapshot().to_public_dict()
    assert json.loads(json.dumps(published)) == published


# ----------------------------------------------------------------------
# What the projection refuses to carry
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "leak",
    [
        "/Users/jeff/Music/mix.wav",
        "~/Music/mix.wav",
        "C:\\Users\\jeff\\mix.wav",
        "../../etc/passwd",
        "https://api.music.ai/v1/job/abc",
        "https://storage.googleapis.com/upload/abc?sig=x",
        "see file:///tmp/secret",
    ],
)
def test_anything_path_or_url_shaped_is_scrubbed(leak):
    snapshot = build_snapshot(
        is_music_session=True,
        lyric_line=leak,
        tools_unavailable_reason=leak,
        job_verb="stems",
        job_label=leak,
    )

    assert snapshot.lyric_line == ""
    assert snapshot.tools_unavailable_reason == ""
    assert snapshot.job.label == ""


def test_no_api_key_can_reach_a_companion():
    """The contract has no field for one, and text carrying one is bounded."""

    published = _live_snapshot(
        tools_unavailable_reason="Song tools need a Music AI key from "
        "https://music.ai/dash."
    ).to_public_dict()

    assert "api_key" not in published
    assert "music_ai_api_key" not in json.dumps(published)
    # The console URL is scrubbed with every other URL.
    assert published["tools_unavailable_reason"] == ""


def test_the_shared_track_is_reported_as_a_fact_not_a_filename():
    snapshot = _live_snapshot()
    published = snapshot.to_public_dict()

    assert published["shared_track_loaded"] is True
    assert "source_name" not in published
    assert "path" not in json.dumps(published)


def test_the_overlay_and_lyric_are_bounded():
    workbench = SongWorkbench(
        notes="Tempo: 120\n"
        + "".join(f"[Part {index} x4]\nC G\n" for index in range(20))
    )
    snapshot = build_snapshot(
        is_music_session=True,
        form_rows=workbench.form_overlay(max_rows=20),
        lyric_line="x" * 500,
    )

    assert len(snapshot.chord_overlay) <= MAX_OVERLAY_ROWS
    assert len(snapshot.lyric_line) <= MAX_LYRIC_CHARS


def test_only_requestable_verbs_are_advertised():
    snapshot = build_snapshot(
        is_music_session=True,
        tools_available=("stems", "master", "enhance", "nonsense"),
    )
    assert snapshot.tools_available == ("stems",)


# ----------------------------------------------------------------------
# What a command cannot say
# ----------------------------------------------------------------------
def test_a_command_has_no_field_in_which_to_name_a_file():
    import dataclasses

    fields = {field.name for field in dataclasses.fields(MusicCompanionCommand)}
    assert fields == {"name", "verb", "section"}


def test_extra_wire_fields_are_dropped_on_the_way_in():
    command = parse_command(
        {
            "command": COMMAND_RUN_TOOL,
            "verb": "stems",
            "path": "/Users/jeff/secret.wav",
            "inputUrl": "https://storage.googleapis.com/upload/x",
            "api_key": "sk-live-1",
        }
    )

    assert command is not None
    assert command.verb == "stems"
    assert not hasattr(command, "path")
    assert not hasattr(command, "api_key")


@pytest.mark.parametrize(
    "payload",
    [None, {}, {"command": "shutdown"}, {"command": ""}, "not a mapping", []],
)
def test_an_unknown_request_is_not_parsed(payload):
    assert parse_command(payload) is None


def test_an_unknown_request_is_refused_by_name():
    decision = evaluate_command(None, _live_snapshot())
    assert decision.status == STATUS_UNSUPPORTED
    assert not decision.accepted


# ----------------------------------------------------------------------
# What the desktop will accept
# ----------------------------------------------------------------------
def test_the_host_may_request_a_tool_on_the_track_they_already_chose():
    decision = evaluate_command(
        parse_command({"command": COMMAND_RUN_TOOL, "verb": "stems"}),
        _live_snapshot(),
    )
    assert decision.status == STATUS_ACCEPTED
    assert decision.command.verb == "stems"


def test_a_tool_request_without_a_shared_track_is_refused():
    """The only file a companion may act on is one the host already chose."""

    decision = evaluate_command(
        parse_command({"command": COMMAND_RUN_TOOL, "verb": "stems"}),
        _live_snapshot(shared_track_loaded=False),
    )

    assert decision.status == STATUS_REJECTED
    assert "Load a Shared Track on the desktop first" in decision.reason
    assert "never uploads the live jam" in decision.reason


def test_a_guest_cannot_request_a_tool():
    decision = evaluate_command(
        parse_command({"command": COMMAND_RUN_TOOL, "verb": "stems"}),
        _live_snapshot(is_host=False),
    )
    assert decision.status == STATUS_REJECTED
    assert "Only the host" in decision.reason


def test_a_verb_the_account_cannot_run_is_refused_with_the_reason():
    decision = evaluate_command(
        parse_command({"command": COMMAND_RUN_TOOL, "verb": "lyrics"}),
        _live_snapshot(
            tools_available=("stems",),
            tools_unavailable_reason="No lyric workflow is on this account.",
        ),
    )
    assert decision.status == STATUS_UNSUPPORTED
    assert "No lyric workflow" in decision.reason


@pytest.mark.parametrize("verb", ["", "master", "enhance", "delete_everything"])
def test_only_the_documented_verbs_may_be_requested(verb):
    decision = evaluate_command(
        parse_command({"command": COMMAND_RUN_TOOL, "verb": verb}),
        _live_snapshot(),
    )
    assert decision.status == STATUS_UNSUPPORTED


def test_a_second_tool_request_while_one_runs_is_refused():
    decision = evaluate_command(
        parse_command({"command": COMMAND_RUN_TOOL, "verb": "stems"}),
        _live_snapshot(job_verb="chords", job_label="Chords & key"),
    )
    assert decision.status == STATUS_REJECTED
    assert "already running" in decision.reason


def test_write_help_needs_no_host_role_key_or_track():
    """It is local, read-only, and uploads nothing."""

    quiet = build_snapshot(is_music_session=True, is_host=False)
    for name in (COMMAND_WRITE_HELP, COMMAND_SUGGEST_CHORDS):
        decision = evaluate_command(parse_command({"command": name}), quiet)
        assert decision.status == STATUS_ACCEPTED


def test_write_help_can_target_a_named_part():
    decision = evaluate_command(
        parse_command({"command": COMMAND_SUGGEST_CHORDS, "section": "Chorus"}),
        _live_snapshot(),
    )
    assert decision.accepted
    assert decision.command.section == "Chorus"


def test_nothing_is_accepted_outside_a_music_session():
    quiet = build_snapshot(is_music_session=False)
    for name in (COMMAND_RUN_TOOL, COMMAND_WRITE_HELP, COMMAND_SUGGEST_CHORDS):
        decision = evaluate_command(parse_command({"command": name}), quiet)
        assert not decision.accepted
        assert "Music session" in decision.reason


# ----------------------------------------------------------------------
# The published contract
# ----------------------------------------------------------------------
def test_the_contract_is_declared_for_the_companion_track():
    contract = describe_contract()

    assert contract["version"] == CONTRACT_VERSION
    assert set(contract["commands"]) == {
        COMMAND_RUN_TOOL,
        COMMAND_WRITE_HELP,
        COMMAND_SUGGEST_CHORDS,
    }
    assert contract["requestable_verbs"] == REQUESTABLE_VERBS
    assert any("cannot name a file" in item for item in contract["guarantees"])
    assert any("only a request" in item for item in contract["guarantees"])
    assert any("parked count" in item for item in contract["guarantees"])


def test_the_snapshot_field_list_is_pinned():
    """A rename breaks the companion track, so it changes deliberately."""

    assert set(describe_contract()["snapshot_fields"]) == {
        "contract_version",
        "revision",
        "is_music_session",
        "section",
        "section_index",
        "bar",
        "bar_in_section",
        "bars_total",
        "beat",
        "position_s",
        "position_known",
        "position_source",
        "following_audio",
        "section_lengths_assumed",
        "key",
        "key_source",
        "bpm",
        "bpm_source",
        "chords_now",
        "chord_overlay",
        "lyric_line",
        "shared_track_loaded",
        "is_host",
        "tools_available",
        "tools_unavailable_reason",
        "job",
        "suggestion",
    }


def test_the_contract_imports_nothing_from_the_ui():
    """core/ owns this so the companion track can build against it freely."""

    import ast
    from pathlib import Path

    tree = ast.parse(
        (Path(__file__).resolve().parent.parent / "core" / "music_companion.py")
        .read_text()
    )
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert imported == {"__future__", "re", "dataclasses", "typing"}
