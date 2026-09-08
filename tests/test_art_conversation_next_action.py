"""An artist's next Conversation action belongs to the saved meeting."""
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.creative_modes import get_creator_profile_by_key
from tests.test_art_conversation_link_journey import saved_personal_settings as _saved_settings
from tests.test_art_notes_conversation_journey import (
    _click_talk_share, _make_notes, _notes_state, _open_notes,
    qapp as _qapp, room as _room,
)
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.widgets.webex_embed import WebexEmbed

qapp, room = _qapp, _room
saved_personal_settings = _saved_settings


@pytest.fixture
def card(qapp):
    panel = WebexEmbed()
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    panel.resize(760, 350)
    panel.show()
    panel.activateWindow()
    qapp.processEvents()
    events = []
    for signal in ("bring_forward_requested", "open_meeting_requested", "change_link_requested"):
        getattr(panel, signal).connect(lambda event=signal: events.append(event))
    yield panel, events
    panel.close()
    panel.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("service,configured,verified,state,target", [
    ("", False, True, "Not opened", "_change_link_btn"),
    ("", False, False, "Not opened", "_change_link_btn"),
    ("Webex", True, True, "Not opened", "_fallback_btn"),
    ("Webex", True, True, "Open failed", "_fallback_btn"),
    ("Webex", True, True, "Opened externally", "_bring_forward_btn"),
    ("Webex", True, False, "Opened externally", "_fallback_btn"),
    ("Google Meet", True, True, "Not opened", "_fallback_btn"),
    ("Zoom", True, True, "Opened externally", "_fallback_btn"),
    ("", True, True, "Opened externally", "_fallback_btn"),
    ("Webex", True, True, "Opening…", "_status_label"),
    ("Google Meet", True, False, "Opening…", "_status_label"),
])
def test_art_entry_focus_and_primary_follow_the_saved_meeting(
    card, qapp, service, configured, verified, state, target,
):
    panel, events = card
    panel.set_meeting_configured(configured)
    panel.set_service_label(service)
    panel.set_app_status("installed", publisher_verified=verified)
    panel.set_launch_status(state)
    panel.focus_primary_action()
    qapp.processEvents()
    assert QApplication.focusWidget() is getattr(panel, target)
    primary = [button for button in (
        panel._change_link_btn, panel._fallback_btn, panel._bring_forward_btn,
    ) if button.objectName() == "PrimaryButton"]
    expected = panel._fallback_btn if state == "Opening…" else getattr(panel, target)
    assert primary == [expected]
    assert events == []
    if state == "Opening…":
        assert not expected.isEnabled()
        QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Space)
        assert events == []


@pytest.mark.parametrize("service,state,advice", [
    ("Google Meet", "Not opened", False), ("Zoom", "Opened externally", False),
    ("", "Opened externally", False), ("Webex", "Not opened", False),
    ("Webex", "Opened externally", True),
])
def test_return_advice_never_substitutes_webex_for_another_meeting(card, service, state, advice):
    panel, _ = card
    panel.set_meeting_configured(True)
    panel.set_service_label(service)
    panel.set_app_status("installed", publisher_verified=True)
    panel.set_launch_status(state)
    assert ("Use Show Webex App" in panel._fallback_btn.toolTip()) is advice
    # Native controls retain the precise operation the user can explicitly choose.
    assert panel._bring_forward_btn.text() == "Show Webex App"


def test_native_detection_and_profile_rendering_do_not_steal_an_artists_focus(card, qapp):
    panel, events = card
    panel.set_meeting_configured(True)
    panel.set_service_label("Google Meet")
    panel._change_link_btn.setFocus()
    for verified in (False, True, False):
        panel.set_app_status("installed", publisher_verified=verified)
        qapp.processEvents()
        assert QApplication.focusWidget() is panel._change_link_btn
        assert panel._fallback_btn.objectName() == "PrimaryButton"
    panel.set_creator_profile(get_creator_profile_by_key("music"))
    assert all(button.objectName() == "GhostButton" for button in (
        panel._change_link_btn, panel._fallback_btn, panel._bring_forward_btn,
    ))
    panel.set_creator_profile(get_creator_profile_by_key("art"))
    assert QApplication.focusWidget() is panel._change_link_btn
    assert panel._fallback_btn.objectName() == "PrimaryButton"
    assert events == []


def test_pending_link_handoff_cannot_focus_another_action_on_repeat_input(card, qapp):
    panel, events = card
    panel.set_meeting_configured(True)
    panel.set_service_label("Webex")
    panel.set_app_status("installed", publisher_verified=True)
    panel._fallback_btn.setFocus()
    panel.set_launch_status("Opening…")
    qapp.processEvents()
    assert QApplication.focusWidget() is panel._status_label
    QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Space)
    assert events == []
    panel.set_launch_status("Opened externally")
    qapp.processEvents()
    assert QApplication.focusWidget() is panel._status_label
    assert panel._bring_forward_btn.objectName() == "PrimaryButton"


