"""Real setlist controls, template reuse, keyboard moments, and narrow geometry."""
from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from core.rehearsal_plan import MAX_PLAN_FILE_BYTES
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.rehearsal_plan import RehearsalPlanPanel


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qapp):
    widget = RehearsalPlanPanel()
    widget.resize(600, 600)
    widget.show()
    widget.activateWindow()
    qapp.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_edit_next_reorder_remove_undo_and_reload_preserve_drafts(panel):
    changed, selected = [], []
    panel.changed.connect(lambda: changed.append(True))
    panel.song_selected.connect(selected.append)
    panel._add.click()
    panel._song_title.setText("First song")
    panel._key.setText("E minor")
    panel._tempo.setValue(104)
    panel._notes.setPlainText("Keep the verse")
    panel._moment_note.setText("Half-typed thought")
    first = panel.payload()["songs"][0]
    panel._add.click()
    panel._song_title.setText("Second song")
    panel._notes.setPlainText("Second draft")
    panel._previous.click()
    assert panel._notes.toPlainText() == "Keep the verse"
    assert panel._moment_note.text() == "Half-typed thought"
    panel._later.click()
    assert panel.payload()["songs"][1] == first
    panel._remove.click()
    panel._undo.click()
    assert panel.payload()["songs"][1] == first
    payload = panel.payload()
    count = len(changed)
    panel.load_payload(payload)
    assert len(changed) == count
    assert panel.payload() == payload
    assert panel._moment_note.text() == "Half-typed thought"
    assert selected[-1]["title"] == "First song"
    assert "notes" in selected[-1]


def test_keyboard_moment_and_take_open_retain_evidence(panel, qapp):
    panel.add_song("Bridge")
    requests, opened = [], []
    def note_requested(note):
        requests.append(note)
        panel.add_bookmark(note)
    panel.bookmark_requested.connect(note_requested)
    panel.bookmark_open_requested.connect(opened.append)
    panel._moment_note.setText("Nice chorus")
    panel._notes.setFocus()
    qapp.processEvents()
    QTest.keySequence(panel._notes, QKeySequence("Ctrl+M"))
    qapp.processEvents()
    assert requests == ["Nice chorus"]
    assert panel._bookmarks.item(0).text() == "Note · Nice chorus"
    assert not panel._open_moment.isEnabled()
    panel.add_bookmark("Again", timing_verified=True, take_id="take1",
                       take_path="/takes/song.wav", source_identity="sha256:one", position_seconds=73.5)
    assert panel._open_moment.isEnabled()
    panel._open_moment.click()
    assert opened[0]["position_seconds"] == 73.5
    assert opened[0]["source_identity"] == "sha256:one"
    panel._remove_moment.click()
    assert len(panel.payload()["songs"][0]["bookmarks"]) == 1


def test_relink_take_updates_only_matching_bookmark_paths(panel):
    panel.add_song("Tune")
    panel.add_bookmark(
        "Chorus",
        timing_verified=True,
        take_id="take-1",
        take_path="/old/take",
        source_identity="sha256:one",
        position_seconds=4.0,
    )
    panel.add_bookmark(
        "Other identity",
        timing_verified=True,
        take_id="take-1",
        take_path="/old/take",
        source_identity="sha256:two",
        position_seconds=8.0,
    )
    panel.relink_take("take-1", "sha256:one", "/new/take")
    bookmarks = panel.payload()["songs"][0]["bookmarks"]
    assert bookmarks[0]["take_path"] == "/new/take"
    assert bookmarks[0]["position_seconds"] == 4.0
    assert bookmarks[1]["take_path"] == "/old/take"


