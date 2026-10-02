"""Library UI slice for metadata-only workspace backup: back up, preview, import."""
from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QLabel, QPushButton

from core.session_library import SessionLibrary, SessionLibraryConflict
from core.workspace_backup import (
    export_workspace_backup,
    import_workspace_backup,
    preview_workspace_backup,
)
from webjam_qt.windows.session_library import SessionLibraryDialog, WorkspaceBackupPreviewDialog

from tests import test_workspace_backup as backup_tests

record = backup_tests.record


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_dialog(app):
    dialogs = []

    def create(library, **kwargs):
        dialog = SessionLibraryDialog(library, **kwargs)
        dialogs.append(dialog)
        return dialog

    yield create
    for dialog in dialogs:
        dialog.timer.stop()
        dialog._dirty = False
        dialog.close()
        dialog.deleteLater()
    app.processEvents()


def _click(dialog, label):
    next(button for button in dialog.findChildren(QPushButton) if button.text() == label).click()


def test_backup_exports_merged_snapshot_even_when_editor_is_clean(tmp_path, make_dialog, monkeypatch, record):
    library = SessionLibrary(tmp_path / "source")
    late_recap = {"summary": "Late recap added by the coordinator",
                  "ended_at": "2026-10-02T01:00:00+00:00"}

    def reconcile(_base, edited):
        return library.save(replace(edited, recaps=(*edited.recaps, late_recap)))

    dialog = make_dialog(library, current_id=record.id, save_record=reconcile)
    assert not dialog._dirty
    destination = tmp_path / "backup.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(destination), ""))
    _click(dialog, "Back up workspace…")
    preview = preview_workspace_backup(destination)
    assert preview.record.recaps[-1]["summary"] == "Late recap added by the coordinator"
    assert "backed up" in dialog.status.text()
    assert "media files are not included" in dialog.status.text()
    assert not dialog._dirty


def test_backup_blocked_by_conflict_does_not_export_stale_snapshot(tmp_path, make_dialog, monkeypatch, record):
    library = SessionLibrary(tmp_path / "source")

    def reconcile(_base, _edited):
        raise SessionLibraryConflict("Workspace changed while this editor was open.")

    dialog = make_dialog(library, current_id=record.id, save_record=reconcile)
    destination = tmp_path / "backup.json"
    asked = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        lambda *_a, **_k: asked.append(True) or (str(destination), ""))
    _click(dialog, "Back up workspace…")
    assert asked == []
    assert not destination.exists()
    assert "not saved" in dialog.status.text()


def test_backup_reports_export_failure_without_crashing(tmp_path, make_dialog, monkeypatch, record):
    library = SessionLibrary(tmp_path / "source")
    dialog = make_dialog(library, current_id=record.id)
    existing = tmp_path / "already-there.json"
    existing.write_text("not a backup")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(existing), ""))
    _click(dialog, "Back up workspace…")
    assert existing.read_text() == "not a backup"
    assert "not backed up" in dialog.status.text()


