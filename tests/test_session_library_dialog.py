"""Library workflows retain drafts and require explicit take/open actions."""
from dataclasses import replace
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QPushButton, QScrollArea

from core.art_workspace import make_reference, normalize_art_workspace
from core.session_library import SessionLibrary
from webjam_qt.windows.session_library import SessionLibraryDialog
from webjam_qt.theme import load_stylesheet


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
    assert source == first  # The recovery acknowledgement binds the loaded snapshot, not edited text.
    assert source.notes == "Original"
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
    _click(dialog, "Open in Studio")
    assert opened == []
    dialog.tabs.setCurrentIndex(4)
    dialog.takes.setCurrentRow(1)
    app.processEvents()
    assert opened == []
    _click(dialog, "Open in Studio")
    assert opened == [dict(record.take_links[1])]
    dialog.select_id(art.id)
    _click(dialog, "Open in Studio")
    assert len(opened) == 1


def test_pending_take_without_path_cannot_open_or_offer_relink(tmp_path, make_dialog, monkeypatch):
    library = SessionLibrary(tmp_path)
    record = library.create("music", "Still recording", take_links=(
        {"take_id": "pending", "take_path": "", "status": "pending", "title": "Recording requested"},
    ))
    dialog = make_dialog(library, current_id=record.id)
    dialog.takes.setCurrentRow(0)
    assert "no completed take yet" in dialog.takes.item(0).text()
    assert "missing" not in dialog.takes.item(0).text()
    assert not dialog.open_take_button.isEnabled()
    assert not dialog.relink_take_button.isEnabled()
    opened = []
    dialog.take_open_requested.connect(opened.append)
    asked = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: asked.append(True) or "")
    dialog._open_take()
    dialog._relink_take()
    assert opened == []
    assert asked == []


def test_save_reconciliation_cannot_change_which_take_the_user_selected(tmp_path, make_dialog):
    library = SessionLibrary(tmp_path)
    first = {"take_id": "one", "take_path": str(tmp_path / "one"), "title": "One"}
    second = {"take_id": "two", "take_path": str(tmp_path / "two"), "title": "Two"}
    record = library.create("music", "Reordered takes", take_links=(first, second))

    def reconcile(_base, edited):
        return library.save(replace(edited, take_links=(second, first)))

    dialog = make_dialog(library, current_id=record.id, save_record=reconcile)
    dialog.takes.setCurrentRow(0)
    dialog.notes.setPlainText("Unsaved editor change")
    opened = []
    dialog.take_open_requested.connect(opened.append)
    dialog._open_take()
    assert dialog.record.take_links[0] == second
    assert dialog.takes.item(0).text().startswith("Two")
    assert dialog.takes.currentRow() == 1
    assert opened == [first]


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


@pytest.mark.parametrize("source", ["disk", "pending"])
@pytest.mark.parametrize(("field", "entry"), [
    ("take_links", {"title": 7, "take_path": None}),
    ("recaps", {"take_ids": [{}]}),
])
def test_malformed_nested_reference_cannot_partially_replace_current_editor(
    tmp_path, make_dialog, source, field, entry,
):
    library = SessionLibrary(tmp_path)
    current = library.create("music", "Current rehearsal", notes="Keep these current notes")
    other = library.create("music", "Malformed workspace", notes="Never partially load me")
    pending = {other.id: replace(other, **{field: (entry,)})} if source == "pending" else {}
    dialog = make_dialog(library, current_id=current.id, pending_records=pending)
    path = tmp_path / f"{other.id}.json"
    if source == "disk":
        # The history row was valid when listed; an external edit can corrupt
        # its nested payload before the user actually selects it.
        raw = json.loads(path.read_bytes())
        raw[field] = [entry]
        path.write_text(json.dumps(raw))
    unchanged = path.read_bytes()
    dialog.select_id(other.id)
    assert dialog.record.id == current.id
    assert dialog.title.text() == current.title
    assert dialog.notes.toPlainText() == current.notes
    assert dialog.takes.count() == 0
    assert "could not be opened" in dialog.status.text()
    assert f"{field}[0]" in dialog.status.text()
    assert _selected_id(dialog) == current.id
    assert path.read_bytes() == unchanged
    if source == "pending":
        assert dialog.pending_records[other.id] == pending[other.id]


