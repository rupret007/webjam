"""Local recovery names the blocked operation and keeps a working save route."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.widgets.notes_recovery_dialog import NotesRecoveryDialog
from webjam_qt.widgets.session_canvas import SessionCanvas


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def notes(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    canvas = SessionCanvas()
    owner = SessionPersistence(SimpleNamespace(), canvas)
    yield canvas, owner
    for dialog in canvas.findChildren(NotesRecoveryDialog):
        dialog.deleteLater()
    canvas.close()
    canvas.deleteLater()
    qapp.processEvents()


def test_unreadable_original_has_export_route_and_keeps_its_bytes(notes, tmp_path, monkeypatch):
    canvas, owner = notes
    original = tmp_path / ".webjam_notes.md"
    original.write_bytes(b"\xfforiginal notes")
    owner._load_notes_only()
    canvas.edit_notes("Keep this new idea")
    assert not owner._save_notes_only()

    assert owner.notes_save_state == "protected_original"
    assert "export your draft" in canvas._notes_save_status.text()
    assert not canvas._save_notes_button.isHidden()
    assert "without replacing the original" in canvas._save_notes_button.accessibleDescription()
    dialog = NotesRecoveryDialog(owner, canvas)
    assert "will not be overwritten" in dialog._message.text()
    assert "shorten" not in dialog._message.text().lower()
    assert not dialog._save.isEnabled()
    assert "Export Copy" in dialog._save.accessibleDescription()
    assert dialog._export.isEnabled()

    copy = tmp_path / "recovered.md"
    monkeypatch.setattr(
        "webjam_qt.widgets.notes_recovery_dialog.QFileDialog.getSaveFileName",
        lambda *args: (str(copy), ""),
    )
    dialog._export.click()
    assert copy.read_text() == "Keep this new idea"
    assert original.read_bytes() == b"\xfforiginal notes"
    assert owner.notes_save_state == "exported"
    assert owner._save_notes_only()
    assert original.read_bytes() == b"\xfforiginal notes"
    assert dialog.result() == dialog.DialogCode.Accepted


def test_workspace_selection_preserves_each_recovery_reason(notes, tmp_path, monkeypatch):
    canvas, owner = notes
    (tmp_path / ".webjam_notes.md").write_bytes(b"\xfforiginal notes")
    owner._load_notes_only()
    canvas.edit_notes("Local music idea")
    owner.switch_profile_key("art")
    monkeypatch.setattr(persistence_module, "_MAX_NOTES_FILE_BYTES", 32)
    canvas.edit_notes("Long art idea " * 5)
    assert not owner._save_notes_only()
    dialog = NotesRecoveryDialog(owner, canvas)
    assert dialog._profile.currentData() == "music"
    assert not dialog._save.isEnabled()

    dialog._profile.setCurrentIndex(dialog._profile.findData("art"))
    assert "too long" in dialog._message.text()
    assert dialog._save.isEnabled()
    dialog._editor.setPlainText("Short art idea")
    dialog._save.click()

    assert owner.profile_key == "art"
    assert (tmp_path / ".webjam_notes.art.md").read_text() == "Short art idea"
    assert owner.unsaved_notes == (("music", "Local music idea"),)
    assert dialog._profile.currentData() == "music"
    assert "will not be overwritten" in dialog._message.text()
    assert not dialog._save.isEnabled()
    assert dialog._export.isEnabled()


def test_replaced_notes_destination_keeps_original_specific_recovery(notes, tmp_path):
    canvas, owner = notes
    owner._load_notes_only()
    original = tmp_path / "keep.md"
    original.write_text("Keep this original")
    (tmp_path / ".webjam_notes.md").symlink_to(original)
    canvas.edit_notes("New local draft")
    assert not owner._save_notes_only()
    assert owner.notes_save_state == "protected_original"
    dialog = NotesRecoveryDialog(owner, canvas)
    assert not dialog._save.isEnabled()
    assert dialog._export.isEnabled()
    assert original.read_text() == "Keep this original"


def test_unconfirmed_save_guidance_does_not_claim_old_file_unchanged(notes, tmp_path, monkeypatch):
    from core import file_io

    canvas, owner = notes
    canvas.edit_notes("First idea")
    assert owner._save_notes_only()
    canvas.edit_notes("Revised idea")

    def fail_directory_sync(_path):
        raise OSError("unconfirmed directory write")

    monkeypatch.setattr(file_io, "_fsync_parent_directory", fail_directory_sync)
    assert not owner._save_notes_only()
    assert (tmp_path / ".webjam_notes.md").read_text() == "Revised idea"
    dialog = NotesRecoveryDialog(owner, canvas)
    assert "could not be confirmed saved" in dialog._message.text()
    dialog._save.click()
    assert "could not be confirmed saved" in dialog._message.text()
    assert "unchanged" not in dialog._message.text()
    assert "shorten" not in dialog._message.text().lower()
    assert dialog._save.isEnabled()
    assert dialog._export.isEnabled()
    assert owner.has_unsaved_notes


def test_failed_export_updates_accessible_recovery_without_acknowledging_draft(notes, tmp_path, monkeypatch):
    canvas, owner = notes
    (tmp_path / ".webjam_notes.md").write_bytes(b"\xfforiginal notes")
    owner._load_notes_only()
    canvas.edit_notes("Keep this idea")
    assert not owner._save_notes_only()
    dialog = NotesRecoveryDialog(owner, canvas)
    monkeypatch.setattr(
        "webjam_qt.widgets.notes_recovery_dialog.QFileDialog.getSaveFileName",
        lambda *args: (str(tmp_path / ".webjam_notes.md"), ""),
    )
    dialog._export.click()
    assert "Choose another file" in dialog._message.text()
    assert dialog._message.accessibleDescription() == dialog._message.text()
    assert owner.unsaved_notes == (("music", "Keep this idea"),)
    assert (tmp_path / ".webjam_notes.md").read_bytes() == b"\xfforiginal notes"
