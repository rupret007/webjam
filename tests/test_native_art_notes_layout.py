"""Real room guidance leaves Art Notes and its communication action usable."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QWidget

from tests.test_art_room_return_ui import qapp as _qapp_fixture
from tests.test_native_art_activities import native_room as _native_room_fixture
from webjam_qt.theme import load_stylesheet

qapp = _qapp_fixture
native_room = _native_room_fixture

_NOTES = (
    "Clay study\n\nTry a taller rim on the second bowl.\n"
    "Keep the first shape for comparison.\n\n"
    "Ask the group which silhouette feels balanced."
)


@pytest.fixture(autouse=True)
def notes_style(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "webjam_qt.controllers.session_persistence._persistence_home", lambda: tmp_path,
    )
    previous = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())
    yield
    qapp.setStyleSheet(previous)


def _settle(qapp):
    for _ in range(5):
        qapp.processEvents()


def _assert_real_notes_fit(window):
    panel = window.session_canvas
    editor = panel._notes
    assert panel.isVisibleTo(window)
    assert editor.height() >= editor.minimumSizeHint().height()
    assert editor.width() >= editor.minimumSizeHint().width()
    rects = []
    widgets = (
        panel._header, panel.room_return_button(), *panel._toolbar_buttons,
        panel._save_notes_button, panel._notes_save_status, panel._guidance,
        panel._pulse, editor, panel._art_communication,
    )
    for widget in widgets:
        if not widget.isVisibleTo(panel):
            continue
        rect = QRect(widget.mapTo(panel, QPoint()), widget.size())
        assert panel.rect().contains(rect), (widget.objectName(), rect, panel.rect())
        assert not any(previous.intersects(rect) for previous in rects), (widget.objectName(), rect)
        rects.append(rect)
    for label in panel.findChildren(QLabel):
        if label.isVisibleTo(panel):
            assert label.height() >= label.heightForWidth(label.width()), label.objectName()
    for button in (*panel._toolbar_buttons, panel.room_return_button(), panel.talk_share_button()):
        if button.isVisibleTo(panel):
            assert button.width() >= button.minimumSizeHint().width(), button.text()
            assert button.height() >= button.minimumSizeHint().height(), button.text()
    assert panel.talk_share_button().isVisibleTo(window)
    assert window.session_strip._audio_button.isVisibleTo(window)


@pytest.mark.parametrize("size", [(720, 560), (760, 600), (1040, 720)])
@pytest.mark.parametrize("save_state", ["saved", "failed"])
@pytest.mark.parametrize("font_stretch", [100, 125])
def test_real_room_guidance_and_transition_text_keep_notes_usable(
    native_room, qapp, size, save_state, font_stretch,
):
    pair = native_room(profile="art")
    app, window = pair.app, pair.app.window
    panel = window.session_canvas
    window.resize(*size)
    app._on_rail_view_changed("canvas")
    panel.set_notes(_NOTES)
    app._refresh_session_pulse()
    assert app._save_notes()
    # Reproduce the real Notes → Conversation → Notes progression too. At
    # ordinary widths the existing room and Conversation remain alongside it.
    _settle(qapp)
    QTest.mouseClick(panel.talk_share_button(), Qt.MouseButton.LeftButton)
    _settle(qapp)
    assert window.webex_embed.isVisibleTo(window)
    meeting = window.webex_embed
    meeting_status = (meeting._status_label.text(), app.settings.webex_url)
    app._on_rail_view_changed("canvas")
    for widget in panel.findChildren(QWidget):
        font = widget.font()
        font.setStretch(font_stretch)
        widget.setFont(font)
    panel.set_notes_save_state(save_state)
    _settle(qapp)
    assert window.size() == QSize(*size)
    assert panel._current_guidance.transitions
    assert panel._guidance_recent.isVisibleTo(panel)
    assert "What did this piece need" in panel._pulse_next.text()
    _assert_real_notes_fit(window)
    if size[0] < 900:
        assert panel.width() >= window.center_splitter.width() - 1
        assert not meeting.isVisibleTo(window)
    else:
        assert meeting.isVisibleTo(window)
    assert (meeting._status_label.text(), app.settings.webex_url) == meeting_status
    QTest.mouseClick(panel.talk_share_button(), Qt.MouseButton.LeftButton)
    _settle(qapp)
    assert window.webex_embed is meeting and meeting.isVisibleTo(window)
    assert meeting.isAncestorOf(window.focusWidget())
    assert panel.current_notes() == _NOTES
    assert panel._suggestion_button.isVisibleTo(panel) == (save_state == "saved")
    assert pair.players == [] and pair.launcher.joined == []
    app.bridge.launch_webex.assert_not_called()



def test_wide_notes_resize_to_compact_preserves_meeting_and_reuses_it(native_room, qapp):
    pair = native_room(profile="art")
    app, window = pair.app, pair.app.window
    panel, meeting = window.session_canvas, window.webex_embed
    window.resize(1040, 720)
    app._on_art_overview_conversation()
    app._on_rail_view_changed("canvas")
    panel.set_notes(_NOTES)
    app._refresh_session_pulse()
    _settle(qapp)
    assert meeting.isVisibleTo(window)
    initial = (meeting._status_label.text(), app.settings.webex_url)
    window.resize(720, 560)
    _settle(qapp)
    assert window.size() == QSize(720, 560)
    assert panel.width() >= window.center_splitter.width() - 1
    assert not meeting.isVisibleTo(window)
    _assert_real_notes_fit(window)
    window.resize(1040, 720)
    _settle(qapp)
    assert not meeting.isVisibleTo(window)
    assert window._room_stage.isVisibleTo(window)
    assert (meeting._status_label.text(), app.settings.webex_url) == initial
    QTest.mouseClick(panel.talk_share_button(), Qt.MouseButton.LeftButton)
    _settle(qapp)
    assert window.webex_embed is meeting and meeting.isVisibleTo(window)
    assert meeting.isAncestorOf(window.focusWidget())
    assert panel.current_notes() == _NOTES
    app.bridge.launch_webex.assert_not_called()
