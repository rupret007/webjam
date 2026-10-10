"""Compact Join keeps one instruction, with network details in Help."""

from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QLineEdit, QMessageBox

from tests.test_art_join_entry_ui import join_door as join_door, qapp as qapp
from webjam_qt.theme import load_stylesheet


@pytest.mark.parametrize("profile", ["art", "music", "podcast_voice", "review_rehearsal"])
@pytest.mark.parametrize("large_text", [False, True])
def test_join_fits_800x600_with_one_instruction(join_door, qapp, profile, large_text):
    dialog = join_door(profile, size=(800, 600))
    if large_text:
        dialog.setStyleSheet(load_stylesheet() + "\nQWidget { font-size: 20px; }")
    qapp.processEvents()
    assert dialog.size().width() == 800 and dialog.size().height() == 600
    assert dialog._join_subtitle.text() == "Paste the invite or the whole message."
    for value in ("", "not an invitation"):
        dialog._invite_input.setText(value)
        if value:
            QTest.keyClick(dialog._invite_input, Qt.Key.Key_Return)
            assert dialog._join_error.isVisibleTo(dialog)
        qapp.processEvents()
        controls = (dialog._invite_input, dialog._join_button_primary, dialog._join_back_button)
        for widget in (*controls, *dialog._join_page.findChildren(QLabel)):
            if not widget.isVisibleTo(dialog):
                continue
            assert dialog.rect().contains(QRect(widget.mapTo(dialog, QPoint()), widget.size()))
            assert widget.visibleRegion().contains(widget.rect()), widget.objectName()
            if isinstance(widget, QLabel) and widget.wordWrap() and widget.text():
                assert widget.height() >= widget.heightForWidth(widget.width())
        assert dialog._invite_input.echoMode() == QLineEdit.EchoMode.Password
        dialog._invite_input.setFocus()
        for control in controls[1:]:
            QTest.keyClick(dialog, Qt.Key.Key_Tab)
            qapp.processEvents()
            assert control.hasFocus()


@pytest.mark.parametrize("profile", ["art", "music"])
def test_join_help_preserves_paste_and_never_submits(join_door, qapp, profile):
    dialog = join_door(profile, size=(800, 600))
    dialog._invite_input.setText("private invitation still being edited")
    menu_actions = dialog._menu_bar.actions()
    help_action = next(action for action in menu_actions if action.text() == "&Help")
    help_menu = help_action.menu()
    # Retain PySide's menu wrappers through the fixture's dialog teardown.
    dialog._test_menu_references = (menu_actions, help_menu)
    action = next(action for action in help_menu.actions() if action.text() == "Joining…")
    assert action.isEnabled()
    with patch("webjam_qt.windows.launch_dialog.QMessageBox.information",
               return_value=QMessageBox.StandardButton.Ok) as help_box, patch(
        "webjam_qt.windows.launch_dialog.parse_invitation_at_ingress"
    ) as parse:
        action.trigger()
    parse.assert_not_called()
    help_box.assert_called_once_with(
        dialog, "Join", "Paste the invite or the whole message.\n\n"
        "If your invitation says “same network,” use your host’s Wi-Fi or local network."
    )
    assert dialog._invite_input.text() == "private invitation still being edited"
    assert dialog._pages.currentWidget() is dialog._join_page
    assert dialog.selected_role == ""
    QTest.mouseClick(dialog._join_back_button, Qt.MouseButton.LeftButton)
    assert dialog.showing_choices


def test_join_help_can_be_read_and_closed_without_losing_input(join_door, qapp):
    dialog = join_door(size=(800, 600))
    dialog._invite_input.setText("unfinished invitation")
    observed = {}

    def close_help():
        box = qapp.activeModalWidget()
        if isinstance(box, QMessageBox):
            observed["text"] = box.text()
            observed["parent"] = box.parentWidget()
            box.accept()

    QTimer.singleShot(0, close_help)
    dialog._join_help_action.trigger()
    qapp.processEvents()
    assert observed["parent"] is dialog
    assert "If your invitation says “same network,”" in observed["text"]
    assert "your host’s Wi-Fi or local network" in observed["text"]
    assert dialog._invite_input.text() == "unfinished invitation"
    assert dialog.isVisible() and not dialog.showing_choices
    assert dialog.selected_role == ""
