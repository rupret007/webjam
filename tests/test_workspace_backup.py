"""Portable metadata preserves content without touching its external references."""
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core import workspace_backup as backup
from core.art_workspace import normalize_art_workspace
from core.rehearsal_plan import RehearsalPlan, make_song, make_bookmark
from core.session_library import (
    SessionLibrary, SessionLibraryError, SessionLibraryConflict,
    SessionLibraryImportUnconfirmed, encode_session_record, decode_session_record,
)
from core.workspace_backup import (
    WorkspaceBackupError,
    export_workspace_backup,
    preview_workspace_backup,
    import_workspace_backup,
)


@pytest.fixture
def record(tmp_path):
    moment = make_bookmark("Keep", take_id="take-1", take_path="/missing/original",
                           source_identity="exact-source", position_seconds=4.25, timing_verified=True)
    moment["id"] = "moment-1"
    plain = make_bookmark("Untimed note")
    plain["id"] = "plain-1"
    song = make_song("First song", id="song-1", notes="Draft", moment_draft="Unsent moment draft", bookmarks=[moment, plain])
    return SessionLibrary(tmp_path / "source").create(
        "music", "Friday — déjà vu", notes="Every line\n  and trailing spaces  \n",
        mode_key="music_jam", decisions=("Play in D",), actions=("Send chart",),
        blockers=("Missing player",), source_key="legacy-profile-notes",
        recaps=({"take_ids": ["take-1"], "summary": "Historical completion", "duration_seconds": 12,
                 "run_id": "run-1", "ended_at": "2026-10-02T00:00:00+00:00"},),
        take_links=({"take_id": "take-1", "take_path": "/missing/original",
                     "recording_session_id": "original-workspace", "status": "complete",
                     "validated": True, "run_id": "run-1",
                     "source_identity": "exact-source"},),
        rehearsal=RehearsalPlan("Friday plan", [song], song["id"]).payload(),
        art=normalize_art_workspace({"version": 1, "brief": "Keep original reference IDs",
            "progress": "Underpainting complete", "next_steps": "Add highlights", "references": [
            {"id": "ref-1", "kind": "file", "locator": "../../missing/art.png"},
            {"id": "ref-2", "kind": "url", "locator": "https://example.invalid/lesson"}],
            "bookmarks": [{"id": "lesson-1", "reference_id": "ref-2", "seconds": 32.5, "note": "Try"}]}),
    )


def _wire(payload, **envelope_changes):
    canonical = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                           sort_keys=True, separators=(",", ":")).encode("utf-8")
    envelope = {"format": "webjam.workspace-backup", "version": 1,
                "content": "metadata-only", "payload": payload,
                "payload_sha256": hashlib.sha256(canonical).hexdigest()}
    envelope.update(envelope_changes)
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")


def _payload(record):
    return json.loads(encode_session_record(record))


def _symlink(path, target):
    try:
        path.symlink_to(target)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable; regular-file and hard-link safety still run")
        raise


def test_export_preview_preserves_all_content_and_does_not_visit_references(tmp_path, record, monkeypatch):
    source_path = tmp_path / "source" / f"{record.id}.json"
    source_bytes = source_path.read_bytes()
    original_lstat = Path.lstat
    def metadata_paths_only(path, *args, **kwargs):
        assert Path(path) not in {Path("/missing/original"), Path("../../missing/art.png")}
        return original_lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", metadata_paths_only)
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: pytest.fail("network access"))
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("external process"))
    destination = tmp_path / "workspace.json"
    exported = export_workspace_backup(record, destination)
    assert exported.record == record
    assert exported.record._store_token is None
    assert preview_workspace_backup(destination) == exported
    assert source_path.read_bytes() == source_bytes
    envelope = json.loads(destination.read_bytes())
    assert envelope["content"] == "metadata-only"
    assert envelope["payload"] == _payload(record)
    assert exported.payload_sha256 == envelope["payload_sha256"]
    assert "not included" in " ".join(exported.limitations)
    assert "not proof" in " ".join(exported.limitations)
    assert not list(tmp_path.glob(".webjam-backup-*"))
    if os.name == "posix":
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600


def test_preview_binds_validated_bytes_and_returns_independent_nested_metadata(tmp_path, record):
    path = tmp_path / "backup.json"
    export_workspace_backup(record, path)
    before = path.read_bytes()
    modified = path.stat().st_mtime_ns
    preview = preview_workspace_backup(path)
    assert path.read_bytes() == before and path.stat().st_mtime_ns == modified
    preview.record.rehearsal["songs"].clear()
    assert preview.record.rehearsal == record.rehearsal
    with pytest.raises(FrozenInstanceError):
        preview._payload_bytes = b"changed"
    path.write_bytes(_wire(_payload(replace(record, notes="Changed after preview"))))
    assert preview.record.notes == record.notes
    assert preview_workspace_backup(path).record.notes == "Changed after preview"


