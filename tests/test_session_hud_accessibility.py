"""Recovery refreshes keep the current keyboard task usable."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
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
    window.activateWindow()
    qapp.processEvents()
    try:
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
