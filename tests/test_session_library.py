"""Durable workspace behavior across independent readers and interrupted saves."""

from dataclasses import replace
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from core import file_io
from core import session_library as library_module
from core.session_library import (
    SessionLibrary,
    SessionLibraryConflict,
    SessionLibraryError,
)


def test_reopen_preserves_named_profile_workspace_and_all_payloads(tmp_path):
    library = SessionLibrary(tmp_path / "library")
    record = library.create(
        "music", "Friday songs", notes="Entire notes\nincluding trailing spaces  \n",
        mode_key="music_jam", decisions=("Play in D",), actions=("Send chart",),
        blockers=("Missing bass player",),
        recaps=({"duration_seconds": 612, "notes": "Keep the bridge"},),
        take_links=({"take_id": "take-1", "path": "/moved/take.wav"},),
        rehearsal={"version": 1, "songs": [{"title": "First song", "tempo": 94}]},
    )
    art = library.create(
        "art", "Clay study", notes="Glaze ideas", art={
            "references": [{"path": "../references/photo.png", "label": "Moved reference"}],
            "feedback": "Keep the green", "url": "https://example.invalid/video",
        },
    )
    reopened = SessionLibrary(tmp_path / "library")
    assert reopened.load(record.id) == record
    assert reopened.load(art.id, profile="art") == art
    assert reopened.load(art.id).art["references"][0]["path"] == "../references/photo.png"
    assert reopened.list(profile="music") == [record]
    assert reopened.list("GLAZE", profile="art") == [art]
    with pytest.raises(SessionLibraryError, match="different creator profile"):
        reopened.load(art.id, profile="music")


def test_edit_is_revision_safe_and_recent_search_tracks_saved_content(tmp_path):
    first = SessionLibrary(tmp_path).create("music", "Older", notes="one")
    second = SessionLibrary(tmp_path).create("music", "Newer", notes="two")
    reader = SessionLibrary(tmp_path)
    stale = reader.load(first.id)
    saved = SessionLibrary(tmp_path).save(replace(first, notes="CHORUS complete", title="Revisited"))
    assert saved.revision == 2
    assert reader.list() == [saved, second]
    assert reader.list("chorus") == [saved]
    with pytest.raises(SessionLibraryConflict):
        reader.save(replace(stale, notes="stale draft"))
    assert reader.load(first.id).notes == "CHORUS complete"


def test_same_revision_external_edit_cannot_be_overwritten(tmp_path):
    library = SessionLibrary(tmp_path)
    record = library.create("art", "Study", notes="mine")
    path = tmp_path / f"{record.id}.json"
    raw = json.loads(path.read_text())
    raw["notes"] = "changed by another tool"
    path.write_text(json.dumps(raw))
    with pytest.raises(SessionLibraryConflict):
        library.save(replace(record, notes="unsaved local draft"))
    assert library.load(record.id).notes == "changed by another tool"


def test_simultaneous_writers_have_one_winner(tmp_path):
    original = SessionLibrary(tmp_path).create("music", "Practice")

    def attempt(note):
        try:
            return SessionLibrary(tmp_path).save(replace(original, notes=note)).notes
        except SessionLibraryConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ("first edit", "second edit")))
    assert results.count("conflict") == 1
    assert SessionLibrary(tmp_path).load(original.id).notes in {"first edit", "second edit"}


def test_legacy_snapshot_import_is_idempotent_non_destructive_and_profile_scoped(tmp_path):
    legacy = tmp_path / "legacy-notes.md"
    legacy.write_text("Keep every line\nOriginal notes\n")
    original_bytes = legacy.read_bytes()
    library = SessionLibrary(tmp_path / "library")
    imported = library.import_snapshot("music", "Imported Music", legacy.read_text())
    updated = library.save(replace(imported, notes="New work after migration"))
    same = SessionLibrary(library.root).import_snapshot("music", "Different title", "different old content")
    art = library.import_snapshot("art", "Imported Art", legacy.read_text())
    assert same == updated
    assert art.id != imported.id
    assert art.notes == "Keep every line\nOriginal notes\n"
    assert legacy.read_bytes() == original_bytes
    assert len(library.list()) == 2