def test_checksum_uses_canonical_metadata_not_envelope_layout(tmp_path, record):
    path = tmp_path / "backup.json"
    exported = export_workspace_backup(record, path)
    value = json.loads(path.read_bytes())
    value["payload"] = dict(reversed(list(value["payload"].items())))
    path.write_text(json.dumps(value, indent=4, ensure_ascii=True), encoding="utf-8")
    assert preview_workspace_backup(path) == exported


@pytest.mark.parametrize("content", [
    b"not JSON", b"\xff", b'{"format":"a","format":"b"}',
    b'{"payload":{"notes":"one","notes":"two"}}', b'{"payload":NaN}',
    b'{"payload":Infinity}', b'{"payload":-Infinity}',
])
def test_malformed_or_duplicate_json_is_refused_without_writes(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_bytes(content)
    before = set(tmp_path.iterdir())
    with pytest.raises(WorkspaceBackupError):
        preview_workspace_backup(path)
    assert path.read_bytes() == content and set(tmp_path.iterdir()) == before


@pytest.mark.parametrize("changes", [
    {"version": 2}, {"version": True}, {"format": "other"},
    {"content": "audio-included"}, {"extra": "unsupported"},
    {"payload_sha256": "0" * 64}, {"payload_sha256": "A" * 64},
    {"payload_sha256": None},
])
def test_unknown_envelope_or_bad_checksum_is_refused(tmp_path, record, changes):
    path = tmp_path / "bad.json"
    path.write_bytes(_wire(_payload(record), **changes))
    with pytest.raises(WorkspaceBackupError):
        preview_workspace_backup(path)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("id", "../outside"), ("profile", "unknown"),
    ("revision", 0), ("revision", 2**64), ("notes", None),
    ("title", "bad\0title"), ("updated_at", "1900-01-01T00:00:00Z"),
    ("rehearsal", {"bad_number": 2**64}), ("take_links", [{"take_id": {}}]),
    ("extra", "must not be silently dropped"),
])
def test_valid_checksum_does_not_bypass_workspace_validation(tmp_path, record, field, value):
    payload = _payload(record)
    payload[field] = value
    path = tmp_path / "bad.json"
    path.write_bytes(_wire(payload))
    with pytest.raises(WorkspaceBackupError):
        preview_workspace_backup(path)


def test_deep_or_nonfinite_or_invalid_unicode_payload_is_refused(tmp_path, record):
    path = tmp_path / "bad.json"
    payload = _payload(record)
    nested = {}
    for _ in range(20):
        nested = {"next": nested}
    payload["art"] = nested
    path.write_bytes(_wire(payload))
    with pytest.raises(WorkspaceBackupError, match="deeply"):
        preview_workspace_backup(path)
    # These tokens decode to invalid domain values despite syntactically valid JSON.
    envelope = json.loads(_wire(_payload(record)))
    for encoded in (b'"\\ud800"', b'1e999'):
        envelope["payload"]["notes"] = "REPLACE-ME"
        raw = json.dumps(envelope).encode().replace(b'"REPLACE-ME"', encoded)
        path.write_bytes(raw)
        with pytest.raises(WorkspaceBackupError):
            preview_workspace_backup(path)


def test_oversized_file_is_refused_before_reading(tmp_path, monkeypatch):
    path = tmp_path / "large.json"
    with path.open("wb") as stream:
        stream.truncate(backup.MAX_WORKSPACE_BACKUP_BYTES + 1)
    monkeypatch.setattr(backup.os, "open", lambda *_a, **_k: pytest.fail("oversized file opened"))
    with pytest.raises(WorkspaceBackupError, match="too large"):
        preview_workspace_backup(path)


@pytest.mark.parametrize("kind", ["symlink", "directory", "fifo"])
def test_preview_refuses_unsafe_file_types_without_opening_them(tmp_path, kind, monkeypatch):
    path = tmp_path / "unsafe"
    if kind == "symlink":
        _symlink(path, tmp_path / "missing")
    elif kind == "directory":
        path.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO is POSIX-only")
        os.mkfifo(path)
    monkeypatch.setattr(backup.os, "open", lambda *_a, **_k: pytest.fail("unsafe file opened"))
    with pytest.raises(WorkspaceBackupError, match="regular"):
        preview_workspace_backup(path)


@pytest.mark.parametrize("kind", ["regular", "symlink", "hardlink", "directory"])
def test_export_never_overwrites_existing_objects(tmp_path, record, kind):
    original = tmp_path / "keep.bin"
    original.write_bytes(b"exact saved bytes")
    target = tmp_path / "chosen.json"
    if kind == "regular":
        target.write_bytes(b"previous backup")
    elif kind == "symlink":
        _symlink(target, original)
    elif kind == "hardlink":
        os.link(original, target)
    else:
        target.mkdir()
    with pytest.raises(WorkspaceBackupError, match="new backup filename"):
        export_workspace_backup(record, target)
    assert original.read_bytes() == b"exact saved bytes"
    if kind == "regular":
        assert target.read_bytes() == b"previous backup"
    assert not list(tmp_path.glob(".webjam-backup-*"))


