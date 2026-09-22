"""Unavailable old recovery data is visible without inventing a dead action."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.widgets.session_canvas import SessionCanvas


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("journal_kind", ["invalid", "unsupported", "symlink"])
def test_unavailable_checkpoint_is_announced_without_blocking_normal_notes(
    qapp, tmp_path, monkeypatch, journal_kind,
):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    primary = tmp_path / ".webjam_notes.md"
    primary.write_text("Saved work")
    journal = tmp_path / ".webjam_notes.recovery.json"
    if journal_kind == "symlink":
        protected = tmp_path / "kept-recovery-data"
        protected.write_text("PRIVATE_RECOVERY_DATA")
        journal.symlink_to(protected)
    else:
        journal.write_text(
            "PRIVATE_RECOVERY_DATA" if journal_kind == "invalid"
            else '{"version":999,"profiles":{}}'
        )
    retained = journal.read_bytes()
    canvas = SessionCanvas()
    owner = SessionPersistence(SimpleNamespace(), canvas)
    canvas.notes_changed.connect(owner.notes_changed)
    try:
        owner._load_notes_only()
        assert canvas.current_notes() == "Saved work"
        assert not canvas._notes_save_status.isHidden()
        assert "recovery copy" in canvas._notes_save_status.text().lower()
        assert "could not be used safely" in canvas._notes_save_status.text()
        assert canvas._notes_save_status.accessibleDescription() == canvas._notes_save_status.text()
        assert "PRIVATE_" not in canvas._notes_save_status.text()
        assert canvas._save_notes_button.isHidden()
        assert not owner.has_unsaved_notes

        canvas.edit_notes("New work")
        assert owner._save_notes_only()
        assert primary.read_text() == "New work"
        assert journal.read_bytes() == retained
        assert (not journal.is_symlink()) == (journal_kind != "symlink")
        assert not canvas._notes_save_status.isHidden()
        assert canvas._save_notes_button.isHidden()
        assert not owner.has_unsaved_notes
    finally:
        canvas.close()
        canvas.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
