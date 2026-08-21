"""
ApplicationController idle<->real participant transition: only confirmed
Jamulus participants become cards; stopping returns to an actionable empty state.
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from jamulus_controller import JamulusParticipant  # noqa: E402
from tests.support.jamulus_monitor import bind_primary_rpc_monitor  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


def _live_primary(pid: int) -> MagicMock:
    process = MagicMock()
    process.pid = pid
    process.poll.return_value = None
    return process


class TestDemoToRealTransition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._microphone_permission_patch = patch(
            "webjam_qt.platform_permissions.microphone_permission_status",
            return_value="authorized",
        )
        cls._microphone_permission_patch.start()
        cls.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Test",
        )
        cls.controller = ApplicationController(cls.window, settings=AppSettings())

    @classmethod
    def tearDownClass(cls):
        cls._microphone_permission_patch.stop()
        cls.controller.shutdown()

    def setUp(self):
        # Reset to disconnected state before each test. Also clear the stopping
        # latch: audio.stop() sets it True and only bridge.stop_jamulus()'s
        # refresh_readiness callback clears it in production, but these
        # tests mock stop_jamulus out entirely, so a prior test's stop()
        # would otherwise leak `stopping=True` into the next test and make
        # apply_participants() silently no-op.
        self.controller._jamulus_connected = False
        self.controller.audio.stopping = False
        self.controller.audio.cleanup_retry_required = False
        self.controller._reconnect_gave_up = False
        self.controller.bridge.jamulus_process = None
        self.controller.bridge.jamulus_launch_intended = False
        self.controller.bridge.jamulus_state = "Not launched"
        self.controller._reset_to_demo_state()
        self.controller.bridge.jamulus_process = _live_primary(4100)
        self.controller.bridge.jamulus_launch_intended = True
        self.controller.bridge.jamulus_state = "Running"
        rpc = MagicMock()
        rpc.available = True
        rpc.last_activity_age.return_value = 0.1
        self.controller.jamulus.rpc_client = rpc
        self.source_identity = bind_primary_rpc_monitor(self.controller)

    def _apply_participants(self, participants, *, source_identity=None):
        self.controller._apply_jamulus_participants(
            participants,
            source_identity=source_identity or self.source_identity,
        )

    def test_idle_replaced_then_restored_after_stop_audio(self):
        self.assertEqual(self.controller.participants, {})
        self.assertFalse(self.window.participant_grid._empty_state.isHidden())
        self.assertEqual(
            self.window.participant_grid._empty_title.text(), "Ready when you are"
        )

        # Real participants arrive via Jamulus callback.
        real = [
            JamulusParticipant(channel_id=10, name="RealAlice", is_local=True),
            JamulusParticipant(channel_id=11, name="RealBob"),
        ]
        self._apply_participants(real)

        # Only real channels are rendered.
        self.assertEqual(set(self.controller.participants.keys()), {10, 11})
        self.assertTrue(self.controller._jamulus_connected)
        self.assertEqual(self.controller.participants[10].name, "RealAlice")

        # Stop Audio: patch QMessageBox.question to auto-confirm Yes, and
        # patch bridge.stop_jamulus so the worker thread doesn't actually
        # tear down the controller's services.
        def stop_primary() -> bool:
            self.controller.bridge.jamulus_process = None
            self.controller.bridge.jamulus_launch_intended = False
            self.controller.bridge.jamulus_state = "Stopped"
            return True

        with patch.object(
            QMessageBox, "question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch.object(
            self.controller.bridge,
            "stop_jamulus",
            side_effect=stop_primary,
        ):
            self.controller._stop_audio()

        # End Session now preserves recorder truth until its background
        # worker has finalized the take and stopped owned services. Process
        # the queued UI completion before asserting the disconnected view.
        for _ in range(100):
            QApplication.processEvents()
            if not self.controller.audio.stopping:
                break
            import time
            time.sleep(0.01)

        # Truthful disconnected state restored; no fake mixer cards.
        self.assertFalse(self.controller._jamulus_connected)
        self.assertEqual(self.controller.participants, {})
        self.assertEqual(
            self.window.participant_grid._empty_title.text(), "Ready when you are"
        )

    def test_stop_audio_cancelled_keeps_real_state(self):
        # Connect to real participants
        real = [
            JamulusParticipant(
                channel_id=20,
                name="Bandmate",
                is_local=True,
            )
        ]
        self._apply_participants(real)
        self.assertTrue(self.controller._jamulus_connected)

        # User clicks "No" — controller should leave state alone.
        with patch.object(
            QMessageBox, "question",
            return_value=QMessageBox.StandardButton.No,
        ), patch.object(
            self.controller.bridge, "stop_jamulus", return_value=True,
        ):
            self.controller._stop_audio()

        self.assertTrue(self.controller._jamulus_connected)
        self.assertIn(20, self.controller.participants)

    def test_hosted_name_waits_for_rpc_then_sends_once(self):
        """An early server-roster callback must not consume the name handoff."""

        original_hosting = self.controller.settings.host_server_enabled
        original_name = self.controller.settings.musician_name
        try:
            self.controller.settings.host_server_enabled = True
            self.controller.settings.musician_name = "Jeff Story"
            rpc = SimpleNamespace(
                available=False,
                last_activity_age=lambda: 0.1,
            )
            local = [
                JamulusParticipant(
                    channel_id=10,
                    name="No Name",
                    is_local=True,
                )
            ]

            with patch.object(
                self.controller.jamulus,
                "rpc_client",
                rpc,
            ), patch.object(
                self.controller.jamulus,
                "set_name",
                return_value=True,
            ) as set_name:
                # The hosted-server roster can arrive before authenticated
                # client RPC. It proves audio presence, but cannot yet accept
                # the musician profile update.
                self._apply_participants(local)
                set_name.assert_not_called()

                # Once client RPC becomes ready, the next authoritative list
                # gets exactly one name handoff.
                rpc.available = True
                self._apply_participants(local)
                set_name.assert_called_once_with("Jeff Story")

                # A follow-up roster acknowledgement and a later manual rename
                # in native Jamulus must not produce repeated setName traffic.
                self._apply_participants([
                    JamulusParticipant(
                        channel_id=10,
                        name="Jeff Story",
                        is_local=True,
                    )
                ])
                self._apply_participants([
                    JamulusParticipant(
                        channel_id=10,
                        name="Jeff — Guitar",
                        is_local=True,
                    )
                ])
                set_name.assert_called_once_with("Jeff Story")
                self.assertEqual(
                    self.controller.participants[10].name,
                    "Jeff — Guitar",
                )
        finally:
            self.controller.settings.host_server_enabled = original_hosting
            self.controller.settings.musician_name = original_name

    def test_name_sync_send_failures_are_bounded(self):
        original_name = self.controller.settings.musician_name
        try:
            self.controller.settings.musician_name = "Jeff Story"
            rpc = SimpleNamespace(
                available=True,
                last_activity_age=lambda: 0.1,
            )
            local = [
                JamulusParticipant(
                    channel_id=10,
                    name="No Name",
                    is_local=True,
                )
            ]
            with patch.object(
                self.controller.jamulus,
                "rpc_client",
                rpc,
            ), patch.object(
                self.controller.jamulus,
                "set_name",
                return_value=False,
            ) as set_name:
                for _ in range(8):
                    self._apply_participants(local)
                self.assertEqual(
                    set_name.call_count,
                    self.controller.audio._NAME_SYNC_MAX_SEND_ATTEMPTS,
                )
        finally:
            self.controller.settings.musician_name = original_name

    def test_invalid_legacy_name_never_enters_rpc_retry_loop(self):
        original_name = self.controller.settings.musician_name
        try:
            self.controller.settings.musician_name = "12345678901234567"
            rpc = SimpleNamespace(
                available=True,
                last_activity_age=lambda: 0.1,
            )
            local = [
                JamulusParticipant(
                    channel_id=10,
                    name="No Name",
                    is_local=True,
                )
            ]
            with patch.object(
                self.controller.jamulus,
                "rpc_client",
                rpc,
            ), patch.object(
                self.controller.jamulus,
                "set_name",
                return_value=True,
            ) as set_name:
                for _ in range(8):
                    self._apply_participants(local)
                set_name.assert_not_called()
                self.assertEqual(
                    self.controller.audio._name_sync_send_attempts,
                    0,
                )
        finally:
            self.controller.settings.musician_name = original_name

    def test_native_rename_survives_same_process_reconnect(self):
        """A roster interruption must not overwrite a later native rename."""

        original_name = self.controller.settings.musician_name
        original_process = self.controller.bridge.jamulus_process
        try:
            self.controller.settings.musician_name = "Jeff Story"
            self.controller.bridge.jamulus_process = _live_primary(4101)
            source_identity = bind_primary_rpc_monitor(
                self.controller,
                process_generation=2,
            )
            rpc = SimpleNamespace(
                available=True,
                last_activity_age=lambda: 0.1,
            )
            local = [
                JamulusParticipant(
                    channel_id=10,
                    name="No Name",
                    is_local=True,
                )
            ]
            with patch.object(
                self.controller.jamulus,
                "rpc_client",
                rpc,
            ), patch.object(
                self.controller.jamulus,
                "set_name",
                return_value=True,
            ) as set_name:
                self._apply_participants(
                    local,
                    source_identity=source_identity,
                )
                set_name.assert_called_once_with("Jeff Story")

                self._apply_participants([
                    JamulusParticipant(
                        channel_id=10,
                        name="Jeff — Guitar",
                        is_local=True,
                    )
                ], source_identity=source_identity)
                self._apply_participants(
                    [],
                    source_identity=source_identity,
                )
                self._apply_participants([
                    JamulusParticipant(
                        channel_id=10,
                        name="Jeff — Guitar",
                        is_local=True,
                    )
                ], source_identity=source_identity)

                set_name.assert_called_once_with("Jeff Story")
                self.assertEqual(
                    self.controller.participants[10].name,
                    "Jeff — Guitar",
                )
        finally:
            self.controller.bridge.jamulus_process = original_process
            self.controller.settings.musician_name = original_name

    def test_replacement_process_receives_fresh_name_handoff(self):
        """A crash replacement starts from its profile and needs the name again."""

        original_name = self.controller.settings.musician_name
        original_process = self.controller.bridge.jamulus_process
        try:
            self.controller.settings.musician_name = "Jeff Story"
            self.controller.bridge.jamulus_process = _live_primary(4102)
            source_identity = bind_primary_rpc_monitor(
                self.controller,
                process_generation=2,
            )
            rpc = SimpleNamespace(
                available=True,
                last_activity_age=lambda: 0.1,
            )
            local = [
                JamulusParticipant(
                    channel_id=10,
                    name="No Name",
                    is_local=True,
                )
            ]
            with patch.object(
                self.controller.jamulus,
                "rpc_client",
                rpc,
            ), patch.object(
                self.controller.jamulus,
                "set_name",
                return_value=True,
            ) as set_name:
                self._apply_participants(
                    local,
                    source_identity=source_identity,
                )
                set_name.assert_called_once_with("Jeff Story")

                self.controller.bridge.jamulus_process = _live_primary(4103)
                replacement_identity = bind_primary_rpc_monitor(
                    self.controller,
                    process_generation=3,
                )
                self.controller.audio.connected = False
                self._apply_participants(
                    local,
                    source_identity=replacement_identity,
                )

                self.assertEqual(set_name.call_count, 2)
                set_name.assert_called_with("Jeff Story")
        finally:
            self.controller.bridge.jamulus_process = original_process
            self.controller.settings.musician_name = original_name


if __name__ == "__main__":
    unittest.main()