def test_exclusive_publication_preserves_a_destination_created_during_export(tmp_path, record, monkeypatch):
    target = tmp_path / "backup.json"
    real_link = backup.os.link
    def race(source, destination, **kwargs):
        Path(destination).write_bytes(b"concurrent writer")
        return real_link(source, destination, **kwargs)
    monkeypatch.setattr(backup.os, "link", race)
    with pytest.raises(WorkspaceBackupError):
        export_workspace_backup(record, target)
    assert target.read_bytes() == b"concurrent writer"
    assert not list(tmp_path.glob(".webjam-backup-*"))


def test_failed_staging_preserves_original_library_and_publishes_nothing(tmp_path, record, monkeypatch):
    source = tmp_path / "source" / f"{record.id}.json"
    original = source.read_bytes()
    def interrupted(*_args):
        raise OSError("private destination details")
    monkeypatch.setattr(backup.os, "fsync", interrupted)
    with pytest.raises(WorkspaceBackupError) as error:
        export_workspace_backup(record, tmp_path / "backup.json")
    assert "private destination" not in str(error.value)
    assert not (tmp_path / "backup.json").exists()
    assert source.read_bytes() == original
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory durability")
def test_post_publication_sync_failure_retains_complete_file_without_claiming_success(tmp_path, record, monkeypatch):
    target = tmp_path / "backup.json"
    real_sync = os.fsync
    def fail_published_directory(descriptor):
        if target.exists() and stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("directory fsync failed after publication")
        return real_sync(descriptor)
    monkeypatch.setattr(backup.os, "fsync", fail_published_directory)
    with pytest.raises(WorkspaceBackupError, match="not confirmed saved"):
        export_workspace_backup(record, target)
    assert preview_workspace_backup(target).record == record
    assert not list(tmp_path.glob(".webjam-backup-*"))


def test_backup_path_replaced_between_inspection_and_open_is_refused(tmp_path, record, monkeypatch):
    target = tmp_path / "backup.json"
    export_workspace_backup(record, target)
    original = target.read_bytes()
    real_open = backup.os.open
    def replaced(path, *args, **kwargs):
        if Path(path) == target:
            target.rename(tmp_path / "original.json")
            target.write_bytes(original)
        return real_open(path, *args, **kwargs)
    monkeypatch.setattr(backup.os, "open", replaced)
    with pytest.raises(WorkspaceBackupError, match="changed while"):
        preview_workspace_backup(target)


