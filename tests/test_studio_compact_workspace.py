"""The embedded Studio keeps safety/actions fixed and scrolls complete editors."""
from __future__ import annotations

from pathlib import Path
import threading
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionButton

from core.take_player import PlaybackDeviceError
from tests.test_art_room_controller import controllers as _controllers_fixture
from tests.test_art_room_controller import qapp as _qapp_fixture
from tests.test_recording_studio import _schema2_studio_take
from tests.test_studio_demo_recovery_actions import failed_studio_save as _failed_fixture
from webjam_qt.theme import load_stylesheet

controllers = _controllers_fixture
qapp = _qapp_fixture
failed_studio_save = _failed_fixture


def _settle(qapp, count=12):
    for _ in range(count):
        qapp.processEvents()


def _rect(root, widget):
    return QRect(widget.mapTo(root, QPoint()), widget.size())


def _button_fits(button):
    option = QStyleOptionButton()
    option.initFrom(button)
    content = button.style().subElementRect(
        QStyle.SubElement.SE_PushButtonContents, option, button,
    )
    assert content.width() >= button.fontMetrics().horizontalAdvance(button.text()), button.text()
    assert content.height() >= button.fontMetrics().height(), button.text()


def _assert_persistent(studio):
    controls = [studio._setup_btn, studio._record_btn]
    if not studio._viewing_live:
        controls.append(studio._live_btn)
    if not studio._viewing_live:
        controls += [studio._play_btn, studio._stop_btn, studio._export_btn]
    widgets = controls + [studio._workspace_scroll, studio._hint]
    rectangles = []
    for widget in widgets:
        assert widget.isVisibleTo(studio)
        rect = _rect(studio, widget)
        assert studio.rect().contains(rect), widget.objectName()
        assert not any(rect.intersects(previous) for previous in rectangles), widget.objectName()
        rectangles.append(rect)
    for button in controls:
        _button_fits(button)
    assert studio._hint.height() >= studio._hint.heightForWidth(studio._hint.width())


@pytest.fixture
def loaded(controllers, qapp):
    app = controllers(hosting=True)
    window = app.window
    window.setStyleSheet(load_stylesheet())
    window.show()
    app._open_take_deck()
    studio = window.recording_studio
    _schema2_studio_take(Path(app.settings.takes_directory))
    studio.reload()
    studio._take_list.setCurrentRow(0)
    window.resize(760, 600)
    _settle(qapp)
    return app, window, studio


@pytest.mark.parametrize("font_size", [13, 22])
@pytest.mark.parametrize("notes_attention", [False, True])
def test_full_shell_keeps_actions_and_complete_scrolling_editors(
    loaded, qapp, font_size, notes_attention,
):
    app, window, studio = loaded
    studio.setStyleSheet(f"QLabel, QPushButton {{font-size:{font_size}px;}}")
    if notes_attention:
        window.session_canvas.set_notes_recovery_context("music", (("music", "permission_denied"),))
        window.session_canvas.set_notes_save_state("failed")
    _settle(qapp)
    assert window.size().toTuple() == (760, 600)
    assert studio.height() < 500
    assert window._notes_review_button.isVisibleTo(window) == notes_attention
    _assert_persistent(studio)
    content = studio._splitter
    rects = []
    for widget in (studio._studio_arrange, studio._track_scroll,
                   studio._arrange_toolbar, studio._comp_toolbar, studio._output_picker):
        rect = _rect(content, widget)
        assert content.rect().contains(rect), widget.objectName()
        assert not any(rect.intersects(other) for other in rects), widget.objectName()
        rects.append(rect)
    assert studio._studio_arrange.height() >= 150
    assert studio._track_scroll.height() >= 88
    scroll = studio._workspace_scroll
    assert scroll.verticalScrollBar().maximum() > 0
    for widget in (studio._studio_arrange, studio._track_scroll, studio._output_picker):
        scroll.ensureWidgetVisible(widget)
        _settle(qapp)
        assert _rect(scroll.viewport(), widget).intersects(scroll.viewport().rect())
        _assert_persistent(studio)
    assert studio.isVisibleTo(window)
    assert app.window.workspace_stack.currentWidget().isAncestorOf(studio)


@pytest.mark.parametrize("font_size", [13, 22])
@pytest.mark.parametrize("notes_attention", [False, True])
def test_save_failure_retry_stays_fixed_above_scrolled_workspace(
    failed_studio_save, qapp, font_size, notes_attention,
):
    rig = failed_studio_save
    rig.app.window.setStyleSheet(load_stylesheet())
    rig.studio.setStyleSheet(f"QLabel, QPushButton {{font-size:{font_size}px;}}")
    rig.app.window.resize(760, 600)
    if notes_attention:
        canvas = rig.app.window.session_canvas
        canvas.set_notes_recovery_context("music", (("music", "permission_denied"),))
        canvas.set_notes_save_state("failed")
    _settle(qapp)
    assert rig.app.window.size().toTuple() == (760, 600)
    assert rig.app.window._notes_review_button.isVisibleTo(rig.app.window) == notes_attention
    scroll = rig.studio._workspace_scroll
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    scroll.horizontalScrollBar().setValue(scroll.horizontalScrollBar().maximum())
    _settle(qapp)
    _assert_persistent(rig.studio)
    action = rig.app.window.session_hud._action
    assert action.text() == "Retry Save" and action.isEnabled()
    assert action.isVisibleTo(rig.app.window)
    QTest.mouseClick(action, Qt.MouseButton.LeftButton)
    assert not rig.studio._studio_controller.dirty
    assert not rig.studio._studio_persistence_failed
    assert all(path.read_bytes() == before for path, before in rig.media.items())
    rig.starts.assert_not_called()
    rig.meter.assert_not_called()
    rig.record.assert_not_called()


