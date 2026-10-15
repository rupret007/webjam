"""Recording Setup keeps recovery controls reachable with real folder labels."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget
from shiboken6 import isValid

from core.settings import AppSettings
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.recording_setup import RecordingSetupDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("size", [(620, 440), (620, 620)])
@pytest.mark.parametrize("stretch", [100, 125], ids=["default-font", "125-percent-font"])
@pytest.mark.parametrize(
    "takes_path",
    [
        "/Users/maya/Music/WebJam Takes",
        "/Users/maya/Music/WebJam Takes/Autumn rehearsal/Session originals and arrangement drafts <archive>/Evening take",
    ],
    ids=["ordinary-folder", "long-folder"],
)
def test_setup_recovery_controls_fit_at_compact_sizes(
    qapp, monkeypatch, size, stretch, takes_path,
):
    for name in ("WEBJAM_AUDIO_SAMPLERATE", "WEBJAM_AUDIO_BLOCKSIZE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "webjam_qt.windows.recording_setup.list_input_devices",
        lambda: [{"name": "Audio interface", "channels": 2, "index": 7}],
    )
    settings = AppSettings(
        takes_directory=takes_path,
        audio_samplerate=44_100,
        audio_blocksize=256,
        audio_input_device_index=7,
        local_capture_enabled=True,
    )
    dialog = RecordingSetupDialog(
        settings,
        format_change_guard=lambda: (
            "Close Recording Setup, end or leave the session, then reopen Setup "
            "to change the format. Your Notes stay available."
        ),
    )
    dialog.setStyleSheet(load_stylesheet())
    try:
        dialog.show()
        qapp.processEvents()
        # Stretch changes actual glyph widths even when QSS owns pixel size.
        for widget in dialog.findChildren(QWidget):
            font = QFont(widget.font())
            font.setStretch(stretch)
            widget.setFont(font)
        dialog.resize(*size)
        qapp.processEvents()
        assert (dialog.width(), dialog.height()) == size
        scroll = dialog._scroll
        viewport = scroll.viewport()
        body = scroll.widget()
        assert body.width() <= viewport.width(), (
            body.width(), viewport.width(), dialog.size(), dialog._folder.text(),
        )
        assert dialog._folder.text() == "Takes: " + takes_path
        for control in (
            dialog._capture, dialog._repair_format, dialog._input,
            dialog._edit_tracks_btn, dialog._show_folder_button,
        ):
            scroll.ensureWidgetVisible(control)
            qapp.processEvents()
            rect = control.rect()
            point = control.mapTo(viewport, QPoint(0, 0))
            assert 0 <= point.x()
            assert point.x() + rect.width() <= viewport.width()
            assert 0 <= point.y()
            assert point.y() + rect.height() <= viewport.height()
        for checkbox in (dialog._capture, dialog._repair_format):
            assert checkbox.width() >= checkbox.sizeHint().width()
        for label in dialog.findChildren(QLabel):
            if label.wordWrap() and label.isVisibleTo(dialog):
                assert label.height() >= label.heightForWidth(label.width()), label.objectName()
        save = next(
            button for button in dialog.findChildren(QPushButton)
            if button.text() == "Save Recording Setup"
        )
        assert save.isVisibleTo(dialog)
        assert save.geometry().bottom() <= dialog.contentsRect().bottom()
        assert dialog._capture.isEnabled()
        assert not dialog._repair_format.isEnabled()
        assert "Close Recording Setup" in dialog._repair_format.accessibleDescription()
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()
        assert not isValid(dialog)


@pytest.mark.parametrize("size", [(620, 360), (620, 440), (620, 620)])
@pytest.mark.parametrize("stretch", [100, 125], ids=["default-font", "125-percent-font"])
def test_first_session_change_rejection_is_fully_visible(
    qapp, monkeypatch, size, stretch,
):
    for name in ("WEBJAM_AUDIO_SAMPLERATE", "WEBJAM_AUDIO_BLOCKSIZE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "webjam_qt.windows.recording_setup.list_input_devices",
        lambda: [{"name": "Audio interface", "channels": 2, "index": 7}],
    )
    saved = []
    monkeypatch.setattr(
        "webjam_qt.windows.recording_setup.save_settings", saved.append,
    )
    state = {"reason": ""}
    dialog = RecordingSetupDialog(
        AppSettings(
            takes_directory="/Users/maya/Music/WebJam Takes",
            audio_samplerate=44_100,
            audio_blocksize=256,
            audio_input_device_index=7,
            local_capture_enabled=True,
        ),
        session_change_guard=lambda: state["reason"],
    )
    dialog.setStyleSheet(load_stylesheet())
    try:
        dialog.show()
        dialog.activateWindow()
        qapp.processEvents()
        for widget in dialog.findChildren(QWidget):
            font = QFont(widget.font())
            font.setStretch(stretch)
            widget.setFont(font)
        dialog.resize(*size)
        dialog._repair_format.setChecked(True)
        save = next(
            button for button in dialog.findChildren(QPushButton)
            if button.text() == "Save Recording Setup"
        )
        save.setFocus()
        qapp.processEvents()
        assert (dialog.width(), dialog.height()) == size
        assert save.hasFocus()
        assert dialog._error.isHidden()
        state["reason"] = (
            "The session setup changed. Close and reopen Recording Setup to review it."
        )

        # One keyboard Save must reveal all guidance without a second Save
        # or any test-driven scroll/layout repair.
        QTest.keyClick(save, Qt.Key.Key_Return)
        qapp.processEvents()

        assert not saved
        assert dialog.isVisible()
        assert dialog.result() == dialog.DialogCode.Rejected
        assert dialog._repair_format.isChecked()
        assert dialog._error.isVisibleTo(dialog)
        assert dialog._error.text() == state["reason"]
        assert dialog._error.accessibleDescription() == state["reason"]
        viewport = dialog._scroll.viewport()
        error_rect = dialog._error.rect().translated(
            dialog._error.mapTo(viewport, QPoint(0, 0)),
        )
        assert viewport.rect().contains(error_rect), (viewport.rect(), error_rect)
        assert dialog._error.height() >= dialog._error.heightForWidth(dialog._error.width())
        assert save.hasFocus()
        QTest.keyClick(save, Qt.Key.Key_Escape)
        assert not dialog.isVisible()
    finally:
        dialog.close()
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()
        assert not isValid(dialog)