@pytest.mark.parametrize("platform", ["posix", "nt"])
@pytest.mark.parametrize("changed", [None, "path_ctime", "handle_ctime"])
def test_preview_checks_ctime_within_each_api_on_windows(tmp_path, record, monkeypatch, platform, changed):
    target = tmp_path / "backup.json"
    target.write_bytes(_wire(_payload(record)))
    real_lstat = Path.lstat
    real_fstat = os.fstat
    inspections = {"path": 0, "handle": 0}

    def inspect(info, source):
        inspections[source] += 1
        values = {key: getattr(info, key) for key in (
            "st_mode", "st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
        # Model the Windows lstat creation-time / fstat change-time mismatch
        # on every host, and a change visible only to one of those APIs.
        values["st_ctime_ns"] = 1_000_000_000 if source == "path" else 2_000_000_000
        if changed == source + "_ctime" and inspections[source] == 2:
            values["st_ctime_ns"] += 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(Path, "lstat", lambda path: inspect(real_lstat(path), "path"))
    simulated_os = SimpleNamespace(**vars(os))
    simulated_os.name = platform
    simulated_os.fstat = lambda fd: inspect(real_fstat(fd), "handle")
    monkeypatch.setattr(backup, "os", simulated_os)
    if platform == "nt" and changed is None:
        assert preview_workspace_backup(target).record == record
    else:
        with pytest.raises(WorkspaceBackupError, match="changed while"):
            preview_workspace_backup(target)
    assert inspections == {"path": 2, "handle": 2}


def test_preview_refuses_same_size_edit_during_read_with_restored_mtime(tmp_path, record, monkeypatch):
    target = tmp_path / "backup.json"
    original = _wire(_payload(record))
    target.write_bytes(original)
    before = target.stat()
    real_fstat = os.fstat
    inspections = []

    def edit_after_read(descriptor):
        if inspections:
            target.write_bytes(original.replace(b"Every line", b"Other line"))
            os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
        result = real_fstat(descriptor)
        inspections.append(result)
        return result

    monkeypatch.setattr(backup.os, "fstat", edit_after_read)
    with pytest.raises(WorkspaceBackupError, match="changed while"):
        preview_workspace_backup(target)
    assert len(inspections) == 2
    assert inspections[0].st_size == inspections[1].st_size
    assert inspections[0].st_mtime_ns == inspections[1].st_mtime_ns
    assert inspections[0].st_ctime_ns != inspections[1].st_ctime_ns


@pytest.mark.parametrize("field,entry", [
    ("take_links", {"take_id": "take-1", "invitation": "private structured secret"}),
    ("take_links", {"take_id": "take-1", "validated": 1}),
    ("recaps", {"summary": "Keep", "token": "private structured secret"}),
    ("recaps", {"summary": [], "duration_seconds": 3}),
    ("recaps", {"duration_seconds": True}),
])
def test_unsupported_portable_structure_is_rejected_not_silently_dropped(tmp_path, record, field, entry):
    changed = replace(record, **{field: (entry,)})
    destination = tmp_path / "unsupported.json"
    with pytest.raises(WorkspaceBackupError):
        export_workspace_backup(changed, destination)
    assert not destination.exists()
    destination.write_bytes(_wire(_payload(changed)))
    original = destination.read_bytes()
    with pytest.raises(WorkspaceBackupError):
        preview_workspace_backup(destination)
    assert destination.read_bytes() == original


@pytest.mark.parametrize("change", [
    lambda p: p["rehearsal"]["songs"][0].update(api_key="unsupported"),
    lambda p: p["rehearsal"]["songs"].append(p["rehearsal"]["songs"][0].copy()),
    lambda p: p["rehearsal"].update(active_song_id="missing"),
    lambda p: p["rehearsal"].update(version=True),
    lambda p: p["rehearsal"]["songs"][0].update(completed=1),
    lambda p: p["rehearsal"]["songs"][0].update(tempo=123.5),
    lambda p: p["rehearsal"]["songs"][0]["bookmarks"][0].update(take_path=None),
    lambda p: p["rehearsal"]["songs"][0]["bookmarks"][0].update(position_seconds=True),
    lambda p: p["art"]["references"][0].update(token="unsupported"),
    lambda p: p["art"]["references"][1].update(locator="https://user:pass@example.invalid"),
    lambda p: p["art"]["references"].append(p["art"]["references"][0].copy()),
    lambda p: p["art"]["bookmarks"][0].update(reference_id="missing"),
    lambda p: p["art"]["bookmarks"][0].update(seconds=True),
])
def test_art_and_rehearsal_must_roundtrip_without_repair_or_identity_loss(tmp_path, record, change):
    payload = _payload(record)
    change(payload)
    path = tmp_path / "invalid.json"
    path.write_bytes(_wire(payload))
    with pytest.raises(WorkspaceBackupError):
        preview_workspace_backup(path)


@pytest.mark.parametrize("replacement", ["symlink", "regular"])
def test_stage_replaced_during_publication_never_reports_success_or_deletes_replacement(tmp_path, record, monkeypatch, replacement):
    target = tmp_path / "backup.json"
    other = tmp_path / "unrelated"
    other.write_bytes(b"unrelated saved bytes")
    real_link = backup.os.link
    real_fdopen = backup.os.fdopen
    stage_descriptors = []
    def stage(descriptor, *args, **kwargs):
        stage_descriptors.append(descriptor)
        return real_fdopen(descriptor, *args, **kwargs)
    monkeypatch.setattr(backup.os, "fdopen", stage)
    replaced = []
    def swap(source, destination, **kwargs):
        source = Path(source)
        opened = os.fstat(stage_descriptors[0])
        assert (opened.st_dev, opened.st_ino) == (source.stat().st_dev, source.stat().st_ino)
        if os.name == "nt":
            # Windows can retain a deleted pathname until the final handle
            # closes. Rename permits a real replacement while pinning the old
            # inode, exercising the same source-path substitution boundary.
            detached = source.rename(tmp_path / "detached-owned-stage")
            detached.unlink()
        else:
            source.unlink()
        if replacement == "symlink":
            _symlink(source, other)
        else:
            source.write_bytes(b"unowned replacement")
        replaced.append(source)
        # The original inode stays allocated despite losing its pathname.
        assert os.fstat(stage_descriptors[0]).st_ino == opened.st_ino
        return real_link(source, destination, **kwargs)
    monkeypatch.setattr(backup.os, "link", swap)
    with pytest.raises(WorkspaceBackupError, match="not confirmed saved"):
        export_workspace_backup(record, target)
    assert other.read_bytes() == b"unrelated saved bytes"
    with pytest.raises(OSError):
        os.fstat(stage_descriptors[0])
    assert replaced[0].exists()  # Unknown replacement is never blindly removed.
    if replacement == "symlink":
        assert replaced[0].is_symlink()
    else:
        assert replaced[0].read_bytes() == b"unowned replacement"


def test_import_creates_new_private_identity_and_detaches_historical_ownership(tmp_path, record, monkeypatch):
    path = tmp_path / "backup.json"
    preview = export_workspace_backup(record, path)
    original = (tmp_path / "source" / f"{record.id}.json").read_bytes()
    library = SessionLibrary(tmp_path / "destination")
    # A previously reviewed snapshot survives changes/removal of its source file.
    path.unlink()
    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: pytest.fail("network access"))
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("external process"))
    real_stat = Path.stat
    def metadata_only(path, *args, **kwargs):
        assert Path(path) not in {Path("/missing/original"), Path("../../missing/art.png")}
        return real_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", metadata_only)
    imported = import_workspace_backup(library, preview)
    assert imported.id != record.id and imported.revision == 1
    assert imported._store_token and not imported.recovered
    assert imported.created_at == imported.updated_at
    assert imported.notes == record.notes and imported.title == record.title
    assert imported.source_key == record.source_key
    for key in ("profile", "mode_key", "decisions", "actions", "blockers", "recaps", "rehearsal", "art"):
        assert getattr(imported, key) == getattr(record, key)
    assert imported.import_provenance == ({
        "source_workspace_id": record.id, "profile": record.profile,
        "revision": record.revision, "created_at": record.created_at,
        "updated_at": record.updated_at, "source_key": record.source_key,
        "payload_sha256": preview.payload_sha256,
    },)
    link = imported.take_links[0]
    assert link["take_id"] == "take-1" and link["source_identity"] == "exact-source"
    assert link["take_path"] == "/missing/original"
    assert not {"run_id", "recording_session_id", "validated", "status"} & link.keys()
    assert link["historical_origins"] == [{"source_workspace_id": record.id,
        "run_id": "run-1", "recording_session_id": "original-workspace", "validated": True, "status": "complete"}]
    assert library.load(imported.id) == imported
    assert (tmp_path / "source" / f"{record.id}.json").read_bytes() == original
    assert json.loads(original)["version"] == 1 and "import_provenance" not in json.loads(original)
    assert json.loads((library.root / f"{imported.id}.json").read_bytes())["version"] == 2
    if os.name == "posix":
        assert stat.S_IMODE((library.root / f"{imported.id}.json").stat().st_mode) == 0o600


