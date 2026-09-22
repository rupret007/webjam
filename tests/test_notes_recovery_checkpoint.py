"""Exact bounded checkpoints never acknowledge or overwrite another revision."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import FrozenInstanceError
from types import MappingProxyType
from unittest.mock import Mock, patch

import pytest

from core import notes_recovery
from core.notes_recovery import (
    MAX_RECOVERY_DRAFT_BYTES,
    MAX_RECOVERY_FILE_BYTES,
    NotesRecoveryConflict,
    NotesRecoveryDraft,
    NotesRecoveryError,
    notes_fingerprint,
    read_notes_recovery,
    write_notes_recovery,
)


@pytest.fixture
def checkpoint(tmp_path):
    path = tmp_path / "notes-recovery.json"
    # Independent on-disk fixture: no checkpoint writer constructs these bytes.
    path.write_bytes(
        b'{"version":1,"profiles":{"music":{"text":"  idea\\nend  ",'
        b'"baseline_fingerprint":"' + b"a" * 64 + b'"},'
        b'"art":{"text":"","baseline_fingerprint":null}}}'
    )
    return path


def test_fixture_load_and_roundtrip_preserve_exact_drafts(checkpoint):
    loaded = read_notes_recovery(checkpoint)
    assert loaded == {
        "music": NotesRecoveryDraft("  idea\nend  ", "a" * 64),
        "art": NotesRecoveryDraft("", None),
    }
    changed = {**loaded, "podcast_voice": NotesRecoveryDraft("🎙 Voice\r\n", notes_fingerprint(None))}
    write_notes_recovery(checkpoint, MappingProxyType(changed), expected=loaded)
    assert read_notes_recovery(checkpoint) == changed
    write_notes_recovery(checkpoint, {}, expected=changed)
    assert read_notes_recovery(checkpoint) == {}
    assert json.loads(checkpoint.read_text()) == {"version": 1, "profiles": {}}


def test_missing_checkpoint_is_read_only_and_fingerprints_distinguish_empty(tmp_path):
    missing = tmp_path / "not-created.json"
    assert read_notes_recovery(missing) == {}
    assert not missing.exists()
    values = (None, "", " ", "x", "x\n", "🎨")
    fingerprints = {notes_fingerprint(value) for value in values}
    assert len(fingerprints) == len(values)
    assert all(len(value) == 64 and set(value) <= set("0123456789abcdef") for value in fingerprints)
    assert notes_fingerprint("🎨") == notes_fingerprint("🎨")
    with pytest.raises(NotesRecoveryError):
        notes_fingerprint(12)


def test_draft_is_frozen_and_exact_utf8_limit_roundtrips(tmp_path):
    text = "🎨" * (MAX_RECOVERY_DRAFT_BYTES // 4)
    draft = NotesRecoveryDraft(text, notes_fingerprint(""))
    with pytest.raises(FrozenInstanceError):
        draft.text = "different"
    with pytest.raises(NotesRecoveryError):
        NotesRecoveryDraft(text + "x", None)
    path = tmp_path / "at-limit.json"
    write_notes_recovery(path, {"art": draft}, expected={})
    assert read_notes_recovery(path) == {"art": draft}
    assert path.stat().st_size < MAX_RECOVERY_FILE_BYTES
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("fingerprint", ["", "A" * 64, "g" * 64, "a" * 63, 7, False])
def test_baseline_fingerprints_must_be_bounded_hashes(fingerprint):
    with pytest.raises(NotesRecoveryError):
        NotesRecoveryDraft("Keep this", fingerprint)


@pytest.mark.parametrize("raw", [
    b"\xffinvalid UTF-8", b"{unfinished", b"[]", b"{}",
    b'{"version":true,"profiles":{}}',
    b'{"version":2,"profiles":{}}',
    b'{"version":1,"profiles":{},"unknown":1}',
    b'{"version":1,"version":1,"profiles":{}}',
    b'{"version":1,"profiles":{"music":{},"music":{}}}',
    b'{"version":1,"profiles":{"music":{"text":"a","text":"b","baseline_fingerprint":null}}}',
    b'{"version":1,"profiles":{"podcast":{"text":"a","baseline_fingerprint":null}}}',
    b'{"version":1,"profiles":{"music":{"text":"a"}}}',
    b'{"version":1,"profiles":{"music":{"text":"a","baseline_fingerprint":null,"extra":1}}}',
    b'{"version":1,"profiles":{"music":{"text":NaN,"baseline_fingerprint":null}}}',
    b'{"version":1,"profiles":{"music":{"text":1,"baseline_fingerprint":null}}}',
    b'{"version":1,"profiles":{"music":{"text":"\\ud800","baseline_fingerprint":null}}}',
])
def test_invalid_journal_is_preserved_on_read_and_write(tmp_path, raw):
    path = tmp_path / "invalid.json"
    path.write_bytes(raw)
    for operation in (
        lambda: read_notes_recovery(path),
        lambda: write_notes_recovery(path, {}, expected={}),
    ):
        with pytest.raises(NotesRecoveryError):
            operation()
        assert path.read_bytes() == raw


def test_invalid_or_oversized_replacement_preserves_other_workspaces(checkpoint, monkeypatch):
    previous = read_notes_recovery(checkpoint)
    original_bytes = checkpoint.read_bytes()
    with pytest.raises(NotesRecoveryError):
        write_notes_recovery(checkpoint, {"unknown": NotesRecoveryDraft("new", None)}, expected=previous)
    with pytest.raises(NotesRecoveryError):
        write_notes_recovery(checkpoint, {"art": {"text": "untyped"}}, expected=previous)
    # Escaping can exceed the file cap even when the UTF-8 draft itself fits.
    monkeypatch.setattr(notes_recovery, "MAX_RECOVERY_FILE_BYTES", 512)
    changed = {**previous, "art": NotesRecoveryDraft("\x01" * 200, None)}
    writer = Mock()
    monkeypatch.setattr(notes_recovery, "atomic_write_bytes", writer)
    with pytest.raises(NotesRecoveryError):
        write_notes_recovery(checkpoint, changed, expected=previous)
    writer.assert_not_called()
    assert checkpoint.read_bytes() == original_bytes


def test_oversized_stored_file_is_rejected_without_reading_or_replacing(tmp_path, monkeypatch):
    path = tmp_path / "oversized.json"
    with path.open("wb") as handle:
        handle.truncate(MAX_RECOVERY_FILE_BYTES + 1)
    opener = Mock(side_effect=AssertionError("must reject before opening"))
    monkeypatch.setattr(notes_recovery.os, "open", opener)
    with pytest.raises(NotesRecoveryError):
        read_notes_recovery(path)
    with pytest.raises(NotesRecoveryError):
        write_notes_recovery(path, {}, expected={})
    opener.assert_not_called()
    assert path.stat().st_size == MAX_RECOVERY_FILE_BYTES + 1


def test_symlinked_checkpoint_never_follows_or_replaces_target(tmp_path, checkpoint):
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(checkpoint)
    except (NotImplementedError, OSError):
        pytest.skip("Symbolic links are unavailable in this environment")
    original = checkpoint.read_bytes()
    with pytest.raises(NotesRecoveryError):
        read_notes_recovery(link)
    with pytest.raises(NotesRecoveryError):
        write_notes_recovery(link, {}, expected={})
    assert link.is_symlink()
    assert checkpoint.read_bytes() == original


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_nonregular_checkpoint_is_rejected_before_open(tmp_path, monkeypatch, kind):
    path = tmp_path / "nonregular"
    if kind == "directory":
        path.mkdir()
    elif hasattr(os, "mkfifo"):
        os.mkfifo(path)
    else:
        pytest.skip("Named pipes are unavailable on this platform")
    opener = Mock(side_effect=AssertionError("must reject before opening"))
    monkeypatch.setattr(notes_recovery.os, "open", opener)
    with pytest.raises(NotesRecoveryError):
        read_notes_recovery(path)
    with pytest.raises(NotesRecoveryError):
        write_notes_recovery(path, {}, expected={})
    opener.assert_not_called()
    assert path.exists()


def test_changed_checkpoint_or_deleted_revision_is_never_overwritten(checkpoint):
    old = read_notes_recovery(checkpoint)
    other = {**old, "art": NotesRecoveryDraft("Another process's draft", None)}
    write_notes_recovery(checkpoint, other, expected=old)
    other_bytes = checkpoint.read_bytes()
    with pytest.raises(NotesRecoveryConflict):
        write_notes_recovery(checkpoint, {}, expected=old)
    assert checkpoint.read_bytes() == other_bytes
    checkpoint.unlink()
    with pytest.raises(NotesRecoveryConflict):
        write_notes_recovery(checkpoint, old, expected=other)
    assert not checkpoint.exists()


def test_failed_publication_leaves_last_good_bytes_and_all_drafts(checkpoint):
    previous = read_notes_recovery(checkpoint)
    original = checkpoint.read_bytes()
    changed = {**previous, "art": NotesRecoveryDraft("New art idea", None)}
    with patch("core.file_io.os.replace", side_effect=OSError("replacement failed")):
        with pytest.raises(OSError):
            write_notes_recovery(checkpoint, changed, expected=previous)
    assert checkpoint.read_bytes() == original
    assert read_notes_recovery(checkpoint) == previous
    assert list(checkpoint.parent.iterdir()) == [checkpoint]
    write_notes_recovery(checkpoint, changed, expected=previous)
    assert read_notes_recovery(checkpoint) == changed


def test_fsync_exception_keeps_published_revision_unacknowledged_until_retry(checkpoint):
    previous = read_notes_recovery(checkpoint)
    changed = {**previous, "art": NotesRecoveryDraft("Published but unconfirmed", None)}
    with patch("core.file_io._fsync_parent_directory", side_effect=OSError("sync failed")):
        with pytest.raises(OSError):
            write_notes_recovery(checkpoint, changed, expected=previous)
    assert read_notes_recovery(checkpoint) == changed
    with pytest.raises(NotesRecoveryConflict):
        write_notes_recovery(checkpoint, changed, expected=previous)
    write_notes_recovery(checkpoint, changed, expected=changed)
    assert read_notes_recovery(checkpoint) == changed
