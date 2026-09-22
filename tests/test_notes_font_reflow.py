"""Recovery text stays whole with taller/wider platform fonts at the window floor."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QStyle, QStyleOptionButton

from tests.test_notes_combined_unreadable import combined as _combined_fixture
from tests.test_notes_unreadable_recheck import (
    qapp as _qapp_fixture,
    unreadable_notes as _unreadable_notes_fixture,
)
from webjam_qt.theme import load_stylesheet

qapp = _qapp_fixture
combined = _combined_fixture
unreadable_notes = _unreadable_notes_fixture


def _stress_style():
    available = set(QFontDatabase.families())
    family = next(
        (
            name
            for name in ("Verdana", "DejaVu Sans", "Noto Sans", "Liberation Sans")
            if name in available
        ),
        None,
    )
    assert family is not None, (
        "A standard desktop sans-serif font is required for the rendered fixture"
    )
    return (
        load_stylesheet()
        + f'\nQLabel, QPushButton {{font-family:"{family}"; font-size:22px;}}'
    )


def _check_recovery(case, qapp, *, combined=False):
    for _ in range(3):
        qapp.processEvents()
    canvas = case.canvas
    status = canvas._notes_save_status
    assert canvas.size() == QSize(280, 560)
    assert status.height() >= status.heightForWidth(status.width())
    assert status.minimumHeight() == status.heightForWidth(status.width())
    widgets = [status, canvas._recheck_notes_button, canvas._notes]
    if combined:
        widgets += [
            canvas._save_notes_button,
            canvas._communication_hint,
            canvas._talk_share_button,
        ]
    for widget in widgets:
        assert widget.isVisibleTo(canvas)
        assert canvas.rect().contains(
            QRect(widget.mapTo(canvas, QPoint()), widget.size())
        )
        assert widget.height() >= widget.fontMetrics().height()
    assert (
        canvas._recheck_notes_button.width()
        >= canvas._recheck_notes_button.fontMetrics().horizontalAdvance(
            canvas._recheck_notes_button.text()
        )
    )
    button = canvas._recheck_notes_button
    option = QStyleOptionButton()
    option.initFrom(button)
    content = button.style().subElementRect(
        QStyle.SubElement.SE_PushButtonContents, option, button
    )
    assert content.width() >= button.fontMetrics().horizontalAdvance(button.text())
    if combined:
        hint = canvas._communication_hint
        assert hint.height() + 1 >= hint.heightForWidth(hint.width())


def test_combined_recovery_reflows_taller_font_without_clipping_or_growing(
    combined, qapp
):
    combined.canvas.setStyleSheet(_stress_style())
    _check_recovery(combined, qapp, combined=True)


def test_music_original_recheck_reflows_taller_font_without_clipping_or_growing(
    unreadable_notes, qapp
):
    unreadable_notes.canvas.setStyleSheet(_stress_style())
    _check_recovery(unreadable_notes, qapp)


def test_recovery_height_tracks_width_font_message_and_visibility(combined, qapp):
    canvas = combined.canvas
    canvas.setStyleSheet(_stress_style())
    _check_recovery(combined, qapp, combined=True)
    status = canvas._notes_save_status
    narrow_height = status.minimumHeight()
    canvas.resize(480, 560)
    for _ in range(3):
        qapp.processEvents()
    assert status.minimumHeight() == status.heightForWidth(status.width())
    assert status.minimumHeight() < narrow_height
    canvas.resize(280, 560)
    _check_recovery(combined, qapp, combined=True)
    canvas.setStyleSheet(load_stylesheet())
    for _ in range(3):
        qapp.processEvents()
    assert status.minimumHeight() < narrow_height
    combined.art.write_text("Recovered Art original")
    assert combined.owner.reload_unreadable_notes("art")
    for _ in range(3):
        qapp.processEvents()
    assert "Recheck Saved Notes" not in status.text()
    assert status.minimumHeight() == status.heightForWidth(status.width())
    assert combined.owner.export_pending_notes(
        "music", "Retained Music draft", str(combined.music.parent / "music-copy.md")
    )
    for _ in range(3):
        qapp.processEvents()
    assert status.isHidden()
    assert status.minimumHeight() == 0
