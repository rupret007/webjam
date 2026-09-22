"""Recording Setup keeps recovery controls reachable with real folder labels."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, QCoreApplication, QEvent
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
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
