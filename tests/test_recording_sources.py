"""Per-source recording truth projection stays conservative and path-free."""

from types import SimpleNamespace

from core.recording_sources import (
    RecordingSourceState,
    project_recording_sources,
    summarize_recording_sources,
)


def _receipt(participant_id="p-1", name="Jeff", digest="d1", channels=2,
             kind="musician"):
    return SimpleNamespace(
        participant_id=participant_id,
        display_name=name,
        recorder_key_sha256=digest,
        channels=channels,
        source_kind=kind,
        source_fingerprint_sha256="f" * 64,
    )


ROSTER = (("p-1", "Jeff"), ("p-2", "Alex"))


def _project(**overrides):
    values = dict(
        phase="recording",
        roster=ROSTER,
        receipts=(_receipt(),),
        conflicted_keys=(),
        receipts_frozen=False,
    )
    values.update(overrides)
    return project_recording_sources(**values)


def test_unproven_participants_wait_and_never_go_missing_before_freeze():
    rows = _project()
    states = {row.participant_id: row.state for row in rows}
    assert states["p-1"] is RecordingSourceState.RECORDING
    assert states["p-2"] is RecordingSourceState.WAITING
    assert RecordingSourceState.MISSING not in states.values()


def test_missing_exists_only_after_the_receipt_set_freezes():
    rows = _project(receipts_frozen=True)
    states = {row.participant_id: row.state for row in rows}
    assert states["p-1"] is RecordingSourceState.FINALIZED
    assert states["p-2"] is RecordingSourceState.MISSING
    for phase in ("validating", "complete", "needs_attention"):
        rows = _project(phase=phase)
        assert {row.state for row in rows} == {
            RecordingSourceState.FINALIZED,
            RecordingSourceState.MISSING,
        }


def test_only_an_explicit_conflict_key_renders_conflicted():
    rows = _project(conflicted_keys=("d1",))
    states = {row.participant_id: row.state for row in rows}
    assert states["p-1"] is RecordingSourceState.CONFLICTED
    assert states["p-2"] is RecordingSourceState.WAITING


def test_count_in_is_an_active_phase_and_preroll_arms():
    assert {row.state for row in _project(phase="count_in", receipts=())} == {
        RecordingSourceState.WAITING
    }
    assert {row.state for row in _project(phase="preflight", receipts=())} == {
        RecordingSourceState.ARMED
    }
    assert _project(phase="idle") == ()
    assert _project(phase="error") == ()
    assert _project(phase="") == ()


def test_shared_track_rows_come_from_receipt_or_planned_intent():
    with_receipt = _project(
        receipts=(
            _receipt(),
            _receipt(
                kind="reference_track",
                name="Song.wav",
                digest="d9",
                participant_id="p-ref",
            ),
        ),
    )
    shared = [row for row in with_receipt if row.kind == "shared_track"]
    assert len(shared) == 1
    assert shared[0].state is RecordingSourceState.RECORDING
    assert shared[0].participant_id == ""

    planned = _project(shared_track_planned=True)
    shared = [row for row in planned if row.kind == "shared_track"]
    assert shared[0].state is RecordingSourceState.WAITING
    frozen = _project(shared_track_planned=True, receipts_frozen=True)
    shared = [row for row in frozen if row.kind == "shared_track"]
    assert shared[0].state is RecordingSourceState.MISSING


def test_projection_is_bounded_path_free_and_redacted():
    rows = _project(
        roster=(("p-1", "  Jeff   the\nGuitarist  " + "x" * 300),),
        receipts=(_receipt(name="Jeff"),),
    )
    assert len(rows[0].display_name) <= 128
    assert "\n" not in rows[0].display_name
    assert repr(rows[0]) == "RecordingSourcePresentation(private=[redacted])"
    encoded = repr(rows) + str(summarize_recording_sources(rows))
    assert "f" * 64 not in encoded


def test_summary_counts_states_without_identities():
    counts = summarize_recording_sources(_project())
    assert counts == {"recording": 1, "waiting": 1}
    assert "Jeff" not in str(counts) and "p-1" not in str(counts)


def test_duplicate_and_empty_roster_ids_collapse():
    rows = _project(roster=(("p-1", "A"), ("p-1", "B"), ("", "C")))
    assert len(rows) == 1
    assert rows[0].display_name == "A"
