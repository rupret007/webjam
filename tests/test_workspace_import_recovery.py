"""Import journals survive actual store and process lifetimes without duplicates."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from core import session_library as store
from core.session_library import SessionLibrary, SessionLibraryError, SessionLibraryImportUnconfirmed, encode_session_record
from core.workspace_backup import export_workspace_backup, import_workspace_backup, prepare_workspace_backup_import
from tests import test_workspace_backup as backup_tests

record = backup_tests.record


@pytest.mark.parametrize("phase", ["journal-before", "journal-after", "primary-before", "primary-after", "ack-before", "ack-after"])
def test_failure_boundaries_retain_exact_evidence_and_retry_same_identity(tmp_path, record, monkeypatch, phase):
    library = SessionLibrary(tmp_path / "dest")
    preview = export_workspace_backup(record, tmp_path / "backup.json")
    prepared = prepare_workspace_backup_import(preview)
    expected = encode_session_record(prepared)
    real_write = store.atomic_write_bytes
    real_primary = library._publish_import_file
    real_replace = store.os.replace
    real_sync = store._fsync_parent_directory
    def write(path, data, **kwargs):
        kind = "journal" if Path(path).name == ".workspace-import.pending" else "primary"
        if phase == f"{kind}-before":
            raise OSError("disk full before publication")
        real_write(path, data, **kwargs)
        if phase == f"{kind}-after":
            raise OSError("directory sync failed after publication")
    def primary(path, data):
        if phase == "primary-before":
            raise OSError("disk full before publication")
        real_primary(path, data)
        if phase == "primary-after":
            raise OSError("directory sync failed after publication")
    def rename(source, target):
        if phase == "ack-before" and Path(target).name == ".workspace-import.completed":
            raise OSError("receipt rename failed")
        real_replace(source, target)
    def sync(path):
        if phase == "ack-after":
            raise OSError("receipt rename directory sync failed")
        real_sync(path)
    with monkeypatch.context() as faults:
        faults.setattr(store, "atomic_write_bytes", write)
        faults.setattr(library, "_publish_import_file", primary)
        faults.setattr(store.os, "replace", rename)
        faults.setattr(store, "_fsync_parent_directory", sync)
        with pytest.raises(SessionLibraryImportUnconfirmed) as caught:
            library.create_imported(prepared)
    assert caught.value.workspace_id == prepared.id
    assert caught.value.expected_sha256 == hashlib.sha256(expected).hexdigest()
    assert library.pending_import() == prepared
    saved = library.reconcile_import(prepared)
    published = phase in {"primary-after", "ack-before", "ack-after"}
    assert (saved is not None) == published
    # The journals are not library items. A failed initial journal cannot have
    # published the primary; after that boundary a new process has evidence.
    assert len(library.list()) == int(published)
    restarted = SessionLibrary(library.root)
    if phase == "journal-before":
        assert restarted.pending_import() is None
    elif phase == "ack-after":
        assert restarted.pending_import() is None
        assert (library.root / ".workspace-import.completed").read_bytes() == expected
    else:
        assert restarted.pending_import() == prepared
    if saved is None:
        library.prepare_import(prepared)
        saved = library.publish_import(prepared)
    library.acknowledge_import(prepared)
    assert library.pending_import() is None
    assert saved.id == prepared.id and len(library.list()) == 1
    assert (library.root / ".workspace-import.completed").read_bytes() == expected
    if os.name == "posix":
        assert ((library.root / ".workspace-import.completed").stat().st_mode & 0o777) == 0o600
    # Completed evidence is historical. Edits are normal; a later new import
    # must never require that this older workspace still equals revision one.
    library.save(replace(saved, notes="Continue after recovery"))
    second = import_workspace_backup(restarted, preview)
    assert second.id != saved.id and len(library.list()) == 2


def test_interrupted_import_reconciles_and_retries_in_fresh_os_process(tmp_path, record):
    library = SessionLibrary(tmp_path / "dest")
    prepared = prepare_workspace_backup_import(export_workspace_backup(record, tmp_path / "backup.json"))
    library.prepare_import(prepared)  # Stop between durable journal and primary publication.
    script = """
import hashlib, json, sys
from core.session_library import SessionLibrary, encode_session_record
library = SessionLibrary(sys.argv[1])
pending = library.pending_import()
assert pending is not None
assert library.reconcile_import(pending) is None
saved = library.publish_import(pending)
library.acknowledge_import(pending)
assert library.pending_import() is None
print(json.dumps({'id': saved.id, 'sha256': hashlib.sha256(encode_session_record(saved)).hexdigest(),
                  'count': len(library.list())}))
