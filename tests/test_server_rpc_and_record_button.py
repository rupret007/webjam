"""
Band-server RPC session + the Record button.

Unit-tests JamulusServerRpc against a fake NDJSON server (auth, recorder
calls, error surfaces, timeouts) and the Conductor's Record button wiring
(unconfigured guidance, worker success/failure paths, button state).
"""
from __future__ import annotations

import json
import os
import socket
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.jamulus_server_rpc import (  # noqa: E402
    JamulusServerRpc,
    ServerRpcError,
    read_secret_file,
)


class _FakeJamulusServer:
    """Minimal jamulusserver/* JSON-RPC endpoint for tests."""

    def __init__(self, secret="server-secret-0123456789"):
        self.secret = secret
        self.recorder_enabled = False
        self.received: list[dict] = []
        self.fail_method: str | None = None   # answer this method with an error
        self.mute_method: str | None = None   # never answer this method
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._conn = None
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        try:
            self._srv.settimeout(5.0)
            self._conn, _ = self._srv.accept()
        except OSError:
            return
        f = self._conn.makefile("r", encoding="utf-8", newline="\n")
        for line in f:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            self.received.append(obj)
            self._handle(obj)

    def _reply(self, obj):
        try:
            self._conn.sendall((json.dumps(obj) + "\n").encode())
        except OSError:
            pass

    def _handle(self, obj):
        method, rid = obj.get("method"), obj.get("id")
        if method == self.mute_method:
            return
        if method == self.fail_method:
            self._reply({"jsonrpc": "2.0", "id": rid,
                         "error": {"code": -1, "message": "nope"}})
            return
        if method == "jamulus/apiAuth":
            ok = (obj.get("params") or {}).get("secret") == self.secret
            self._reply({"jsonrpc": "2.0", "id": rid,
                         "result": "ok" if ok else "not ok"})
        elif method == "jamulusserver/startRecording":
            self.recorder_enabled = True
            self._reply({"jsonrpc": "2.0", "id": rid, "result": "acknowledged"})
        elif method == "jamulusserver/stopRecording":
            self.recorder_enabled = False
            self._reply({"jsonrpc": "2.0", "id": rid, "result": "acknowledged"})
        elif method == "jamulusserver/restartRecording":
            self._reply({"jsonrpc": "2.0", "id": rid, "result": "acknowledged"})
        elif method == "jamulusserver/getRecorderStatus":
            self._reply({"jsonrpc": "2.0", "id": rid, "result": {
                "initialised": True, "enabled": self.recorder_enabled,
                "recordingDirectory": "/recordings", "errorMessage": "",
            }})
        elif method == "jamulusserver/getClients":
            self._reply({"jsonrpc": "2.0", "id": rid, "result": {
                "connections": 2, "clients": [{"id": 0}, {"id": 1}],
            }})

    def stop(self):
        for s in (self._conn, self._srv):
            try:
                if s:
                    s.close()
            except OSError:
                pass


class TestJamulusServerRpc(unittest.TestCase):
    def setUp(self):
        self.fake = _FakeJamulusServer()

    def tearDown(self):
        self.fake.stop()

    def _rpc(self, secret=None):
        return JamulusServerRpc(
            port=self.fake.port, secret=secret or self.fake.secret,
        )

    def test_full_record_cycle(self):
        with self._rpc() as rpc:
            self.assertTrue(rpc.start_recording())
            self.assertTrue(rpc.get_recorder_status()["enabled"])
            self.assertTrue(rpc.restart_recording())
            self.assertTrue(rpc.stop_recording())
            self.assertFalse(rpc.get_recorder_status()["enabled"])

    def test_get_clients(self):
        with self._rpc() as rpc:
            result = rpc.get_clients()
        self.assertEqual(result["connections"], 2)

    def test_wrong_secret_raises_actionable_error(self):
        with self.assertRaises(ServerRpcError) as ctx:
            self._rpc(secret="wrong").connect()
        self.assertIn("refused the RPC secret", str(ctx.exception))

    def test_unreachable_port_covers_same_mac_and_remote_server(self):
        dead = JamulusServerRpc(port=1, secret="x")
        with self.assertRaises(ServerRpcError) as ctx:
            dead.connect()
        message = str(ctx.exception)
        self.assertIn("same-Mac server", message)
        self.assertIn("SSH tunnel", message)

    def test_error_response_raises(self):
        self.fake.fail_method = "jamulusserver/startRecording"
        with self._rpc() as rpc:
            with self.assertRaises(ServerRpcError) as ctx:
                rpc.start_recording()
        self.assertIn("nope", str(ctx.exception))

    def test_call_timeout_raises(self):
        self.fake.mute_method = "jamulusserver/getRecorderStatus"
        with self._rpc() as rpc:
            with patch.object(JamulusServerRpc, "CALL_TIMEOUT_S", 0.5):
                with self.assertRaises(ServerRpcError) as ctx:
                    rpc.get_recorder_status()
        self.assertIn("timed out", str(ctx.exception))

    def test_call_without_connect_raises(self):
        with self.assertRaises(ServerRpcError):
            self._rpc().start_recording()


