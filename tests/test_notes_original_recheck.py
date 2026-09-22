"""Recheck saved notes without replacing either version during recovery."""
from __future__ import annotations

import errno
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from core import file_io
from core.notes_recovery import read_notes_recovery
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.session_persistence import SessionPersistence


class Canvas:
    def __init__(self):
        self.text = ""
        self.state = ""

    def current_notes(self):
        return self.text

    def restore_notes(self, text):
        self.text = text

    def set_notes_save_state(self, state):
        self.state = state


@pytest.fixture
def blocked_notes(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    original = tmp_path / ".webjam_notes.md"
    original.write_bytes(b"\xffsaved notes currently unreadable")
    canvas = Canvas()
    owner = SessionPersistence(SimpleNamespace(), canvas)
    owner._load_notes_only()
    draft = "Keep this draft written while saved notes were unavailable."
    canvas.text = draft
    owner.notes_changed(draft)
    assert owner._save_notes_only() is False
    assert owner.notes_recovery_state("music") == "protected_original"
    return SimpleNamespace(
        owner=owner, canvas=canvas, original=original, draft=draft,
        journal=tmp_path / ".webjam_notes.recovery.json", copy=tmp_path / "draft-copy.md",
    )


def repair_and_recheck(case, saved="Saved notes after repair"):
    case.original.write_text(saved)
    snapshot = case.owner.recheck_notes_original("music", case.draft)
    assert snapshot is not None
    assert snapshot.text == saved
    return snapshot


def attempt_resolution(case, snapshot, path=None):
    """IO may raise for bounded dialog copy; stale revisions must not succeed."""
    try:
        return case.owner.export_draft_and_use_original(
            "music", case.draft, snapshot, str(path or case.copy),
        )
    except (OSError, ValueError):
        return False


def test_recheck_reads_repaired_original_without_mutating_draft_or_checkpoint(blocked_notes):
    case = blocked_notes
    before_checkpoint = case.journal.read_bytes()
    before_pending = case.owner.unsaved_notes
    with mock.patch.object(file_io, "_atomic_write", side_effect=AssertionError("Recheck must not write")):
        snapshot = repair_and_recheck(case)
    assert snapshot.text == "Saved notes after repair"
    assert case.owner.unsaved_notes == before_pending
    assert case.canvas.text == case.draft
    assert case.owner.notes_recovery_state("music") == "protected_original"
    assert case.journal.read_bytes() == before_checkpoint


@pytest.mark.parametrize("kind", ["invalid", "missing", "directory", "symlink"])
def test_unavailable_original_has_no_preview_and_never_unprotects_draft(blocked_notes, tmp_path, kind):
    case = blocked_notes
    if kind != "invalid":
        case.original.unlink()
    if kind == "directory":
        case.original.mkdir()
    elif kind == "symlink":
        target = tmp_path / "untouched.md"
        target.write_text("Keep this saved original")
        case.original.symlink_to(target)
    assert case.owner.recheck_notes_original("music", case.draft) is None
    assert case.owner.unsaved_notes == (("music", case.draft),)
    assert case.owner.notes_recovery_state("music") == "protected_original"
    if kind == "symlink":
        assert target.read_text() == "Keep this saved original"


def test_existing_empty_original_is_a_readable_snapshot(blocked_notes):
    snapshot = repair_and_recheck(blocked_notes, saved="")
    assert snapshot.text == ""
    assert blocked_notes.owner.has_unsaved_notes


def test_success_exports_exact_draft_then_adopts_original_without_writing_it(blocked_notes):
    case = blocked_notes
    snapshot = repair_and_recheck(case)
    real_write = file_io._atomic_write
    writes = []

    def track_write(path, data, *, mode):
        writes.append(Path(path).name)
        return real_write(path, data, mode=mode)

    with mock.patch.object(file_io, "_atomic_write", side_effect=track_write):
        assert case.owner.export_draft_and_use_original("music", case.draft, snapshot, str(case.copy))
    assert ".webjam_notes.md" not in writes
    assert case.copy.read_text() == case.draft
    assert case.original.read_text() == "Saved notes after repair"
    assert case.canvas.text == "Saved notes after repair"
    assert not case.owner.has_unsaved_notes
    assert case.owner.notes_save_state == "saved"
    assert read_notes_recovery(case.journal) == {}
    assert case.owner._save_notes_only()

    case.canvas.text = "A new edit made after adopting the saved notes"
    case.owner.notes_changed(case.canvas.text)
    assert case.owner._save_notes_only()
    assert case.original.read_text() == case.canvas.text
    restarted_canvas = Canvas()
    restarted = SessionPersistence(SimpleNamespace(), restarted_canvas)
    restarted._load_notes_only()
    assert restarted_canvas.text == case.canvas.text
    assert not restarted.has_unsaved_notes


def test_hidden_workspace_adoption_keeps_active_art_notes_and_namespace(blocked_notes, tmp_path):
    case = blocked_notes
    (tmp_path / ".webjam_notes.art.md").write_text("Active Art notes")
    case.owner.switch_profile_key("art")
    snapshot = repair_and_recheck(case)
    assert case.owner.export_draft_and_use_original("music", case.draft, snapshot, str(case.copy))
    assert case.owner.profile_key == "art"
    assert case.canvas.text == "Active Art notes"
    assert case.copy.read_text() == case.draft
    assert case.original.read_text() == "Saved notes after repair"
    case.owner.switch_profile_key("music")
    assert case.canvas.text == "Saved notes after repair"


def test_snapshot_cannot_be_rebound_to_a_newer_draft(blocked_notes):
    case = blocked_notes
    snapshot = repair_and_recheck(case)
    newer = "A newer draft that was never reviewed with this snapshot"
    case.canvas.text = newer
    case.owner.notes_changed(newer)
    assert not case.owner.export_draft_and_use_original("music", newer, snapshot, str(case.copy))
    assert case.owner.unsaved_notes == (("music", newer),)
    assert case.canvas.text == newer
    assert case.original.read_text() == "Saved notes after repair"
    assert not case.copy.exists()


def test_original_change_after_preview_prevents_adoption(blocked_notes):
    case = blocked_notes
    snapshot = repair_and_recheck(case)
    case.original.write_text("Saved notes changed after preview")
    assert not attempt_resolution(case, snapshot)
    assert case.original.read_text() == "Saved notes changed after preview"
    assert case.canvas.text == case.draft
    assert case.owner.has_unsaved_notes
    if case.copy.exists():
        assert case.copy.read_text() == case.draft


@pytest.mark.parametrize("change", ["draft", "original"])
def test_change_during_copy_keeps_both_versions_and_does_not_adopt_stale_preview(blocked_notes, change):
    case = blocked_notes
    snapshot = repair_and_recheck(case)
    real_write = file_io._atomic_write

    def write_then_change(path, data, *, mode):
        result = real_write(path, data, mode=mode)
        if Path(path) == case.copy:
            if change == "draft":
                case.canvas.text = "Newer retained draft"
                case.owner.notes_changed(case.canvas.text)
            else:
                case.original.write_text("Newer external original")
        return result

    with mock.patch.object(file_io, "_atomic_write", side_effect=write_then_change):
        assert not attempt_resolution(case, snapshot)
    assert case.copy.read_text() == case.draft
    assert case.owner.has_unsaved_notes
    if change == "draft":
        assert case.canvas.text == "Newer retained draft"
        assert dict(case.owner.unsaved_notes)["music"] == "Newer retained draft"
        assert case.original.read_text() == "Saved notes after repair"
    else:
        assert case.canvas.text == case.draft
        assert case.original.read_text() == "Newer external original"


@pytest.mark.parametrize("published", [False, True])
def test_unconfirmed_export_never_adopts_saved_original(blocked_notes, published):
    case = blocked_notes
    snapshot = repair_and_recheck(case)
    checkpoint = case.journal.read_bytes()
    real_write = file_io._atomic_write

    def fail_copy(path, data, *, mode):
        if Path(path) != case.copy:
            return real_write(path, data, mode=mode)
        if published:
            with mock.patch.object(file_io, "_fsync_parent_directory", side_effect=OSError(errno.EIO, "Controlled sync failure")):
                return real_write(path, data, mode=mode)
        raise OSError(errno.ENOSPC, "Controlled copy failure")

    with mock.patch.object(file_io, "_atomic_write", side_effect=fail_copy):
        assert not attempt_resolution(case, snapshot)
    assert case.canvas.text == case.draft
    assert case.owner.unsaved_notes == (("music", case.draft),)
    assert case.original.read_text() == "Saved notes after repair"
    assert case.journal.read_bytes() == checkpoint
    assert case.copy.exists() == published


@pytest.mark.parametrize("destination", ["original", "journal"])
def test_resolution_cannot_export_over_original_or_recovery_journal(blocked_notes, destination):
    case = blocked_notes
    snapshot = repair_and_recheck(case)
    path = case.original if destination == "original" else case.journal
    previous = path.read_bytes()
    assert not attempt_resolution(case, snapshot, path)
    assert path.read_bytes() == previous
    assert case.owner.has_unsaved_notes
    assert case.canvas.text == case.draft


def test_successful_resolution_cannot_reuse_its_snapshot(blocked_notes):
    case = blocked_notes
    snapshot = repair_and_recheck(case)
    assert case.owner.export_draft_and_use_original("music", case.draft, snapshot, str(case.copy))
    another = case.copy.with_name("duplicate.md")
    assert not case.owner.export_draft_and_use_original("music", case.draft, snapshot, str(another))
    assert not another.exists()
    assert case.canvas.text == "Saved notes after repair"


def test_snapshot_from_another_workspace_cannot_authorize_adoption(blocked_notes, tmp_path):
    case = blocked_notes
    snapshot = repair_and_recheck(case)
    art = tmp_path / ".webjam_notes.art.md"
    art.write_bytes(b"\xffunreadable")
    case.owner.switch_profile_key("art")
    case.canvas.text = case.draft
    case.owner.notes_changed(case.draft)
    assert not case.owner._save_notes_only()
    art.write_text(snapshot.text)
    assert not case.owner.export_draft_and_use_original("art", case.draft, snapshot, str(case.copy))
    assert not case.copy.exists()
    assert art.read_text() == snapshot.text
    assert set(dict(case.owner.unsaved_notes)) == {"music", "art"}


def test_checkpoint_cleanup_failure_keeps_durable_copy_and_review_only_restart(blocked_notes):
    case = blocked_notes
    snapshot = repair_and_recheck(case)
    checkpoint = case.journal.read_bytes()
    real_write = file_io._atomic_write

    def fail_cleanup(path, data, *, mode):
        if Path(path) == case.journal:
            raise OSError(errno.ENOSPC, "Controlled cleanup failure")
        return real_write(path, data, mode=mode)

    with mock.patch.object(file_io, "_atomic_write", side_effect=fail_cleanup):
        assert case.owner.export_draft_and_use_original("music", case.draft, snapshot, str(case.copy))
    assert case.copy.read_text() == case.draft
    assert case.original.read_text() == case.canvas.text == snapshot.text
    assert not case.owner.has_unsaved_notes
    assert case.journal.read_bytes() == checkpoint
    restarted = SessionPersistence(SimpleNamespace(), Canvas())
    restarted._load_notes_only()
    assert restarted.notes_recovery_state("music") == "recovery_conflict"
    assert not restarted._save_notes_only()
    assert case.original.read_text() == snapshot.text


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture
def recheck_dialog(blocked_notes, qapp, monkeypatch):
    from webjam_qt.theme import load_stylesheet
    from webjam_qt.widgets.notes_recovery_dialog import NotesOriginalPreviewDialog, NotesRecoveryDialog

    dialog = NotesRecoveryDialog(blocked_notes.owner)
    dialog.setStyleSheet(load_stylesheet())
    dialog.show()
    dialog.activateWindow()
    qapp.processEvents()
    previews = []

    def open_preview(preview):
        previews.append(preview)
        preview.show()
        preview.activateWindow()
        qapp.processEvents()
        return preview.DialogCode.Rejected

    monkeypatch.setattr(NotesOriginalPreviewDialog, "exec", open_preview)
    yield dialog, previews
    dialog.reject()
    dialog.deleteLater()
    qapp.processEvents()


def test_recheck_keyboard_action_previews_read_only_original_and_cancel_keeps_draft(
    blocked_notes, recheck_dialog, qapp,
):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    case = blocked_notes
    case.original.write_text("Saved notes ready to review")
    dialog, previews = recheck_dialog
    assert dialog._recheck.isVisibleTo(dialog)
    assert not dialog._save.isEnabled()
    assert dialog._export.isEnabled()
    dialog._recheck.setFocus()
    QTest.keyClick(dialog._recheck, Qt.Key.Key_Space)
    assert len(previews) == 1
    preview = previews[0]
    assert preview._preview.isReadOnly()
    assert preview._preview.toPlainText() == "Saved notes ready to review"
    assert preview._preview.accessibleName() == "Current saved notes"
    preview._preview.setFocus()
    QTest.keyClicks(preview._preview, "Attempt to type into saved notes")
    assert preview._preview.toPlainText() == "Saved notes ready to review"
    preview.reject()
    assert case.owner.unsaved_notes == (("music", case.draft),)
    assert case.canvas.text == dialog._editor.toPlainText() == case.draft
    assert case.original.read_text() == "Saved notes ready to review"


def test_preview_export_action_adopts_original_and_closing_dialog_cannot_restore_old_draft(
    blocked_notes, recheck_dialog, monkeypatch,
):
    case = blocked_notes
    case.original.write_text("Reviewed saved notes")
    dialog, previews = recheck_dialog
    dialog._recheck.click()
    preview = previews[0]
    monkeypatch.setattr(
        "webjam_qt.widgets.notes_recovery_dialog.QFileDialog.getSaveFileName",
        lambda *args: (str(case.copy), ""),
    )
    preview._adopt.click()
    assert case.copy.read_text() == case.draft
    assert case.original.read_text() == case.canvas.text == "Reviewed saved notes"
    assert preview.result() == preview.DialogCode.Accepted
    assert dialog.result() == dialog.DialogCode.Accepted
    dialog.reject()
    assert case.canvas.text == "Reviewed saved notes"
    assert not case.owner.has_unsaved_notes
    assert case.owner._save_notes_only()
    assert case.original.read_text() == "Reviewed saved notes"


@pytest.mark.parametrize("change", ["cancel", "draft", "original"])
def test_native_picker_cancellation_or_stale_revision_never_adopts(
    blocked_notes, recheck_dialog, monkeypatch, change,
):
    case = blocked_notes
    case.original.write_text("Reviewed saved notes")
    dialog, previews = recheck_dialog
    dialog._recheck.click()
    preview = previews[0]

    def choose_copy(*args):
        if change == "cancel":
            return "", ""
        if change == "draft":
            case.canvas.text = "New draft during native picker"
            case.owner.notes_changed(case.canvas.text)
        else:
            case.original.write_text("New saved notes during native picker")
        return str(case.copy), ""

    monkeypatch.setattr(
        "webjam_qt.widgets.notes_recovery_dialog.QFileDialog.getSaveFileName", choose_copy,
    )
    preview._adopt.click()
    assert preview.isVisible()
    assert case.owner.has_unsaved_notes
    assert not case.copy.exists()
    if change == "cancel":
        assert preview._adopt.isEnabled()
    else:
        assert not preview._adopt.isEnabled()
        assert "choose Save Notes again" in preview._message.text()
        assert preview._message.accessibleDescription() == preview._message.text()
    assert case.canvas.text == ("New draft during native picker" if change == "draft" else case.draft)
    assert case.original.read_text() == (
        "New saved notes during native picker" if change == "original" else "Reviewed saved notes"
    )


def test_failed_preview_export_is_specific_and_remains_retryable(blocked_notes, recheck_dialog, monkeypatch):
    case = blocked_notes
    case.original.write_text("Reviewed saved notes")
    dialog, previews = recheck_dialog
    dialog._recheck.click()
    preview = previews[0]
    monkeypatch.setattr(
        "webjam_qt.widgets.notes_recovery_dialog.QFileDialog.getSaveFileName",
        lambda *args: (str(case.copy), ""),
    )
    with mock.patch.object(file_io, "_atomic_write", side_effect=OSError(errno.EACCES, "PRIVATE error")):
        preview._adopt.click()
    assert "permission" in preview._message.text()
    assert "PRIVATE" not in preview._message.text()
    assert preview._message.accessibleDescription() == preview._message.text()
    assert preview._adopt.isEnabled()
    assert case.owner.has_unsaved_notes
    assert case.original.read_text() == "Reviewed saved notes"
    preview._adopt.click()
    assert case.copy.read_text() == case.draft
    assert not case.owner.has_unsaved_notes


@pytest.mark.parametrize("stretch", [100, 125])
def test_recheck_and_preview_fit_compact_dialogs_without_stealing_editor_focus(
    blocked_notes, recheck_dialog, qapp, stretch,
):
    from PySide6.QtCore import QPoint, QRect, QSize
    from PySide6.QtWidgets import QLabel, QPushButton, QWidget

    case = blocked_notes
    case.original.write_text("Saved notes\n\nA quiet final verse.")
    dialog, previews = recheck_dialog
    for widget in [dialog, *dialog.findChildren(QWidget)]:
        font = widget.font()
        font.setStretch(stretch)
        widget.setFont(font)
    dialog._editor.setFocus()
    qapp.processEvents()
    dialog._show_recovery_guidance()
    assert dialog._editor.hasFocus()
    dialog._recheck.click()
    preview = previews[0]
    for widget in [preview, *preview.findChildren(QWidget)]:
        font = widget.font()
        font.setStretch(stretch)
        widget.setFont(font)
    for surface in (dialog, preview):
        qapp.processEvents()
        assert surface.size() == QSize(560, 420)
        for widget in surface.findChildren(QPushButton) + surface.findChildren(QLabel):
            if not widget.isVisibleTo(surface) or widget.window() is not surface:
                continue
            rect = QRect(widget.mapTo(surface, QPoint()), widget.size())
            assert surface.rect().contains(rect), (widget.objectName(), rect)
            if isinstance(widget, QPushButton):
                assert widget.width() >= widget.minimumSizeHint().width(), widget.text()
            elif widget.wordWrap():
                assert widget.height() >= widget.heightForWidth(widget.width())
        folder = os.environ.get("WEBJAM_TEST_RECHECK_SCREENSHOTS")
        if folder:
            path = Path(folder)
            path.mkdir(parents=True, exist_ok=True)
            name = "preview" if surface is preview else "recheck"
            assert surface.grab().save(str(path / f"notes-{name}-font{stretch}.png"))
