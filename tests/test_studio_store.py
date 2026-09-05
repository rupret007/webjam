"""Deterministic persistence and recovery coverage for Studio arrangements."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from core.file_io import atomic_write_bytes
from core.studio_project import default_studio_document
from core.studio_store import (
    MAX_STUDIO_STATE_BYTES,
    STUDIO_STATE_BACKUP_FILENAME,
    STUDIO_STATE_LOCK_FILENAME,
    STUDIO_STATE_V1_BACKUP_FILENAME,
    StudioLoadOrigin,
    StudioStoreConflict,
    StudioStoreError,
    load_studio_document,
    save_studio_document,
    studio_state_backup_path,
    studio_state_path,
)
from core.take_project import (
    MediaSegment,
    MediaStatus,
    ProjectStatus,
    ProjectTrack,
    SourceQuality,
    SourceType,
    TakeProject,
    write_take_project,
)


_TEST_NAMESPACE = uuid.UUID("7d5973de-2c66-46ec-85e8-81da436fe505")


def _id(label: str) -> str:
    return str(uuid.uuid5(_TEST_NAMESPACE, label))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_take(
    tmp_path: Path,
    *,
    label: str = "take",
    track_count: int = 2,
) -> tuple[Path, TakeProject]:
    take_dir = tmp_path / label
    media_dir = take_dir / "media"
    media_dir.mkdir(parents=True)
    tracks: list[ProjectTrack] = []
    for index in range(track_count):
        contents = f"immutable source {label} track {index}\n".encode("ascii")
        relative_path = f"media/track-{index}.wav"
        (take_dir / relative_path).write_bytes(contents)
        tracks.append(
            ProjectTrack(
                track_id=_id(f"{label}:track:{index}"),
                source_id=_id(f"{label}:source:{index}"),
                participant_id=None,
                name=f"Track {index + 1}",
                instrument="",
                source_type=SourceType.JAMULUS_SERVER,
                quality=SourceQuality.NETWORK_TRACK,
                media_status=MediaStatus.AVAILABLE,
                order=index,
                segments=(
                    MediaSegment(
                        segment_id=_id(f"{label}:segment:{index}"),
                        path=relative_path,
                        project_start_frame=index * 4_800,
                        frame_count=48_000 + index * 2_400,
                        sample_rate=48_000,
                        channels=1,
                        sample_format="PCM_24",
                        sha256=_digest(contents),
                        size_bytes=len(contents),
                    ),
                ),
            )
        )
    project = TakeProject(
        session_id=_id(f"{label}:session"),
        take_id=_id(f"{label}:take"),
        session_title="Deterministic Store Session",
        take_name="Take 01",
        status=ProjectStatus.COMPLETE,
        project_sample_rate=48_000,
        participants=(),
        tracks=tuple(tracks),
    )
    write_take_project(take_dir, project)
    return take_dir, project


def _file_snapshot(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _evidence_hashes(take_dir: Path) -> dict[str, str]:
    paths = [take_dir / "webjam-take.json", *sorted(take_dir.glob("media/*.wav"))]
    return {
        path.relative_to(take_dir).as_posix(): _digest(path.read_bytes())
        for path in paths
    }


def _legacy_state_bytes(project: TakeProject) -> bytes:
    first, second = project.tracks
    payload = {
        "schema_version": 1,
        "session_id": project.session_id,
        "take_id": project.take_id,
        "tracks": [
            {
                "track_id": first.track_id,
                "gain": 0.625,
                "pan": -0.375,
                "muted": True,
                "solo": False,
                "export_included": False,
            },
            {
                "track_id": second.track_id,
                "gain": 1.25,
                "pan": 0.5,
                "muted": False,
                "solo": True,
                "export_included": True,
            },
        ],
    }
    # Non-canonical whitespace makes byte-for-byte migration preservation
    # observable instead of merely comparing equivalent decoded JSON.
    return json.dumps(payload, indent=3, sort_keys=True).encode("utf-8") + b"\r\n \t"


def test_default_load_is_read_only_and_uses_deterministic_durable_ids(
    tmp_path: Path,
) -> None:
    take_dir, project = _make_take(tmp_path)
    before = _file_snapshot(take_dir)

    expected_first = default_studio_document(project)
    expected_second = default_studio_document(project)
    first = load_studio_document(take_dir)
    second = load_studio_document(take_dir)

    assert first.origin is StudioLoadOrigin.DEFAULT
    assert first.token is None
    assert first.needs_save is False
    assert first.recovery_notice == ""
    assert first.document == expected_first == expected_second == second.document
    assert tuple(item.track_id for item in first.document.tracks) == tuple(
        item.track_id for item in project.tracks
    )
    assert tuple(item.region_id for item in first.document.regions) == tuple(
        item.region_id for item in expected_second.regions
    )
    assert len({item.region_id for item in first.document.regions}) == len(
        first.document.regions
    )
    assert _file_snapshot(take_dir) == before
    assert not studio_state_path(take_dir).exists()
    assert not studio_state_backup_path(take_dir).exists()
    assert not (take_dir / STUDIO_STATE_LOCK_FILENAME).exists()


def test_schema_one_migration_preserves_mix_in_memory_without_load_writes(
    tmp_path: Path,
) -> None:
    take_dir, project = _make_take(tmp_path)
    legacy_bytes = _legacy_state_bytes(project)
    primary = studio_state_path(take_dir)
    primary.write_bytes(legacy_bytes)
    before = _file_snapshot(take_dir)

    loaded = load_studio_document(take_dir)

    first = loaded.document.state_for(project.tracks[0].track_id)
    second = loaded.document.state_for(project.tracks[1].track_id)
    assert loaded.origin is StudioLoadOrigin.MIGRATED_V1
    assert loaded.token == _digest(legacy_bytes)
    assert loaded.needs_save is True
    assert first.fader_gain == pytest.approx(0.625)
    assert first.pan == pytest.approx(-0.375)
    assert first.muted is True
    assert first.export_included is False
    assert second.fader_gain == pytest.approx(1.25)
    assert second.pan == pytest.approx(0.5)
    assert second.solo is True
    assert _file_snapshot(take_dir) == before
    assert primary.read_bytes() == legacy_bytes


def test_first_migration_save_preserves_exact_v1_then_rolls_v2_backups(
    tmp_path: Path,
) -> None:
    take_dir, project = _make_take(tmp_path)
    primary = studio_state_path(take_dir)
    backup = take_dir / STUDIO_STATE_BACKUP_FILENAME
    migration_backup = take_dir / STUDIO_STATE_V1_BACKUP_FILENAME
    legacy_bytes = _legacy_state_bytes(project)
    primary.write_bytes(legacy_bytes)
    loaded = load_studio_document(take_dir)
    first_edit = loaded.document.update_track(
        project.tracks[0].track_id,
        trim_gain=0.8,
    )

    first_save = save_studio_document(
        take_dir,
        first_edit,
        expected_token=loaded.token,
    )

    first_v2_bytes = primary.read_bytes()
    assert first_save.backup_path == backup
    assert json.loads(first_v2_bytes) == first_save.document.to_dict()
    assert json.loads(first_v2_bytes)["schema_version"] == 2
    assert backup.read_bytes() == legacy_bytes
    assert migration_backup.read_bytes() == legacy_bytes

    region = first_save.document.regions[0]
    second_edit = first_save.document.move_region(
        region.region_id,
        region.timeline_start_frame + 960,
    )
    second_save = save_studio_document(
        take_dir,
        second_edit,
        expected_token=first_save.token,
    )

    assert second_save.backup_path == backup
    assert backup.read_bytes() == first_v2_bytes
    assert migration_backup.read_bytes() == legacy_bytes
    assert primary.read_bytes() != first_v2_bytes
    assert load_studio_document(take_dir).document == second_save.document


def test_edit_save_close_reopen_is_exact_and_never_mutates_recording_truth(
    tmp_path: Path,
) -> None:
    take_dir, project = _make_take(tmp_path)
    evidence_before = _evidence_hashes(take_dir)
    loaded = load_studio_document(take_dir)
    region = loaded.document.regions[0]
    edited = loaded.document.update_track(
        project.tracks[0].track_id,
        trim_gain=0.75,
        fader_gain=1.375,
        pan=-0.2,
        muted=True,
    )
    edited = edited.duplicate_region(
        region.region_id,
        new_region_id=_id("round-trip:duplicate-region"),
        timeline_start_frame=region.timeline_end_frame + 4_800,
    )

    saved = save_studio_document(
        take_dir,
        edited,
        expected_token=loaded.token,
    )
    expected_document = saved.document
    expected_payload = saved.document.to_dict()
    expected_token = saved.token
    del loaded, edited, saved

    reopened = load_studio_document(take_dir)

    assert reopened.origin is StudioLoadOrigin.PRIMARY_V2
    assert reopened.document == expected_document
    assert reopened.document.to_dict() == expected_payload
    assert reopened.token == expected_token
    assert reopened.needs_save is False
    assert _evidence_hashes(take_dir) == evidence_before


def test_corrupt_primary_recovers_read_only_then_is_preserved_on_explicit_save(
    tmp_path: Path,
) -> None:
    take_dir, project = _make_take(tmp_path)
    initial = default_studio_document(project)
    first_save = save_studio_document(take_dir, initial, expected_token=None)
    changed = first_save.document.update_track(
        project.tracks[0].track_id,
        pan=0.25,
    )
    second_save = save_studio_document(
        take_dir,
        changed,
        expected_token=first_save.token,
    )
    primary = studio_state_path(take_dir)
    backup = studio_state_backup_path(take_dir)
    valid_backup = backup.read_bytes()
    corrupt_bytes = b'{"schema_version": 2, "tracks": [broken'
    primary.write_bytes(corrupt_bytes)
    corrupt_copy = take_dir / (
        f".webjam-studio-state.corrupt-{_digest(corrupt_bytes)}.json"
    )

    recovered = load_studio_document(take_dir)

    assert recovered.origin is StudioLoadOrigin.BACKUP
    assert recovered.document == first_save.document
    assert recovered.token == _digest(corrupt_bytes)
    assert recovered.needs_save is True
    assert "Recovered" in recovered.recovery_notice
    assert primary.read_bytes() == corrupt_bytes
    assert backup.read_bytes() == valid_backup
    assert not corrupt_copy.exists()

    recovered_edit = recovered.document.update_track(
        project.tracks[1].track_id,
        fader_gain=0.9,
    )
    saved = save_studio_document(
        take_dir,
        recovered_edit,
        expected_token=recovered.token,
    )

    assert saved.backup_path == corrupt_copy
    assert corrupt_copy.read_bytes() == corrupt_bytes
    assert backup.read_bytes() == valid_backup
    assert primary.read_bytes() != corrupt_bytes
    assert load_studio_document(take_dir).document == saved.document
    assert second_save.document != saved.document


def test_partial_schema_two_primary_recovers_backup_instead_of_defaulting_fields(
    tmp_path: Path,
) -> None:
    take_dir, project = _make_take(tmp_path)
    first = save_studio_document(
        take_dir,
        default_studio_document(project),
        expected_token=None,
    )
    second = save_studio_document(
        take_dir,
        first.document.update_track(project.tracks[0].track_id, pan=0.4),
        expected_token=first.token,
    )
    primary = studio_state_path(take_dir)
    partial = json.loads(primary.read_text(encoding="utf-8"))
    partial.pop("regions")
    partial_bytes = (json.dumps(partial) + "\n").encode("utf-8")
    primary.write_bytes(partial_bytes)

    recovered = load_studio_document(take_dir)

    assert recovered.origin is StudioLoadOrigin.BACKUP
    assert recovered.document == first.document
    assert recovered.token == _digest(partial_bytes)
    assert recovered.document != second.document


def test_oversized_primary_with_valid_backup_can_be_quarantined_and_replaced(
    tmp_path: Path,
) -> None:
    take_dir, project = _make_take(tmp_path)
    first = save_studio_document(
        take_dir,
        default_studio_document(project),
        expected_token=None,
    )
    save_studio_document(
        take_dir,
        first.document.update_track(project.tracks[0].track_id, pan=0.2),
        expected_token=first.token,
    )
    primary = studio_state_path(take_dir)
    oversized = b"x" * (MAX_STUDIO_STATE_BYTES + 1)
    primary.write_bytes(oversized)

    recovered = load_studio_document(take_dir)
    assert recovered.origin is StudioLoadOrigin.BACKUP
    assert recovered.token is not None and len(recovered.token) == 64

    saved = save_studio_document(
        take_dir,
        recovered.document.update_track(project.tracks[1].track_id, pan=-0.3),
        expected_token=recovered.token,
    )

    assert saved.backup_path is not None
    assert "corrupt-oversized" in saved.backup_path.name
    assert saved.backup_path.read_bytes() == oversized
    assert primary.stat().st_size < MAX_STUDIO_STATE_BYTES
    assert load_studio_document(take_dir).document == saved.document


def test_existing_schema_one_backup_must_match_exact_legacy_bytes(
    tmp_path: Path,
) -> None:
    take_dir, project = _make_take(tmp_path)
    primary = studio_state_path(take_dir)
    legacy_bytes = _legacy_state_bytes(project)
    primary.write_bytes(legacy_bytes)
    migration_backup = take_dir / STUDIO_STATE_V1_BACKUP_FILENAME
    migration_backup.write_bytes(b"unrelated prior evidence")
    loaded = load_studio_document(take_dir)

    with pytest.raises(StudioStoreError, match="does not match"):
        save_studio_document(
            take_dir,
            loaded.document.update_track(project.tracks[0].track_id, pan=0.1),
            expected_token=loaded.token,
        )

    assert primary.read_bytes() == legacy_bytes
    assert migration_backup.read_bytes() == b"unrelated prior evidence"
    assert not studio_state_backup_path(take_dir).exists()


def test_backup_from_another_take_is_rejected(tmp_path: Path) -> None:
    first_dir, _first_project = _make_take(tmp_path, label="first")
    second_dir, second_project = _make_take(tmp_path, label="second")
    wrong = save_studio_document(
        second_dir,
        default_studio_document(second_project),
        expected_token=None,
    )
    wrong_bytes = wrong.path.read_bytes()
    backup = studio_state_backup_path(first_dir)
    backup.write_bytes(wrong_bytes)

    with pytest.raises(StudioStoreError, match="backup is invalid"):
        load_studio_document(first_dir)

    assert backup.read_bytes() == wrong_bytes
    assert not studio_state_path(first_dir).exists()


def test_compare_and_swap_uses_exact_primary_bytes_not_json_equivalence(
    tmp_path: Path,
) -> None:
    take_dir, project = _make_take(tmp_path)
    first_save = save_studio_document(
        take_dir,
        default_studio_document(project),
        expected_token=None,
    )
    loaded = load_studio_document(take_dir)
    primary = studio_state_path(take_dir)
    semantically_identical = (
        json.dumps(
            json.loads(primary.read_bytes()),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    assert semantically_identical != primary.read_bytes()
    primary.write_bytes(semantically_identical)
    pending_edit = loaded.document.update_track(
        project.tracks[0].track_id,
        pan=-0.5,
    )

    with pytest.raises(StudioStoreConflict, match="changed after it was loaded"):
        save_studio_document(
            take_dir,
            pending_edit,
            expected_token=loaded.token,
        )

    assert loaded.token == first_save.token
    assert primary.read_bytes() == semantically_identical
    assert not studio_state_backup_path(take_dir).exists()


def test_symlinked_primary_is_rejected_without_following_or_replacing_it(
    tmp_path: Path,
) -> None:
    take_dir, project = _make_take(tmp_path)
    outside = tmp_path / "outside-studio-state.json"
    outside.write_bytes(b"outside bytes")
    primary = studio_state_path(take_dir)
    primary.symlink_to(outside)

    with pytest.raises(StudioStoreError, match="symbolic link"):
        load_studio_document(take_dir)
    with pytest.raises(StudioStoreError, match="symbolic link"):
        save_studio_document(
            take_dir,
            default_studio_document(project),
            expected_token=None,
        )

    assert primary.is_symlink()
    assert outside.read_bytes() == b"outside bytes"


def test_oversized_primary_is_rejected_without_rewriting_it(tmp_path: Path) -> None:
    take_dir, project = _make_take(tmp_path)
    primary = studio_state_path(take_dir)
    oversized = b"x" * (MAX_STUDIO_STATE_BYTES + 1)
    primary.write_bytes(oversized)

    with pytest.raises(StudioStoreError, match="too large"):
        load_studio_document(take_dir)
    with pytest.raises(StudioStoreError, match="too large"):
        save_studio_document(
            take_dir,
            default_studio_document(project),
            expected_token=None,
        )

    assert primary.stat().st_size == len(oversized)
    assert primary.read_bytes() == oversized


def test_primary_atomic_write_failure_leaves_old_primary_readable(
    tmp_path: Path,
) -> None:
    take_dir, project = _make_take(tmp_path)
    first_save = save_studio_document(
        take_dir,
        default_studio_document(project),
        expected_token=None,
    )
    primary = studio_state_path(take_dir)
    old_primary = primary.read_bytes()
    edited = first_save.document.update_track(
        project.tracks[0].track_id,
        fader_gain=1.5,
    )
    attempted_paths: list[Path] = []

    def fail_primary_only(path: str | Path, data: bytes, *, mode: int | None) -> None:
        destination = Path(path)
        attempted_paths.append(destination)
        if destination == primary:
            raise OSError("simulated atomic replacement failure")
        atomic_write_bytes(destination, data, mode=mode)

    with patch("core.studio_store.atomic_write_bytes", side_effect=fail_primary_only):
        with pytest.raises(StudioStoreError, match="atomically save"):
            save_studio_document(
                take_dir,
                edited,
                expected_token=first_save.token,
            )

    assert attempted_paths == [studio_state_backup_path(take_dir), primary]
    assert primary.read_bytes() == old_primary
    reopened = load_studio_document(take_dir)
    assert reopened.document == first_save.document
    assert reopened.token == first_save.token
    assert reopened.origin is StudioLoadOrigin.PRIMARY_V2


def test_oversized_recovery_failed_replacement_preserves_original_and_retries(tmp_path):
    take_dir, project = _make_take(tmp_path)
    evidence = _evidence_hashes(take_dir)
    first = save_studio_document(take_dir, default_studio_document(project), expected_token=None)
    save_studio_document(take_dir, first.document.update_track(project.tracks[0].track_id, pan=0.2), expected_token=first.token)
    primary = studio_state_path(take_dir)
    original = b"x" * (MAX_STUDIO_STATE_BYTES + 1)
    primary.write_bytes(original)
    backup = studio_state_backup_path(take_dir).read_bytes()
    recovered = load_studio_document(take_dir)
    edited = recovered.document.update_track(project.tracks[1].track_id, pan=-0.3)
    with patch("core.studio_store.atomic_write_bytes", side_effect=OSError("simulated failed replacement")):
        with pytest.raises(StudioStoreError, match="atomically save"):
            save_studio_document(take_dir, edited, expected_token=recovered.token)
    assert primary.read_bytes() == original
    assert load_studio_document(take_dir).token == recovered.token
    assert studio_state_backup_path(take_dir).read_bytes() == backup
    saved = save_studio_document(take_dir, edited, expected_token=recovered.token)
    assert saved.backup_path.read_bytes() == original
    assert load_studio_document(take_dir).document == edited
    assert _evidence_hashes(take_dir) == evidence


def test_invalid_replacement_size_preserves_oversized_primary_without_quarantine(tmp_path):
    take_dir, project = _make_take(tmp_path)
    first = save_studio_document(take_dir, default_studio_document(project), expected_token=None)
    save_studio_document(take_dir, first.document, expected_token=first.token)
    primary = studio_state_path(take_dir)
    original = b"x" * (MAX_STUDIO_STATE_BYTES + 1)
    primary.write_bytes(original)
    recovered = load_studio_document(take_dir)
    backup = studio_state_backup_path(take_dir).read_bytes()
    with patch("core.studio_store.MAX_STUDIO_STATE_BYTES", 1):
        with pytest.raises(StudioStoreError, match="too large to save"):
            save_studio_document(take_dir, recovered.document, expected_token=recovered.token)
    assert primary.read_bytes() == original
    assert studio_state_backup_path(take_dir).read_bytes() == backup
    assert not list(take_dir.glob(".webjam-studio-state.corrupt-oversized-*"))


def test_post_replace_sync_failure_returns_exact_unconfirmed_token(tmp_path):
    from core.studio_store import StudioStoreSaveUnconfirmed
    take_dir, project = _make_take(tmp_path)
    primary = studio_state_path(take_dir)
    document = default_studio_document(project)
    with patch("core.file_io._fsync_parent_directory", side_effect=OSError("unconfirmed")):
        with pytest.raises(StudioStoreSaveUnconfirmed) as result:
            save_studio_document(take_dir, document, expected_token=None)
    assert result.value.published_token == _digest(primary.read_bytes())
    saved = save_studio_document(take_dir, document, expected_token=result.value.published_token)
    assert load_studio_document(take_dir).token == saved.token