@pytest.mark.parametrize("move_on", ["change_link", "hidden"])
def test_late_native_recheck_does_not_take_back_focus_after_the_artist_moves_on(card, qapp, move_on):
    panel, events = card
    panel.set_meeting_configured(True)
    panel.set_service_label("Google Meet")
    panel.set_app_status("not-installed")
    panel._recheck_btn.setFocus()
    panel.set_app_checking()
    assert QApplication.focusWidget() is panel._app_status_label
    panel._change_link_btn.setFocus()
    if move_on == "hidden":
        panel.hide()
    before = QApplication.focusWidget()
    panel.set_app_status("not-installed")
    qapp.processEvents()
    assert QApplication.focusWidget() is before
    assert events == []


@pytest.mark.parametrize("configured,new_url,target", [
    (False, "https://meet.google.com/abc-defg-hij", "_fallback_btn"),
    (True, "https://meet.google.com/abc-defg-hij", "_fallback_btn"),
    (True, "", "_change_link_btn"),
])
@pytest.mark.parametrize("role", ["host", "native"])
def test_saving_the_real_link_editor_returns_to_its_new_meeting_action(
    room, qapp, monkeypatch, saved_personal_settings, configured, new_url, target, role,
):
    from tests.test_art_conversation_link_journey import (
        _button, _drive_room_meeting, _drive_settings, _raise_dialog_failures,
        _room_meeting_field, _settings_bytes,
    )

    pair = room(role=role, profile="art", configured=configured)
    app, panel = pair.app, pair.app.window.webex_embed
    personal_url = app.settings.webex_url
    personal_bytes = _settings_bytes(app)
    panel.set_app_status("installed", publisher_verified=True)
    if configured:
        panel.set_launch_status("Opened externally")
    app._on_art_overview_conversation()
    generation = app._room_participant.generation

    def save(dialog):
        editor = dialog._video if role == "host" else _room_meeting_field(dialog)
        editor.setText(new_url)
        QTest.mouseClick(
            _button(dialog, "Save" if role == "host" else "OK"),
            Qt.MouseButton.LeftButton,
        )

    driver = _drive_settings if role == "host" else _drive_room_meeting
    observed = driver(monkeypatch, save)
    panel._change_link_btn.click()
    _raise_dialog_failures(observed, accepted=True)
    qapp.processEvents()
    assert app._effective_meeting_url() == new_url
    if role == "host":
        assert app.settings.webex_url == new_url
    else:
        assert app.settings.webex_url == personal_url
        assert _settings_bytes(app) == personal_bytes
    assert app.window.focusWidget() is getattr(panel, target)
    assert getattr(panel, target).objectName() == "PrimaryButton"
    assert "Use Show Webex App" not in panel._fallback_btn.toolTip()
    assert app._room_participant.generation == generation
    app.bridge.launch_webex.assert_not_called()
    pair.player_factory.assert_not_called()


@pytest.mark.parametrize("role", ["host", "lan", "native"])
@pytest.mark.parametrize("service,url", [
    ("Google Meet", "https://meet.google.com/abc-defg-hij"),
    ("Webex", "https://studio.webex.com/meet/PRIVATE_ART_CONVERSATION"),
])
def test_real_art_notes_entry_opens_the_saved_meeting_only_on_explicit_input(
    room, qapp, monkeypatch, caplog, role, service, url,
):
    native = Mock()
    monkeypatch.setattr(ApplicationController, "_show_webex_app", native)
    pair = room(role=role, profile="art", configured=True)
    app = pair.app
    canvas = _open_notes(pair, qapp)
    _make_notes(pair)
    before = _notes_state(canvas)
    panel = app.window.webex_embed
    if role == "host":
        app.settings.webex_url = url
    else:
        app._set_session_meeting_url(url)
    panel.set_service_label(service)
    panel.set_app_status("installed", publisher_verified=True)
    _click_talk_share(pair, qapp)
    assert app.window.focusWidget() is panel._fallback_btn
    assert panel._fallback_btn.objectName() == "PrimaryButton"
    native.assert_not_called()
    app.bridge.launch_webex.assert_not_called()
    QTest.keyClick(app.window.focusWidget(), Qt.Key.Key_Space)
    app.bridge.launch_webex.assert_called_once_with(manual=True, meeting_url=url)
    native.assert_not_called()
    assert _notes_state(canvas) == before
    assert app._room_participant.generation == pair.generation
    assert app._reference_video_identity() == pair.identity
    pair.player_factory.assert_not_called()
    app.jamulus.send_chat.assert_not_called()
    assert "PRIVATE_ART_CONVERSATION" not in caplog.text
