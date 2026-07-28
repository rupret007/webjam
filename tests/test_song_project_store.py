from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
import wave
from pathlib import Path

import pytest

import core.song_project_store as store_module
from core.song_project import (
    InputMapping,
    MediaImportMethod,
    MediaProvenance,
)
from core.song_project_store import (
    MAX_PROJECT_MANIFEST_BYTES,
    MAX_RECENT_PROJECTS,
    PROJECT_AUTOSAVE_FILENAME,
    PROJECT_BACKUP_FILENAME,
    PROJECT_MANIFEST_FILENAME,
    ProjectLoadOrigin,
    SongProjectConflict,
    SongProjectStoreError,
    create_project_bundle,
    discard_project_autosave,
    import_project_media,
    load_project_bundle,
    load_recent_projects,
    project_autosave_path,
    record_recent_project,
    recover_project_autosave,
    relink_project_media,
    resolve_project_media,
    save_project_as,
    save_project_bundle,
    verify_project_media,
    write_project_autosave,
    write_recent_projects,
)


_NAMESPACE = uuid.UUID("a1fd1b56-2c94-45fc-adab-8040b0783296")


def _id(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_wav(
    path: Path,
    *,
    sample_rate: int = 48_000,
    channels: int = 2,
    frames: int = 480,
    sample: int = 1_000,
) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_bytes = int(sample).to_bytes(2, "little", signed=True)
    frame = sample_bytes * channels
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(frame * frames)
    return path.read_bytes()


def _create(tmp_path: Path, label: str = "Song With Spaces"):
    bundle = tmp_path / f"{label}.webjam"
    saved = create_project_bundle(
        bundle,
        label,
        project_id=_id(f"project:{label}"),
    )
    return bundle, saved


def _snapshot(folder: Path) -> dict[str, bytes]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_create_and_load_project_with_spaces_is_read_only_and_take_independent(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    before = _snapshot(bundle)

    loaded = load_project_bundle(bundle)

    assert loaded.origin is ProjectLoadOrigin.PRIMARY
    assert loaded.project == created.project
    assert loaded.token == created.token == _digest(created.manifest_path.read_bytes())
    assert loaded.project.project_sample_rate == 48_000
    assert loaded.recovery_candidate is None
    assert loaded.recovery_notice == ""
    assert _snapshot(bundle) == before
    manifest = json.loads((bundle / PROJECT_MANIFEST_FILENAME).read_text())
    assert "take_id" not in manifest
    assert "session_id" not in manifest
    assert (bundle / "Media").is_dir()


def test_new_bundle_rejects_nonempty_directory_and_file(tmp_path: Path) -> None:
    occupied = tmp_path / "occupied.webjam"
    occupied.mkdir()
    (occupied / "notes.txt").write_text("mine", encoding="utf-8")
    before = _snapshot(occupied)

    with pytest.raises(SongProjectStoreError, match="empty"):
        create_project_bundle(occupied, "No")
    ordinary_file = tmp_path / "ordinary-file"
    ordinary_file.write_bytes(b"mine")
    with pytest.raises(SongProjectStoreError, match="directory"):
        create_project_bundle(ordinary_file, "No")

    assert _snapshot(occupied) == before


def test_explicit_save_uses_exact_byte_cas_and_last_known_good_backup(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    original = created.manifest_path.read_bytes()
    edited = created.project.add_track(
        "Voice",
        track_id=_id("track:voice"),
        input_mapping=InputMapping("device:mic", (1,)),
    )

    saved = save_project_bundle(bundle, edited, expected_token=created.token)

    assert saved.backup_path == bundle / PROJECT_BACKUP_FILENAME
    assert saved.backup_path.read_bytes() == original
    assert saved.manifest_path.read_bytes() != original
    assert load_project_bundle(bundle).project == edited

    loaded = load_project_bundle(bundle)
    semantic_rewrite = (
        json.dumps(
            json.loads(saved.manifest_path.read_bytes()),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    saved.manifest_path.write_bytes(semantic_rewrite)
    pending = edited.add_track("Guitar", track_id=_id("track:guitar"))

    with pytest.raises(SongProjectConflict, match="changed after"):
        save_project_bundle(bundle, pending, expected_token=loaded.token)

    assert saved.manifest_path.read_bytes() == semantic_rewrite
    assert saved.backup_path.read_bytes() == original


def test_corrupt_primary_recovers_backup_then_preserves_exact_damage_on_save(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    first = created.project.add_track("Voice", track_id=_id("recovery:voice"))
    first_save = save_project_bundle(bundle, first, expected_token=created.token)
    valid_backup = (bundle / PROJECT_BACKUP_FILENAME).read_bytes()
    corrupt = b'{"schema_version":1,"project_id":'
    first_save.manifest_path.write_bytes(corrupt)

    recovered = load_project_bundle(bundle)

    assert recovered.origin is ProjectLoadOrigin.BACKUP
    assert recovered.project == created.project
    assert recovered.token == _digest(corrupt)
    assert "Recovered" in recovered.recovery_notice
    assert first_save.manifest_path.read_bytes() == corrupt

    edited = recovered.project.add_track(
        "Recovered Voice",
        track_id=_id("recovered:voice"),
    )
    saved = save_project_bundle(
        bundle,
        edited,
        expected_token=recovered.token,
    )

    assert saved.preserved_corrupt_path is not None
    assert saved.preserved_corrupt_path.read_bytes() == corrupt
    assert (bundle / PROJECT_BACKUP_FILENAME).read_bytes() == valid_backup
    assert load_project_bundle(bundle).project == edited


def test_oversized_manifest_is_rejected_without_rewriting_it(tmp_path: Path) -> None:
    bundle, created = _create(tmp_path)
    oversized = b"x" * (MAX_PROJECT_MANIFEST_BYTES + 1)
    created.manifest_path.write_bytes(oversized)

    with pytest.raises(SongProjectStoreError, match="too large"):
        load_project_bundle(bundle)

    assert created.manifest_path.read_bytes() == oversized


def test_unknown_manifest_field_is_rejected_not_silently_dropped(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    value = json.loads(created.manifest_path.read_bytes())
    value["future_field"] = True
    changed = (json.dumps(value) + "\n").encode()
    created.manifest_path.write_bytes(changed)

    with pytest.raises(SongProjectStoreError, match="unsupported fields"):
        load_project_bundle(bundle)

    assert created.manifest_path.read_bytes() == changed


def test_symlinked_manifest_is_never_followed_or_replaced(tmp_path: Path) -> None:
    bundle, created = _create(tmp_path)
    outside = tmp_path / "outside-manifest.json"
    outside.write_bytes(created.manifest_path.read_bytes())
    created.manifest_path.unlink()
    created.manifest_path.symlink_to(outside)

    with pytest.raises(SongProjectStoreError, match="symbolic link"):
        load_project_bundle(bundle)
    with pytest.raises(SongProjectStoreError, match="symbolic link"):
        save_project_bundle(bundle, created.project, expected_token=None)

    assert created.manifest_path.is_symlink()
    assert outside.read_bytes() != b""


def test_symlinked_backup_is_rejected_without_touching_target(tmp_path: Path) -> None:
    bundle, created = _create(tmp_path)
    outside = tmp_path / "outside-backup.json"
    outside.write_bytes(b"outside")
    backup = bundle / PROJECT_BACKUP_FILENAME
    backup.symlink_to(outside)

    with pytest.raises(SongProjectStoreError, match="symbolic link"):
        save_project_bundle(
            bundle,
            created.project.add_track("Voice", track_id=_id("backup-link-track")),
            expected_token=created.token,
        )

    assert backup.is_symlink()
    assert outside.read_bytes() == b"outside"
    assert created.manifest_path.read_bytes() == (
        bundle / PROJECT_MANIFEST_FILENAME
    ).read_bytes()


def test_autosave_is_separate_and_explicit_recovery_promotes_it(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    primary_before = created.manifest_path.read_bytes()
    edited = created.project.add_track(
        "Vocal",
        track_id=_id("autosave:vocal"),
    )

    candidate = write_project_autosave(
        bundle,
        edited,
        base_primary_token=created.token,
    )

    assert candidate.path == bundle / PROJECT_AUTOSAVE_FILENAME
    assert created.manifest_path.read_bytes() == primary_before
    loaded = load_project_bundle(bundle)
    assert loaded.project == created.project
    assert loaded.recovery_candidate is not None
    assert loaded.recovery_candidate.project == edited
    assert "autosave" in loaded.recovery_notice

    recovered = recover_project_autosave(bundle, expected_token=created.token)

    assert recovered.project == edited
    assert load_project_bundle(bundle).project == edited
    assert not project_autosave_path(bundle).exists()
    assert (bundle / PROJECT_BACKUP_FILENAME).read_bytes() == primary_before


def test_autosave_uses_primary_cas_and_stale_candidate_is_not_offered(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    edited = created.project.add_track("Vocal", track_id=_id("stale:vocal"))

    with pytest.raises(SongProjectConflict, match="changed"):
        write_project_autosave(
            bundle,
            edited,
            base_primary_token="0" * 64,
        )

    write_project_autosave(
        bundle,
        edited,
        base_primary_token=created.token,
    )
    raw = json.loads(created.manifest_path.read_bytes())
    semantically_same = (
        json.dumps(raw, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    assert _digest(semantically_same) != created.token
    created.manifest_path.write_bytes(semantically_same)

    loaded = load_project_bundle(bundle)

    assert loaded.project == created.project
    assert loaded.recovery_candidate is None
    assert project_autosave_path(bundle).exists()


def test_corrupt_or_symlinked_autosave_does_not_get_promoted(
    tmp_path: Path,
) -> None:
    bundle, _created = _create(tmp_path)
    autosave = bundle / PROJECT_AUTOSAVE_FILENAME
    autosave.write_bytes(b"{broken")
    loaded = load_project_bundle(bundle)
    assert loaded.recovery_candidate is None
    assert "damaged" in loaded.recovery_notice
    assert autosave.read_bytes() == b"{broken"

    autosave.unlink()
    outside = tmp_path / "outside-autosave.json"
    outside.write_bytes(b"outside")
    autosave.symlink_to(outside)
    with pytest.raises(SongProjectStoreError, match="symbolic link"):
        load_project_bundle(bundle)
    with pytest.raises(SongProjectStoreError, match="symbolic link"):
        discard_project_autosave(bundle)
    assert outside.read_bytes() == b"outside"


def test_import_collects_descriptor_bound_audio_and_never_modifies_original(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    source = tmp_path / "Source Music" / "My backing song.wav"
    original = _write_wav(source, frames=777, sample=1_234)
    os.chmod(source, 0o444)
    before = source.stat()

    imported = import_project_media(
        bundle,
        created.project,
        source,
        designate_backing=True,
        provenance=MediaProvenance.LOCAL_FILE,
        import_method=MediaImportMethod.COLLECT_COPY,
        provenance_detail="Reference mix supplied by songwriter",
        media_id=_id("media:import"),
    )

    after = source.stat()
    assert source.read_bytes() == original
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert after.st_mtime_ns == before.st_mtime_ns
    assert imported.media.media_id == _id("media:import")
    assert imported.media.path == f"Media/{_id('media:import')}.wav"
    assert imported.media.original_basename == source.name
    assert imported.media.original_read_only is True
    assert imported.media.sha256 == _digest(original)
    assert imported.media.size_bytes == len(original)
    assert imported.media.sample_rate == 48_000
    assert imported.media.channels == 2
    assert imported.media.frame_count == 777
    assert imported.project.backing_media_id == imported.media.media_id
    assert imported.path.read_bytes() == original

    saved = save_project_bundle(
        bundle,
        imported.project,
        expected_token=created.token,
    )
    manifest_text = saved.manifest_path.read_text(encoding="utf-8")
    assert str(source) not in manifest_text
    assert str(source.parent) not in manifest_text
    assert source.name in manifest_text
    assert verify_project_media(bundle, saved.project) == (imported.path,)


def test_invalid_audio_rolls_back_collected_copy(tmp_path: Path) -> None:
    bundle, created = _create(tmp_path)
    source = tmp_path / "not audio.dat"
    source.write_bytes(b"not audio but original bytes")
    media_id = _id("invalid-audio")
    destination = bundle / "Media" / f"{media_id}.dat"

    with pytest.raises(SongProjectStoreError, match="supported"):
        import_project_media(
            bundle,
            created.project,
            source,
            media_id=media_id,
        )

    assert source.read_bytes() == b"not audio but original bytes"
    assert not destination.exists()
    assert load_project_bundle(bundle).project == created.project


def test_import_rejects_source_symlink_and_existing_destination(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    real = tmp_path / "real.wav"
    _write_wav(real)
    link = tmp_path / "linked.wav"
    link.symlink_to(real)

    with pytest.raises(SongProjectStoreError, match="symbolic link"):
        import_project_media(bundle, created.project, link)

    media_id = _id("collision")
    first = import_project_media(
        bundle,
        created.project,
        real,
        media_id=media_id,
    )
    with pytest.raises(SongProjectStoreError, match="occupied"):
        import_project_media(
            bundle,
            created.project,
            real,
            media_id=media_id,
        )
    assert first.path.read_bytes() == real.read_bytes()


def test_relink_restores_only_missing_media_after_checksum_and_metadata_match(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    source = tmp_path / "original reference.wav"
    original = _write_wav(source, frames=960, sample=2_222)
    imported = import_project_media(
        bundle,
        created.project,
        source,
        media_id=_id("relink"),
    )
    saved = save_project_bundle(
        bundle,
        imported.project,
        expected_token=created.token,
    )

    with pytest.raises(SongProjectStoreError, match="only when"):
        relink_project_media(bundle, saved.project, imported.media.media_id, source)

    imported.path.unlink()
    wrong = tmp_path / "wrong candidate.wav"
    _write_wav(wrong, frames=960, sample=-2_222)
    with pytest.raises(SongProjectStoreError, match="checksum"):
        relink_project_media(bundle, saved.project, imported.media.media_id, wrong)
    assert not imported.path.exists()

    relinked = relink_project_media(
        bundle,
        saved.project,
        imported.media.media_id,
        source,
    )

    assert relinked.project == saved.project
    assert relinked.media == imported.media
    assert relinked.path.read_bytes() == original
    assert str(source) not in saved.manifest_path.read_text(encoding="utf-8")
    assert source.read_bytes() == original


def test_relink_rejects_same_size_checksum_mismatch_without_partial_file(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    source = tmp_path / "one.wav"
    _write_wav(source, frames=100, sample=100)
    imported = import_project_media(
        bundle,
        created.project,
        source,
        media_id=_id("same-size"),
    )
    saved = save_project_bundle(
        bundle,
        imported.project,
        expected_token=created.token,
    )
    imported.path.unlink()
    wrong = tmp_path / "two.wav"
    _write_wav(wrong, frames=100, sample=101)
    assert wrong.stat().st_size == source.stat().st_size

    with pytest.raises(SongProjectStoreError, match="checksum"):
        relink_project_media(bundle, saved.project, imported.media.media_id, wrong)

    assert not imported.path.exists()


def test_media_symlink_is_rejected_on_load_resolve_and_verify(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    source = tmp_path / "source.wav"
    _write_wav(source)
    imported = import_project_media(
        bundle,
        created.project,
        source,
        media_id=_id("media-link"),
    )
    saved = save_project_bundle(
        bundle,
        imported.project,
        expected_token=created.token,
    )
    imported.path.unlink()
    imported.path.symlink_to(source)

    with pytest.raises(SongProjectStoreError, match="symbolic link"):
        load_project_bundle(bundle)
    with pytest.raises(SongProjectStoreError, match="symbolic link"):
        resolve_project_media(bundle, imported.media)
    with pytest.raises(SongProjectStoreError, match="symbolic link"):
        verify_project_media(bundle, saved.project)
    assert source.is_file()


def test_verify_detects_media_corruption_without_mutating_any_bytes(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    source = tmp_path / "source.wav"
    _write_wav(source)
    imported = import_project_media(
        bundle,
        created.project,
        source,
        media_id=_id("corrupt-media"),
    )
    saved = save_project_bundle(
        bundle,
        imported.project,
        expected_token=created.token,
    )
    corrupted = bytearray(imported.path.read_bytes())
    corrupted[-1] ^= 0x01
    imported.path.write_bytes(bytes(corrupted))
    before = imported.path.read_bytes()

    with pytest.raises(SongProjectStoreError, match="verification failed"):
        verify_project_media(bundle, saved.project)

    assert imported.path.read_bytes() == before
    assert source.read_bytes() != before


def test_save_as_changes_project_identity_but_preserves_track_media_lineage(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path, "Original Song")
    source = tmp_path / "reference.wav"
    _write_wav(source, frames=321)
    imported = import_project_media(
        bundle,
        created.project.add_track(
            "Voice",
            track_id=_id("save-as-track"),
        ),
        source,
        designate_backing=True,
        media_id=_id("save-as-media"),
    )
    source_saved = save_project_bundle(
        bundle,
        imported.project,
        expected_token=created.token,
    )
    before = _snapshot(bundle)
    destination = tmp_path / "A New Folder" / "Song Copy.webjam"

    cloned = save_project_as(
        bundle,
        destination,
        source_saved.project,
        expected_token=source_saved.token,
        new_project_id=_id("save-as-project"),
    )

    assert cloned.project.project_id == _id("save-as-project")
    assert cloned.project.project_id != source_saved.project.project_id
    assert [item.track_id for item in cloned.project.tracks] == [
        item.track_id for item in source_saved.project.tracks
    ]
    assert [item.media_id for item in cloned.project.media] == [
        item.media_id for item in source_saved.project.media
    ]
    assert cloned.project.backing_media_id == source_saved.project.backing_media_id
    assert verify_project_media(destination, cloned.project)
    assert (
        destination / imported.media.path
    ).read_bytes() == imported.path.read_bytes()
    assert _snapshot(bundle) == before


def test_save_as_rejects_stale_source_and_existing_destination(
    tmp_path: Path,
) -> None:
    bundle, created = _create(tmp_path)
    destination = tmp_path / "existing.webjam"
    destination.mkdir()

    with pytest.raises(SongProjectConflict, match="changed"):
        save_project_as(
            bundle,
            tmp_path / "unused.webjam",
            expected_token="0" * 64,
        )
    with pytest.raises(SongProjectStoreError, match="already exists"):
        save_project_as(
            bundle,
            destination,
            expected_token=created.token,
        )


def test_failed_primary_atomic_write_leaves_old_primary_and_valid_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, created = _create(tmp_path)
    original = created.manifest_path.read_bytes()
    real_atomic_write = store_module.atomic_write_bytes

    def fail_primary(path, data, *, mode=None):
        if Path(path).name == PROJECT_MANIFEST_FILENAME:
            raise OSError("simulated full disk")
        return real_atomic_write(path, data, mode=mode)

    monkeypatch.setattr(store_module, "atomic_write_bytes", fail_primary)
    with pytest.raises(SongProjectStoreError, match="atomically save"):
        save_project_bundle(
            bundle,
            created.project.add_track(
                "Voice",
                track_id=_id("atomic-fail-track"),
            ),
            expected_token=created.token,
        )

    assert created.manifest_path.read_bytes() == original
    assert (bundle / PROJECT_BACKUP_FILENAME).read_bytes() == original


def test_recent_project_index_is_bounded_deduplicated_and_space_safe(
    tmp_path: Path,
) -> None:
    first, _ = _create(tmp_path, "First Project")
    second, _ = _create(tmp_path, "Second Project")
    index = tmp_path / "User Settings" / "recent-projects.json"

    assert load_recent_projects(index).paths == ()
    record_recent_project(index, first)
    result = record_recent_project(index, second)
    result = record_recent_project(index, first)

    assert result.paths == (first.resolve(), second.resolve())
    assert load_recent_projects(index) == result
    raw = json.loads(index.read_bytes())
    assert raw == {
        "schema_version": 1,
        "projects": [str(first.resolve()), str(second.resolve())],
    }

    too_many = tuple(
        tmp_path / f"project-{index}.webjam"
        for index in range(MAX_RECENT_PROJECTS + 1)
    )
    with pytest.raises(SongProjectStoreError, match="entry limit"):
        write_recent_projects(index, too_many)


def test_recent_index_rejects_relative_control_unknown_and_symlink_paths(
    tmp_path: Path,
) -> None:
    index = tmp_path / "recent.json"
    with pytest.raises(SongProjectStoreError, match="absolute"):
        write_recent_projects(index, [Path("relative.webjam")])
    with pytest.raises(SongProjectStoreError, match="not safe"):
        write_recent_projects(index, [Path("/tmp/bad\npath.webjam")])

    index.write_text(
        json.dumps({"schema_version": 1, "projects": [], "unknown": True}),
        encoding="utf-8",
    )
    with pytest.raises(SongProjectStoreError, match="unsupported shape"):
        load_recent_projects(index)

    outside = tmp_path / "outside-recent.json"
    outside.write_text('{"schema_version":1,"projects":[]}', encoding="utf-8")
    index.unlink()
    index.symlink_to(outside)
    with pytest.raises(SongProjectStoreError, match="symbolic link"):
        load_recent_projects(index)
    assert outside.read_text(encoding="utf-8").startswith("{")