def test_template_save_and_add_keep_notes_and_no_recording_claims(panel, monkeypatch, tmp_path):
    panel.add_song("A tune")
    panel._goals.setPlainText("Keep time")
    panel._notes.setPlainText("Private draft")
    panel._completed.setChecked(True)
    panel.add_bookmark("Note")
    original = panel.payload()["songs"][0]
    path = tmp_path / "weekly.json"
    monkeypatch.setattr("webjam_qt.widgets.rehearsal_plan.QFileDialog.getSaveFileName", lambda *_: (str(path), ""))
    panel._save.click()
    data = json.loads(path.read_text())
    assert data["songs"][0]["notes"] == ""
    assert data["songs"][0]["goals"] == "Keep time"
    monkeypatch.setattr("webjam_qt.widgets.rehearsal_plan.QFileDialog.getOpenFileName", lambda *_: (str(path), ""))
    panel._load.click()
    songs = panel.payload()["songs"]
    assert len(songs) == 2 and songs[0] == original
    assert not songs[1]["completed"] and not songs[1]["bookmarks"]


def _fill_template_to_size(panel, size, character):
    # Include the real JSON structure, whitespace, escaping and UTF-8 overhead.
    panel._goals.setPlainText("")
    overhead = len(json.dumps(panel._plan.template_payload(), ensure_ascii=False, indent=2).encode("utf-8"))
    # Short paragraphs avoid making this a stress test of Qt's line wrapping.
    unit = character * 80 + "\n"
    unit_bytes = len(json.dumps(unit, ensure_ascii=False).encode("utf-8")) - 2
    count, remainder = divmod(size - overhead, unit_bytes)
    panel._goals.setPlainText(unit * count + "x" * remainder)


@pytest.mark.parametrize("character", ["x", "é", "🎵", '"'])
@pytest.mark.parametrize("replace_existing", [False, True])
def test_oversized_template_export_keeps_destination_and_all_drafts(
    panel, monkeypatch, tmp_path, character, replace_existing
):
    panel.add_song("First song")
    panel._notes.setPlainText("First song's private notes")
    panel.add_song("Second song")
    panel._title.setText("Weekly plan")
    panel._key.setText("E minor")
    panel._tempo.setValue(104)
    panel._notes.setPlainText("Keep these notes")
    panel._next_steps.setPlainText("Keep these next steps")
    panel._completed.setChecked(True)
    panel.add_bookmark("Keep this moment")
    panel._moment_note.setText("Unfinished moment")
    path = tmp_path / "weekly.json"
    warnings = []
    monkeypatch.setattr("webjam_qt.widgets.rehearsal_plan.QFileDialog.getSaveFileName", lambda *_: (str(path), ""))
    monkeypatch.setattr("webjam_qt.widgets.rehearsal_plan.QMessageBox.warning", lambda *args: warnings.append(args))
    if replace_existing:
        panel._save.click()
        previous_bytes = path.read_bytes()
        previous_stat = path.stat()
        assert panel._feedback.text() == "Song order and goals saved for another rehearsal."
    _fill_template_to_size(panel, MAX_PLAN_FILE_BYTES + 1, character)
    before = panel.payload()
    panel._save.click()

    if replace_existing:
        assert path.read_bytes() == previous_bytes
        assert path.stat().st_ino == previous_stat.st_ino
        assert path.stat().st_mtime_ns == previous_stat.st_mtime_ns
    else:
        assert not path.exists()
    assert list(tmp_path.iterdir()) == ([path] if replace_existing else [])
    assert panel.payload() == before
    assert panel._title.text() == before["title"]
    song = before["songs"][1]
    assert panel._song_title.text() == song["title"]
    assert panel._key.text() == song["key"]
    assert panel._tempo.value() == song["tempo"]
    assert panel._goals.toPlainText() == song["goals"]
    assert panel._notes.toPlainText() == song["notes"]
    assert panel._next_steps.toPlainText() == song["next_steps"]
    assert panel._moment_note.text() == song["moment_draft"]
    assert len(warnings) == 1
    assert warnings[0][1] == "Plan not saved"
    assert "Shorten the goals" in warnings[0][2]
    assert panel._feedback.text().startswith("Plan not saved.")
    assert "saved for another rehearsal" not in panel._feedback.text()


