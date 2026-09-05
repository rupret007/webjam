"""Real themed layout/keyboard gates for the opt-in temporary help surface."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QPushButton,
    QStyle,
    QStyleOptionButton,
)

from core.creative_modes import get_creator_profile_by_key  # noqa: E402
from webjam_qt.controllers.room_help import SEND_FAILED_STATUS  # noqa: E402
from webjam_qt.theme import load_stylesheet  # noqa: E402
from webjam_qt.theme.tokens import Space  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous_stylesheet = app.styleSheet()
    previous_font = app.font()
    font_ids = []
    fonts = Path(__file__).resolve().parents[1] / "webjam_qt" / "theme" / "fonts"
    for path in sorted(fonts.glob("Inter-*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            font_ids.append(font_id)
    font = QFont("Inter") if "Inter" in QFontDatabase.families() else QFont(previous_font)
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(load_stylesheet())
    try:
        yield app
    finally:
        app.setStyleSheet(previous_stylesheet)
        app.setFont(previous_font)
        for font_id in font_ids:
            QFontDatabase.removeApplicationFont(font_id)


@pytest.fixture
def window(qapp):
    window = ConductorWindow(
        mode_entries=[("music_jam", "Music jam")],
        initial_mode_key="music_jam",
        initial_title="Offline layout fixture",
    )
    window.set_creator_profile(get_creator_profile_by_key("music"))
    window.resize(720, 560)
    window.show()
    _settle(qapp)
    try:
        yield window
    finally:
        window.session_strip._record_clock.stop()
        window.session_strip.stop_session_clock()
        window._room_help_dialog.close()
        window.close()
        window.deleteLater()
        _settle(qapp)


def _settle(app):
    # Show/resize and stylesheet repolish both enqueue layout requests.
    for _ in range(3):
        app.processEvents()


def _active_controls(window, *, profile="music", phase="recording", preview=True):
    window.set_creator_profile(get_creator_profile_by_key(profile))
    strip = window.session_strip
    strip.set_invite_available(True)
    strip.set_audio_state("End Session")
    strip.set_tools_enabled(True)
    strip.set_recording_available(True)
    if profile == "music":
        strip.set_recording_phase(phase)
    window.set_room_help_enabled(preview)
    window.room_help.setVisible(preview)


def _assert_readable_inside_bar(window, widgets):
    bar = window.session_controls
    previous = None
    for widget in widgets:
        assert widget.isVisible(), widget.accessibleName()
        rect = widget.geometry()
        text = widget.text()
        assert bar.rect().contains(rect), (text, rect, bar.rect())
        assert rect.width() >= widget.minimumSizeHint().width(), (
            text, rect.width(), widget.minimumSizeHint().width()
        )
        assert rect.height() >= widget.minimumSizeHint().height(), (
            text, rect.height(), widget.minimumSizeHint().height()
        )
        if isinstance(widget, QPushButton):
            option = QStyleOptionButton()
            widget.initStyleOption(option)
            content = widget.style().subElementRect(
                QStyle.SubElement.SE_PushButtonContents, option, widget
            )
        else:
            content = widget.contentsRect()
        assert content.width() >= widget.fontMetrics().horizontalAdvance(text), (
            text, content.width(), widget.fontMetrics().horizontalAdvance(text)
        )
        assert content.height() >= widget.fontMetrics().height(), (text, content)
        if previous is not None:
            assert previous.right() < rect.left(), (previous, rect, text)
        previous = rect


@pytest.mark.parametrize(
    ("phase", "status", "record_action"),
    [
        ("recording", "REC 00:00", "■ Stop Recording"),
        ("stop_failed", "CLEANUP PENDING", "■ Finish Stop"),
        ("complete", "READY · TAKE SAVED", "● Record Session"),
    ],
)
def test_themed_720px_active_music_keeps_every_action_and_receipt_readable(
    qapp, window, phase, status, record_action
):
    _active_controls(window, phase=phase)
    _settle(qapp)
    strip = window.session_strip
    assert window.size() == QSize(720, 560)
    assert window.session_controls.property("helpPreviewCompact") is True
    assert strip._record_elapsed.text() == status
    assert strip._record_button.accessibleName() == record_action
    assert strip._audio_button.accessibleName() == "End Session"
    _assert_readable_inside_bar(
        window,
        [
            strip._invite_button,
            strip._record_elapsed,
            strip._record_button,
            strip._video_button,
            strip._song_button,
            strip._studio_button,
            window._room_help_button,
            strip._tools_button,
            strip._audio_button,
        ],
    )


def test_themed_720px_art_keeps_help_and_every_applicable_action_readable(qapp, window):
    _active_controls(window, profile="art")
    _settle(qapp)
    strip = window.session_strip
    assert window.size() == QSize(720, 560)
    assert strip._record_button.isHidden()
    assert strip._record_elapsed.isHidden()
    assert strip._song_button.isHidden()
    assert strip._studio_button.isHidden()
    _assert_readable_inside_bar(
        window,
        [
            strip._invite_button,
            strip._video_button,
            window._room_help_button,
            strip._tools_button,
            strip._audio_button,
        ],
    )


def test_disabling_preview_restores_original_bar_density_and_hides_help(qapp, window):
    _active_controls(window, phase="complete", preview=False)
    _settle(qapp)
    layout = window.session_controls.layout()
    margins = layout.contentsMargins()
    baseline = (margins.left(), margins.top(), margins.right(), margins.bottom(), layout.spacing())
    original_invite_hint = window.session_strip._invite_button.sizeHint()
    assert baseline == (Space.LG, Space.SM, Space.LG, Space.SM, Space.SM)
    assert window._room_help_button.isHidden()
    window.set_room_help_enabled(True)
    window.room_help.setVisible(True)
    _settle(qapp)
    assert window.session_controls.property("helpPreviewCompact") is True
    assert layout.contentsMargins().left() == Space.SM
    assert layout.spacing() == Space.XS
    assert window.session_strip._invite_button.sizeHint().width() < original_invite_hint.width()
    window._show_room_help()
    _settle(qapp)
    assert window._room_help_dialog.isVisible()
    window.set_room_help_enabled(False)
    _settle(qapp)
    margins = layout.contentsMargins()
    restored = (margins.left(), margins.top(), margins.right(), margins.bottom(), layout.spacing())
    assert restored == baseline
    assert window.session_strip._invite_button.sizeHint() == original_invite_hint
    assert window.session_controls.property("helpPreviewCompact") is False
    assert window._room_help_button.isHidden()
    assert window._room_help_dialog.isHidden()


def test_preview_density_only_applies_below_900px(qapp, window):
    _active_controls(window, phase="complete")
    window.resize(900, 560)
    _settle(qapp)
    assert window.session_controls.property("helpPreviewCompact") is False
    assert window.session_controls.layout().contentsMargins().left() == Space.LG
    assert window._room_help_button.isVisible()
    window.resize(720, 560)
    _settle(qapp)
    assert window.session_controls.property("helpPreviewCompact") is True


def test_themed_300px_help_dialog_fits_long_status_and_composer(qapp, window):
    _active_controls(window)
    panel = window.room_help
    panel.set_available(True, SEND_FAILED_STATUS)
    panel.set_entries([("Peer", "A" * 500, "")])
    window._room_help_dialog.resize(300, 340)
    window._show_room_help()
    _settle(qapp)
    dialog = window._room_help_dialog
    assert dialog.width() == 300
    assert dialog.rect().contains(panel.geometry())
    assert not dialog.isModal()
    for widget in (panel._input, panel._send, panel._messages, *panel.findChildren(QLabel)):
        assert widget.isVisible()
        assert panel.rect().contains(widget.geometry()), (widget.objectName(), widget.geometry())
    assert panel._input.width() >= 90
    assert panel._input.geometry().right() < panel._send.geometry().left()
    assert panel._send.width() >= panel._send.minimumSizeHint().width()
    status_bounds = panel._status.fontMetrics().boundingRect(
        panel._status.contentsRect(), Qt.TextFlag.TextWordWrap, SEND_FAILED_STATUS
    )
    assert status_bounds.height() <= panel._status.contentsRect().height()
    assert panel._messages.horizontalScrollBar().maximum() == 0


def test_keyboard_can_reach_help_open_dialog_and_submit_once(qapp, window):
    _active_controls(window)
    panel = window.room_help
    panel.set_available(True, "Secure peer connected")
    emitted = []
    panel.submitted.connect(emitted.append)
    window.activateWindow()
    window.session_strip._studio_button.setFocus()
    _settle(qapp)
    QTest.keyClick(window.session_strip._studio_button, Qt.Key.Key_Tab)
    _settle(qapp)
    assert window._room_help_button.hasFocus()
    QTest.keyClick(window._room_help_button, Qt.Key.Key_Space)
    _settle(qapp)
    assert window._room_help_dialog.isVisible()
    for _ in range(8):
        if panel._input.hasFocus():
            break
        QTest.keyClick(window._room_help_dialog, Qt.Key.Key_Tab)
        _settle(qapp)
    assert panel._input.hasFocus()
    QTest.keyClicks(panel._input, "Can you hear this setup question?")
    QTest.keyClick(panel._input, Qt.Key.Key_Return)
    _settle(qapp)
    assert emitted == ["Can you hear this setup question?"]
    assert panel.draft_text() == "Can you hear this setup question?"
    assert window._room_help_dialog.isVisible()
