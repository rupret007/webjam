"""A complete invite owns Conversation only for the accepted room."""
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QMessageBox

from core.meeting_companion import build_invite_message
from core.network_invite import create_invite_link
from core.settings import AppSettings, load_settings, save_settings
from tests.test_art_room_controller import qapp as _qapp
from tests.test_bridge_service_launch_paths import _make_bridge
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.launch_dialog import LaunchDialog

qapp = _qapp

PERSONAL = "https://personal.webex.com/meet/PRIVATE_PERSONAL"
ROOM = "https://meet.google.com/abc-defg-hij"


def message(meeting=ROOM):
    return build_invite_message(
        join_link=create_invite_link(
            "192.168.1.42", session_id="11111111-1111-1111-1111-111111111111",
            peer_port=42001, invite_token="a" * 64,
        ),
        creator_profile_key="art", meeting_url=meeting,
        same_network_required=True,
    ).text


@pytest.fixture
def apps(qapp, monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", lambda self: False)
    monkeypatch.setattr("webjam_qt.windows.launch_dialog._windows_jamulus_installer", lambda *a: None)
    monkeypatch.setattr("webjam_qt.controllers.session_persistence._persistence_home", lambda: tmp_path)
    monkeypatch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))

    def create(*, cold=False, meeting=ROOM):
        root = tmp_path / str(len(made))
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"), takes_directory=str(root / "takes"),
            webex_url=PERSONAL, host_server_enabled=False, last_creator_profile_key="art",
        )
        save_settings(settings)
        door = LaunchDialog(settings)
        if cold:
            assert door.accept_invite(message(meeting))
        window = ConductorWindow(mode_entries=ApplicationController.mode_entries(), initial_mode_key="music_jam", initial_title="Room")
        app = ApplicationController(
            window, settings=settings, session_invite=door.band_invite,
            session_meeting_url=door.invitation_meeting_url,
        )
        door.deleteLater()
        app.begin_startup_journey = Mock(return_value=True)
        app.bridge.launch_webex = Mock(return_value=True)
        app.window.flash_message = Mock()
        made.append(app)
        return app

    yield create
    for app in reversed(made):
        assert app.shutdown()
        app.window.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("cold", [False, True])
@pytest.mark.parametrize("meeting", [ROOM, ""])
def test_complete_invite_controls_conversation_without_personal_settings_or_auto_open(apps, cold, meeting):
    app = apps(cold=cold, meeting=meeting)
    if not cold:
        assert app.accept_invite_url(message(meeting))
    assert app._effective_meeting_url() == meeting
    assert app.webex.meeting_url == meeting
    app.bridge.launch_webex.assert_not_called()
    assert app.settings.webex_url == PERSONAL
    assert load_settings(app.settings.config_file).webex_url == PERSONAL
    if meeting:
        app._on_join_video()
        app.bridge.launch_webex.assert_called_once_with(manual=True, meeting_url=ROOM)
    else:
        app._show_actionable_error = Mock()
        app._on_join_video()
        app.bridge.launch_webex.assert_not_called()
        app._show_actionable_error.assert_called_once()


def test_copy_uses_room_link_and_settings_reconfigure_does_not_replace_it(apps, qapp):
    from dataclasses import replace
    from PySide6.QtWidgets import QApplication

    app = apps(cold=True)
    app._on_copy_meeting_link()
    assert QApplication.clipboard().text() == ROOM
    previous = app.settings
    app.settings = replace(previous, webex_url="https://other.webex.com/meet/personal")
    app._reconfigure_services_after_settings(previous)
    assert app.webex.meeting_url == ROOM
    assert app._effective_meeting_url() == ROOM
    app.bridge.launch_webex.assert_not_called()
    QApplication.clipboard().clear()


