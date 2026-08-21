"""Verb discovery: what an account can run, and what it honestly cannot."""

from __future__ import annotations

import json

import pytest

from core.music_ai_catalog import (
    SONG_TOOL_VERBS,
    UNSUPPORTED_MOISES_FEATURES,
    failed_catalog,
    resolve_song_tools,
    verb_for_key,
)
from core.music_ai_client import (
    DOCUMENTED_STEMS_WORKFLOW,
    MusicAIResponse,
    MusicAIWorkflow,
)
from core.music_ai_results import (
    ARTIFACT_AUDIO,
    SongArtifact,
    SongToolRun,
    download_artifacts,
    extract_facts,
    interpret_job,
)
from core.music_ai_client import MusicAIJob


def _workflow(name, slug, description="") -> MusicAIWorkflow:
    return MusicAIWorkflow(id=slug, name=name, slug=slug, description=description)


REALISTIC_ACCOUNT = [
    _workflow("Advanced Stem Separation", "my-stems", "Isolate vocals and drums"),
    _workflow(
        "Extract Beat map and BPM",
        "untitled-workflow-e78c2e",
        "Transcribe song BPM and beats with AI for precise rhythm analysis.",
    ),
    _workflow("Transcribe and Align Lyrics", "lyrics-v2", "Align lyrics to audio"),
    _workflow("Pitch shift and time stretch", "shift", "Change key or speed"),
    _workflow("Auto Master", "master-2", "Mastering chain"),
    _workflow("Translate lyrics", "translate", "Localize lyrics to Spanish"),
]


# ----------------------------------------------------------------------
# Matching
# ----------------------------------------------------------------------
def test_a_realistic_account_maps_onto_the_product_verbs():
    catalog = resolve_song_tools(REALISTIC_ACCOUNT)
    supported = {item.key: item.workflow_slug for item in catalog.available}

    assert supported["stems"] == "my-stems"
    assert supported["chords"] == "untitled-workflow-e78c2e"
    assert supported["lyrics"] == "lyrics-v2"
    assert supported["key_tempo"] == "shift"
    assert supported["master"] == "master-2"


def test_detection_and_transformation_are_not_confused():
    """Both mention "key"; only one of them rewrites the audio."""

    catalog = resolve_song_tools(
        [
            _workflow("Detect key and tempo", "detect", "Analyze key and tempo"),
            _workflow("Transpose to a new key", "transpose", "Pitch shift a song"),
        ]
    )
    assert catalog.capability("chords").workflow_slug == "detect"
    assert catalog.capability("key_tempo").workflow_slug == "transpose"


def test_a_workflow_named_for_its_job_beats_a_passing_mention():
    catalog = resolve_song_tools(
        [
            _workflow("Batch encoder", "encode", "Handy before you run lyrics jobs"),
            _workflow("Lyric transcription", "real-lyrics", ""),
        ]
    )
    assert catalog.capability("lyrics").workflow_slug == "real-lyrics"


def test_unmatched_verbs_are_unsupported_with_a_reason_not_a_button():
    catalog = resolve_song_tools(REALISTIC_ACCOUNT)
    sections = catalog.capability("sections")

    assert not sections.supported
    assert not sections.workflow_slug
    assert "section" in sections.reason.lower()
    assert "unavailable" in sections.describe()


def test_an_unrelated_account_offers_only_the_documented_shared_template():
    """No workflows must not silently become no features, or fake ones."""

    catalog = resolve_song_tools([_workflow("Translate", "t", "Localization only")])

    assert [item.key for item in catalog.available] == ["stems"]
    stems = catalog.capability("stems")
    assert stems.workflow_slug == DOCUMENTED_STEMS_WORKFLOW
    assert stems.shared_template
    assert "shared Music AI template" in stems.describe()


def test_the_shared_template_is_not_offered_twice():
    catalog = resolve_song_tools(
        [_workflow("Vocals and accompaniment", DOCUMENTED_STEMS_WORKFLOW, "")]
    )
    stems = catalog.capability("stems")
    assert stems.workflow_slug == DOCUMENTED_STEMS_WORKFLOW
    assert not stems.shared_template


def test_shared_templates_can_be_declined():
    catalog = resolve_song_tools([], allow_shared_templates=False)
    assert catalog.available == ()
    assert not catalog.usable


def test_an_empty_account_says_so_rather_than_looking_broken():
    catalog = resolve_song_tools([])
    assert catalog.discovered
    assert "Split stems" in catalog.summary_line()