def test_repeat_import_and_export_reimport_preserve_flat_provenance_and_source_key(tmp_path, record):
    library = SessionLibrary(tmp_path / "destination")
    preview = export_workspace_backup(record, tmp_path / "first.json")
    assert preview.matching_import_ids(library) == () and not library.root.exists()
    one = import_workspace_backup(library, preview)
    two = import_workspace_backup(library, preview)
    assert len({one.id, two.id, record.id}) == 3
    assert set(preview.matching_import_ids(library)) == {one.id, two.id}
    # User edits remain editable, but import evidence itself is immutable.
    one = library.save(replace(one, notes="New local notes"))
    second = export_workspace_backup(one, tmp_path / "second.json")
    three = import_workspace_backup(library, second)
    assert three.revision == 1 and three.id not in {one.id, two.id, record.id}
    assert three.import_provenance[:-1] == one.import_provenance
    assert three.import_provenance[-1]["source_workspace_id"] == one.id
    assert three.import_provenance[-1]["revision"] == 2
    assert three.import_provenance[-1]["payload_sha256"] == second.payload_sha256
    assert three.source_key == record.source_key
    assert three.take_links == one.take_links  # No fabricated extra take history.
    assert three.notes == "New local notes"
    assert second.matching_import_ids(library) == (three.id,)
    assert set(preview.matching_import_ids(library)) == {one.id, two.id}
    for altered in ((), (*one.import_provenance[:-1], dict(one.import_provenance[-1], source_key="changed"))):
        with pytest.raises(SessionLibraryConflict):
            library.save(replace(one, import_provenance=altered))


def test_new_recording_in_an_imported_workspace_gets_separate_historical_origin_on_next_import(tmp_path, record):
    library = SessionLibrary(tmp_path / "destination")
    first = import_workspace_backup(library, export_workspace_backup(record, tmp_path / "one.json"))
    updated = dict(first.take_links[0], recording_session_id="new-session", run_id="new-run", validated=False, status="pending")
    first = library.save(replace(first, take_links=(updated,)))
    second = import_workspace_backup(library, export_workspace_backup(first, tmp_path / "two.json"))
    origins = second.take_links[0]["historical_origins"]
    assert origins[0] == first.take_links[0]["historical_origins"][0]
    assert origins[1] == {"source_workspace_id": first.id, "recording_session_id": "new-session",
                           "run_id": "new-run", "validated": False, "status": "pending"}
    assert "recording_session_id" not in second.take_links[0]


def test_prior_import_check_reports_incomplete_library_scan_instead_of_false_absence(tmp_path, record):
    library = SessionLibrary(tmp_path / "destination")
    library.root.mkdir()
    (library.root / ("a" * 32 + ".json")).write_bytes(b"damaged")
    preview = export_workspace_backup(record, tmp_path / "backup.json")
    with pytest.raises(WorkspaceBackupError, match="could not be checked"):
        preview.matching_import_ids(library)


