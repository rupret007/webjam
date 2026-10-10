"""Host/Join consequences remain readable beside the Art door actions."""

from pathlib import Path
import sys
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from core.settings import AppSettings
from webjam_qt.theme import Color, Font, load_stylesheet
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.mark.parametrize("size", [(620, 520), (800, 600), (1280, 800)])
@pytest.mark.parametrize("start_key", ["talk_and_make", "paint_along"])
def test_art_consequence_copy_is_readable_next_to_join(qapp, tmp_path: Path, size, start_key):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key="art",
        last_creator_start_key=start_key,
    )
    with patch.object(sys, "platform", "darwin"):
        dialog = LaunchDialog(settings)
    try:
        dialog._menu_bar.setNativeMenuBar(False)
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(*size)
        dialog.show()
        qapp.processEvents()

        helper = dialog._choice_helper
        join = dialog._join_button
        assert helper.isVisibleTo(dialog)
        assert join.isVisibleTo(dialog)
        assert dialog.size().toTuple() == size
        assert helper.font().pixelSize() >= Font.SIZE_MD
        assert helper.palette().color(QPalette.ColorRole.WindowText) == QColor(Color.TEXT_SECONDARY)

        helper_rect = QRect(helper.mapTo(dialog, QPoint()), helper.size())
        join_rect = QRect(join.mapTo(dialog, QPoint()), join.size())
        assert dialog.rect().contains(helper_rect)
        assert dialog.rect().contains(join_rect)
        assert 0 <= helper_rect.top() - join_rect.bottom() <= 24
        assert helper.height() >= helper.heightForWidth(helper.width())
        assert helper.text() == (
            "Host starts Paint along, then chooses a file or lesson link. Join uses the host's invite."
            if start_key == "paint_along" else
            "Host starts Make together. Join uses the host's invite."
        )
    finally:
        dialog.close()
        dialog.deleteLater()
