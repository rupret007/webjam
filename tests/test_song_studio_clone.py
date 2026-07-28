"""Save-As identity, dependency-remap, and transaction safety coverage."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

import core.song_studio_clone as clone_module
from core.song_project import (
    MediaImportMethod,
    MediaProvenance,
    SongMedia,
    SongProject,
    SongTrack,
)
from core.song_project_store import (
    PROJECT_AUTOSAVE_FILENAME,
    PROJECT_BACKUP_FILENAME,
    create_project_bundle,
    import_project_media,
    load_project_bundle,
    save_project_bundle,
    write_project_autosave,
)
from core.song_studio_clone import (
    SongStudioCloneError,
    SongStudioSaveAsConflict,
    SongStudioSaveAsError,
    clone_song_studio_document,
    save_song_studio_project_as,
)
from core.song_studio_store import (
    SONG_STUDIO_AUTOSAVE_FILENAME,
    SONG_STUDIO_BACKUP_FILENAME,
    SongStudioLoadOrigin,
    SongStudioStoreError,
    load_song_studio_document,
    save_song_studio_document,
    write_song_studio_autosave,
)
from core.studio_project import (
    FadeCurve,
    MarkerKind,
    SnapMode,
    StudioCompRange,
    StudioCrossfade,
    StudioCycleRange,
    StudioMarker,
    StudioMaster,
    StudioRegion,
    StudioTakeLane,
    StudioTrack,
    StudioTrackKind,
    default_song_studio_document,
)


_NAMESPACE = uuid.UUID("46f79226-183a-45a2-b121-15f66a086548")


def _id(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _media(label: str, *, frames: int = 48_000) -> SongMedia:
    identifier = _id(f"media:{label}")
    return SongMedia(
        media_id=identifier,
        path=f"Media/{identifier}.wav",
        sha256=uuid.uuid5(_NAMESPACE, f"hash:{label}").hex * 2,
        size_bytes=frames * 4,
        sample_rate=48_000,
        channels=2,
        frame_count=frames,
        format="WAV",
        original_basename=f"{label}.wav",
        provenance=MediaProvenance.LOCAL_FILE,
        import_method=MediaImportMethod.COPY,
    )


def _project_pair(*, backing: bool) -> tuple[SongProject, SongProject]:
    media = _media("backing") if backing else None
    source = SongProject(
        project_id=_id("source-project"),
        name="Save As Source",
        tracks=(
            SongTrack(
                track_id=_id("voice-track"),
                name="Voice",
                order=0,
            ),
        ),
        media=(media,) if media is not None else (),
        backing_media_id=media.media_id if media is not None else None,
        revision=7,
    )
    return source, replace(source, project_id=_id("destination-project"))


def _backing_ids(project: SongProject) -> tuple[str, str]:
    default = default_song_studio_document(project)
    backing = next(
        track for track in default.tracks if track.kind is StudioTrackKind.BACKING
    )
    return backing.track_id, default.regions[0].region_id


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(folder)): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def _write_empty_recording_evidence(folder: Path, project: SongProject) -> None:
    (folder / ".webjam-recording-evidence.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": project.project_id,
                "commits": [],
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (folder / ".webjam-recording-commit.lock").touch()


def test_no_backing_clone_changes_only_project_identity_and_clears_token() -> None:
    source, destination = _project_pair(backing=False)
    default = default_song_studio_document(source)
    voice = default.tracks[0]
    document = replace(
        default.update_track(
            voice.track_id,
            fader_gain=0.42,
            pan=-0.35,
            muted=True,
        ),
        markers=(
            StudioMarker(
                marker_id=_id("verse"),
                start_frame=0,
                end_frame=48_000,
                label="Verse",
                kind=MarkerKind.SECTION,
            ),
        ),
        cycle_range=StudioCycleRange(1_000, 12_000),
        snap_mode=SnapMode.MARKERS,
        master=StudioMaster(gain=0.8, limiter_enabled=False),
        revision=31,
        _store_token="a" * 64,
    )
    before = document.to_dict()

    cloned = clone_song_studio_document(source, destination, document)

    assert cloned == replace(
        document,
        project_id=destination.project_id,
        _store_token=None,
    )
    assert cloned.store_token is None
    assert document.to_dict() == before
    assert document.store_token == "a" * 64


def test_backing_clone_remaps_every_typed_dependency_and_preserves_edits() -> None:
    source, destination = _project_pair(backing=True)
    default = default_song_studio_document(source)
    old_track_id, old_region_id = _backing_ids(source)
    new_track_id, new_region_id = _backing_ids(destination)
    base = default.regions[0]
    edited_base = replace(
        base,
        source_frame_count=36_000,
        timeline_frame_count=36_000,
        mapping_source_frame_count=36_000,
        mapping_timeline_frame_count=36_000,
        fade_out_frames=1_000,
        fade_out_curve=FadeCurve.S_CURVE,
    )
    overlap = StudioRegion(
        region_id=_id("backing-overlap"),
        track_id=old_track_id,
        source_media_id=source.backing_media_id,
        source_start_frame=24_000,
        source_frame_count=24_000,
        timeline_start_frame=24_000,
        timeline_frame_count=24_000,
        fade_in_frames=1_000,
        fade_in_curve=FadeCurve.EQUAL_POWER,
    )
    lane = StudioTakeLane(
        lane_id=_id("backing-lane"),
        track_id=old_track_id,
        name="Backing alternatives",
        region_ids=(old_region_id, overlap.region_id),
        source_media_id=source.backing_media_id,
    )
    comp = StudioCompRange(
        comp_range_id=_id("backing-comp"),
        track_id=old_track_id,
        lane_id=lane.lane_id,
        timeline_start_frame=1_000,
        frame_count=2_000,
        fade_in_frames=100,
        fade_out_frames=200,
    )
    crossfade = StudioCrossfade(
        crossfade_id=_id("backing-crossfade"),
        left_region_id=old_region_id,
        right_region_id=overlap.region_id,
        start_frame=24_000,
        frame_count=1_000,
        curve=FadeCurve.EQUAL_POWER,
    )
    tracks = tuple(
        replace(
            track,
            trim_gain=0.85,
            fader_gain=0.63,
            pan=0.2,
            solo=True,
        )
        if track.track_id == old_track_id
        else replace(track, fader_gain=0.9, pan=-0.1)
        for track in default.tracks
    )
    document = replace(
        default,
        tracks=tracks,
        regions=(edited_base, overlap),
        take_lanes=(lane,),
        comp_ranges=(comp,),
        markers=(
            StudioMarker(
                marker_id=_id("count-in"),
                start_frame=0,
                label="Count in",
            ),
            StudioMarker(
                marker_id=_id("chorus"),
                start_frame=12_000,
                end_frame=36_000,
                label="Chorus",
                kind=MarkerKind.SECTION,
            ),
        ),
        crossfades=(crossfade,),
        cycle_range=StudioCycleRange(12_000, 36_000, enabled=True),
        snap_mode=SnapMode.TIME,
        master=StudioMaster(gain=0.77, limiter_enabled=False),
        revision=91,
        _store_token="b" * 64,
    )
    before = document.to_dict()

    cloned = clone_song_studio_document(source, destination, document)

    assert cloned.project_id == destination.project_id
    assert cloned.store_token is None
    assert cloned.revision == document.revision
    assert cloned.tracks[0] == replace(document.tracks[0], track_id=new_track_id)
    assert cloned.tracks[1:] == document.tracks[1:]
    assert cloned.regions[0] == replace(
        edited_base,
        region_id=new_region_id,
        track_id=new_track_id,
    )
    assert cloned.regions[1] == replace(overlap, track_id=new_track_id)
    assert cloned.take_lanes[0] == replace(
        lane,
        track_id=new_track_id,
        region_ids=(new_region_id, overlap.region_id),
    )
    assert cloned.comp_ranges[0] == replace(comp, track_id=new_track_id)
    assert cloned.crossfades[0] == replace(
        crossfade,
        left_region_id=new_region_id,
    )
    assert cloned.markers == document.markers
    assert cloned.cycle_range == document.cycle_range
    assert cloned.snap_mode is document.snap_mode
    assert cloned.master == document.master
    assert cloned.regions[0].source_media_id == source.backing_media_id
    assert new_track_id != old_track_id
    assert new_region_id != old_region_id
    assert document.to_dict() == before
    assert document.store_token == "b" * 64


def test_clone_rejects_destination_or_source_identity_mismatches() -> None:
    source, destination = _project_pair(backing=False)
    document = default_song_studio_document(source)

    with pytest.raises(SongStudioCloneError, match="new project identity"):
        clone_song_studio_document(source, source, document)
    with pytest.raises(SongStudioCloneError, match="exact project lineage"):
        clone_song_studio_document(
            source,
            replace(destination, name="Changed during Save As"),
            document,
        )
    with pytest.raises(SongStudioCloneError, match="different project"):
        clone_song_studio_document(
            source,
            destination,
            replace(document, project_id=_id("wrong-project")),
        )
    with pytest.raises(SongStudioCloneError, match="missing a durable"):
        clone_song_studio_document(
            source,
            destination,
            replace(document, tracks=()),
        )


def test_clone_rejects_media_without_lineage_and_missing_backing_base() -> None:
    source, destination = _project_pair(backing=False)
    document = default_song_studio_document(source)
    foreign = StudioRegion(
        region_id=_id("foreign-region"),
        track_id=document.tracks[0].track_id,
        source_media_id=_id("foreign-media"),
        source_frame_count=100,
        timeline_frame_count=100,
    )
    with pytest.raises(SongStudioCloneError, match="outside the source"):
        clone_song_studio_document(
            source,
            destination,
            replace(document, regions=(foreign,)),
        )

    backed_source, backed_destination = _project_pair(backing=True)
    backed = default_song_studio_document(backed_source)
    with pytest.raises(SongStudioCloneError, match="base region is missing"):
        clone_song_studio_document(
            backed_source,
            backed_destination,
            replace(backed, regions=()),
        )


def test_clone_rejects_destination_deterministic_id_collision() -> None:
    source, destination = _project_pair(backing=True)
    document = default_song_studio_document(source)
    destination_backing_id, _region_id = _backing_ids(destination)
    collision = StudioTrack(
        track_id=destination_backing_id,
        order=len(document.tracks),
        name="User Bus",
        kind=StudioTrackKind.BUS,
        channel_count=2,
    )

    with pytest.raises(SongStudioCloneError, match="collides"):
        clone_song_studio_document(
            source,
            destination,
            replace(document, tracks=(*document.tracks, collision)),
        )


def _stored_backing_project(
    tmp_path: Path,
) -> tuple[Path, object, object]:
    bundle = tmp_path / "Source Song.webjam"
    created = create_project_bundle(
        bundle,
        "Source Song",
        project_id=_id("stored-source"),
    )
    with_track = created.project.add_track(
        "Lead Voice",
        track_id=_id("stored-voice"),
    )
    tracked = save_project_bundle(
        bundle,
        with_track,
        expected_token=created.token,
    )
    audio = tmp_path / "Audio Files" / "reference with spaces.wav"
    audio.parent.mkdir()
    sf.write(audio, np.zeros((4_800, 2), dtype=np.float32), 48_000)
    imported = import_project_media(
        bundle,
        tracked.project,
        audio,
        designate_backing=True,
        media_id=_id("stored-backing"),
    )
    project_save = save_project_bundle(
        bundle,
        imported.project,
        expected_token=tracked.token,
    )
    document = default_song_studio_document(project_save.project)
    backing_track_id, _region_id = _backing_ids(project_save.project)
    document = document.update_track(
        backing_track_id,
        fader_gain=0.51,
        pan=0.25,
    )
    studio_save = save_song_studio_document(
        bundle,
        project_save.project,
        document,
        expected_token=None,
    )
    return bundle, project_save, studio_save


def test_transaction_publishes_loadable_clean_destination_and_preserves_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, project_save, studio_save = _stored_backing_project(tmp_path)
    write_project_autosave(
        source,
        project_save.project,
        base_primary_token=project_save.token,
    )
    write_song_studio_autosave(
        source,
        project_save.project,
        studio_save.document.update_track(
            studio_save.document.tracks[0].track_id,
            fader_gain=0.49,
        ),
        base_primary_token=studio_save.token,
    )
    (source / ".webjam-project.corrupt-evidence.json").write_bytes(b"project")
    (source / ".webjam-song-studio.corrupt-evidence.json").write_bytes(b"studio")
    _write_empty_recording_evidence(source, project_save.project)
    before = _snapshot(source)
    destination = tmp_path / "Copies With Spaces" / "Song Copy.webjam"
    evidence_copy_calls: list[tuple[Path, Path]] = []
    real_copy = clone_module.copy_recording_evidence_for_project_copy

    def copy_evidence(
        source_path, source_project, destination_path, destination_project, **kwargs
    ):
        evidence_copy_calls.append((Path(source_path), Path(destination_path)))
        return real_copy(
            source_path,
            source_project,
            destination_path,
            destination_project,
            **kwargs,
        )

    monkeypatch.setattr(
        clone_module,
        "copy_recording_evidence_for_project_copy",
        copy_evidence,
    )

    result = save_song_studio_project_as(
        source,
        destination,
        project_save.project,
        studio_save.document,
        expected_project_token=project_save.token,
        expected_studio_token=studio_save.token,
        new_project_id=_id("stored-destination"),
    )

    assert result.bundle_path == destination.resolve()
    assert result.project.project_id == _id("stored-destination")
    assert [item.track_id for item in result.project.tracks] == [
        item.track_id for item in project_save.project.tracks
    ]
    assert [item.media_id for item in result.project.media] == [
        item.media_id for item in project_save.project.media
    ]
    assert result.document.store_token == result.studio_token
    loaded_project = load_project_bundle(destination)
    loaded_studio = load_song_studio_document(
        destination,
        loaded_project.project,
    )
    assert loaded_project.token == result.project_token
    assert loaded_project.project == result.project
    assert loaded_studio.origin is SongStudioLoadOrigin.PRIMARY
    assert loaded_studio.token == result.studio_token
    assert loaded_studio.document == result.document
    assert loaded_studio.recovery_candidate is None
    assert loaded_studio.document.tracks[0].fader_gain == 0.51
    for name in (
        PROJECT_BACKUP_FILENAME,
        PROJECT_AUTOSAVE_FILENAME,
        SONG_STUDIO_BACKUP_FILENAME,
        SONG_STUDIO_AUTOSAVE_FILENAME,
        ".webjam-project.corrupt-evidence.json",
        ".webjam-song-studio.corrupt-evidence.json",
    ):
        assert not (destination / name).exists()
    assert _snapshot(source) == before
    assert not tuple(destination.parent.glob(f".{destination.name}.*.saving"))
    assert len(evidence_copy_calls) == 1
    assert evidence_copy_calls[0][0] == source
    assert evidence_copy_calls[0][1].parent == destination.parent
    assert evidence_copy_calls[0][1].name.startswith(f".{destination.name}.")
    assert evidence_copy_calls[0][1].name.endswith(".saving")


def test_transaction_rolls_back_when_recording_evidence_cannot_be_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, project_save, studio_save = _stored_backing_project(tmp_path)
    destination = tmp_path / "Evidence Failure.webjam"
    _write_empty_recording_evidence(source, project_save.project)
    before = _snapshot(source)

    def fail_evidence(*_args, **_kwargs):
        from core.project_recording_commit import ProjectRecordingCommitError

        raise ProjectRecordingCommitError("simulated path-free evidence-copy failure")

    monkeypatch.setattr(
        clone_module,
        "copy_recording_evidence_for_project_copy",
        fail_evidence,
    )
    with pytest.raises(
        SongStudioSaveAsError,
        match="preserve recording evidence",
    ):
        save_song_studio_project_as(
            source,
            destination,
            project_save.project,
            studio_save.document,
            expected_project_token=project_save.token,
            expected_studio_token=studio_save.token,
        )

    assert not destination.exists()
    assert not tuple(tmp_path.glob(f".{destination.name}.*.saving"))
    assert _snapshot(source) == before


def test_transaction_rolls_back_unpublished_stage_after_studio_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, project_save, studio_save = _stored_backing_project(tmp_path)
    destination = tmp_path / "Failed Copy.webjam"
    before = _snapshot(source)

    def fail_studio(*_args, **_kwargs):
        raise SongStudioStoreError("simulated Studio publication failure")

    monkeypatch.setattr(
        clone_module,
        "save_song_studio_document",
        fail_studio,
    )
    with pytest.raises(SongStudioSaveAsError, match="save Studio state"):
        save_song_studio_project_as(
            source,
            destination,
            project_save.project,
            studio_save.document,
            expected_project_token=project_save.token,
            expected_studio_token=studio_save.token,
        )

    assert not destination.exists()
    assert not tuple(tmp_path.glob(f".{destination.name}.*.saving"))
    assert _snapshot(source) == before


def test_transaction_rechecks_source_studio_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, project_save, studio_save = _stored_backing_project(tmp_path)
    destination = tmp_path / "Raced Copy.webjam"
    real_save = clone_module.save_song_studio_document
    raced = False

    def save_then_race(bundle, project, document, **kwargs):
        nonlocal raced
        result = real_save(bundle, project, document, **kwargs)
        if Path(bundle).resolve() != source.resolve() and not raced:
            raced = True
            real_save(
                source,
                project_save.project,
                studio_save.document.update_track(
                    studio_save.document.tracks[0].track_id,
                    fader_gain=0.33,
                ),
                expected_token=studio_save.token,
            )
        return result

    monkeypatch.setattr(
        clone_module,
        "save_song_studio_document",
        save_then_race,
    )
    with pytest.raises(SongStudioSaveAsConflict, match="changed during"):
        save_song_studio_project_as(
            source,
            destination,
            project_save.project,
            studio_save.document,
            expected_project_token=project_save.token,
            expected_studio_token=studio_save.token,
        )

    assert raced is True
    assert not destination.exists()
    assert not tuple(tmp_path.glob(f".{destination.name}.*.saving"))
    assert (
        load_song_studio_document(
            source,
            project_save.project,
        ).token
        != studio_save.token
    )


def test_transaction_rejects_existing_destination_without_touching_it(
    tmp_path: Path,
) -> None:
    source, project_save, studio_save = _stored_backing_project(tmp_path)
    destination = tmp_path / "Existing Copy.webjam"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    source_before = _snapshot(source)
    destination_before = _snapshot(destination)

    with pytest.raises(SongStudioSaveAsError, match="already exists"):
        save_song_studio_project_as(
            source,
            destination,
            project_save.project,
            studio_save.document,
            expected_project_token=project_save.token,
            expected_studio_token=studio_save.token,
        )

    assert _snapshot(source) == source_before
    assert _snapshot(destination) == destination_before


def test_transaction_rejects_destination_inside_source_without_creating_it(
    tmp_path: Path,
) -> None:
    source, project_save, studio_save = _stored_backing_project(tmp_path)
    destination = source / "Nested Folder" / "Copy.webjam"
    source_before = _snapshot(source)

    with pytest.raises(SongStudioSaveAsError, match="outside the source"):
        save_song_studio_project_as(
            source,
            destination,
            project_save.project,
            studio_save.document,
            expected_project_token=project_save.token,
            expected_studio_token=studio_save.token,
        )

    assert _snapshot(source) == source_before
    assert not destination.parent.exists()


@pytest.mark.parametrize("stale", ["project", "studio"])
def test_transaction_rejects_stale_source_tokens(
    tmp_path: Path,
    stale: str,
) -> None:
    source, project_save, studio_save = _stored_backing_project(tmp_path)
    destination = tmp_path / f"Stale {stale}.webjam"

    with pytest.raises(SongStudioSaveAsConflict, match="changed"):
        save_song_studio_project_as(
            source,
            destination,
            project_save.project,
            studio_save.document,
            expected_project_token=(
                "0" * 64 if stale == "project" else project_save.token
            ),
            expected_studio_token=(
                "0" * 64 if stale == "studio" else studio_save.token
            ),
        )

    assert not destination.exists()