@pytest.mark.parametrize("after_publication", [False, True])
def test_import_write_failure_exposes_exact_intended_identity_for_reconciliation(tmp_path, record, monkeypatch, after_publication):
    library = SessionLibrary(tmp_path / "destination")
    preview = export_workspace_backup(record, tmp_path / "backup.json")
    real_write = library._publish_import_file
    attempted = []
    def fail(path, data, **kwargs):
        attempted.append((Path(path), data))
        if after_publication:
            real_write(path, data, **kwargs)
        raise OSError("private error detail")
    monkeypatch.setattr(library, "_publish_import_file", fail)
    with pytest.raises(SessionLibraryImportUnconfirmed) as caught:
        import_workspace_backup(library, preview)
    error = caught.value
    assert len(attempted) == 1
    path, data = attempted[0]
    assert error.workspace_id == path.stem
    assert error.expected_sha256 == hashlib.sha256(data).hexdigest()
    assert "private error detail" not in str(error)
    prepared = decode_session_record(data)
    assert prepared.import_provenance[-1]["payload_sha256"] == preview.payload_sha256
    assert prepared.id != record.id
    if after_publication:
        assert library.load(error.workspace_id)._store_token == error.expected_sha256
        assert preview.matching_import_ids(library) == (error.workspace_id,)
        # Reconcile exact identity; attempting the same operation cannot replace it.
        with pytest.raises(SessionLibraryConflict):
            library.create_imported(prepared)
        assert path.read_bytes() == data
    else:
        assert not path.exists() and library.list() == []


@pytest.mark.parametrize("profile", ["music", "art"])
def test_imported_history_and_exact_content_survive_a_fresh_python_process(tmp_path, record, profile):
    record = replace(record, profile=profile, mode_key="")
    library = SessionLibrary(tmp_path / "destination")
    preview = export_workspace_backup(record, tmp_path / "backup.json")
    imported = import_workspace_backup(library, preview)
    expected = _payload(imported)
    code = '''
import json, sys
from core.session_library import SessionLibrary, encode_session_record
from core.workspace_backup import preview_workspace_backup
library = SessionLibrary(sys.argv[1])
record = library.load(sys.argv[2])
print(json.dumps({"record": json.loads(encode_session_record(record)),
                  "duplicates": preview_workspace_backup(sys.argv[3]).matching_import_ids(library)}))
'''
    completed = subprocess.run([sys.executable, "-c", code, str(library.root), imported.id, str(tmp_path / "backup.json")],
                               cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True, timeout=15)
    result = json.loads(completed.stdout)
    assert result == {"record": expected, "duplicates": [imported.id]}
    assert result["record"]["rehearsal"]["songs"][0]["bookmarks"][0]["id"] == "moment-1"
    assert result["record"]["art"]["references"][0]["id"] == "ref-1"
    assert result["record"]["profile"] == profile
    assert result["record"]["art"]["progress"] == "Underpainting complete"
    assert result["record"]["art"]["next_steps"] == "Add highlights"
    song = result["record"]["rehearsal"]["songs"][0]
    assert song["moment_draft"] == "Unsent moment draft"
    assert song["bookmarks"][1]["position_seconds"] is None


@pytest.mark.parametrize("change", [
    lambda p: p.update(import_provenance=[]),
    lambda p: p["import_provenance"][0].update(extra="unsupported"),
    lambda p: p["import_provenance"][0].update(source_workspace_id="../source"),
    lambda p: p["import_provenance"][0].update(payload_sha256="invalid"),
    lambda p: p["import_provenance"][0].update(revision=True),
    lambda p: p["import_provenance"][0].update(source_key="x" * 513),
    lambda p: p["import_provenance"].extend([p["import_provenance"][0]] * 16),
    lambda p: p["take_links"][0]["historical_origins"][0].update(validated=1),
    lambda p: p["take_links"][0]["historical_origins"][0].update(extra="unsupported"),
    lambda p: p["take_links"][0].update(historical_origins=[]),
    lambda p: p["take_links"][0]["historical_origins"].extend([p["take_links"][0]["historical_origins"][0]] * 16),
])
def test_imported_v2_history_is_strictly_validated_on_store_read(tmp_path, record, change):
    library = SessionLibrary(tmp_path / "destination")
    imported = import_workspace_backup(library, export_workspace_backup(record, tmp_path / "backup.json"))
    payload = _payload(imported)
    change(payload)
    with pytest.raises(SessionLibraryError):
        decode_session_record(json.dumps(payload).encode())


