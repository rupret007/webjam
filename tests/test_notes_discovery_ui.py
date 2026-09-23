"""Retained drafts are discoverable while another workspace stays in use."""

from __future__ import annotations

import errno
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QSize
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core.creative_modes import get_creator_profile_by_key_or_default
from core.notes_recovery import (
    NotesRecoveryDraft,
    notes_fingerprint,
    write_notes_recovery,
)
from tests.test_native_art_activities import native_room as _native_room_fixture
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.widgets.notes_recovery_dialog import NotesRecoveryDialog

native_room = _native_room_fixture


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def retained_window(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="My work",
    )
    window.setStyleSheet(load_stylesheet())
    canvas = window.session_canvas
    owner = SessionPersistence(window.session_strip, canvas)
    canvas.notes_changed.connect(owner.notes_changed)
    owner._load_notes_only()
    original_write = persistence_module.atomic_write_text
    blocked = {".webjam_notes.md"}

    def write(path, text, **kwargs):
        if path.name in blocked:
            raise OSError(errno.EACCES, "PRIVATE_PATH PRIVATE_DRAFT")
        return original_write(path, text, **kwargs)

    monkeypatch.setattr(persistence_module, "atomic_write_text", write)
    canvas.edit_notes("PRIVATE_DRAFT music idea")
    assert not owner._save_notes_only()
    owner.switch_profile_key("art")
    window.set_creator_profile(get_creator_profile_by_key_or_default("art"))
    window.resize(720, 560)
    window.show()
    qapp.processEvents()
    yield SimpleNamespace(window=window, canvas=canvas, owner=owner, blocked=blocked)
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _review_button(window):
    matches = [
        button
        for button in window.findChildren(QPushButton)
        if button.text() == "Review Notes" and button.isVisibleTo(window)
    ]
    assert len(matches) == 1, (
        "A retained draft needs a visible review action outside hidden Notes"
    )
    return matches[0]


@pytest.mark.parametrize("workspace", ["live", "studio", "offline_studio"])
def test_hidden_music_draft_has_visible_review_without_switching_active_workspace(
    retained_window,
    qapp,
    workspace,
):
    window, canvas, owner = (
        retained_window.window,
        retained_window.canvas,
        retained_window.owner,
    )
    if workspace == "studio":
        window.workspace_stack.setCurrentWidget(window.reference_studio)
    elif workspace == "offline_studio":
        window.show_reference_studio_only()
    qapp.processEvents()
    active = window.workspace_stack.currentWidget()
    assert not canvas.isVisibleTo(window)
    review = _review_button(window)
    assert review.isEnabled()
    labels = [
        label.text()
        for label in window.findChildren(QLabel)
        if label.isVisibleTo(window)
    ]
    assert any("Music notes need saving" in text for text in labels)
    assert "Music" in review.accessibleDescription()
    assert "PRIVATE_" not in review.accessibleDescription() + " ".join(labels)
    assert owner.profile_key == "art"
    assert canvas.current_notes() == ""
    assert window.workspace_stack.currentWidget() is active
    assert window.size() == QSize(720, 560)
    rect = QRect(review.mapTo(window, QPoint()), review.size())
    assert window.rect().contains(rect)


def test_export_settles_hidden_notice_but_later_failure_returns_it(
    retained_window, qapp, tmp_path
):
    window, owner = retained_window.window, retained_window.owner
    _review_button(window)
    assert owner.export_pending_notes(
        "music", "PRIVATE_DRAFT music idea", str(tmp_path / "copy.md")
    )
    qapp.processEvents()
    assert not any(
        button.text() == "Review Notes" and button.isVisibleTo(window)
        for button in window.findChildren(QPushButton)
    )
    owner.switch_profile_key("music")
    retained_window.canvas.edit_notes("A later idea")
    assert not owner._save_notes_only()
    owner.switch_profile_key("art")
    qapp.processEvents()
    _review_button(window)


def test_successful_active_notes_save_keeps_hidden_workspace_review(
    retained_window, qapp
):
    retained_window.canvas.edit_notes("Art draft")
    assert not retained_window.owner._save_notes_only()
    assert retained_window.owner.unsaved_notes == (
        ("music", "PRIVATE_DRAFT music idea"),
    )
    qapp.processEvents()
    assert "Music" in _review_button(retained_window.window).accessibleDescription()