def test_a_discovery_failure_offers_nothing_and_reports_why():
    catalog = failed_catalog("Music AI rejected this API key.")

    assert not catalog.usable
    assert not catalog.discovered
    assert catalog.summary_line() == "Music AI rejected this API key."
    assert all(not item.supported for item in catalog.capabilities)


def test_the_six_product_verbs_are_the_primary_list():
    primary = [verb.key for verb in SONG_TOOL_VERBS if verb.primary]
    assert primary == [
        "stems",
        "chords",
        "lyrics",
        "sections",
        "key_tempo",
        "master",
    ]


def test_every_verb_is_retrievable_and_carries_help_text():
    for verb in SONG_TOOL_VERBS:
        assert verb_for_key(verb.key) is verb
        assert verb.summary
        assert verb.unsupported_hint
    assert verb_for_key("nope") is None


def test_app_only_moises_features_are_listed_with_a_reason():
    names = [name for name, _reason in UNSUPPORTED_MOISES_FEATURES]
    assert any("library" in name.lower() for name in names)
    assert any("live separation" in name.lower() for name in names)
    for _name, reason in UNSUPPORTED_MOISES_FEATURES:
        assert reason.strip()


def test_no_moises_password_is_ever_requested():
    for name, reason in UNSUPPORTED_MOISES_FEATURES:
        assert "password" not in name.lower()
        if "password" in reason.lower():
            assert "never asks" in reason.lower()


# ----------------------------------------------------------------------
# Result interpretation
# ----------------------------------------------------------------------
def test_stem_urls_become_audio_artifacts():
    catalog = resolve_song_tools(REALISTIC_ACCOUNT)
    job = MusicAIJob(
        id="j1",
        status="SUCCEEDED",
        result={
            "vocals": "https://cdn.music.ai/a/vocals.wav",
            "accompaniments": "https://cdn.music.ai/a/accompaniments.wav",
        },
    )

    run = interpret_job(job, catalog.capability("stems"), source_name="mix.wav")

    assert len(run.audio_artifacts) == 2
    assert all(item.kind == ARTIFACT_AUDIO for item in run.audio_artifacts)
    assert "2 audio files" in run.summary_line()


def test_results_from_outside_the_published_hosts_are_dropped():
    catalog = resolve_song_tools(REALISTIC_ACCOUNT)
    job = MusicAIJob(
        id="j2",
        status="SUCCEEDED",
        result={"vocals": "https://attacker.example.com/vocals.wav"},
    )
    run = interpret_job(job, catalog.capability("stems"))
    assert run.artifacts == ()


@pytest.mark.parametrize(
    ("payload", "expected_key", "expected_tempo"),
    [
        ({"key": "G major", "bpm": 96}, "G major", "96"),
        ({"musicalKey": "Am", "tempo": 128.4}, "A minor", "128"),
        ({"key": "not a key", "bpm": 5000}, "", ""),
        ({}, "", ""),
    ],
)
def test_only_genuinely_present_facts_are_reported(
    payload, expected_key, expected_tempo
):
    facts = extract_facts(payload)
    assert facts["detected_key"] == expected_key
    assert facts["detected_tempo"] == expected_tempo


def test_chord_and_section_lists_are_read_from_structured_results():
    facts = extract_facts(
        {
            "chords": [{"chord": "Am"}, {"chord": "F"}, {"chord": "C"}],
            "sections": [{"label": "Verse"}, {"label": "Chorus"}],
        }
    )
    assert facts["chord_symbols"] == ("Am", "F", "C")
    assert facts["detected_sections"] == ("Verse", "Chorus")


def test_lyrics_are_read_from_either_a_string_or_word_timings():
    assert extract_facts({"lyrics": "hold on"})["lyrics_text"] == "hold on"
    assert (
        extract_facts({"transcript": [{"text": "hold"}, {"text": "on"}]})[
            "lyrics_text"
        ]
        == "hold on"
    )


