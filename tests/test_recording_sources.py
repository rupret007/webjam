"""Per-source recording truth projection stays conservative and path-free."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.recording_sources import (
    RecordingSourceKind,
    RecordingSourcePresentation,
    RecordingSourcePresentationError,
    RecordingSourceState,
    project_recording_sources,
    summarize_recording_sources,
    validate_exact_recording_sources,
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
    for phase in ("finalizing", "validating", "complete", "needs_attention"):
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


def test_stopping_never_claims_the_server_has_already_stopped():
    rows = _project(phase="stopping")
    states = {row.participant_id: row.state for row in rows}
    assert states == {
        "p-1": RecordingSourceState.STOPPING,
        "p-2": RecordingSourceState.WAITING,
    }
    shared = _project(
        phase="stopping",
        receipts=(),
        shared_track_planned=True,
    )[-1]
    assert shared.state is RecordingSourceState.WAITING


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


def test_guest_side_structural_guard_idle_phase_yields_no_claims():
    """Guests never render per-musician badges.

    Guest controllers receive recording phase as widget-level pushes only;
    their own RecordingCoordinator stays idle, so the projection must
    return nothing even when stale receipts or roster entries exist. This
    is the structural guarantee that guest cards cannot claim per-musician
    recording proof the guest does not hold.
    """

    for stale_phase in ("idle", "", "error"):
        assert (
            project_recording_sources(
                phase=stale_phase,
                roster=ROSTER,
                receipts=(_receipt(),),
                conflicted_keys=("d1",),
                receipts_frozen=False,
                shared_track_planned=True,
            )
            == ()
        )


def _exact_rows() -> tuple[RecordingSourcePresentation, ...]:
    return (
        RecordingSourcePresentation(
            participant_id="host-id",
            display_name="Host server stem",
            kind="musician",
            state=RecordingSourceState.RECORDING,
            channels=1,
            logical_source_id="00000000-0000-4000-8000-000000000101",
            source_kind=RecordingSourceKind.JAMULUS_SERVER,
            channel_id=7,
            meter_level=0.42,
            dropout_count=0,
            overloaded=False,
        ),
        RecordingSourcePresentation(
            participant_id="host-id",
            display_name="Host Local Original",
            kind="local_original",
            state=RecordingSourceState.RECORDING,
            channels=2,
            logical_source_id="00000000-0000-4000-8000-000000000102",
            source_kind=RecordingSourceKind.LOCAL_ORIGINAL,
            meter_level=None,
            dropout_count=2,
            overloaded=True,
        ),
        RecordingSourcePresentation(
            participant_id="",
            display_name="Shared Track",
            kind="shared_track",
            state=RecordingSourceState.RECORDING,
            channels=2,
            logical_source_id="00000000-0000-4000-8000-000000000103",
            source_kind=RecordingSourceKind.SHARED_TRACK,
            dropout_count=None,
            overloaded=None,
        ),
    )


def test_exact_snapshot_accepts_all_three_routes_and_redacts_identity():
    rows = validate_exact_recording_sources(_exact_rows())

    assert [row.source_kind for row in rows] == [
        RecordingSourceKind.JAMULUS_SERVER,
        RecordingSourceKind.LOCAL_ORIGINAL,
        RecordingSourceKind.SHARED_TRACK,
    ]
    assert [row.channels for row in rows] == [1, 2, 2]
    assert "host-id" not in repr(rows)
    assert "00000000" not in repr(rows)


@pytest.mark.parametrize(
    "invalid",
    (
        lambda rows: (replace(rows[0], logical_source_id=""), *rows[1:]),
        lambda rows: (replace(rows[0], channels=0), *rows[1:]),
        lambda rows: (replace(rows[0], meter_level=1.01), *rows[1:]),
        lambda rows: (replace(rows[1], channel_id=7), rows[0], rows[2]),
        lambda rows: (replace(rows[2], kind="musician"), *rows[:2]),
        lambda rows: (rows[0], replace(rows[1], dropout_count=-1), rows[2]),
        lambda rows: (rows[0], replace(rows[1], overloaded="yes"), rows[2]),
        lambda rows: (rows[0], replace(rows[1], logical_source_id=rows[0].logical_source_id), rows[2]),
    ),
)
def test_exact_snapshot_rejects_incomplete_or_ambiguous_rows(invalid):
    with pytest.raises(RecordingSourcePresentationError):
        validate_exact_recording_sources(invalid(_exact_rows()))


def test_legacy_receipt_projection_cannot_be_promoted_to_exact_snapshot():
    with pytest.raises(RecordingSourcePresentationError):
        validate_exact_recording_sources(_project())
