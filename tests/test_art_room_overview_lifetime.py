"""Deferred focus scrolling belongs to the lifetime of its Art overview."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from shiboken6 import isValid

from core.art_room_overview import ArtRoomOverview
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.art_room_overview import ArtRoomOverviewWidget


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _settle(qapp):
    for _ in range(3):
        qapp.processEvents()


def _room_window():
    window = QWidget()
    layout = QVBoxLayout(window)
    panel = ArtRoomOverviewWidget(window)
    layout.addWidget(panel)
    panel.setStyleSheet(load_stylesheet())
    panel.set_overview(ArtRoomOverview(
        connection_detail=(
            "The room connection is confirmed. Each artist keeps their own tools open. "
        ) * 5,
        activity_detail=(
            "Keep making with your own tools. Conversation is optional. "
        ) * 5,
    ))
    window.resize(360, 480)
    return window, panel


def _delete(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
    assert not isValid(widget)


@pytest.mark.parametrize("delete_owner", ["overview", "window"])
def test_pending_focus_reveal_is_cancelled_when_its_widget_is_deleted(
    qapp, monkeypatch, delete_owner
):
    window, panel = _room_window()
    try:
        window.show()
        window.activateWindow()
        assert QTest.qWaitForWindowActive(window)
        _settle(qapp)
        panel.conversation_button().setFocus(Qt.FocusReason.TabFocusReason)
        _settle(qapp)
        callbacks = []
        # Record delivery without dereferencing a deleted C++ widget. The
        # old contextless timer delivered this callable after destruction.
        monkeypatch.setattr(
            panel, "_reveal_focused_action", lambda: callbacks.append(True)
        )
        panel._sync_density()
        assert callbacks == []
        _delete(panel if delete_owner == "overview" else window)
        assert not isValid(panel)
        _settle(qapp)
        assert callbacks == []
    finally:
        if isValid(window):
            _delete(window)
        _settle(qapp)


def test_deferred_reveal_keeps_an_already_focused_action_visible_after_resize(qapp):
    window, panel = _room_window()
    try:
        window.show()
        window.activateWindow()
        assert QTest.qWaitForWindowActive(window)
        _settle(qapp)
        button = panel.conversation_button()
        button.setFocus(Qt.FocusReason.TabFocusReason)
        _settle(qapp)
        assert button.hasFocus()
        window.resize(340, 220)
        panel.widget().layout().activate()
        panel._content.layout().activate()
        panel.verticalScrollBar().setValue(0)
        assert panel.verticalScrollBar().maximum() > 0
        before = QRect(button.mapTo(panel.viewport(), QPoint()), button.size())
        assert not panel.viewport().rect().contains(before)
        # The action already owns focus, so density changes must reveal it
        # after the queued layout without relying on another FocusIn event.
        panel._sync_density()
        _settle(qapp)
        assert button.hasFocus()
        after = QRect(button.mapTo(panel.viewport(), QPoint()), button.size())
        assert panel.viewport().rect().contains(after)
    finally:
        _delete(window)
        _settle(qapp)
