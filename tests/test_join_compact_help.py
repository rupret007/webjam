"""Compact Join keeps the paste path visible and network advice in Help."""

from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMessageBox

from tests.test_art_join_entry_ui import join_door as join_door, qapp as qapp
from tests.support.start_ux import (
    assert_no_banned_first_screen_words,
    harvest_join_page,
)
from webjam_qt.theme import load_stylesheet


@pytest.mark.parametrize("profile", ["art", "music", "podcast_voice", "review_rehearsal"])
@pytest.mark.parametrize("large_text", [False, True])
@pytest.mark.parametrize("state", ["empty", "pasted", "error"])
def test_join_actions_stay_together_above_fold(join_door, qapp, profile, large_text, state):
    dialog = join_door(profile, (800, 600))
    if large_text:
        dialog.setStyleSheet(load_stylesheet() + "\nQWidget { font-size: 20px; }")
    if state == "pasted":
        dialog._invite_input.setText("An unchecked invitation")
    elif state == "error":
        dialog.show_ingress_error(
            "WebJam couldn’t save this choice. The invitation was cleared. "
            "Paste the full invitation again, then choose Join."
        )
    qapp.processEvents()

    assert dialog._join_subtitle.text() == "Paste the invite or the whole message."
    spoken = harvest_join_page(dialog)
    assert "same network" not in spoken
    assert_no_banned_first_screen_words(spoken)
    assert (dialog.width(), dialog.height()) == (800, 600)
    controls = [dialog._invite_input, dialog._join_button_primary, dialog._join_back_button]
    controls += [label for label in dialog._join_page.findChildren(QLabel) if label.isVisibleTo(dialog)]
    for control in controls:
        assert control.isVisibleTo(dialog)
        rect = QRect(control.mapTo(dialog, QPoint()), control.size())
        assert dialog.rect().contains(rect), (control.objectName(), rect)
        if isinstance(control, QLabel) and control.wordWrap():
            assert control.height() >= control.heightForWidth(control.width())
    assert dialog._invite_input.height() >= 44
    assert dialog._join_button_primary.height() >= 48
    join_bottom = dialog._join_button_primary.geometry().bottom()
    back_top = dialog._join_back_button.geometry().top()
    assert 0 < back_top - join_bottom <= 24


def test_help_opens_network_advice_without_using_the_invitation(join_door, qapp):
    dialog = join_door("art", (800, 600))
    dialog._invite_input.setText("Private unchecked invitation")
    assert dialog._help_menu.title() == "&Help"
    action = next(action for action in dialog._help_menu.actions() if action.text() == "Joining a room…")
    observed = {}

    def read_and_close_help():
        box = qapp.activeModalWidget()
        if isinstance(box, QMessageBox):
            observed["text"] = box.text()
            QTest.keyClick(box, Qt.Key.Key_Escape)

    with (
        patch("webjam_qt.windows.launch_dialog.parse_invitation_at_ingress") as parse,
        patch("webjam_qt.windows.launch_dialog.save_settings") as save,
    ):
        QTimer.singleShot(0, read_and_close_help)
        action.trigger()
        parse.assert_not_called()
        save.assert_not_called()
    assert "Paste the invite or the whole message." in observed["text"]
    assert "If your invitation says “same network,” use your host’s Wi-Fi or local network." in observed["text"]
    assert dialog._invite_input.text() == "Private unchecked invitation"
    assert dialog.selected_role == ""
    assert not dialog.showing_choices
    dialog.activateWindow()
    dialog._invite_input.setFocus()
    qapp.processEvents()
    QTest.keyClick(dialog._invite_input, Qt.Key.Key_Tab)
    assert dialog._join_button_primary.hasFocus()
    QTest.keyClick(dialog._join_button_primary, Qt.Key.Key_Tab)
    assert dialog._join_back_button.hasFocus()
    QTest.keyClick(dialog._join_back_button, Qt.Key.Key_Space)
    assert dialog.showing_choices
