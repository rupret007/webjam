"""Ordinary exports preserve recovery ownership and exact-draft guidance."""
from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.notes_recovery import (
    NotesRecoveryDraft, notes_fingerprint, read_notes_recovery, write_notes_recovery,
)
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.widgets.notes_recovery_dialog import NotesRecoveryDialog
from webjam_qt.widgets.session_canvas import SessionCanvas


JOURNAL = ".webjam_notes.recovery.json"
MUSIC = ".webjam_notes.md"
ART = ".webjam_notes.art.md"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def notes(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    (tmp_path / MUSIC).write_text("Saved music original")
    (tmp_path / ART).write_text("Saved art original")
    write_notes_recovery(
        tmp_path / JOURNAL,
        {"art": NotesRecoveryDraft("Only retained art draft", notes_fingerprint("Saved art original"))},
        expected={},
    )
    canvas = SessionCanvas()
    owner = SessionPersistence(SimpleNamespace(), canvas)
    owner._load_notes_only()
    canvas.notes_changed.connect(owner.notes_changed)
    canvas.edit_notes("Current unsaved music draft")
    yield canvas, owner
    for dialog in canvas.findChildren(NotesRecoveryDialog):
        dialog.deleteLater()
    canvas.close()
    canvas.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("export_method", ["export_notes", "export_brief"])
@pytest.mark.parametrize("reserved_name", [JOURNAL, MUSIC, ART])
def test_ordinary_exports_cannot_replace_recovery_or_workspace_originals(
    notes, qapp, tmp_path, monkeypatch, export_method, reserved_name,
):
    canvas, owner = notes
    canvas.show()
    qapp.processEvents()
    assert canvas._export_button.isVisible()
    assert canvas._export_button.isEnabled()
    before = {name: (tmp_path / name).read_bytes() for name in (JOURNAL, MUSIC, ART)}
    pending = owner.unsaved_notes
    warnings = []
    monkeypatch.setattr(
        "webjam_qt.widgets.session_canvas.QFileDialog.getSaveFileName",
        lambda *args: (str(tmp_path / reserved_name), ""),
    )
    monkeypatch.setattr(
        "webjam_qt.widgets.session_canvas.QMessageBox.warning",
        lambda *args: warnings.append(args[2]),
    )

    getattr(canvas, export_method)()

    assert {name: (tmp_path / name).read_bytes() for name in before} == before
    assert read_notes_recovery(tmp_path / JOURNAL)["art"].text == "Only retained art draft"
    assert owner.unsaved_notes == pending
    assert owner.notes_recovery_requires_review("art")
    assert warnings
    assert "choose" in warnings[0].lower()


@pytest.mark.parametrize("export_method", ["export_notes", "export_brief"])
def test_ordinary_export_to_chosen_copy_does_not_acknowledge_unsaved_draft(
    notes, tmp_path, monkeypatch, export_method,
):
    canvas, owner = notes
    copy = tmp_path / "chosen-export.md"
    pending = owner.unsaved_notes
    journal = (tmp_path / JOURNAL).read_bytes()
    monkeypatch.setattr(
        "webjam_qt.widgets.session_canvas.QFileDialog.getSaveFileName",
        lambda *args: (str(copy), ""),
    )

    getattr(canvas, export_method)()

    assert copy.read_text() == "Current unsaved music draft"
    assert owner.unsaved_notes == pending
    assert owner.has_unsaved_notes
    assert (tmp_path / JOURNAL).read_bytes() == journal
    assert (tmp_path / MUSIC).read_text() == "Saved music original"
    assert (tmp_path / ART).read_text() == "Saved art original"


def test_editing_recovered_dialog_draft_invalidates_saved_copy_claim_without_losing_focus(
    notes, qapp, tmp_path,
):
    canvas, owner = notes
    dialog = NotesRecoveryDialog(owner, canvas)
    dialog._profile.setCurrentIndex(dialog._profile.findData("art"))
    dialog.show()
    dialog.activateWindow()
    dialog._editor.setFocus()
    qapp.processEvents()
    confirmed_guidance = dialog._message.text()
    assert "A recovery copy of this draft is saved" in confirmed_guidance
    assert dialog._editor.hasFocus()

    QTest.keyClicks(dialog._editor, "New changes: ")
    qapp.processEvents()

    assert dialog._editor.hasFocus()
    assert dialog._message.text() != confirmed_guidance
    assert "A recovery copy of this draft is saved" not in dialog._message.text()
    assert dialog._message.accessibleDescription() == dialog._message.text()
    assert read_notes_recovery(tmp_path / JOURNAL)["art"].text == "Only retained art draft"
    assert dict(owner.unsaved_notes)["art"] == "Only retained art draft"