def test_history_overflow_refuses_import_before_any_library_creation(tmp_path, record):
    source = SessionLibrary(tmp_path / "source")
    first = import_workspace_backup(source, export_workspace_backup(record, tmp_path / "one.json"))
    full = replace(first, import_provenance=first.import_provenance * backup.MAX_IMPORT_HISTORY)
    preview = export_workspace_backup(full, tmp_path / "full.json")
    destination = SessionLibrary(tmp_path / "untouched")
    with pytest.raises(WorkspaceBackupError, match="history is full"):
        import_workspace_backup(destination, preview)
    assert not destination.root.exists()


def test_unimported_v1_bytes_remain_exactly_compatible(tmp_path):
    # An independent pre-v2 byte fixture, including the old field order.
    expected = (b'{"version": 1, "id": "0123456789abcdef0123456789abcdef", '
                b'"profile": "music", "title": "Prior workspace", "revision": 2, '
                b'"created_at": "2026-09-01T00:00:00+00:00", "updated_at": "2026-09-02T00:00:00+00:00", '
                b'"notes": "Every line\\n", "mode_key": "", "decisions": ["Keep"], '
                b'"actions": [], "blockers": [], "recaps": [], "take_links": [], '
                b'"rehearsal": {}, "art": {}, "source_key": "legacy-profile-notes"}\n')
    record = decode_session_record(expected)
    assert record.import_provenance == ()
    assert encode_session_record(record) == expected
    library = SessionLibrary(tmp_path / "legacy")
    library.root.mkdir()
    path = library.root / f"{record.id}.json"
    path.write_bytes(expected)
    loaded = library.load(record.id)
    assert encode_session_record(loaded) == expected
    saved = library.save(replace(loaded, notes="New local edit"))
    assert json.loads(path.read_bytes())["version"] == 1
    assert "import_provenance" not in json.loads(path.read_bytes())
    assert saved.notes == "New local edit"


def test_stage_inspection_failure_closes_descriptor_without_touching_other_files(tmp_path, record, monkeypatch):
    real_fstat = os.fstat
    descriptors = []
    def fail(descriptor):
        descriptors.append(descriptor)
        raise OSError("injected stage inspection failure")
    monkeypatch.setattr(backup.os, "fstat", fail)
    with pytest.raises(WorkspaceBackupError):
        export_workspace_backup(record, tmp_path / "backup.json")
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        real_fstat(descriptors[0])
    assert not (tmp_path / "backup.json").exists()


def test_normal_v1_keeps_opaque_legacy_fields_readable_but_backup_refuses_unknown_meaning(tmp_path, record):
    library = SessionLibrary(tmp_path / "source")
    legacy = library.save(replace(record, take_links=({"take_id": "old", "historical_origins": "old opaque metadata"},)))
    raw = (library.root / f"{legacy.id}.json").read_bytes()
    assert json.loads(raw)["version"] == 1
    assert decode_session_record(raw) == legacy
    assert library.load(legacy.id).take_links == legacy.take_links
    with pytest.raises(WorkspaceBackupError, match="Legacy historical"):
        export_workspace_backup(legacy, tmp_path / "unsupported.json")
    assert (library.root / f"{legacy.id}.json").read_bytes() == raw


def test_copy_detached_take_ownership_still_exports_as_a_backup(tmp_path):
    library = SessionLibrary(tmp_path / "source")
    original = library.create("music", "Still recording", take_links=({
        "take_id": "t1", "take_path": "", "status": "pending", "run_id": "r1",
        "recording_session_id": "s1", "validated": False, "title": "Recording requested",
    },))
    copied_links = backup.detach_take_ownership(original.take_links, original.id)
    copy = library.create("music", "Copy", take_links=copied_links)
    preview = export_workspace_backup(copy, tmp_path / "backup.json")
    assert preview.record.take_links == copied_links


@pytest.mark.parametrize("legacy_origins", [
    {"note": "keep"},
    "old opaque metadata",
    None,
])
def test_detach_take_ownership_refuses_to_corrupt_opaque_legacy_history(legacy_origins):
    # Version-1 records permit arbitrary bounded metadata under this key
    # (core.session_library only assigns it meaning once import_provenance is
    # non-empty). A pending reservation's live ownership fields must still be
    # detached safely: merging into whatever shape happens to be here would
    # silently scramble a dict's keys, explode a string into characters, or
    # crash on None.
    take_links = ({
        "take_id": "t1", "take_path": "", "status": "pending", "run_id": "r1",
        "recording_session_id": "s1", "validated": False, "title": "Recording requested",
        "historical_origins": legacy_origins,
    },)
    with pytest.raises(WorkspaceBackupError, match="unsupported legacy shape"):
        backup.detach_take_ownership(take_links, "a" * 32)
    assert take_links[0]["historical_origins"] == legacy_origins


