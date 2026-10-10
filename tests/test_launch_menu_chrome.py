"""The launch door has no menu chrome or hidden File keyboard routes."""

from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenuBar, QPushButton

from core.settings import AppSettings
from tests.support.start_ux import assert_no_banned_first_screen_words, harvest_first_screen
from webjam_qt.theme import load_stylesheet
from webjam_qt.windows.launch_dialog import LaunchDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    previous = app.styleHints().tabFocusBehavior()
    app.styleHints().setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusAllControls)
    yield app
    app.styleHints().setTabFocusBehavior(previous)


@pytest.fixture
def door(qapp, tmp_path, request):
    platform, profile, allow_choices = request.param
    with patch("webjam_qt.windows.launch_dialog.sys.platform", platform), patch(
        "webjam_qt.windows.launch_dialog._windows_jamulus_installer",
        return_value="C:/WebJam/setup.exe" if platform == "win32" else "",
    ):
        dialog = LaunchDialog(
            AppSettings(config_file=str(tmp_path / "settings.json"),
                        last_creator_profile_key=profile),
            allow_workspace_choices=allow_choices,
        )
    dialog.setStyleSheet(load_stylesheet())
    dialog.resize(800, 600)
    dialog.show()
    qapp.processEvents()
    yield dialog
    dialog.close()
    dialog.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("door", [
    (platform, profile, allow_choices)
    for platform in ("darwin", "win32")
    for profile in ("art", "music")
    for allow_choices in (True, False)
], indirect=True)
def test_launch_and_join_have_no_menu_or_file_shortcut(qapp, door):
    file_dispatch = Mock()
    for action in door._workspace_actions.values():
        action.triggered.connect(file_dispatch)
        assert action.shortcut().isEmpty()
    for show_page in (door.show_choices, door.show_join, door.show_choices):
        show_page()
        qapp.processEvents()
        assert door.layout().menuBar() is None
        for bar in door.findChildren(QMenuBar):
            assert not bar.isNativeMenuBar()
            assert not bar.isVisibleTo(door)
            assert all(not action.isVisible() for action in bar.actions())
        assert_no_banned_first_screen_words(harvest_first_screen(door))
        for sequence in QKeySequence.keyBindings(QKeySequence.StandardKey.New):
            QTest.keySequence(door, sequence)
        QTest.keyClick(door, Qt.Key.Key_F, Qt.KeyboardModifier.AltModifier)
        qapp.processEvents()
        file_dispatch.assert_not_called()
        assert QApplication.activePopupWidget() is None
        assert door.selected_role == ""
        assert door.isVisible()
        assert door.size().width() == 800 and door.size().height() == 600


@pytest.mark.parametrize("door", [("win32", "music", True), ("win32", "art", False)], indirect=True)
def test_single_help_link_keeps_setup_reachable_by_mouse_and_keyboard(qapp, door):
    links = [button for button in door.findChildren(QPushButton)
             if button.isVisibleTo(door) and button.text() == "Help"]
    assert len(links) == 1
    help_link = links[0]
    assert not help_link.autoDefault()
    assert not help_link.isDefault()
    assert help_link.accessibleName() == "Help"
    assert help_link.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert door.rect().contains(QRect(help_link.mapTo(door, QPoint()), help_link.size()))
    QTest.mouseClick(help_link, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert door._pages.currentWidget() is door._setup_page
    assert door._install_jamulus_button.isVisibleTo(door)
    door.show_join()
    door._join_back_button.setFocus()
    for _ in range(12):
        QTest.keyClick(door, Qt.Key.Key_Tab)
        qapp.processEvents()
        if help_link.hasFocus():
            break
    assert help_link.hasFocus()
    QTest.keyClick(help_link, Qt.Key.Key_Return)
    qapp.processEvents()
    assert door._pages.currentWidget() is door._setup_page
    assert door.selected_role == ""
    door.show_join()
    assert door._begin_submission(door._join_button_primary, "Joining…")
    assert not help_link.isEnabled()
    help_link.clicked.emit()
    assert door._pages.currentWidget() is door._join_page
    door._restore_submission()
    assert help_link.isEnabled()


@pytest.mark.parametrize("door", [("darwin", "art", True)], indirect=True)
def test_no_empty_help_link_when_setup_is_unavailable(door):
    assert not door._help_link.isVisibleTo(door)