class _LayoutCounter(QObject):
    def __init__(self):
        super().__init__()
        self.count = 0

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.LayoutRequest:
            self.count += 1
        return False


def test_reflow_settles_and_preserves_document_selection_and_audio(loaded, qapp, monkeypatch):
    _, window, studio = loaded
    owner = studio._studio_controller
    before = (studio._current, owner.document, owner.generation, studio._take_list.currentRow())
    play, stop, flush = Mock(), Mock(), Mock(return_value=True)
    monkeypatch.setattr(studio._player, "play", play)
    monkeypatch.setattr(studio._player, "stop", stop)
    monkeypatch.setattr(studio, "_flush_studio_state", flush)
    counter = _LayoutCounter()
    studio.installEventFilter(counter)
    studio._splitter.installEventFilter(counter)
    widths = []
    for font_size, size in ((22, (760, 600)), (13, (1100, 800)), (13, (760, 600))):
        studio.setStyleSheet(f"QLabel, QPushButton {{font-size:{font_size}px;}}")
        window.resize(*size)
        _settle(qapp, 30)
        widths.append(studio._splitter.minimumWidth())
        count = counter.count
        _settle(qapp, 30)
        assert counter.count == count
    assert widths[-1] < widths[0]
    assert before == (studio._current, owner.document, owner.generation, studio._take_list.currentRow())
    play.assert_not_called()
    stop.assert_not_called()
    flush.assert_not_called()


def test_keyboard_focus_reveals_output_and_playback_failure_names_it(loaded, qapp):
    _, window, studio = loaded
    studio.setStyleSheet("QLabel, QPushButton {font-size:22px;}")
    _settle(qapp)
    window.activateWindow()
    studio._take_list.setFocus(Qt.FocusReason.TabFocusReason)
    for _ in range(12):
        if studio._output_picker.hasFocus():
            break
        QTest.keyClick(window.focusWidget(), Qt.Key.Key_Tab)
        _settle(qapp)
    assert studio._output_picker.hasFocus()
    viewport = studio._workspace_scroll.viewport()
    assert viewport.rect().contains(_rect(viewport, studio._output_picker))
    studio._workspace_scroll.verticalScrollBar().setValue(0)
    studio._handle_playback_error(PlaybackDeviceError("Private output detail"))
    _settle(qapp)
    assert viewport.rect().contains(_rect(viewport, studio._output_picker))
    assert "Playback output" in studio._hint.text()
    assert "Private" not in studio._hint.text()
    _assert_persistent(studio)


@pytest.mark.parametrize("font_size", [13, 22])
def test_live_stop_recording_survives_scroll_and_notes_attention(loaded, qapp, font_size):
    _, window, studio = loaded
    studio.setStyleSheet(f"QLabel, QPushButton {{font-size:{font_size}px;}}")
    studio.record_requested.disconnect()
    requested = QSignalSpy(studio.record_requested)
    studio.set_can_record(True)
    studio.set_recording_phase("recording")
    window.session_canvas.set_notes_recovery_context("music", (("music", "permission_denied"),))
    window.session_canvas.set_notes_save_state("failed")
    _settle(qapp)
    assert studio._record_btn.text() == "■ Stop Recording"
    assert studio._record_btn.isEnabled()
    _assert_persistent(studio)
    studio._record_btn.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(studio._record_btn, Qt.Key.Key_Space)
    assert requested.count() == 1
    studio.set_recording_phase("idle")


def test_export_busy_keeps_stop_playback_accessible(loaded, qapp, monkeypatch):
    _, window, studio = loaded
    started, release = threading.Event(), threading.Event()

    def hold_export(*args, **kwargs):
        started.set()
        assert release.wait(5)
        raise RuntimeError("Synthetic cancelled export")

    monkeypatch.setattr("webjam_qt.widgets.recording_studio.studio_export_supported", lambda: True)
    monkeypatch.setattr("webjam_qt.widgets.recording_studio.export_studio_arrangement", hold_export)
    stop = Mock()
    monkeypatch.setattr(studio._player, "stop", stop)
    studio.setStyleSheet("QLabel, QPushButton {font-size:22px;}")
    studio._export_tracks()
    try:
        assert started.wait(2)
        _settle(qapp)
        assert studio.export_in_progress
        assert studio._export_btn.text() == "Exporting…"
        assert not studio._export_btn.isEnabled()
        scroll = studio._workspace_scroll
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        _settle(qapp)
        _assert_persistent(studio)
        assert studio._stop_btn.isEnabled()
        studio._stop_btn.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(studio._stop_btn, Qt.Key.Key_Space)
        assert stop.called
        assert studio.export_in_progress
    finally:
        release.set()
        worker = studio._export_thread
        if worker is not None:
            worker.join(3)
        studio._drain_export_results()


