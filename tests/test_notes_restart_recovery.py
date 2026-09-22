"""Real persistence owners recover exact drafts without silently replacing originals."""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from core import file_io
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.session_persistence import SessionPersistence


JOURNAL = ".webjam_notes.recovery.json"
MUSIC = ".webjam_notes.md"
ART = ".webjam_notes.art.md"


class NotesCanvas:
    def __init__(self):
        self.text = ""
        self.save_state = ""
        self.recovery_context = ("music", ())

    def current_notes(self):
        return self.text

    def restore_notes(self, text):
        self.text = text

    def set_notes_save_state(self, state):
        self.save_state = state

    def set_notes_recovery_context(self, profile, summary):
        self.recovery_context = (profile, summary)


@pytest.fixture
def owners(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)

    def create(profile="music"):
        canvas = NotesCanvas()
        owner = SessionPersistence(SimpleNamespace(), canvas, creator_profile_key=profile)
        owner._load_notes_only()
        return canvas, owner

    return create


def edit(canvas, owner, text):
    canvas.text = text
    owner.notes_changed(text)


@pytest.fixture
def writes(monkeypatch):
    """Fault real writes by destination, including imported writer aliases."""
    original = file_io._atomic_write
    state = SimpleNamespace(blocked=set(), attempted=[], uncertain=set(), error_numbers={})

    def write(path, data, *, mode):
        name = Path(path).name
        state.attempted.append(name)
        if name in state.blocked or "*" in state.blocked:
            raise OSError(state.error_numbers.get(name, errno.ENOSPC), "Controlled local write failure")
        if name in state.uncertain:
            with mock.patch.object(
                file_io, "_fsync_parent_directory",
                side_effect=OSError(errno.EIO, "Controlled unconfirmed directory sync"),
            ):
                return original(path, data, mode=mode)
        return original(path, data, mode=mode)

    monkeypatch.setattr(file_io, "_atomic_write", write)
    return state


def retain_failed_music(owners, writes, *, draft="Retained music draft"):
    canvas, owner = owners()
    writes.blocked = {MUSIC}
    edit(canvas, owner, draft)
    assert owner._save_notes_only() is False
    writes.blocked.clear()
    return canvas, owner


def test_failed_primary_is_checkpointed_before_write_and_recovers_exactly(owners, writes, tmp_path):
    _, first = retain_failed_music(owners, writes)
    assert writes.attempted.index(JOURNAL) < writes.attempted.index(MUSIC)
    assert first.notes_restart_recovery_state("music") == "confirmed"
    assert (tmp_path / JOURNAL).is_file()
    assert not (tmp_path / MUSIC).exists()

    canvas, recovered = owners()
    assert canvas.current_notes() == "Retained music draft"
    assert recovered.unsaved_notes == (("music", "Retained music draft"),)
    assert recovered.notes_recovery_state("music") == "recovered"
    assert recovered.notes_restart_recovery_state("music") == "confirmed"


def test_recovered_draft_waits_for_explicit_save_including_ordinary_autosave(owners, writes, tmp_path):
    retain_failed_music(owners, writes)
    canvas, recovered = owners()
    writes.attempted.clear()
    assert recovered._save_notes_only() is False
    assert MUSIC not in writes.attempted
    assert canvas.current_notes() == "Retained music draft"
    assert not (tmp_path / MUSIC).exists()

    assert recovered.save_recovered_notes("music", "Retained music draft")
    assert (tmp_path / MUSIC).read_text() == "Retained music draft"
    assert not recovered.has_unsaved_notes
    _, restarted = owners()
    assert not restarted.has_unsaved_notes


def test_all_profiles_load_before_navigation_and_hidden_recovery_keeps_active_editor(owners, writes, tmp_path):
    (tmp_path / MUSIC).write_text("Saved music")
    (tmp_path / ART).write_text("Saved art")
    canvas, first = owners()
    writes.blocked = {MUSIC, ART}
    edit(canvas, first, "Unfinished music")
    first.switch_profile_key("art")
    edit(canvas, first, "Unfinished art")
    assert first._save_notes_only() is False
    writes.blocked.clear()

    canvas, recovered = owners("art")
    assert dict(recovered.unsaved_notes) == {"music": "Unfinished music", "art": "Unfinished art"}
    assert canvas.current_notes() == "Unfinished art"
    assert recovered.profile_key == "art"
    assert recovered.save_recovered_notes("music", "Unfinished music")
    assert recovered.profile_key == "art"
    assert canvas.current_notes() == "Unfinished art"
    assert (tmp_path / MUSIC).read_text() == "Unfinished music"
    assert (tmp_path / ART).read_text() == "Saved art"
    _, restarted = owners("art")
    assert restarted.unsaved_notes == (("art", "Unfinished art"),)


