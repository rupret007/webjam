"""In-session chat over Jamulus: controller send/receive plumbing, the canvas
append, and the app surfacing incoming chat (HTML-stripped) in the canvas."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile  # noqa: E402
import unittest  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest import mock  # noqa: E402

from jamulus_controller import JamulusController  # noqa: E402


class TestControllerChat(unittest.TestCase):
    def _controller(self, available=True):
        c = JamulusController.__new__(JamulusController)
        c.rpc_client = mock.MagicMock()
        c.rpc_client.available = available
        c.rpc_client.send_chat_text.return_value = True
        c.chat_callback = None
        return c

    def test_send_chat_delegates_when_available(self):
        c = self._controller(available=True)
        self.assertTrue(c.send_chat("hi band"))
        c.rpc_client.send_chat_text.assert_called_once_with("hi band")

    def test_send_chat_noop_when_unavailable(self):
        c = self._controller(available=False)
        self.assertFalse(c.send_chat("hi"))
        c.rpc_client.send_chat_text.assert_not_called()

    def test_send_chat_noop_on_empty(self):
        c = self._controller(available=True)
        self.assertFalse(c.send_chat(""))
        c.rpc_client.send_chat_text.assert_not_called()

    def test_incoming_chat_forwards_to_callback(self):
        c = self._controller()
        got = []
        c.chat_callback = got.append
        c._on_rpc_chat("hello")
        self.assertEqual(got, ["hello"])

    def test_incoming_chat_callback_errors_swallowed(self):
        c = self._controller()
        c.chat_callback = mock.Mock(side_effect=RuntimeError("boom"))
        c._on_rpc_chat("x")  # must not raise


# ----------------------------------------------------------------------
# Qt: canvas append + app integration
# ----------------------------------------------------------------------
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestCanvasAppend(unittest.TestCase):
    def test_append_line_adds_text(self):
        from webjam_qt.widgets.session_canvas import SessionCanvas
        c = SessionCanvas()
        c.set_notes("line1")
        c.append_line("line2")
        self.assertIn("line1", c.current_notes())
        self.assertIn("line2", c.current_notes())

    def test_append_line_ignores_empty(self):
        from webjam_qt.widgets.session_canvas import SessionCanvas
        c = SessionCanvas()
        c.set_notes("only")
        c.append_line("")
        self.assertEqual(c.current_notes().strip(), "only")


class TestAppChatIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.settings import AppSettings
        from webjam_qt.controllers.application_controller import ApplicationController
        from webjam_qt.windows.conductor_window import ConductorWindow
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_home = os.environ.get("HOME")
        os.environ["HOME"] = cls._tmp.name
        cls.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam", initial_title="Chat",
        )
        # The production invoker queues routing-scan callbacks correctly. This
        # test replaces it with a synchronous stub, so prevent an in-flight
        # scan from crossing that test-only boundary on a worker thread.
        with mock.patch.object(ApplicationController, "_start_routing_scan"):
            cls.controller = ApplicationController(cls.window, settings=AppSettings())
        # Run the UI-thread invoker synchronously for deterministic testing.
        cls.controller._ui_invoker = SimpleNamespace(invoke=lambda fn: fn())

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()
        if cls._old_home is not None:
            os.environ["HOME"] = cls._old_home
        else:
            os.environ.pop("HOME", None)
        cls._tmp.cleanup()

    def test_chat_callback_is_wired(self):
        # The controller wired its chat handler into the Jamulus controller.
        self.assertEqual(self.controller.jamulus.chat_callback,
                         self.controller._on_jamulus_chat)

    def test_incoming_chat_appended_to_canvas_html_stripped(self):
        self.controller.window.session_canvas.set_notes("")
        self.controller._on_jamulus_chat('<font color="blue">(10:00) <b>Alice</b></font> sounds great')
        notes = self.controller.window.session_canvas.current_notes()
        self.assertIn("Alice", notes)
        self.assertIn("sounds great", notes)
        self.assertNotIn("<b>", notes)
        self.assertNotIn("<font", notes)


if __name__ == "__main__":
    unittest.main()
