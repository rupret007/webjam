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
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402
from shiboken6 import isValid  # noqa: E402


_LOGICAL_HOST = "192.168.71.20"


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

    def _door(self, name, start):
        from core.settings import AppSettings
        from webjam_qt.windows.launch_dialog import LaunchDialog

        root = self.root / name
        root.mkdir()
        settings = AppSettings(
            config_file=str(root / "settings.json"),
            takes_directory=str(root / "takes"), log_file=str(root / "app.log"),
            mix_file=str(root / "mix.json"),
            musician_name=name, last_creator_profile_key="art",
            last_creator_start_key=start,
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

    def _host(self, start):
        door = self._door("Host Artist", start)
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
        door = self._door("Guest Artist", start)
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
        host._room_participant.tick()
        self.assertIs(host._room_participant.state, ArtRoomState.CONNECTED)
        self.assertEqual(len(server.room_participants()), 1)
        names = host._room_participant.host_connection_names()
        self.assertTrue(names is not None and names.names == ("Guest Artist",), "Only the authenticated reader should appear")
        self.assertTrue(owner.connection_available)
        self.assertIsNone(guest.guest_peer, "Art must not construct a Music capture owner")
        self.assertFalse(guest._jamulus_connected)

        guest._on_conductor_action_requested("end_session")
        self._drain(lambda: not guest.audio.stopping and guest._room_participant.lan_guest is None, "Leave Room did not release the actual guest owner")
        self.assertTrue(owner._stop.is_set())
        self.assertFalse(owner._thread.is_alive())
        self.assertIsNone(guest._guest_invite)
        self.assertTrue(host.host_peer.active, "Guest Leave must not stop the host")
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
