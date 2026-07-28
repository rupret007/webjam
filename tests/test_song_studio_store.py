"""Safety and recovery coverage for schema-3 song Studio persistence."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from core.song_project import SongProject
from core.song_project_store import (
    create_project_bundle,
    save_project_bundle,
)
from core.song_studio_store import (
    MAX_SONG_STUDIO_AUTOSAVE_BYTES,
    MAX_SONG_STUDIO_BYTES,
    SONG_STUDIO_AUTOSAVE_FILENAME,
    SONG_STUDIO_BACKUP_FILENAME,
    SONG_STUDIO_FILENAME,
    SongStudioConflict,
    SongStudioLoadOrigin,
    SongStudioStoreError,
    discard_song_studio_autosave,
    load_song_studio_document,
    recover_song_studio_autosave,
    save_song_studio_document,
    song_studio_path,
    write_song_studio_autosave,
)
from core.studio_project import (
    StudioDocument,
    StudioTrack,
    default_song_studio_document,
)


_NAMESPACE = uuid.UUID("e02b742d-bbc7-4482-9bb4-ae74ace3eed8")


def _id(label: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, label))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _create(
    tmp_path: Path,
    label: str = "Song Studio",
) -> tuple[Path, SongProject]:
    bundle = tmp_path / f"{label}.webjam"
    created = create_project_bundle(
        bundle,
        label,
        project_id=_id(f"project:{label}"),
    )
    project = created.project.add_track(
        "Lead Vocal",
        track_id=_id(f"track:{label}:vocal"),
    )
    saved = save_project_bundle(
        bundle,
        project,
        expected_token=created.token,
    )
    return bundle, saved.project


def _changed(document: StudioDocument, *, gain: float) -> StudioDocument:
    return document.update_track(
        document.tracks[0].track_id,
        fader_gain=gain,
    )


def test_default_save_reopen_and_valid_backup_are_schema3_and_path_free(
    tmp_path: Path,
) -> None:
    bundle, project = _create(tmp_path)

    loaded = load_song_studio_document(bundle, project)
    assert loaded.origin is SongStudioLoadOrigin.DEFAULT
    assert loaded.document == default_song_studio_document(project)
    assert loaded.token is None
    assert not (bundle / SONG_STUDIO_FILENAME).exists()

    first_document = _changed(loaded.document, gain=0.75)
    first = save_song_studio_document(
        bundle,
        project,
        first_document,
        expected_token=None,
    )
    first_bytes = first.path.read_bytes()
    assert first.token == _digest(first_bytes)
    assert first.backup_path is None

    second_document = _changed(first.document, gain=1.25)
    second = save_song_studio_document(
        bundle,
        project,
        second_document,
        expected_token=first.token,
    )
    assert second.backup_path == bundle / SONG_STUDIO_BACKUP_FILENAME
    assert second.backup_path.read_bytes() == first_bytes

    reopened = load_song_studio_document(bundle, project)
    assert reopened.origin is SongStudioLoadOrigin.PRIMARY
    assert reopened.document == second_document
    payload = json.loads(second.path.read_bytes())
    serialized = json.dumps(payload, sort_keys=True)
    assert payload["schema_version"] == 3
    assert "session_id" not in payload
    assert "take_id" not in payload
    assert '"path"' not in serialized
    assert "sha256" not in serialized
    assert '"media"' not in serialized


def test_primary_save_is_exact_byte_cas(tmp_path: Path) -> None:
    bundle, project = _create(tmp_path)
    initial = load_song_studio_document(bundle, project)
    first = save_song_studio_document(
        bundle,
        project,
        _changed(initial.document, gain=0.8),
        expected_token=None,
    )
    semantic_rewrite = (
        json.dumps(
            json.loads(first.path.read_bytes()),
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    first.path.write_bytes(semantic_rewrite)

    with pytest.raises(SongStudioConflict, match="changed after"):
        save_song_studio_document(
            bundle,
            project,
            _changed(first.document, gain=1.2),
            expected_token=first.token,
        )

    assert first.path.read_bytes() == semantic_rewrite


def test_corrupt_primary_uses_valid_backup_and_preserves_exact_damage_on_save(
    tmp_path: Path,
) -> None:
    bundle, project = _create(tmp_path)
    default = load_song_studio_document(bundle, project)
    first = save_song_studio_document(
        bundle,
        project,
        _changed(default.document, gain=0.8),
        expected_token=None,
    )
    second = save_song_studio_document(
        bundle,
        project,
        _changed(first.document, gain=1.2),
        expected_token=first.token,
    )
    valid_backup = second.backup_path.read_bytes()
    corrupt = b'{"schema_version":3,"project_id":'
    second.path.write_bytes(corrupt)

    recovered = load_song_studio_document(bundle, project)
    assert recovered.origin is SongStudioLoadOrigin.BACKUP
    assert recovered.needs_save is True
    assert recovered.document == first.document
    assert recovered.token == _digest(corrupt)
    assert second.path.read_bytes() == corrupt

    saved = save_song_studio_document(
        bundle,
        project,
        recovered.document,
        expected_token=recovered.token,
    )
    assert saved.preserved_corrupt_path is not None
    assert saved.preserved_corrupt_path.read_bytes() == corrupt
    assert (bundle / SONG_STUDIO_BACKUP_FILENAME).read_bytes() == valid_backup
    assert load_song_studio_document(bundle, project).document == recovered.document


def test_autosave_is_explicitly_recovered_or_discarded(tmp_path: Path) -> None:
    bundle, project = _create(tmp_path)
    default = load_song_studio_document(bundle, project)
    primary = save_song_studio_document(
        bundle,
        project,
        _changed(default.document, gain=0.9),
        expected_token=None,
    )
    dirty = _changed(primary.document, gain=1.4)
    autosave = write_song_studio_autosave(
        bundle,
        project,
        dirty,
        base_primary_token=primary.token,
    )
    primary_bytes = primary.path.read_bytes()

    offered = load_song_studio_document(bundle, project)
    assert offered.document == primary.document
    assert offered.recovery_candidate is not None
    assert offered.recovery_candidate.document == dirty
    assert offered.recovery_candidate.autosave_token == autosave.token
    assert primary.path.read_bytes() == primary_bytes

    recovered = recover_song_studio_autosave(
        bundle,
        project,
        expected_autosave_token=autosave.token,
    )
    assert recovered.document == dirty
    assert not (bundle / SONG_STUDIO_AUTOSAVE_FILENAME).exists()
    assert load_song_studio_document(bundle, project).document == dirty

    newer = _changed(recovered.document, gain=1.6)
    replacement = write_song_studio_autosave(
        bundle,
        project,
        newer,
        base_primary_token=recovered.token,
    )
    discard_song_studio_autosave(
        bundle,
        project,
        expected_token=replacement.token,
    )
    assert not (bundle / SONG_STUDIO_AUTOSAVE_FILENAME).exists()
    assert load_song_studio_document(bundle, project).document == dirty


def test_stale_autosave_is_preserved_until_explicit_discard(tmp_path: Path) -> None:
    bundle, project = _create(tmp_path)
    default = load_song_studio_document(bundle, project)
    primary = save_song_studio_document(
        bundle,
        project,
        _changed(default.document, gain=0.9),
        expected_token=None,
    )
    autosave = write_song_studio_autosave(
        bundle,
        project,
        _changed(primary.document, gain=1.3),
        base_primary_token=primary.token,
    )
    advanced = save_song_studio_document(
        bundle,
        project,
        _changed(primary.document, gain=1.1),
        expected_token=primary.token,
    )
    # Explicit primary save normally clears autosave; restore the old exact
    # envelope to simulate a crashed writer racing a later successful save.
    write_song_studio_autosave(
        bundle,
        project,
        _changed(advanced.document, gain=1.5),
        base_primary_token=advanced.token,
    )
    stale_bytes = (bundle / SONG_STUDIO_AUTOSAVE_FILENAME).read_bytes()
    value = json.loads(stale_bytes)
    value["base_primary_token"] = primary.token
    stale_bytes = (json.dumps(value) + "\n").encode()
    (bundle / SONG_STUDIO_AUTOSAVE_FILENAME).write_bytes(stale_bytes)

    loaded = load_song_studio_document(bundle, project)
    assert loaded.recovery_candidate is None
    assert loaded.autosave_requires_discard is True
    assert "stale" in loaded.recovery_notice.lower()
    assert (bundle / SONG_STUDIO_AUTOSAVE_FILENAME).read_bytes() == stale_bytes
    with pytest.raises(SongStudioConflict):
        recover_song_studio_autosave(
            bundle,
            project,
            expected_autosave_token=autosave.token,
        )
    discard_song_studio_autosave(bundle, project)
    assert not (bundle / SONG_STUDIO_AUTOSAVE_FILENAME).exists()


def test_schema2_and_project_or_rate_mismatches_are_rejected(tmp_path: Path) -> None:
    bundle, project = _create(tmp_path)
    legacy = StudioDocument(
        session_id=_id("legacy-session"),
        take_id=_id("legacy-take"),
        tracks=(StudioTrack(_id("legacy-track")),),
    )
    legacy_bytes = (json.dumps(legacy.to_dict()) + "\n").encode()
    song_studio_path(bundle).write_bytes(legacy_bytes)

    with pytest.raises(SongStudioStoreError, match="schema 3"):
        load_song_studio_document(bundle, project)
    assert song_studio_path(bundle).read_bytes() == legacy_bytes

    other_bundle, other_project = _create(tmp_path, "Other Song")
    with pytest.raises(SongStudioStoreError, match="identity"):
        load_song_studio_document(bundle, other_project)
    wrong_rate = replace(project, project_sample_rate=96_000)
    with pytest.raises(SongStudioStoreError, match="identity"):
        load_song_studio_document(bundle, wrong_rate)

    correct = default_song_studio_document(project)
    with pytest.raises(SongStudioStoreError, match="identity"):
        save_song_studio_document(
            other_bundle,
            other_project,
            correct,
            expected_token=None,
        )


def test_oversized_primary_with_backup_is_preserved_before_recovery_save(
    tmp_path: Path,
) -> None:
    bundle, project = _create(tmp_path)
    default = load_song_studio_document(bundle, project)
    first = save_song_studio_document(
        bundle,
        project,
        _changed(default.document, gain=0.8),
        expected_token=None,
    )
    second = save_song_studio_document(
        bundle,
        project,
        _changed(first.document, gain=1.2),
        expected_token=first.token,
    )
    oversized = b"x" * (MAX_SONG_STUDIO_AUTOSAVE_BYTES + 1)
    second.path.write_bytes(oversized)

    loaded = load_song_studio_document(bundle, project)
    assert loaded.origin is SongStudioLoadOrigin.BACKUP
    assert loaded.needs_save
    saved = save_song_studio_document(
        bundle,
        project,
        loaded.document,
        expected_token=loaded.token,
    )
    assert saved.preserved_corrupt_path is not None
    assert saved.preserved_corrupt_path.stat().st_size == len(oversized)
    assert load_song_studio_document(bundle, project).document == loaded.document


def test_symlinked_state_or_autosave_is_never_followed(tmp_path: Path) -> None:
    bundle, project = _create(tmp_path)
    outside = tmp_path / "private-outside.json"
    outside.write_bytes(b"outside")
    primary = song_studio_path(bundle)
    primary.symlink_to(outside)

    with pytest.raises(SongStudioStoreError, match="symbolic link"):
        load_song_studio_document(bundle, project)
    with pytest.raises(SongStudioStoreError, match="symbolic link"):
        save_song_studio_document(
            bundle,
            project,
            default_song_studio_document(project),
            expected_token=None,
        )
    assert primary.is_symlink()
    assert outside.read_bytes() == b"outside"

    primary.unlink()
    backup = bundle / SONG_STUDIO_BACKUP_FILENAME
    backup.symlink_to(outside)
    with pytest.raises(SongStudioStoreError, match="symbolic link"):
        save_song_studio_document(
            bundle,
            project,
            default_song_studio_document(project),
            expected_token=None,
        )
    assert backup.is_symlink()
    assert outside.read_bytes() == b"outside"
    backup.unlink()

    autosave = bundle / SONG_STUDIO_AUTOSAVE_FILENAME
    autosave.symlink_to(outside)
    with pytest.raises(SongStudioStoreError, match="symbolic link"):
        load_song_studio_document(bundle, project)
    with pytest.raises(SongStudioStoreError, match="symbolic link"):
        discard_song_studio_autosave(bundle, project)
    assert autosave.is_symlink()
    assert outside.read_bytes() == b"outside"


def test_oversized_autosave_is_bounded_and_can_be_explicitly_discarded(
    tmp_path: Path,
) -> None:
    bundle, project = _create(tmp_path)
    autosave = bundle / SONG_STUDIO_AUTOSAVE_FILENAME
    oversized = b"x" * (MAX_SONG_STUDIO_BYTES + 1)
    autosave.write_bytes(oversized)

    loaded = load_song_studio_document(bundle, project)
    assert loaded.document == default_song_studio_document(project)
    assert loaded.recovery_candidate is None
    assert loaded.autosave_requires_discard is True
    assert autosave.read_bytes() == oversized

    discard_song_studio_autosave(bundle, project)
    assert not autosave.exists()


def test_errors_do_not_echo_private_bundle_paths(tmp_path: Path) -> None:
    secret = "Jeff Private Demo Song"
    bundle, project = _create(tmp_path, secret)
    wrong = replace(project, project_id=_id("wrong-project"))

    with pytest.raises(SongStudioStoreError) as captured:
        load_song_studio_document(bundle, wrong)

    message = str(captured.value)
    assert secret not in message
    assert str(bundle) not in message
