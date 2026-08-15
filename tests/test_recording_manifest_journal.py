from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import pytest

from core.recording_manifest_journal import (
    JOURNAL_SCHEMA_VERSION,
    JOURNAL_FILE_MODE,
    MAX_JOURNAL_BYTES,
    JournalDirectoryIssue,
    JournalLoadResult,
    RecordingManifestJournal,
    RecordingManifestJournalError,
)
from core.recording_readiness import RecordingStorageCheck, RecordingStorageStatus
from core.session_recording_plan import (
    InputMapBinding,
    SessionRecordingPlan,
    SharedTrackBinding,
)
from core.take_project import (
    HostIdentity,
    RecoveryStatus,
    SessionEvidence,
    SessionTimelineEvent,
)


def _take_id() -> str:
    return str(uuid.uuid4())


def _evidence() -> SessionEvidence:
    participant_id = _take_id()
    return SessionEvidence(
        protocol_version="jamulus-3.12.2 / webjam-v2",
        started_utc="2026-07-14T01:02:03Z",
        host=HostIdentity(participant_id, "Jeff"),
        recovery_status=RecoveryStatus.NOT_NEEDED,
        timeline=(
            SessionTimelineEvent(
                "recording_started",
                occurred_utc="2026-07-14T01:02:03Z",
                participant_id=participant_id,
            ),
        ),
    )


def _plan(take_id: str) -> SessionRecordingPlan:
    return SessionRecordingPlan(
        session_id=_take_id(),
        take_id=take_id,
        plan_generation=3,
        roster=((_take_id(), "Jeff"), (_take_id(), "Alex")),
        expected_server_stems=("host-stem", "guest-stem"),
        count_in_frames=48_000,
        pre_roll_frames=4_800,
        storage=RecordingStorageCheck(
            status=RecordingStorageStatus.READY,
            detail="Recording storage is ready.",
            free_bytes=20_000_000,
            required_bytes=10_000_000,
        ),
        expected_source_count=4,
        created_at_utc="2026-08-15T12:00:00Z",
        shared_track=SharedTrackBinding("ab" * 32, 7),
        shared_track_planned=True,
        input_maps=(
            InputMapBinding("Vocal", 1, local_original_enabled=True),
            InputMapBinding("Keys", 2, local_original_enabled=True),
        ),
    )


def _evidence_for_plan(plan: SessionRecordingPlan) -> SessionEvidence:
    evidence = _evidence()
    return SessionEvidence(
        protocol_version=evidence.protocol_version,
        started_utc=evidence.started_utc,
        host=evidence.host,
        recovery_status=evidence.recovery_status,
        timeline=evidence.timeline,
        recording_plan_fingerprint=plan.plan_fingerprint(),
    )


def test_create_load_update_and_remove_private_typed_journal(tmp_path: Path) -> None:
    journal = RecordingManifestJournal(tmp_path / "takes")
    take_id = _take_id()
    evidence = _evidence()

    path = journal.create(take_id, evidence)

    assert path == journal.path_for(take_id)
    assert path.parent == tmp_path / "takes" / ".webjam-recording-evidence"
    assert path.stat().st_mode & 0o777 == JOURNAL_FILE_MODE
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "take_id", "session", "plan"}
    assert payload["schema_version"] == JOURNAL_SCHEMA_VERSION
    assert payload["take_id"] == take_id
    assert payload["session"] == evidence.to_dict()
    assert payload["plan"] is None
    assert "config" not in payload
    assert "invite" not in payload

    loaded = journal.load(take_id)

    assert loaded == JournalLoadResult(take_id, evidence, trusted=True)
    assert journal.directory.stat().st_mode & 0o777 == 0o700

    updated = SessionEvidence(
        protocol_version=evidence.protocol_version,
        started_utc=evidence.started_utc,
        ended_utc="2026-07-14T01:07:45Z",
        host=evidence.host,
        recovery_status=RecoveryStatus.RECOVERED,
        recovery_notes=("Recorder reconnect completed.",),
        timeline=evidence.timeline,
    )
    assert journal.update(take_id, updated) == path
    assert journal.load(take_id) == JournalLoadResult(take_id, updated, trusted=True)

    assert journal.remove(take_id) is True
    assert journal.remove(take_id) is False
    assert journal.load(take_id) is None


def test_create_refuses_to_overwrite_and_update_failure_keeps_old_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = RecordingManifestJournal(tmp_path)
    take_id = _take_id()
    original = _evidence()
    journal.create(take_id, original)

    with pytest.raises(FileExistsError):
        journal.create(take_id, original)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated interrupted rename")

    monkeypatch.setattr("core.recording_manifest_journal.os.replace", fail_replace)
    with pytest.raises(RecordingManifestJournalError):
        journal.update(
            take_id,
            SessionEvidence(recovery_status=RecoveryStatus.RECOVERED),
        )

    loaded = journal.load(take_id)
    assert loaded is not None
    assert loaded.trusted is True
    assert loaded.evidence == original


