"""Portable media exercises real codecs, storage, restart and changed-file gates."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
from uuid import uuid4
import wave
import zipfile

import pytest

from core.art_workspace import normalize_art_workspace
from core.session_library import (
    SessionLibrary, SessionLibraryError, SessionLibraryMediaImportRequired,
    SessionLibraryImportUnconfirmed, encode_session_record, decode_session_record,
)
from core import workspace_media_backup as media
from core.workspace_backup import export_workspace_backup, preview_workspace_backup, import_workspace_backup
from core.workspace_media_schema import relative_path


def _digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def art(tmp_path):
    path = tmp_path / "painting.png"
    path.write_bytes(b"owned artwork\x00\xff")
    record = SessionLibrary(tmp_path / "original").create("art", "Painting", notes="literal notes",
        art=normalize_art_workspace({"version": 1, "references": [
            {"id": "painting", "kind": "file", "locator": str(path), "title": "Painting"},
            {"id": "lesson", "kind": "url", "locator": "https://example.invalid/lesson", "title": "Lesson"}],
            "bookmarks": [{"id": "mark", "reference_id": "painting", "seconds": 12, "note": "Keep"}]}))
    return record, path


def _package(tmp_path, record, selection=None, name="work.webjambackup"):
    selection = selection or media.WorkspacePackageSelection(art_reference_ids=("painting",))
    plan = media.plan_workspace_package(record, selection)
    assert not plan.blockers, plan.blockers
    path = tmp_path / name
    receipt = media.export_workspace_package(plan, path)
    assert receipt.sha256 == _digest(path.read_bytes())
    return media.preview_workspace_package(path)


def _take(root, *, session_id=None, label="first", amplitude=100):
    from core.take_project import MediaSegment, MediaStatus, ProjectTrack, TakeProject, ProjectStatus, SourceType, SourceQuality, write_take_project
    directory = root / label
    directory.mkdir()
    audio = directory / "audio.wav"
    with wave.open(str(audio), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(48000)
        writer.writeframes(struct.pack("<h", amplitude) * 4800)
    segment = MediaSegment(str(uuid4()), "audio.wav", 0, 4800, 48000, 1, "PCM_16",
                           sha256=_digest(audio.read_bytes()), size_bytes=audio.stat().st_size)
    track = ProjectTrack(str(uuid4()), str(uuid4()), None, "Guitar", "", SourceType.JAMULUS_SERVER,
                         SourceQuality.NETWORK_TRACK, MediaStatus.AVAILABLE, order=0, segments=(segment,))
    project = TakeProject(session_id or str(uuid4()), str(uuid4()), "Session", label, ProjectStatus.COMPLETE,
                          48000, (), (track,))
    write_take_project(directory, project)
    from core.take_library import load_take
    from core.take_review import load_take_review, save_take_review
    from core.studio_store import load_studio_document, save_studio_document
    take = load_take(directory)
    assert take.validation_status == "complete" and not take.manifest_errors
    save_take_review(take, replace(load_take_review(take), notes="Saved review", favorite=True))
    state = load_studio_document(directory)
    save_studio_document(directory, state.document, expected_token=state.token)
    return directory, project


def test_art_package_roundtrip_restart_copy_reexport_and_original_unchanged(tmp_path, art):
    record, original = art
    before = original.read_bytes()
    preview = _package(tmp_path, record)
    destination = SessionLibrary(tmp_path / "restored")
    imported = media.import_workspace_package(destination, preview)
    assert imported.id != record.id and imported.source_key == record.source_key
    assert imported.import_provenance[-1]["source_workspace_id"] == record.id
    assert imported.art["bookmarks"] == record.art["bookmarks"]
    assert json.loads(encode_session_record(imported))["version"] == 3
    restored = Path(imported.art["references"][0]["locator"])
    assert restored.read_bytes() == before and original.read_bytes() == before
    assert media.verify_workspace_media(imported, "art", "painting").matches_expected_content
    restarted = SessionLibrary(destination.root).load(imported.id)
    assert restarted.media_provenance == imported.media_provenance
    metadata = tmp_path / "metadata.json"
    export_workspace_backup(restarted, metadata)
    copied = import_workspace_backup(SessionLibrary(tmp_path / "copy"), preview_workspace_backup(metadata))
    assert copied.media_provenance == imported.media_provenance
    assert media.verify_workspace_media(copied, "art", "painting").sha256 == _digest(before)
    _package(tmp_path, copied, name="again.webjambackup")
    assert original.read_bytes() == before


def test_candidate_listing_never_stats_references(tmp_path, art, monkeypatch):
    record, _ = art
    monkeypatch.setattr(Path, "lstat", lambda *_a, **_k: pytest.fail("passive filesystem access"))
    rows = media.workspace_backup_candidates(record)
    assert len(rows) == 2 and rows[0].selectable and not rows[1].selectable


def test_changed_art_cannot_be_relinked_or_rebacked_as_same_content(tmp_path, art):
    record, _ = art
    imported = media.import_workspace_package(SessionLibrary(tmp_path / "library"), _package(tmp_path, record))
    restored = Path(imported.art["references"][0]["locator"])
    restored.write_bytes(b"different content")
    with pytest.raises(media.WorkspaceBackupError):
        media.verify_workspace_media(imported, "art", "painting")
    plan = media.plan_workspace_package(imported, media.WorkspacePackageSelection(art_reference_ids=("painting",)))
    assert plan.blockers and not plan.exportable
    with pytest.raises(media.WorkspaceBackupError):
        media.export_workspace_package(plan, tmp_path / "bad.webjambackup")


def test_verified_art_relink_updates_only_locator_and_preserves_bookmarks(tmp_path, art):
    record, _ = art
    imported = media.import_workspace_package(SessionLibrary(tmp_path / "library"), _package(tmp_path, record))
    original = Path(imported.art["references"][0]["locator"])
    replacement = tmp_path / "same.png"
    shutil.copyfile(original, replacement)
    checked = media.verify_workspace_media(imported, "art", "painting", locator=replacement)
    edited = media.relink_workspace_media(imported, checked)
    assert edited.art["references"][0]["locator"] == str(replacement)
    assert edited.media_provenance == imported.media_provenance
    assert edited.art["bookmarks"] == imported.art["bookmarks"]
    assert imported.art["references"][0]["locator"] == str(original)


def test_art_removal_retains_dormant_proof_and_saves(tmp_path, art):
    record, _ = art
    library = SessionLibrary(tmp_path / "library")
    imported = media.import_workspace_package(library, _package(tmp_path, record))
    edited_art = dict(imported.art, references=[imported.art["references"][1]], bookmarks=[])
    saved = library.save(replace(imported, art=edited_art))
    assert saved.media_provenance == imported.media_provenance
    with pytest.raises(media.WorkspaceBackupError):
        media.verify_workspace_media(saved, "art", "painting")


def test_two_real_take_consumers_roundtrip_with_exact_manifests_reviews_and_edits(tmp_path):
    first, p1 = _take(tmp_path)
    second, p2 = _take(tmp_path, session_id=p1.session_id, label="second", amplitude=500)
    roots = (first, second)
    original = {str(path): path.read_bytes() for root in roots for path in root.iterdir() if path.is_file()}
    links = tuple({"take_id": p.take_id, "take_path": str(root), "source_identity": _digest((root / "webjam-take.json").read_bytes()),
                   "recording_session_id": "historical-owner", "validated": True, "status": "complete"} for root, p in zip(roots, (p1, p2)))
    record = SessionLibrary(tmp_path / "source").create("music", "Two takes", take_links=links)
    preview = _package(tmp_path, record, media.WorkspacePackageSelection(take_ids=(p1.take_id, p2.take_id)))
    restored = media.import_workspace_package(SessionLibrary(tmp_path / "library"), preview)
    from core.take_review import load_take_review
    from core.studio_store import load_studio_document, save_studio_document
    for ref, root in zip(restored.take_links, roots):
        assert "recording_session_id" not in ref and "validated" not in ref
        checked = media.verify_workspace_media(restored, "take", ref["take_id"])
        assert checked.take.take_id == ref["take_id"]
        assert load_take_review(checked.take).notes == "Saved review"
        state = load_studio_document(checked.take.path)
        assert state.document.take_id == ref["take_id"]
        assert (checked.take.path / "webjam-take.json").read_bytes() == (root / "webjam-take.json").read_bytes()
        # A legitimate post-restore edit does not invalidate immutable audio.
        save_studio_document(checked.take.path, replace(state.document, master=replace(state.document.master, gain=0.5)), expected_token=state.token)
        assert media.verify_workspace_media(restored, "take", ref["take_id"]).take.take_id == ref["take_id"]
    assert {name: Path(name).read_bytes() for name in original} == original


@pytest.mark.parametrize("name", ["../x", "/x", "a//b", "a/./b", "a\\b", "C:x", "CON", "COM¹.wav", "a?b", "x.", "e\u0301.wav"])
def test_cross_platform_member_names_are_rejected(name):
    with pytest.raises(ValueError):
        relative_path(name)


def test_source_replacement_after_plan_refuses_new_backup(tmp_path, art):
    record, source = art
    plan = media.plan_workspace_package(record, media.WorkspacePackageSelection(art_reference_ids=("painting",)))
    source.write_bytes(b"changed")
    target = tmp_path / "backup.webjambackup"
    with pytest.raises(media.WorkspaceBackupError):
        media.export_workspace_package(plan, target)
    assert not target.exists()


def test_existing_destination_never_overwritten(tmp_path, art):
    record, _ = art
    plan = media.plan_workspace_package(record, media.WorkspacePackageSelection(art_reference_ids=("painting",)))
    target = tmp_path / "keep"
    target.write_bytes(b"keep")
    with pytest.raises(media.WorkspaceBackupError):
        media.export_workspace_package(plan, target)
    assert target.read_bytes() == b"keep"


def test_cancel_keeps_journal_generic_retry_cannot_publish_partial_media(tmp_path, art):
    record, _ = art
    preview = _package(tmp_path, record)
    library = SessionLibrary(tmp_path / "library")
    with pytest.raises(media.WorkspacePackageCancelled):
        media.import_workspace_package(library, preview, cancel_check=lambda: True)
    pending = SessionLibrary(library.root).pending_import()
    assert pending is not None
    assert library.pending_import_has_media()
    for method in (library.publish_import, library.reconcile_import, library.acknowledge_import, library.prepare_import):
        with pytest.raises(SessionLibraryMediaImportRequired):
            method(pending)
    check = media.reconcile_workspace_package_import(library)
    assert check.state == "partial" and not check.can_retry
    imported = media.import_workspace_package(SessionLibrary(library.root), preview, retry=True)
    assert imported.id == pending.id and len(library.list()) == 1


def test_prepare_return_cannot_mutate_persisted_or_fallback_snapshot(tmp_path, art):
    record, _ = art
    library = SessionLibrary(tmp_path / "library")
    prepared = media.prepare_workspace_package_import(library, _package(tmp_path, record))
    prepared.art["references"][0]["locator"] = "poison"
    assert library.pending_import().art["references"][0]["locator"] != "poison"


def test_publication_fault_recovers_exact_id_without_duplicate(tmp_path, art, monkeypatch):
    record, _ = art
    library = SessionLibrary(tmp_path / "library")
    preview = _package(tmp_path, record)
    write = library._publish_import_file
    intended = []
    def fail_after_publish(path, data, **kwargs):
        write(path, data, **kwargs)
        if path.name.endswith(".json") and path.parent == library.root:
            intended.append(path.stem)
            raise OSError("parent sync failed")
    monkeypatch.setattr(library, "_publish_import_file", fail_after_publish)
    with pytest.raises(SessionLibraryImportUnconfirmed):
        media.import_workspace_package(library, preview)
    monkeypatch.setattr(library, "_publish_import_file", write)
    restarted = SessionLibrary(library.root)
    outcome = media.reconcile_workspace_package_import(restarted)
    assert outcome.state == "published" and outcome.record.id == intended[0]
    saved = media.reconcile_workspace_package_import(restarted, retry=True)
    assert saved.id == intended[0] and len(restarted.list()) == 1


def test_acknowledgement_sync_failure_reconciles_completed_receipt_after_restart(tmp_path, art, monkeypatch):
    record, _ = art
    library = SessionLibrary(tmp_path / "library")
    preview = _package(tmp_path, record)
    sync = media._fsync_parent_directory
    def fail_final(path):
        if path == library.root and (path / ".workspace-import.completed").exists():
            raise OSError("ack sync")
        return sync(path)
    monkeypatch.setattr(media, "_fsync_parent_directory", fail_final)
    with pytest.raises(SessionLibraryImportUnconfirmed):
        media.import_workspace_package(library, preview)
    monkeypatch.setattr(media, "_fsync_parent_directory", sync)
    restarted = SessionLibrary(library.root)
    assert media.reconcile_workspace_package_import(restarted).state == "published"
    saved = media.reconcile_workspace_package_import(restarted, retry=True)
    assert len(restarted.list()) == 1 and saved.id == restarted.list()[0].id


def test_fresh_process_resume_uses_prepared_id_and_declared_owned_root(tmp_path, art):
    record, _ = art
    preview = _package(tmp_path, record)
    library = SessionLibrary(tmp_path / "library")
    prepared = media.prepare_workspace_package_import(library, preview)
    command = """from core.session_library import SessionLibrary