def test_cancelled_or_failed_replacement_keeps_current_meeting(apps, monkeypatch):
    app = apps(cold=True)
    app._room_is_busy_for_invitation = Mock(return_value=True)
    monkeypatch.setattr(QMessageBox, "question", Mock(return_value=QMessageBox.StandardButton.No))
    assert not app.accept_invite_url(message("https://other.webex.com/meet/new"))
    assert app._effective_meeting_url() == ROOM
    app._room_is_busy_for_invitation = Mock(return_value=False)
    app._stop_session_peer = Mock(return_value=False)
    app._show_private_session_cleanup_failure = Mock()
    assert not app.accept_invite_url(message("https://other.webex.com/meet/new"))
    assert app._effective_meeting_url() == ROOM
    app._stop_session_peer = lambda **kwargs: True


def test_pending_invite_keeps_its_own_link_and_bare_invite_clears_it(apps):
    app = apps(cold=True)
    app._invite_switch_in_flight = True
    assert app.accept_invite_url(message("https://other.webex.com/meet/new"))
    assert app._pending_invitation_meeting_url == "https://other.webex.com/meet/new"
    assert app._effective_meeting_url() == ROOM
    assert app.accept_invite_url(message(""))
    assert app._pending_invitation_meeting_url == ""
    assert app._effective_meeting_url() == ROOM
    app._invite_switch_in_flight = False


def test_restore_retires_external_handoff_and_returns_personal_meeting(apps):
    app = apps(cold=True)
    app.bridge.webex_state = "Opened externally"
    before = app._session_meeting_generation
    app._restore_personal_meeting()
    assert app._session_meeting_generation > before
    assert app._effective_meeting_url() == PERSONAL
    assert app.webex.meeting_url == PERSONAL
    assert app.bridge.webex_state == "Not opened"
    assert not app.webex.browser_opened
    app.bridge.launch_webex.assert_not_called()


def test_bridge_snapshots_explicit_room_link_and_drops_invalidated_worker(monkeypatch):
    workers = []
    class Worker:
        def __init__(self, *, target, **kwargs):
            self.target = target
        def start(self):
            workers.append(self.target)
    monkeypatch.setattr("services.bridge_service.threading.Thread", Worker)
    bridge = _make_bridge()
    personal = bridge.settings.webex_url
    assert bridge.launch_webex(manual=True, meeting_url=ROOM)
    assert len(workers) == 1
    bridge.settings.webex_url = "https://changed.webex.com/meet/personal"
    workers.pop()()
    bridge.webex_controller.join_meeting_url.assert_called_once_with(ROOM)
    bridge.webex_controller.join_meeting_url.reset_mock()
    assert bridge.launch_webex(manual=True, meeting_url=ROOM)
    bridge.invalidate_webex_launch()
    workers.pop()()
    bridge.webex_controller.join_meeting_url.assert_not_called()
    assert personal != ROOM


def test_failed_room_meeting_retry_retains_url_and_expires_with_room(monkeypatch):
    from tests.test_bridge_service_launch_paths import _ImmediateThread

    monkeypatch.setattr("services.bridge_service.threading.Thread", _ImmediateThread)
    bridge = _make_bridge()
    bridge.webex_controller.join_meeting_url.side_effect = RuntimeError("failed")
    assert bridge.launch_webex(manual=True, meeting_url=ROOM)
    retry = bridge.show_actionable_error.call_args.kwargs["retry_callback"]
    bridge.webex_controller.join_meeting_url.side_effect = None
    bridge.webex_controller.join_meeting_url.return_value = True
    retry()
    assert bridge.webex_controller.join_meeting_url.call_args.args == (ROOM,)
    assert bridge.webex_controller.join_meeting_url.call_count == 2
    bridge.invalidate_webex_launch()
    retry()
    assert bridge.webex_controller.join_meeting_url.call_count == 2


@pytest.mark.parametrize("meeting", [ROOM, ""])
def test_band_check_uses_session_meeting_without_changing_personal_settings(apps, meeting):
    app = apps(cold=True, meeting=meeting)
    settings = app._effective_band_check_settings()
    assert settings is not app.settings
    assert settings.webex_url == meeting
    assert app.settings.webex_url == PERSONAL
    assert load_settings(app.settings.config_file).webex_url == PERSONAL