def test_malformed_or_untrusted_journal_fails_closed_to_recovery_attention(
    tmp_path: Path,
) -> None:
    journal = RecordingManifestJournal(tmp_path)
    take_id = _take_id()
    path = journal.path_for(take_id)
    path.parent.mkdir(parents=True, mode=0o700)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "take_id": take_id,
                "session": {
                    "recovery_status": "not_needed",
                    "password": "must never be accepted",
                },
            }
        ),
        encoding="utf-8",
    )
    os.chmod(path, JOURNAL_FILE_MODE)

    loaded = journal.load(take_id)

    assert loaded is not None
    assert loaded.trusted is False
    assert loaded.error == "journal_untrusted"
    assert loaded.evidence.recovery_status is RecoveryStatus.NEEDS_ATTENTION
    assert "unreadable" in loaded.evidence.recovery_notes[0].lower()

    path.write_text("{not valid json", encoding="utf-8")
    os.chmod(path, JOURNAL_FILE_MODE)
    malformed = journal.load(take_id)

    assert malformed is not None
    assert malformed.trusted is False
    assert malformed.evidence.recovery_status is RecoveryStatus.NEEDS_ATTENTION


def test_path_traversal_and_non_uuid_take_ids_are_rejected(tmp_path: Path) -> None:
    journal = RecordingManifestJournal(tmp_path / "takes")

    with pytest.raises(RecordingManifestJournalError):
        journal.path_for("../../outside")
    with pytest.raises(RecordingManifestJournalError):
        journal.create("not-a-uuid", _evidence())

    assert not (tmp_path / "outside.json").exists()


def test_list_pending_reports_untrusted_entries_without_returning_untrusted_names(
    tmp_path: Path,
) -> None:
    journal = RecordingManifestJournal(tmp_path / "takes")
    trusted_take_id = _take_id()
    malformed_take_id = _take_id()
    trusted_evidence = _evidence()
    journal.create(trusted_take_id, trusted_evidence)
    malformed_path = journal.create(malformed_take_id, _evidence())
    malformed_path.write_text("{bad journal", encoding="utf-8")
    os.chmod(malformed_path, JOURNAL_FILE_MODE)
    (journal.directory / "leftover-secret.tmp").write_text("not a journal")

    scan = journal.list_pending()

    results = {result.take_id: result for result in scan.journals}
    assert set(results) == {trusted_take_id, malformed_take_id}
    assert results[trusted_take_id] == JournalLoadResult(
        trusted_take_id, trusted_evidence, trusted=True
    )
    assert results[malformed_take_id].trusted is False
    assert results[malformed_take_id].error == "journal_untrusted"
    assert (
        results[malformed_take_id].evidence.recovery_status
        is RecoveryStatus.NEEDS_ATTENTION
    )
    assert scan.untrusted_entries == (JournalDirectoryIssue("journal_untrusted_name"),)
    assert journal.pending_take_ids() == (trusted_take_id,)


def test_recording_plan_fingerprint_round_trips_and_legacy_journals_load(tmp_path):
    journal = RecordingManifestJournal(tmp_path)
    fingerprint = "ab" * 32
    plan_take = str(uuid.uuid4())
    legacy_take = str(uuid.uuid4())
    with_plan = SessionEvidence(recording_plan_fingerprint=fingerprint)
    journal.create(plan_take, with_plan)
    loaded = journal.load(plan_take)
    assert loaded.evidence is not None
    assert loaded.evidence.recording_plan_fingerprint == fingerprint
    assert loaded.evidence.recovery_status is RecoveryStatus.NOT_NEEDED

    # A literal legacy schema-v1 journal (no plan field) remains readable.
    legacy = SessionEvidence()
    assert "recording_plan_fingerprint" not in legacy.to_dict()
    legacy_path = journal.path_for(legacy_take)
    legacy_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    legacy_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "take_id": legacy_take,
                "session": legacy.to_dict(),
            }
        ),
        encoding="utf-8",
    )
    os.chmod(legacy_path, JOURNAL_FILE_MODE)
    loaded_legacy = journal.load(legacy_take)
    assert loaded_legacy.evidence is not None
    assert loaded_legacy.evidence.recording_plan_fingerprint == ""
    assert loaded_legacy.plan is None


