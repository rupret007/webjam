"""A retained draft must not hide the active workspace's read-only recovery."""

from types import SimpleNamespace
import errno
from unittest.mock import Mock
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect
from PySide6.QtWidgets import QApplication
from core.creative_modes import get_creator_profile_by_key_or_default
from webjam_qt.controllers import session_persistence as module
from webjam_qt.controllers.session_persistence import SessionPersistence
from webjam_qt.widgets.session_canvas import SessionCanvas
from webjam_qt.theme import load_stylesheet
from tests.test_notes_unreadable_recheck import (
    unreadable_controller as _unreadable_controller_fixture,
)

from tests.test_notes_unreadable_recheck import (
    unreadable_notes as _unreadable_notes_fixture,
)

unreadable_controller = _unreadable_controller_fixture
unreadable_notes = _unreadable_notes_fixture


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def combined(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(module, "_persistence_home", lambda: tmp_path)
    music = tmp_path / ".webjam_notes.md"
    art = tmp_path / ".webjam_notes.art.md"
    music.write_text("Saved Music")
    art.write_bytes(b"\xffPRIVATE_SAVED_ART")
    canvas = SessionCanvas()
    canvas.setStyleSheet(load_stylesheet())
    owner = SessionPersistence(SimpleNamespace(), canvas)
    canvas.notes_changed.connect(owner.notes_changed)
    owner._load_notes_only()
    write = module.atomic_write_text

    def fail_music(path, *args, **kwargs):
        if path == music:
            raise OSError(errno.EACCES, "PRIVATE_SAVE_ERROR")
        return write(path, *args, **kwargs)

    monkeypatch.setattr(module, "atomic_write_text", fail_music)
    canvas.edit_notes("Retained Music draft")
    assert not owner._save_notes_only()
    owner.switch_profile_key("art")
    canvas.set_creator_profile(get_creator_profile_by_key_or_default("art"))
    canvas.resize(280, 560)
    canvas.show()
    qapp.processEvents()
    yield SimpleNamespace(
        owner=owner,
        canvas=canvas,
        music=music,
        art=art,
        journal=tmp_path / ".webjam_notes.recovery.json",
    )
    canvas.close()
    canvas.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.mark.parametrize("font_size", [13, 22])
def test_hidden_music_draft_does_not_hide_current_art_recheck(
    combined, qapp, font_size
):
    case = combined
    case.canvas.setStyleSheet(
        load_stylesheet() + f"\nQLabel, QPushButton {{ font-size: {font_size}px; }}"
    )
    qapp.processEvents()
    assert case.owner.profile_key == "art"
    assert case.owner.unsaved_notes == (("music", "Retained Music draft"),)
    assert case.canvas.current_notes() == ""
    case.art.write_text("Saved Art after access is fixed")
    print("STATUS:", case.canvas._notes_save_status.text())
    assert case.canvas._recheck_notes_button.isVisibleTo(case.canvas), (
        "The current empty Art original can be safely rechecked while Music remains unsaved."
    )
    assert case.canvas._save_notes_button.isVisibleTo(case.canvas)
    status = case.canvas._notes_save_status
    assert "Art" in status.text() and "Recheck Saved Notes" in status.text()
    assert "Music" in status.text() and "Save Notes" in status.text()
    assert "Music" in case.canvas._notes_attention_presentation[1]
    for widget in (
        status,
        case.canvas._save_notes_button,
        case.canvas._recheck_notes_button,
        case.canvas._notes,
    ):
        rect = QRect(widget.mapTo(case.canvas, QPoint()), widget.size())
        assert case.canvas.rect().contains(rect)
        assert widget.height() >= widget.fontMetrics().height()
    assert status.height() + 1 >= status.heightForWidth(status.width())


def test_recheck_can_preserve_the_other_workspace_exact_draft_and_checkpoint(
    combined, monkeypatch
):
    case = combined
    case.art.write_text("Saved Art after access is fixed")
    journal = case.journal.read_bytes()
    writes = Mock(side_effect=AssertionError("No writes during original recheck"))
    monkeypatch.setattr(module, "atomic_write_text", writes)
    monkeypatch.setattr(module, "write_notes_recovery", writes)
    assert case.owner.reload_unreadable_notes("art")
    assert case.canvas.current_notes() == "Saved Art after access is fixed"
    assert case.owner.unsaved_notes == (("music", "Retained Music draft"),)
    assert case.music.read_text() == "Saved Music"
    assert case.journal.read_bytes() == journal
    writes.assert_not_called()


@pytest.mark.parametrize("repair", [False, True])
def test_controller_can_recheck_music_while_art_draft_stays_retained(
    unreadable_controller,
    tmp_path,
    monkeypatch,
    repair,
):
    case = unreadable_controller
    app, canvas = case.app, case.window.session_canvas
    owner = app._persistence
    art = tmp_path / ".webjam_notes.art.md"
    art.write_text("Saved Art")
    owner.switch_profile_key("art")
    write = module.atomic_write_text

    def fail_art(path, *args, **kwargs):
        if path == art:
            raise OSError(errno.EACCES, "PRIVATE_ART_WRITE_ERROR")
        return write(path, *args, **kwargs)

    monkeypatch.setattr(module, "atomic_write_text", fail_art)
    canvas.edit_notes("Retained Art draft")
    app._notes_save_timer.stop()
    assert not owner._save_notes_only()
    owner.switch_profile_key("music")
    assert owner.notes_save_state == "failed"
    journal = tmp_path / ".webjam_notes.recovery.json"
    before_journal = journal.read_bytes()
    before_music = case.original.read_bytes()
    case.window.flash_message.reset_mock()
    try:
        assert canvas._recheck_notes_button.isVisibleTo(case.window)
        if repair:
            case.original.write_text("Saved Music after repair")
        writes = Mock(side_effect=AssertionError("Recheck must not attempt any save"))
        with monkeypatch.context() as patch:
            patch.setattr(module, "atomic_write_text", writes)
            patch.setattr(module, "write_notes_recovery", writes)
            canvas._recheck_notes_button.click()
        assert owner.unsaved_notes == (("art", "Retained Art draft"),)
        assert art.read_text() == "Saved Art"
        assert journal.read_bytes() == before_journal
        assert not app._notes_save_timer.isActive()
        if repair:
            assert canvas.current_notes() == "Saved Music after repair"
            assert not canvas._recheck_notes_button.isVisibleTo(case.window)
            case.window.flash_message.assert_called_once_with(
                "Saved notes reopened on this computer.", ms=5000
            )
        else:
            assert canvas.current_notes() == ""
            assert case.original.read_bytes() == before_music
            assert canvas._recheck_notes_button.isVisibleTo(case.window)
            case.window.flash_message.assert_called_once_with(
                "Saved notes are still unavailable. Check file access and choose Recheck Saved Notes again.",
                ms=7000,
            )
    finally:
        assert owner.export_pending_notes(
            "art", "Retained Art draft", str(tmp_path / "art-copy.md")
        )


def test_external_original_replacement_after_recheck_is_still_protected_on_next_save(
    combined, monkeypatch
):
    case = combined
    case.art.write_text("First readable original")
    read = module._read_bounded_notes

    def read_then_replace(path):
        result = read(path)
        if path == case.art:
            case.art.write_text("External replacement after read")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(module, "_read_bounded_notes", read_then_replace)
        assert case.owner.reload_unreadable_notes("art")
    assert case.canvas.current_notes() == "First readable original"
    case.canvas.edit_notes("My next Art draft")
    assert not case.owner._save_notes_only()
    assert case.owner.notes_recovery_state("art") == "recovery_conflict"
    assert case.art.read_text() == "External replacement after read"
    assert dict(case.owner.unsaved_notes)["art"] == "My next Art draft"


@pytest.mark.parametrize(
    "reason",
    [
        "failed",
        "too_large",
        "protected_original",
        "disk_full",
        "permission_denied",
        "read_only",
        "recovered",
        "recovery_conflict",
    ],
)
def test_combined_copy_names_each_action_once(combined, reason):
    canvas = combined.canvas
    canvas.set_notes_recovery_context("art", (("music", reason),))
    message = canvas._notes_save_status.text().lower()
    assert message.count("choose save notes") == 1
    assert message.count("choose recheck saved notes") == 1


def test_clearing_an_unattempted_draft_keeps_unreadable_original_copy(unreadable_notes):
    case = unreadable_notes
    case.canvas.edit_notes("A draft I decided not to keep")
    case.canvas.edit_notes("")
    assert not case.owner.has_unsaved_notes
    assert case.canvas._recheck_notes_button.isVisibleTo(case.canvas)
    assert case.canvas._notes_save_status.isVisibleTo(case.canvas)
    assert "Recheck Saved Notes" in case.canvas._notes_save_status.text()
    assert "Saving notes" not in case.canvas._notes_save_status.text()