def test_changed_original_blocks_automatic_and_explicit_recovered_save(owners, writes, tmp_path):
    path = tmp_path / MUSIC
    path.write_text("Original before failure")
    retain_failed_music(owners, writes)
    path.write_text("A later external edit")
    canvas, recovered = owners()
    assert recovered.notes_recovery_state("music") == "recovery_conflict"
    assert recovered._save_notes_only() is False
    assert not recovered.save_recovered_notes("music", "Retained music draft")
    assert path.read_text() == "A later external edit"
    assert canvas.current_notes() == "Retained music draft"
    assert recovered.has_unsaved_notes


def test_original_changed_after_recovery_load_is_rechecked_at_explicit_save(owners, writes, tmp_path):
    path = tmp_path / MUSIC
    path.write_text("Known original")
    retain_failed_music(owners, writes)
    _, recovered = owners()
    assert recovered.notes_recovery_state("music") == "recovered"
    path.write_text("Changed after the recovery screen opened")
    assert not recovered.save_recovered_notes("music", "Retained music draft")
    assert path.read_text() == "Changed after the recovery screen opened"
    assert recovered.notes_recovery_state("music") == "recovery_conflict"


def test_unknown_original_baseline_is_not_inferred_from_empty_fallback(owners, writes, tmp_path):
    path = tmp_path / MUSIC
    path.write_bytes(b"\xffunreadable original")
    canvas, first = owners()
    edit(canvas, first, "New notes while original was unreadable")
    assert first._save_notes_only() is False
    path.write_text("Repaired external notes")

    _, recovered = owners()
    assert recovered.notes_recovery_state("music") == "recovery_conflict"
    assert not recovered.save_recovered_notes("music", "New notes while original was unreadable")
    assert path.read_text() == "Repaired external notes"


def test_recovered_draft_never_follows_replaced_original_symlink(owners, writes, tmp_path):
    retain_failed_music(owners, writes)
    target = tmp_path / "preserve.md"
    target.write_text("External original")
    (tmp_path / MUSIC).symlink_to(target)
    _, recovered = owners()
    assert recovered.notes_recovery_state("music") == "protected_original"
    assert recovered._save_notes_only() is False
    assert not recovered.save_recovered_notes("music", "Retained music draft")
    assert target.read_text() == "External original"
    assert (tmp_path / MUSIC).is_symlink()


def test_stale_journal_after_export_cannot_overwrite_later_original(owners, writes, tmp_path):
    path = tmp_path / MUSIC
    path.write_text("Earlier original")
    _, first = retain_failed_music(owners, writes)
    writes.blocked = {JOURNAL}
    copy = tmp_path / "retained-copy.md"
    first.export_pending_notes("music", "Retained music draft", str(copy))
    assert copy.read_text() == "Retained music draft"
    writes.blocked.clear()
    path.write_text("Later external original")

    _, recovered = owners()
    if recovered.has_unsaved_notes:
        assert recovered.notes_recovery_state("music") == "recovery_conflict"
        assert recovered._save_notes_only() is False
        assert not recovered.save_recovered_notes("music", "Retained music draft")
    assert path.read_text() == "Later external original"
    assert copy.read_text() == "Retained music draft"


@pytest.mark.parametrize("original", ["Original notes", ""])
def test_unconfirmed_primary_and_undo_keep_latest_exact_checkpoint(owners, writes, tmp_path, original):
    path = tmp_path / MUSIC
    path.write_text(original)
    canvas, first = owners()
    writes.uncertain = {MUSIC}
    edit(canvas, first, "Revised notes")
    assert first._save_notes_only() is False
    assert path.read_text() == "Revised notes"
    edit(canvas, first, original)
    assert first.has_unsaved_notes
    assert first._save_notes_only() is False
    assert first.unsaved_notes == (("music", original),)
    assert first.notes_restart_recovery_state("music") == "confirmed"
    writes.uncertain.clear()

    writes.attempted.clear()
    canvas, recovered = owners()
    assert canvas.current_notes() == original
    assert path.read_text() == original
    assert MUSIC not in writes.attempted
    # Current primary bytes exactly match the checkpoint, so a fresh owner
    # can load those bytes without proposing an obsolete recovery draft.
    assert not recovered.has_unsaved_notes


