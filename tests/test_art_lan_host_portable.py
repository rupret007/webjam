"""Desktop dependency smoke: real Art owners and authenticated loopback HTTP.

Run with ``python -m unittest tests.test_art_lan_host_portable -v``. This
module deliberately imports no pytest helpers: desktop packaging environments
have the application dependencies, not pytest. It proves source runtime on the
actual runner OS, not a frozen package, physical LAN reachability, or media.
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402
from shiboken6 import isValid  # noqa: E402


_LOGICAL_HOST = "192.168.71.20"
_PERSONAL_MEETING = "https://personal.webex.com/meet/my-room"
_ROOM_MEETING = "https://meet.google.com/abc-defg-hij"


class _UiErrors(logging.Handler):
    def __init__(self):
        super().__init__(logging.ERROR)
        self.count = 0

    def emit(self, record):
        self.count += 1


class ArtLanHostPortableTest(unittest.TestCase):
    """Keep runtime ownership real; replace only external/device boundaries."""

    @classmethod
    def setUpClass(cls):
        # Python may query Windows version metadata through a subprocess on
        # its first uname(). Warm the real OS identity before application
        # guards; subsequent component checks still use this runner's actual
        # architecture, with every application process launch forbidden.
        platform.uname()
        cls.qapp = QApplication.instance() or QApplication([])
        cls.qapp.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="webjam-art-lan-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.controllers = []
        self.doors = []
        self.servers = []
        self.workers = []
        self.guards = {}
        self.qt_errors = []
        self.thread_errors = []
        self.address = _LOGICAL_HOST
        self.fail_next_bind = False
        self.listener_errors = []
        self.clipboard = Mock(name="private_test_clipboard")
        self.addCleanup(self._cleanup)

        # Optional PortAudio discovery itself runs ldconfig on Linux. Treat the
        # optional dependency as unavailable before constructing any windows;
        # retain explicit engine/process guards below, rather than permitting it.
        missing = object()
        previous_sounddevice = sys.modules.get("sounddevice", missing)
        sys.modules["sounddevice"] = None

        def restore_sounddevice():
            if previous_sounddevice is missing:
                sys.modules.pop("sounddevice", None)
            else:
                sys.modules["sounddevice"] = previous_sounddevice

        self.patches.callback(restore_sounddevice)
        self.patches.enter_context(patch.dict(os.environ, {
            "WEBJAM_ENABLE_REFERENCE_LOCAL": "0",
        }))
        self.patches.enter_context(patch(
            "sys.excepthook", lambda kind, value, trace: self.qt_errors.append(kind.__name__),
        ))
        self.patches.enter_context(patch(
            "threading.excepthook", lambda args: self.thread_errors.append(args.exc_type.__name__),
        ))
        app_logger = logging.getLogger("webjam")
        previous_handlers = tuple(app_logger.handlers)
        previous_level, previous_propagate = app_logger.level, app_logger.propagate
        for handler in previous_handlers:
            app_logger.removeHandler(handler)

        def restore_logging():
            # Windows cannot remove the temporary tree while its log is open.
            for handler in tuple(app_logger.handlers):
                app_logger.removeHandler(handler)
                handler.close()
            for handler in previous_handlers:
                app_logger.addHandler(handler)
            app_logger.setLevel(previous_level)
            app_logger.propagate = previous_propagate

        self.patches.callback(restore_logging)
        self.ui_errors = _UiErrors()
        logger = logging.getLogger("webjam.ui_thread")
        logger.addHandler(self.ui_errors)
        self.patches.callback(logger.removeHandler, self.ui_errors)

        from core.provider_credentials import PROVIDERS
        from core.secret_store import NoSecretStore, set_default_secret_store

        environment = dict(os.environ)
        for provider in PROVIDERS.values():
            for variable in provider.env_vars:
                environment.pop(variable, None)
        self.patches.enter_context(patch.dict(os.environ, environment, clear=True))
        set_default_secret_store(NoSecretStore("No credentials in desktop smoke."))
        self.patches.callback(set_default_secret_store, None)

        for target in (
            "subprocess.Popen", "webbrowser.open",
            "PySide6.QtGui.QDesktopServices.openUrl",
        ):
            self._deny(target)

        from core import jamulus_rpc_client, session_transfer_runtime
        from core.session_transfer import SessionPeerClient, SessionPeerServer
        from core.settings import AppSettings
        from services import bridge_service
        from webjam_qt.controllers import application_controller, session_persistence

        self.controller_type = application_controller.ApplicationController
        repository_type = application_controller.WebJamRepository
        repositories = [0]

        def repository(db_path=None):
            repositories[0] += 1
            return repository_type(str(self.root / f"repository-{repositories[0]}.db"))

        self.patches.enter_context(patch.object(application_controller, "WebJamRepository", repository))
        persistence = self.root / "notes"
        persistence.mkdir()
        self.patches.enter_context(patch.object(session_persistence, "_persistence_home", lambda: persistence))
        for module in (jamulus_rpc_client, bridge_service):
            self.patches.enter_context(patch.object(module, "DEFAULT_SECRET_PATH", self.root / "rpc.secret"))
        self.patches.enter_context(patch.object(bridge_service.BridgeService, "_runtime_home", lambda _: self.root))
        # Host defaults and the independent adapters have their own normal
        # path/settings factories; isolate those too, before any real window.
        for module in ("core.settings", "webjam_qt.windows.launch_dialog"):
            self.patches.enter_context(patch(module + ".hosted_server_recordings_dir", return_value=self.root / "host-takes"))
            self.patches.enter_context(patch(module + ".hosted_server_secret_path", return_value=self.root / "host.secret"))
        for module in ("jamulus_controller", "webex_integration"):
            self.patches.enter_context(patch(module + ".load_settings", side_effect=lambda: AppSettings(
                config_file=str(self.root / "adapter-settings.json"),
                takes_directory=str(self.root / "adapter-takes"),
                mix_file=str(self.root / "adapter-mix.json"),
                log_file=str(self.root / "adapter.log"),
            )))
        self.patches.enter_context(patch("core.network_invite.local_band_address", lambda: self.address))
        self.patches.enter_context(patch.object(self.controller_type, "_start_routing_scan", lambda _: None))
        self.patches.enter_context(patch.object(self.controller_type, "_start_webex_app_detection", lambda _: False))
        self.patches.enter_context(patch("webjam_qt.platform_permissions.microphone_permission_status", return_value="unavailable"))
        self.patches.enter_context(patch("webjam_qt.windows.launch_dialog._windows_jamulus_installer", return_value=None))
        self.patches.enter_context(patch.object(QApplication, "clipboard", return_value=self.clipboard))
        self.patches.enter_context(patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes))
        self.patches.enter_context(patch("services.drawpile_service.DrawpileLauncher.available", return_value=False))

        for target in (
            "api.local_bridge.LocalApiBridge.start",
            "jamulus_controller.JamulusController.start",
            "services.bridge_service.BridgeService.launch_jamulus",
            "services.bridge_service.BridgeService.launch_webex",
            "core.audio_engine.RealAudioEngine.start",
            "services.drawpile_service.DrawpileLauncher.open_host_page",
            "services.drawpile_service.DrawpileLauncher.open_canvas",
            "webjam_qt.widgets.reference_video_player.create_qt_reference_video_player",
            "webjam_qt.controllers.application_controller.GuestPeerSession",
            "webjam_qt.controllers.application_controller.ApplicationController._begin_remote_host",
            "webjam_qt.controllers.application_controller.ApplicationController._launch_native_jamulus_for_startup",
            "webjam_qt.controllers.application_controller.ApplicationController._start_hosted_server_for_startup",
            "webjam_qt.controllers.recording_coordinator.RecordingCoordinator._start_local_capture",
        ):
            self._deny(target)

        test = self

        class LoopbackServer(SessionPeerServer):
            def __init__(self, host, port, **kwargs):
                if host != _LOGICAL_HOST or port != 0:
                    raise AssertionError("Unexpected logical listener destination")
                if test.fail_next_bind:
                    test.fail_next_bind = False
                    raise OSError("Controlled listener bind failure")
                try:
                    super().__init__("127.0.0.1", 0, **kwargs)
                except Exception as error:
                    test.listener_errors.append(type(error).__name__)
                    raise
                test.servers.append(self)

            @property
            def address(self):
                return _LOGICAL_HOST, self._httpd.server_address[1]

        self.patches.enter_context(patch.object(session_transfer_runtime, "SessionPeerServer", LoopbackServer))

        def routed_client(host, port, **kwargs):
            known = any(server.address[1] == port for server in self.servers)
            if host != _LOGICAL_HOST or not known:
                raise AssertionError("Unexpected invitation destination")
            return SessionPeerClient("127.0.0.1", port, **kwargs)

        self.patches.enter_context(patch("services.lan_room_guest.SessionPeerClient", routed_client))
        original_connection = socket.create_connection

        def connect(address, *args, **kwargs):
            known = any(server.address[1] == address[1] for server in self.servers)
            if address[0] != "127.0.0.1" or not known:
                self.guards["unexpected_network"].append(True)
                raise AssertionError("Only the owned loopback listener is allowed")
            return original_connection(address, *args, **kwargs)

        self.guards["unexpected_network"] = []
        self.patches.enter_context(patch("socket.create_connection", connect))

    def _deny(self, target):
        guard = Mock(name=target, side_effect=AssertionError("Forbidden external effect"))
        self.guards[target] = guard
        self.patches.enter_context(patch(target, guard))

    def _drain(self, predicate, message):
        deadline = time.monotonic() + 5.0
        while not predicate() and time.monotonic() < deadline:
            self.qapp.processEvents()
            time.sleep(0.005)
        self.qapp.processEvents()
        self.assertTrue(bool(predicate()), message)

    def _cleanup(self):
        failures = []
        for app in reversed(self.controllers):
            try:
                self._drain(lambda: not app.audio.stopping, "Room cleanup worker did not finish")
                if app.shutdown() is not True:
                    failures.append("Controller shutdown was not confirmed")
            except Exception as error:
                failures.append("Controller cleanup: " + type(error).__name__)
        # Even a failed assertion/stop must not leave a daemon listener behind.
        for server in reversed(self.servers):
            try:
                server.stop()
            except Exception as error:
                failures.append("Listener cleanup: " + type(error).__name__)
        for app in reversed(self.controllers):
            try:
                if app.host_peer.stop() is not True:
                    failures.append("Host worker cleanup was not confirmed")
                window = app.window
                window.deleteLater()
                QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
                if isValid(window):
                    failures.append("Window remained alive")
            except Exception as error:
                failures.append("Window cleanup: " + type(error).__name__)
        for door in self.doors:
            if isValid(door):
                door.deleteLater()
        self.qapp.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for worker in self.workers:
            if worker is not None and worker.is_alive():
                failures.append("Owned worker remained alive")
        for name, guard in self.guards.items():
            count = len(guard) if isinstance(guard, list) else guard.call_count
            if count:
                failures.append(f"Forbidden boundary {name}: {count}")
        if self.qt_errors or self.thread_errors or self.ui_errors.count:
            failures.append("An asynchronous callback raised")
        self.assertEqual(failures, [])

    def _door(self, name, start, *, meeting="", profile="art"):
        from core.settings import AppSettings
        from webjam_qt.windows.launch_dialog import LaunchDialog

        root = self.root / name
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"),
            takes_directory=str(root / "takes"), log_file=str(root / "app.log"),
            mix_file=str(root / "mix.json"),
            musician_name=name, last_creator_profile_key=profile,
            last_creator_start_key=start, webex_url=meeting,
        )
        door = LaunchDialog(settings)
        self.doors.append(door)
        door.show()
        self.qapp.processEvents()
        return door

    def _controller(self, door):
        from webjam_qt.windows.conductor_window import ConductorWindow

        window = ConductorWindow(
            mode_entries=self.controller_type.mode_entries(),
            initial_mode_key="music_jam", initial_title="Making room",
        )
        app = self.controller_type(window, settings=door._settings, session_invite=door.band_invite)
        self.controllers.append(app)
        window.show()
        self.qapp.processEvents()
        return app

    def _host(self, start, *, meeting=""):
        door = self._door("Host Artist", start, meeting=meeting)
        self.assertTrue(door._host_button.isEnabled(), "Art LAN Host must be available on this desktop")
        door._host_button.click()
        self.assertEqual(door.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(door.selected_role, "host")
        self.assertEqual(door._settings.last_creator_start_key, start)
        app = self._controller(door)
        self.assertTrue(app.begin_startup_journey())
        app._room_participant.tick()
        return app

    def _assert_waiting(self, host):
        from core.session_conductor import ArtRoomState, SessionConductorPhase
        from core.session_transfer_runtime import HostPeerSession

        self.assertIsInstance(host.host_peer, HostPeerSession)
        self.assertTrue(host.host_peer.active, "Host start failed: " + ",".join(self.listener_errors))
        self.assertIs(host._room_participant.state, ArtRoomState.WAITING)
        self.assertIs(host._last_session_conductor.phase, SessionConductorPhase.INVITE_READY)
        self.assertEqual(len(host.host_peer.server.room_participants()), 0)
        self.assertFalse(host._jamulus_connected)
        self.assertEqual(host.host_peer.control.snapshot().art_start_key, host.creator_start.key)

    def _join_and_leave(self, host, start):
        from core.session_conductor import ArtRoomState
        from core.session_transfer import SessionCredentials, SessionPeerClient, TransferAuthenticationError

        server = host.host_peer.server
        self.workers.extend((host.host_peer._thread, server._thread))
        # Enrollment is not presence. A wrong authenticated state request may
        # not manufacture a connected reader either.
        probe = SessionPeerClient("127.0.0.1", server.address[1], credentials=host.host_peer.credentials, timeout_s=1)
        enrolled = probe.enroll(str(uuid.uuid4()), "Enrollment Probe")
        self.assertEqual(len(server.room_participants()), 0)
        invalid = replace(enrolled, participant_token=SessionCredentials.create().participant_token(enrolled.participant_id))
        with self.assertRaises(TransferAuthenticationError):
            probe.state(invalid)
        self.assertEqual(len(server.room_participants()), 0)

        host._copy_band_invite()
        self.assertEqual(self.clipboard.setText.call_count, 1)
        message = self.clipboard.setText.call_args.args[0]
        door = self._door("Guest Artist", start, meeting=_PERSONAL_MEETING)
        door.show_join()
        door._invite_input.setText(message)
        door._join_button_primary.click()
        self.assertEqual(door.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(door.selected_role, "join")
        self.assertTrue(door._invite_input.text() == "", "Private paste was not cleared")
        guest = self._controller(door)
        self.assertTrue(guest.begin_startup_journey())
        owner = guest._room_participant.lan_guest
        self.assertIsNotNone(owner)
        self.workers.append(owner._thread)
        self._drain(lambda: guest._room_participant.state is ArtRoomState.CONNECTED, "Authenticated guest did not enter the Art room")
        # No meeting in the invite must suppress an unrelated saved URL until
        # Leave. This also retains the three original no-meeting controls.
        self._assert_guest_meeting(guest, "")
        self._assert_connected_pair(host, guest, owner, server)
        self._leave_pair(host, guest, owner, server)

    def _assert_connected_pair(self, host, guest, owner, server):
        from core.session_conductor import ArtRoomState

        host._room_participant.tick()
        self.assertIs(host._room_participant.state, ArtRoomState.CONNECTED)
        self.assertEqual(len(server.room_participants()), 1)
        names = host._room_participant.host_connection_names()
        self.assertTrue(names is not None and names.names == ("Guest Artist",), "Only the authenticated reader should appear")
        self.assertTrue(owner.connection_available)
        self.assertEqual(owner.last_state.art_start_key, host.creator_start.key)
        self.assertIsNone(guest.guest_peer, "Art must not construct a Music capture owner")
        self.assertFalse(guest._jamulus_connected)

    def _assert_guest_meeting(self, guest, meeting):
        from core.settings import load_settings

        self.assertTrue(guest._effective_meeting_url() == meeting, "Wrong room Conversation")
        self.assertTrue(guest.webex.meeting_url == meeting, "Wrong meeting adapter context")
        self.assertEqual(guest.window.webex_embed._meeting_configured, bool(meeting))
        self.assertEqual(guest.bridge.webex_state, "Not opened")
        self.assertTrue(guest.settings.webex_url == _PERSONAL_MEETING, "Saved preference changed in memory")
        self.assertTrue(load_settings(guest.settings.config_file).webex_url == _PERSONAL_MEETING, "Saved preference changed on disk")

    def _leave_pair(self, host, guest, owner, server, *, after_leave=None):
        guest._on_conductor_action_requested("end_session")
        self._drain(lambda: not guest.audio.stopping and guest._room_participant.lan_guest is None, "Leave Room did not release the actual guest owner")
        self.assertTrue(owner._stop.is_set())
        self.assertFalse(owner._thread.is_alive())
        self.assertIsNone(guest._guest_invite)
        self.assertTrue(host.host_peer.active, "Guest Leave must not stop the host")
        self._assert_guest_meeting(guest, _PERSONAL_MEETING)
        if after_leave is not None:
            after_leave()
        host._on_conductor_action_requested("end_session")
        self._drain(lambda: not host.audio.stopping and not host.host_peer.active, "End Room did not release the actual host owner")
        self.assertEqual(server.active_handler_count, 0)
        self.assertEqual(server._httpd.socket.fileno(), -1)
        self.assertEqual(len(server.room_participants()), 0)
        self.assertTrue(guest.shutdown())
        self.assertTrue(host.shutdown())

    def test_make_together_host_copy_join_and_leave(self):
        host = self._host("talk_and_make")
        self._assert_waiting(host)
        self._join_and_leave(host, "talk_and_make")

    def test_paint_along_host_copy_join_and_leave_without_opening_media(self):
        host = self._host("paint_along")
        self._assert_waiting(host)
        self._join_and_leave(host, "paint_along")

    def test_full_art_invite_bootstrap_keeps_conversation_temporary_and_notes_local(self):
        from core.session_conductor import ArtRoomState
        from core.settings import load_settings, save_settings
        from webjam_qt import app as app_module

        # Both in-process windows use the same isolated local Notes root. A
        # shared sentinel tests preservation without pretending two accounts
        # have separate global persistence factories in this process.
        note = "Local clay study notes stay on this computer."
        music_note = "Local rehearsal notes stay separate."
        notes_path = self.root / "notes" / ".webjam_notes.art.md"
        notes_path.write_text(note, encoding="utf-8")
        original_notes = notes_path.read_bytes()
        music_notes_path = self.root / "notes" / ".webjam_notes.md"
        music_notes_path.write_text(music_note, encoding="utf-8")
        original_music_notes = music_notes_path.read_bytes()
        host = self._host("talk_and_make", meeting=_ROOM_MEETING)
        self._assert_waiting(host)
        server = host.host_peer.server
        self.workers.extend((host.host_peer._thread, server._thread))
        host_settings = Path(host.settings.config_file).read_bytes()
        host._copy_band_invite()
        self.assertEqual(self.clipboard.setText.call_count, 1)
        message = self.clipboard.setText.call_args.args[0]
        self.assertTrue(_ROOM_MEETING in message and _PERSONAL_MEETING not in message, "Copy used the wrong Conversation")
        self.assertTrue(note not in message and music_note not in message, "Invitation exposed local Notes")
        self.assertEqual(sum(line.startswith("webjam://") for line in message.splitlines()), 1)
        self.assertTrue(all(text in message for text in (
            "choose Join, then paste this full invitation", "same Wi-Fi or local network",
            "host needs to keep this room open",
        )), "Complete invitation guidance is missing")

        # A person with the default Music workspace pastes once. Only the
        # authenticated host state may select Art; no Art card is clicked.
        door = self._door("Guest Artist", "", meeting=_PERSONAL_MEETING, profile="music")
        save_settings(door._settings)
        guest_config = door._settings.config_file
        scheduled, captured = [], []

        def accept_copy():
            door.show_join()
            door._invite_input.setText(message)
            door._join_button_primary.click()
            self.assertEqual(door.result(), QDialog.DialogCode.Accepted)
            self.assertEqual(door.selected_role, "join")
            self.assertEqual(door.selected_creator_profile_key, "music")
            self.assertTrue(door._invite_input.text() == "", "Private paste was not cleared")
            self.assertTrue(door.invitation_meeting_url == _ROOM_MEETING, "Entry lost the optional Conversation")
            invite = door.band_invite
            self.assertTrue(invite is not None and (
                invite.session_id == host.host_peer.credentials.session_id
                and invite.invite_token == host.host_peer.credentials.invite_token
                and invite.host == _LOGICAL_HOST and invite.peer_port == server.address[1]
            ), "Entry changed the opaque room invitation")
            return door.result()

        def build_controller(window, **kwargs):
            self.assertTrue(kwargs["session_invite"] is door.band_invite, "Bootstrap changed the typed invitation")
            self.assertTrue(kwargs["session_meeting_url"] == _ROOM_MEETING, "Bootstrap dropped the room Conversation")
            guest = self.controller_type(window, **kwargs)
            self.controllers.append(guest)
            # Update/desktop-integration startup is unrelated to joining; its
            # lower provider/media/process guards remain installed unchanged.
            guest.start_desktop_integrations = Mock()
            captured.append(guest)
            return guest

        def event_loop():
            self.assertEqual(len(captured), 1)
            self.assertEqual(len(scheduled), 1)
            guest = captured[0]
            self.assertTrue(door.invitation_meeting_url == "", "Bootstrap retained entry-only context")
            self.assertEqual(guest.creator_profile.key, "music")
            self._assert_guest_meeting(guest, _ROOM_MEETING)
            self.assertEqual(guest.window.webex_embed._service_label, "Google Meet")
            settings_bytes = Path(guest.settings.config_file).read_bytes()
            self.assertTrue(guest.window.session_canvas.current_notes() == music_note, "Entry replaced saved Music Notes")
            scheduled.pop()()  # The actual startup callback chosen by _run_app.
            owner = guest._room_participant.lan_guest
            self.assertIsNotNone(owner)
            self.workers.append(owner._thread)
            self._drain(lambda: guest._room_participant.state is ArtRoomState.CONNECTED, "Authenticated guest did not enter the Art room")
            self._assert_connected_pair(host, guest, owner, server)
            self.assertEqual(guest.creator_profile.key, "art")
            self.assertEqual(guest.settings.last_creator_profile_key, "music")
            self.assertTrue(guest.window.session_canvas.current_notes() == note, "Authenticated Art entry lost local Art Notes")
            self._assert_guest_meeting(guest, _ROOM_MEETING)
            old_callback, old_state = owner._on_state, owner.last_state

            def after_leave():
                old_callback(owner, old_state)
                self.qapp.processEvents()
                self._assert_guest_meeting(guest, _PERSONAL_MEETING)
                self.assertEqual(guest.creator_profile.key, "music")
                self.assertTrue(guest.window.session_canvas.current_notes() == music_note, "Leave did not restore local Music Notes")
                self.assertTrue(notes_path.read_bytes() == original_notes, "Notes file changed")
                self.assertTrue(music_notes_path.read_bytes() == original_music_notes, "Music Notes file changed")
                self.assertTrue(Path(guest.settings.config_file).read_bytes() == settings_bytes, "Join/Leave overwrote saved settings")

            self._leave_pair(host, guest, owner, server, after_leave=after_leave)
            self.assertTrue(notes_path.read_bytes() == original_notes, "Shutdown replaced local Notes")
            self.assertTrue(music_notes_path.read_bytes() == original_music_notes, "Shutdown replaced local Music Notes")
            self.assertTrue(Path(host.settings.config_file).read_bytes() == host_settings, "Guest changed host preferences")
            return 0

        class BootstrapLoop:
            def __getattr__(_self, name):
                return getattr(self.qapp, name)

            def exec(_self):
                return event_loop()

        constructor = Mock(side_effect=build_controller)
        constructor.mode_entries = self.controller_type.mode_entries
        door_factory = Mock(return_value=door)
        door_factory.DialogCode = QDialog.DialogCode
        with ExitStack() as bootstrap:
            bootstrap.enter_context(patch.object(sys, "argv", ["WebJam"]))
            bootstrap.enter_context(patch.dict(os.environ, {"WEBJAM_SMOKE_AUTOSTART_AUDIO": "0", "WEBJAM_COMPANION_API": "0"}))
            bootstrap.enter_context(patch.object(app_module, "load_settings", lambda path=None: load_settings(path or guest_config)))
            bootstrap.enter_context(patch.object(app_module, "QApplication", SimpleNamespace(instance=lambda: BootstrapLoop())))
            bootstrap.enter_context(patch.object(app_module, "QTimer", SimpleNamespace(singleShot=lambda _delay, action: scheduled.append(action))))
            bootstrap.enter_context(patch.object(app_module, "LaunchDialog", door_factory))
            bootstrap.enter_context(patch.object(app_module, "ApplicationController", constructor))
            bootstrap.enter_context(patch.object(app_module, "_report_unhandled_exception", lambda kind, _error, _trace: self.qt_errors.append(kind.__name__)))
            bootstrap.enter_context(patch.object(door, "exec", accept_copy))
            self.assertEqual(app_module._run_app(), 0)
        self.assertTrue(captured[0]._shutdown)

    def test_missing_address_then_bind_failure_retry_keeps_host_fail_closed(self):
        from core.session_conductor import ArtRoomState

        self.address = ""
        host = self._host("talk_and_make")
        self.assertFalse(host.host_peer.active)
        self.assertIs(host._room_participant.state, ArtRoomState.FAILED)
        host._copy_band_invite()
        self.assertEqual(self.clipboard.setText.call_count, 0)
        self.address = _LOGICAL_HOST
        self.fail_next_bind = True
        host._on_conductor_action_requested("retry_startup")
        self.assertFalse(host.host_peer.active)
        self.assertIs(host._room_participant.state, ArtRoomState.FAILED)
        self.assertFalse(self.fail_next_bind, "Retry must attempt the real host owner")
        host._copy_band_invite()
        self.assertEqual(self.clipboard.setText.call_count, 0)
        host._on_conductor_action_requested("retry_startup")
        host._room_participant.tick()
        self._assert_waiting(host)
        self._join_and_leave(host, "talk_and_make")


if __name__ == "__main__":
    unittest.main()
