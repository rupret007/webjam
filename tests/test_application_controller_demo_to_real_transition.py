"""
ApplicationController idle<->real participant transition: only confirmed
Jamulus participants become cards; stopping returns to an actionable empty state.
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from jamulus_controller import JamulusParticipant  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


class TestDemoToRealTransition(unittest.TestCase):
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
        # Reset to disconnected state before each test. Also clear the stopping
        # latch: audio.stop() sets it True and only bridge.stop_jamulus()'s
        # refresh_readiness callback clears it in production, but these
        # tests mock stop_jamulus out entirely, so a prior test's stop()
        # would otherwise leak `stopping=True` into the next test and make
        # apply_participants() silently no-op.
        self.controller._jamulus_connected = False
        self.controller.audio.stopping = False
        self.controller._reset_to_demo_state()

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
        self.controller._apply_jamulus_participants(real)

        # Only real channels are rendered.
        self.assertEqual(set(self.controller.participants.keys()), {10, 11})
        self.assertTrue(self.controller._jamulus_connected)
        self.assertEqual(self.controller.participants[10].name, "RealAlice")

        # Stop Audio: patch QMessageBox.question to auto-confirm Yes, and
        # patch bridge.stop_jamulus so the worker thread doesn't actually
        # tear down the controller's services.
        with patch.object(
            QMessageBox, "question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch.object(
            self.controller.bridge, "stop_jamulus", return_value=True,
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
        real = [JamulusParticipant(channel_id=20, name="Bandmate")]
        self.controller._apply_jamulus_participants(real)
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
            rpc = SimpleNamespace(available=False)
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
                self.controller._apply_jamulus_participants(local)
                set_name.assert_not_called()

                # Once client RPC becomes ready, the next authoritative list
                # gets exactly one name handoff.
                rpc.available = True
                self.controller._apply_jamulus_participants(local)
                set_name.assert_called_once_with("Jeff Story")

                # A follow-up roster acknowledgement and a later manual rename
                # in native Jamulus must not produce repeated setName traffic.
                self.controller._apply_jamulus_participants([
                    JamulusParticipant(
                        channel_id=10,
                        name="Jeff Story",
                        is_local=True,
                    )
                ])
                self.controller._apply_jamulus_participants([
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
            rpc = SimpleNamespace(available=True)
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
                    self.controller._apply_jamulus_participants(local)
                self.assertEqual(
                    set_name.call_count,
                    self.controller.audio._NAME_SYNC_MAX_SEND_ATTEMPTS,
                )
        finally:
            self.controller.settings.musician_name = original_name

    def test_native_rename_survives_same_process_reconnect(self):
        """A roster interruption must not overwrite a later native rename."""

        original_name = self.controller.settings.musician_name
        original_process = self.controller.bridge.jamulus_process
        try:
            self.controller.settings.musician_name = "Jeff Story"
            self.controller.bridge.jamulus_process = object()
            rpc = SimpleNamespace(available=True)
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
                self.controller._apply_jamulus_participants(local)
                set_name.assert_called_once_with("Jeff Story")

                self.controller._apply_jamulus_participants([
                    JamulusParticipant(
                        channel_id=10,
                        name="Jeff — Guitar",
                        is_local=True,
                    )
                ])
                self.controller._apply_jamulus_participants([])
                self.controller._apply_jamulus_participants([
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
            self.controller.bridge.jamulus_process = original_process
            self.controller.settings.musician_name = original_name

    def test_replacement_process_receives_fresh_name_handoff(self):
        """A crash replacement starts from its profile and needs the name again."""

        original_name = self.controller.settings.musician_name
        original_process = self.controller.bridge.jamulus_process
        try:
            self.controller.settings.musician_name = "Jeff Story"
            self.controller.bridge.jamulus_process = object()
            rpc = SimpleNamespace(available=True)
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
                self.controller._apply_jamulus_participants(local)
                set_name.assert_called_once_with("Jeff Story")

                self.controller.bridge.jamulus_process = object()
                self.controller.audio.connected = False
                self.controller._apply_jamulus_participants(local)

                self.assertEqual(set_name.call_count, 2)
                set_name.assert_called_with("Jeff Story")
        finally:
            self.controller.bridge.jamulus_process = original_process
            self.controller.settings.musician_name = original_name


if __name__ == "__main__":
    unittest.main()