def test_newer_recovered_draft_rejects_stale_save_and_export(owners, writes, tmp_path):
    retain_failed_music(owners, writes)
    canvas, recovered = owners()
    edit(canvas, recovered, "A newer retained draft")
    copy = tmp_path / "stale.md"
    assert not recovered.export_pending_notes("music", "Retained music draft", str(copy))
    assert not recovered.save_recovered_notes("music", "Retained music draft")
    assert not copy.exists()
    assert recovered._save_notes_only() is False
    assert not (tmp_path / MUSIC).exists()
    assert recovered.notes_restart_recovery_state("music") == "confirmed"
    canvas, restarted = owners()
    assert canvas.current_notes() == "A newer retained draft"
    assert restarted.unsaved_notes == (("music", "A newer retained draft"),)


def test_checkpoint_failure_does_not_block_successful_primary_save(owners, writes, tmp_path):
    canvas, owner = owners()
    writes.blocked = {JOURNAL}
    edit(canvas, owner, "Saved directly to original")
    assert owner._save_notes_only()
    assert (tmp_path / MUSIC).read_text() == "Saved directly to original"
    assert not owner.has_unsaved_notes
    assert not (tmp_path / JOURNAL).exists()
    canvas, restarted = owners()
    assert canvas.current_notes() == "Saved directly to original"
    assert not restarted.has_unsaved_notes


def test_all_writes_fail_without_claiming_new_revision_is_restart_safe(owners, writes, tmp_path):
    canvas, first = retain_failed_music(owners, writes, draft="Earlier checkpoint")
    checkpoint = (tmp_path / JOURNAL).read_bytes()
    writes.blocked = {"*"}
    edit(canvas, first, "Newer unconfirmed draft")
    assert first._save_notes_only() is False
    assert first.unsaved_notes == (("music", "Newer unconfirmed draft"),)
    assert first.notes_restart_recovery_state("music") == "unconfirmed"
    assert (tmp_path / JOURNAL).read_bytes() == checkpoint
    writes.blocked.clear()
    canvas, restarted = owners()
    assert canvas.current_notes() == "Earlier checkpoint"
    assert restarted.unsaved_notes == (("music", "Earlier checkpoint"),)


def test_oversized_draft_does_not_destroy_other_confirmed_profile_checkpoint(owners, writes, tmp_path):
    from core.notes_recovery import MAX_RECOVERY_DRAFT_BYTES

    canvas, first = retain_failed_music(owners, writes, draft="Small music draft")
    checkpoint = (tmp_path / JOURNAL).read_bytes()
    writes.blocked = {MUSIC}
    first.switch_profile_key("art")
    oversized = "x" * (MAX_RECOVERY_DRAFT_BYTES + 1)
    edit(canvas, first, oversized)
    assert first._save_notes_only() is False
    assert dict(first.unsaved_notes) == {"music": "Small music draft", "art": oversized}
    assert first.notes_restart_recovery_state("music") == "confirmed"
    assert first.notes_restart_recovery_state("art") == "unconfirmed"
    assert (tmp_path / JOURNAL).read_bytes() == checkpoint
    writes.blocked.clear()
    _, restarted = owners("art")
    assert restarted.unsaved_notes == (("music", "Small music draft"),)


def test_corrupt_journal_is_preserved_without_false_restart_confirmation(owners, writes, tmp_path):
    corrupt = b'{"version":1,"profiles":invalid\xff'
    path = tmp_path / JOURNAL
    path.write_bytes(corrupt)
    (tmp_path / MUSIC).write_text("Existing saved notes")
    canvas, owner = owners()
    assert canvas.current_notes() == "Existing saved notes"
    assert not owner.has_unsaved_notes
    writes.blocked = {MUSIC}
    edit(canvas, owner, "New local draft")
    assert owner._save_notes_only() is False
    assert owner.has_unsaved_notes
    assert owner.notes_restart_recovery_state("music") == "unconfirmed"
    assert path.read_bytes() == corrupt


def test_export_cannot_replace_the_recovery_journal(owners, writes, tmp_path):
    _, owner = retain_failed_music(owners, writes)
    journal = tmp_path / JOURNAL
    checkpoint = journal.read_bytes()
    with pytest.raises(ValueError):
        owner.export_pending_notes("music", "Retained music draft", str(journal))
    assert journal.read_bytes() == checkpoint
    assert owner.has_unsaved_notes


