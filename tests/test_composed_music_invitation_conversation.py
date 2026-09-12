"""Music copy, cold bootstrap and room ownership share one Conversation.

ApplicationController, LaunchDialog, the LAN observer and Music transfer owner
are production. Network replies, application event-loop entry and machine
launches are controlled boundaries; no connected flag or media proof is seeded.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from core.network_invite import create_invite_link
from core.session_transfer import SessionStateSnapshot
from core.session_transfer_runtime import GuestPeerSession
from core.settings import AppSettings, load_settings, save_settings
from services.lan_room_guest import LanRoomGuest
from tests.test_art_room_controller import arm_lan, drain, invitation, qapp as _qapp
from webjam_qt import app as app_module
from webjam_qt.controllers.application_controller import ApplicationController
from webjam_qt.windows.conductor_window import ConductorWindow
from webjam_qt.windows.launch_dialog import LaunchDialog

qapp = _qapp
PERSONAL = "https://personal.webex.com/meet/my-room"
ROOM = "https://meet.google.com/abc-defg-hij"


@pytest.fixture(autouse=True)
def callback_errors(monkeypatch, caplog):
    errors = []

    def capture(kind, _error, _traceback):
        errors.append(kind.__name__)

    monkeypatch.setattr(sys, "excepthook", capture)
    monkeypatch.setattr(app_module, "_report_unhandled_exception", capture)
    yield
    assert not errors
    ui_errors = sum(
        record.name == "webjam.ui_thread" and record.levelno >= logging.ERROR
        for phase in ("setup", "call", "teardown")
        for record in caplog.get_records(phase)
    )
    assert ui_errors == 0


@pytest.fixture
def copied_room(qapp, monkeypatch, tmp_path):
    made = []
    widgets = []
    guards = []
    monkeypatch.setattr(ApplicationController, "_start_webex_app_detection", lambda self: False)
    monkeypatch.setattr("webjam_qt.windows.launch_dialog._windows_jamulus_installer", lambda *a: None)
    monkeypatch.setattr("webjam_qt.controllers.session_persistence._persistence_home", lambda: tmp_path)
    monkeypatch.setenv("WEBJAM_SMOKE_AUTOSTART_AUDIO", "0")
    # Music's idle HUD reads this status. Supply an unknown result without
    # consulting the OS or implying permission/device readiness.
    monkeypatch.setattr("webjam_qt.platform_permissions.microphone_permission_status", lambda: "unavailable")
    # The real Studio picker and metering-engine import otherwise discover
    # PortAudio through sounddevice (including ldconfig on Linux). This test
    # has no audio device: exercise the existing optional-dependency fallback
    # without library discovery, while keeping process and media guards intact.
    monkeypatch.setitem(sys.modules, "sounddevice", None)

    for target in (
        "subprocess.Popen",
        "socket.create_connection",
        "webbrowser.open",
        "PySide6.QtGui.QDesktopServices.openUrl",
        "webex_integration.open_webex_meeting",
        "core.session_transfer_runtime.GuestPeerSession.start",
        "core.audio_engine.RealAudioEngine.start",
    ):
        guard = Mock(name=target, side_effect=AssertionError("No machine or media action in this journey"))
        monkeypatch.setattr(target, guard)
        guards.append(guard)
    monkeypatch.setattr(QMessageBox, "information", Mock(return_value=QMessageBox.StandardButton.Ok))

    def settings(name, *, host=False):
        root = tmp_path / name
        root.mkdir()
        result = AppSettings(
            config_file=str(root / "settings.json"), takes_directory=str(root / "takes"),
            log_file=str(root / "webjam.log"), musician_name=name,
            last_creator_profile_key="music", host_server_enabled=host,
            webex_url=PERSONAL,
        )
        save_settings(result)
        return result

    def run(meeting, inspect):
        host_settings = settings("Host", host=True)
        host_window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Music rehearsal",
        )
        host = ApplicationController(host_window, settings=host_settings)
        made.append(host)
        widgets.append(host_window)
        # A host's current room meeting may differ from its saved preference.
        # Copy must continue using #108's effective room context after #109.
        host._set_session_meeting_url(meeting)
        invite = invitation()
        link = create_invite_link(
            invite.host, session_id=invite.session_id, peer_port=invite.peer_port,
            invite_token=invite.invite_token, session_name="Music rehearsal",
        )
        host._host_share_readiness = Mock(return_value=SimpleNamespace(
            shareable=True, address=invite.host,
        ))
        host._current_invite_url = Mock(return_value=link)
        host.window.flash_message = Mock()
        clipboard = Mock()
        monkeypatch.setattr(QApplication, "clipboard", lambda: clipboard)
        host._copy_band_invite()
        assert clipboard.setText.call_count == 1
        message = clipboard.setText.call_args.args[0]
        exact_link_count = message.splitlines().count(link)
        assert exact_link_count == 1
        instructions_present = (
            "choose Join, then paste this full invitation" in message
            and "same Wi-Fi or local network" in message
            and "host needs to keep this room open" in message
            and "Open the link in WebJam" not in message
        )
        assert instructions_present
        room_link_preserved = (meeting in message) if meeting else ("Optional video chat" not in message)
        assert room_link_preserved
        personal_link_omitted = PERSONAL not in message
        assert personal_link_omitted
        feedback = host.window.flash_message.call_args.args[0]
        feedback_honest = all(part in feedback for part in (
            "whole message", "same Wi-Fi or local network", "Keep this room open",
        ))
        assert feedback_honest

        guest_settings = settings("Guest")
        door = LaunchDialog(guest_settings)
        widgets.append(door)
        arm_lan(monkeypatch, invite, profile="music")
        scheduled = []
        captured = []
        qt_loop = MagicMock()

        def accept_copy():
            door.show_join()
            door._invite_input.setText(message)
            door._join_button_primary.click()
            assert door.result() == QDialog.DialogCode.Accepted
            assert door.selected_role == "join"
            empty_input = door._invite_input.text() == ""
            assert empty_input
            exact_invite = (
                door.band_invite.session_id == invite.session_id
                and door.band_invite.invite_token == invite.invite_token
                and door.band_invite.host == invite.host
                and door.band_invite.peer_port == invite.peer_port
            )
            assert exact_invite
            accepted_meeting = door.invitation_meeting_url == meeting
            assert accepted_meeting
            return door.result()

        def build_controller(window, **kwargs):
            app = ApplicationController(window, **kwargs)
            made.append(app)
            widgets.append(window)
            app.start_companion_api = Mock()
            app.start_desktop_integrations = Mock()
            launch = Mock(name="native startup boundary")
            app._launch_native_jamulus_for_startup = launch
            for owner, name in (
                (app, "_start_hosted_server_for_startup"),
                (app.bridge, "launch_webex"), (app.bridge, "launch_jamulus"),
                (app, "_play_reference_track"), (app.recording, "on_record_requested"),
            ):
                guard = Mock(name=name, side_effect=AssertionError("No automatic provider or media launch"))
                monkeypatch.setattr(owner, name, guard)
                guards.append(guard)
            for timer in app.findChildren(QTimer):
                timer.stop()
            captured.append(SimpleNamespace(app=app, launch=launch, kwargs=kwargs))
            return app

        constructor = Mock(side_effect=build_controller)
        constructor.mode_entries = ApplicationController.mode_entries
        door_factory = Mock(return_value=door)
        door_factory.DialogCode = QDialog.DialogCode

        def inside_event_loop():
            assert len(captured) == 1
            pair = captured[0]
            app = pair.app
            passed_meeting = pair.kwargs["session_meeting_url"] == meeting
            assert passed_meeting
            assert pair.kwargs["session_invite"] is door.band_invite
            bootstrap_forgot_meeting = door.invitation_meeting_url == ""
            assert bootstrap_forgot_meeting
            assert pair.launch.call_count == 0
            _assert_conversation(app, meeting)
            pair.settings_bytes = Path(app.settings.config_file).read_bytes()
            assert len(scheduled) == 1
            # Execute the actual startup callback selected by _run_app. The
            # observer sees a controlled typed Music state, not a seeded flag.
            scheduled.pop()()
            observer = app._room_participant.lan_guest
            assert isinstance(observer, LanRoomGuest)
            pair.observer_generation = app._room_participant.generation
            old_callback = observer._on_state
            received_state = observer.poll_once()
            assert isinstance(received_state, SessionStateSnapshot)
            drain(qapp, lambda: pair.launch.call_count == 1)
            assert observer._stop.is_set()
            assert app._room_participant.lan_guest is None
            assert isinstance(app.guest_peer, GuestPeerSession)
            assert app.creator_profile.key == "music"
            assert not app._jamulus_connected
            assert not app.audio.connected
            pair.observer, pair.old_state = observer, received_state
            pair.old_callback = old_callback
            pair.peer = app.guest_peer
            _assert_conversation(app, meeting)
            inspect(pair)
            for guard in guards:
                assert guard.call_count == 0
            return 0

        qt_loop.exec.side_effect = inside_event_loop
        with monkeypatch.context() as boot:
            boot.setattr(sys, "argv", ["WebJam"])
            boot.setattr(app_module, "configure_logging", lambda _settings: None)
            boot.setattr(app_module, "load_settings", lambda path=None: load_settings(path or guest_settings.config_file))
            boot.setattr(app_module, "load_stylesheet", lambda: "")
            boot.setattr(app_module, "QApplication", SimpleNamespace(instance=lambda: qt_loop))
            boot.setattr(app_module, "QTimer", SimpleNamespace(singleShot=lambda _delay, action: scheduled.append(action)))
            boot.setattr(app_module, "LaunchDialog", door_factory)
            boot.setattr(app_module, "ApplicationController", constructor)
            boot.setattr(door, "exec", accept_copy)
            assert app_module._run_app() == 0
        assert captured[0].app._shutdown

    yield run
    for app in reversed(made):
        if app.audio.stopping:
            drain(qapp, lambda: not app.audio.stopping)
        if app.audio.cleanup_retry_required:
            app.audio.retry_stop()
            drain(qapp, lambda: not app.audio.stopping)
        assert app.shutdown()
    for widget in reversed(widgets):
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
        assert not shiboken6.isValid(widget)
    qapp.processEvents()
    for guard in guards:
        assert guard.call_count == 0


def _assert_conversation(app, meeting):
    matched = app._effective_meeting_url() == meeting and app.webex.meeting_url == meeting
    assert matched
    assert app.window.webex_embed._meeting_configured is bool(meeting)
    if meeting:
        assert app.window.webex_embed._service_label == "Google Meet"
    personal_unchanged = (
        app.settings.webex_url == PERSONAL
        and load_settings(app.settings.config_file).webex_url == PERSONAL
    )
    assert personal_unchanged


@pytest.mark.parametrize("meeting", [ROOM, ""], ids=["invited-meeting", "no-meeting"])
def test_copied_music_invitation_survives_cold_bootstrap_and_music_handoff(copied_room, meeting):
    def inspect(pair):
        unchanged = Path(pair.app.settings.config_file).read_bytes() == pair.settings_bytes
        assert unchanged
        assert pair.app.bridge.webex_state == "Not opened"

    copied_room(meeting, inspect)


def test_failed_music_leave_retains_meeting_until_owned_cleanup_retry(copied_room, qapp, monkeypatch):
    def inspect(pair):
        app, peer = pair.app, pair.peer
        real_stop = peer.stop
        attempts = []

        def stop_once_then_real():
            attempts.append(True)
            return False if len(attempts) == 1 else real_stop()

        monkeypatch.setattr(peer, "stop", stop_once_then_real)
        app.audio._begin_session_stop(False, art_room=False)
        drain(qapp, lambda: not app.audio.stopping)
        assert app.audio.cleanup_retry_required
        assert app.guest_peer is peer
        assert app._guest_invite is not None
        assert len(attempts) == 1
        _assert_conversation(app, ROOM)
        app.audio.retry_stop()
        drain(qapp, lambda: not app.audio.stopping)
        assert not app.audio.cleanup_retry_required
        assert len(attempts) == 2
        assert peer._stop_event.is_set() and peer._thread is None
        assert app.guest_peer is None and app._guest_invite is None
        restored = app._effective_meeting_url() == PERSONAL and app.webex.meeting_url == PERSONAL
        assert restored
        assert app.bridge.webex_state == "Not opened"
        pair.old_callback(pair.observer, pair.old_state)
        qapp.processEvents()
        still_personal = app._effective_meeting_url() == PERSONAL
        assert still_personal
        unchanged = Path(app.settings.config_file).read_bytes() == pair.settings_bytes
        assert unchanged
        assert pair.launch.call_count == 1

    copied_room(ROOM, inspect)