@pytest.mark.parametrize("character", ["x", "🎵"])
@pytest.mark.parametrize("size", [MAX_PLAN_FILE_BYTES - 1, MAX_PLAN_FILE_BYTES])
def test_template_export_at_import_limit_round_trips(panel, monkeypatch, tmp_path, character, size):
    panel.add_song("First song")
    panel._goals.setPlainText("Keep the order")
    panel.add_song("Café 🎵")
    panel._key.setText("E minor")
    panel._tempo.setValue(104)
    panel._notes.setPlainText("Private session notes")
    _fill_template_to_size(panel, size, character)
    before = panel.payload()
    path = tmp_path / "boundary.json"
    warnings = []
    monkeypatch.setattr("webjam_qt.widgets.rehearsal_plan.QMessageBox.warning", lambda *args: warnings.append(args))
    monkeypatch.setattr("webjam_qt.widgets.rehearsal_plan.QFileDialog.getSaveFileName", lambda *_: (str(path), ""))
    panel._save.click()
    assert path.stat().st_size == size
    assert panel.payload() == before
    assert panel._feedback.text() == "Song order and goals saved for another rehearsal."
    monkeypatch.setattr("webjam_qt.widgets.rehearsal_plan.QFileDialog.getOpenFileName", lambda *_: (str(path), ""))
    panel._load.click()
    songs = panel.payload()["songs"]
    assert songs[:2] == before["songs"]
    assert len(songs) == 4
    for original, imported in zip(before["songs"], songs[2:]):
        for field in ("title", "key", "tempo", "goals"):
            assert imported[field] == original[field]
        assert imported["notes"] == ""
        assert not imported["completed"] and not imported["bookmarks"]
    assert warnings == []


@pytest.mark.parametrize("bad", [b'{"version":2}', b"{broken", b" " * (MAX_PLAN_FILE_BYTES + 1)])
def test_bad_import_leaves_the_current_session_intact(panel, monkeypatch, tmp_path, bad):
    panel.add_song("Keep me")
    before = panel.payload()
    path = tmp_path / "bad.json"
    path.write_bytes(bad)
    warnings = []
    monkeypatch.setattr("webjam_qt.widgets.rehearsal_plan.QMessageBox.warning", lambda *args: warnings.append(args))
    monkeypatch.setattr("webjam_qt.widgets.rehearsal_plan.QFileDialog.getOpenFileName", lambda *_: (str(path), ""))
    panel._load.click()
    assert panel.payload() == before
    assert len(warnings) == 1


@pytest.mark.parametrize("size", [(320, 500), (720, 560)])
@pytest.mark.parametrize("font_size", [13, 22])
def test_controls_remain_full_width_and_scrollable_with_enlarged_text(panel, qapp, size, font_size):
    panel.setStyleSheet(load_stylesheet() + f"\nQLabel, QPushButton, QLineEdit, QComboBox, QCheckBox {{ font-size: {font_size}px; }}")
    panel.add_song("A long title for the rehearsal")
    panel._notes.setPlainText("Draft remains editable")
    panel.resize(*size)
    for _ in range(6):
        qapp.processEvents()
    viewport = panel._scroll.viewport()
    assert panel.size().width() == size[0]
    assert panel._scroll.widget().width() <= viewport.width()
    for button in panel.findChildren(QPushButton):
        if not button.isHidden():
            assert button.width() >= button.minimumSizeHint().width(), button.text()
    assert panel._scroll.verticalScrollBar().maximum() > 0
    panel._scroll.ensureWidgetVisible(panel._load)
    qapp.processEvents()
    assert panel._load.isVisibleTo(panel)
    assert panel._notes.toPlainText() == "Draft remains editable"
    assert panel._notes.tabChangesFocus()
