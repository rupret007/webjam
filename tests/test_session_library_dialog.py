"""Library workflows retain drafts and require explicit take/open actions."""
from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QPushButton, QScrollArea

from core.art_workspace import make_reference, normalize_art_workspace
from core.session_library import SessionLibrary
from webjam_qt.windows.session_library import SessionLibraryDialog


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


def _selected_id(dialog):
    item = dialog.history.currentItem()
    return item.data(Qt.ItemDataRole.UserRole) if item else None


def test_switching_dirty_workspaces_saves_old_draft_and_opens_requested_record(tmp_path, make_dialog):
    library = SessionLibrary(tmp_path)
    old = library.create("music", "First rehearsal", notes="Before")
    requested = library.create("art", "Second project", notes="Art notes")
    dialog = make_dialog(library, current_id=old.id)
    dialog.notes.setPlainText("Decision: Keep the ending\nA complete draft")
    assert dialog._dirty
    # The save rebuilds history, deleting its original QListWidgetItems.
    dialog.select_id(requested.id)
    assert dialog.record.id == requested.id
    assert _selected_id(dialog) == requested.id
    assert dialog.notes.toPlainText() == "Art notes"
    assert library.load(old.id).notes.endswith("A complete draft")
    assert library.load(old.id).decisions == ("Keep the ending",)
    assert not dialog._dirty


def test_profile_tabs_history_search_and_restart_preserve_independent_work(tmp_path, make_dialog):
    library = SessionLibrary(tmp_path)
    music = library.create("music", "Tuesday songs", notes="Unique bridge lyric")
    art = library.create("art", "Portrait", notes="Ultramarine shadows", art={
        "version": 1, "brief": "Paint reflected light", "progress": "First layer",
    })
    dialog = make_dialog(library, current_id=music.id)
    assert dialog.tabs.isTabVisible(1)
    assert not dialog.tabs.isTabVisible(2)
    assert dialog.tabs.isTabVisible(4)
    dialog.select_id(art.id)
    assert not dialog.tabs.isTabVisible(1)
    assert dialog.tabs.isTabVisible(2)
    assert not dialog.tabs.isTabVisible(4)
    dialog.art.next_steps.setPlainText("Glaze the reflected edge")
    assert dialog.save_current()
    dialog.search.setText("BRIDGE")
    assert dialog.history.count() == 1
    assert dialog.history.item(0).data(Qt.ItemDataRole.UserRole) == music.id
    assert dialog.record.id == art.id  # Search does not replace the editor.
    dialog.search.clear()
    fresh = make_dialog(SessionLibrary(tmp_path), current_id=art.id)
    assert fresh.art.next_steps.toPlainText() == "Glaze the reflected edge"
    assert fresh.notes.toPlainText() == "Ultramarine shadows"
    assert SessionLibrary(tmp_path).load(music.id).notes == "Unique bridge lyric"


def test_conflict_keeps_draft_blocks_switch_and_can_save_separate_copy(tmp_path, make_dialog, monkeypatch):
    library = SessionLibrary(tmp_path)
    first = library.create("music", "Shared rehearsal", notes="Original")
    second = library.create("art", "Another project")
    dialog = make_dialog(library, current_id=first.id)
    dialog.notes.setPlainText("My unsaved complete draft\nAction: Send charts")
    library.save(replace(first, notes="Saved in another window"))
    assert not dialog.save_current()
    assert dialog._dirty
    assert "not saved" in dialog.status.text()
    assert dialog.notes.toPlainText().startswith("My unsaved complete draft")
    dialog.select_id(second.id)
    assert dialog.record.id == first.id
    assert _selected_id(dialog) == first.id
    assert dialog.notes.toPlainText().startswith("My unsaved complete draft")
    copies = []
    dialog.copy_saved.connect(lambda source, saved: copies.append((source, saved)))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("My retained copy", True))
    _click(dialog, "Save as copy…")
    copy = dialog.record
    assert copy.id not in {first.id, second.id}
    assert copy.title == "My retained copy"
    assert copy.notes == "My unsaved complete draft\nAction: Send charts"
    assert library.load(first.id).notes == "Saved in another window"
    assert library.load(copy.id).notes == copy.notes
    assert not dialog._dirty
    assert len(copies) == 1
    source, saved = copies[0]
    assert source.id == first.id
    assert source.revision == first.revision
    assert source._store_token == first._store_token
    assert source.notes == copy.notes
    assert saved == library.load(copy.id)


