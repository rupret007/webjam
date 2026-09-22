"""Compact Music Notes retain complete actions and explicitly opened details."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QLabel, QStyle, QStyleOptionButton

from core.creative_modes import get_creator_profile_by_key_or_default
from tests.test_art_room_controller import controllers as _controllers_fixture
from tests.test_art_room_controller import qapp as _qapp_fixture
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.session_canvas import SessionCanvas

controllers = _controllers_fixture
qapp = _qapp_fixture


def _settle(qapp, count=10):
    for _ in range(count):
        qapp.processEvents()


def _style(size):
    available = set(QFontDatabase.families())
    family = next(name for name in (
        "Verdana", "DejaVu Sans", "Noto Sans", "Liberation Sans",
    ) if name in available)
    return load_stylesheet() + (
        f'\nQLabel, QPushButton {{ font-family:"{family}"; font-size:{size}px; }}'
    )


def _button_text_fits(button):
    option = QStyleOptionButton()
    option.initFrom(button)
    contents = button.style().subElementRect(
        QStyle.SubElement.SE_PushButtonContents, option, button,
    )
    assert contents.width() >= button.fontMetrics().horizontalAdvance(button.text())


def _inside(panel, widget):
    assert panel.rect().contains(QRect(widget.mapTo(panel, QPoint()), widget.size()))


@pytest.mark.parametrize("font_size", [13, 22])
@pytest.mark.parametrize("size", [(760, 600), (1000, 740)])
def test_actual_music_notes_keep_tools_and_editor_whole(controllers, qapp, font_size, size):
    app = controllers(hosting=True)
    window, panel = app.window, app.window.session_canvas
    window.setStyleSheet(load_stylesheet())
    panel.setStyleSheet(_style(font_size))
    window.show()
    app._on_rail_view_changed("canvas")
    window.resize(*size)
    _settle(qapp)
    window.center_splitter.setSizes([size[0] - 280, 280])
    _settle(qapp)
    assert window.size().toTuple() == size
    assert panel.width() == 280
    for button in (*panel._normal_notes_buttons, panel._export_button):
        assert button.isVisibleTo(window)
        _inside(panel, button)
        _button_text_fits(button)
    _inside(panel, panel._notes)
    assert panel._notes.height() >= panel._notes.minimumSizeHint().height()
    assert panel._music_details_button.isVisibleTo(window)
    assert not panel._music_details_button.isChecked()
    assert panel._music_readout_scroll.isHidden()
    # The existing operational HUD stays the first-screen source of guidance.
    assert window.session_hud.isVisibleTo(window)


@pytest.mark.parametrize("font_size", [13, 22])
def test_opened_details_keep_full_wrapped_copy_reachable(controllers, qapp, font_size):
    app = controllers(hosting=True)
    window, panel = app.window, app.window.session_canvas
    window.setStyleSheet(load_stylesheet())
    panel.setStyleSheet(_style(font_size))
    window.show()
    app._on_rail_view_changed("canvas")
    window.resize(760, 600)
    _settle(qapp)
    window.center_splitter.setSizes([480, 280])
    panel.set_notes("Keep this local draft")
    before_guidance = panel._current_guidance
    before_pulse = panel._current_pulse
    panel._music_details_button.click()
    _settle(qapp)
    scroll = panel._music_readout_scroll
    assert scroll.isVisibleTo(window)
    assert panel._notes.isVisibleTo(window)
    for readout in (panel._guidance, panel._pulse):
        assert readout.isVisibleTo(window)
        for label in readout.findChildren(QLabel):
            if label.isVisibleTo(readout):
                assert label.height() >= label.heightForWidth(label.width()), label.text()
    bar = scroll.verticalScrollBar()
    assert bar.maximum() > 0
    bar.setValue(bar.maximum())
    _settle(qapp)
    assert bar.value() == bar.maximum()
    assert panel._current_guidance is before_guidance
    assert panel._current_pulse is before_pulse
    panel._music_details_button.click()
    _settle(qapp)
    assert scroll.isHidden()
    assert panel.current_notes() == "Keep this local draft"
    _inside(panel, panel._notes)


class _LayoutCounter(QObject):
    def __init__(self):
        super().__init__()
        self.count = 0

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.LayoutRequest:
            self.count += 1
        return False


def test_music_layout_settles_after_font_width_and_details_changes(qapp):
    panel = SessionCanvas()
    counter = _LayoutCounter()
    panel.installEventFilter(counter)
    panel.setStyleSheet(_style(22))
    panel.resize(280, 560)
    panel.show()
    try:
        for expanded in (False, True, False):
            panel._music_details_button.setChecked(expanded)
            _settle(qapp, 30)
            count = counter.count
            _settle(qapp, 30)
            assert counter.count == count
    finally:
        panel.close()
        panel.deleteLater()
        _settle(qapp)


def test_export_keeps_keyboard_focus_when_tools_reflow(qapp):
    panel = SessionCanvas()
    panel.setStyleSheet(_style(22))
    panel.resize(700, 560)
    panel.show()
    panel.activateWindow()
    _settle(qapp)
    try:
        panel._export_button.setFocus(Qt.FocusReason.TabFocusReason)
        _settle(qapp)
        assert panel._export_button.hasFocus()
        for width in (280, 700, 280):
            panel.resize(width, 560)
            _settle(qapp)
            assert panel._export_button.hasFocus()
            assert panel._music_export_row.isVisibleTo(panel) == (width == 280)
            _button_text_fits(panel._export_button)
    finally:
        panel.close()
        panel.deleteLater()
        _settle(qapp)


def test_art_retains_existing_readouts_and_music_details_selection(qapp):
    panel = SessionCanvas()
    panel.setStyleSheet(load_stylesheet())
    panel.resize(400, 650)
    panel.show()
    _settle(qapp)
    try:
        panel.set_notes("Keep the local draft across profiles")
        panel._music_details_button.setChecked(True)
        for profile in ("art", "music", "art"):
            panel.set_creator_profile(get_creator_profile_by_key_or_default(profile))
            _settle(qapp)
            art = profile == "art"
            assert panel._music_details_button.isHidden() == art
            assert panel._music_readout_scroll.isHidden() == art
            assert panel._guidance.isVisibleTo(panel)
            assert panel._pulse.isVisibleTo(panel)
            assert panel.current_notes() == "Keep the local draft across profiles"
    finally:
        panel.close()
        panel.deleteLater()
        _settle(qapp)


@pytest.mark.parametrize("font_size", [13, 22])
@pytest.mark.parametrize("expanded", [False, True])
@pytest.mark.parametrize("failure", ["permission", "unreadable"])
def test_actual_recovery_prioritizes_copy_editor_and_actions(
    controllers, qapp, monkeypatch, tmp_path, font_size, expanded, failure,
):
    import errno
    from unittest.mock import Mock
    from webjam_qt.controllers import session_persistence as persistence

    monkeypatch.setattr(persistence, "_persistence_home", lambda: tmp_path)
    original = tmp_path / ".webjam_notes.md"
    original.write_bytes(b"\xfforiginal" if failure == "unreadable" else b"Saved notes")
    app = controllers(hosting=True)
    window, panel = app.window, app.window.session_canvas
    window.setStyleSheet(load_stylesheet())
    panel.setStyleSheet(_style(font_size))
    window.show()
    app._on_rail_view_changed("canvas")
    window.resize(760, 600)
    _settle(qapp)
    window.center_splitter.setSizes([480, 280])
    panel._music_details_button.setChecked(expanded)
    if failure == "permission":
        panel.edit_notes("Keep this retained draft")
        with monkeypatch.context() as patch:
            patch.setattr(persistence, "atomic_write_text", Mock(
                side_effect=PermissionError(errno.EACCES, "Private failure"),
            ))
            assert not app._save_notes()
    _settle(qapp)
    assert window.size().toTuple() == (760, 600)
    assert panel.width() == 280
    assert panel.height() < 500
    assert panel._music_details_button.isHidden()
    assert panel._music_readout_scroll.isHidden()
    assert panel._music_details_button.isChecked() == expanded
    widgets = [panel._notes_save_status, panel._notes, panel._chat_input]
    widgets += [button for button in (
        panel._save_notes_button, panel._recheck_notes_button, panel._export_button,
    ) if button.isVisibleTo(panel)]
    rects = []
    for widget in widgets:
        _inside(panel, widget)
        rect = QRect(widget.mapTo(panel, QPoint()), widget.size())
        assert not any(rect.intersects(previous) for previous in rects)
        rects.append(rect)
    status = panel._notes_save_status
    assert status.height() >= status.heightForWidth(status.width())
    assert panel._notes.height() >= 2 * panel._notes.fontMetrics().height()
    for button in (panel._save_notes_button, panel._recheck_notes_button):
        if button.isVisibleTo(panel):
            _button_text_fits(button)
    if failure == "permission":
        assert app._persistence.unsaved_notes == (("music", "Keep this retained draft"),)
        assert app._save_notes()
    else:
        original.write_text("Saved original is available again")
        assert app._persistence.reload_unreadable_notes("music")
    _settle(qapp)
    assert panel._music_details_button.isVisibleTo(panel)
    assert panel._music_details_button.isChecked() == expanded
    assert panel._music_readout_scroll.isVisibleTo(panel) == expanded


def test_details_and_profile_round_trip_preserve_editor_and_recovery(qapp):
    canvas = SessionCanvas()
    canvas.setStyleSheet(load_stylesheet())
    canvas.resize(280, 560)
    canvas.show()
    canvas.activateWindow()
    _settle(qapp)
    try:
        canvas.set_notes('Local private draft')
        editor = canvas._notes
        document = editor.document()
        editor.moveCursor(editor.textCursor().MoveOperation.End)
        editor.insertPlainText(' retained')
        cursor = editor.textCursor()
        cursor.setPosition(3)
        cursor.setPosition(8, cursor.MoveMode.KeepAnchor)
        editor.setTextCursor(cursor)
        canvas._chat_input.setText('Unsent chat stays local')
        canvas.set_notes_save_state('failed')
        editor.setFocus(Qt.FocusReason.TabFocusReason)
        before = (canvas.current_notes(), cursor.position(), cursor.anchor(), document.isUndoAvailable())
        writes = []
        saves = []
        chats = []
        canvas.notes_changed.connect(writes.append)
        canvas.save_notes_requested.connect(lambda: saves.append(True))
        canvas.chat_submitted.connect(chats.append)
        for expanded in (True, False, True):
            canvas._music_details_button.setChecked(expanded)
            _settle(qapp)
            assert editor.hasFocus()
        canvas.set_creator_profile(get_creator_profile_by_key_or_default('art'))
        _settle(qapp)
        assert canvas._guidance.parentWidget() is canvas
        assert canvas._pulse.parentWidget() is canvas
        assert canvas.layout().indexOf(canvas._guidance) < canvas.layout().indexOf(canvas._pulse)
        assert not canvas._music_details_button.isVisibleTo(canvas)
        assert not canvas._music_readout_scroll.isVisibleTo(canvas)
        assert canvas._save_notes_button.isVisibleTo(canvas)
        assert editor.hasFocus()
        canvas.set_creator_profile(get_creator_profile_by_key_or_default('music'))
        _settle(qapp)
        assert canvas._music_details_button.isChecked()
        assert canvas._guidance.parentWidget() is canvas._music_readouts
        assert canvas._save_notes_button.isVisibleTo(canvas)
        assert editor.hasFocus()
        current = editor.textCursor()
        assert editor.document() is document
        assert before == (canvas.current_notes(), current.position(), current.anchor(), document.isUndoAvailable())
        assert canvas._chat_input.text() == 'Unsent chat stays local'
        assert canvas._notes_save_state == 'failed'
        assert not writes and not saves and not chats
    finally:
        canvas.close()
        canvas.deleteLater()
        _settle(qapp)


def test_keyboard_opens_scrolls_and_closes_session_details(qapp):
    from PySide6.QtTest import QTest

    panel = SessionCanvas()
    panel.setStyleSheet(_style(22))
    panel.resize(280, 560)
    panel.show()
    panel.activateWindow()
    _settle(qapp)
    try:
        button = panel._music_details_button
        button.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(button, Qt.Key.Key_Space)
        _settle(qapp)
        scroll = panel._music_readout_scroll
        assert scroll.isVisibleTo(panel)
        QTest.keyClick(button, Qt.Key.Key_Tab)
        _settle(qapp)
        assert scroll.hasFocus()
        bar = scroll.verticalScrollBar()
        assert bar.maximum() > 0
        QTest.keyClick(scroll, Qt.Key.Key_PageDown)
        _settle(qapp)
        assert bar.value() > 0
        QTest.keyClick(scroll, Qt.Key.Key_Backtab)
        assert button.hasFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
        _settle(qapp)
        assert scroll.isHidden()
        assert button.hasFocus()
    finally:
        panel.close()
        panel.deleteLater()
        _settle(qapp)
