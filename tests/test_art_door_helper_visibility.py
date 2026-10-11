"""The selected Art start's consequence stays readable beside Join."""

from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from core.settings import AppSettings
from tests.support.start_ux import assert_no_banned_first_screen_words, harvest_first_screen
from webjam_qt.theme import load_stylesheet
from webjam_qt.theme.tokens import Color, Font
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("size", [(620, 520), (800, 600), (1280, 800)])
@pytest.mark.parametrize("start_key, expected", [
    ("talk_and_make", "Host starts Make together. Join uses the host's invite."),
    ("paint_along", "Host starts Paint along, then chooses a file or lesson link. "
     "Join uses the host's invite."),
])
def test_art_consequence_is_readable_beside_join(qapp, tmp_path, size, start_key, expected):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key="art",
    )
    with patch("webjam_qt.windows.launch_dialog.sys.platform", "darwin"):
        dialog = LaunchDialog(settings)
    try:
        dialog._menu_bar.setNativeMenuBar(False)
        dialog.setStyleSheet(load_stylesheet())
        dialog.resize(*size)
        dialog.show()
        qapp.processEvents()
        for card in dialog._visible_start_cards():
            if card.start_key == start_key:
                card.click()
        qapp.processEvents()

        assert (dialog.width(), dialog.height()) == size
        helper = dialog._choice_helper
        join = dialog._join_button
        assert helper.isVisibleTo(dialog)
        assert helper.text() == expected
        # Consequences are body copy, not tiny muted metadata. Inspect the
        # polished widget so the test catches stylesheet regressions too.
        assert helper.font().pixelSize() >= Font.SIZE_MD
        assert helper.palette().color(QPalette.ColorRole.WindowText) == QColor(Color.TEXT_SECONDARY)
        assert 0 <= helper.y() - (join.y() + join.height()) <= 24
        assert helper.height() >= helper.heightForWidth(helper.width())
        for widget in (dialog._host_button, join, helper):
            assert widget.isVisibleTo(dialog)
            bounds = QRect(widget.mapTo(dialog, QPoint()), widget.size())
            assert dialog.rect().contains(bounds)
        assert_no_banned_first_screen_words(harvest_first_screen(dialog))
    finally:
        dialog.close()
        dialog.deleteLater()