def test_notice_follows_notes_visibility_without_taking_editor_focus(
    retained_window, qapp
):
    window, canvas = retained_window.window, retained_window.canvas
    _review_button(window)
    canvas.show()
    canvas._notes.setFocus()
    qapp.processEvents()
    assert not window._notes_notice.isVisibleTo(window)
    assert canvas._notes.hasFocus()
    canvas.hide()
    qapp.processEvents()
    _review_button(window)


def test_hidden_notice_remains_reachable_with_larger_text(retained_window, qapp):
    window = retained_window.window
    window._notes_notice.setStyleSheet("QLabel, QPushButton { font-size: 22px; }")
    qapp.processEvents()
    review = _review_button(window)
    label = window._notes_notice_label
    for widget in (review, label):
        rect = QRect(widget.mapTo(window, QPoint()), widget.size())
        assert window.rect().contains(rect)
        assert widget.height() >= widget.fontMetrics().height()
    assert window.size() == QSize(720, 560)


def test_native_room_can_review_hidden_restart_draft_without_writing_or_changing_room(
    native_room,
    qapp,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    primary = tmp_path / ".webjam_notes.md"
    primary.write_text("Saved music")
    write_notes_recovery(
        tmp_path / ".webjam_notes.recovery.json",
        {
            "music": NotesRecoveryDraft(
                "Recovered music", notes_fingerprint("Saved music")
            )
        },
        expected={},
    )
    pair = native_room(profile="art")
    app, window = pair.app, pair.app.window
    room = app._room_participant
    before = (room.state, room.generation, room.native_source)
    active = window.workspace_stack.currentWidget()
    dialogs = []

    def review(dialog):
        dialogs.append(dialog)
        dialog.show()
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(NotesRecoveryDialog, "exec", review)
    try:
        _review_button(window).click()
        assert len(dialogs) == 1
        dialog = dialogs[0]
        assert dialog._profile.currentData() == "music"
        assert dialog._editor.toPlainText() == "Recovered music"
        assert primary.read_text() == "Saved music"
        assert app._persistence.profile_key == app._active_creator_profile_key == "art"
        assert (room.state, room.generation, room.native_source) == before
        assert window.workspace_stack.currentWidget() is active
        assert window.session_canvas.current_notes() == ""
        dialog._save.click()
        assert primary.read_text() == "Recovered music"
        assert not app._persistence.has_unsaved_notes
        qapp.processEvents()
        assert not window._notes_notice.isVisibleTo(window)
    finally:
        for dialog in dialogs:
            dialog.deleteLater()
        if app._persistence.has_unsaved_notes:
            assert app._persistence.save_recovered_notes("music", "Recovered music")


def test_cancel_review_resumes_a_preexisting_autosave_after_leaving_modal(
    native_room,
    qapp,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    primary = tmp_path / ".webjam_notes.md"
    primary.write_text("Saved music")
    write_notes_recovery(
        tmp_path / ".webjam_notes.recovery.json",
        {
            "music": NotesRecoveryDraft(
                "Recovered music", notes_fingerprint("Saved music")
            )
        },
        expected={},
    )
    app = native_room(profile="art").app
    art = tmp_path / ".webjam_notes.art.md"
    app.window.session_canvas.edit_notes("A new Art idea")
    assert app._notes_save_timer.isActive()

    def cancel(dialog):
        assert not app._notes_save_timer.isActive()
        assert not art.exists()
        assert primary.read_text() == "Saved music"
        return dialog.DialogCode.Rejected

    monkeypatch.setattr(NotesRecoveryDialog, "exec", cancel)
    try:
        app._review_retained_notes()
        assert app._notes_save_timer.isActive()
        app._notes_save_timer.timeout.emit()
        assert art.read_text() == "A new Art idea"
        assert primary.read_text() == "Saved music"
    finally:
        app._save_notes()
        assert app._persistence.save_recovered_notes("music", "Recovered music")
