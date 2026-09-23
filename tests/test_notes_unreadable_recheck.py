"""An unreadable saved original has a read-only recovery action before any draft."""

from __future__ import annotations

import errno
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from core.creative_modes import get_creator_profile_by_key_or_default
from core.settings import AppSettings
from webjam_qt.controllers import session_persistence as persistence_module
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.session_canvas import SessionCanvas
from webjam_qt.windows.conductor_window import ConductorWindow

UNREADABLE_COPY = "Saved notes could not be opened. Choose Recheck Saved Notes."


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def unreadable_notes(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    original = tmp_path / ".webjam_notes.md"
    original.write_bytes(b"\xffPRIVATE_ORIGINAL")
    canvas = SessionCanvas()
    canvas.setStyleSheet(load_stylesheet())
    canvas.set_creator_profile(get_creator_profile_by_key_or_default("music"))
    owner = SessionPersistence(SimpleNamespace(), canvas)
    canvas.notes_changed.connect(owner.notes_changed)
    owner._load_notes_only()
    canvas.resize(280, 560)
    canvas.show()
    qapp.processEvents()
    assert owner.notes_save_state == "unreadable"
    assert not owner.has_unsaved_notes
    yield SimpleNamespace(owner=owner, canvas=canvas, original=original)
    canvas.close()
    canvas.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_recheck_reopens_original_without_writes_or_edit_notifications(
    unreadable_notes,
    monkeypatch,
):
    case = unreadable_notes
    case.original.write_text("Saved rehearsal ideas")
    changed = QSignalSpy(case.canvas.notes_changed)
    writes = Mock(side_effect=AssertionError("Rechecking saved notes must never write"))
    monkeypatch.setattr(persistence_module, "atomic_write_text", writes)
    monkeypatch.setattr(persistence_module, "write_notes_recovery", writes)
    assert case.owner.reload_unreadable_notes("music")
    assert case.canvas.current_notes() == "Saved rehearsal ideas"
    assert case.owner.notes_save_state == "saved"
    assert not case.owner.has_unsaved_notes
    assert case.canvas._recheck_notes_button.isHidden()
    assert case.original.read_text() == "Saved rehearsal ideas"
    assert changed.count() == 0
    writes.assert_not_called()


@pytest.mark.parametrize(
    "kind", ["invalid", "missing", "directory", "symlink", "oversize", "permission"]
)
def test_failed_recheck_keeps_protection_and_a_named_action(
    unreadable_notes,
    tmp_path,
    monkeypatch,
    kind,
):
    case = unreadable_notes
    if kind != "invalid":
        case.original.unlink()
    target = tmp_path / "PRIVATE_TARGET.md"
    target.write_text("Keep this saved original")
    if kind == "directory":
        case.original.mkdir()
    elif kind == "symlink":
        case.original.symlink_to(target)
    elif kind == "oversize":
        monkeypatch.setattr(persistence_module, "_MAX_NOTES_FILE_BYTES", 16)
        case.original.write_text("Long saved original " * 10)
    elif kind == "permission":
        case.original.write_text("Saved rehearsal ideas")
        monkeypatch.setattr(
            persistence_module,
            "_read_bounded_notes",
            Mock(
                side_effect=OSError(errno.EACCES, "PRIVATE_ERROR", str(case.original))
            ),
        )
    assert not case.owner.reload_unreadable_notes("music")
    assert case.owner.notes_save_state == "unreadable"
    assert case.canvas.current_notes() == ""
    assert not case.owner.has_unsaved_notes
    assert target.read_text() == "Keep this saved original"
    assert case.canvas._notes_save_status.text() == UNREADABLE_COPY
    assert case.canvas._recheck_notes_button.isVisibleTo(case.canvas)
    assert case.canvas._recheck_notes_button.isEnabled()
    assert "PRIVATE" not in (
        case.canvas._notes_save_status.text()
        + case.canvas._notes_save_status.accessibleDescription()
        + case.canvas._recheck_notes_button.accessibleDescription()
    )


def test_existing_empty_original_is_reopened_without_fabricating_a_draft(
    unreadable_notes,
):
    case = unreadable_notes
    case.original.write_text("")
    assert case.owner.reload_unreadable_notes("music")
    assert case.owner.notes_save_state == "saved"
    assert case.canvas.current_notes() == ""
    assert not case.owner.has_unsaved_notes
    assert case.original.read_bytes() == b""


def test_late_recheck_cannot_replace_a_typed_draft(unreadable_notes, monkeypatch):
    case = unreadable_notes
    case.original.write_text("Saved rehearsal ideas")
    emitted = QSignalSpy(case.canvas.recheck_saved_notes_requested)
    case.canvas.edit_notes("New draft written during recovery")
    reads = Mock(
        side_effect=AssertionError("A late click must not read over the draft")
    )
    monkeypatch.setattr(persistence_module, "_read_bounded_notes", reads)
    case.canvas._recheck_notes_button.click()
    assert emitted.count() == 0
    assert not case.owner.reload_unreadable_notes("music")
    assert case.canvas.current_notes() == "New draft written during recovery"
    assert case.owner.unsaved_notes == (("music", "New draft written during recovery"),)
    assert case.original.read_text() == "Saved rehearsal ideas"
    reads.assert_not_called()


@pytest.mark.parametrize("change", ["draft", "workspace", "unnotified_editor"])
def test_context_changed_during_read_keeps_the_newer_editor(
    unreadable_notes,
    tmp_path,
    monkeypatch,
    change,
):
    case = unreadable_notes
    case.original.write_text("Saved rehearsal ideas")
    art = tmp_path / ".webjam_notes.art.md"
    art.write_text("Saved Art ideas")
    read = persistence_module._read_bounded_notes

    def read_then_change(path):
        text = read(path)
        if path == case.original:
            if change == "draft":
                case.canvas.edit_notes("A newer draft")
            elif change == "workspace":
                case.owner.switch_profile_key("art")
            else:
                case.canvas.restore_notes("A newer restored editor")
        return text

    monkeypatch.setattr(persistence_module, "_read_bounded_notes", read_then_change)
    assert not case.owner.reload_unreadable_notes("music")
    expected = {
        "draft": "A newer draft",
        "workspace": "Saved Art ideas",
        "unnotified_editor": "A newer restored editor",
    }[change]
    assert case.canvas.current_notes() == expected
    assert case.owner.profile_key == ("art" if change == "workspace" else "music")
    assert case.original.read_text() == "Saved rehearsal ideas"
    assert art.read_text() == "Saved Art ideas"


def test_stale_profile_does_not_read_or_change_the_active_editor(
    unreadable_notes, tmp_path, monkeypatch
):
    case = unreadable_notes
    (tmp_path / ".webjam_notes.art.md").write_text("Saved Art ideas")
    case.owner.switch_profile_key("art")
    read = Mock(
        side_effect=AssertionError("The previous workspace cannot be reloaded here")
    )
    monkeypatch.setattr(persistence_module, "_read_bounded_notes", read)
    for profile in ("music", "unknown", "../PRIVATE_PATH"):
        assert not case.owner.reload_unreadable_notes(profile)
    assert case.canvas.current_notes() == "Saved Art ideas"
    assert case.owner.profile_key == "art"
    read.assert_not_called()


@pytest.mark.parametrize("font_size", [13, 22])
def test_recheck_copy_and_button_fit_a_compact_notes_panel(
    unreadable_notes,
    qapp,
    font_size,
):
    case = unreadable_notes
    case.canvas.setStyleSheet(
        load_stylesheet() + f"\nQLabel, QPushButton {{ font-size: {font_size}px; }}"
    )
    qapp.processEvents()
    button = case.canvas._recheck_notes_button
    status = case.canvas._notes_save_status
    assert status.text() == status.accessibleDescription() == UNREADABLE_COPY
    assert button.text() == button.accessibleName() == "Recheck Saved Notes"
    assert "No files are changed" in button.accessibleDescription()
    assert case.canvas.width() == 280
    for widget in (button, status, case.canvas._notes):
        rect = QRect(widget.mapTo(case.canvas, QPoint()), widget.size())
        assert case.canvas.rect().contains(rect)
        assert widget.height() >= widget.fontMetrics().height()
    # Qt rounds the wrapped layout to integral device pixels.
    assert status.height() + 1 >= status.heightForWidth(status.width())
    assert button.width() >= button.fontMetrics().horizontalAdvance(button.text())
    assert button.geometry().bottom() < case.canvas._notes.geometry().top()
    assert not case.canvas._save_notes_button.isVisibleTo(case.canvas)


@pytest.fixture
def unreadable_controller(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(persistence_module, "_persistence_home", lambda: tmp_path)
    original = tmp_path / ".webjam_notes.md"
    original.write_bytes(b"\xffPRIVATE_ORIGINAL")
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Rehearsal notes",
    )
    window.setStyleSheet(load_stylesheet())
    app = ApplicationController(
        window,
        settings=AppSettings(
            config_file=str(tmp_path / "settings.json"),
            takes_directory=str(tmp_path / "takes"),
        ),
    )
    app.bridge.stop_jamulus = Mock(return_value=True)
    window.flash_message = Mock()
    window.resize(720, 560)
    window.show()
    window.session_canvas.show()
    qapp.processEvents()
    assert app._persistence.notes_save_state == "unreadable"
    yield SimpleNamespace(app=app, window=window, original=original)
    assert app.shutdown()
    window.close()
    window.deleteLater()
    app.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_visible_recheck_routes_through_controller_without_saving_or_switching_workspace(
    unreadable_controller,
    qapp,
    monkeypatch,
):
    case = unreadable_controller
    app, window = case.app, case.window
    canvas = window.session_canvas
    original_workspace = window.workspace_stack.currentWidget()
    case.original.write_text("Saved rehearsal ideas")
    writes = Mock(side_effect=AssertionError("Recheck must not start a write"))
    with monkeypatch.context() as patch:
        patch.setattr(persistence_module, "atomic_write_text", writes)
        patch.setattr(persistence_module, "write_notes_recovery", writes)
        patch.setattr(app, "_save_notes", writes)
        assert canvas._recheck_notes_button.isVisibleTo(window)
        assert not app._notes_save_timer.isActive()
        canvas._recheck_notes_button.click()
        qapp.processEvents()
        assert canvas.current_notes() == "Saved rehearsal ideas"
        assert not app._notes_save_timer.isActive()
        assert app._persistence.notes_save_state == "saved"
        assert not app._persistence.has_unsaved_notes
        assert window.workspace_stack.currentWidget() is original_workspace
        assert app._persistence.profile_key == "music"
        window.flash_message.assert_called_once_with(
            "Saved notes reopened on this computer.",
            ms=5000,
        )
        writes.assert_not_called()


def test_failed_controller_recheck_names_the_same_available_action(
    unreadable_controller,
):
    case = unreadable_controller
    canvas = case.window.session_canvas
    canvas._recheck_notes_button.click()
    case.window.flash_message.assert_called_once_with(
        "Saved notes are still unavailable. Check file access and choose Recheck Saved Notes again.",
        ms=7000,
    )
    assert canvas._recheck_notes_button.isVisibleTo(case.window)
    assert case.app._persistence.notes_save_state == "unreadable"
    assert not case.app._persistence.has_unsaved_notes


def test_controller_rejects_stale_workspace_and_shutdown_clicks(
    unreadable_controller, monkeypatch
):
    case = unreadable_controller
    reads = Mock(side_effect=AssertionError("Late recheck must not read"))
    monkeypatch.setattr(case.app._persistence, "reload_unreadable_notes", reads)
    case.app._recheck_saved_notes("art")
    case.app._shutdown_cleanup_pending = True
    try:
        case.window.session_canvas._recheck_notes_button.click()
    finally:
        case.app._shutdown_cleanup_pending = False
    case.app._shutdown_in_progress = True
    try:
        case.window.session_canvas._recheck_notes_button.click()
    finally:
        case.app._shutdown_in_progress = False
    reads.assert_not_called()
    case.window.flash_message.assert_not_called()


def test_unreadable_original_does_not_invent_retained_draft_attention(unreadable_notes):
    case = unreadable_notes
    assert case.canvas._notes_need_attention
    assert case.canvas._notes_attention_presentation[0] is False
    assert case.canvas._save_notes_button.isHidden()
    assert case.canvas._pulse.isHidden()
    before_guidance = case.canvas._guidance_next.text()
    case.original.write_text("Saved rehearsal ideas")
    assert case.owner.reload_unreadable_notes("music")
    assert not case.canvas._notes_need_attention
    assert case.canvas._notes_attention_presentation[0] is False
    assert case.canvas._guidance_next.text() == before_guidance
