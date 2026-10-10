"""Music's role helper remains readable through profile changes."""

from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QAbstractButton, QApplication

from core.settings import AppSettings
from tests.support.start_ux import assert_no_banned_first_screen_words, harvest_first_screen
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.mark.parametrize("size", [(620, 520), (800, 600), (1280, 800)])
@pytest.mark.parametrize("larger_text", [False, True])
def test_music_role_helper_fits_and_returns_after_art(qapp, tmp_path: Path, size, larger_text):
    settings = AppSettings(
        config_file=str(tmp_path / "settings.json"),
        last_creator_profile_key="music",
    )
    with patch("sys.platform", "darwin"):
        dialog = LaunchDialog(settings)
    try:
        dialog._menu_bar.setNativeMenuBar(False)
        dialog.setStyleSheet(
            load_stylesheet() + ("\nQWidget { font-size: 20px; }" if larger_text else "")
        )
        dialog.resize(*size)
        dialog.show()
        qapp.processEvents()

        for art_start in (None, "talk_and_make", "paint_along"):
            if art_start is not None:
                dialog._art_profile_card.click()
                cards = dialog._visible_start_cards()
                assert [card.start_key for card in cards] == ["talk_and_make", "paint_along"]
                next(card for card in cards if card.start_key == art_start).click()
                assert dialog.selected_start_key == art_start
                assert dialog._choice_helper.text().startswith(
                    "Host starts Paint along" if art_start == "paint_along"
                    else "Host starts Make together"
                )
                dialog._music_profile_card.click()
                qapp.processEvents()

            helper = dialog._choice_helper
            assert helper.text() == "Host starts the room. Join uses the host's invite."
            assert helper.isVisibleTo(dialog)
            assert (dialog.width(), dialog.height()) == size
            assert helper.height() >= helper.heightForWidth(helper.width())
            assert dialog.rect().contains(QRect(helper.mapTo(dialog, QPoint()), helper.size()))
            assert dialog._visible_start_cards() == []
            assert dialog._studio_button.isHidden()
            assert dialog._host_button.isDefault()
            buttons = [
                button.text() for button in dialog._choice_page.findChildren(QAbstractButton)
                if button.isVisibleTo(dialog)
            ]
            assert buttons == ["Art", "Music", "Host", "Join"]
            assert_no_banned_first_screen_words(harvest_first_screen(dialog))
    finally:
        dialog.close()
        dialog.deleteLater()