def test_compact_library_keeps_actions_visible_and_art_controls_scrollable(tmp_path, make_dialog, app):
    library = SessionLibrary(tmp_path)
    art = library.create("art", "Compact workspace")
    dialog = make_dialog(library, current_id=art.id)
    dialog.resize(480, 460)
    dialog.tabs.setCurrentIndex(2)
    dialog.show()
    app.processEvents()
    assert dialog.width() <= 480
    for button in (dialog.save_button, dialog.export_button, dialog.continue_button, dialog.copy_button,
                   dialog.backup_button, dialog.import_backup_button):
        dialog.content_scroll.ensureWidgetVisible(button, 0, 0)
        app.processEvents()
        assert button.isVisible()
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().topLeft()))
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    scroll = dialog.tabs.widget(2)
    assert isinstance(scroll, QScrollArea)
    assert scroll.verticalScrollBar().maximum() > 0
    assert scroll.horizontalScrollBar().maximum() == 0
    scroll.ensureWidgetVisible(dialog.art.bookmark_note)
    dialog.content_scroll.ensureWidgetVisible(dialog.art.bookmark_note, 0, 0)
    app.processEvents()
    visible_point = dialog.art.bookmark_note.mapTo(scroll.viewport(), dialog.art.bookmark_note.rect().center())
    assert scroll.viewport().rect().contains(visible_point)
    assert dialog.tabs.widget(1) is dialog.rehearsal  # The plan already owns its own scroll area.


@pytest.mark.parametrize("font_size", [13, 22])
def test_library_actions_and_art_buttons_fit_after_resizing_and_hidden_tab_activation(
    tmp_path, make_dialog, app, font_size,
):
    library = SessionLibrary(tmp_path)
    record = library.create("art", "Layout study", notes="Keep the complete draft")
    dialog = make_dialog(library, current_id=record.id)
    dialog.setStyleSheet(load_stylesheet() + f"QWidget {{ font-size: {font_size}px; }}")
    dialog.show()
    actions = (dialog.new_button, dialog.copy_button, dialog.save_button,
               dialog.export_button, dialog.backup_button, dialog.import_backup_button,
               dialog.continue_button,
               next(button for button in dialog.findChildren(QPushButton) if button.text() == "Close"))
    for width, height in ((760, 680), (480, 500), (760, 680)):
        dialog.tabs.setCurrentIndex(0)
        dialog.resize(width, height)
        for _ in range(6):
            app.processEvents()
        assert (dialog.width(), dialog.height()) == (width, height)
        bounds = [QRect(button.mapTo(dialog._content, QPoint()), button.size()) for button in actions]
        assert all(not left.intersects(right) for index, left in enumerate(bounds) for right in bounds[index + 1:])
        for button in actions:
            dialog.content_scroll.ensureWidgetVisible(button, 0, 0)
            app.processEvents()
            assert button.width() >= button.minimumSizeHint().width(), button.text()
            viewport = dialog.content_scroll.viewport()
            bounds = QRect(button.mapTo(viewport, QPoint()), button.size())
            assert viewport.rect().contains(bounds), button.text()
            assert dialog.content_scroll.horizontalScrollBar().maximum() == 0
        dialog.tabs.setCurrentIndex(2)
        for _ in range(6):
            app.processEvents()
        scroll = dialog.tabs.widget(2)
        assert scroll.horizontalScrollBar().maximum() == 0
        assert dialog.art.width() <= scroll.viewport().width()
        for button in dialog.art.findChildren(QPushButton):
            scroll.ensureWidgetVisible(button, 0, 0)
            dialog.content_scroll.ensureWidgetVisible(button, 0, 0)
            app.processEvents()
            assert button.width() >= button.minimumSizeHint().width(), button.text()
            bounds = QRect(button.mapTo(scroll.viewport(), QPoint()), button.size())
            assert scroll.viewport().rect().contains(bounds), button.text()
            viewport = dialog.content_scroll.viewport()
            bounds = QRect(button.mapTo(viewport, QPoint()), button.size())
            assert viewport.rect().contains(bounds), button.text()
        assert dialog.notes.toPlainText() == record.notes
        assert not dialog._dirty
        assert not dialog.isModal()


def test_tab_leaves_workspace_notes_without_changing_the_draft(tmp_path, make_dialog, app):
    record = SessionLibrary(tmp_path).create("music", "Keyboard editing", notes="Keep every character")
    dialog = make_dialog(SessionLibrary(tmp_path), current_id=record.id)
    dialog.show()
    dialog.activateWindow()
    dialog.notes.setFocus()
    app.processEvents()
    assert dialog.notes.hasFocus()
    QTest.keyClick(dialog.notes, Qt.Key.Key_Tab)
    assert not dialog.notes.hasFocus()
    assert dialog.notes.toPlainText() == record.notes
    assert not dialog._dirty
