"""Retry and copy destinations must preserve every retained Notes version."""

import errno
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import file_io
from tests.test_notes_export_recovery_boundary import (
    notes as _notes_fixture,
    qapp as _qapp_fixture,
)
from webjam_qt.widgets.notes_recovery_dialog import NotesRecoveryDialog
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.widgets.session_canvas import SessionCanvas

notes = _notes_fixture
qapp = _qapp_fixture


@pytest.mark.parametrize("export_method", ["export_notes", "export_brief"])
@pytest.mark.parametrize(
    "reserved_name",
    [".webjam_notes.md", ".webjam_notes.art.md", ".webjam_notes.recovery.json"],
)
def test_case_alias_export_preserves_originals(
    notes, tmp_path, monkeypatch, export_method, reserved_name
):
    canvas, owner = notes
    reserved = tmp_path / reserved_name
    alias = tmp_path / reserved_name.upper()
    before = reserved.read_bytes()
    retained = owner.unsaved_notes
    warnings = []
    monkeypatch.setattr(
        "webjam_qt.widgets.session_canvas.QFileDialog.getSaveFileName",
        lambda *args: (str(alias), ""),
    )
    monkeypatch.setattr(
        "webjam_qt.widgets.session_canvas.QMessageBox.warning",
        lambda *args: warnings.append(args[2]),
    )
    getattr(canvas, export_method)()
    assert reserved.read_bytes() == before
    assert owner.unsaved_notes == retained
    assert warnings
    assert "choose" in warnings[0].lower()


def test_failed_draft_retry_preserves_externally_changed_primary(
    notes, tmp_path, monkeypatch
):
    canvas, owner = notes
    draft = canvas.current_notes()
    primary = tmp_path / ".webjam_notes.md"
    write = file_io._atomic_write

    def unavailable(path, data, *, mode):
        if Path(path) == primary:
            raise OSError(errno.EACCES, "Controlled file access failure")
        return write(path, data, mode=mode)

    with monkeypatch.context() as patch:
        patch.setattr(file_io, "_atomic_write", unavailable)
        assert not owner._save_notes_only()
    assert owner.notes_recovery_state("music") == "permission_denied"
    primary.write_text("A separately edited version made while fixing file access")
    dialog = NotesRecoveryDialog(owner, canvas)
    dialog._profile.setCurrentIndex(dialog._profile.findData("music"))
    dialog._save.click()
    assert (
        primary.read_text()
        == "A separately edited version made while fixing file access"
    )
    assert dict(owner.unsaved_notes)["music"] == draft
    assert owner.notes_recovery_state("music") == "recovery_conflict"


@pytest.mark.parametrize("external_edit", [False, True])
def test_recovered_retry_recognizes_own_unconfirmed_write_but_not_an_external_edit(
    notes,
    tmp_path,
    monkeypatch,
    external_edit,
):
    canvas, owner = notes
    primary = tmp_path / ".webjam_notes.art.md"
    first = dict(owner.unsaved_notes)["art"]
    write = file_io._atomic_write

    def publish_then_fail(path, data, *, mode):
        write(path, data, mode=mode)
        if Path(path) == primary:
            raise OSError(errno.EIO, "Controlled failure after publication")

    with monkeypatch.context() as patch:
        patch.setattr(file_io, "_atomic_write", publish_then_fail)
        assert not owner.save_recovered_notes("art", first)
    assert primary.read_text() == first
    assert owner.revise_pending_notes("art", first, "A revised recovered draft")
    if external_edit:
        primary.write_text("An external revision")
    saved = owner.save_recovered_notes("art", "A revised recovered draft")
    assert saved is (not external_edit)
    assert primary.read_text() == (
        "An external revision" if external_edit else "A revised recovered draft"
    )
    if external_edit:
        assert owner.notes_recovery_state("art") == "recovery_conflict"


def test_conflict_undo_to_old_baseline_keeps_exact_draft(notes, tmp_path):
    canvas, owner = notes
    primary = tmp_path / ".webjam_notes.md"
    original = primary.read_text()
    primary.write_text("External version")
    assert not owner._save_notes_only()
    assert owner.notes_recovery_state("music") == "recovery_conflict"
    canvas.edit_notes(original)
    assert dict(owner.unsaved_notes)["music"] == original
    assert not owner._save_notes_only()
    assert primary.read_text() == "External version"


@pytest.mark.parametrize("external_edit", [False, True])
def test_edit_after_copy_export_recognizes_own_unconfirmed_primary(
    notes,
    tmp_path,
    monkeypatch,
    external_edit,
):
    canvas, owner = notes
    primary = tmp_path / ".webjam_notes.md"
    draft = canvas.current_notes()
    write = file_io._atomic_write

    def publish_then_fail(path, data, *, mode):
        write(path, data, mode=mode)
        if Path(path) == primary:
            raise OSError(errno.EIO, "Controlled failure after publication")

    with monkeypatch.context() as patch:
        patch.setattr(file_io, "_atomic_write", publish_then_fail)
        assert not owner._save_notes_only()
    copy = tmp_path / "retained-copy.md"
    assert owner.export_pending_notes("music", draft, str(copy))
    canvas.edit_notes("The next idea after export")
    if external_edit:
        primary.write_text("An external revision")
    owner._save_notes_only()
    assert primary.read_text() == (
        "An external revision" if external_edit else "The next idea after export"
    )
    assert ("music" in dict(owner.unsaved_notes)) is external_edit
    assert copy.read_text() == draft


@pytest.mark.parametrize("original", [None, "", "Existing original"])
@pytest.mark.parametrize("blocked_journal", [False, True])
def test_save_without_startup_load_captures_original_even_with_blocked_journal(
    qapp,
    tmp_path,
    monkeypatch,
    original,
    blocked_journal,
):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    primary = tmp_path / ".webjam_notes.md"
    journal = tmp_path / ".webjam_notes.recovery.json"
    if original is not None:
        primary.write_text(original)
    if blocked_journal:
        journal.write_bytes(b"Preserve unknown recovery format")
    canvas = SessionCanvas()
    owner = SessionPersistence(SimpleNamespace(), canvas)
    canvas.notes_changed.connect(owner.notes_changed)
    try:
        canvas.edit_notes("Draft supplied before load")
        assert owner._save_notes_only()
        assert primary.read_text() == "Draft supplied before load"
        if blocked_journal:
            assert journal.read_bytes() == b"Preserve unknown recovery format"
    finally:
        canvas.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize(
    "name", [".WEBJAM_NOTES.REVIEW_REHEARSAL.MD", ".webjam_notes.podcast_voice.md."]
)
def test_export_reserves_absent_notes_names_only_inside_notes_home(
    notes, tmp_path, name
):
    _, owner = notes
    with pytest.raises(ValueError, match="separate"):
        owner.export_notes_copy("copy", str(tmp_path / name))
    elsewhere = tmp_path / "copies"
    elsewhere.mkdir()
    owner.export_notes_copy("copy", str(elsewhere / name))
    assert (elsewhere / name).read_text() == "copy"


def test_export_rejects_existing_file_identity_alias(notes, tmp_path):
    _, owner = notes
    primary = tmp_path / ".webjam_notes.art.md"
    alias = tmp_path / "different-name.md"
    alias.hardlink_to(primary)
    original = primary.read_bytes()
    with pytest.raises(ValueError, match="separate"):
        owner.export_notes_copy("copy", str(alias))
    assert primary.read_bytes() == alias.read_bytes() == original