"""
    completed = subprocess.run([sys.executable, "-c", script, str(library.root)], check=True,
                               capture_output=True, text=True, timeout=15)
    result = json.loads(completed.stdout)
    assert result == {"id": prepared.id, "sha256": hashlib.sha256(encode_session_record(prepared)).hexdigest(), "count": 1}
    assert SessionLibrary(library.root).load(prepared.id).notes == record.notes


@pytest.mark.parametrize("damage", ["primary-changed", "backup-present", "journal-damaged", "journal-symlink", "journal-oversized"])
def test_uninspectable_or_changed_evidence_blocks_duplicate_retry(tmp_path, record, damage):
    library = SessionLibrary(tmp_path / "dest")
    preview = export_workspace_backup(record, tmp_path / "backup.json")
    prepared = prepare_workspace_backup_import(preview)
    library.prepare_import(prepared)
    journal = library.root / ".workspace-import.pending"
    if damage == "primary-changed":
        (library.root / f"{prepared.id}.json").write_bytes(encode_session_record(replace(prepared, notes="Changed externally")))
    elif damage == "backup-present":
        (library.root / f"{prepared.id}.json.bak").write_bytes(encode_session_record(prepared))
    elif damage == "journal-damaged":
        journal.write_bytes(b"damaged evidence")
    elif damage == "journal-oversized":
        journal.write_bytes(b"x" * (store.MAX_SESSION_RECORD_BYTES + 1))
    else:
        journal.unlink()
        try:
            journal.symlink_to(tmp_path / "backup.json")
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                pytest.skip("Windows symbolic link privilege unavailable")
            raise
    before = {path.name: path.read_bytes() for path in library.root.iterdir() if path.is_file()}
    with pytest.raises(SessionLibraryError):
        library.reconcile_import(prepared)
    with pytest.raises(SessionLibraryError):
        import_workspace_backup(library, preview)
    after = {path.name: path.read_bytes() for path in library.root.iterdir() if path.is_file()}
    assert after == before


def test_one_bounded_pending_import_refuses_second_preparation_without_overwrite(tmp_path, record):
    library = SessionLibrary(tmp_path / "dest")
    preview = export_workspace_backup(record, tmp_path / "backup.json")
    one = prepare_workspace_backup_import(preview)
    two = prepare_workspace_backup_import(preview)
    library.prepare_import(one)
    original = (library.root / ".workspace-import.pending").read_bytes()
    with pytest.raises(SessionLibraryError, match="previous import"):
        library.prepare_import(two)
    assert (library.root / ".workspace-import.pending").read_bytes() == original
    assert library.list() == []


def test_failed_journal_memory_snapshot_cannot_be_changed_by_pending_reader(tmp_path, record, monkeypatch):
    library = SessionLibrary(tmp_path / "dest")
    prepared = prepare_workspace_backup_import(export_workspace_backup(record, tmp_path / "backup.json"))
    def fail(*_args, **_kwargs):
        raise OSError("disk full")
    with monkeypatch.context() as fault:
        fault.setattr(store, "atomic_write_bytes", fail)
        with pytest.raises(SessionLibraryImportUnconfirmed):
            library.prepare_import(prepared)
    leaked = library.pending_import()
    leaked.take_links[0]["take_id"] = "substitute"
    leaked.import_provenance[0]["payload_sha256"] = "0" * 64
    assert library.pending_import() == prepared
    assert library.reconcile_import(prepared) is None
    library.prepare_import(library.pending_import())
    saved = library.publish_import(prepared)
    library.acknowledge_import(prepared)
    assert saved.take_links == prepared.take_links
    assert saved.id == prepared.id


def test_acknowledgement_retry_rechecks_completed_receipt_and_retries_directory_sync(tmp_path, record, monkeypatch):
    library = SessionLibrary(tmp_path / "dest")
    prepared = prepare_workspace_backup_import(export_workspace_backup(record, tmp_path / "backup.json"))
    library.prepare_import(prepared)
    library.publish_import(prepared)
    attempts = []
    real_sync = store._fsync_parent_directory
    def sync(path):
        attempts.append(path)
        if len(attempts) == 1:
            raise OSError("directory sync failed after receipt rename")
        real_sync(path)
    monkeypatch.setattr(store, "_fsync_parent_directory", sync)
    with pytest.raises(SessionLibraryImportUnconfirmed):
        library.acknowledge_import(prepared)
    assert not (library.root / ".workspace-import.pending").exists()
    receipt = library.root / ".workspace-import.completed"
    expected = receipt.read_bytes()
    receipt.write_bytes(encode_session_record(replace(prepared, notes="unexpected replacement")))
    with pytest.raises(SessionLibraryError, match="receipt is missing or changed"):
        library.acknowledge_import(prepared)
    assert attempts == [library.root]
    receipt.write_bytes(expected)
    library.acknowledge_import(prepared)
    assert attempts == [library.root, library.root]
    assert library.pending_import() is None