def test_aligned_originals_fallback_label_fits_large_text(loaded, qapp, monkeypatch):
    _, _, studio = loaded
    monkeypatch.setattr("webjam_qt.widgets.recording_studio.studio_export_supported", lambda: False)
    studio._refresh_export_presentation()
    studio.setStyleSheet("QLabel, QPushButton {font-family:Verdana; font-size:22px;}")
    _settle(qapp)
    assert studio._export_btn.text() == "Export Aligned Originals"
    _assert_persistent(studio)


def test_native_failed_save_layout_settles_without_changing_retry_context(failed_studio_save, qapp):
    rig = failed_studio_save
    studio, owner = rig.studio, rig.studio._studio_controller
    before = (owner.document, owner.generation, owner.store_token, studio.studio_save_retry_context())
    counter = _LayoutCounter()
    studio.installEventFilter(counter)
    studio._splitter.installEventFilter(counter)
    for size, font in (((760, 600), 22), ((1000, 740), 13), ((760, 600), 13)):
        rig.app.window.resize(*size)
        studio.setStyleSheet(f"QLabel, QPushButton {{font-size:{font}px;}}")
        _settle(qapp, 30)
    observed = counter.count
    _settle(qapp, 80)
    assert counter.count == observed
    assert owner.document is before[0]
    assert (owner.generation, owner.store_token, studio.studio_save_retry_context()) == before[1:]
    assert owner.dirty and studio._studio_persistence_failed


def test_export_keeps_keyboard_focus_across_long_label_reflow(loaded, qapp, monkeypatch):
    _, window, studio = loaded
    monkeypatch.setattr("webjam_qt.widgets.recording_studio.studio_export_supported", lambda: False)
    studio._refresh_export_presentation()
    window.activateWindow()
    studio._export_btn.setFocus(Qt.FocusReason.TabFocusReason)
    _settle(qapp)
    assert studio._export_btn.hasFocus()
    parents = set()
    for font_size, stretch in ((22, 140), (13, 100), (22, 140)):
        studio.setStyleSheet(f"QLabel, QPushButton {{font-size:{font_size}px;}}")
        font = studio._export_btn.font()
        font.setStretch(stretch)
        studio._export_btn.setFont(font)
        _settle(qapp)
        assert studio._export_btn.hasFocus()
        _button_fits(studio._export_btn)
        parents.add(studio._export_btn.parentWidget())
    assert parents == {studio._transport_buttons, studio._transport_position}


def test_keyboard_focus_reveals_each_existing_workspace_control(loaded, qapp):
    _, window, studio = loaded
    studio.setStyleSheet('QLabel,QPushButton{font-size:22px;}')
    _settle(qapp)
    window.activateWindow()
    _settle(qapp)
    viewport = studio._workspace_scroll.viewport()
    owner = studio._studio_controller
    before = (owner.document, owner.generation, owner.store_token)
    studio._take_list.setFocus(Qt.FocusReason.TabFocusReason)
    for _ in range(30):
        focused = QApplication.focusWidget()
        if focused is studio._output_picker:
            break
        assert focused is not None
        QTest.keyClick(focused, Qt.Key.Key_Tab)
        _settle(qapp)
    assert studio._output_picker.hasFocus()
    output_center = studio._output_picker.mapTo(viewport, studio._output_picker.rect().center())
    assert viewport.rect().contains(output_center), ('real Tab output focus', output_center, viewport.rect())
    targets = (
        studio._output_picker, studio._master_gain, studio._take_list,
        studio._studio_arrange._canvas.viewport(), studio._lanes[1]._gain,
        studio._play_btn, studio._export_btn,
    )
    seen = set()
    for _ in range(80):
        focused = QApplication.focusWidget()
        assert focused is not None
        for control in targets:
            if control.hasFocus() or control.isAncestorOf(focused):
                seen.add(id(control))
                center = control.mapTo(viewport, control.rect().center())
                if studio._workspace_scroll.isAncestorOf(control) and control.height() <= viewport.height():
                    assert viewport.rect().contains(center), (control.accessibleName(), center, viewport.rect())
        if len(seen) == len(targets):
            break
        QTest.keyClick(focused, Qt.Key.Key_Tab)
        _settle(qapp)
    assert seen == {id(control) for control in targets}
    assert owner.document is before[0]
    assert owner.generation == before[1] and owner.store_token == before[2]



def test_queued_workspace_layout_cannot_restart_after_shutdown(loaded, qapp):
    _, _, studio = loaded
    observed = []
    studio._workspace_layout_timer.timeout.connect(lambda: observed.append('layout'))
    QApplication.postEvent(studio._splitter, QEvent(QEvent.Type.LayoutRequest))
    assert studio.shutdown()
    _settle(qapp, 50)
    assert not observed
    assert not studio._workspace_layout_timer.isActive()