def test_concurrent_imports_create_only_one_workspace(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as executor:
        records = list(executor.map(
            lambda _: SessionLibrary(tmp_path).import_snapshot("art", "My Art", "Original"),
            range(2),
        ))
    assert records[0] == records[1]
    assert len(SessionLibrary(tmp_path).list()) == 1


def test_interrupted_primary_publication_retains_original_and_valid_backup(tmp_path, monkeypatch):
    library = SessionLibrary(tmp_path)
    record = library.create("music", "Practice", notes="Original")
    original_replace = file_io.os.replace

    def interrupted(source, destination):
        if Path(destination) == tmp_path / f"{record.id}.json":
            raise OSError("simulated interruption before rename")
        return original_replace(source, destination)

    monkeypatch.setattr(file_io.os, "replace", interrupted)
    with pytest.raises(OSError, match="interruption"):
        library.save(replace(record, notes="New draft"))
    assert SessionLibrary(tmp_path).load(record.id).notes == "Original"
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads((tmp_path / f"{record.id}.json.bak").read_text())["notes"] == "Original"


def test_fsync_failure_after_publication_requires_reload_and_rejects_stale_retry(tmp_path, monkeypatch):
    library = SessionLibrary(tmp_path)
    record = library.create("music", "Practice", notes="Original")
    calls = 0
    real_fsync = file_io._fsync_parent_directory

    def fail_primary_directory_fsync(parent):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("directory fsync failed after publication")
        real_fsync(parent)

    monkeypatch.setattr(file_io, "_fsync_parent_directory", fail_primary_directory_fsync)
    with pytest.raises(OSError, match="after publication"):
        library.save(replace(record, notes="Already published"))
    assert SessionLibrary(tmp_path).load(record.id).notes == "Already published"
    with pytest.raises(SessionLibraryConflict):
        library.save(replace(record, notes="Old record retry"))


def test_damaged_primary_offers_backup_without_writing_and_preserves_evidence_on_save(tmp_path):
    library = SessionLibrary(tmp_path)
    original = library.create("art", "Study", notes="first")
    library.save(replace(original, notes="second"))
    path = tmp_path / f"{original.id}.json"
    damaged = b'{"version":1,"truncated":'
    path.write_bytes(damaged)
    recovered = SessionLibrary(tmp_path).load(original.id)
    assert recovered.recovered
    assert recovered.notes == "first"
    assert path.read_bytes() == damaged
    assert library.list() == [recovered]
    assert "backup" in library.warnings[0]
    saved = library.save(replace(recovered, notes="Reviewed recovered work"))
    assert not saved.recovered
    assert library.load(saved.id).notes == "Reviewed recovered work"
    assert [p.read_bytes() for p in tmp_path.glob("*.damaged-*")] == [damaged]


def test_recovery_rejects_an_externally_changed_same_revision_backup(tmp_path):
    library = SessionLibrary(tmp_path)
    original = library.create("music", "Practice", notes="first")
    library.save(replace(original, notes="second"))
    path = tmp_path / f"{original.id}.json"
    path.write_bytes(b"damaged")
    recovered = library.load(original.id)
    backup = path.with_suffix(".json.bak")
    changed = json.loads(backup.read_text())
    changed["notes"] = "externally reviewed backup"
    backup.write_text(json.dumps(changed))
    with pytest.raises(SessionLibraryConflict):
        library.save(replace(recovered, notes="stale recovery"))
    assert library.load(original.id).notes == "externally reviewed backup"


def test_missing_primary_remains_discoverable_through_backup(tmp_path):
    library = SessionLibrary(tmp_path)
    original = library.create("music", "Practice", notes="first")
    library.save(replace(original, notes="second"))
    (tmp_path / f"{original.id}.json").unlink()
    records = library.list()
    assert len(records) == 1
    assert records[0].id == original.id
    assert records[0].recovered
    assert "backup" in library.warnings[0]
    assert library.save(replace(records[0], notes="restored")).notes == "restored"


@pytest.mark.parametrize("content", [b"broken JSON", b'{"version":1,"version":1}', b'{"x":NaN}'])
def test_unreadable_records_do_not_hide_valid_sessions_and_warn(tmp_path, content):
    library = SessionLibrary(tmp_path)
    good = library.create("music", "Keep this")
    bad = library.create("music", "Damaged")
    path = tmp_path / f"{bad.id}.json"
    path.write_bytes(content)
    assert library.list() == [good]
    assert len(library.warnings) == 1
    assert bad.id in library.warnings[0]
    assert path.read_bytes() == content


@pytest.mark.parametrize("payload", [
    {"notes": "x" * (library_module.MAX_RECOVERY_DRAFT_BYTES + 1)},
    {"art": {"bad": float("nan")}},
    {"take_links": ("wrong schema",)},
    {"rehearsal": {"path": Path("local-file")}},
    {"decisions": (4,)},
])
def test_invalid_or_oversized_save_preserves_exact_previous_bytes(tmp_path, payload):
    library = SessionLibrary(tmp_path)
    record = library.create("music", "Saved", notes="Keep exact bytes")
    path = tmp_path / f"{record.id}.json"
    before = path.read_bytes()
    with pytest.raises(SessionLibraryError):
        library.save(replace(record, **payload))
    assert path.read_bytes() == before


def test_nested_payload_is_bounded_before_serialization(tmp_path):
    payload = {}
    payload["cycle"] = payload
    with pytest.raises(SessionLibraryError, match="deeply nested"):
        SessionLibrary(tmp_path).create("art", "Cycle", art=payload)
    assert not list(tmp_path.glob("*.json"))


def test_profile_and_identity_cannot_be_changed_by_save(tmp_path):
    library = SessionLibrary(tmp_path)
    record = library.create("music", "A session")
    with pytest.raises(SessionLibraryError, match="different creator profile"):
        library.save(replace(record, profile="art"))
    with pytest.raises(SessionLibraryConflict):
        library.save(replace(record, source_key="fake-import"))
    with pytest.raises(SessionLibraryError):
        library.load("../../notes")
    assert library.load(record.id).profile == "music"


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes and symbolic links")
def test_store_is_private_and_refuses_linked_records_and_root(tmp_path):
    library = SessionLibrary(tmp_path / "library")
    record = library.create("art", "A private workspace")
    path = library.root / f"{record.id}.json"
    assert library.root.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    outside = tmp_path / "outside.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(SessionLibraryError, match="symbolic links"):
        library.load(record.id)
    assert library.list() == []
    assert library.warnings
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(library.root, target_is_directory=True)
    with pytest.raises(SessionLibraryError, match="symbolic links"):
        SessionLibrary(linked_root).create("art", "Wrong root")


def test_moved_reference_is_metadata_and_returned_objects_do_not_mutate_disk(tmp_path):
    library = SessionLibrary(tmp_path / "library")
    reference = tmp_path / "sketch.png"
    reference.write_bytes(b"image placeholder")
    record = library.create("art", "Drawing", art={"references": [{"path": str(reference)}]})
    reference.rename(tmp_path / "moved.png")
    loaded = library.load(record.id)
    assert loaded.art["references"][0]["path"] == str(reference)
    loaded.art["references"][0]["path"] = "an unsaved edit"
    assert library.load(record.id).art["references"][0]["path"] == str(reference)