def test_failed_copy_keeps_draft_and_does_not_acknowledge_recovery(tmp_path, make_dialog, monkeypatch):
    library = SessionLibrary(tmp_path)
    record = library.create("art", "My work")
    dialog = make_dialog(library, current_id=record.id)
    dialog.notes.setPlainText("Draft still needs a home")
    copies = []
    dialog.copy_saved.connect(lambda source, saved: copies.append((source, saved)))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Copy", True))

    def no_space(*_args, **_kwargs):
        raise OSError("No space available")

    monkeypatch.setattr(library, "create", no_space)
    _click(dialog, "Save as copy…")
    assert copies == []
    assert dialog._dirty
    assert dialog.record.id == record.id
    assert dialog.notes.toPlainText() == "Draft still needs a home"


def test_another_profiles_pending_draft_can_be_found_and_preserved_as_copy(tmp_path, make_dialog, monkeypatch):
    library = SessionLibrary(tmp_path)
    current = library.create("music", "Current rehearsal")
    original = library.create("art", "Art original", notes="Old disk notes", art={"version": 1, "brief": "Old disk brief"})
    pending = replace(original, notes="Unsaved violet shadows", art={
        "version": 1, "brief": "Retained project brief", "progress": "New brushwork",
        "next_steps": "Keep this exact Art draft",
    })
    library.save(replace(original, notes="External notes on disk"))
    pending_map = {pending.id: pending}
    dialog = make_dialog(library, current_id=current.id, pending_records=pending_map)
    assert dialog.record.id == current.id  # A newer pending row must not steal initial selection.
    dialog.search.setText("VIOLET")
    assert dialog.history.count() == 1
    assert "unsaved" in dialog.history.item(0).text()
    dialog.select_id(pending.id)
    assert dialog.record.id == pending.id
    assert dialog._dirty
    assert "not saved" in dialog.status.text()
    assert dialog.notes.toPlainText() == "Unsaved violet shadows"
    assert dialog.art.brief.toPlainText() == "Retained project brief"
    assert dialog.art.progress.toPlainText() == "New brushwork"
    assert not dialog.save_current()
    copies = []
    dialog.copy_saved.connect(lambda source, saved: copies.append((source, saved)))
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Retained Art copy", True))
    _click(dialog, "Save as copy…")
    copied = library.load(dialog.record.id)
    assert copied.id != original.id
    assert copied.profile == "art"
    assert copied.notes == pending.notes
    assert copied.art["next_steps"] == "Keep this exact Art draft"
    assert library.load(original.id).notes == "External notes on disk"
    assert copies[0][0].id == original.id
    assert copies[0][1] == copied
    assert pending_map == {pending.id: pending}  # Only the coordinator settles its recovery map.


def test_pending_workspace_remains_recoverable_when_original_was_removed(tmp_path, make_dialog, monkeypatch):
    library = SessionLibrary(tmp_path)
    original = library.create("music", "Unavailable original", notes="Old")
    pending = replace(original, notes="Retain even after original removal")
    (tmp_path / f"{original.id}.json").unlink()
    dialog = make_dialog(library, current_id=original.id, pending_records={original.id: pending})
    assert dialog.history.count() == 1
    assert dialog._dirty
    assert dialog.notes.toPlainText() == pending.notes
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Recovered separately", True))
    _click(dialog, "Save as copy…")
    assert library.load(dialog.record.id).notes == pending.notes


def test_pending_record_retry_settles_only_dialog_snapshot(tmp_path, make_dialog):
    library = SessionLibrary(tmp_path)
    original = library.create("music", "Retry", notes="Original")
    pending = replace(original, notes="Retained retry")
    external_pending = {pending.id: pending}
    dialog = make_dialog(library, current_id=pending.id, pending_records=external_pending)
    saved = []
    dialog.record_saved.connect(saved.append)
    assert dialog.save_current()
    assert len(saved) == 1
    assert library.load(original.id).notes == "Retained retry"
    assert original.id not in dialog.pending_records
    assert external_pending == {pending.id: pending}


def test_failed_save_does_not_close_or_continue_with_unsaved_changes(tmp_path, make_dialog):
    library = SessionLibrary(tmp_path)
    original = library.create("art", "Study")
    dialog = make_dialog(library, current_id=original.id)
    dialog.notes.setPlainText("My current draft")
    library.save(replace(original, notes="Other writer"))
    requested = []
    dialog.continue_requested.connect(requested.append)
    _click(dialog, "Continue this work")
    assert requested == []
    assert dialog.selected_record is None
    assert dialog._dirty
    assert dialog.notes.toPlainText() == "My current draft"