def test_detach_take_ownership_extends_existing_well_formed_history():
    first = backup.detach_take_ownership(({
        "take_id": "t1", "take_path": "", "status": "pending",
        "recording_session_id": "s1", "run_id": "r1", "validated": False,
    },), "a" * 32)
    second = backup.detach_take_ownership(({
        **first[0], "status": "pending",
        "recording_session_id": "s2", "run_id": "r2", "validated": True,
    },), "b" * 32)
    assert second[0]["historical_origins"] == [
        {"source_workspace_id": "a" * 32, "status": "pending",
         "recording_session_id": "s1", "run_id": "r1", "validated": False},
        {"source_workspace_id": "b" * 32, "status": "pending",
         "recording_session_id": "s2", "run_id": "r2", "validated": True},
    ]


def test_copy_detached_history_with_invalid_field_type_refuses_export_not_just_import(tmp_path):
    # detach_take_ownership only validates *pre-existing* historical_origins;
    # it does not retype the ownership fields it just moved. The export guard
    # must still catch an invalid type here instead of letting a backup out
    # that is only discovered broken by whoever imports it later.
    library = SessionLibrary(tmp_path / "source")
    original = library.create("music", "Still recording", take_links=({
        "take_id": "t1", "take_path": "", "status": "pending", "run_id": "r1",
        "recording_session_id": "s1", "validated": 1, "title": "Recording requested",
    },))
    copied_links = backup.detach_take_ownership(original.take_links, original.id)
    assert copied_links[0]["historical_origins"][0]["validated"] == 1
    copy = library.create("music", "Copy", take_links=copied_links)
    with pytest.raises(WorkspaceBackupError, match="Legacy historical"):
        export_workspace_backup(copy, tmp_path / "backup.json")


def test_store_import_boundary_rejects_reusing_source_identity_before_writes(tmp_path, record):
    source = SessionLibrary(tmp_path / "source")
    imported = import_workspace_backup(source, export_workspace_backup(record, tmp_path / "backup.json"))
    target = SessionLibrary(tmp_path / "untouched")
    prepared = replace(imported, id=record.id, _store_token=None)
    with pytest.raises(SessionLibraryError, match="fresh local identity"):
        target.create_imported(prepared)
    assert not target.root.exists()


def test_historical_origin_overflow_refuses_import_without_losing_prior_history(tmp_path, record):
    library = SessionLibrary(tmp_path / "source")
    imported = import_workspace_backup(library, export_workspace_backup(record, tmp_path / "one.json"))
    full_link = dict(imported.take_links[0], run_id="new-run",
                     historical_origins=imported.take_links[0]["historical_origins"] * backup.MAX_IMPORT_HISTORY)
    full = replace(imported, take_links=(full_link,))
    preview = export_workspace_backup(full, tmp_path / "full.json")
    target = SessionLibrary(tmp_path / "untouched")
    with pytest.raises(WorkspaceBackupError, match="origins are full"):
        import_workspace_backup(target, preview)
    assert not target.root.exists()
    assert preview.record.take_links == full.take_links


def test_maximum_legacy_source_key_and_literal_private_notes_are_never_scrubbed_or_truncated(tmp_path, record):
    source = replace(record, source_key="é" * 256, notes="My private token=my-user-text stays literal")
    preview = export_workspace_backup(source, tmp_path / "backup.json")
    imported = import_workspace_backup(SessionLibrary(tmp_path / "destination"), preview)
    assert imported.source_key == source.source_key
    assert imported.import_provenance[-1]["source_key"] == source.source_key
    assert imported.notes == source.notes
    assert "not a secret-scrubbing" in " ".join(preview.limitations)


def test_successful_export_closes_stage_descriptor_and_removes_private_stage(tmp_path, record, monkeypatch):
    real_stage = backup._new_stage
    opened = []
    def stage(parent):
        result = real_stage(parent)
        opened.append(result)
        return result
    monkeypatch.setattr(backup, "_new_stage", stage)
    path = tmp_path / "backup.json"
    exported = export_workspace_backup(record, path)
    assert preview_workspace_backup(path) == exported
    assert len(opened) == 1
    descriptor, stage_path = opened[0]
    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert not Path(stage_path).exists()
    assert not list(tmp_path.glob(".webjam-backup-*"))


@pytest.mark.skipif(os.name != "nt", reason="Real Windows native handle ownership")
def test_windows_native_handle_closes_if_crt_transfer_fails(tmp_path, record, monkeypatch):
    import ctypes
    from ctypes import wintypes
    import msvcrt
    handles = []
    def fail(handle, _flags):
        handles.append(handle)
        raise OSError("injected CRT ownership transfer failure")
    monkeypatch.setattr(msvcrt, "open_osfhandle", fail)
    with pytest.raises(WorkspaceBackupError):
        export_workspace_backup(record, tmp_path / "backup.json")
    assert len(handles) == 1
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    information = kernel.GetHandleInformation
    information.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    information.restype = wintypes.BOOL
    flags = wintypes.DWORD()
    assert not information(handles[0], ctypes.byref(flags))
    assert ctypes.get_last_error() == 6  # ERROR_INVALID_HANDLE
    assert not (tmp_path / "backup.json").exists()
