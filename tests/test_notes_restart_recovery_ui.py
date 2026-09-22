"""Restart drafts remain reviewable through the real local Notes controls."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core import file_io
from core.creative_modes import get_creator_profile_by_key_or_default
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.notes_recovery_dialog import NotesRecoveryDialog
from webjam_qt.widgets.session_canvas import SessionCanvas


MUSIC_FILE = ".webjam_notes.md"
JOURNAL_FILE = ".webjam_notes.recovery.json"
SAVED = "Earlier saved music notes"
DRAFT = "A recovered verse\n\nKeep the quieter ending for tomorrow."
ART = "Art notes already saved"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def recovered_ui(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    widgets = []
    blocked = set()
    original_write = file_io._atomic_write

    def write(path, data, *, mode):
        if Path(path).name in blocked:
            raise OSError(errno.ENOSPC, "Controlled unavailable local storage")
        return original_write(path, data, mode=mode)

    monkeypatch.setattr(file_io, "_atomic_write", write)

    def fresh(profile="music"):
        canvas = SessionCanvas()
        canvas.setStyleSheet(load_stylesheet())
        canvas.set_creator_profile(get_creator_profile_by_key_or_default(profile))
        owner = SessionPersistence(SimpleNamespace(), canvas, creator_profile_key=profile)
        canvas.notes_changed.connect(owner.notes_changed)
        owner._load_notes_only()
        widgets.append(canvas)
        return canvas, owner

    def create(*, active="music", conflict=False, unconfirmed=False):
        (tmp_path / MUSIC_FILE).write_text(SAVED)
        (tmp_path / ".webjam_notes.art.md").write_text(ART)
        initial_canvas, initial_owner = fresh()
        initial_canvas.edit_notes(DRAFT)
        blocked.add(MUSIC_FILE)
        assert not initial_owner._save_notes_only()
        blocked.clear()
        assert initial_owner.notes_restart_recovery_state("music") == "confirmed"
        if conflict:
            (tmp_path / MUSIC_FILE).write_text("A newer original saved elsewhere")
        canvas, owner = fresh(active)
        if unconfirmed:
            blocked.add(JOURNAL_FILE)
            canvas.edit_notes(DRAFT + "\nOne more idea after reopening.")
            assert not owner._save_notes_only()
            assert owner.notes_restart_recovery_state("music") == "unconfirmed"
        return SimpleNamespace(canvas=canvas, owner=owner, blocked=blocked, fresh=fresh)

    yield create
    for canvas in reversed(widgets):
        for dialog in canvas.findChildren(NotesRecoveryDialog):
            dialog.deleteLater()
        canvas.close()
        canvas.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _settle(qapp):
    qapp.processEvents()
    qapp.processEvents()


def _dialog(owner, canvas, qapp):
    dialog = NotesRecoveryDialog(owner, canvas)
    dialog.resize(560, 420)
    dialog.show()
    dialog.activateWindow()
    _settle(qapp)
    return dialog


def _rect(widget, parent):
    return QRect(widget.mapTo(parent, QPoint()), widget.size())


def _capture(widget, name):
    # Opt-in local review artifacts; ordinary test runs do not write screenshots.
    folder = os.environ.get("WEBJAM_TEST_RECOVERY_SCREENSHOTS")
    if folder:
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        assert widget.grab().save(str(path / f"{name}.png"))


@pytest.mark.parametrize("active", ["music", "art"])
def test_recovered_workspace_opens_real_save_route_without_implicit_write(
    recovered_ui, qapp, tmp_path, monkeypatch, active,
):
    pair = recovered_ui(active=active)
    canvas, owner = pair.canvas, pair.owner
    expected_editor = DRAFT if active == "music" else ART
    assert canvas.current_notes() == expected_editor
    assert owner.notes_recovery_requires_review("music")
    assert "Music" in canvas._notes_save_status.text()
    assert "Recovered draft" in canvas._notes_save_status.text()
    assert "Music" in canvas._save_notes_button.accessibleDescription()
    assert not canvas._save_notes_button.isHidden()
    dialogs = []

    def open_dialog(dialog):
        dialogs.append(dialog)
        dialog.show()
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(NotesRecoveryDialog, "exec", open_dialog)
    controller = SimpleNamespace(
        _save_notes=owner._save_notes_only, _persistence=owner, window=canvas,
    )
    canvas.save_notes_requested.connect(lambda: ApplicationController._recover_notes(controller))
    canvas.show()
    _settle(qapp)
    canvas._save_notes_button.click()
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert dialog._profile.currentData() == "music"
    assert "Review this draft recovered after restart" in dialog._message.text()
    assert "recovery copy of this draft is saved" in dialog._message.text()
    assert (tmp_path / MUSIC_FILE).read_text() == SAVED
    assert owner.profile_key == active
    assert canvas.current_notes() == expected_editor

    dialog._save.click()
    assert (tmp_path / MUSIC_FILE).read_text() == DRAFT
    assert (tmp_path / ".webjam_notes.art.md").read_text() == ART
    assert not owner.has_unsaved_notes
    assert owner.profile_key == active
    assert canvas.current_notes() == expected_editor
    assert dialog.result() == dialog.DialogCode.Accepted


def test_changed_original_disables_save_and_keyboard_export_preserves_both_versions(
    recovered_ui, qapp, tmp_path, monkeypatch,
):
    pair = recovered_ui(conflict=True)
    dialog = _dialog(pair.owner, pair.canvas, qapp)
    assert not dialog._save.isEnabled()
    assert "Export Copy" in dialog._save.accessibleDescription()
    assert "keep both versions" in dialog._message.text()
    assert dialog._export.isEnabled()
    copy = tmp_path / "recovered-copy.md"
    monkeypatch.setattr(
        "webjam_qt.widgets.notes_recovery_dialog.QFileDialog.getSaveFileName",
        lambda *args: (str(copy), ""),
    )
    dialog._export.setFocus()
    assert dialog._export.hasFocus()
    QTest.keyClick(dialog._export, Qt.Key.Key_Space)
    assert copy.read_text() == DRAFT
    assert (tmp_path / MUSIC_FILE).read_text() == "A newer original saved elsewhere"
    assert not pair.owner.has_unsaved_notes
    assert dialog.result() == dialog.DialogCode.Accepted


def test_newer_edit_during_picker_survives_export_of_recovered_snapshot(
    recovered_ui, qapp, tmp_path, monkeypatch,
):
    pair = recovered_ui()
    dialog = _dialog(pair.owner, pair.canvas, qapp)
    copy = tmp_path / "older-recovered-copy.md"
    newer = "A newer verse typed while the native picker was open"

    def choose_copy(*args):
        pair.canvas.edit_notes(newer)
        return str(copy), ""

    monkeypatch.setattr(
        "webjam_qt.widgets.notes_recovery_dialog.QFileDialog.getSaveFileName", choose_copy,
    )
    dialog._export.click()
    assert copy.read_text() == DRAFT
    assert (tmp_path / MUSIC_FILE).read_text() == SAVED
    assert pair.owner.unsaved_notes == (("music", newer),)
    assert pair.canvas.current_notes() == dialog._editor.toPlainText() == newer
    assert dialog.isVisible()
    assert "could not be confirmed" in dialog._message.text()
    assert not pair.owner._save_notes_only()
    _, restarted = pair.fresh()
    assert restarted.unsaved_notes == (("music", newer),)


@pytest.mark.parametrize("state", ["recovered", "conflict", "unconfirmed"])
def test_restart_messages_fit_compact_notes_and_review_dialog(
    recovered_ui, qapp, state,
):
    pair = recovered_ui(conflict=state == "conflict", unconfirmed=state == "unconfirmed")
    canvas, owner = pair.canvas, pair.owner
    canvas.resize(280, 560)
    canvas.show()
    canvas.activateWindow()
    canvas._notes.setFocus()
    _settle(qapp)
    owner._refresh_notes_state()
    assert canvas._notes.hasFocus()
    assert canvas.size() == QSize(280, 560)
    for widget in (canvas._header, canvas._notes_save_status, canvas._save_notes_button, canvas._notes):
        assert widget.isVisibleTo(canvas)
        assert canvas.rect().contains(_rect(widget, canvas))
    assert canvas._notes.height() >= canvas._notes.minimumSizeHint().height()
    for label in canvas.findChildren(QLabel):
        if label.isVisibleTo(canvas) and label.wordWrap():
            assert label.height() >= label.heightForWidth(label.width())
    _capture(canvas, f"notes-restart-{state}-compact")

    dialog = _dialog(owner, canvas, qapp)
    assert dialog.size() == QSize(560, 420)
    rects = [_rect(widget, dialog) for widget in (dialog._message, dialog._profile, dialog._editor)]
    assert all(dialog.rect().contains(rect) for rect in rects)
    assert not rects[0].intersects(rects[1])
    assert not rects[1].intersects(rects[2])
    assert dialog._message.height() >= dialog._message.heightForWidth(dialog._message.width())
    for button in dialog.findChildren(QPushButton):
        assert dialog.rect().contains(_rect(button, dialog))
        assert button.width() >= button.minimumSizeHint().width()
        assert button.height() >= button.minimumSizeHint().height()
    assert dialog._editor.accessibleName() == "Retained local notes"
    assert dialog._message.accessibleDescription() == dialog._message.text()
    if state == "unconfirmed":
        assert "restart recovery copy could not be confirmed" in dialog._message.text()
        assert "Keep WebJam open" in dialog._message.text()
        assert "recovery copy of this draft is saved" not in dialog._message.text()
    else:
        assert "recovery copy of this draft is saved" in dialog._message.text()
    dialog._editor.setFocus()
    owner._refresh_notes_state()
    assert dialog._editor.hasFocus()
    _capture(dialog, f"notes-restart-{state}-dialog")