def test_selecting_history_or_takes_never_opens_an_accidental_take(tmp_path, make_dialog, app):
    library = SessionLibrary(tmp_path / "library")
    one = tmp_path / "first-take"
    two = tmp_path / "second-take"
    one.mkdir()
    two.mkdir()
    record = library.create("music", "Two takes", take_links=(
        {"take_id": "first", "title": "First", "take_path": str(one)},
        {"take_id": "second", "title": "Second", "take_path": str(two)},
    ))
    art = library.create("art", "Other work")
    dialog = make_dialog(library, current_id=art.id)
    opened = []
    dialog.take_open_requested.connect(opened.append)
    dialog.select_id(record.id)
    assert dialog.takes.currentRow() == -1
    _click(dialog, "Open selected take in Studio")
    assert opened == []
    dialog.tabs.setCurrentIndex(4)
    dialog.takes.setCurrentRow(1)
    app.processEvents()
    assert opened == []
    _click(dialog, "Open selected take in Studio")
    assert opened == [dict(record.take_links[1])]
    dialog.select_id(art.id)
    _click(dialog, "Open selected take in Studio")
    assert len(opened) == 1


def test_summary_export_keeps_local_locators_private_and_never_overwrites_original(tmp_path, make_dialog, monkeypatch):
    reference_path = tmp_path / "private.kra"
    reference_path.write_bytes(b"original art project")
    reference = make_reference(str(reference_path), kind="file", title="Painting project")
    art = normalize_art_workspace({"version": 1, "brief": "Light study", "references": [reference],
        "bookmarks": [{"id": "mark-1", "reference_id": reference["id"], "seconds": 92, "note": "Soft edges"}]})
    library = SessionLibrary(tmp_path / "library")
    record = library.create("art", "My painting", art=art, notes="Keep the reflections", recaps=(
        {"ended_at": "2026-09-30", "summary": "Finished the first pass"},
    ))
    dialog = make_dialog(library, current_id=record.id)
    destination = tmp_path / "summary.md"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(destination), ""))
    _click(dialog, "Export summary…")
    summary = destination.read_text()
    assert "Light study" in summary
    assert "1:32: Soft edges" in summary
    assert "Keep the reflections" in summary
    assert "Finished the first pass" in summary
    assert str(tmp_path) not in summary
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args, **_kwargs: (str(reference_path), ""))
    _click(dialog, "Export summary…")
    assert reference_path.read_bytes() == b"original art project"
    assert "not exported" in dialog.status.text()


def test_unreadable_art_record_does_not_partially_replace_current_editor(tmp_path, make_dialog):
    library = SessionLibrary(tmp_path)
    current = library.create("art", "Good workspace", notes="Current notes", art={"version": 1, "brief": "Current brief"})
    invalid = library.create("art", "Unsupported Art", art={"version": 99})
    dialog = make_dialog(library, current_id=current.id)
    dialog.select_id(invalid.id)
    assert dialog.record.id == current.id
    assert dialog.title.text() == "Good workspace"
    assert dialog.notes.toPlainText() == "Current notes"
    assert dialog.art.brief.toPlainText() == "Current brief"
    assert "could not be opened" in dialog.status.text()
    assert _selected_id(dialog) == current.id


def test_compact_library_keeps_actions_visible_and_art_controls_scrollable(tmp_path, make_dialog, app):
    library = SessionLibrary(tmp_path)
    art = library.create("art", "Compact workspace")
    dialog = make_dialog(library, current_id=art.id)
    dialog.resize(480, 460)
    dialog.tabs.setCurrentIndex(2)
    dialog.show()
    app.processEvents()
    assert dialog.width() <= 480
    for button in (dialog.save_button, dialog.export_button, dialog.continue_button, dialog.copy_button):
        assert button.isVisible()
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    scroll = dialog.tabs.widget(2)
    assert isinstance(scroll, QScrollArea)
    assert scroll.verticalScrollBar().maximum() > 0
    assert scroll.horizontalScrollBar().maximum() == 0
    scroll.ensureWidgetVisible(dialog.art.bookmark_note)
    app.processEvents()
    visible_point = dialog.art.bookmark_note.mapTo(scroll.viewport(), dialog.art.bookmark_note.rect().center())
    assert scroll.viewport().rect().contains(visible_point)
