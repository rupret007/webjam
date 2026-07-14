from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from core.take_project import (
    AlignmentAnchor,
    AlignmentState,
    CaptureDevice,
    GapInterval,
    MediaSegment,
    MediaStatus,
    Participant,
    ProjectMarker,
    ProjectStatus,
    ProjectTrack,
    RecoveryStatus,
    SessionEvidence,
    SessionTimelineEvent,
    SourceQuality,
    SourceType,
    TakeProject,
    TakeProjectError,
    HostIdentity,
    load_take_project,
    migrate_v1_manifest,
    new_project_id,
    write_take_project,
)


def _project() -> TakeProject:
    participant_id = new_project_id()
    device = CaptureDevice(
        device_id="coreaudio:ssl-2-plus",
        display_name="SSL 2+",
        backend="Core Audio",
        sample_rate=48000,
        channel_indices=(0,),
        channel_labels=("Instrument",),
    )
    gap = GapInterval(960, 480, "queue_overflow", (0,))
    segment = MediaSegment(
        segment_id=new_project_id(),
        path="media/guitar-01.wav",
        project_start_frame=2400,
        frame_count=48000,
        sample_rate=48000,
        channels=1,
        sample_format="PCM_24",
        media_status=MediaStatus.AVAILABLE,
        sha256=hashlib.sha256(b"source stays immutable").hexdigest(),
        device_id=device.device_id,
        gaps=(gap,),
    )
    alignment = AlignmentState(
        automatic_offset_s=-0.125,
        manual_nudge_s=0.002,
        drift_ppm=12.5,
        confidence=0.91,
        method="multi-anchor-v1",
        residual_ms=1.5,
        anchors=(AlignmentAnchor(0.5, 0.375, 1.0),),
    )
    track = ProjectTrack(
        track_id=new_project_id(),
        source_id=new_project_id(),
        participant_id=participant_id,
        name="Jeff — Guitar",
        instrument="Guitar",
        source_type=SourceType.LOCAL_ISOLATED,
        quality=SourceQuality.VERIFIED_ISOLATED,
        media_status=MediaStatus.AVAILABLE,
        order=0,
        segments=(segment,),
        alignment=alignment,
    )
    return TakeProject(
        session_id=new_project_id(),
        take_id=new_project_id(),
        session_title="Band Rehearsal",
        take_name="Take 01",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=48000,
        participants=(Participant(participant_id, "Jeff", "Guitar"),),
        tracks=(track,),
        app_version="1.0.0-rc1",
        created_utc="2026-07-13T12:00:00Z",
        tempo_bpm=110.0,
        time_signature_numerator=4,
        time_signature_denominator=4,
        devices=(device,),
        markers=(ProjectMarker(new_project_id(), 8.0, "Verse"),),
    )


def test_schema_v2_round_trip_preserves_identity_segments_gaps_and_alignment(tmp_path):
    original = _project()

    manifest = write_take_project(tmp_path, original)
    loaded = load_take_project(tmp_path)

    assert manifest.stat().st_mode & 0o777 == 0o600
    assert loaded == original
    assert loaded.tracks[0].segments[0].gaps[0].start_frame == 960
    assert loaded.tracks[0].alignment.effective_offset_s == pytest.approx(-0.123)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["tracks"][0]["filename"] == "media/guitar-01.wav"
    assert data["tracks"][0]["offset_s"] == pytest.approx(-0.123)
    assert "session" not in data


def test_session_evidence_round_trip_preserves_recording_provenance(tmp_path):
    original = _project()
    host = original.participants[0]
    evidence = SessionEvidence(
        protocol_version="jamulus-3.12.2 / webjam-v2",
        started_utc="2026-07-14T01:02:03Z",
        ended_utc="2026-07-14T01:07:45Z",
        host=HostIdentity(host.participant_id, host.display_name),
        recovery_status=RecoveryStatus.RECOVERED,
        recovery_notes=("Recorder reconnect completed.",),
        timeline=(
            SessionTimelineEvent(
                "reconnecting",
                occurred_utc="2026-07-14T01:04:00Z",
                detail="The music engine retried once.",
            ),
            SessionTimelineEvent(
                "media_gap",
                at_s=12.5,
                participant_id=host.participant_id,
                detail="queue_overflow",
            ),
        ),
    )
    project = replace(original, session_evidence=evidence)

    write_take_project(tmp_path, project)
    loaded = load_take_project(tmp_path)
    payload = json.loads((tmp_path / "webjam-take.json").read_text())

    assert loaded.session_evidence == evidence
    assert payload["session"]["host"]["participant_id"] == host.participant_id
    assert payload["session"]["timeline"][1]["event"] == "media_gap"
    assert loaded.effective_status is ProjectStatus.COMPLETE


