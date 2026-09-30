"""Saved Art context survives restart and only opens references on request."""
from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog, QPushButton

from core.art_workspace import art_summary, make_reference, normalize_art_workspace, valid_reference_url
from core.session_library import SessionLibrary
from webjam_qt.widgets import art_workspace as widget_module
from webjam_qt.widgets.art_workspace import ArtWorkspacePanel


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    widget = ArtWorkspacePanel()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def _click(panel, label):
    next(button for button in panel.findChildren(QPushButton) if button.text() == label).click()


def test_restart_keeps_art_context_and_manual_bookmarks_without_opening_tools(tmp_path, panel, monkeypatch):
    opened = []
    monkeypatch.setattr(widget_module.QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    library = SessionLibrary(tmp_path / "library")
    panel.brief.setPlainText("Study reflected light")
    panel.progress.setPlainText("Finished the underpainting")
    panel.next_steps.setPlainText("Try warmer shadows")
    panel.add_reference("https://example.com/lesson", kind="url", title="Light lesson")
    panel.position.setValue(132.5)
    panel.bookmark_note.setText("Layer the warm edge")
    _click(panel, "Save lesson bookmark")
    assert panel.references.currentRow() == 0
    record = library.create("art", "Evening painting", art=panel.payload())
    reopened = ArtWorkspacePanel()
    try:
        reopened.load_payload(SessionLibrary(library.root).load(record.id).art)
        assert reopened.brief.toPlainText() == "Study reflected light"
        assert reopened.progress.toPlainText() == "Finished the underpainting"
        assert reopened.next_steps.toPlainText() == "Try warmer shadows"
        reopened.bookmarks.setCurrentRow(0)
        assert reopened.position.value() == 132.5
        assert reopened.bookmark_note.text() == "Layer the warm edge"
        assert "in your player" in reopened.status.text()
        assert opened == []
        _click(reopened, "Open reference")
        assert [url.toString() for url in opened] == ["https://example.com/lesson"]
    finally:
        reopened.close()
        reopened.deleteLater()


def test_moved_file_relink_keeps_bookmark_identity_and_requires_explicit_open(tmp_path, panel, monkeypatch):
    old_path = tmp_path / "painting.kra"
    old_path.write_bytes(b"local project placeholder")
    panel.add_reference(str(old_path), kind="file", title="My painting")
    panel.position.setValue(75)
    panel.bookmark_note.setText("Check the layers")
    _click(panel, "Save lesson bookmark")
    before = panel.payload()
    moved_path = tmp_path / "moved.kra"
    old_path.rename(moved_path)
    opened = []
    monkeypatch.setattr(widget_module.QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    panel.load_payload(before)
    panel.references.setCurrentRow(0)
    assert "missing" in panel.references.item(0).text()
    _click(panel, "Open reference")
    assert opened == []
    assert "Relink" in panel.status.text()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args, **_kwargs: (str(moved_path), ""))
    _click(panel, "Relink…")
    assert panel.references.currentRow() == 0
    after = panel.payload()
    assert after["references"][0]["id"] == before["references"][0]["id"]
    assert after["references"][0]["locator"] == str(moved_path)
    assert after["bookmarks"] == before["bookmarks"]
    assert "missing" not in panel.references.item(0).text()
    assert opened == []
    _click(panel, "Open reference")
    assert opened[0].toLocalFile() == str(moved_path)


def test_removing_reference_only_removes_its_linked_bookmarks(tmp_path, panel):
    path = tmp_path / "keep.png"
    path.write_bytes(b"do not delete this file")
    panel.add_reference(str(path), kind="file")
    panel.bookmark_note.setText("First")
    _click(panel, "Save lesson bookmark")
    panel.add_reference("https://example.com/second", kind="url", title="Second lesson")
    panel.bookmark_note.setText("Second")
    _click(panel, "Save lesson bookmark")
    retained = panel.payload()["bookmarks"][1]
    panel.references.setCurrentRow(0)
    _click(panel, "Remove reference")
    assert path.read_bytes() == b"do not delete this file"
    assert panel.payload()["bookmarks"] == [retained]


def test_summary_is_portable_and_preserves_manual_position_without_private_locator(tmp_path, panel):
    private_path = tmp_path / "private-project" / "source.png"
    panel.add_reference(str(private_path), kind="file", title="Color reference")
    panel.brief.setPlainText("Portrait study")
    panel.progress.setPlainText("Blocked in the shapes")
    panel.next_steps.setPlainText("Refine the edges")
    panel.position.setValue(92)
    panel.bookmark_note.setText("Return to soft edges")
    _click(panel, "Save lesson bookmark")
    summary = art_summary("Portrait", panel.payload())
    assert "Portrait study" in summary
    assert "Blocked in the shapes" in summary
    assert "Refine the edges" in summary
    assert "Color reference — 1:32: Return to soft edges" in summary
    assert str(tmp_path) not in summary
    assert "source.png" not in summary


@pytest.mark.parametrize("url", [
    "file:///private/notes", "javascript:alert(1)", "https://user:password@example.com",
    "https://:password@example.com", "https://[broken", "https://example.com/one\ntwo",
])
def test_only_ordinary_http_references_are_accepted(url):
    assert not valid_reference_url(url)
    with pytest.raises(ValueError):
        make_reference(url, kind="url")


def test_bookmark_limit_keeps_unsaved_note_and_existing_context(panel):
    reference = make_reference("https://example.com/lesson", kind="url")
    bookmarks = [{"id": f"mark-{index}", "reference_id": reference["id"],
                  "seconds": index, "note": "Existing"} for index in range(500)]
    panel.load_payload({"version": 1, "references": [reference], "bookmarks": bookmarks})
    panel.references.setCurrentRow(0)
    panel.bookmark_note.setText("Still here, not silently dropped")
    _click(panel, "Save lesson bookmark")
    assert len(panel.payload()["bookmarks"]) == 500
    assert panel.bookmark_note.text() == "Still here, not silently dropped"
    assert "500" in panel.status.text()


def test_invalid_loaded_bookmark_leaves_current_project_unchanged(panel):
    panel.brief.setPlainText("My current draft")
    before = panel.payload()
    with pytest.raises(ValueError):
        panel.load_payload({"version": 1, "bookmarks": [
            {"id": "bad", "reference_id": "missing", "seconds": 3, "note": "Bad reference"},
        ]})
    assert panel.payload() == before


def test_workspace_payload_copy_is_independent_of_saved_record(tmp_path):
    reference = make_reference("https://example.com/lesson", kind="url")
    library = SessionLibrary(tmp_path)
    record = library.create("art", "Study", art=normalize_art_workspace({"version": 1, "references": [reference]}))
    updated = normalize_art_workspace(record.art)
    updated["references"][0]["title"] = "New title"
    saved = library.save(replace(record, art=updated))
    assert record.art["references"][0]["title"] != "New title"
    assert library.load(saved.id).art["references"][0]["title"] == "New title"
