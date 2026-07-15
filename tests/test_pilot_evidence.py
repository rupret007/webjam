"""Focused regression coverage for the private, local-only pilot ledger."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import stat
import uuid
from pathlib import Path
from unittest.mock import ANY, patch

import pytest

import core.pilot_evidence as pilot_evidence
from core.file_io import atomic_write_text
from core.pilot_evidence import (
    EvidenceLimitation,
    EvidenceOutcome,
    EvidenceReference,
    PilotEvidenceError,
    PilotObservationClass,
    PilotRole,
    PilotSessionState,
    build_sanitized_pilot_report,
    create_pilot_ledger,
    list_pilot_ledgers,
    load_pilot_ledger,
    pilot_ledger_path,
    resume_pilot_ledger,
    save_pilot_ledger,
)


BUILD_COMMIT = "a" * 40
ARTIFACT_IDENTITY = "sha256:" + "b" * 64
START = datetime(2026, 7, 14, 1, 2, 3, tzinfo=timezone.utc)


def _ledger():
    return create_pilot_ledger(
        app_version="0.15.0",
        build_commit=BUILD_COMMIT,
        artifact_identity=ARTIFACT_IDENTITY,
        role=PilotRole.HOST,
        now=START,
    )


def _failed_band_check(ledger, *, at: datetime = START):
    return ledger.record_observation(
        PilotObservationClass.BAND_CHECK,
        EvidenceOutcome.FAILED,
        state_before=PilotSessionState.BAND_CHECK_IN_PROGRESS,
        state_after=PilotSessionState.BAND_CHECK_REQUIRED,
        evidence_reference=EvidenceReference.BAND_CHECK_RESULT,
        limitations=(EvidenceLimitation.HARDWARE_NOT_EXERCISED,),
        occurred_at=at,
    )


def test_create_save_resume_and_append_preserves_failure(tmp_path: Path):
    ledger = _failed_band_check(_ledger(), at=START + timedelta(seconds=1))

    with patch(
        "core.pilot_evidence.atomic_write_text", wraps=atomic_write_text
    ) as atomic_write:
        path = save_pilot_ledger(tmp_path, ledger)

    assert path == pilot_ledger_path(tmp_path, ledger.run_id)
    assert uuid.UUID(ledger.run_id).version == 4
    atomic_write.assert_called_once_with(path, ANY, mode=0o600)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.parent.name == ".webjam-pilot-evidence"
    assert str(tmp_path) not in json.dumps(ledger.to_dict())

    restarted = resume_pilot_ledger(tmp_path, ledger.run_id)
    assert restarted == ledger
    recovered = restarted.record_observation(
        PilotObservationClass.BAND_CHECK,
        EvidenceOutcome.VERIFIED,
        state_before=PilotSessionState.BAND_CHECK_REQUIRED,
        state_after=PilotSessionState.READY_TO_START,
        evidence_reference=EvidenceReference.BAND_CHECK_RESULT,
        occurred_at=START + timedelta(seconds=2),
    )
    save_pilot_ledger(tmp_path, recovered)

    loaded = load_pilot_ledger(tmp_path, ledger.run_id)
    assert [event.result for event in loaded.events] == [
        EvidenceOutcome.FAILED,
        EvidenceOutcome.VERIFIED,
    ]
    assert set(loaded.events[0].to_dict()) == {
        "event_id",
        "sequence",
        "run_id",
        "app_version",
        "build_commit",
        "artifact_identity",
        "role",
        "timestamp_utc",
        "state_before",
        "state_after",
        "observation_class",
        "result",
        "evidence_reference",
        "limitations",
        "previous_event_sha256",
        "event_sha256",
    }
    assert loaded.events[1].previous_event_sha256 == loaded.events[0].event_sha256
    report = build_sanitized_pilot_report(loaded)
    assert report["summary"]["ever_failed"] is True
    assert report["summary"]["outcome_counts"] == {
        "VERIFIED": 1,
        "FAILED": 1,
        "BLOCKED": 0,
        "NOT RUN": 0,
        "INDETERMINATE": 0,
        "NOT AVAILABLE": 0,
    }


def test_listed_runs_are_verified_and_newest_first(tmp_path: Path):
    older = _failed_band_check(_ledger(), at=START + timedelta(seconds=1))
    newer = _ledger().record_observation(
        PilotObservationClass.APP_LAUNCHED,
        EvidenceOutcome.VERIFIED,
        state_before=PilotSessionState.IDLE,
        state_after=PilotSessionState.CONFIRMING_IDENTITY_AND_SOUND,
        evidence_reference=EvidenceReference.PACKAGE_METADATA,
        occurred_at=START + timedelta(seconds=2),
    )
    save_pilot_ledger(tmp_path, older)
    save_pilot_ledger(tmp_path, newer)

    assert [ledger.run_id for ledger in list_pilot_ledgers(tmp_path)] == [
        newer.run_id,
        older.run_id,
    ]


def test_human_outcomes_require_a_deliberate_human_api():
    ledger = _ledger()

    with pytest.raises(PilotEvidenceError, match="Human outcomes require"):
        ledger.record_observation(
            PilotObservationClass.HUMAN_HOST_HEARD_BANDMATE,
            EvidenceOutcome.VERIFIED,
            state_before=PilotSessionState.LIVE,
            state_after=PilotSessionState.LIVE,
            evidence_reference=EvidenceReference.HUMAN_CONFIRMATION,
        )

    observed = ledger.record_human_observation(
        PilotObservationClass.HUMAN_HOST_HEARD_BANDMATE,
        EvidenceOutcome.NOT_RUN,
        state_before=PilotSessionState.CONNECTED,
        state_after=PilotSessionState.CONNECTED,
        limitations=(EvidenceLimitation.SECOND_MAC_UNAVAILABLE,),
        occurred_at=START + timedelta(seconds=1),
    )

    event = observed.events[0]
    assert event.evidence_reference is EvidenceReference.HUMAN_CONFIRMATION
    assert event.result is EvidenceOutcome.NOT_RUN
    assert event.limitations == (EvidenceLimitation.SECOND_MAC_UNAVAILABLE,)
    with pytest.raises(PilotEvidenceError, match="Only explicitly human"):
        ledger.record_human_observation(
            PilotObservationClass.BAND_CHECK,
            EvidenceOutcome.VERIFIED,
            state_before=PilotSessionState.IDLE,
            state_after=PilotSessionState.IDLE,
        )


def test_allowlist_rejects_paths_invites_secrets_raw_devices_and_unknown_fields(
    tmp_path: Path,
):
    exact_candidate = create_pilot_ledger(
        app_version="0.15.0",
        build_commit=BUILD_COMMIT,
        artifact_identity="WebJam-v0.15.0-TEST-NIGHT-macos-arm64.zip",
        role=PilotRole.HOST,
    )
    assert (
        exact_candidate.artifact_identity == "webjam-v0.15.0-test-night-macos-arm64.zip"
    )

    with pytest.raises(PilotEvidenceError):
        create_pilot_ledger(
            app_version="0.15.0 /Users/jeff/private",
            build_commit=BUILD_COMMIT,
            artifact_identity=ARTIFACT_IDENTITY,
            role=PilotRole.HOST,
        )
    with pytest.raises(PilotEvidenceError):
        create_pilot_ledger(
            app_version="0.15.0",
            build_commit="invite-secret",
            artifact_identity=ARTIFACT_IDENTITY,
            role=PilotRole.HOST,
        )
    with pytest.raises(PilotEvidenceError):
        create_pilot_ledger(
            app_version="0.15.0",
            build_commit=BUILD_COMMIT,
            artifact_identity="webjam://join?token=private",
            role=PilotRole.HOST,
        )

    ledger = _ledger()
    with pytest.raises(PilotEvidenceError):
        ledger.record_observation(
            "device_name=Focusrite Scarlett",
            EvidenceOutcome.VERIFIED,
            state_before=PilotSessionState.IDLE,
            state_after=PilotSessionState.IDLE,
            evidence_reference=EvidenceReference.NONE,
        )
    with pytest.raises(PilotEvidenceError):
        ledger.record_observation(
            PilotObservationClass.BAND_CHECK,
            EvidenceOutcome.VERIFIED,
            state_before=PilotSessionState.IDLE,
            state_after=PilotSessionState.IDLE,
            evidence_reference="/Users/jeff/evidence.json",
        )
    with pytest.raises(PilotEvidenceError):
        ledger.record_observation(
            PilotObservationClass.BAND_CHECK,
            EvidenceOutcome.VERIFIED,
            state_before=PilotSessionState.IDLE,
            state_after=PilotSessionState.IDLE,
            evidence_reference=EvidenceReference.NONE,
            limitations="invite-token=private",
        )

    path = save_pilot_ledger(tmp_path, ledger)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["device_name"] = "Focusrite Scarlett"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(PilotEvidenceError, match="unsupported schema"):
        load_pilot_ledger(tmp_path, ledger.run_id)


def test_append_only_checks_reject_erasure_rewrite_and_stale_parallel_save(
    tmp_path: Path,
):
    initial = _failed_band_check(_ledger(), at=START + timedelta(seconds=1))
    save_pilot_ledger(tmp_path, initial)

    erased = replace(
        initial,
        updated_at_utc=initial.created_at_utc,
        events=(),
        event_chain_head_sha256="0" * 64,
    )
    with pytest.raises(PilotEvidenceError, match="cannot be removed"):
        save_pilot_ledger(tmp_path, erased)

    first_retry = initial.record_observation(
        PilotObservationClass.BAND_CHECK,
        EvidenceOutcome.VERIFIED,
        state_before=PilotSessionState.BAND_CHECK_REQUIRED,
        state_after=PilotSessionState.READY_TO_START,
        evidence_reference=EvidenceReference.BAND_CHECK_RESULT,
        occurred_at=START + timedelta(seconds=2),
    )
    stale_retry = initial.record_observation(
        PilotObservationClass.BAND_CHECK,
        EvidenceOutcome.BLOCKED,
        state_before=PilotSessionState.BAND_CHECK_REQUIRED,
        state_after=PilotSessionState.BLOCKED,
        evidence_reference=EvidenceReference.BAND_CHECK_RESULT,
        limitations=(EvidenceLimitation.AUDIO_INTERFACE_UNAVAILABLE,),
        occurred_at=START + timedelta(seconds=2),
    )
    save_pilot_ledger(tmp_path, first_retry)
    with pytest.raises(PilotEvidenceError, match="append without rewriting"):
        save_pilot_ledger(tmp_path, stale_retry)


def test_loading_rejects_permission_leaks_unknown_fields_and_chain_tampering(
    tmp_path: Path,
):
    ledger = _failed_band_check(_ledger(), at=START + timedelta(seconds=1))
    path = save_pilot_ledger(tmp_path, ledger)

    path.chmod(0o644)
    with pytest.raises(PilotEvidenceError, match="permissions"):
        load_pilot_ledger(tmp_path, ledger.run_id)

    path.chmod(0o600)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["events"][0]["result"] = EvidenceOutcome.VERIFIED.value
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(PilotEvidenceError, match="integrity"):
        load_pilot_ledger(tmp_path, ledger.run_id)


def test_report_is_sanitized_and_carries_all_required_safe_observation_fields():
    ledger = _ledger().record_observation(
        PilotObservationClass.PACKAGE_IDENTITY,
        EvidenceOutcome.NOT_AVAILABLE,
        state_before=PilotSessionState.IDLE,
        state_after=PilotSessionState.IDLE,
        evidence_reference=EvidenceReference.PACKAGE_METADATA,
        limitations=(EvidenceLimitation.PARTIAL_EVIDENCE,),
        occurred_at=START + timedelta(seconds=1),
    )

    report = ledger.sanitized_report()
    encoded = json.dumps(report, sort_keys=True)
    event = report["events"][0]
    assert report["privacy"] == {
        "storage": "local_only",
        "collection": "allowlist_only",
        "audio_included": False,
        "invites_included": False,
        "credentials_included": False,
        "network_addresses_included": False,
        "device_identifiers_included": False,
        "paths_included": False,
        "names_or_notes_included": False,
    }
    assert set(event) == {
        "sequence",
        "timestamp_utc",
        "state_before",
        "state_after",
        "observation_class",
        "result",
        "evidence_reference",
        "limitations",
    }
    for forbidden in (
        "/Users/",
        "webjam://",
        "token=",
        "192.168.",
        "Focusrite",
        "private note",
    ):
        assert forbidden not in encoded
    summary = ledger.render_sanitized_summary()
    assert "Earlier failures preserved: no" in summary
    assert "No audio, invites, credentials" in summary


def test_record_cap_fails_without_dropping_existing_events(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(pilot_evidence, "MAX_PILOT_EVENTS", 1)
    ledger = _ledger().record_observation(
        PilotObservationClass.APP_LAUNCHED,
        EvidenceOutcome.VERIFIED,
        state_before=PilotSessionState.IDLE,
        state_after=PilotSessionState.CONFIRMING_IDENTITY_AND_SOUND,
        evidence_reference=EvidenceReference.PACKAGE_METADATA,
        occurred_at=START + timedelta(seconds=1),
    )

    with pytest.raises(PilotEvidenceError, match="record is full"):
        ledger.record_observation(
            PilotObservationClass.BAND_CHECK,
            EvidenceOutcome.NOT_RUN,
            state_before=PilotSessionState.BAND_CHECK_REQUIRED,
            state_after=PilotSessionState.BAND_CHECK_REQUIRED,
            evidence_reference=EvidenceReference.NONE,
            occurred_at=START + timedelta(seconds=2),
        )
    assert len(ledger.events) == 1
