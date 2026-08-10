from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

import pytest

from core.recording_manifest_journal import (
    JOURNAL_FILE_MODE,
    JournalDirectoryIssue,
    JournalLoadResult,
    RecordingManifestJournal,
    RecordingManifestJournalError,
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


def test_create_load_update_and_remove_private_typed_journal(tmp_path: Path) -> None:
    journal = RecordingManifestJournal(tmp_path / "takes")
    take_id = _take_id()
    evidence = _evidence()

    path = journal.create(take_id, evidence)

    assert path == journal.path_for(take_id)
    assert path.parent == tmp_path / "takes" / ".webjam-recording-evidence"
    assert path.stat().st_mode & 0o777 == JOURNAL_FILE_MODE
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "take_id", "session"}
    assert payload["take_id"] == take_id
    assert payload["session"] == evidence.to_dict()
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
    from core.recording_manifest_journal import RecordingManifestJournal
    from core.take_project import RecoveryStatus, SessionEvidence

    import uuid

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

    # A legacy journal (no plan field) still loads with an empty binding.
    legacy = SessionEvidence()
    assert "recording_plan_fingerprint" not in legacy.to_dict()
    journal.create(legacy_take, legacy)
    loaded_legacy = journal.load(legacy_take)
    assert loaded_legacy.evidence is not None
    assert loaded_legacy.evidence.recording_plan_fingerprint == ""