def test_downloaded_results_land_locally_and_are_re_read(tmp_path):
    catalog = resolve_song_tools(REALISTIC_ACCOUNT)
    chords_url = "https://cdn.music.ai/a/chords.json"
    audio_url = "https://cdn.music.ai/a/vocals.wav"
    payload = json.dumps({"key": "D major", "bpm": 112}).encode()

    class Transport:
        def request(self, method, url, *, headers, body=None, timeout=30.0):
            if url == chords_url:
                return MusicAIResponse(200, payload)
            return MusicAIResponse(200, b"RIFFaudio")

    run = SongToolRun(
        verb_key="chords",
        label="Chords & key",
        workflow_slug="untitled-workflow-e78c2e",
        job_id="j3",
        source_name="mix.wav",
        artifacts=interpret_job(
            MusicAIJob(
                id="j3",
                status="SUCCEEDED",
                result={"chords": chords_url, "vocals": audio_url},
            ),
            catalog.capability("chords"),
        ).artifacts,
    )

    finished = download_artifacts(
        run, transport=Transport(), directory=tmp_path / "out"
    )

    assert finished.detected_key == "D major"
    assert finished.detected_tempo == "112"
    saved = [item.local_path for item in finished.artifacts]
    assert all(saved)
    assert (tmp_path / "out" / "chords.json").read_bytes() == payload


def test_a_result_download_failure_leaves_the_artifact_rather_than_lying(tmp_path):
    catalog = resolve_song_tools(REALISTIC_ACCOUNT)
    run = interpret_job(
        MusicAIJob(
            id="j4",
            status="SUCCEEDED",
            result={"vocals": "https://cdn.music.ai/a/vocals.wav"},
        ),
        catalog.capability("stems"),
    )

    class Failing:
        def request(self, method, url, *, headers, body=None, timeout=30.0):
            return MusicAIResponse(503, b"")

    finished = download_artifacts(
        run, transport=Failing(), directory=tmp_path / "out"
    )
    assert not finished.artifacts[0].downloaded


def test_downloaded_filenames_cannot_escape_the_results_directory(tmp_path):
    catalog = resolve_song_tools(REALISTIC_ACCOUNT)
    run = interpret_job(
        MusicAIJob(
            id="j5",
            status="SUCCEEDED",
            result={"vocals": "https://cdn.music.ai/a/%2e%2e%2f%2e%2e%2fevil.wav"},
        ),
        catalog.capability("stems"),
    )

    class Transport:
        def request(self, method, url, *, headers, body=None, timeout=30.0):
            return MusicAIResponse(200, b"RIFF")

    target = tmp_path / "out"
    finished = download_artifacts(run, transport=Transport(), directory=target)

    for artifact in finished.artifacts:
        if artifact.local_path:
            assert target.resolve() == type(target)(artifact.local_path).parent.resolve()


# ----------------------------------------------------------------------
# A job can succeed and still return nothing usable
# ----------------------------------------------------------------------
def test_a_succeeded_job_with_no_usable_output_says_so():
    """"0 files" reads like a bug; this is a real workflow outcome."""

    run = SongToolRun(
        verb_key="stems",
        label="Split stems",
        workflow_slug="my-stems",
        job_id="j-empty",
        source_name="mix.wav",
    )

    assert run.is_empty
    summary = run.summary_line()
    assert "returned nothing WebJam could use" in summary
    assert "Music AI dashboard" in summary
    assert "0 file" not in summary


def test_results_dropped_for_being_off_host_read_as_empty_not_as_zero():
    catalog = resolve_song_tools(REALISTIC_ACCOUNT)
    run = interpret_job(
        MusicAIJob(
            id="j-off-host",
            status="SUCCEEDED",
            result={"vocals": "https://attacker.example.com/vocals.wav"},
        ),
        catalog.capability("stems"),
    )

    assert run.is_empty
    assert "returned nothing WebJam could use" in run.summary_line()


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "1 audio file"), (2, "2 audio files"), (5, "5 audio files")],
)
def test_counts_read_as_english(count, expected):
    run = SongToolRun(
        verb_key="stems",
        label="Split stems",
        workflow_slug="s",
        job_id="j",
        source_name="mix.wav",
        artifacts=tuple(
            SongArtifact(f"stem{index}", ARTIFACT_AUDIO, local_path=f"/tmp/{index}.wav")
            for index in range(count)
        ),
    )
    assert expected in run.summary_line()


def test_a_single_chord_or_section_is_not_pluralised():
    run = SongToolRun(
        verb_key="chords",
        label="Chords & key",
        workflow_slug="c",
        job_id="j",
        source_name="mix.wav",
        chord_symbols=("G",),
        detected_sections=("Verse",),
    )
    assert "1 chord," in run.summary_line()
    assert "1 section" in run.summary_line()
    assert "chords," not in run.summary_line()
