"""Compact Join keeps the next action visible and network advice in Help."""

from unittest.mock import patch

import pytest
from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMessageBox

from tests.test_art_join_entry_ui import join_door as join_door, qapp as qapp
from tests.support.start_ux import assert_no_banned_first_screen_words, harvest_first_screen
from webjam_qt.theme import load_stylesheet


@pytest.mark.parametrize("profile", ["art", "music", "podcast_voice", "review_rehearsal"])
@pytest.mark.parametrize("large_text", [False, True])
def test_join_actions_and_short_guidance_fit_800x600(join_door, qapp, profile, large_text):
    dialog = join_door(profile, (800, 600))
    if large_text:
        dialog.setStyleSheet(load_stylesheet() + "\nQWidget { font-size: 20px; }")
    for state in ("empty", "pasted", "error"):
        if state == "pasted":
            dialog._invite_input.setText("Unchecked invitation")
        elif state == "error":
            dialog.show_ingress_error("That invite link doesn’t look right. Copy it again from your host.")
        qapp.processEvents()
        assert (dialog.width(), dialog.height()) == (800, 600)
        assert dialog._join_subtitle.text() == "Paste the invite or the whole message."
        assert "same network" not in harvest_first_screen(dialog).casefold()
        controls = [dialog._invite_input, dialog._join_button_primary,
                    dialog._join_back_button, dialog._join_help_button]
        for control in controls:
            assert control.isVisibleTo(dialog)
            bounds = QRect(control.mapTo(dialog, QPoint()), control.size())
            assert dialog.rect().contains(bounds)
        for label in dialog.findChildren(QLabel):
            if label.isVisibleTo(dialog) and label.wordWrap() and label.text():
                assert label.height() >= label.heightForWidth(label.width())
        assert dialog._invite_input.height() >= 46
        assert dialog._join_button_primary.height() >= 48
        if profile in {"art", "music"}:
            assert_no_banned_first_screen_words(harvest_first_screen(dialog))


def test_keyboard_help_opens_network_advice_without_submitting_or_losing_invite(join_door, qapp):
    dialog = join_door("art", (800, 600))
    dialog._invite_input.setText("Unchecked private invitation")
    dialog._invite_input.setFocus()
    for control in (dialog._join_button_primary, dialog._join_back_button, dialog._join_help_button):
        QTest.keyClick(qapp.focusWidget(), Qt.Key.Key_Tab)
        assert control.hasFocus()
    assert dialog._join_help_button.text() == "Need help?"
    assert dialog._join_help_button.accessibleName() == "Need help?"
    captured = []

    def close_help():
        box = qapp.activeModalWidget()
        if isinstance(box, QMessageBox):
            captured.append((box.parentWidget(), box.text()))
            QTest.keyClick(box, Qt.Key.Key_Escape)

    with patch("webjam_qt.windows.launch_dialog.parse_invitation_at_ingress") as parse:
        QTimer.singleShot(0, close_help)
        QTest.keyClick(dialog._join_help_button, Qt.Key.Key_Space)
        qapp.processEvents()
        parse.assert_not_called()
    assert len(captured) == 1
    assert captured[0][0] is dialog
    assert "If your invitation says “same network,” use your host’s Wi-Fi or local network." in captured[0][1]
    assert dialog._invite_input.text() == "Unchecked private invitation"
    assert dialog.selected_role == ""
    assert not dialog.showing_choices
    # The offscreen plugin has no window manager to reactivate the parent.
    dialog.activateWindow()
    assert QTest.qWaitForWindowActive(dialog)
    assert dialog._join_help_button.hasFocus()
    dialog._join_back_button.click()
    assert dialog.showing_choices
