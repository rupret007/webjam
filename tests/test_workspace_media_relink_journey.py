"""Explicit restoration keeps editable work, identity and compact controls."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import shutil

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QPushButton, QScrollArea

from core.session_library import SessionLibrary
from core.workspace_backup import export_workspace_backup, import_workspace_backup, preview_workspace_backup
from core import workspace_media_backup as media
from tests.test_workspace_media_activation import portable_takes as portable_takes
from tests.test_workspace_media_backup import art as art
from tests.test_workspace_media_library_ui import app as app, box_font as box_font, make_dialog as make_dialog, _wait
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets import art_workspace as art_ui


@pytest.mark.parametrize("conflict", [False, True])
def test_take_relink_preserves_song_moment_drafts_and_exact_matching_bookmarks(
    app, portable_takes, make_dialog, tmp_path, monkeypatch, conflict,
):
    library, record, _originals, _roots, _projects = portable_takes
    reference = record.take_links[0]
    dialog = make_dialog(library, current_id=record.id)
    panel = dialog.rehearsal
    panel.add_song("Opening")
    panel.add_bookmark("Exact entrance", **reference, position_seconds=.04, timing_verified=True)
    panel.add_bookmark("Different content", **dict(reference, source_identity="f" * 64),
                       position_seconds=.08, timing_verified=True)
    panel.add_song("Ending")
    panel.add_bookmark("Same take later", **reference, position_seconds=.09, timing_verified=True)
    panel._song_choice.setCurrentIndex(0)
    panel._bookmarks.setCurrentRow(0)
    assert dialog.save_current()
    baseline = deepcopy(dialog.record)
    panel._notes.setPlainText("Keep edited song notes")
    panel._goals.setPlainText("Keep edited goal")
    panel._next_steps.setPlainText("Keep next steps")
    panel._moment_note.setText("Unsubmitted new moment")
    dialog.notes.setPlainText("Keep my workspace draft")
    before = dialog._edited_record()
    expected = deepcopy(before.rehearsal)
    old_path = Path(reference["take_path"])
    moved = tmp_path / "moved-completed-take"
    old_path.rename(moved)
    original_bytes = {p.relative_to(moved): p.read_bytes() for p in moved.rglob("*") if p.is_file()}
    for song in expected["songs"]:
        for mark in song["bookmarks"]:
            if mark.get("take_id") == reference["take_id"] and mark.get("source_identity") == reference["source_identity"]:
                mark["take_path"] = str(moved)
    disk = library.save(replace(baseline, notes="Other writer")) if conflict else baseline
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(moved))
    opened = []
    dialog.take_open_requested.connect(opened.append)
    dialog.takes.setCurrentRow(0)
    dialog.relink_take_button.click()
    _wait(app, lambda: not dialog.media_operation_pending)
    assert dialog.record.take_links[0]["take_path"] == str(moved)
    assert panel.payload() == expected
    assert panel._plan.index == 0 and panel._bookmarks.currentRow() == 0
    assert panel._moment_note.text() == "Unsubmitted new moment"
    assert dialog.notes.toPlainText() == "Keep my workspace draft"
    assert not opened
    assert {p.relative_to(moved): p.read_bytes() for p in moved.rglob("*") if p.is_file()} == original_bytes
    if conflict:
        assert dialog._dirty and "not saved" in dialog.status.text()
        assert library.load(record.id) == disk
        panel._open_moment.click()
        assert not opened and not dialog.media_operation_pending
    else:
        assert not dialog._dirty and library.load(record.id).rehearsal == expected
        panel._open_moment.click()
        _wait(app, lambda: not dialog.media_operation_pending)
        assert len(opened) == 1
        assert opened[0]["take_path"] == str(moved)
        assert opened[0]["take_id"] == reference["take_id"]
        assert opened[0]["position_seconds"] == .04


def test_metadata_only_art_checks_availability_but_refuses_unprovable_relink(
    app, art, tmp_path, make_dialog, monkeypatch,
):
    source, original = art
    backup = tmp_path / "metadata.json"
    export_workspace_backup(source, backup)
    library = SessionLibrary(tmp_path / "metadata-restored")
    record = import_workspace_backup(library, preview_workspace_backup(backup))
    assert record.import_provenance and not record.media_provenance
    dialog = make_dialog(library, current_id=record.id)
    panel = dialog.art
    panel.references.setCurrentRow(0)
    panel.brief.setPlainText("Unsaved imported brief")
    panel.bookmark_note.setText("Unsubmitted imported bookmark")
    before = dialog._edited_record()
    opened = []
    monkeypatch.setattr(art_ui.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    panel.verify_button.click()
    _wait(app, lambda: not dialog.media_operation_pending)
    assert "no original content checksum" in panel.status.text()
    assert not opened and dialog._dirty and dialog._edited_record() == before
    panel._open()
    _wait(app, lambda: not dialog.media_operation_pending)
    assert [Path(value) for value in opened] == [original]
    replacement = tmp_path / "same-bytes-new-location.png"
    shutil.copyfile(original, replacement)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(replacement), ""))
    attempted_reads = []

    def reject_inspection(*args, **_kwargs):
        attempted_reads.append(args)
        raise AssertionError("Unprovable replacement was inspected")

    monkeypatch.setattr(media, "_inspect", reject_inspection)
    panel._relink()
    _wait(app, lambda: not dialog.media_operation_pending)
    assert not attempted_reads
    assert "no content checksum" in dialog.status.text()
    assert dialog._edited_record() == before and dialog._dirty
    assert panel.bookmark_note.text() == "Unsubmitted imported bookmark"
    assert library.load(record.id) == record and len(opened) == 1


@pytest.mark.parametrize("stretch,full_em", [(100, False), (125, False), (100, True), (125, True)])
def test_compact_enlarged_takes_controls_remain_reachable(app, request, portable_takes, make_dialog, monkeypatch, stretch, full_em):
    library, record, _originals, _roots, _projects = portable_takes
    previous_font = app.font()
    font = QFont(previous_font)
    family_name = request.getfixturevalue("box_font") if full_em else ""
    if full_em:
        font.setFamily(family_name)
    font.setPixelSize(22)
    font.setStretch(100 if full_em else stretch)
    app.setFont(font)
    try:
        dialog = make_dialog(library, current_id=record.id)
        family = f'font-family: "{family_name}";' if full_em else ""
        dialog.setStyleSheet(load_stylesheet() + f"QWidget {{ font-size: 22px; {family} }}")
        dialog.resize(480, 500)
        dialog.tabs.setCurrentIndex(0)
        app.processEvents()
        dialog.tabs.setCurrentIndex(4)
        dialog.takes.setCurrentRow(0)
        dialog.status.setText("This stored recording is missing or changed. Locate the same original take to preserve your work.")
        for _ in range(6):
            app.processEvents()
        scroll = dialog.tabs.currentWidget()
        assert isinstance(scroll, QScrollArea)
        assert dialog.width() == 480 and dialog.height() == 500
        if full_em:
            advance = dialog.fontMetrics().horizontalAdvance("MW")
            assert advance == (56 if stretch == 125 else 44)
        assert scroll.viewport().height() > 0
        for button in (dialog.verify_take_button, dialog.open_take_button, dialog.relink_take_button):
            scroll.ensureWidgetVisible(button, 0, 0)
            dialog.content_scroll.ensureWidgetVisible(button, 0, 0)
            app.processEvents()
            assert button.width() >= button.minimumSizeHint().width(), (button.text(), button.size(), button.minimumSizeHint(), stretch)
            assert scroll.viewport().rect().contains(button.mapTo(scroll.viewport(), button.rect().topLeft()))
            assert scroll.viewport().rect().contains(button.mapTo(scroll.viewport(), button.rect().bottomRight()))
            assert scroll.horizontalScrollBar().maximum() == 0
            assert dialog.content_scroll.horizontalScrollBar().maximum() == 0
            viewport = dialog.content_scroll.viewport()
            assert viewport.rect().contains(button.mapTo(viewport, button.rect().topLeft()))
            assert viewport.rect().contains(button.mapTo(viewport, button.rect().bottomRight()))
            button.setFocus()
            assert button.hasFocus()
        scroll.ensureWidgetVisible(dialog.verify_take_button, 0, 0)
        dialog.content_scroll.ensureWidgetVisible(dialog.verify_take_button, 0, 0)
        dialog.verify_take_button.setFocus()
        QTest.keyClick(dialog.verify_take_button, Qt.Key.Key_Space)
        _wait(app, lambda: not dialog.media_operation_pending)
        assert "Content matched when checked" in dialog.takes.item(0).text()
        assert dialog.isVisible()
        dialog.notes.setPlainText("Keep my keyboard draft")
        dialog.timer.stop()
        wanted = {dialog.verify_take_button, dialog.open_take_button, dialog.relink_take_button,
                  dialog.save_button, dialog.continue_button}
        reached = set()
        dialog.activateWindow()
        dialog.takes.setFocus()
        app.processEvents()
        # Qt must scroll focus into view by itself, including both nesting levels.
        for modifiers in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
            for _ in range(45):
                current = app.focusWidget()
                assert current is not None, "keyboard focus left the active Library"
                QTest.keyClick(current, Qt.Key.Key_Tab, modifiers)
                app.processEvents()
                focused = app.focusWidget()
                if isinstance(focused, QPushButton):
                    reached.add(focused)
                    parent = focused.parentWidget()
                    while parent is not None and parent is not dialog:
                        if isinstance(parent, QScrollArea):
                            viewport = parent.viewport()
                            assert viewport.rect().contains(focused.mapTo(viewport, focused.rect().topLeft()))
                            assert viewport.rect().contains(focused.mapTo(viewport, focused.rect().bottomRight()))
                        parent = parent.parentWidget()
        assert wanted.issubset(reached)
        assert dialog.notes.toPlainText() == "Keep my keyboard draft" and dialog._dirty
        dialog.tabs.setCurrentIndex(0)
        dialog.notes.setFocus()
        def fail_save(*_args, **_kwargs):
            raise OSError("Owned disk-full failure")
        monkeypatch.setattr(library, "save", fail_save)
        assert not dialog.save_current()
        for _ in range(6):
            app.processEvents()
        assert "not saved" in dialog.status.text() and dialog.notes.hasFocus()
        assert dialog.rect().contains(dialog.status.mapTo(dialog, dialog.status.rect().topLeft()))
        assert dialog.rect().contains(dialog.status.mapTo(dialog, dialog.status.rect().bottomRight()))
    finally:
        app.setFont(previous_font)
