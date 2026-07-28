"""Transaction, recovery, and evidence tests for Studio recording commit."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import uuid

import numpy as np
import pytest
import soundfile as sf

import core.project_recording_commit as commit_module
from core.song_media_catalog import SongMediaCatalog
from core.project_recording import (
    ArmedProjectTrack,
    ProjectRecorderState,
    ProjectRecordingDropout,
    ProjectRecordingResult,
    ProjectRecordingSchedule,
    ProjectRecordingSegment,
    ProjectTrackRecording,
)
from core.project_recording_commit import (
    ProjectRecordingCommitError,
    ProjectRecordingCommitRecoveryRequired,
    ProjectRecordingCommitState,
    RECORDING_COMMIT_JOURNAL_FILENAME,
    RECORDING_EVIDENCE_FILENAME,
    commit_project_recording,
    copy_recording_evidence_for_project_copy,
    inspect_project_recording_recovery,
    load_recording_evidence,
    recover_project_recording_commit,
)
from core.song_project import InputMapping, MediaImportMethod, MediaProvenance
from core.song_project_store import (
    create_project_bundle,
    load_project_bundle,
    save_project_as,
    save_project_bundle,
)
from core.song_studio_store import (
    load_song_studio_document,
    save_song_studio_document,
)
from core.studio_project import StudioDocument
from core.studio_renderer import StudioRenderer


_NAMESPACE = uuid.UUID("351658b6-dbf9-41f8-9985-a215e73df0df")


def _id(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _project(
    tmp_path: Path,
    *,
    tracks: int = 1,
    save_studio: bool = False,
) -> tuple[Path, object, StudioDocument, str, str | None]:
    bundle = tmp_path / "Song With Spaces.webjam"
    created = create_project_bundle(
        bundle,
        "Song With Spaces",
        project_id=_id("project"),
    )
    project = created.project
    for index in range(tracks):
        project = project.add_track(
            f"Track {index + 1}",
            track_id=_id(f"track:{index}"),
            input_mapping=InputMapping(
                f"studio-input-{index}",
                (index + 1,),
            ),
        )
    saved = save_project_bundle(
        bundle,
        project,
        expected_token=created.token,
    )
    studio = load_song_studio_document(bundle, saved.project)
    if save_studio:
        studio_saved = save_song_studio_document(
            bundle,
            saved.project,
            studio.document,
            expected_token=studio.token,
        )
        return (
            bundle,
            saved.project,
            studio_saved.document,
            saved.token,
            studio_saved.token,
        )
    return bundle, saved.project, studio.document, saved.token, studio.token


def _wav(
    path: Path,
    *,
    frames: int,
    channels: int = 1,
    value: float = 0.25,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = np.full((frames, channels), value, dtype=np.float32)
    sf.write(
        path,
        samples,
        48_000,
        format="WAV",
        subtype="FLOAT",
    )
    return path


def _result(
    tmp_path: Path,
    project,
    *,
    cycles: int = 1,
    latency: int = 0,
    generation: int = 7,
    dropout: bool = False,
    recovered: bool = False,
    tracks: int = 1,
) -> ProjectRecordingResult:
    schedule = ProjectRecordingSchedule(
        punch_in_frame=100,
        punch_out_frame=140,
        count_in_frames=20,
        pre_roll_frames=20,
        cycle_start_frame=80 if cycles > 1 else None,
        cycle_end_frame=160 if cycles > 1 else None,
        cycle_count=cycles,
    )
    frame_count = schedule.punch_frames * cycles
    track_results = []
    for index, project_track in enumerate(project.tracks[:tracks]):
        path = _wav(
            tmp_path / "captured secret folder" / f"track-{index}.wav",
            frames=frame_count,
            value=0.1 * (index + 1),
        )
        armed = ArmedProjectTrack(
            project_track.track_id,
            (index,),
            latency_compensation_frames=latency,
        )
        dropouts = (
            (
                ProjectRecordingDropout(
                    track_id=project_track.track_id,
                    output_start_frame=5,
                    frame_count=3,
                    channels=(0,),
                ),
            )
            if dropout
            else ()
        )
        track_results.append(
            ProjectTrackRecording(
                track=armed,
                file=path,
                frame_count=frame_count,
                dropouts=dropouts,
                overflow_frames=3 if dropout else 0,
                recovered=recovered,
            )
        )
    segments = tuple(
        ProjectRecordingSegment(
            output_start_frame=index * schedule.punch_frames,
            project_start_frame=schedule.punch_in_frame,
            frame_count=schedule.punch_frames,
            cycle_index=index,
        )
        for index in range(cycles)
    )
    return ProjectRecordingResult(
        state=(
            ProjectRecorderState.FAILED
            if recovered
            else ProjectRecorderState.COMPLETED
        ),
        generation=generation,
        schedule=schedule,
        input_frames_seen=schedule.scheduled_input_frames,
        output_frames=frame_count,
        segments=segments,
        tracks=tuple(track_results),
        output_dir=None if recovered else tmp_path / "captured secret folder",
        recovery_dir=(
            tmp_path / "captured secret folder" if recovered else None
        ),
        errors=("recovered recording",) if recovered else (),
    )


def test_commit_collects_recording_adds_cycle_lanes_and_durable_evidence(
    tmp_path: Path,
) -> None:
    bundle, project, document, project_token, studio_token = _project(tmp_path)
    capture = _result(
        tmp_path,
        project,
        cycles=3,
        latency=25,
        dropout=True,
    )
    jamulus_settings = bundle / "jamulus-settings.ini"
    jamulus_settings.write_bytes(b"server=untouched.example:22124\n")
    before_jamulus = jamulus_settings.read_bytes()
    commit_id = _id("commit:cycles")

    committed = commit_project_recording(
        bundle,
        project,
        document,
        capture,
        expected_project_token=project_token,
        expected_studio_token=studio_token,
        commit_id=commit_id,
    )

    assert committed.state is ProjectRecordingCommitState.COMMITTED
    assert committed.commit_id == commit_id
    assert len(committed.imported_media_ids) == 1
    assert len(committed.region_ids) == 3
    assert len(committed.lane_ids) == 2
    assert committed.notice == "Recording committed."
    assert not (bundle / RECORDING_COMMIT_JOURNAL_FILENAME).exists()
    assert (bundle / RECORDING_EVIDENCE_FILENAME).is_file()
    assert jamulus_settings.read_bytes() == before_jamulus

    media = committed.project.media_by_id(committed.imported_media_ids[0])
    assert media.provenance is MediaProvenance.LOCAL_RECORDING
    assert media.import_method is MediaImportMethod.RECORDING
    assert media.provenance_detail == f"recording {commit_id}"
    assert media.original_read_only is True
    assert media.path.startswith("Media/")
    assert "captured secret folder" not in json.dumps(
        committed.project.to_dict()
    )

    regions = [
        committed.document.region_for(region_id)
        for region_id in committed.region_ids
    ]
    assert [item.source_start_frame for item in regions] == [0, 40, 80]
    assert [item.timeline_start_frame for item in regions] == [75, 75, 75]
    assert [item.timeline_frame_count for item in regions] == [40, 40, 40]
    assert committed.document.take_lanes[0].region_ids == (regions[1].region_id,)
    assert committed.document.take_lanes[1].region_ids == (regions[2].region_id,)

    ledger = load_recording_evidence(bundle, committed.project)
    assert len(ledger.commits) == 1
    evidence = ledger.commits[0]
    assert evidence.commit_id == commit_id
    assert evidence.count_in_frames == 20
    assert evidence.pre_roll_frames == 20
    assert evidence.cycle_count == 3
    assert evidence.tracks[0].latency_compensation_frames == 25
    assert evidence.tracks[0].overflow_frames == 3
    assert evidence.tracks[0].dropouts[0].output_start_frame == 5
    assert evidence.tracks[0].dropouts[0].frame_count == 3
    evidence_text = (bundle / RECORDING_EVIDENCE_FILENAME).read_text()
    assert str(tmp_path) not in evidence_text
    assert "captured secret folder" not in evidence_text
    assert capture.tracks[0].file.is_file()

    reopened_project = load_project_bundle(bundle)
    reopened_studio = load_song_studio_document(
        bundle,
        reopened_project.project,
    )
    assert reopened_project.project == committed.project
    assert reopened_studio.document == committed.document
    rendered = StudioRenderer(
        committed.project,
        committed.document,
        bundle,
        source_catalog=SongMediaCatalog.load(committed.project, bundle),
    ).render_block(75, 40)
    assert rendered.shape == (40, 2)
    # Only the unlaned first pass is audible until a comp range selects an
    # alternate lane; cycle lanes must not triple the monitor signal.
    assert float(np.max(np.abs(rendered))) == pytest.approx(
        0.1,
        rel=1e-5,
    )


def test_later_punch_recording_becomes_take_lane_instead_of_doubling_base(
    tmp_path: Path,
) -> None:
    bundle, project, document, project_token, studio_token = _project(tmp_path)
    first = commit_project_recording(
        bundle,
        project,
        document,
        _result(tmp_path / "one", project),
        expected_project_token=project_token,
        expected_studio_token=studio_token,
        commit_id=_id("commit:first"),
    )

    second = commit_project_recording(
        bundle,
        first.project,
        first.document,
        _result(tmp_path / "two", first.project, generation=8),
        expected_project_token=first.project_token,
        expected_studio_token=first.studio_token,
        commit_id=_id("commit:second"),
    )

    assert len(first.lane_ids) == 0
    assert len(second.lane_ids) == 1
    second_region = second.document.region_for(second.region_ids[0])
    assert second.document.lane_for(second.lane_ids[0]).region_ids == (
        second_region.region_id,
    )
    assert second.document.lane_for(second.lane_ids[0]).name == "Take 1"


def test_multitrack_commit_keeps_track_media_and_regions_independent(
    tmp_path: Path,
) -> None:
    bundle, project, document, project_token, studio_token = _project(
        tmp_path,
        tracks=2,
    )
    capture = _result(tmp_path, project, tracks=2)

    committed = commit_project_recording(
        bundle,
        project,
        document,
        capture,
        expected_project_token=project_token,
        expected_studio_token=studio_token,
        commit_id=_id("commit:multitrack"),
    )

    assert len(committed.imported_media_ids) == 2
    assert len(committed.region_ids) == 2
    assert len(set(committed.imported_media_ids)) == 2
    assert {
        committed.document.region_for(region_id).track_id
        for region_id in committed.region_ids
    } == {item.track_id for item in project.tracks}


def test_project_save_as_can_copy_recording_evidence_with_preserved_lineage(
    tmp_path: Path,
) -> None:
    bundle, project, document, project_token, studio_token = _project(tmp_path)
    committed = commit_project_recording(
        bundle,
        project,
        document,
        _result(tmp_path, project),
        expected_project_token=project_token,
        expected_studio_token=studio_token,
        commit_id=_id("commit:save-as"),
    )
    destination = tmp_path / "Song Copy.webjam"
    copied_project = save_project_as(
        bundle,
        destination,
        committed.project,
        expected_token=committed.project_token,
        new_project_id=_id("project:copy"),
    )

    copied_ledger = copy_recording_evidence_for_project_copy(
        bundle,
        committed.project,
        destination,
        copied_project.project,
        expected_source_token=committed.project_token,
        expected_destination_token=copied_project.token,
    )

    assert copied_ledger.project_id == copied_project.project.project_id
    assert copied_ledger.commits == (
        load_recording_evidence(bundle, committed.project).commits
    )
    assert load_recording_evidence(
        destination,
        copied_project.project,
    ) == copied_ledger
    assert copied_ledger.commits[0].tracks[0].media_id in {
        item.media_id for item in copied_project.project.media
    }
    # Retrying the post-Save-As hook is exact and does not duplicate evidence.
    assert copy_recording_evidence_for_project_copy(
        bundle,
        committed.project,
        destination,
        copied_project.project,
        expected_source_token=committed.project_token,
        expected_destination_token=copied_project.token,
    ) == copied_ledger


def test_import_failure_rolls_back_exact_new_media_and_clears_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, project, document, project_token, studio_token = _project(
        tmp_path,
        tracks=2,
    )
    capture = _result(tmp_path, project, tracks=2)
    real_import = commit_module.import_project_media
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("/private/secret disk failure")
        return real_import(*args, **kwargs)

    monkeypatch.setattr(commit_module, "import_project_media", fail_second)
    with pytest.raises(ProjectRecordingCommitError) as caught:
        commit_project_recording(
            bundle,
            project,
            document,
            capture,
            expected_project_token=project_token,
            expected_studio_token=studio_token,
            commit_id=_id("commit:rollback"),
        )

    assert str(tmp_path) not in str(caught.value)
    assert "secret" not in str(caught.value)
    assert not (bundle / RECORDING_COMMIT_JOURNAL_FILENAME).exists()
    assert not tuple((bundle / "Media").iterdir())
    assert load_project_bundle(bundle).project == project
    assert load_song_studio_document(bundle, project).document == document


def test_studio_save_failure_preserves_recovery_and_resume_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, project, document, project_token, studio_token = _project(tmp_path)
    capture = _result(tmp_path, project, dropout=True)
    commit_id = _id("commit:recover")
    real_save_studio = commit_module.save_song_studio_document

    def fail_studio(*_args, **_kwargs):
        raise OSError("/private/secret Studio failure")

    monkeypatch.setattr(
        commit_module,
        "save_song_studio_document",
        fail_studio,
    )
    with pytest.raises(ProjectRecordingCommitRecoveryRequired) as caught:
        commit_project_recording(
            bundle,
            project,
            document,
            capture,
            expected_project_token=project_token,
            expected_studio_token=studio_token,
            commit_id=commit_id,
        )
    assert caught.value.commit_id == commit_id
    assert caught.value.recovery_available is True
    assert str(tmp_path) not in str(caught.value)
    assert (bundle / RECORDING_COMMIT_JOURNAL_FILENAME).is_file()
    candidate = inspect_project_recording_recovery(bundle)
    assert candidate is not None
    assert candidate.can_resume is True
    assert candidate.commit_id == commit_id

    monkeypatch.setattr(
        commit_module,
        "save_song_studio_document",
        real_save_studio,
    )
    recovered = recover_project_recording_commit(bundle)
    assert recovered.state is ProjectRecordingCommitState.RECOVERED
    assert recovered.commit_id == commit_id
    assert recovered.evidence is not None
    assert not (bundle / RECORDING_COMMIT_JOURNAL_FILENAME).exists()
    assert len(load_recording_evidence(bundle, recovered.project).commits) == 1
    with pytest.raises(ProjectRecordingCommitError, match="No Studio"):
        recover_project_recording_commit(bundle)


def test_prepared_crash_recovery_removes_only_exact_orphan_and_rolls_back(
    tmp_path: Path,
) -> None:
    bundle, project, _document, project_token, studio_token = _project(tmp_path)
    capture = _result(tmp_path, project)
    commit_id = _id("commit:prepared-crash")
    recordings, _skipped = commit_module._successful_recordings(
        capture,
        allow_recovered=False,
    )
    plans = commit_module._build_source_plans(
        capture,
        recordings,
        commit_id,
    )
    prepared = commit_module._journal_payload(
        commit_id=commit_id,
        stage="prepared",
        project_id=project.project_id,
        expected_project_token=project_token,
        expected_studio_token=studio_token,
        plans=plans,
    )
    commit_module._write_journal(bundle, prepared)
    orphan = bundle / plans[0].relative_path
    shutil.copyfile(capture.tracks[0].file, orphan)

    candidate = inspect_project_recording_recovery(bundle)
    assert candidate is not None
    assert candidate.stage == "prepared"
    assert candidate.can_resume is False
    recovered = recover_project_recording_commit(bundle)

    assert recovered.state is ProjectRecordingCommitState.ROLLED_BACK
    assert recovered.commit_id == commit_id
    assert recovered.evidence is None
    assert recovered.studio_token is None
    assert not orphan.exists()
    assert not (bundle / RECORDING_COMMIT_JOURNAL_FILENAME).exists()
    assert load_project_bundle(bundle).project == project


def test_explicit_recovered_partial_import_is_required_and_retains_flag(
    tmp_path: Path,
) -> None:
    bundle, project, document, project_token, studio_token = _project(tmp_path)
    capture = _result(tmp_path, project, recovered=True)

    with pytest.raises(ProjectRecordingCommitError, match="explicitly"):
        commit_project_recording(
            bundle,
            project,
            document,
            capture,
            expected_project_token=project_token,
            expected_studio_token=studio_token,
        )

    committed = commit_project_recording(
        bundle,
        project,
        document,
        capture,
        expected_project_token=project_token,
        expected_studio_token=studio_token,
        commit_id=_id("commit:partial"),
        allow_recovered=True,
    )
    assert committed.evidence is not None
    assert committed.evidence.tracks[0].recovered is True
    assert committed.notice == "Recovered partial recording committed."


def test_wrong_track_channel_token_and_symlink_sources_fail_without_mutation(
    tmp_path: Path,
) -> None:
    bundle, project, document, project_token, studio_token = _project(tmp_path)
    capture = _result(tmp_path, project)
    wrong_track = ProjectTrackRecording(
        track=ArmedProjectTrack(_id("missing-track"), (0,)),
        file=capture.tracks[0].file,
        frame_count=capture.tracks[0].frame_count,
    )
    wrong_result = ProjectRecordingResult(
        state=capture.state,
        generation=capture.generation,
        schedule=capture.schedule,
        input_frames_seen=capture.input_frames_seen,
        output_frames=capture.output_frames,
        segments=capture.segments,
        tracks=(wrong_track,),
        output_dir=capture.output_dir,
    )
    with pytest.raises(ProjectRecordingCommitError, match="no longer"):
        commit_project_recording(
            bundle,
            project,
            document,
            wrong_result,
            expected_project_token=project_token,
            expected_studio_token=studio_token,
        )
    assert not (bundle / RECORDING_COMMIT_JOURNAL_FILENAME).exists()

    with pytest.raises(ProjectRecordingCommitError, match="changed"):
        commit_project_recording(
            bundle,
            project,
            document,
            capture,
            expected_project_token="0" * 64,
            expected_studio_token=studio_token,
        )
    link = tmp_path / "secret-link.wav"
    try:
        os.symlink(capture.tracks[0].file, link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable")
    linked_track = ProjectTrackRecording(
        track=capture.tracks[0].track,
        file=link,
        frame_count=capture.tracks[0].frame_count,
    )
    linked = ProjectRecordingResult(
        state=capture.state,
        generation=capture.generation,
        schedule=capture.schedule,
        input_frames_seen=capture.input_frames_seen,
        output_frames=capture.output_frames,
        segments=capture.segments,
        tracks=(linked_track,),
        output_dir=capture.output_dir,
    )
    with pytest.raises(ProjectRecordingCommitError, match="verified"):
        commit_project_recording(
            bundle,
            project,
            document,
            linked,
            expected_project_token=project_token,
            expected_studio_token=studio_token,
        )
    assert not tuple((bundle / "Media").iterdir())


def test_evidence_and_journal_redirects_are_rejected(
    tmp_path: Path,
) -> None:
    bundle, project, document, project_token, studio_token = _project(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("mine", encoding="utf-8")
    evidence = bundle / RECORDING_EVIDENCE_FILENAME
    try:
        os.symlink(outside, evidence)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(ProjectRecordingCommitError, match="recording evidence"):
        load_recording_evidence(bundle, project)
    with pytest.raises(ProjectRecordingCommitError):
        commit_project_recording(
            bundle,
            project,
            document,
            _result(tmp_path, project),
            expected_project_token=project_token,
            expected_studio_token=studio_token,
        )
    assert outside.read_text(encoding="utf-8") == "mine"