def test_schema_v2_durably_round_trips_and_updates_the_full_private_plan(
    tmp_path: Path,
) -> None:
    journal = RecordingManifestJournal(tmp_path)
    take_id = _take_id()
    plan = _plan(take_id)
    evidence = _evidence_for_plan(plan)

    path = journal.create(take_id, evidence, plan=plan)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 2
    assert payload["plan"] == plan.to_private_dict()
    assert payload["plan"]["plan_fingerprint_sha256"] == (
        evidence.recording_plan_fingerprint
    )
    assert path.stat().st_mode & 0o777 == JOURNAL_FILE_MODE
    loaded = journal.load(take_id)
    assert loaded == JournalLoadResult(
        take_id,
        evidence,
        trusted=True,
        plan=plan,
    )

    updated = SessionEvidence(
        protocol_version=evidence.protocol_version,
        started_utc=evidence.started_utc,
        ended_utc="2026-08-15T12:10:00Z",
        host=evidence.host,
        recovery_status=RecoveryStatus.RECOVERED,
        timeline=evidence.timeline,
        recording_plan_fingerprint=plan.plan_fingerprint(),
    )
    journal.update(take_id, updated, plan=plan)
    assert journal.load(take_id) == JournalLoadResult(
        take_id,
        updated,
        trusted=True,
        plan=plan,
    )


def test_writer_rejects_plan_take_or_evidence_fingerprint_mismatch(
    tmp_path: Path,
) -> None:
    journal = RecordingManifestJournal(tmp_path)
    take_id = _take_id()
    wrong_take_plan = _plan(_take_id())

    with pytest.raises(RecordingManifestJournalError, match="take identity"):
        journal.create(
            take_id,
            _evidence_for_plan(wrong_take_plan),
            plan=wrong_take_plan,
        )

    plan = _plan(take_id)
    with pytest.raises(RecordingManifestJournalError, match="fingerprint"):
        journal.create(take_id, _evidence(), plan=plan)


@pytest.mark.parametrize(
    "tamper",
    (
        "plan_fact",
        "plan_fingerprint",
        "evidence_fingerprint",
        "plan_take_id",
        "plan_unknown_field",
    ),
)
def test_v2_plan_tampering_fails_closed(tmp_path: Path, tamper: str) -> None:
    journal = RecordingManifestJournal(tmp_path)
    take_id = _take_id()
    plan = _plan(take_id)
    path = journal.create(take_id, _evidence_for_plan(plan), plan=plan)
    payload = json.loads(path.read_text(encoding="utf-8"))

    if tamper == "plan_fact":
        payload["plan"]["count_in_frames"] += 1
    elif tamper == "plan_fingerprint":
        payload["plan"]["plan_fingerprint_sha256"] = "cd" * 32
    elif tamper == "evidence_fingerprint":
        payload["session"]["recording_plan_fingerprint"] = "cd" * 32
    elif tamper == "plan_take_id":
        payload["plan"]["take_id"] = _take_id()
    else:
        payload["plan"]["unexpected"] = True
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(path, JOURNAL_FILE_MODE)

    loaded = journal.load(take_id)

    assert loaded is not None
    assert loaded.trusted is False
    assert loaded.plan is None
    assert loaded.evidence.recovery_status is RecoveryStatus.NEEDS_ATTENTION


def test_duplicate_fields_and_oversized_journals_fail_closed(tmp_path: Path) -> None:
    journal = RecordingManifestJournal(tmp_path)
    duplicate_take = _take_id()
    duplicate_path = journal.path_for(duplicate_take)
    duplicate_path.parent.mkdir(parents=True, mode=0o700)
    duplicate_path.write_text(
        (
            '{"schema_version":2,"take_id":"'
            + duplicate_take
            + '","take_id":"'
            + duplicate_take
            + '","session":{"recovery_status":"not_needed"},"plan":null}'
        ),
        encoding="utf-8",
    )
    os.chmod(duplicate_path, JOURNAL_FILE_MODE)
    duplicate = journal.load(duplicate_take)
    assert duplicate is not None
    assert duplicate.trusted is False

    oversized_take = _take_id()
    oversized_path = journal.path_for(oversized_take)
    oversized_path.write_bytes(b"{" + b"x" * MAX_JOURNAL_BYTES + b"}")
    os.chmod(oversized_path, JOURNAL_FILE_MODE)
    oversized = journal.load(oversized_take)
    assert oversized is not None
    assert oversized.trusted is False
    assert oversized.error == "journal_untrusted"


def test_writer_refuses_an_oversized_v2_plan_before_creating_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = RecordingManifestJournal(tmp_path)
    take_id = _take_id()
    plan = _plan(take_id)
    monkeypatch.setattr("core.recording_manifest_journal.MAX_JOURNAL_BYTES", 512)

    with pytest.raises(RecordingManifestJournalError, match="too large"):
        journal.create(take_id, _evidence_for_plan(plan), plan=plan)

    assert not journal.path_for(take_id).exists()
