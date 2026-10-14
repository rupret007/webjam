"""Retry an uncertain workspace create before allocating another ID."""
import json
from unittest.mock import patch

import pytest

from core import file_io
from core.session_library import SessionLibrary, SessionLibraryConflict
from tests.support.workspace_save_faults import fail_create_sync


def _create_unconfirmed():
    from core import session_library as module
    return getattr(module, "SessionLibraryCreateUnconfirmed", OSError)


def failed_create(library, *args, **kwargs):
    with fail_create_sync(), pytest.raises(OSError) as failure:
        library.create(*args, **kwargs)
    return failure.value


def test_post_publication_create_stays_unconfirmed_then_retry_confirms_one(tmp_path):
    library = SessionLibrary(tmp_path)
    error = failed_create(library, "music", "Friday rehearsal", notes="Keep this work")
    listed = library.list()
    assert len(listed) == 1
    intended = listed[0]
    assert intended.notes == "Keep this work"
    path = tmp_path / f"{intended.id}.json"
    published = path.read_bytes()
    assert isinstance(error, _create_unconfirmed())
    with pytest.raises(OSError) as failure:
        with patch.object(file_io, "_fsync_parent_directory", side_effect=OSError("still unavailable")):
            library.create("music", "Friday rehearsal", notes="Keep this work")
    assert library.list()[0].id == intended.id
    assert path.read_bytes() == published
    assert isinstance(failure.value, _create_unconfirmed())
    with patch.object(file_io, "_fsync_parent_directory", wraps=file_io._fsync_parent_directory) as sync:
        saved = library.create("music", "A different typed title", notes="Ignored on retry")
    sync.assert_called_once_with(tmp_path)
    assert saved.id == intended.id
    assert saved.title == "Friday rehearsal"
    assert saved.notes == "Keep this work"
    assert saved._store_token == library.load(intended.id)._store_token
    assert path.read_bytes() == published
    assert len(library.list()) == 1


def test_pre_publication_create_failure_remains_retryable(tmp_path, monkeypatch):
    library = SessionLibrary(tmp_path)
    original_replace = file_io.os.replace

    def interrupted(source, destination):
        raise OSError("simulated interruption before rename")

    monkeypatch.setattr(file_io.os, "replace", interrupted)
    with pytest.raises(OSError):
        library.create("music", "Friday rehearsal", notes="Keep this work")
    assert library.list() == []
    assert not list(tmp_path.glob("*.json"))
    monkeypatch.setattr(file_io.os, "replace", original_replace)
    saved = library.create("music", "Friday rehearsal", notes="Keep this work")
    assert saved.notes == "Keep this work"
    assert len(library.list()) == 1


def test_replaced_create_bytes_are_preserved_and_never_adopted(tmp_path):
    library = SessionLibrary(tmp_path)
    failed_create(library, "music", "Friday rehearsal", notes="Keep this work")
    intended = library.list()[0]
    path = tmp_path / f"{intended.id}.json"
    published = path.read_bytes()
    payload = json.loads(published)
    payload["updated_at"] = "2099-01-01T00:00:00.000000+00:00"
    replacement = json.dumps(payload).encode()
    assert payload["title"] == "Friday rehearsal"
    assert payload["notes"] == "Keep this work"
    path.write_bytes(replacement)
    with pytest.raises(SessionLibraryConflict, match="changed"):
        library.create("music", "Friday rehearsal", notes="Keep this work")
    assert path.read_bytes() == replacement
    assert path.read_bytes() != published
    second = library.create("art", "Separate later workspace")
    assert second.id != intended.id
    assert path.read_bytes() == replacement
    assert {record.id for record in library.list()} == {intended.id, second.id}


def test_create_backup_evidence_is_preserved_and_blocks_retry(tmp_path):
    library = SessionLibrary(tmp_path)
    failed_create(library, "art", "Study", notes="Keep")
    intended = library.list()[0]
    path = tmp_path / f"{intended.id}.json"
    published = path.read_bytes()
    path.unlink()
    backup = path.with_suffix(".json.bak")
    backup.write_bytes(published)
    with pytest.raises(SessionLibraryConflict, match="recovery or newer evidence"):
        library.create("art", "Study", notes="Keep")
    assert backup.read_bytes() == published
    assert not path.exists()


def test_separate_explicit_creates_remain_distinct(tmp_path):
    library = SessionLibrary(tmp_path)
    first = library.create("music", "Friday rehearsal", notes="Keep this work")
    second = library.create("music", "Friday rehearsal", notes="Keep this work")
    assert first.id != second.id
    assert {record.id for record in library.list()} == {first.id, second.id}


def test_create_retry_receipt_cannot_authorize_another_identity(tmp_path):
    library = SessionLibrary(tmp_path)
    error = failed_create(library, "music", "Song")
    attempt = getattr(error, "attempt", None)
    assert attempt is not None
    other = SessionLibrary(tmp_path / "other").create("music", "Other")
    wrong = type(attempt)(
        workspace_id=other.id,
        profile="music",
        data=(tmp_path / "other" / f"{other.id}.json").read_bytes(),
    )
    with pytest.raises(SessionLibraryConflict, match="no longer belongs"):
        library.create("music", "Song", retry=wrong)
    assert library.list()[0].id == attempt.workspace_id