class TestReadSecretFile(unittest.TestCase):
    def test_missing_file_is_actionable(self):
        with self.assertRaises(ServerRpcError) as ctx:
            read_secret_file("/nonexistent/jsonrpc.secret")
        self.assertIn("jsonrpc.secret", str(ctx.exception))

    def test_empty_file_rejected(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".secret") as f:
            f.flush()
            with self.assertRaises(ServerRpcError):
                read_secret_file(f.name)

    def test_reads_and_strips(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".secret",
                                         delete=False) as f:
            f.write("  the-secret \n")
        self.assertEqual(read_secret_file(f.name), "the-secret")


class TestRecordButtonWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])
        from core.settings import AppSettings
        from webjam_qt.controllers.application_controller import (
            ApplicationController,
        )
        from webjam_qt.windows.conductor_window import ConductorWindow
        cls.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Test",
        )
        cls.controller = ApplicationController(cls.window, settings=AppSettings())

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()

    def setUp(self):
        c = self.controller
        c.window.flash_message = MagicMock()
        c._recorder_armed = False
        c._server_recording = False
        c.recording.phase = c.recording.phase.__class__.IDLE
        c.recording._local_capture = None
        c.settings.local_capture_enabled = False
        c.settings.takes_directory = ""
        c.settings.server_rpc_secret_file = ""
        # A prior test may have left the button disabled mid-toggle.
        self.window.session_strip.set_recording_phase("idle")
        # The strip signal is wired to the real handler; with no secret file
        # configured it would open a MODAL error dialog and hang the suite —
        # mock it (tests that assert on it use this mock).
        c._show_actionable_error = MagicMock()

    def test_strip_button_emits_signal(self):
        received = []
        self.window.session_strip.record_requested.connect(
            lambda: received.append(True)
        )
        self.window.session_strip._record_button.click()
        self.assertEqual(received, [True])

    def test_unconfigured_shows_setup_instructions(self):
        c = self.controller
        c._on_record_requested()
        c._show_actionable_error.assert_called_once()
        self.assertEqual(
            c._show_actionable_error.call_args.args[0],
            "Record Button Not Set Up",
        )
        kwargs = c._show_actionable_error.call_args.kwargs
        self.assertIn("jsonrpc.secret", kwargs["next_action"])
        self.assertIn("Same Mac", kwargs["next_action"])
        self.assertIn("ssh -N -L", kwargs["next_action"])

    def test_configured_spawns_worker_toward_armed(self):
        c = self.controller
        c.settings.server_rpc_secret_file = "/tmp/secret"
        c._jamulus_connected = True
        c.participants = {1: SimpleNamespace(role="Guitar")}
        with patch.object(c, "_record_toggle_worker") as worker, \
             patch("webjam_qt.controllers.application_controller.threading.Thread",
                   side_effect=lambda *a, **kw: _Immediate(*a, **kw)):
            c._on_record_requested()
        worker.assert_called_once_with(True, "/tmp/secret")
        c._jamulus_connected = False
        c.participants = {}

    def test_record_preflight_requires_confirmed_participant(self):
        c = self.controller
        c.settings.server_rpc_secret_file = "/tmp/secret"
        c._jamulus_connected = False
        c.participants = {}
        with patch.object(c, "_record_toggle_worker") as worker:
            c._on_record_requested()
        worker.assert_not_called()
        self.assertEqual(c.recording.phase.value, "error")
        c._show_actionable_error.assert_called_once()

    def test_local_capture_starts_independently_of_talkback_mode(self):
        c = self.controller
        c.settings.webex_audio_mode = "talkback"
        c.settings.local_capture_enabled = True
        c.settings.takes_directory = "/tmp/takes"
        fake_capture = MagicMock()
        with patch("core.local_capture.LocalInputCapture", return_value=fake_capture):
            self.assertTrue(c.recording._start_local_capture())
        fake_capture.start.assert_called_once()
        self.assertIs(c.recording._local_capture, fake_capture)
        c.recording._local_capture = None
        c.settings.local_capture_enabled = False

    def test_local_capture_failure_blocks_server_start(self):
        c = self.controller
        c.settings.webex_audio_mode = "video_only"
        c.settings.local_capture_enabled = True
        c.settings.takes_directory = "/tmp/takes"
        with patch("core.local_capture.LocalInputCapture",
                   side_effect=RuntimeError("device busy")):
            self.assertFalse(c.recording._start_local_capture())
        self.assertEqual(c.recording.phase.value, "error")
        c._show_actionable_error.assert_called_once()
        c.settings.local_capture_enabled = False

    def test_audience_bridge_does_not_implicitly_enable_local_capture(self):
        c = self.controller
        c.settings.webex_audio_mode = "audience_bridge"
        c.settings.local_capture_enabled = False
        with patch("core.local_capture.LocalInputCapture") as capture_cls:
            self.assertTrue(c.recording._start_local_capture())
        capture_cls.assert_not_called()

    def test_worker_success_arms_button_and_flashes(self):
        c = self.controller
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.get_recorder_status.return_value = {"enabled": True}
        with patch("core.jamulus_server_rpc.JamulusServerRpc",
                   return_value=fake_rpc), \
             patch("core.jamulus_server_rpc.read_secret_file",
                   return_value="s3cret"), \
             patch.object(c._ui_invoker, "invoke",
                          side_effect=lambda fn: fn()):
            c._record_toggle_worker(True, "/tmp/secret")
        fake_rpc.start_recording.assert_called_once()
        self.assertTrue(c._recorder_armed)
        self.assertEqual(
            self.window.session_strip._record_button.text(), "■ Stop Rec"
        )
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("confirmed" in m for m in msgs), msgs)

    def test_worker_failure_offers_actionable_retry(self):
        c = self.controller
        with patch("core.jamulus_server_rpc.read_secret_file",
                   side_effect=ServerRpcError("Is the SSH tunnel up?")), \
             patch.object(c._ui_invoker, "invoke",
                          side_effect=lambda fn: fn()):
            c._record_toggle_worker(True, "/tmp/secret")
        self.assertFalse(c._recorder_armed)
        self.assertEqual(
            self.window.session_strip._record_button.text(), "Retry Record"
        )
        c._show_actionable_error.assert_called_once()
        args, kwargs = c._show_actionable_error.call_args
        self.assertEqual(args[0], "Recording Could Not Start")
        self.assertIn("SSH tunnel", kwargs["what_failed"])
        self.assertEqual(kwargs["retry_callback"], c.recording.on_record_requested)

    def test_shutdown_salvages_active_capture_into_recovery_folder(self):
        """Quitting mid-recording must preserve the stems, never abort them."""
        import tempfile
        from pathlib import Path

        c = self.controller
        with tempfile.TemporaryDirectory() as d:
            c.settings.takes_directory = d
            fake_capture = MagicMock()
            fake_capture.stop_into.return_value = SimpleNamespace(errors=())
            c.recording._local_capture = fake_capture
            c.recording.salvage_on_shutdown()
            fake_capture.stop_into.assert_called_once()
            dest = Path(fake_capture.stop_into.call_args.args[0])
            self.assertEqual(dest.parent, Path(d))
            self.assertTrue(dest.name.startswith("Recovered-"))
            fake_capture.abort.assert_not_called()
            self.assertIsNone(c.recording._local_capture)
            # Idempotent: a second salvage (closeEvent + app.py both call
            # shutdown) finds nothing to do.
            c.recording.salvage_on_shutdown()
            fake_capture.stop_into.assert_called_once()

    def test_take_local_capture_hand_off_is_atomic(self):
        """Validation worker and shutdown race for the capture; exactly one
        side may finalize it."""
        c = self.controller
        for _ in range(50):
            c.recording._local_capture = object()
            barrier = threading.Barrier(2)
            claimed = []

            def worker():
                barrier.wait()
                claimed.append(c.recording._take_local_capture())

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            winners = [item for item in claimed if item is not None]
            self.assertEqual(len(winners), 1)
        self.assertIsNone(c.recording._local_capture)

    def test_confirm_quit_idle_skips_dialog(self):
        c = self.controller
        with patch(
            "webjam_qt.controllers.recording_coordinator.QMessageBox"
        ) as mbox:
            self.assertTrue(c.recording.confirm_quit())
        mbox.assert_not_called()

    def test_confirm_quit_while_recording_defaults_to_cancel(self):
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        c.recording.phase = RecorderPhase.RECORDING
        with patch(
            "webjam_qt.controllers.recording_coordinator.QMessageBox"
        ) as mbox:
            box = mbox.return_value
            box.clickedButton.return_value = object()  # anything but Quit
            self.assertFalse(c.recording.confirm_quit())
            box.exec.assert_called_once()
            box.setDefaultButton.assert_called_once()
            # Quit clicked → the close proceeds.
            box.clickedButton.return_value = box.addButton.return_value
            self.assertTrue(c.recording.confirm_quit())
        self.assertIn(
            "keeps recording", mbox.return_value.setText.call_args.args[0]
        )

    def test_confirm_close_is_wired_to_confirm_quit(self):
        self.assertEqual(
            self.window.confirm_close, self.controller.recording.confirm_quit
        )

    def test_stop_audio_salvages_inflight_capture_and_resets_phase(self):
        import tempfile
        from pathlib import Path
        from PySide6.QtWidgets import QMessageBox
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        with tempfile.TemporaryDirectory() as d:
            c.settings.takes_directory = d
            fake_capture = MagicMock()
            fake_capture.stop_into.return_value = SimpleNamespace(errors=())
            c.recording._local_capture = fake_capture
            c.recording.phase = RecorderPhase.RECORDING
            c._recorder_armed = True
            c._server_recording = True
            c.bridge.stop_jamulus = MagicMock()
            with patch(
                "webjam_qt.controllers.audio_coordinator.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                c.audio.stop()
            fake_capture.stop_into.assert_called_once()
            dest = Path(fake_capture.stop_into.call_args.args[0])
            self.assertEqual(dest.parent, Path(d))
            self.assertTrue(dest.name.startswith("Recovered-"))
            self.assertEqual(c.recording.phase.value, "idle")
            self.assertFalse(c._recorder_armed)
            self.assertFalse(c._server_recording)
            strip = self.window.session_strip
            self.assertTrue(strip._record_elapsed.isHidden())
            self.assertEqual(strip._record_button.text(), "● Record")
            msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
            self.assertTrue(any("saved to" in m for m in msgs), msgs)

    def test_stop_audio_during_validation_does_not_steal_capture(self):
        from PySide6.QtWidgets import QMessageBox
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        fake_capture = MagicMock()
        c.recording._local_capture = fake_capture
        c.recording.phase = RecorderPhase.VALIDATING
        c.bridge.stop_jamulus = MagicMock()
        with patch(
            "webjam_qt.controllers.audio_coordinator.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            c.audio.stop()
        fake_capture.stop_into.assert_not_called()
        fake_capture.abort.assert_not_called()
        self.assertIs(c.recording._local_capture, fake_capture)
        self.assertEqual(c.recording.phase.value, "validating")

    def test_stop_audio_clears_stale_take_verified_chip(self):
        from PySide6.QtWidgets import QMessageBox
        from webjam_qt.controllers.recording_coordinator import RecorderPhase

        c = self.controller
        c.recording.phase = RecorderPhase.COMPLETE
        self.window.session_strip.set_recording_phase("complete")
        self.assertEqual(
            self.window.session_strip._record_elapsed.text(), "TAKE VERIFIED"
        )
        c.bridge.stop_jamulus = MagicMock()
        with patch(
            "webjam_qt.controllers.audio_coordinator.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            c.audio.stop()
        self.assertEqual(c.recording.phase.value, "idle")
        self.assertTrue(self.window.session_strip._record_elapsed.isHidden())

    def test_no_takes_dir_recovery_uses_recovered_prefix(self):
        from pathlib import Path

        c = self.controller
        fake_capture = MagicMock()
        fake_capture.stop_into.return_value = SimpleNamespace(errors=())
        c.recording._local_capture = fake_capture
        c.settings.takes_directory = ""
        try:
            c.recording._begin_take_validation()
            fake_capture.stop_into.assert_called_once()
            dest = Path(fake_capture.stop_into.call_args.args[0])
            self.assertTrue(dest.name.startswith("Recovered-"))
            self.assertEqual(dest.parent.name, "WebJam Recovered Takes")
            self.assertEqual(c.recording.phase.value, "needs_attention")
        finally:
            if c.recording._recovery_box is not None:
                c.recording._recovery_box.close()
                c.recording._recovery_box = None

    def test_outside_takes_dir_recovery_opens_persistent_box(self):
        c = self.controller
        fake_capture = MagicMock()
        fake_capture.stop_into.return_value = SimpleNamespace(errors=())
        c.recording._local_capture = fake_capture
        c.settings.takes_directory = ""
        try:
            c.recording._begin_take_validation()
            box = c.recording._recovery_box
            self.assertIsNotNone(box)
            self.assertTrue(box.isVisible())  # open(), not exec(): non-blocking
            self.assertIn("WebJam Recovered Takes", box.text())
            self.assertIn("Take Deck", box.text())
            labels = [button.text() for button in box.buttons()]
            self.assertIn("Reveal in Finder", labels)
        finally:
            if c.recording._recovery_box is not None:
                c.recording._recovery_box.close()
                c.recording._recovery_box = None

    def test_completion_copy_failure_states_folder_and_next_step(self):
        c = self.controller
        result = SimpleNamespace(
            ok=False,
            take=SimpleNamespace(path="/takes/Take01"),
            errors=("Expected at least 3 tracks but found 2.",),
            warnings=("guitar.wav appears silent.",),
            summary="",
        )
        title, body = c.recording._completion_text(result)
        self.assertEqual(title, "WebJam — Take needs attention")
        self.assertIn("preserved", body)
        self.assertIn("/takes/Take01", body)
        self.assertIn("Expected at least 3 tracks but found 2.", body)
        self.assertIn("guitar.wav appears silent.", body)
        self.assertIn("Take Deck", body)

    def test_completion_copy_no_take_points_to_ready_check(self):
        c = self.controller
        result = SimpleNamespace(
            ok=False,
            take=None,
            errors=("No new Jamulus take folder appeared after recording stopped.",),
            warnings=(),
            summary="",
        )
        title, body = c.recording._completion_text(result)
        self.assertEqual(title, "WebJam — Take needs attention")
        self.assertIn("Ready Check", body)
        self.assertNotIn("Saved to:", body)

    def test_completion_copy_success_unchanged(self):
        c = self.controller
        result = SimpleNamespace(
            ok=True, take=SimpleNamespace(path="/takes/Take01"),
            errors=(), warnings=(), summary="2 tracks · 1:04 · 48 kHz",
        )
        title, body = c.recording._completion_text(result)
        self.assertEqual(title, "WebJam — Recording complete")
        self.assertIn("Take saved · 2 tracks · 1:04 · 48 kHz", body)

    def test_validation_worker_posts_staged_progress(self):
        import tempfile
        from pathlib import Path

        c = self.controller
        strip = self.window.session_strip
        details: list[str] = []
        original = strip.set_recording_phase

        def spy(phase, detail=""):
            if phase == "validating" and detail:
                details.append(detail)
            original(phase, detail)

        with tempfile.TemporaryDirectory() as d:
            c.settings.takes_directory = d
            fake_result = SimpleNamespace(
                ok=True, take=SimpleNamespace(path=Path(d) / "Take01"),
                errors=(), warnings=(), summary="1 track",
            )
            try:
                with patch.object(strip, "set_recording_phase", side_effect=spy), \
                     patch("webjam_qt.controllers.recording_coordinator."
                           "find_changed_take", return_value=Path(d) / "Take01"), \
                     patch("webjam_qt.controllers.recording_coordinator."
                           "wait_for_take_files_stable", return_value=True), \
                     patch("webjam_qt.controllers.recording_coordinator."
                           "write_take_manifest", return_value=fake_result), \
                     patch.object(c._ui_invoker, "invoke",
                                  side_effect=lambda fn: fn()):
                    c.recording._validate_take_worker()
            finally:
                if c.recording._completion_box is not None:
                    c.recording._completion_box.close()
                    c.recording._completion_box = None
        self.assertEqual(
            details,
            ["WAITING FOR SERVER FILES…", "CHECKING TRACKS…",
             "ALIGNING HOST TRACKS…"],
        )

    def test_worker_stop_path(self):
        c = self.controller
        c._recorder_armed = True
        fake_rpc = MagicMock()
        fake_rpc.__enter__ = MagicMock(return_value=fake_rpc)
        fake_rpc.__exit__ = MagicMock(return_value=None)
        fake_rpc.get_recorder_status.return_value = {"enabled": False}
        with patch("core.jamulus_server_rpc.JamulusServerRpc",
                   return_value=fake_rpc), \
             patch("core.jamulus_server_rpc.read_secret_file",
                   return_value="s3cret"), \
             patch.object(c._ui_invoker, "invoke",
                          side_effect=lambda fn: fn()):
            c._record_toggle_worker(False, "/tmp/secret")
        fake_rpc.stop_recording.assert_called_once()
        self.assertFalse(c._recorder_armed)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("stopped" in m for m in msgs), msgs)


class _Immediate:
    def __init__(self, *positional, **kwargs):
        self._target = kwargs.get("target")
        self._args = kwargs.get("args", ())

    def start(self):
        if self._target is not None:
            self._target(*self._args)


if __name__ == "__main__":
    unittest.main()