@pytest.mark.parametrize(
    ("error_number", "reason"),
    [(errno.ENOSPC, "disk_full"), (errno.EACCES, "permission_denied"),
     (errno.EROFS, "read_only"), (errno.EIO, "failed")],
)
def test_failed_explicit_recovery_keeps_reason_and_review_ownership_until_explicit_retry(
    owners, writes, tmp_path, error_number, reason,
):
    retain_failed_music(owners, writes)
    _, recovered = owners()
    writes.blocked = {MUSIC}
    writes.error_numbers[MUSIC] = error_number
    assert not recovered.save_recovered_notes("music", "Retained music draft")
    assert recovered.notes_recovery_state("music") == reason
    assert recovered.notes_recovery_requires_review("music")
    assert recovered.unsaved_notes == (("music", "Retained music draft"),)
    assert recovered.notes_restart_recovery_state("music") == "confirmed"

    writes.blocked.clear()
    writes.attempted.clear()
    assert recovered._save_notes_only() is False
    assert MUSIC not in writes.attempted
    assert not (tmp_path / MUSIC).exists()
    assert recovered.save_recovered_notes("music", "Retained music draft")
    assert (tmp_path / MUSIC).read_text() == "Retained music draft"
    assert not recovered.has_unsaved_notes
    assert not recovered.notes_recovery_requires_review("music")


@pytest.mark.parametrize("kind", [
    "directory",
    pytest.param("fifo", marks=pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="No FIFO support")),
])
def test_hidden_non_regular_original_is_rejected_before_open_and_keeps_recovered_draft(
    owners, writes, tmp_path, monkeypatch, kind,
):
    retain_failed_music(owners, writes)
    original = tmp_path / MUSIC
    if kind == "fifo":
        os.mkfifo(original)
    else:
        original.mkdir()
    opened = []
    real_open = os.open

    def guarded_open(path, *args, **kwargs):
        if Path(path) == original:
            opened.append(path)
            # This deliberately fails the former reader before an actual
            # FIFO open can block the test or application startup.
            raise AssertionError("A non-regular notes original reached os.open")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(persistence_module.os, "open", guarded_open)
    with pytest.raises(ValueError, match="regular"):
        persistence_module._read_bounded_notes(original)
    canvas, recovered = owners("art")
    assert recovered.profile_key == "art"
    assert canvas.current_notes() == ""
    assert recovered.unsaved_notes == (("music", "Retained music draft"),)
    assert recovered.notes_recovery_state("music") == "protected_original"
    writes.attempted.clear()
    assert recovered._save_notes_only() is False
    assert not recovered.save_recovered_notes("music", "Retained music draft")
    assert MUSIC not in writes.attempted
    assert opened == []
    assert original.exists()


def test_fresh_process_restores_checkpoint_after_exit_without_shutdown(tmp_path):
    script = r'''
import errno
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from core import file_io
from webjam_qt.controllers import session_persistence as module

root, phase = Path(sys.argv[1]), sys.argv[2]
module._persistence_home = lambda: root
state = SimpleNamespace(text="")
canvas = SimpleNamespace(
    current_notes=lambda: state.text,
    restore_notes=lambda value: setattr(state, "text", value),
)
owner = module.SessionPersistence(SimpleNamespace(), canvas)
owner._load_notes_only()
real_write = file_io._atomic_write
attempted = []

def write(path, data, *, mode):
    name = Path(path).name
    attempted.append(name)
    if phase == "checkpoint" and name == ".webjam_notes.md":
        raise OSError(errno.ENOSPC, "Controlled primary write failure")
    return real_write(path, data, mode=mode)

file_io._atomic_write = write
if phase == "checkpoint":
    state.text = "Exact draft from the exited process\n"
    owner.notes_changed(state.text)
    assert owner._save_notes_only() is False
    assert owner.notes_restart_recovery_state("music") == "confirmed"
    # Exit without application shutdown, save(), finalizers, or shared owner
    # memory. Only the already-confirmed file can reach the next interpreter.
    os._exit(0)

assert state.text == "Exact draft from the exited process\n"
assert owner.unsaved_notes == (("music", state.text),)
assert owner.notes_recovery_requires_review("music")
assert owner._save_notes_only() is False
assert ".webjam_notes.md" not in attempted
assert not (root / ".webjam_notes.md").exists()
print(json.dumps({"text": state.text, "state": owner.notes_recovery_state("music")}))
'''
    for phase in ("checkpoint", "restart"):
        result = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), phase],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "text": "Exact draft from the exited process\n", "state": "recovered",
    }
