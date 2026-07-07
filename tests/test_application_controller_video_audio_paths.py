"""
ApplicationController live-session path coverage (v0.4.9 hardening).

Covers the previously untested Join/Leave Video flow, Webex state-machine
transitions (_on_webex_state), the guest-token refresh tick, the Launch/Stop
Audio toggle (with confirmation dialog), the reconnect-tick crash banner,
the side-rail view handler, the settings wizard round-trip, and the
diagnostics exporter.  All headless (QT_QPA_PLATFORM=offscreen).
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


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
        # Replace the embed pane with a mock — the real one may not have
        # QtWebEngine available headless, and we only assert call routing.
        c.window.webex_embed = MagicMock()
        c.bridge.webex_state = "Not opened"
        c.bridge.jamulus_state = "Not launched"


class TestJoinLeaveVideo(_ControllerTestBase):
    def test_join_without_url_shows_actionable_error(self):
        c = self.controller
        c.settings.webex_url = ""
        c._show_actionable_error = MagicMock()
        c._on_join_video()
        c._show_actionable_error.assert_called_once()
        self.assertEqual(c._show_actionable_error.call_args.args[0], "No Meeting URL")
        c.window.webex_embed.load_meeting.assert_not_called()
        c.window.webex_embed.load_meeting_with_guest_token.assert_not_called()

    def test_join_direct_url_loads_embed(self):
        c = self.controller
        c.settings.webex_url = "https://example.webex.com/meet/band"
        c.settings.webex_guest_issuer_id = ""
        c.settings.webex_guest_issuer_secret = ""
        c._on_join_video()
        c.window.webex_embed.load_meeting.assert_called_once_with(
            "https://example.webex.com/meet/band"
        )
        c.window.webex_embed.load_meeting_with_guest_token.assert_not_called()
        c.window.session_strip.set_video_state.assert_called_with(
            "Joining…", enabled=False
        )

    def test_join_invalid_webex_url_shows_actionable_error(self):
        c = self.controller
        c.settings.webex_url = "http://example.webex.com/meet/band"
        c._show_actionable_error = MagicMock()

        c._on_join_video()

        c._show_actionable_error.assert_called_once()
        self.assertEqual(
            c._show_actionable_error.call_args.args[0],
            "Invalid Webex URL",
        )
        c.window.webex_embed.load_meeting.assert_not_called()
        c.window.webex_embed.load_meeting_with_guest_token.assert_not_called()

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
        c.window.webex_embed.load_meeting.assert_not_called()
        c.window.webex_embed.load_meeting_with_guest_token.assert_not_called()

    def test_join_with_guest_creds_uses_token_path(self):
        c = self.controller
        c.settings.webex_url = "https://example.webex.com/meet/band"
        c.settings.webex_guest_issuer_id = "issuer-123"
        c.settings.webex_guest_issuer_secret = "c2VjcmV0"
        c.settings.webex_display_name = "Jeff"
        c._on_join_video()
        c.window.webex_embed.load_meeting_with_guest_token.assert_called_once_with(
            "https://example.webex.com/meet/band",
            issuer_id="issuer-123",
            secret_b64="c2VjcmV0",
            display_name="Jeff",
        )
        c.window.webex_embed.load_meeting.assert_not_called()
        # Restore defaults for sibling tests
        c.settings.webex_guest_issuer_id = ""
        c.settings.webex_guest_issuer_secret = ""

    def test_join_while_active_leaves_instead(self):
        c = self.controller
        c.bridge.webex_state = "In Meeting"
        c._on_join_video()
        c.window.webex_embed.leave_meeting.assert_called_once()
        c.window.webex_embed.load_meeting.assert_not_called()
        self.assertEqual(c.bridge.webex_state, "Not opened")
        self.assertFalse(c.bridge.webex_launch_intended)


class TestWebexStateMachine(_ControllerTestBase):
    def test_active_state(self):
        c = self.controller
        c._on_webex_state("ACTIVE")
        c.window.set_status_video.assert_called_with("In Meeting")
        c.window.session_strip.set_video_state.assert_called_with(
            "Leave Video", enabled=True
        )
        self.assertEqual(c.bridge.webex_state, "In Meeting")
        self.assertTrue(c._is_video_active())

    def test_lobby_state(self):
        c = self.controller
        c._on_webex_state("lobby")
        c.window.set_status_video.assert_called_with("Lobby")
        self.assertEqual(c.bridge.webex_state, "Lobby")
        self.assertTrue(c._is_video_active())

    def test_ended_state_resets_toggle(self):
        c = self.controller
        c._on_webex_state("ENDED")
        c.window.set_status_video.assert_called_with("Meeting ended")
        c.window.session_strip.set_video_state.assert_called_with(
            "Join Video", enabled=True
        )
        self.assertEqual(c.bridge.webex_state, "Not opened")
        self.assertFalse(c._is_video_active())

    def test_left_state_resets_toggle(self):
        c = self.controller
        c._on_webex_state("left")
        self.assertEqual(c.bridge.webex_state, "Not opened")
        self.assertFalse(c._is_video_active())

    def test_error_state_restores_placeholder_and_flashes(self):
        c = self.controller
        c._on_webex_state("error")
        c.window.webex_embed.leave_meeting.assert_called_once()
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("browser" in m for m in msgs), msgs)
        self.assertEqual(c.bridge.webex_state, "Not opened")

    def test_joining_state_disables_button(self):
        c = self.controller
        c._on_webex_state("joining")
        c.window.session_strip.set_video_state.assert_called_with(
            "Joining…", enabled=False
        )
        self.assertEqual(c.bridge.webex_state, "Joining…")
        self.assertTrue(c._is_video_active())

    def test_unknown_state_falls_back_to_title_case(self):
        c = self.controller
        c._on_webex_state("reconnecting")
        c.window.set_status_video.assert_called_with("Reconnecting")
        self.assertEqual(c.bridge.webex_state, "Reconnecting")


class TestTokenRefreshTick(_ControllerTestBase):
    def test_noop_without_guest_credentials(self):
        c = self.controller
        c.settings.webex_guest_issuer_id = ""
        c.settings.webex_guest_issuer_secret = ""
        c._on_token_refresh_tick()
        c.window.webex_embed.maybe_refresh_token.assert_not_called()

    def test_refresh_called_with_credentials(self):
        c = self.controller
        c.settings.webex_guest_issuer_id = "iss"
        c.settings.webex_guest_issuer_secret = "sec"
        c.settings.webex_display_name = "Jeff"
        c._on_token_refresh_tick()
        c.window.webex_embed.maybe_refresh_token.assert_called_once_with(
            issuer_id="iss", secret_b64="sec", display_name="Jeff"
        )
        c.settings.webex_guest_issuer_id = ""
        c.settings.webex_guest_issuer_secret = ""

    def test_refresh_exception_is_swallowed(self):
        c = self.controller
        c.settings.webex_guest_issuer_id = "iss"
        c.settings.webex_guest_issuer_secret = "sec"
        c.window.webex_embed.maybe_refresh_token.side_effect = RuntimeError("boom")
        c._on_token_refresh_tick()  # must not raise
        c.settings.webex_guest_issuer_id = ""
        c.settings.webex_guest_issuer_secret = ""


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

    def test_stop_confirmed_resets_to_demo(self):
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
            if stop_called.called:
                break
            import time
            time.sleep(0.02)
        self.assertTrue(stop_called.called)
        self.assertFalse(c._jamulus_connected)
        self.assertFalse(c._reconnect_banner_shown)
        self.assertFalse(c._rpc_hang_banner_shown)
        # Demo participants restored
        names = {p.name for p in c.participants.values()}
        self.assertIn("You", names)
        self.assertEqual(len(c.participants), 5)

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
        c._reconnect_banner_shown = False
        c._rpc_hang_banner_shown = False

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
        self.assertTrue(any("auto-reconnecting" in m for m in msgs), msgs)
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

    def test_reconnect_success_clears_banner(self):
        c = self.controller
        alive = MagicMock()
        alive.poll.return_value = None
        c.bridge.jamulus_process = alive
        c.bridge.jamulus_launch_intended = True
        c.bridge.jamulus_state = "Running"
        c._reconnect_banner_shown = True
        c._on_reconnect_tick()
        self.assertFalse(c._reconnect_banner_shown)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("reconnected" in m for m in msgs), msgs)


class TestRailViewChanges(_ControllerTestBase):
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

    def test_chat_flashes_coming_soon_and_restores_selection(self):
        c = self.controller
        c._on_rail_view_changed("mixer")
        c.window.side_rail.set_active_key = MagicMock()
        c._on_rail_view_changed("chat")
        c.window.side_rail.set_active_key.assert_called_once_with("mixer")
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("Chat" in m for m in msgs), msgs)

    def test_roles_flashes_coming_soon(self):
        c = self.controller
        c.window.side_rail.set_active_key = MagicMock()
        c._on_rail_view_changed("roles")
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("Role management" in m for m in msgs), msgs)

    def test_settings_key_opens_wizard_and_restores_selection(self):
        c = self.controller
        c._on_rail_view_changed("stage")
        c.window.side_rail.set_active_key = MagicMock()
        c._open_settings_wizard = MagicMock()
        c._on_rail_view_changed("settings")
        c.window.side_rail.set_active_key.assert_called_once_with("stage")
        c._open_settings_wizard.assert_called_once()
        del c.__dict__["_open_settings_wizard"]


class TestSettingsWizard(_ControllerTestBase):
    def _run_wizard(self, accepted: bool, new_settings=None):
        c = self.controller
        wizard = MagicMock()
        with patch(
            "webjam_qt.windows.setup_wizard.SetupWizard"
        ) as wizard_cls, patch(
            "core.settings.load_settings",
            return_value=new_settings or AppSettings(),
        ):
            wizard_cls.return_value = wizard
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

    def test_accepted_with_changed_webex_url_warns_when_video_active(self):
        c = self.controller
        c.bridge.webex_state = "In Meeting"
        fresh = AppSettings()
        fresh.webex_url = "https://example.webex.com/meet/other"
        self._run_wizard(accepted=True, new_settings=fresh)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("re-join" in m for m in msgs), msgs)

    def test_accepted_with_changed_server_warns_when_audio_running(self):
        c = self.controller
        c.bridge.jamulus_state = "Running"
        fresh = AppSettings()
        fresh.jamulus_server = "other.example.com"
        self._run_wizard(accepted=True, new_settings=fresh)
        msgs = [call.args[0] for call in c.window.flash_message.call_args_list]
        self.assertTrue(any("re-launch" in m for m in msgs), msgs)


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


class TestRoutingScanShutdownRace(unittest.TestCase):
    def test_scan_survives_invoker_destroyed_mid_scan(self):
        """If the app shuts down while the routing scan is in flight, the
        Qt invoker may already be deleted when the scan finishes.  The scan
        thread must swallow that RuntimeError instead of dying with a
        traceback (regression: noisy 'Internal C++ object already deleted')."""
        import threading
        import time

        window, controller = _make_controller()
        controller._ui_invoker.invoke = MagicMock(
            side_effect=RuntimeError("Internal C++ object already deleted")
        )
        with self.assertLogs("webjam.qt.application_controller", level="DEBUG") as logs:
            controller._start_routing_scan()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and any(
                t.name == "routing-scan" and t.is_alive()
                for t in threading.enumerate()
            ):
                time.sleep(0.02)
        self.assertTrue(
            any("routing status dropped" in line for line in logs.output),
            logs.output,
        )
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