def test_import_preview_shows_title_profile_date_counts_and_duplicate_info(tmp_path, app, record):
    library = SessionLibrary(tmp_path / "dest")
    destination = tmp_path / "backup.json"
    export_workspace_backup(record, destination)
    preview = preview_workspace_backup(destination)
    dialog = WorkspaceBackupPreviewDialog(library, preview)
    try:
        text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        assert record.title in text
        assert "music" in text
        assert record.updated_at in text
        assert "Take links: 1" in text
        assert "Session history entries: 1" in text
        assert "Rehearsal songs: 1" in text
        assert "No prior import of this exact backup found here." in text
        assert "audio, artwork and other referenced files are not included." in text
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_import_preview_reports_prior_imports_of_same_backup(tmp_path, app, record):
    library = SessionLibrary(tmp_path / "dest")
    destination = tmp_path / "backup.json"
    export_workspace_backup(record, destination)
    import_workspace_backup(library, preview_workspace_backup(destination))
    preview_again = preview_workspace_backup(destination)
    dialog = WorkspaceBackupPreviewDialog(library, preview_again)
    try:
        text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        assert "Already imported here as 1 separate workspace(s)." in text
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_cancel_import_preview_leaves_library_unchanged_and_emits_no_signals(
    tmp_path, make_dialog, monkeypatch, record,
):
    dest_library = SessionLibrary(tmp_path / "dest")
    destination = tmp_path / "backup.json"
    export_workspace_backup(record, destination)
    dialog = make_dialog(dest_library)
    emitted = []
    dialog.copy_saved.connect(lambda *_a: emitted.append("copy_saved"))
    dialog.continue_requested.connect(lambda *_a: emitted.append("continue_requested"))
    dialog.take_open_requested.connect(lambda *_a: emitted.append("take_open_requested"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(destination), ""))
    monkeypatch.setattr(WorkspaceBackupPreviewDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    _click(dialog, "Import backup…")
    assert dest_library.list() == []
    assert emitted == []
    assert dialog.selected_record is None


def test_import_creates_new_workspace_without_selecting_or_continuing(
    tmp_path, make_dialog, monkeypatch, record,
):
    dest_library = SessionLibrary(tmp_path / "dest")
    destination = tmp_path / "backup.json"
    export_workspace_backup(record, destination)
    dialog = make_dialog(dest_library)
    emitted = []
    dialog.copy_saved.connect(lambda *_a: emitted.append("copy_saved"))
    dialog.continue_requested.connect(lambda *_a: emitted.append("continue_requested"))
    dialog.take_open_requested.connect(lambda *_a: emitted.append("take_open_requested"))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(destination), ""))
    monkeypatch.setattr(WorkspaceBackupPreviewDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    _click(dialog, "Import backup…")
    records = dest_library.list()
    assert len(records) == 1
    imported = records[0]
    assert imported.id != record.id
    assert imported.import_provenance
    assert imported.import_provenance[-1]["source_workspace_id"] == record.id
    assert emitted == []
    assert dialog.selected_record is None
    assert dialog.record is not None and dialog.record.id == imported.id
    assert "imported" in dialog.status.text()
    assert "media files are not included" in dialog.status.text()


def test_imported_pending_take_link_is_not_shown_as_finalizing(tmp_path, make_dialog):
    source_library = SessionLibrary(tmp_path / "source")
    pending = source_library.create("music", "Still recording", take_links=(
        {"take_id": "take-9", "take_path": "", "status": "pending",
         "run_id": "run-9", "recording_session_id": "session-9",
         "validated": False, "title": "Recording requested"},
    ))
    dest_library = SessionLibrary(tmp_path / "dest")
    destination = tmp_path / "backup.json"
    export_workspace_backup(pending, destination)
    imported = import_workspace_backup(dest_library, preview_workspace_backup(destination))
    dialog = make_dialog(dest_library, current_id=imported.id)
    assert dialog.takes.count() == 1
    text = dialog.takes.item(0).text()
    assert "historical reference" in text
    assert "finalizing" not in text
    assert not dialog.open_take_button.isEnabled()
    assert not dialog.relink_take_button.isEnabled()


def test_imported_take_link_with_path_stays_a_stored_link_not_checked(tmp_path, make_dialog, record):
    dest_library = SessionLibrary(tmp_path / "dest")
    destination = tmp_path / "backup.json"
    export_workspace_backup(record, destination)
    imported = import_workspace_backup(dest_library, preview_workspace_backup(destination))
    dialog = make_dialog(dest_library, current_id=imported.id)
    assert dialog.takes.count() == 1
    # The source fixture's take path never existed; display only reports it
    # missing for relinking, it is never opened, read, or otherwise verified.
    assert "missing; locate it" in dialog.takes.item(0).text()
    dialog.takes.setCurrentRow(0)
    assert dialog.relink_take_button.isEnabled()


def test_save_as_copy_of_imported_workspace_preserves_provenance(tmp_path, make_dialog, monkeypatch, record):
    dest_library = SessionLibrary(tmp_path / "dest")
    destination = tmp_path / "backup.json"
    export_workspace_backup(record, destination)
    imported = import_workspace_backup(dest_library, preview_workspace_backup(destination))
    dialog = make_dialog(dest_library, current_id=imported.id)
    monkeypatch.setattr(QInputDialog, "getText", lambda *_a, **_k: ("Imported copy", True))
    _click(dialog, "Save as copy…")
    copies = [item for item in dest_library.list() if item.id != imported.id]
    assert len(copies) == 1
    assert copies[0].import_provenance == imported.import_provenance
    assert copies[0].take_links == imported.take_links


def test_imported_workspace_survives_restart_with_provenance_intact(tmp_path, make_dialog, record):
    dest_root = tmp_path / "dest"
    destination = tmp_path / "backup.json"
    export_workspace_backup(record, destination)
    imported = import_workspace_backup(SessionLibrary(dest_root), preview_workspace_backup(destination))
    # A fresh process only has the on-disk library; no in-memory state carries over.
    restarted = SessionLibrary(dest_root)
    dialog = make_dialog(restarted, current_id=imported.id)
    assert dialog.record.id == imported.id
    assert dialog.record.import_provenance == imported.import_provenance
    assert dialog.title.text() == record.title
    assert dialog.notes.toPlainText() == record.notes
    assert not dialog._dirty


def test_import_preview_counts_art_references_for_art_profile_backup(tmp_path, app):
    from core.art_workspace import make_reference, normalize_art_workspace

    reference = make_reference(str(tmp_path / "private.kra"), kind="file", title="Painting project")
    art = normalize_art_workspace({"version": 1, "brief": "Light study", "references": [reference]})
    source = SessionLibrary(tmp_path / "source").create("art", "Painting workspace", art=art)
    dest_library = SessionLibrary(tmp_path / "dest")
    destination = tmp_path / "backup.json"
    export_workspace_backup(source, destination)
    preview = preview_workspace_backup(destination)
    dialog = WorkspaceBackupPreviewDialog(dest_library, preview)
    try:
        text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        assert "Art references: 1" in text
        assert "Rehearsal songs" not in text
        # The locator is a private local path; it must never appear in the preview.
        assert str(tmp_path) not in text
    finally:
        dialog.deleteLater()
        app.processEvents()


def test_title_near_byte_limit_loaded_without_truncation(tmp_path, make_dialog):
    long_title = ("é" * 255) + "x"  # 511 UTF-8 bytes; 256 characters, over the old 200-char cap.
    library = SessionLibrary(tmp_path)
    saved = library.create("music", long_title)
    dialog = make_dialog(library, current_id=saved.id)
    assert dialog.title.text() == long_title
    validator = dialog.title.validator()
    over_limit = long_title + ("z" * 50)
    state, _text, _pos = validator.validate(over_limit, len(over_limit))
    assert state == QValidator.State.Invalid
    state, _text, _pos = validator.validate(long_title, len(long_title))
    assert state == QValidator.State.Acceptable