from core.workspace_media_backup import preview_workspace_package, import_workspace_package, verify_workspace_media
import sys
record = import_workspace_package(SessionLibrary(sys.argv[1]), preview_workspace_package(sys.argv[2]), retry=True)
assert record.id == sys.argv[3]
assert verify_workspace_media(record, 'art', 'painting').matches_expected_content
print(record.id)
"""
    result = subprocess.run([sys.executable, "-c", command, str(library.root), str(preview.path), prepared.id],
                            capture_output=True, text=True, timeout=30, check=True)
    assert result.stdout.strip() == prepared.id


def test_huge_central_directory_rejected_before_zipfile_allocates(tmp_path, monkeypatch):
    path = tmp_path / "bomb.zip"
    path.write_bytes(struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 6000, 6000, 6000 * 46, 0, 0))
    monkeypatch.setattr(media.zipfile, "ZipFile", lambda *_a, **_k: pytest.fail("ZIP constructor reached"))
    with pytest.raises(media.WorkspaceBackupError, match="central directory"):
        media.preview_workspace_package(path)


def test_changed_package_after_preview_cannot_extract(tmp_path, art):
    record, _ = art
    preview = _package(tmp_path, record)
    data = preview.path.read_bytes()
    preview.path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    library = SessionLibrary(tmp_path / "library")
    with pytest.raises(media.WorkspaceBackupError):
        media.import_workspace_package(library, preview)
    assert not library.list()


@pytest.mark.parametrize("mutation", [
    lambda a: a.update(kind=[]), lambda a: a.update(reference_id=[]),
    lambda a: a.update(files=[None]), lambda a: a["files"][0].update(role=[]),
])
def test_malformed_asset_types_are_domain_errors(tmp_path, art, mutation):
    record, _ = art
    preview = _package(tmp_path, record)
    with zipfile.ZipFile(preview.path) as archive:
        files = {i.filename: archive.read(i) for i in archive.infolist()}
    manifest = json.loads(files["workspace.json"])
    mutation(manifest["assets"][0])
    files["workspace.json"] = json.dumps(manifest).encode()
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in files.items():
            archive.writestr(media._zip_info(name), data)
    with pytest.raises((media.WorkspaceBackupError, SessionLibraryError)):
        media.preview_workspace_package(broken)


def test_v1_and_v2_codec_stay_byte_compatible(tmp_path, art):
    record, _ = art
    v1 = encode_session_record(record)
    assert json.loads(v1)["version"] == 1 and encode_session_record(decode_session_record(v1)) == v1
    path = tmp_path / "metadata.json"
    export_workspace_backup(record, path)
    imported = import_workspace_backup(SessionLibrary(tmp_path / "library"), preview_workspace_backup(path))
    v2 = encode_session_record(imported)
    assert json.loads(v2)["version"] == 2 and encode_session_record(decode_session_record(v2)) == v2


def test_process_crash_mid_member_resumes_same_id_without_accepting_partial_bytes(tmp_path, art):
    record, _ = art
    preview = _package(tmp_path, record)
    library = SessionLibrary(tmp_path / "library")
    prepared = media.prepare_workspace_package_import(library, preview)
    command = """import os, sys
