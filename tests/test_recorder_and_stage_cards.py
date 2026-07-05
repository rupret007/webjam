"""
Server-recording indicator + stage cards v2 (skill badges).

Covers the recorderState notification path end to end: RPC parse →
JamulusController hook → ApplicationController → ● REC status chip, plus
skill-level propagation from RPC metadata into participant role labels.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.jamulus_rpc_client import JamulusRpcClient  # noqa: E402
from core.settings import AppSettings  # noqa: E402
from jamulus_controller import JamulusController  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


class TestRpcRecorderStateParsing(unittest.TestCase):
    def _client_with_hook(self):
        received = []
        client = JamulusRpcClient(
            port=22222,
            on_recorder_state=lambda rec, raw: received.append((rec, raw)),
        )
        return client, received

    def test_recording_state_3_means_recording(self):
        client, received = self._client_with_hook()
        client._handle_notification("jamulusclient/recorderState", {"state": 3})
        self.assertEqual(received, [(True, 3)])

    def test_not_enabled_state_2_means_not_recording(self):
        client, received = self._client_with_hook()
        client._handle_notification("jamulusclient/recorderState", {"state": 2})
        self.assertEqual(received, [(False, 2)])

    def test_garbage_state_is_ignored(self):
        client, received = self._client_with_hook()
        client._handle_notification("jamulusclient/recorderState", {"state": "??"})
        client._handle_notification("jamulusclient/recorderState", {})
        self.assertEqual(received, [])

    def test_no_hook_is_safe(self):
        client = JamulusRpcClient(port=22222)
        client._handle_notification("jamulusclient/recorderState", {"state": 3})

    def test_failing_hook_is_swallowed(self):
        client = JamulusRpcClient(
            port=22222,
            on_recorder_state=MagicMock(side_effect=RuntimeError("boom")),
        )
        client._handle_notification("jamulusclient/recorderState", {"state": 3})


class TestControllerRecorderForwarding(unittest.TestCase):
    def test_forwarded_to_ui_hook(self):
        c = JamulusController(host="127.0.0.1", port=22124, rpc_port=22222)
        received = []
        c.recorder_state_callback = lambda rec, raw: received.append((rec, raw))
        c._on_rpc_recorder_state(True, 3)
        self.assertEqual(received, [(True, 3)])

    def test_no_hook_and_failing_hook_are_safe(self):
        c = JamulusController(host="127.0.0.1", port=22124, rpc_port=22222)
        c.recorder_state_callback = None
        c._on_rpc_recorder_state(True, 3)
        c.recorder_state_callback = MagicMock(side_effect=RuntimeError("boom"))
        c._on_rpc_recorder_state(False, 2)


class TestSkillPropagation(unittest.TestCase):
    def test_skill_level_lands_on_participants(self):
        c = JamulusController(host="127.0.0.1", port=22124, rpc_port=22222)
        c.rpc_client = MagicMock()
        c.rpc_client.available = True
        c.protocol = MagicMock()
        c.audio_engine = MagicMock()
        c._on_rpc_participants([
            {"channel_id": 0, "name": "Jeff", "instrument": "guitar",
             "skill_level": "expert", "is_local": True},
            {"channel_id": 1, "name": "Ann", "skill_level": ""},
        ])
        self.assertEqual(c.participants[0].skill_level, "expert")
        self.assertEqual(c.participants[1].skill_level, "")


class TestConductorRecIndicator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        self.controller.window.flash_message = MagicMock()
        self.controller._server_recording = False
        self.window.set_status_recording(False)

    def test_rec_chip_hidden_by_default(self):
        self.assertFalse(self.window._status_recording.isVisibleTo(self.window))

    def test_recording_shows_chip_and_flashes_once(self):
        c = self.controller
        c._apply_recorder_state(True)
        self.assertTrue(self.window._status_recording.isVisibleTo(self.window))
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("recording" in m for m in msgs), msgs)
        # Same state again → no second flash.
        c.window.flash_message.reset_mock()
        c._apply_recorder_state(True)
        c.window.flash_message.assert_not_called()

    def test_stop_recording_hides_chip(self):
        c = self.controller
        c._apply_recorder_state(True)
        c._apply_recorder_state(False)
        self.assertFalse(self.window._status_recording.isVisibleTo(self.window))
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("stopped" in m for m in msgs), msgs)

    def test_role_label_includes_skill_badge(self):
        from types import SimpleNamespace
        label = ApplicationController._role_label(SimpleNamespace(
            channel_id=1, is_local=False, instrument="bass",
            skill_level="intermediate",
        ))
        self.assertEqual(label, "Bass · Intermediate")

    def test_role_label_skips_null_skill(self):
        from types import SimpleNamespace
        label = ApplicationController._role_label(SimpleNamespace(
            channel_id=0, is_local=True, instrument="", skill_level="null",
        ))
        self.assertEqual(label, "You")


if __name__ == "__main__":
    unittest.main()
