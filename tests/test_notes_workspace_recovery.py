"""Retained workspace drafts stay discoverable without changing the live room."""

from __future__ import annotations

import errno
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.widgets.notes_recovery_dialog import NotesRecoveryDialog
from webjam_qt.widgets.session_canvas import SessionCanvas


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def notes(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    canvas = SessionCanvas()
    owner = SessionPersistence(SimpleNamespace(), canvas)
    canvas.notes_changed.connect(owner.notes_changed)
    original_write = persistence_module.atomic_write_text
    blocked = {".webjam_notes.md", ".webjam_notes.art.md"}

    def write(path, text, **kwargs):
        if path.name in blocked:
            raise OSError(errno.EACCES, "PRIVATE_PATH PRIVATE_DRAFT")
        return original_write(path, text, **kwargs)

    monkeypatch.setattr(persistence_module, "atomic_write_text", write)
    owner._load_notes_only()
    canvas.edit_notes("Music idea")
    assert not owner._save_notes_only()
    owner.switch_profile_key("art")
    yield canvas, owner, blocked
    canvas.close()
    canvas.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_hidden_failed_draft_names_its_workspace_and_opens_existing_recovery(notes, qapp):
    canvas, owner, blocked = notes
    canvas.edit_notes("Art idea")
    blocked.remove(".webjam_notes.art.md")
    assert not owner._save_notes_only()
    assert owner.unsaved_notes == (("music", "Music idea"),)
    status = canvas._notes_save_status
    assert "Music" in status.text()
    assert "permission" in status.text().lower()
    assert "Art" not in status.text()
    assert "Music" in canvas._save_notes_button.accessibleDescription()

    dialogs = []

    def open_recovery():
        dialog = NotesRecoveryDialog(owner, canvas)
        dialogs.append(dialog)
        dialog.show()

    canvas.save_notes_requested.connect(open_recovery)
    canvas.show()
    qapp.processEvents()
    canvas._save_notes_button.click()
    assert dialogs[0]._profile.currentData() == "music"
    assert owner.profile_key == "art"
    assert canvas.current_notes() == "Art idea"
    assert "PRIVATE_" not in status.text() + status.accessibleDescription()


def test_export_updates_workspace_count_without_clearing_newer_other_draft(notes, tmp_path, monkeypatch):
    canvas, owner, _ = notes
    canvas.edit_notes("Art idea")
    assert not owner._save_notes_only()
    assert "2 workspaces" in canvas._notes_save_status.text()
    assert "Music" in canvas._notes_save_status.text()
    assert "Art" in canvas._notes_save_status.text()
    dialog = NotesRecoveryDialog(owner, canvas)
    assert dialog._profile.currentData() == "music"
    copy = tmp_path / "music-copy.md"

    def choose_copy(*args):
        canvas.edit_notes("Newer Art idea")
        return str(copy), ""

    monkeypatch.setattr(
        "webjam_qt.widgets.notes_recovery_dialog.QFileDialog.getSaveFileName", choose_copy,
    )
    dialog._export.click()
    assert copy.read_text() == "Music idea"
    assert owner.unsaved_notes == (("art", "Newer Art idea"),)
    assert owner.has_unsaved_notes
    assert owner.profile_key == "art"
    assert canvas.current_notes() == "Newer Art idea"
    assert "Art" in canvas._notes_save_status.text()
    assert "Music" not in canvas._notes_save_status.text()
    assert "2 workspaces" not in canvas._notes_save_status.text()
    assert not canvas._save_notes_button.isHidden()


def test_export_of_stale_selected_copy_keeps_newer_hidden_draft_discoverable(notes, tmp_path, monkeypatch):
    canvas, owner, _ = notes
    dialog = NotesRecoveryDialog(owner, canvas)
    copy = tmp_path / "older-music-copy.md"

    def choose_copy(*args):
        assert owner.revise_pending_notes("music", "Music idea", "Newer Music idea")
        return str(copy), ""

    monkeypatch.setattr(
        "webjam_qt.widgets.notes_recovery_dialog.QFileDialog.getSaveFileName", choose_copy,
    )
    dialog._export.click()
    assert copy.read_text() == "Music idea"
    assert owner.unsaved_notes == (("music", "Newer Music idea"),)
    assert owner.profile_key == "art"
    assert canvas.current_notes() == ""
    assert "Music" in canvas._notes_save_status.text()
    assert not canvas._save_notes_button.isHidden()


def test_editing_active_notes_does_not_hide_another_workspaces_failed_draft(notes, qapp):
    canvas, owner, _ = notes
    canvas.show()
    canvas.activateWindow()
    canvas._notes.setFocus()
    qapp.processEvents()
    canvas.edit_notes("Art work in progress")
    assert "Music" in canvas._notes_save_status.text()
    assert not canvas._save_notes_button.isHidden()
    assert canvas._notes.hasFocus()
    assert owner.profile_key == "art"


def test_context_renders_only_canonical_workspace_names_and_bounded_reasons(qapp):
    canvas = SessionCanvas()
    try:
        canvas.set_notes_recovery_context("art", (
            ("music", "disk_full"), ("PRIVATE_PATH", "PRIVATE_ERROR"),
        ))
        canvas.set_notes_save_state("failed")
        visible_and_spoken = (
            canvas._notes_save_status.text() + canvas._notes_save_status.accessibleDescription()
            + canvas._save_notes_button.accessibleDescription()
        )
        assert "Music" in visible_and_spoken
        assert "PRIVATE_" not in visible_and_spoken
        assert "full" in visible_and_spoken.lower()
    finally:
        canvas.close()
        canvas.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
