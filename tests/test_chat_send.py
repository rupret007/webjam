"""Chat SEND: the canvas input emits chat_submitted; the controller sends it to
the band via Jamulus and echoes it locally."""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestCanvasChatInput(unittest.TestCase):
    def test_returnpressed_emits_and_clears(self):
        from webjam_qt.widgets.session_canvas import SessionCanvas
        c = SessionCanvas()
        got = []
        c.chat_submitted.connect(got.append)
        c._chat_input.setText("  hello band  ")
        c._on_chat_entered()
        self.assertEqual(got, ["hello band"])          # stripped
        self.assertEqual(c._chat_input.text(), "")     # cleared

    def test_empty_message_ignored(self):
        from webjam_qt.widgets.session_canvas import SessionCanvas
        c = SessionCanvas()
        got = []
        c.chat_submitted.connect(got.append)
        c._chat_input.setText("   ")
        c._on_chat_entered()
        self.assertEqual(got, [])


class TestControllerChatSubmit(unittest.TestCase):
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
            initial_mode_key="music_jam", initial_title="ChatSend",
        )
        cls.controller = ApplicationController(cls.window, settings=AppSettings())

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()
        if cls._old_home is not None:
            os.environ["HOME"] = cls._old_home
        else:
            os.environ.pop("HOME", None)
        cls._tmp.cleanup()

    def test_submit_sends_to_band_and_echoes(self):
        self.controller.jamulus = mock.MagicMock()
        self.controller.jamulus.send_chat.return_value = True
        self.controller.window.session_canvas.set_notes("")
        self.controller._on_chat_submitted("count us in")
        self.controller.jamulus.send_chat.assert_called_once_with("count us in")
        self.assertIn("You: count us in",
                      self.controller.window.session_canvas.current_notes())

    def test_failed_send_is_restored_and_never_falsely_echoed(self):
        self.controller.jamulus = mock.MagicMock()
        self.controller.jamulus.send_chat.return_value = False
        canvas = self.controller.window.session_canvas
        canvas.set_notes("")
        canvas._chat_input.clear()
        with mock.patch.object(
            self.controller.window,
            "flash_message",
        ) as flash_message:
            self.controller._on_chat_submitted("count us in")

            self.controller.jamulus.send_chat.assert_called_once_with("count us in")
            self.assertEqual(canvas._chat_input.text(), "count us in")
            self.assertNotIn("You: count us in", canvas.current_notes())
            self.assertIn(
                "not sent",
                flash_message.call_args.args[0].lower(),
            )

    def test_wired_to_canvas_signal(self):
        # Emitting the canvas signal must reach the controller -> Jamulus.
        self.controller.jamulus = mock.MagicMock()
        self.controller.jamulus.send_chat.return_value = True
        self.controller.window.session_canvas.chat_submitted.emit("hey")
        self.controller.jamulus.send_chat.assert_called_once_with("hey")


if __name__ == "__main__":
    unittest.main()
