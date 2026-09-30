"""Acceptance tests for the Music end-of-rehearsal recap card."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.rehearsal_recap import (  # noqa: E402
    RehearsalRecapSnapshot,
    take_status_label_for_recap,
)
from core.session_conductor import (  # noqa: E402
    SessionConductorFacts,
    TakeValidationState,
)
from core.session_intelligence import (  # noqa: E402
    ParticipantSignal,
    SessionAction,
    SessionPulse,
    build_session_pulse,
)
from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import (  # noqa: E402
    ApplicationController,
)
from webjam_qt.widgets.rehearsal_recap import RehearsalRecapPanel  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


def _sample_pulse(
    *,
    decision_count: int = 2,
    action_count: int = 1,
    participant_count: int = 4,
) -> SessionPulse:
    return SessionPulse(
        mode_key="music_jam",
        mode_label="Band Jam",
        title="Evening rehearsal",
        stage="live",
        summary="Worked the bridge.",
        next_step="Send the recap.",
        decisions=tuple(f"Decision {index}" for index in range(1, decision_count + 1)),
        actions=tuple(
            SessionAction(text=f"Action {index}") for index in range(1, action_count + 1)
        ),
        blockers=(),
        questions=(),
        references=(),
        participant_signal=ParticipantSignal(count=participant_count),
        checkpoint="save mix",
    )


def _conductor_window() -> ConductorWindow:
    return ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Band Rehearsal",
    )


class TestTakeStatusLabelForRecap(unittest.TestCase):
    def test_no_take_returns_none(self):
        self.assertIsNone(take_status_label_for_recap(SessionConductorFacts()))

    def test_ready_take(self):
        facts = SessionConductorFacts(
            take_available=True,
            take_path="/tmp/take.wav",
            take_validation=TakeValidationState.VALID,
        )
        self.assertEqual(take_status_label_for_recap(facts), "Ready")

    def test_unvalidated_take(self):
        facts = SessionConductorFacts(
            take_available=True,
            take_path="/tmp/take.wav",
            take_validation=TakeValidationState.NEEDS_ATTENTION,
        )
        self.assertEqual(take_status_label_for_recap(facts), "Needs attention")


class TestRehearsalRecapPanel(unittest.TestCase):
    def setUp(self):
        self.panel = RehearsalRecapPanel()

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        _app.processEvents()

    def test_ready_take_shows_signal_line_and_open_studio(self):
        pulse = _sample_pulse()
        self.panel.apply_snapshot(
            RehearsalRecapSnapshot(
                duration_seconds=3723,
                pulse=pulse,
                take_status="Ready",
            )
        )
        self.assertEqual(self.panel.objectName(), "RehearsalRecap")
        self.assertTrue(self.panel.isVisible())
        self.assertIn(
            "2 decisions · 1 actions · 0 blockers",
            self.panel._signals.text(),
        )
        self.assertTrue(self.panel._open_studio_button.isVisible())
        self.assertTrue(self.panel._open_studio_button.isEnabled())

    def test_no_take_hides_open_studio_and_take_line(self):
        pulse = _sample_pulse()
        self.panel.apply_snapshot(
            RehearsalRecapSnapshot(duration_seconds=90, pulse=pulse, take_status=None)
        )
        self.assertFalse(self.panel._open_studio_button.isVisible())
        self.assertFalse(self.panel._take_status.isVisible())
        self.assertEqual(self.panel._take_status.text(), "")

    def test_export_writes_pulse_markdown_not_raw_notes(self):
        raw_secret = "RAW_NOTE_BODY_MUST_NOT_EXPORT"
        pulse = build_session_pulse(
            mode_key="music_jam",
            notes=f"decision: keep the chorus\n{raw_secret}",
        )
        self.panel.apply_snapshot(
            RehearsalRecapSnapshot(duration_seconds=60, pulse=pulse)
        )
        expected = pulse.to_markdown()
        self.assertNotIn(raw_secret, expected)

        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "recap.md")

            with patch(
                "webjam_qt.widgets.rehearsal_recap.QFileDialog.getSaveFileName",
                return_value=(target, "Markdown (*.md)"),
            ):
                self.panel._export_recap()

            self.assertEqual(Path(target).read_text(encoding="utf-8"), expected)


class TestRehearsalRecapController(unittest.TestCase):
    def test_art_end_never_captures_recap(self):
        controller = ApplicationController.__new__(ApplicationController)
        controller._active_creator_profile_key = "art"
        controller._pending_rehearsal_recap = RehearsalRecapSnapshot(
            duration_seconds=1,
            pulse=_sample_pulse(),
        )
        controller._capture_rehearsal_recap_before_stop()
        self.assertIsNone(controller._pending_rehearsal_recap)

    def test_present_after_music_stop_shows_panel(self):
        window = _conductor_window()
        controller = ApplicationController.__new__(ApplicationController)
        controller._active_creator_profile_key = "music"
        controller.window = window
        controller._current_session_pulse = _sample_pulse()
        window.session_strip._elapsed_seconds = 615
        ready_facts = SessionConductorFacts(
            take_available=True,
            take_path="/tmp/take.wav",
            take_validation=TakeValidationState.VALID,
        )
        with patch.object(controller, "_session_conductor_facts", return_value=ready_facts):
            controller._capture_rehearsal_recap_before_stop()
        controller._present_rehearsal_recap_after_stop()
        window.show()
        _app.processEvents()
        try:
            panel = window.rehearsal_recap
            self.assertTrue(panel.isVisible())
            self.assertEqual(panel.objectName(), "RehearsalRecap")
            self.assertIn(
                "2 decisions · 1 actions · 0 blockers",
                panel._signals.text(),
            )
            self.assertTrue(panel._open_studio_button.isEnabled())
        finally:
            window.close()
            window.deleteLater()
            _app.processEvents()

    def test_open_studio_button_click_shows_take_review_and_hides_recap(self):
        """Cover the actual wiring behind the Open Studio button.

        Earlier tests only asserted the button's own enabled/visible state.
        The button's ``open_studio_requested`` signal is wired in
        ApplicationController's real ``_connect_signals`` (not on the
        ``__new__``-built stand-ins used above), so only a fully constructed
        controller exercises the click actually switching the workspace to
        the reference studio and dismissing the recap card.
        """

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = AppSettings(config_file=str(Path(tmpdir) / "settings.json"))
            window = _conductor_window()
            controller = ApplicationController(window, settings=settings)
            try:
                self.assertEqual(controller.creator_profile.key, "music")
                controller._current_session_pulse = _sample_pulse()
                window.session_strip._elapsed_seconds = 615
                ready_facts = SessionConductorFacts(
                    take_available=True,
                    take_path="/tmp/take.wav",
                    take_validation=TakeValidationState.VALID,
                )
                with patch.object(
                    controller, "_session_conductor_facts", return_value=ready_facts
                ), patch.object(controller.session_library, "current_take_status", return_value="Ready"):
                    controller._capture_rehearsal_recap_before_stop()
                    controller._present_rehearsal_recap_after_stop()
                window.show()
                _app.processEvents()
                panel = window.rehearsal_recap
                self.assertTrue(panel.isVisible())
                self.assertTrue(panel._open_studio_button.isVisibleTo(window))
                self.assertTrue(panel._open_studio_button.isEnabled())

                panel._open_studio_button.click()
                _app.processEvents()

                self.assertFalse(panel.isVisible())
                self.assertIs(
                    window.workspace_stack.currentWidget(), window.reference_studio
                )
            finally:
                window.close()
                window.deleteLater()
                _app.processEvents()

    def test_art_present_leaves_recap_hidden(self):
        window = _conductor_window()
        controller = ApplicationController.__new__(ApplicationController)
        controller._active_creator_profile_key = "art"
        controller.window = window
        controller._pending_rehearsal_recap = None
        controller._present_rehearsal_recap_after_stop()
        _app.processEvents()
        try:
            self.assertFalse(window.rehearsal_recap.isVisible())
        finally:
            window.close()
            window.deleteLater()
            _app.processEvents()


class TestRehearsalRecapLayout(unittest.TestCase):
    def test_recap_fits_offscreen_window_without_covering_end_controls(self):
        window = _conductor_window()
        window.resize(1000, 740)
        window.show()
        pulse = _sample_pulse()
        window.rehearsal_recap.apply_snapshot(
            RehearsalRecapSnapshot(
                duration_seconds=1200,
                pulse=pulse,
                take_status="Ready",
            )
        )
        window.session_strip.set_audio_state("End Session", enabled=True)
        window.session_controls.show()
        _app.processEvents()
        try:
            audio_button = window.session_strip._audio_button
            recap = window.rehearsal_recap
            controls = window.session_controls
            self.assertTrue(audio_button.isVisibleTo(window))
            self.assertTrue(recap.isVisibleTo(window))
            window_rect = QRect(QPoint(0, 0), window.size())
            button_rect = QRect(
                audio_button.mapTo(window, QPoint(0, 0)),
                audio_button.size(),
            )
            recap_rect = QRect(recap.mapTo(window, QPoint(0, 0)), recap.size())
            controls_rect = QRect(
                controls.mapTo(window, QPoint(0, 0)),
                controls.size(),
            )
            self.assertTrue(window_rect.contains(button_rect))
            self.assertTrue(window_rect.contains(recap_rect.topLeft()))
            self.assertTrue(window_rect.contains(controls_rect.bottomRight()))
            self.assertLessEqual(recap_rect.bottom(), controls_rect.top())
            self.assertGreaterEqual(recap_rect.top(), 0)
        finally:
            window.close()
            window.deleteLater()
            _app.processEvents()


if __name__ == "__main__":
    unittest.main()
