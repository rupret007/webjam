"""Art Conversation remains readable when Notes retains it in a narrow stage."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QBoxLayout, QVBoxLayout, QWidget
from shiboken6 import isValid

from core.creative_modes import get_creator_profile_by_key
from tests.test_art_room_return_ui import qapp as _qapp_fixture
from tests.test_native_art_activities import native_room as _native_room_fixture
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.theme import load_stylesheet
from webjam_qt.widgets.webex_embed import WebexEmbed

qapp = _qapp_fixture
native_room = _native_room_fixture


@pytest.fixture(autouse=True)
def isolate_notes_and_style(qapp, tmp_path, monkeypatch):
    notes = tmp_path / "notes"
    notes.mkdir()
    monkeypatch.setattr(
        "webjam_qt.controllers.session_persistence._persistence_home", lambda: notes,
    )
    previous = qapp.styleSheet()
    qapp.setStyleSheet(load_stylesheet())
    # Geometry uses controlled app evidence and never detects or opens providers.
    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", lambda self: False)
    yield
    qapp.setStyleSheet(previous)


def _settle(qapp):
    for _ in range(5):
        qapp.processEvents()


@pytest.fixture
def card(qapp):
    owner = QWidget()
    layout = QVBoxLayout(owner)
    layout.setContentsMargins(0, 0, 0, 0)
    panel = WebexEmbed()
    layout.addWidget(panel)
    layout.addStretch(1)
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    events = []
    for name in (
        "install_webex_requested", "bring_forward_requested", "open_meeting_requested",
        "change_link_requested", "mute_in_webex_requested", "copy_link_requested",
        "recheck_webex_requested", "meeting_state_changed",
    ):
        getattr(panel, name).connect(lambda *args, event=name: events.append(event))
    yield owner, panel, events
    owner.close()
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(owner) and not isValid(panel)
    _settle(qapp)


def _state(panel, state):
    panel.set_meeting_configured(state != "unconfigured")
    if state != "unconfigured":
        panel.set_service_label("Webex")
    if state in {"verified", "opening", "opened", "launch_failed"}:
        panel.set_app_status("installed", version="46.7.0.35472", publisher_verified=True)
    elif state == "missing":
        panel.set_app_status("not-installed")
    elif state == "check_failed":
        panel.set_app_status("unsupported", reason_code="detection-failed")
    elif state == "checking":
        panel.set_app_checking()
    status = {
        "opening": "Opening…", "opened": "Opened externally", "launch_failed": "Open failed",
    }.get(state, "Not opened")
    panel.set_launch_status(status)


def _rect(widget, panel):
    return QRect(widget.mapTo(panel, QPoint(0, 0)), widget.size())


def _assert_readable(panel):
    labels = [panel._title_label, panel._mode_label, panel._status_label]
    if panel._app_status_label.isVisibleTo(panel):
        labels.append(panel._app_status_label)
    rects = []
    for label in labels:
        assert label.isVisibleTo(panel)
        assert panel.rect().contains(_rect(label, panel))
        needed = label.heightForWidth(label.width()) if label.wordWrap() else label.sizeHint().height()
        assert label.height() >= needed, (label.objectName(), label.size(), needed)
        if not label.wordWrap():
            assert label.width() >= label.sizeHint().width()
        rects.append(_rect(label, panel))
    buttons = [
        panel.show_app_button(), panel.fallback_button(), panel.change_link_button(),
        panel._copy_link_btn, panel.install_button(), panel.recheck_button(),
    ]
    for button in buttons:
        if button.isVisibleTo(panel):
            assert panel.rect().contains(_rect(button, panel))
            assert button.width() >= button.sizeHint().width(), button.text()
            assert button.height() >= button.sizeHint().height(), button.text()
            rects.append(_rect(button, panel))
    for index, rect in enumerate(rects):
        for other in rects[index + 1:]:
            assert not rect.intersects(other), (rect, other)


def _control_state(panel):
    controls = [
        panel._app_status_label, panel._status_label, panel.show_app_button(),
        panel.fallback_button(), panel.change_link_button(), panel._copy_link_btn,
        panel.install_button(), panel.recheck_button(),
    ]
    return tuple(
        (id(control), control.text(), control.isHidden(), control.isEnabled(),
         control.accessibleName(), control.accessibleDescription())
        for control in controls
    )


@pytest.mark.parametrize("width", [320, 390, 760])
@pytest.mark.parametrize("state", [
    "unconfigured", "missing", "verified", "check_failed", "checking",
    "opening", "opened", "launch_failed",
])
def test_art_conversation_fits_full_text_and_current_controls(card, qapp, width, state):
    owner, panel, events = card
    _state(panel, state)
    owner.resize(width, 750)
    owner.show()
    owner.activateWindow()
    _settle(qapp)
    assert owner.width() == width
    assert panel.width() == width
    _assert_readable(panel)
    assert "if you like" in panel._mode_label.text()
    assert "Use your own tools" in panel._mode_label.text()
    assert "separate silent local video" in panel._mode_label.text()
    assert panel._status_label.accessibleDescription() == panel._status_label.text()
    assert events == []
    assert panel.mute_button().isHidden() and not panel.mute_button().isEnabled()


@pytest.mark.parametrize("state", ["verified", "check_failed", "opening"])
def test_resize_and_profile_return_preserve_state_focus_and_actions(card, qapp, state):
    owner, panel, events = card
    _state(panel, state)
    owner.resize(390, 750)
    owner.show()
    owner.activateWindow()
    _settle(qapp)
    panel.change_link_button().setFocus()
    before = _control_state(panel)
    for width in (1040, 390, 1040):
        owner.resize(width, 750)
        _settle(qapp)
        _assert_readable(panel)
        assert _control_state(panel) == before
        assert owner.focusWidget() is panel.change_link_button()
        assert events == []
    panel.set_creator_profile(get_creator_profile_by_key("music"))
    _settle(qapp)
    assert panel.minimumHeight() == 112 and panel.maximumHeight() == 152
    assert panel._content_layout.direction() == QBoxLayout.Direction.LeftToRight
    assert panel._header_layout.direction() == QBoxLayout.Direction.LeftToRight
    assert not panel._app_status_label.wordWrap()
    assert _control_state(panel) == before
    assert owner.focusWidget() is panel.change_link_button()
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    owner.resize(390, 750)
    _settle(qapp)
    _assert_readable(panel)
    assert _control_state(panel) == before
    assert owner.focusWidget() is panel.change_link_button()
    assert events == []
    QTest.mouseClick(panel.change_link_button(), Qt.MouseButton.LeftButton)
    _settle(qapp)
    assert events == ["change_link_requested"]


@pytest.mark.parametrize("width,height", [(720, 560), (1040, 720)])
@pytest.mark.parametrize("app_state", ["unconfigured", "check_failed"])
def test_actual_native_notes_retains_readable_conversation(
    native_room, qapp, width, height, app_state,
):
    pair = native_room(profile="music")
    app, window = pair.app, pair.app.window
    window.resize(width, height)
    window.activateWindow()
    window.side_rail.trigger("canvas")
    _settle(qapp)
    canvas = window.session_canvas
    canvas._notes.setPlainText("PRIVATE_LAYOUT_NOTES: clay study")
    _state(window.webex_embed, app_state)
    button = canvas.talk_share_button()
    assert button.isVisibleTo(window) and button.isEnabled()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    _settle(qapp)
    panel = window.webex_embed
    assert panel.isVisibleTo(window)
    _assert_readable(panel)
    card_state = _control_state(panel)
    window.side_rail.trigger("canvas")
    _settle(qapp)
    assert canvas.isVisibleTo(window)
    assert canvas.current_notes() == "PRIVATE_LAYOUT_NOTES: clay study"
    # Compact Notes owns the workspace; normal Notes retains the stage/card.
    if width == 1040:
        assert panel.isVisibleTo(window)
        assert panel.width() < 500
        _assert_readable(panel)
    else:
        assert not panel.isVisibleTo(window)
        assert not window._room_stage.isVisibleTo(window)
        assert canvas.width() >= window.workspace_stack.width() - 2
    assert _control_state(panel) == card_state
    QTest.mouseClick(canvas.talk_share_button(), Qt.MouseButton.LeftButton)
    _settle(qapp)
    assert window.webex_embed is panel and panel.isVisibleTo(window)
    assert _control_state(panel) == card_state
    _assert_readable(panel)
    assert canvas.current_notes() == "PRIVATE_LAYOUT_NOTES: clay study"
    assert pair.players == []
    assert pair.launcher.joined == [] and pair.launcher.host_pages == 0
    app.bridge.launch_webex.assert_not_called()
    assert app._remote_session is pair.source
    assert app._room_participant.generation == pair.room_generation


def test_art_conversation_settles_near_the_responsive_boundary(card, qapp):
    owner, panel, events = card
    _state(panel, "verified")
    owner.show()
    _settle(qapp)
    header_width = (
        panel._title_label.sizeHint().width()
        + panel._app_status_label.fontMetrics().horizontalAdvance(panel._app_status_label.text())
        + panel._header_layout.spacing()
    )
    margins = panel._content_layout.contentsMargins()
    boundary = (
        max(280, header_width) + panel._actions_layout.minimumSize().width()
        + panel._content_layout.spacing() + margins.left() + margins.right()
        + 2 * panel.frameWidth()
    )
    for width in (boundary - 1, boundary, boundary + 1, boundary - 1):
        owner.resize(width, 750)
        _settle(qapp)
        _assert_readable(panel)
        settled = (panel.size(), panel._content_layout.direction(), panel.minimumHeight())
        for _ in range(8):
            qapp.processEvents()
            assert (panel.size(), panel._content_layout.direction(), panel.minimumHeight()) == settled
    assert events == []