def test_session_evidence_requires_known_host_and_fails_closed_for_unknown_status():
    original = _project()
    with pytest.raises(TakeProjectError, match="unknown participant"):
        replace(
            original,
            session_evidence=SessionEvidence(
                host=HostIdentity(new_project_id(), "Unknown Host")
            ),
        )

    payload = original.to_dict()
    payload["session"] = {"recovery_status": "not-a-real-status"}
    loaded = TakeProject.from_dict(payload)

    assert loaded.session_evidence.recovery_status is RecoveryStatus.NEEDS_ATTENTION
    assert loaded.effective_status is ProjectStatus.NEEDS_ATTENTION
    assert "unreadable" in loaded.session_evidence.recovery_notes[0].lower()


def test_session_evidence_redacts_invites_addresses_and_credentials():
    host_id = new_project_id()
    evidence = SessionEvidence(
        protocol_version="jamulus-3.12.2",
        host=HostIdentity(
            host_id,
            "webjam://join?token=private-token at 192.168.10.9",
        ),
        recovery_notes=(
            "Retry webjam://join?token=private-token at 192.168.10.9 ",
        ),
        timeline=(
            SessionTimelineEvent(
                "reconnecting",
                detail="Authorization: Bearer private-secret",
            ),
        ),
    )
    payload = evidence.to_dict()
    rendered = json.dumps(payload)

    assert "webjam://" not in rendered
    assert "192.168.10.9" not in rendered
    assert "private-token" not in rendered
    assert "private-secret" not in rendered
    assert payload["host"]["participant_id"] == host_id
    assert payload["host"]["display_name"] == "Private host"


def test_manual_nudge_is_separate_and_restore_is_idempotent():
    automatic = AlignmentState(
        automatic_offset_s=-0.2,
        manual_nudge_s=0.015,
        confidence=0.8,
        method="fixture",
    )

    restored = automatic.restore_automatic()

    assert automatic.automatic_offset_s == -0.2
    assert automatic.effective_offset_s == pytest.approx(-0.185)
    assert restored.automatic_offset_s == -0.2
    assert restored.manual_nudge_s == 0.0
    assert restored.restore_automatic() == restored


@pytest.mark.parametrize(
    "unsafe",
    ["../outside.wav", "/tmp/outside.wav", "folder/../../outside.wav", "C:\\audio.wav"],
)
def test_segment_paths_cannot_escape_take_directory(unsafe):
    with pytest.raises(TakeProjectError, match="path"):
        MediaSegment(
            new_project_id(), unsafe, 0, 100, 48000, 1, "PCM_24"
        )


def test_gap_must_fit_segment_and_channel_layout():
    with pytest.raises(TakeProjectError, match="beyond"):
        MediaSegment(
            new_project_id(),
            "audio.wav",
            0,
            100,
            48000,
            1,
            "PCM_24",
            gaps=(GapInterval(80, 40, "dropout", (0,)),),
        )
    with pytest.raises(TakeProjectError, match="channel"):
        MediaSegment(
            new_project_id(),
            "audio.wav",
            0,
            100,
            48000,
            1,
            "PCM_24",
            gaps=(GapInterval(10, 5, "dropout", (1,)),),
        )


