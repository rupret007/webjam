"""Art Help starts with the door flow; lesson setup needs an explicit expansion."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QRect, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtTest import QTest

from core.creative_modes import get_creator_profile_by_key
from tests.test_help_dialog import app, window  # noqa: F401
from webjam_qt.theme import load_stylesheet


@pytest.fixture(params=[(800, 600), (1280, 800)])
def art_help(app, window, request):  # noqa: F811
    previous = app.styleSheet()
    app.setStyleSheet(load_stylesheet())
    window.set_creator_profile(get_creator_profile_by_key("art"))
    window.show()
    with patch.object(QGuiApplication, "screenAt", return_value=SimpleNamespace(
        availableGeometry=lambda: QRect(0, 0, *request.param)
    )):
        window.show_help()
    app.processEvents()
    yield window._workflow_help_dialog
    app.setStyleSheet(previous)


def test_art_help_has_three_door_flow_bullets_without_scroll_or_banned_words(art_help):
    body = art_help._body
    document = body.document()
    bullets = []
    block = document.begin()
    while block.isValid():
        if block.textList():
            bullets.append(block.text())
        block = block.next()
    assert len(bullets) == 3
    assert "Make together" in bullets[0] and "Paint along" in bullets[0]
    assert all(word in bullets[1] for word in ("Host", "Join", "invite"))
    assert "room" in bullets[2] and "own tools" in bullets[2]
    text = body.toPlainText()
    assert "Shared lessons" in text
    for word in ("Drawpile", "Krita", "Jamulus", "Webex", "Moises", "Music AI",
                 "stems", "BYOK", "host-clocked", "Preview", "API", "Studio Visit",
                 "Bob Ross", "YouTube"):
        assert word.casefold() not in text.casefold()
    assert body.verticalScrollBar().maximum() == 0
    assert body.horizontalScrollBar().maximum() == 0


def test_shared_lessons_expands_by_keyboard_and_collapses_without_external_actions(
    art_help, app, window, monkeypatch,  # noqa: F811
):
    external, navigation = Mock(), Mock()
    monkeypatch.setattr(QDesktopServices, "openUrl", external)
    window.workflow_help_requested.connect(navigation)
    art_help._topics.setFocus()
    QTest.keyClick(art_help._topics, Qt.Key.Key_Tab)
    assert art_help._body.hasFocus()
    QTest.keyClick(art_help._body, Qt.Key.Key_Tab)
    QTest.keyClick(art_help._body, Qt.Key.Key_Return)
    app.processEvents()
    text = art_help._body.toPlainText()
    assert "Webex" in text and "Choose YouTube" in text
    assert "computer sound" in text and "Local files are not transferred" in text
    assert "Open my copy" in text and "silent" in text
    assert art_help.isVisible()
    # The same focused disclosure remains keyboard-operable after rendering.
    QTest.keyClick(art_help._body, Qt.Key.Key_Return)
    assert "Webex" not in art_help._body.toPlainText()
    external.assert_not_called()
    navigation.assert_not_called()


def test_shared_lessons_resets_on_topic_and_profile_changes(art_help, window):  # noqa: F811
    art_help._body.anchorClicked.emit(QUrl("help:shared-lessons"))
    assert "Webex" in art_help._body.toPlainText()
    art_help._search.setText("relink")
    assert "Shared lessons" not in art_help._body.toPlainText()
    art_help._search.clear()
    assert "Shared lessons" in art_help._body.toPlainText()
    assert "Webex" not in art_help._body.toPlainText()
    window.set_creator_profile(get_creator_profile_by_key("music"))
    assert "help:shared-lessons" not in art_help._body.toHtml()
    window.set_creator_profile(get_creator_profile_by_key("art"))
    assert "Webex" not in art_help._body.toPlainText()


def test_shared_lessons_mouse_toggle_and_compact_enlarged_text(art_help, app):  # noqa: F811
    cursor = art_help._body.document().find("Shared lessons")
    art_help._body.setTextCursor(cursor)
    point = art_help._body.cursorRect(cursor).center()
    QTest.mouseClick(art_help._body.viewport(), Qt.MouseButton.LeftButton, pos=point)
    assert "Webex" in art_help._body.toPlainText()
    art_help._available_geometry = QRect(0, 0, 480, 500)
    art_help.setStyleSheet("#HelpDialog QWidget, QTextBrowser#HelpBody { font-size: 22px; }")
    art_help._fit_to_screen()
    app.processEvents()
    assert art_help._available_geometry.contains(art_help.frameGeometry())
    body = art_help._body
    assert body.horizontalScrollBar().maximum() == 0
    assert body.verticalScrollBar().maximum() > 0
    body.setFocus()
    bar = body.verticalScrollBar()
    for _ in range(2 + bar.maximum() // max(1, bar.pageStep())):
        QTest.keyClick(body, Qt.Key.Key_PageDown)
    assert body.verticalScrollBar().value() == body.verticalScrollBar().maximum()
    assert art_help._ok.isVisible()
