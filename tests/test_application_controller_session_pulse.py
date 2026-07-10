from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.widgets.participant_card import ParticipantPresentation  # noqa: E402


class TestSessionPulseParticipants(unittest.TestCase):
    def setUp(self):
        self.controller = ApplicationController.__new__(ApplicationController)
        self.controller.audio = SimpleNamespace(connected=False)
        self.controller.participants = {
            0: ParticipantPresentation(
                channel_id=0,
                name="You",
                role="Preview · You",
                is_local=True,
            ),
            1: ParticipantPresentation(
                channel_id=1,
                name="Sample 1",
                role="Preview · Guitar",
            ),
        }

    def test_demo_participants_are_never_included(self):
        self.assertEqual(self.controller._session_pulse_participants(), [])

        self.controller.audio.connected = True
        self.assertEqual(self.controller._session_pulse_participants(), [])

    def test_confirmed_non_preview_participants_are_included(self):
        self.controller.audio.connected = True
        self.controller.participants = {
            7: ParticipantPresentation(
                channel_id=7,
                name="Taylor",
                role="Musician",
                is_local=True,
            )
        }

        participants = self.controller._session_pulse_participants()
        self.assertEqual([participant.name for participant in participants], ["Taylor"])

    def test_note_change_restarts_the_single_shot_refresh_timer(self):
        self.controller._shutdown = False
        self.controller._pulse_refresh_timer = MagicMock()

        self.controller._schedule_session_pulse_refresh("Decision: keep it tight")

        self.controller._pulse_refresh_timer.start.assert_called_once_with()

    def test_refresh_uses_current_title_mode_notes_and_real_participants(self):
        self.controller.audio.connected = True
        self.controller.participants = {
            7: ParticipantPresentation(
                channel_id=7,
                name="Taylor",
                role="Musician",
                is_local=True,
            )
        }
        canvas = SimpleNamespace(
            current_notes=lambda: "Decision: keep the bridge short",
            set_session_pulse=MagicMock(),
        )
        self.controller.window = SimpleNamespace(
            session_strip=SimpleNamespace(
                current_mode_key=lambda: "music_jam",
                current_title=lambda: "Bridge\npass",
            ),
            session_canvas=canvas,
        )

        self.controller._refresh_session_pulse()

        pulse = canvas.set_session_pulse.call_args.args[0]
        self.assertEqual(pulse.title, "Bridge pass")
        self.assertEqual(pulse.participant_signal.count, 1)
        self.assertEqual(pulse.decisions, ("keep the bridge short",))

    def test_refresh_failure_discards_stale_derived_content(self):
        self.controller.audio.connected = False
        canvas = SimpleNamespace(
            current_notes=lambda: "newer raw notes",
            set_session_pulse=MagicMock(),
            clear_session_pulse=MagicMock(),
        )
        self.controller.window = SimpleNamespace(
            session_strip=SimpleNamespace(
                current_mode_key=lambda: "music_jam",
                current_title=lambda: "Rehearsal",
            ),
            session_canvas=canvas,
        )

        with patch(
            "webjam_qt.controllers.application_controller.build_session_pulse",
            side_effect=RuntimeError("unexpected parser failure"),
        ), self.assertLogs("webjam.qt.application_controller", level="WARNING"):
            self.controller._refresh_session_pulse()

        canvas.clear_session_pulse.assert_called_once_with()
        canvas.set_session_pulse.assert_not_called()


class TestSessionPulseDebounce(unittest.TestCase):
    def test_real_note_edit_refreshes_after_debounce(self):
        from core.settings import AppSettings
        from webjam_qt.windows.conductor_window import ConductorWindow

        old_home = os.environ.get("HOME")
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["HOME"] = tmpdir
            window = ConductorWindow(
                mode_entries=ApplicationController.mode_entries(),
                initial_mode_key="music_jam",
                initial_title="Debounce",
            )
            with patch.object(ApplicationController, "_start_routing_scan"):
                controller = ApplicationController(window, settings=AppSettings())
            try:
                self.assertEqual(window.session_canvas._current_pulse.decisions, ())

                window.session_canvas._notes.setPlainText(
                    "Decision: keep the bridge short"
                )
                QTest.qWait(250)

                self.assertEqual(
                    window.session_canvas._current_pulse.decisions,
                    ("keep the bridge short",),
                )
            finally:
                controller.shutdown()
                window.close()
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)


if __name__ == "__main__":
    unittest.main()
