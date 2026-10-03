"""Video/ensemble choice keeps its sound guidance readable at enlarged text."""
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from tests.test_reference_video_ui import qapp as qapp
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.play_along import PlayAlongDialog


@pytest.mark.parametrize("hosting", [True, False])
@pytest.mark.parametrize("width", [390, 500])
def test_choice_scrolls_full_guidance_and_reveals_keyboard_actions(qapp, hosting, width):
    previous = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())
    dialog = PlayAlongDialog(hosting=hosting)
    dialog.setStyleSheet("QWidget { font-size: 22px; }")
    dialog.resize(width, 390)
    events = []
    dialog.choice_requested.connect(events.append)
    dialog.show()
    dialog.activateWindow()
    for _ in range(3):
        qapp.processEvents()
    try:
        assert dialog.width() == width
        content = dialog.scroll.widget()
        for label in content.findChildren(QLabel):
            assert label.height() >= label.heightForWidth(label.width()), label.text()
            assert content.rect().contains(QRect(label.mapTo(content, QPoint()), label.size()))
        assert dialog.scroll.verticalScrollBar().maximum() > 0
        for key, button in dialog.buttons.items():
            button.setFocus(Qt.FocusReason.TabFocusReason)
            qapp.processEvents()
            viewport = dialog.scroll.viewport()
            assert viewport.rect().contains(QRect(button.mapTo(viewport, QPoint()), button.size()))
            assert button.width() >= button.minimumSizeHint().width()
            QTest.keyClick(button, Qt.Key.Key_Space)
            assert events[-1] == key
        assert events == ["lesson", "ensemble"]
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
        qapp.setStyleSheet(previous)
