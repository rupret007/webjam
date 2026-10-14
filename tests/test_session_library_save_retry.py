"""Only an owning save receipt can reconcile a post-publication failure."""
from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from core import file_io
from core.session_library import SessionLibrary, SessionLibraryConflict, SessionLibrarySaveUnconfirmed
from tests.support.workspace_save_faults import fail_primary_sync


def failed_attempt(library, draft):
    with fail_primary_sync(), pytest.raises(SessionLibrarySaveUnconfirmed) as failure:
        library.save(draft)
    return failure.value.attempt


def test_post_primary_retry_confirms_durability_without_revision_or_backup_churn(tmp_path):
    library = SessionLibrary(tmp_path)
    base = library.create("art", "Study", notes="Original")
    path = tmp_path / f"{base.id}.json"
    original = path.read_bytes()
    draft = replace(base, notes="Attempted", art={"brief": "Keep the blue"})
    attempt = failed_attempt(library, draft)
    published = path.read_bytes()
    backup = path.with_suffix(".json.bak")
    assert backup.read_bytes() == original
    # An unrelated stale caller, even using the same store, has no receipt.
    with pytest.raises(SessionLibraryConflict):
        library.save(draft)
    with patch.object(file_io, "_fsync_parent_directory", side_effect=OSError("still unavailable")):
        with pytest.raises(SessionLibrarySaveUnconfirmed) as failure:
            library.save(draft, retry=attempt)
    assert failure.value.attempt == attempt
    assert path.read_bytes() == published
    with patch.object(file_io, "_fsync_parent_directory", wraps=file_io._fsync_parent_directory) as sync:
        saved = library.save(draft, retry=attempt)
    sync.assert_called_once_with(tmp_path)
    assert saved.revision == base.revision + 1
    assert saved._store_token == library.load(base.id)._store_token
    assert path.read_bytes() == published
    assert backup.read_bytes() == original
    assert len(library.list()) == 1


@pytest.mark.parametrize("field,value", [
    ("title", "External title"), ("revision", 9), ("notes", "Other notes"),
    ("art", {"brief": "Other Art"}), ("rehearsal", {"version": 1, "songs": []}),
    ("take_links", [{"take_id": "other"}]), ("recaps", [{"notes": "Other recap"}]),
    ("source_key", "other-owner"), ("profile", "music"),
])
def test_post_primary_retry_rejects_any_external_payload_change(tmp_path, field, value):
    library = SessionLibrary(tmp_path)
    base = library.create("art", "Study")
    draft = replace(base, notes="My draft")
    attempt = failed_attempt(library, draft)
    path = tmp_path / f"{base.id}.json"
    payload = json.loads(path.read_bytes())
    payload[field] = value
    external = json.dumps(payload).encode()
    path.write_bytes(external)
    backup = path.with_suffix(".json.bak").read_bytes()
    with pytest.raises(ValueError):
        library.save(draft, retry=attempt)
    assert path.read_bytes() == external
    assert path.with_suffix(".json.bak").read_bytes() == backup


def test_post_primary_receipt_cannot_authorize_another_owner_snapshot(tmp_path):
    library = SessionLibrary(tmp_path)
    base = library.create("music", "Song")
    draft = replace(base, notes="My draft")
    attempt = failed_attempt(library, draft)
    other = library.create("music", "Other")
    for wrong in (other, library.load(base.id), replace(base, _store_token=None)):
        with pytest.raises(SessionLibraryConflict):
            library.save(wrong, retry=attempt)


@pytest.mark.parametrize("failure_stage", ["before_rename", "after_sync"])
def test_post_primary_newer_edit_failure_remains_retryable(tmp_path, failure_stage):
    library = SessionLibrary(tmp_path)
    base = library.create("music", "Song", notes="Original")
    attempt = failed_attempt(library, replace(base, notes="Attempted"))
    draft = replace(base, notes="Newer", title="Renamed", take_links=({"take_id": "late"},))
    from core import session_library as module
    real_write = module.atomic_write_bytes
    primary_writes = 0

    def write(path, data, **kwargs):
        nonlocal primary_writes
        if path.suffix == ".json":
            primary_writes += 1
            if primary_writes == 2:
                if failure_stage == "after_sync":
                    real_write(path, data, **kwargs)
                raise OSError("second publication failed")
        return real_write(path, data, **kwargs)

    with patch.object(module, "atomic_write_bytes", side_effect=write):
        with pytest.raises(SessionLibrarySaveUnconfirmed) as failure:
            library.save(draft, retry=attempt)
    saved = library.save(draft, retry=failure.value.attempt)
    assert saved.notes == "Newer"
    assert saved.title == "Renamed"
    assert saved.take_links == ({"take_id": "late"},)
    assert saved.revision == 3
    assert len(library.list()) == 1


def test_post_primary_recovered_save_preserves_original_backup_and_damage(tmp_path):
    library = SessionLibrary(tmp_path)
    base = library.create("art", "Study", notes="Original")
    library.save(replace(base, notes="Second"))
    path = tmp_path / f"{base.id}.json"
    damaged = b'{"broken":'
    path.write_bytes(damaged)
    recovered = library.load(base.id)
    backup = path.with_suffix(".json.bak").read_bytes()
    draft = replace(recovered, notes="Reviewed")
    attempt = failed_attempt(library, draft)
    saved = library.save(draft, retry=attempt)
    assert saved.notes == "Reviewed"
    assert not saved.recovered
    assert path.with_suffix(".json.bak").read_bytes() == backup
    assert [p.read_bytes() for p in tmp_path.glob("*.damaged-*")] == [damaged]


def test_post_primary_retry_rejects_change_between_snapshot_and_byte_check(tmp_path):
    library = SessionLibrary(tmp_path)
    base = library.create("music", "Song")
    draft = replace(base, notes="Attempted")
    attempt = failed_attempt(library, draft)
    path = tmp_path / f"{base.id}.json"
    library.save(replace(library.load(base.id), title="External version"))
    real_load = library._load

    def changed_during_load(*args):
        current = real_load(*args)
        path.write_bytes(attempt.data)
        return current

    with patch.object(library, "_load", side_effect=changed_during_load), \
            patch("core.session_library.atomic_write_bytes") as write:
        with pytest.raises(SessionLibraryConflict, match="during save retry"):
            library.save(draft, retry=attempt)
    write.assert_not_called()