from core.session_library import SessionLibrary
from core import workspace_media_backup as media
preview = media.preview_workspace_package(sys.argv[2])
def crash(archive, item, work, writer=None):
    writer.write(b'partial')
    writer.flush()
    os.fsync(writer.fileno())
    os._exit(77)
media._copy_member = crash
media.import_workspace_package(SessionLibrary(sys.argv[1]), preview, retry=True)
"""
    result = subprocess.run([sys.executable, "-c", command, str(library.root), str(preview.path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 77, result.stderr
    pending = SessionLibrary(library.root).pending_import()
    assert pending.id == prepared.id
    target = Path(pending.art["references"][0]["locator"])
    assert not target.exists()
    assert any(p.read_bytes() == b"partial" for p in target.parent.iterdir())
    restored = media.import_workspace_package(SessionLibrary(library.root), preview, retry=True)
    assert restored.id == prepared.id and target.read_bytes() == art[1].read_bytes()


def test_disk_full_mid_copy_retains_journal_and_removes_only_owned_partial(tmp_path, art, monkeypatch):
    record, _ = art
    preview = _package(tmp_path, record)
    library = SessionLibrary(tmp_path / "library")
    original = media._copy_member
    def disk_full(archive, item, work, writer=None):
        assert writer is not None
        writer.write(b"partial")
        writer.flush()
        raise OSError(28, "No space left")
    monkeypatch.setattr(media, "_copy_member", disk_full)
    with pytest.raises(media.WorkspaceBackupError):
        media.import_workspace_package(library, preview)
    pending = library.pending_import()
    target = Path(pending.art["references"][0]["locator"])
    assert not target.exists() and not list(target.parent.iterdir())
    monkeypatch.setattr(media, "_copy_member", original)
    assert media.import_workspace_package(library, preview, retry=True).id == pending.id


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor attack; Windows denies rename while pinned")
def test_destination_parent_swap_never_writes_through_symlink(tmp_path, art, monkeypatch):
    record, _ = art
    preview = _package(tmp_path, record)
    library = SessionLibrary(tmp_path / "library")
    outside = tmp_path / "outside"
    outside.mkdir()
    original = media._at_open
    swapped = []
    def swap(parent, directory, name, flags, mode=0o600):
        if name.startswith(".webjam-part-") and not swapped:
            moved = directory.with_name(directory.name + "-owned-moved")
            directory.rename(moved)
            directory.symlink_to(outside, target_is_directory=True)
            swapped.append(moved)
        return original(parent, directory, name, flags, mode)
    monkeypatch.setattr(media, "_at_open", swap)
    with pytest.raises(media.WorkspaceBackupError):
        media.import_workspace_package(library, preview)
    assert swapped and not list(outside.iterdir()) and not library.list()


@pytest.mark.skipif(os.name == "nt", reason="POSIX unlink race; Windows stage handles use explicit delete sharing")
def test_replaced_export_stage_is_not_deleted_or_reported_complete(tmp_path, art, monkeypatch):
    record, _ = art
    plan = media.plan_workspace_package(record, media.WorkspacePackageSelection(art_reference_ids=("painting",)))
    original = media._at_link
    replaced = []
    def swap(parent, directory, source, destination):
        (directory / source).unlink()
        (directory / source).write_bytes(b"another writer")
        replaced.append(directory / source)
        return original(parent, directory, source, destination)
    monkeypatch.setattr(media, "_at_link", swap)
    with pytest.raises(media.WorkspaceBackupError):
        media.export_workspace_package(plan, tmp_path / "backup.webjambackup")
    assert replaced and replaced[0].read_bytes() == b"another writer"


def test_same_inode_modification_during_package_publication_is_detected(tmp_path, art, monkeypatch):
    record, _ = art
    plan = media.plan_workspace_package(record, media.WorkspacePackageSelection(art_reference_ids=("painting",)))
    original = media._at_link
    def modify(parent, directory, source, destination):
        original(parent, directory, source, destination)
        with open(directory / destination, "r+b") as handle:
            handle.seek(0)
            handle.write(b"BAD!")
    monkeypatch.setattr(media, "_at_link", modify)
    target = tmp_path / "backup.webjambackup"
    with pytest.raises(media.WorkspaceBackupError, match="uncertain"):
        media.export_workspace_package(plan, target)
    assert target.exists() and target.read_bytes().startswith(b"BAD!")


def test_verification_progress_does_not_hold_library_writer_lock(tmp_path, art):
    record, _ = art
    preview = _package(tmp_path, record)
    library = SessionLibrary(tmp_path / "library")
    imported = media.import_workspace_package(library, preview)
    checked = []
    def progress(event):
        if not checked:
            checked.append(event)
            code = "from core.session_library import SessionLibrary; import sys; SessionLibrary(sys.argv[1]).create('art', 'Concurrent save')"
            subprocess.run([sys.executable, "-c", code, str(library.root)], capture_output=True, check=True, timeout=5)
    result = media.reconcile_workspace_package_import(library, progress=progress)
    assert result.record.id == imported.id and checked and len(library.list()) == 2


def test_subset_package_preserves_unselected_expected_content_proof(tmp_path, art):
    record, _ = art
    other = tmp_path / "other.png"
    other.write_bytes(b"other original")
    updated = dict(record.art, references=[*record.art["references"],
                   {"id": "other", "title": "Other", "kind": "file", "locator": str(other)}])
    record = replace(record, art=updated)
    both = _package(tmp_path, record, media.WorkspacePackageSelection(art_reference_ids=("painting", "other")))
    imported = media.import_workspace_package(SessionLibrary(tmp_path / "first"), both)
    subset = _package(tmp_path, imported, name="subset.webjambackup")
    restored = media.import_workspace_package(SessionLibrary(tmp_path / "second"), subset)
    assert {p["reference_id"] for p in restored.media_provenance} == {"painting", "other"}
    assert media.verify_workspace_media(restored, "art", "other").matches_expected_content


def test_literal_historical_v1_fixture_preserves_exact_codec_bytes():
    # Fixed wire ordering emitted by the pre-media (f3ddd58) record codec.
    data = b'{"version": 1, "id": "11111111111111111111111111111111", "profile": "art", "title": "Old", "revision": 1, "created_at": "2026-10-02T00:00:00+00:00", "updated_at": "2026-10-02T00:00:00+00:00", "notes": "literal", "mode_key": "", "decisions": [], "actions": [], "blockers": [], "recaps": [], "take_links": [], "rehearsal": {}, "art": {}, "source_key": ""}\n'
    assert encode_session_record(decode_session_record(data)) == data


def test_failed_media_journal_before_write_never_falls_back_to_metadata_publication(tmp_path, art, monkeypatch):
    record, _ = art
    preview = _package(tmp_path, record)
    library = SessionLibrary(tmp_path / "library")
    write = media.atomic_write_bytes
    with monkeypatch.context() as fault:
        fault.setattr(media, "atomic_write_bytes", lambda *_a, **_k: (_ for _ in ()).throw(OSError(28, "disk full")))
        with pytest.raises(SessionLibraryImportUnconfirmed):
            media.prepare_workspace_package_import(library, preview)
    prepared = library.pending_import()
    assert prepared is not None and library.pending_import_has_media()
    assert not (library.root / ".workspace-import.pending").exists()
    for method in (library.prepare_import, library.publish_import, library.reconcile_import, library.acknowledge_import):
        with pytest.raises(SessionLibraryMediaImportRequired):
            method(prepared)
    prepared.art["references"][0]["locator"] = "poison"
    pending = library.pending_import()
    assert pending.art["references"][0]["locator"] != "poison"
    assert media.reconcile_workspace_package_import(library).state == "partial"
    assert media.atomic_write_bytes is write
    result = media.import_workspace_package(library, preview, retry=True)
    assert result.id == pending.id and len(library.list()) == 1


def test_same_instance_acknowledgement_fault_checks_and_resumes_completed_receipt(tmp_path, art, monkeypatch):
    record, _ = art
    preview = _package(tmp_path, record)
    library = SessionLibrary(tmp_path / "library")
    sync = media._fsync_parent_directory
    def fail(path):
        if path == library.root and (path / ".workspace-import.completed").exists():
            raise OSError("ack sync")
        return sync(path)
    with monkeypatch.context() as fault:
        fault.setattr(media, "_fsync_parent_directory", fail)
        with pytest.raises(SessionLibraryImportUnconfirmed):
            media.import_workspace_package(library, preview)
    outcome = media.reconcile_workspace_package_import(library)
    assert outcome.state == "published" and outcome.can_retry
    result = media.reconcile_workspace_package_import(library, retry=True)
    assert result.id == outcome.record.id and library.pending_import() is None


def test_primary_target_collision_after_absence_check_preserves_other_writer(tmp_path, art, monkeypatch):
    record, _ = art
    preview = _package(tmp_path, record)
    library = SessionLibrary(tmp_path / "library")
    link = media._at_link
    collision = []
    def occupy(parent, directory, source, target):
        if directory == library.root and target.endswith(".json"):
            path = directory / target
            path.write_bytes(b"other writer owns this path")
            collision.append(path)
        return link(parent, directory, source, target)
    monkeypatch.setattr(media, "_at_link", occupy)
    with pytest.raises(SessionLibraryImportUnconfirmed):
        media.import_workspace_package(library, preview)
    assert collision and collision[0].read_bytes() == b"other writer owns this path"
    with pytest.raises(SessionLibraryError):
        media.reconcile_workspace_package_import(library)


def test_post_publication_cancel_reports_destination_and_expected_digest(tmp_path, art):
    record, _ = art
    plan = media.plan_workspace_package(record, media.WorkspacePackageSelection(art_reference_ids=("painting",)))
    cancel = []
    def progress(event):
        if event.phase == "Confirm published backup":
            cancel.append(True)
    target = tmp_path / "backup.webjambackup"
    # A second chunk guarantees cancellation is checked after the first
    # post-publication progress callback, even for this tiny package.
    def check():
        return bool(cancel)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(media, "_CHUNK", 64)
        with pytest.raises(media.WorkspacePackagePublicationUnconfirmed) as caught:
            media.export_workspace_package(plan, target, progress=progress, cancel_check=check)
    assert target.exists() and caught.value.destination == target
    assert caught.value.expected_sha256 == _digest(target.read_bytes())


def test_declared_studio_dependencies_are_complete_and_explicitly_verified(tmp_path):
    first, primary = _take(tmp_path)
    second, alternate = _take(tmp_path, session_id=primary.session_id, label="second")
    from core.studio_comping import add_take_lane
    from core.studio_store import load_studio_document, save_studio_document
    saved = load_studio_document(first)
    document = add_take_lane(saved.document, primary, alternate, destination_track_id=primary.tracks[0].track_id)
    save_studio_document(first, document, expected_token=saved.token)
    links = tuple({"take_id": p.take_id, "take_path": str(root), "source_identity": _digest((root / "webjam-take.json").read_bytes())}
                  for root, p in ((first, primary), (second, alternate)))
    record = SessionLibrary(tmp_path / "source").create("music", "Comp", take_links=links)
    incomplete = media.plan_workspace_package(record, media.WorkspacePackageSelection(take_ids=(primary.take_id,)))
    assert incomplete.blockers and "Studio source" in " ".join(incomplete.blockers)
    preview = _package(tmp_path, record, media.WorkspacePackageSelection(take_ids=(primary.take_id, alternate.take_id)))
    restored = media.import_workspace_package(SessionLibrary(tmp_path / "library"), preview)
    result = media.verify_workspace_media(restored, "take", primary.take_id)
    assert set(result.dependency_map) == {alternate.take_id}
    assert result.dependency_map[alternate.take_id][0] != second
    assert result.dependency_map[alternate.take_id][1].take_id == alternate.take_id
    # Tamper only the claimed dependency list; source Studio bytes remain exact.
    with zipfile.ZipFile(preview.path) as archive:
        files = {i.filename: archive.read(i) for i in archive.infolist()}
    manifest = json.loads(files["workspace.json"])
    asset = next(a for a in manifest["assets"] if a["reference_id"] == primary.take_id)
    asset["dependencies"] = []
    files["workspace.json"] = json.dumps(manifest).encode()
    broken = tmp_path / "hidden-dependency.zip"
    with zipfile.ZipFile(broken, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in files.items():
            archive.writestr(media._zip_info(name), data)
    with pytest.raises(media.WorkspaceBackupError, match="dependencies"):
        media.preview_workspace_package(broken)


def test_journal_requires_exact_prepared_record_bytes(tmp_path, art):
    record, _ = art
    library = SessionLibrary(tmp_path / "library")
    media.prepare_workspace_package_import(library, _package(tmp_path, record))
    value = json.loads((library.root / ".workspace-import.pending").read_bytes())
    value["record"] = " " + value["record"]
    with pytest.raises(media.WorkspaceBackupError, match="checksum"):
        media.decode_media_import_journal(json.dumps(value).encode())


def test_retained_media_transaction_refuses_same_record_with_substituted_context(tmp_path, art):
    record, _ = art
    library = SessionLibrary(tmp_path / "library")
    media.prepare_workspace_package_import(library, _package(tmp_path, record))
    journal = library.root / ".workspace-import.pending"
    value = json.loads(journal.read_bytes())
    value["media"]["package_sha256"] = "0" * 64
    journal.write_text(json.dumps(value), encoding="utf-8")
    before = journal.read_bytes()
    with pytest.raises(SessionLibraryError, match="context"):
        library.pending_import()
    with pytest.raises(SessionLibraryError, match="context"):
        media.reconcile_workspace_package_import(library)
    assert journal.read_bytes() == before and not library.list()


def test_post_link_domain_failure_retains_primary_identity_and_uncertainty(tmp_path, art, monkeypatch):
    from contextlib import contextmanager
    record, _ = art
    preview = _package(tmp_path, record)
    library = SessionLibrary(tmp_path / "library")
    original = media._source_bound
    @contextmanager
    def failed_confirmation(parent, directory, name, expected=None):
        with original(parent, directory, name, expected) as value:
            yield value
            if directory == library.root and name.endswith(".json"):
                raise media.WorkspaceBackupError("identity changed after publication")
    with monkeypatch.context() as fault:
        fault.setattr(media, "_source_bound", failed_confirmation)
        with pytest.raises(SessionLibraryImportUnconfirmed) as caught:
            media.import_workspace_package(library, preview)
    assert library.load(caught.value.workspace_id)._store_token == caught.value.expected_sha256
    assert media.reconcile_workspace_package_import(library).state == "published"
    assert media.reconcile_workspace_package_import(library, retry=True).id == caught.value.workspace_id


def test_v3_malformed_art_proof_reference_is_store_error_not_uncaught_attribute_error(tmp_path, art):
    record, _ = art
    imported = media.import_workspace_package(SessionLibrary(tmp_path / "library"), _package(tmp_path, record))
    value = json.loads(encode_session_record(imported))
    value["art"]["references"] = [None]
    with pytest.raises(SessionLibraryError):
        decode_session_record(json.dumps(value).encode())


@pytest.mark.parametrize("unsafe", ["compressed", "extra", "duplicate", "symlink", "checksum"])
def test_unsupported_or_changed_archives_rejected_before_import_writes(tmp_path, art, unsafe):
    record, _ = art
    preview = _package(tmp_path, record)
    with zipfile.ZipFile(preview.path) as archive:
        files = {i.filename: archive.read(i) for i in archive.infolist()}
    if unsafe == "extra":
        files["unrelated.txt"] = b"unrelated"
    media_name = next(name for name in files if name != "workspace.json")
    if unsafe == "checksum":
        files[media_name] += b"changed"
    bad = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        for name, data in files.items():
            info = media._zip_info(name)
            if unsafe == "compressed":
                info.compress_type = zipfile.ZIP_DEFLATED
            if unsafe == "symlink" and name == media_name:
                info.external_attr = (0o120777 << 16)
            archive.writestr(info, data)
        if unsafe == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate"):
                archive.writestr(media._zip_info(media_name), files[media_name])
    with pytest.raises(media.WorkspaceBackupError):
        media.preview_workspace_package(bad)
    assert not (tmp_path / "destination").exists()
