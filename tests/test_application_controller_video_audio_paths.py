"""
ApplicationController live-session path coverage (v0.4.9 hardening).

Covers the external Webex launch contract, ignored legacy embed callbacks, Launch/Stop
Audio toggle (with confirmation dialog), the reconnect-tick crash banner,
the side-rail view handler, the settings wizard round-trip, and the
diagnostics exporter.  All headless (QT_QPA_PLATFORM=offscreen).
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from tests.support.jamulus_monitor import bind_primary_rpc_monitor  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _make_controller():
    window = ConductorWindow(
        mode_entries=ApplicationController.mode_entries(),
        initial_mode_key="music_jam",
        initial_title="Test",
    )
    controller = ApplicationController(window, settings=AppSettings())
    return window, controller


class _ControllerTestBase(unittest.TestCase):
    """Shared per-class window/controller with mocked UI surfaces."""

    @classmethod
    def setUpClass(cls):
        cls.window, cls.controller = _make_controller()

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()

    def setUp(self):
        c = self.controller
        c.window.flash_message = MagicMock()
        c.window.set_status_video = MagicMock()
        c.window.set_status_audio = MagicMock()
        c.window.session_strip.set_video_state = MagicMock()
        c.window.session_strip.set_audio_state = MagicMock()
        # Replace the lightweight launch card with a mock; these tests only
        # assert controller call routing.
        c.window.webex_embed = MagicMock()
        c.bridge.webex_state = "Not opened"
        c.bridge.jamulus_state = "Not launched"
        c.audio.stopping = False
        c.audio.cleanup_retry_required = False
        c.audio.ended_by_user = False
        c._invite_switch_in_flight = False
        c._webex_app_info = None
        c._webex_activation_inflight = False
        c._webex_events = []
        c._shutdown_in_progress = False
        c.window.session_strip.set_tools_enabled(True)


class TestExternalWebexLaunch(_ControllerTestBase):
    def test_main_webex_navigation_never_hands_off_the_meeting_url(self):
        c = self.controller
        c.settings.webex_url = "https://example.webex.com/meet/band"
        c.bridge.launch_webex = MagicMock()

        c.window.session_strip._video_button.click()

        c.bridge.launch_webex.assert_not_called()
        c.window.webex_embed.setVisible.assert_called_once_with(True)
        c.window.webex_embed.focus_primary_action.assert_called_once_with()
        self.assertEqual(c._last_content_key, "stage")
        self.assertEqual(
            c._webex_events[-1],
            {"action": "conversation-panel", "result": "shown"},
        )

    def test_more_conversation_uses_the_same_side_effect_free_route(self):
        c = self.controller
        c.settings.webex_url = "https://example.webex.com/meet/band"
        c.bridge.launch_webex = MagicMock()
        c.window.session_strip.set_video_configured(True)
        action = next(
            action
            for action in c.window.session_strip._tools_button.menu().actions()
            if action.text() == "Webex Controls"
        )

        action.trigger()

        c.bridge.launch_webex.assert_not_called()
        c.window.webex_embed.setVisible.assert_called_once_with(True)
        c.window.webex_embed.focus_primary_action.assert_called_once_with()

    def test_join_without_url_shows_actionable_error(self):
        c = self.controller
        c.settings.webex_url = ""
        c._show_actionable_error = MagicMock()
        c._on_join_video()
        c.window.webex_embed.setVisible.assert_called_once_with(True)
        c._show_actionable_error.assert_called_once()
        self.assertEqual(c._show_actionable_error.call_args.args[0], "No Webex Link")
        c.bridge.launch_webex = MagicMock()
        c.bridge.launch_webex.assert_not_called()

    def test_valid_url_opens_externally(self):
        c = self.controller
        c.settings.webex_url = "https://example.webex.com/meet/band"
        c.bridge.launch_webex = MagicMock(return_value=True)
        c._on_join_video()
        c.window.webex_embed.setVisible.assert_called_once_with(True)
        self.assertEqual(c.webex.meeting_url, "https://example.webex.com/meet/band")
        c.bridge.launch_webex.assert_called_once_with(manual=True)
        c.window.session_strip.set_video_state.assert_called_with(
            "Opening…", enabled=False
        )
        c.window.webex_embed.set_launch_status.assert_called_with("Opening…")
        c.window.webex_embed.load_meeting.assert_not_called()
        self.assertEqual(
            c._webex_events[-1],
            {"action": "meeting-handoff", "result": "accepted"},
        )

    def test_rejected_single_flight_launch_never_leaves_opening_ui(self):
        c = self.controller
        c.settings.webex_url = "https://example.webex.com/meet/new-room"
        c.bridge.webex_state = "Not opened"
        c.bridge.launch_webex = MagicMock(return_value=False)

        c._on_join_video()

        c.bridge.launch_webex.assert_called_once_with(manual=True)
        c.window.set_status_video.assert_called_with("Not opened")
        c.window.session_strip.set_video_state.assert_called_with(
            "Open Webex",
            enabled=True,
        )
        c.window.webex_embed.set_launch_status.assert_called_with(
            "Not opened"
        )
        self.assertIn(
            "previous Webex open request",
            c.window.flash_message.call_args.args[0],
        )

    def test_join_invalid_webex_url_shows_actionable_error(self):
        c = self.controller
        c.settings.webex_url = "http://example.webex.com/meet/band"
        c._show_actionable_error = MagicMock()

        c._on_join_video()

        c.window.webex_embed.setVisible.assert_called_once_with(True)
        c._show_actionable_error.assert_called_once()
        self.assertEqual(
            c._show_actionable_error.call_args.args[0],
            "Invalid Webex URL",
        )
        self.assertEqual(
            c._webex_events[-1],
            {"action": "meeting-handoff", "result": "invalid-link"},
        )
        c.bridge.launch_webex = MagicMock()
        c.bridge.launch_webex.assert_not_called()

    def test_join_non_webex_url_shows_actionable_error(self):
        c = self.controller
        c.settings.webex_url = "https://example.com/meet/band"
        c._show_actionable_error = MagicMock()

        c._on_join_video()

        c._show_actionable_error.assert_called_once()
        self.assertEqual(
            c._show_actionable_error.call_args.args[0],
            "Invalid Webex URL",
        )
        c.bridge.launch_webex = MagicMock()
        c.bridge.launch_webex.assert_not_called()

    def test_open_again_launches_again_instead_of_claiming_leave(self):
        c = self.controller
        c.settings.webex_url = "https://example.webex.com/meet/band"
        c.bridge.webex_state = "Opened externally"
        c.bridge.launch_webex = MagicMock(return_value=True)
        c._on_join_video()
        c.bridge.launch_webex.assert_called_once_with(manual=True)
        c.window.webex_embed.leave_meeting.assert_not_called()

    def test_join_open_is_single_flight_while_handoff_is_in_progress(self):
        c = self.controller
        c.settings.webex_url = "https://example.webex.com/meet/band"
        c.bridge.webex_state = "Opening…"
        c.bridge.launch_webex = MagicMock()

        c._on_join_video()

        c.bridge.launch_webex.assert_not_called()
        self.assertIn(
            "already opening",
            c.window.flash_message.call_args.args[0],
        )


class TestNativeWebexControls(_ControllerTestBase):
    def test_bring_forward_activates_only_the_detected_app(self):
        from services.webex_app import (
            WebexActivationResult,
            WebexActivationState,
            WebexAppInfo,
            WebexAppState,
        )

        c = self.controller
        info = WebexAppInfo(
            state=WebexAppState.INSTALLED,
            version="46.7.0",
            publisher_verified=True,
            path=Path("/Applications/Webex.app"),
        )
        c._webex_app_info = info
        c.bridge.launch_webex = MagicMock()
        with patch(
            "services.webex_app.show_webex_app",
            return_value=WebexActivationResult(
                WebexActivationState.ACTIVATED_RUNNING
            ),
        ) as activate:
            c._show_webex_app()
            self.assertTrue(
                _wait_until(
                    lambda: activate.called
                    and not c._webex_activation_inflight
                )
            )

        activate.assert_called_once()
        self.assertEqual(activate.call_args.args, (info,))
        self.assertTrue(callable(activate.call_args.kwargs["cancelled"]))
        c.bridge.launch_webex.assert_not_called()
        self.assertIn(
            "verified running Webex app is active",
            c.window.flash_message.call_args.args[0],
        )
        self.assertIn(
            "No browser or meeting link was opened",
            c.window.flash_message.call_args.args[0],
        )
        self.assertEqual(
            c._webex_events[-1],
            {"action": "show-webex-app", "result": "activated-running"},
        )

    def test_mute_action_never_changes_webex_or_jamulus_audio_blindly(self):
        from services.webex_app import (
            WebexActivationResult,
            WebexActivationState,
            WebexAppInfo,
            WebexAppState,
        )

        c = self.controller
        c._webex_app_info = WebexAppInfo(
            state=WebexAppState.INSTALLED,
            version="46.7.0",
            publisher_verified=True,
            path=Path("/Applications/Webex.app"),
        )
        c.bridge.launch_webex = MagicMock()
        c.jamulus.set_mute = MagicMock()
        c.jamulus.set_self_muted = MagicMock()
        with patch(
            "services.webex_app.show_webex_app",
            return_value=WebexActivationResult(
                WebexActivationState.ACTIVATED_RUNNING
            ),
        ) as activate:
            c._focus_webex_mute()
            self.assertTrue(
                _wait_until(
                    lambda: activate.called
                    and not c._webex_activation_inflight
                )
            )

        c.bridge.launch_webex.assert_not_called()
        c.jamulus.set_mute.assert_not_called()
        c.jamulus.set_self_muted.assert_not_called()
        message = c.window.flash_message.call_args.args[0]
        self.assertIn("verified running Webex app is active", message)
        self.assertIn("use Webex’s own Mute control", message)
        self.assertIn("restore it from the Dock", message)
        self.assertIn("did not change", message)
        self.assertEqual(
            c._webex_events[-1],
            {"action": "mute-guidance", "result": "activated-running"},
        )

    def test_mute_guidance_handles_verified_stopped_app_launch_truthfully(self):
        from services.webex_app import (
            WebexActivationResult,
            WebexActivationState,
            WebexAppInfo,
            WebexAppState,
        )

        c = self.controller
        c._webex_app_info = WebexAppInfo(
            state=WebexAppState.INSTALLED,
            version="46.7.0",
            publisher_verified=True,
            path=Path("/Applications/Webex.app"),
        )
        c.bridge.launch_webex = MagicMock()
        c.jamulus.set_mute = MagicMock()
        c.jamulus.set_self_muted = MagicMock()
        with patch(
            "services.webex_app.show_webex_app",
            return_value=WebexActivationResult(
                WebexActivationState.LAUNCHED_APP
            ),
        ) as activate:
            c._focus_webex_mute()
            self.assertTrue(
                _wait_until(
                    lambda: activate.called
                    and not c._webex_activation_inflight
                )
            )

        c.bridge.launch_webex.assert_not_called()
        c.jamulus.set_mute.assert_not_called()
        c.jamulus.set_self_muted.assert_not_called()
        message = c.window.flash_message.call_args.args[0]
        self.assertIn(
            "verified Webex app was launched without a meeting link",
            message,
        )
        self.assertIn("Use Webex’s own Mute control after joining", message)
        self.assertIn("did not open a browser", message)
        self.assertIn("affect Jamulus audio", message)
        self.assertEqual(
            c._webex_events[-1],
            {"action": "mute-guidance", "result": "launched-app"},
        )

    def test_unavailable_app_does_not_launch_a_meeting_as_fallback(self):
        from services.webex_app import WebexAppInfo, WebexAppState

        c = self.controller
        c._webex_app_info = WebexAppInfo(
            state=WebexAppState.NOT_INSTALLED,
        )
        c.bridge.launch_webex = MagicMock()

        c._bring_webex_forward()

        c.bridge.launch_webex.assert_not_called()
        self.assertIn(
            "not available",
            c.window.flash_message.call_args.args[0],
        )

    def test_failed_revalidation_rescans_without_opening_a_meeting(self):
        from services.webex_app import (
            WebexActivationResult,
            WebexActivationState,
            WebexAppInfo,
            WebexAppState,
        )

        c = self.controller
        c._webex_app_info = WebexAppInfo(
            state=WebexAppState.INSTALLED,
            publisher_verified=True,
            path=Path("/Applications/Webex.app"),
        )
        c.bridge.launch_webex = MagicMock()
        with patch(
            "services.webex_app.show_webex_app",
            return_value=WebexActivationResult(
                WebexActivationState.REFUSED,
                "reverification-refused",
            ),
        ) as activate, patch.object(
            c,
            "_start_webex_app_detection",
            return_value=True,
        ) as rescan:
            c._bring_webex_forward()
            self.assertTrue(
                _wait_until(
                    lambda: activate.called
                    and not c._webex_activation_inflight
                )
            )

        rescan.assert_called_once_with()
        c.bridge.launch_webex.assert_not_called()
        self.assertIn(
            "couldn't verify",
            c.window.flash_message.call_args.args[0],
        )
        self.assertEqual(
            c._webex_events[-1],
            {
                "action": "show-webex-app",
                "result": "refused",
                "reason_code": "reverification-refused",
            },
        )

    def test_stopped_app_launches_verified_app_without_meeting_handoff(self):
        from services.webex_app import (
            WebexActivationResult,
            WebexActivationState,
            WebexAppInfo,
            WebexAppState,
        )

        c = self.controller
        c._webex_app_info = WebexAppInfo(
            state=WebexAppState.INSTALLED,
            publisher_verified=True,
            path=Path("/Applications/Webex.app"),
        )
        with patch(
            "services.webex_app.show_webex_app",
            return_value=WebexActivationResult(
                WebexActivationState.LAUNCHED_APP,
            ),
        ), patch.object(
            c,
            "_start_webex_app_detection",
        ) as rescan:
            c._show_webex_app()
            self.assertTrue(
                _wait_until(lambda: not c._webex_activation_inflight)
            )

        rescan.assert_not_called()
        message = c.window.flash_message.call_args.args[0]
        self.assertIn(
            "launched without a meeting link or browser",
            message,
        )
        self.assertIn("Webex decides which of its own screens", message)
        c.bridge.launch_webex.assert_not_called()
        self.assertEqual(
            c._webex_events[-1],
            {"action": "show-webex-app", "result": "launched-app"},
        )

    def test_shutdown_during_disk_reverification_never_enters_appkit(self):
        from services.webex_app import WebexAppInfo, WebexAppState

        c = self.controller
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "Webex.app"
            app.mkdir()
            info = WebexAppInfo(
                state=WebexAppState.INSTALLED,
                publisher_verified=True,
                path=app,
            )
            c._webex_app_info = info

            def reverify(**_kwargs):
                c._shutdown_in_progress = True
                return info

            with patch(
                "services.webex_app.sys.platform",
                "darwin",
            ), patch(
                "services.webex_app.detect_webex_app",
                side_effect=reverify,
            ), patch(
                "services.webex_app._MacOSApplicationRuntime",
            ) as runtime, patch.object(
                c,
                "_start_webex_app_detection",
            ):
                c._show_webex_app()
                self.assertTrue(
                    _wait_until(lambda: c._shutdown_in_progress)
                )

            runtime.assert_not_called()
        c._shutdown_in_progress = False
        c._webex_activation_inflight = False

    def test_native_activation_cancellation_tracks_generation_change(self):
        from services.webex_app import (
            WebexActivationResult,
            WebexActivationState,
            WebexAppInfo,
            WebexAppState,
        )

        c = self.controller
        c._webex_app_info = WebexAppInfo(
            state=WebexAppState.INSTALLED,
            publisher_verified=True,
            path=Path("/Applications/Webex.app"),
        )
        observed = []

        def activate(_info, *, cancelled):
            observed.append(cancelled())
            c._webex_activation_generation += 1
            observed.append(cancelled())
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "activation-cancelled",
            )

        with patch(
            "services.webex_app.show_webex_app",
            side_effect=activate,
        ) as request:
            c._show_webex_app()
            self.assertTrue(_wait_until(lambda: request.called))

        self.assertEqual(observed, [False, True])
        c._webex_activation_inflight = False

    def test_native_activation_is_single_flight(self):
        from services.webex_app import WebexAppInfo, WebexAppState

        c = self.controller
        c._webex_app_info = WebexAppInfo(
            state=WebexAppState.INSTALLED,
            publisher_verified=True,
            path=Path("/Applications/Webex.app"),
        )
        c._webex_activation_inflight = True
        with patch("services.webex_app.show_webex_app") as activate:
            c._show_webex_app()

        activate.assert_not_called()
        self.assertIn(
            "still verifying",
            c.window.flash_message.call_args.args[0],
        )


class TestJamulusForegroundGuidance(_ControllerTestBase):
    def test_unavailable_jamulus_never_claims_it_is_opening(self):
        c = self.controller
        c.bridge.bring_jamulus_forward = MagicMock(return_value=False)

        c._bring_jamulus_forward()

        message = c.window.flash_message.call_args.args[0]
        self.assertIn("isn’t open yet", message)
        self.assertIn("Start or retry", message)
        self.assertNotIn("still opening", message)


class TestTruthfulWebexState(_ControllerTestBase):
    def test_obsolete_embed_state_is_ignored(self):
        c = self.controller
        c.bridge.webex_state = "Opened externally"
        c._on_webex_state("ACTIVE")
        self.assertEqual(c.bridge.webex_state, "Opened externally")
        c.window.set_status_video.assert_not_called()

    def test_readiness_uses_external_labels_only(self):
        c = self.controller
        c.bridge.webex_state = "Opened externally"
        c._refresh_readiness()
        c.window.set_status_video.assert_called_with("Opened externally")
        c.window.session_strip.set_video_state.assert_called_with(
            "Open Again", enabled=True
        )


class TestLaunchStopAudio(_ControllerTestBase):
    def test_launch_when_stopped_calls_bridge(self):
        c = self.controller
        c.bridge.jamulus_state = "Not launched"
        c.bridge.launch_jamulus = MagicMock()
        c._on_launch_audio()
        c.bridge.launch_jamulus.assert_called_once_with(manual=True)
        c.window.session_strip.set_audio_state.assert_called_with(
            "Launching…", enabled=False
        )

    def test_stop_confirmed_resets_to_disconnected_state(self):
        c = self.controller
        c.bridge.jamulus_state = "Running"
        c._jamulus_connected = True
        c._reconnect_banner_shown = True
        c._rpc_hang_banner_shown = True
        stop_called = MagicMock()
        c.bridge.stop_jamulus = stop_called
        with patch(
            "webjam_qt.controllers.application_controller.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            c._on_launch_audio()
        # Worker thread runs bridge.stop_jamulus — wait briefly for it.
        for _ in range(50):
            QApplication.processEvents()
            if stop_called.called:
                # The worker queues the final UI reset immediately after the
                # service stop; give that queued callback a chance to run.
                QApplication.processEvents()
                if not c._reconnect_banner_shown:
                    break
            import time
            time.sleep(0.02)
        self.assertTrue(stop_called.called)
        self.assertFalse(c._jamulus_connected)
        self.assertFalse(c._reconnect_banner_shown)
        self.assertFalse(c._rpc_hang_banner_shown)
        self.assertEqual(c.participants, {})
        self.assertEqual(
            c.window.participant_grid._empty_title.text(), "Ready when you are"
        )

    def test_stop_warns_when_server_recording_is_active(self):
        c = self.controller
        c.bridge.jamulus_state = "Running"
        c._jamulus_connected = True
        c._server_recording = True
        c.bridge.stop_jamulus = MagicMock()
        try:
            with patch(
                "webjam_qt.controllers.application_controller.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ) as question:
                c._on_launch_audio()
            text = question.call_args.args[2]
            self.assertIn("host's recording will keep running", text)
            self.assertIn("Only this Mac will disconnect", text)
            self.assertIn("Leave this jam", text)
            # Without an active recording the plain wording returns.
            c._server_recording = False
            c._recorder_armed = False
            c.recording.phase = c.recording.phase.__class__.IDLE
            with patch(
                "webjam_qt.controllers.application_controller.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ) as question:
                c._on_launch_audio()
            self.assertNotIn("keeps recording", question.call_args.args[2])
        finally:
            c._server_recording = False
            c._recorder_armed = False
            c._jamulus_connected = False

    def test_stop_declined_makes_no_changes(self):
        c = self.controller
        c.bridge.jamulus_state = "Running"
        c._jamulus_connected = True
        c.bridge.stop_jamulus = MagicMock()
        with patch(
            "webjam_qt.controllers.application_controller.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            c._on_launch_audio()
        c.bridge.stop_jamulus.assert_not_called()
        self.assertTrue(c._jamulus_connected)
        c._jamulus_connected = False


class TestReconnectCrashBanner(_ControllerTestBase):
    def setUp(self):
        super().setUp()
        c = self.controller
        c.bridge.attempt_auto_reconnects = MagicMock()
        c._jamulus_connected = False
        c.audio.recovering = False
        c._reconnect_banner_shown = False
        c._rpc_hang_banner_shown = False
        c._clear_primary_local_roster_proof()

    def test_crash_detected_flashes_reconnect_banner(self):
        c = self.controller
        dead = MagicMock()
        dead.poll.return_value = 1  # exited
        c.bridge.jamulus_process = dead
        c.bridge.jamulus_launch_intended = True
        c.bridge.jamulus_reconnect_attempts = 0
        c._on_reconnect_tick()
        self.assertTrue(c._reconnect_banner_shown)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("reconnecting" in m for m in msgs), msgs)
        c.window.set_status_audio.assert_called_with("Reconnecting…")
        c.bridge.attempt_auto_reconnects.assert_called_once()

    def test_crash_banner_fires_once(self):
        c = self.controller
        dead = MagicMock()
        dead.poll.return_value = 1
        c.bridge.jamulus_process = dead
        c.bridge.jamulus_launch_intended = True
        c._on_reconnect_tick()
        c.window.flash_message.reset_mock()
        c._on_reconnect_tick()
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertFalse(any("auto-reconnecting" in m for m in msgs), msgs)

    def test_dead_replacement_invalidates_truth_even_with_existing_banner(self):
        c = self.controller
        dead = MagicMock()
        dead.poll.return_value = 1
        c.bridge.jamulus_process = dead
        c.bridge.jamulus_launch_intended = True
        c.bridge.jamulus_state = "Running"
        c._reconnect_banner_shown = True
        c._jamulus_connected = True
        c.window.flash_message.reset_mock()

        with patch.object(
            c,
            "_stop_reference_track_for_session_end",
        ) as stop_reference:
            c._on_reconnect_tick()

        self.assertFalse(c._jamulus_connected)
        self.assertTrue(c.audio.recovering)
        stop_reference.assert_called_once_with(background=True)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertFalse(any("reconnected" in m for m in msgs), msgs)

    def test_process_restart_is_not_success_until_local_connection_is_proven(self):
        c = self.controller
        alive = MagicMock()
        alive.pid = 4600
        alive.poll.return_value = None
        c.bridge.jamulus_process = alive
        c.bridge.jamulus_launch_intended = True
        c.bridge.jamulus_state = "Running"
        c.jamulus.rpc_client = MagicMock()
        c.jamulus.rpc_client.available = True
        c.jamulus.rpc_client.last_activity_age.return_value = 0.0
        bind_primary_rpc_monitor(c)
        c._reconnect_banner_shown = True
        c._on_reconnect_tick()
        self.assertTrue(c._reconnect_banner_shown)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertFalse(any("reconnected" in m for m in msgs), msgs)

        # Process existence is implementation truth; the local participant /
        # RPC path is the musician-facing connection proof.
        c._jamulus_connected = True
        recovery = c._primary_jamulus_recovery_snapshot()
        self.assertIsNotNone(recovery)
        c._record_primary_local_roster_proof(recovery)
        c._on_reconnect_tick()
        self.assertFalse(c._reconnect_banner_shown)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("reconnected" in m for m in msgs), msgs)
        c._jamulus_connected = False


class TestRailViewChanges(_ControllerTestBase):
    def _more_action(self, label: str):
        return next(
            action
            for action in self.window.session_strip._tools_button.menu().actions()
            if action.text() == label
        )

    def test_canvas_expands_notes_panel(self):
        c = self.controller
        c._on_rail_view_changed("canvas")
        self.assertEqual(c._last_content_key, "canvas")
        sizes = c.window.center_splitter.sizes()
        self.assertGreater(sizes[1], sizes[0])

    def test_stage_expands_grid(self):
        c = self.controller
        c._on_rail_view_changed("stage")
        self.assertEqual(c._last_content_key, "stage")
        sizes = c.window.center_splitter.sizes()
        self.assertGreater(sizes[0], sizes[1])

    def test_direct_studio_button_opens_live_take_review(self):
        c = self.controller
        with patch.object(
            c.window.reference_studio,
            "show_take_review",
        ) as show_take_review:
            c.window.session_strip._studio_button.click()

        self.assertEqual(c._last_content_key, "takes")
        self.assertIs(
            c.window.workspace_stack.currentWidget(),
            c.window.reference_studio,
        )
        show_take_review.assert_called_once_with()

    def test_dead_placeholder_views_are_not_in_navigation(self):
        keys = {
            button.property("railKey")
            for button in self.window.side_rail._group.buttons()
        }
        self.assertEqual(keys, {"stage", "canvas", "takes", "settings"})

    def test_settings_key_opens_wizard_and_restores_selection(self):
        c = self.controller
        c._on_rail_view_changed("stage")
        c.window.side_rail.set_active_key = MagicMock()
        c._open_settings_wizard = MagicMock()
        c._on_rail_view_changed("settings")
        c.window.side_rail.set_active_key.assert_called_once_with("stage")
        c._open_settings_wizard.assert_called_once()
        del c.__dict__["_open_settings_wizard"]

    def test_visible_more_actions_reach_their_controller_owners(self):
        c = self.controller
        routes = (
            ("Audio Settings in Jamulus", "_bring_jamulus_forward"),
            ("Recording Setup", "_open_recording_setup"),
            ("Reference Track…", "_open_reference_track"),
            ("Use iPhone as Pocket Stage…", "_open_pocket_stage"),
            ("Band Check / Verify Sound\tF2", "_on_ready_check"),
            ("Support", "_on_save_support_bundle"),
            ("WebJam Settings", "_open_settings_wizard"),
        )
        for label, handler_name in routes:
            with self.subTest(label=label), patch.object(
                c,
                handler_name,
            ) as handler:
                action = self._more_action(label)
                host_only = label == "Reference Track…"
                if host_only:
                    self.assertFalse(action.isVisible())
                    action.setVisible(True)
                    action.setEnabled(True)
                action.trigger()
                handler.assert_called_once_with()
                if host_only:
                    action.setVisible(False)

        with patch.object(c.window, "show_help") as show_help:
            self._more_action("Help").trigger()
            show_help.assert_called_once_with()
        with patch.object(c.window, "show_about") as show_about:
            self._more_action("About WebJam").trigger()
            show_about.assert_called_once_with()


class TestSettingsWizard(_ControllerTestBase):
    def _run_wizard(
        self,
        accepted: bool,
        new_settings=None,
        *,
        run_band_check: bool = False,
    ):
        c = self.controller
        wizard = MagicMock()
        with patch(
            "webjam_qt.windows.simple_settings.SimpleSettingsDialog"
        ) as wizard_cls, patch(
            "core.settings.load_settings",
            return_value=new_settings or AppSettings(),
        ):
            wizard_cls.return_value = wizard
            wizard.run_band_check_after_save = run_band_check
            wizard.exec.return_value = (
                wizard_cls.DialogCode.Accepted if accepted else 0
            )
            c._open_settings_wizard()
        return wizard

    def test_rejected_wizard_keeps_settings(self):
        c = self.controller
        before = c.settings
        self._run_wizard(accepted=False)
        self.assertIs(c.settings, before)

    def test_settings_are_blocked_during_session_cleanup(self):
        c = self.controller
        c.audio.stopping = True
        try:
            with patch(
                "webjam_qt.windows.simple_settings.SimpleSettingsDialog"
            ) as wizard_cls:
                c._open_settings_wizard()

            wizard_cls.assert_not_called()
            self.assertIn(
                "session change",
                c.window.flash_message.call_args.args[0],
            )
        finally:
            # The production guard deliberately keeps this latch owned by the
            # in-progress End/Leave worker. This test has no such worker, so
            # restore settled fixture state before class-level shutdown.
            c.audio.stopping = False

    def test_accepted_wizard_reloads_and_pushes_settings(self):
        c = self.controller
        fresh = AppSettings()
        fresh.jamulus_server = "fresh.example.com"
        fresh.jamulus_port = 22224
        fresh.jamulus_rpc_port = 23333
        fresh.webex_url = "https://fresh.webex.com/meet/room"
        fresh.companion_api_port = 8877
        self._run_wizard(accepted=True, new_settings=fresh)
        self.assertIs(c.settings, fresh)
        self.assertIs(c.bridge.settings, fresh)
        self.assertEqual(c.jamulus.host, "fresh.example.com")
        self.assertEqual(c.jamulus.port, 22224)
        self.assertEqual(c.jamulus.rpc_port, 23333)
        self.assertEqual(c.jamulus.rpc_client._port, 23333)
        self.assertEqual(c.webex.meeting_url, "https://fresh.webex.com/meet/room")
        self.assertEqual(c.api_bridge.port, 8877)
        self.assertIs(c._mix_manager._jamulus, c.jamulus)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("Settings saved" in m for m in msgs), msgs)

    def test_run_band_check_uses_the_reloaded_saved_settings(self):
        c = self.controller
        fresh = AppSettings()
        fresh.musician_name = "New Guitar"
        c._on_ready_check = MagicMock()

        with patch.object(QTimer, "singleShot") as single_shot:
            self._run_wizard(
                accepted=True,
                new_settings=fresh,
                run_band_check=True,
            )

        self.assertIs(c.settings, fresh)
        self.assertEqual(c.settings.musician_name, "New Guitar")
        single_shot.assert_called_once_with(0, c._on_ready_check)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertIn("Settings saved. Band Check is using this setup.", msgs)

    def test_accepted_wizard_recreates_visible_start_gate(self):
        c = self.controller
        stale_dialog = MagicMock()
        stale_dialog.isVisible.return_value = True
        stale_dialog._start_session_when_ready = True
        c._ready_check_dialog = stale_dialog
        old_generation = c._settings_generation
        fresh = AppSettings()

        try:
            with patch.object(c, "_open_band_check") as reopen, patch.object(
                QTimer,
                "singleShot",
                side_effect=lambda _delay, callback: callback(),
            ):
                self._run_wizard(accepted=True, new_settings=fresh)

            self.assertEqual(c._settings_generation, old_generation + 1)
            stale_dialog.close.assert_called_once_with()
            reopen.assert_called_once_with(start_session_when_ready=True)
        finally:
            c._ready_check_dialog = None

    def test_accepted_with_changed_webex_url_warns_after_external_launch(self):
        c = self.controller
        c.settings.webex_url = "https://example.webex.com/meet/old"
        c.webex.meeting_url = c.settings.webex_url
        c.bridge.webex_state = "Opened externally"
        c.bridge.launch_webex = MagicMock()
        fresh = AppSettings()
        fresh.webex_url = "https://example.webex.com/meet/other"
        with patch.object(
            c.bridge,
            "invalidate_webex_launch",
            wraps=c.bridge.invalidate_webex_launch,
        ) as invalidate:
            self._run_wizard(accepted=True, new_settings=fresh)
        invalidate.assert_called_once_with()
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(
            any("meeting already open stays open" in m for m in msgs),
            msgs,
        )
        self.assertEqual(c.bridge.webex_state, "Not opened")
        c.window.session_strip.set_video_state.assert_called_with(
            "Open Webex",
            enabled=True,
        )
        c.bridge.launch_webex.assert_not_called()

    def test_accepted_with_changed_server_warns_when_audio_running(self):
        c = self.controller
        c.bridge.jamulus_state = "Running"
        fresh = AppSettings()
        fresh.jamulus_server = "other.example.com"
        self._run_wizard(accepted=True, new_settings=fresh)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("restart the session" in m for m in msgs), msgs)


class TestExportDiagnostics(_ControllerTestBase):
    def test_export_copies_summary_to_clipboard(self):
        c = self.controller
        c._on_export_diagnostics()
        text = QApplication.clipboard().text()
        self.assertIn("WebJam", text)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("Diagnostics copied" in m for m in msgs), msgs)

    def test_export_failure_flashes_error(self):
        c = self.controller
        with patch(
            "webjam_qt.controllers.diagnostics.DiagnosticsExporter",
            side_effect=RuntimeError("boom"),
        ):
            c._on_export_diagnostics()
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("Couldn't export" in m for m in msgs), msgs)

    def test_band_check_support_action_previews_then_saves_zip(self):
        c = self.controller
        with tempfile.TemporaryDirectory() as temp_dir:
            requested = Path(temp_dir) / "WebJam support.zip"
            with patch(
                "webjam_qt.windows.support_bundle_preview."
                "SupportBundlePreviewDialog.exec",
                return_value=QDialog.DialogCode.Accepted,
            ) as preview, patch(
                "PySide6.QtWidgets.QFileDialog.getSaveFileName",
                return_value=(str(requested), "ZIP archives (*.zip)"),
            ) as choose:
                c._on_save_support_bundle()
            preview.assert_called_once_with()
            choose.assert_called_once()
            bundles = list(Path(temp_dir).glob("*.zip"))
            self.assertEqual(len(bundles), 1)
            self.assertTrue(bundles[0].is_file())
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("Support bundle saved" in m for m in msgs), msgs)

    def test_cancelled_privacy_preview_never_opens_save_picker(self):
        c = self.controller
        with patch(
            "webjam_qt.windows.support_bundle_preview."
            "SupportBundlePreviewDialog.exec",
            return_value=QDialog.DialogCode.Rejected,
        ), patch(
            "PySide6.QtWidgets.QFileDialog.getSaveFileName"
        ) as choose:
            c._on_save_support_bundle()
        choose.assert_not_called()


class TestRoutingScanShutdownRace(unittest.TestCase):
    def test_routing_is_automatic_and_starts_no_scan_thread(self):
        window, controller = _make_controller()
        controller.settings.webex_audio_mode = "audience_bridge"
        controller.window.set_status_routing = MagicMock()
        with patch("core.audio_routing.scan_loopback_devices") as scan:
            controller._start_routing_scan()
        scan.assert_not_called()
        controller.window.set_status_routing.assert_called_with("")
        controller.shutdown()


class TestShutdownAutoSave(unittest.TestCase):
    def test_shutdown_autosaves_dirty_mix_when_connected(self):
        window, controller = _make_controller()
        controller._mix_dirty = True
        controller._jamulus_connected = True
        controller._mix_manager.save = MagicMock(return_value=True)
        controller.shutdown()
        controller._mix_manager.save.assert_called_once()
        self.assertFalse(controller._mix_dirty)

    def test_shutdown_skips_autosave_when_demo_only(self):
        window, controller = _make_controller()
        controller._mix_dirty = True
        controller._jamulus_connected = False
        controller._mix_manager.save = MagicMock(return_value=True)
        controller.shutdown()
        controller._mix_manager.save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
