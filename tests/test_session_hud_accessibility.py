"""Recovery refreshes keep the current keyboard task usable."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from webjam_qt.widgets.session_hud import SessionHud


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_retry_refresh_preserves_keyboard_focus_after_initial_recovery(qapp):
    window = QWidget()
    layout = QVBoxLayout(window)
    hud = SessionHud()
    editor = QLineEdit()
    layout.addWidget(hud)
    layout.addWidget(editor)
    window.show()
    window.raise_()
    window.activateWindow()
    qapp.processEvents()
    try:
        assert QTest.qWaitForWindowActive(window, 1000)
        hud.set_state(
            "Connection needs attention", "Connection was interrupted.",
            action_text="Try Again", action_kind="retry",
        )
        assert hud._action.hasFocus()
        editor.setFocus()
        assert editor.hasFocus()

        for detail in ("Connection was interrupted.", "Connection is still unavailable."):
            hud.set_state(
                "Connection needs attention", detail,
                action_text="Try Again", action_kind="retry",
            )
            assert editor.hasFocus(), "Periodic recovery status stole keyboard focus"
            assert detail in hud.accessibleDescription()

        hud.set_state("Connected", "The session is ready.", action_visible=False)
        hud.set_state(
            "Connection needs attention", "Another connection was interrupted.",
            action_text="Try Again", action_kind="retry",
        )
        assert hud._action.hasFocus()
    finally:
        window.close()
        window.deleteLater()


def test_invite_notice_stays_on_the_hud_after_status_updates(qapp):
    from webjam_qt.widgets.session_hud import INVITE_COPIED_NOTICE, WIFI_NEEDED_NOTICE

    hud = SessionHud()
    try:
        hud.set_state(
            "Your room is open",
            "Copy the invite. Keep making while you wait for another artist.",
            invite_available=True,
            action_text="Copy Invite",
            action_kind="invite",
        )
        hud.set_notice(INVITE_COPIED_NOTICE)
        assert INVITE_COPIED_NOTICE == (
            "Copied. Send the whole message to your friend. Keep this window open."
        )
        assert "Wi-Fi" not in INVITE_COPIED_NOTICE
        assert hud.notice_text() == INVITE_COPIED_NOTICE
        assert not hud._notice.isHidden()
        hud.set_state(
            "Your room is open",
            "Copy the invite. Keep making while you wait for another artist.",
            invite_available=True,
            action_text="Copy Invite",
            action_kind="invite",
        )
        assert hud.notice_text() == INVITE_COPIED_NOTICE
        hud.set_notice(WIFI_NEEDED_NOTICE)
        assert hud.notice_text() == WIFI_NEEDED_NOTICE
        hud.set_notice("")
        assert hud.notice_text() == ""
        assert hud._notice.isHidden()
    finally:
        hud.deleteLater()