def test_duplicate_ids_and_unknown_participant_are_rejected():
    project = _project()
    track = project.tracks[0]
    duplicate = ProjectTrack(
        track_id=track.track_id,
        source_id=new_project_id(),
        participant_id=track.participant_id,
        name="Duplicate",
        instrument="",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=1,
        segments=(MediaSegment(
            new_project_id(), "other.wav", 0, 10, 48000, 1, "PCM_24"
        ),),
    )
    with pytest.raises(TakeProjectError, match="duplicate track IDs"):
        TakeProject(
            session_id=project.session_id,
            take_id=project.take_id,
            session_title="",
            take_name="Take",
            status=ProjectStatus.COMPLETE,
            project_sample_rate=48000,
            participants=project.participants,
            tracks=(track, duplicate),
            devices=project.devices,
        )

    orphan = ProjectTrack(
        track_id=new_project_id(),
        source_id=new_project_id(),
        participant_id=new_project_id(),
        name="Orphan",
        instrument="",
        source_type=SourceType.JAMULUS_SERVER,
        quality=SourceQuality.NETWORK_TRACK,
        media_status=MediaStatus.AVAILABLE,
        order=0,
        segments=(MediaSegment(
            new_project_id(), "orphan.wav", 0, 10, 48000, 1, "PCM_24"
        ),),
    )
    with pytest.raises(TakeProjectError, match="unknown participant"):
        TakeProject(
            session_id=new_project_id(),
            take_id=new_project_id(),
            session_title="",
            take_name="Take",
            status=ProjectStatus.COMPLETE,
            project_sample_rate=48000,
            participants=(),
            tracks=(orphan,),
        )


def test_blocking_media_truth_downgrades_serialized_complete_state():
    project = _project()
    source = project.tracks[0]
    missing_segment = MediaSegment(
        segment_id=source.segments[0].segment_id,
        path=source.segments[0].path,
        project_start_frame=0,
        frame_count=48000,
        sample_rate=48000,
        channels=1,
        sample_format="PCM_24",
        media_status=MediaStatus.MISSING,
        device_id=source.segments[0].device_id,
    )
    missing_track = ProjectTrack(
        track_id=source.track_id,
        source_id=source.source_id,
        participant_id=source.participant_id,
        name=source.name,
        instrument=source.instrument,
        source_type=source.source_type,
        quality=source.quality,
        media_status=MediaStatus.MISSING,
        order=0,
        segments=(missing_segment,),
    )
    changed = TakeProject(
        session_id=project.session_id,
        take_id=project.take_id,
        session_title=project.session_title,
        take_name=project.take_name,
        status=ProjectStatus.COMPLETE,
        project_sample_rate=48000,
        participants=project.participants,
        tracks=(missing_track,),
        devices=project.devices,
    )

    assert changed.status is ProjectStatus.COMPLETE
    assert changed.effective_status is ProjectStatus.NEEDS_ATTENTION
    assert changed.to_dict()["status"] == "needs_attention"


def test_v1_migration_is_read_only_stable_and_keeps_missing_inventory(tmp_path):
    existing = tmp_path / "Jeff-0-1.wav"
    existing.write_bytes(b"not opened by migration")
    legacy = {
        "schema_version": 1,
        "app_version": "0.9.0",
        "session_title": "Band Rehearsal",
        "status": "complete",
        "local_capture": {
            "started_utc": "2026-07-13T12:00:00Z",
            "alignment_confidence": 0.8,
            "alignment_method": "legacy",
        },
        "tracks": [
            {
                "filename": existing.name,
                "name": "Jeff",
                "source": "jamulus_server",
                "sample_rate": 48000,
                "duration_s": 1.0,
            },
            {
                "filename": "Guest-1-1.wav",
                "name": "Jeff",
                "source": "jamulus_server",
                "sample_rate": 48000,
                "duration_s": 1.0,
            },
        ],
    }
    before = hashlib.sha256(existing.read_bytes()).hexdigest()

    first = migrate_v1_manifest(tmp_path, legacy)
    second = migrate_v1_manifest(tmp_path, legacy)

    assert first == second
    assert first.session_id == second.session_id
    assert first.take_id == second.take_id
    assert len({item.participant_id for item in first.participants}) == 2
    assert first.tracks[0].media_status is MediaStatus.AVAILABLE
    assert first.tracks[1].media_status is MediaStatus.MISSING
    assert first.effective_status is ProjectStatus.NEEDS_ATTENTION
    assert any("not proof of identity" in item for item in first.warnings)
    assert hashlib.sha256(existing.read_bytes()).hexdigest() == before
    assert not (tmp_path / "webjam-take-v2.json").exists()


def test_load_v1_does_not_rewrite_manifest(tmp_path):
    manifest = tmp_path / "webjam-take.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "status": "complete",
        "tracks": [{
            "filename": "missing.wav",
            "name": "Guest",
            "sample_rate": 48000,
            "duration_s": 2.0,
        }],
    }), encoding="utf-8")
    original = manifest.read_bytes()

    loaded = load_take_project(tmp_path)

    assert loaded.schema_version == 2
    assert manifest.read_bytes() == original
