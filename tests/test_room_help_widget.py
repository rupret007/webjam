from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from webjam_qt.widgets.room_help import RoomHelpPanel  # noqa: E402
from webjam_qt.widgets.session_canvas import SessionCanvas  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def test_preview_starts_hidden_and_unavailable(qapp):
    panel = RoomHelpPanel()
    assert panel.isHidden()
    assert not panel._input.isEnabled()
    assert not panel._send.isEnabled()


def test_submit_keeps_draft_until_presenter_accepts(qapp):
    panel = RoomHelpPanel()
    panel.set_available(True, "Secure peer connected")
    emitted = []
    panel.submitted.connect(emitted.append)
    panel._input.setText("Can you hear me?")
    QTest.keyClick(panel._input, Qt.Key.Key_Return)
    assert emitted == ["Can you hear me?"]
    assert panel.draft_text() == "Can you hear me?"
    assert panel._messages.toPlainText() == ""
    panel.clear_draft()
    assert panel.draft_text() == ""


def test_pending_and_unavailable_cannot_send(qapp):
    panel = RoomHelpPanel()
    panel.set_available(True, "Secure peer connected")
    panel._input.setText("hello")
    emitted = []
    panel.submitted.connect(emitted.append)
    panel.set_pending(True)
    panel._submit()
    assert not panel._send.isEnabled()
    assert not panel._input.isEnabled()
    panel.set_pending(False)
    panel.set_available(False, "Peer disconnected")
    panel._submit()
    assert emitted == []


def test_plain_text_is_bounded_and_not_copied_to_local_notes(qapp):
    canvas = SessionCanvas()
    canvas.set_notes("Local notes remain unchanged")
    panel = RoomHelpPanel()
    panel.set_entries(
        [("Peer", f"message-{index}", "Received") for index in range(45)]
    )
    visible = panel._messages.toPlainText()
    assert "message-0\n" not in visible
    assert "message-44" in visible
    panel.set_entries([("Peer", "<b>literal text</b>", "Received")])
    assert "<b>literal text</b>" in panel._messages.toPlainText()
    assert canvas.current_notes() == "Local notes remain unchanged"


def test_session_clear_erases_messages_and_unsent_text(qapp):
    panel = RoomHelpPanel()
    panel.set_available(True, "Secure peer connected")
    panel.set_entries([("Peer", "fixture message", "Received")])
    panel._input.setText("unsent fixture")
    panel.set_pending(True)
    panel.clear_session()
    assert panel.draft_text() == ""
    assert panel._messages.toPlainText() == ""
    assert not panel._send.isEnabled()
    assert not panel._input.isEnabled()


def test_panel_composer_fits_existing_280px_notes_rail(qapp):
    panel = RoomHelpPanel()
    panel.set_available(True, "Secure peer connected · messages are temporary")
    panel.resize(280, 300)
    panel.show()
    qapp.processEvents()
    assert panel._input.geometry().right() < panel.width()
    assert panel._send.geometry().right() < panel.width()
    assert panel._input.width() > 80
    panel.close()


def test_undo_cannot_resurrect_an_old_room_draft(qapp):
    panel = RoomHelpPanel()
    panel.set_available(True, "Old peer")
    QTest.keyClicks(panel._input, "private old room draft")
    panel.clear_session()
    panel.set_available(True, "New peer")
    assert not panel._input.isUndoAvailable()
    panel._input.undo()
    assert panel.draft_text() == ""


def test_accepted_draft_cannot_return_through_undo(qapp):
    panel = RoomHelpPanel()
    panel.set_available(True, "Secure peer")
    QTest.keyClicks(panel._input, "accepted fixture message")
    panel.clear_draft()
    assert not panel._input.isUndoAvailable()
    panel._input.undo()
    assert panel.draft_text() == ""
