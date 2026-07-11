"""
Tests for ApplicationController toggle behaviour (v0.4.4).

Verifies the Launch ↔ Stop and Join ↔ Leave button state transitions in
``_refresh_readiness`` and the ``_is_jamulus_running`` / ``_is_video_active``
helpers. Uses QT_QPA_PLATFORM=offscreen so no display is required.
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.widgets.participant_card import ParticipantPresentation  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


class TestToggleButtonState(unittest.TestCase):
    """Verify the audio/video buttons toggle labels and helpers behave."""

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

    # -- helper predicates -------------------------------------------------
    def test_is_jamulus_running_false_when_state_is_initial(self):
        self.controller.bridge.jamulus_state = "Not launched"
        self.assertFalse(self.controller._is_jamulus_running())

    def test_is_jamulus_running_true_when_state_is_running(self):
        self.controller.bridge.jamulus_state = "Running"
        self.assertTrue(self.controller._is_jamulus_running())

    def test_is_jamulus_running_true_when_state_is_already_running(self):
        self.controller.bridge.jamulus_state = "Already running"
        self.assertTrue(self.controller._is_jamulus_running())

    def test_is_video_active_false_when_not_opened(self):
        self.controller.bridge.webex_state = "Not opened"
        self.assertFalse(self.controller._is_video_active())

    def test_is_video_active_true_for_in_meeting(self):
        self.controller.bridge.webex_state = "In Meeting"
        self.assertTrue(self.controller._is_video_active())

    def test_is_video_active_true_for_opened_in_browser(self):
        self.controller.bridge.webex_state = "Opened in browser"
        self.assertTrue(self.controller._is_video_active())

    def test_is_video_active_true_for_lobby(self):
        self.controller.bridge.webex_state = "Lobby"
        self.assertTrue(self.controller._is_video_active())

    # -- button labels -----------------------------------------------------
    def test_audio_button_says_launch_when_stopped(self):
        self.controller.bridge.jamulus_state = "Not launched"
        self.controller.bridge.webex_state = "Not opened"
        self.controller._refresh_readiness()
        self.assertEqual(
            self.window.session_strip._audio_button.text(), "Start Audio"
        )

    def test_audio_button_says_stop_when_running(self):
        self.controller.bridge.jamulus_state = "Running"
        self.controller.bridge.webex_state = "Not opened"
        self.controller._refresh_readiness()
        self.assertEqual(
            self.window.session_strip._audio_button.text(), "Stop Audio"
        )

    def test_video_button_says_join_when_not_active(self):
        self.controller.bridge.jamulus_state = "Not launched"
        self.controller.bridge.webex_state = "Not opened"
        self.controller._refresh_readiness()
        self.assertEqual(
            self.window.session_strip._video_button.text(), "Join Video"
        )

    def test_video_button_says_leave_when_in_meeting(self):
        self.controller.bridge.jamulus_state = "Not launched"
        self.controller.bridge.webex_state = "In Meeting"
        self.controller._refresh_readiness()
        self.assertEqual(
            self.window.session_strip._video_button.text(), "Leave Video"
        )

    def test_status_audio_includes_server_when_running(self):
        self.controller.bridge.jamulus_state = "Running"
        self.controller.bridge.webex_state = "Not opened"
        self.controller._jamulus_connected = False
        self.controller._refresh_readiness()
        # Status bar should avoid "Running" until participant/RPC truth arrives.
        text = self.window._status_audio.text()
        self.assertIn("Connecting", text)
        self.assertIn(":", text)  # server:port separator
        self.assertIn(str(self.controller.settings.jamulus_port), text)


class TestMuteSelf(unittest.TestCase):
    """Test the SessionStrip 'Mute Me' button behaviour."""

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
        self.controller._jamulus_connected = False
        self.controller._reset_to_demo_state()
        self.controller.participants[0] = ParticipantPresentation(
            channel_id=0, name="You", is_local=True
        )
        self.controller._push_participants_to_grid()

    def test_mute_self_without_live_session_does_not_toggle(self):
        local = self.controller.participants[0]
        self.assertTrue(local.is_local)
        self.assertFalse(local.muted)

        self.controller._on_mute_self()

        self.assertFalse(self.controller.participants[0].muted)
        self.assertEqual(self.window.session_strip._mute_self_button.text(), "Mute Me")
        self.assertFalse(self.window.session_strip._mute_self_button.isChecked())

    def test_mute_self_with_live_rpc_success_toggles_mute(self):
        self.controller._jamulus_connected = True
        with mock.patch.object(
            self.controller.jamulus, "set_self_muted", return_value=True
        ):
            self.controller._on_mute_self()

        self.assertTrue(self.controller.participants[0].muted)
        self.assertEqual(
            self.window.session_strip._mute_self_button.text(), "Unmute Me"
        )
        self.assertTrue(self.window.session_strip._mute_self_button.isChecked())

    def test_mute_self_unmutes_when_already_muted(self):
        # First mute, then unmute
        self.controller._jamulus_connected = True
        with mock.patch.object(
            self.controller.jamulus, "set_self_muted", return_value=True
        ):
            self.controller._on_mute_self()
            self.controller._on_mute_self()

        self.assertFalse(self.controller.participants[0].muted)
        self.assertEqual(
            self.window.session_strip._mute_self_button.text(), "Mute Me"
        )
        self.assertFalse(self.window.session_strip._mute_self_button.isChecked())

    def test_mute_self_rpc_failure_reverts_button_and_participant(self):
        self.controller._jamulus_connected = True
        with mock.patch.object(
            self.controller.jamulus, "set_self_muted", return_value=False
        ):
            self.controller._on_mute_self()

        self.assertFalse(self.controller.participants[0].muted)
        self.assertEqual(self.window.session_strip._mute_self_button.text(), "Mute Me")
        self.assertFalse(self.window.session_strip._mute_self_button.isChecked())

    def test_sync_self_mute_button_reflects_local_user_mute_state(self):
        self.controller.participants[0].muted = True
        self.controller._sync_self_mute_button()
        self.assertTrue(self.window.session_strip._mute_self_button.isChecked())

        self.controller.participants[0].muted = False
        self.controller._sync_self_mute_button()
        self.assertFalse(self.window.session_strip._mute_self_button.isChecked())


class TestAloneOnServerStatus(unittest.TestCase):
    """When the user is alone on the Jamulus server, show a friendly hint."""

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

    def test_single_participant_shows_waiting_message(self):
        """v0.4.4: solo participant triggers 'waiting for others' hint."""
        from jamulus_controller import JamulusParticipant
        p = JamulusParticipant(channel_id=0, name="You", is_local=True)
        self.controller._apply_jamulus_participants([p])
        text = self.window._status_latency.text()
        self.assertIn("1 participant", text)
        self.assertIn("waiting for others", text)

    def test_multiple_participants_show_plain_count(self):
        """Two or more participants show '{N} participants' without hint."""
        from jamulus_controller import JamulusParticipant
        participants = [
            JamulusParticipant(channel_id=0, name="You", is_local=True),
            JamulusParticipant(channel_id=1, name="Bandmate"),
        ]
        self.controller._apply_jamulus_participants(participants)
        text = self.window._status_latency.text()
        self.assertIn("2 participants", text)
        self.assertNotIn("waiting", text)


class TestMutedCardDimming(unittest.TestCase):
    """Muted participant cards set a 'muted' Qt property for QSS to dim them."""

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

    def test_muting_card_sets_muted_property(self):
        self.controller.participants[5] = ParticipantPresentation(
            channel_id=5, name="Bandmate"
        )
        self.controller._push_participants_to_grid()
        cards = list(self.window.participant_grid._cards.values())
        self.assertTrue(cards, "expected a real participant card to exist")
        c = cards[0]
        c._apply_mute_state(True)
        self.assertEqual(c.property("muted"), "true")
        c._apply_mute_state(False)
        self.assertEqual(c.property("muted"), "false")


class TestSessionMetadataPersistence(unittest.TestCase):
    """v0.4.4: title and mode persist via ~/.webjam_session.json."""

    def test_save_and_load_round_trips_title_and_mode(self):
        import json
        import os
        import tempfile

        # Redirect HOME to a temp dir so we don't clobber the user's file
        old_home = os.environ.get("HOME", "")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["HOME"] = tmp

            window = ConductorWindow(
                mode_entries=ApplicationController.mode_entries(),
                initial_mode_key="music_jam",
                initial_title="Original",
            )
            controller = ApplicationController(window, settings=AppSettings())
            try:
                # Change title and mode, save
                window.session_strip._title_input.setText("Saved Session")
                # Pick a mode that's NOT music_jam
                modes = ApplicationController.mode_entries()
                non_default = next((k for k, _ in modes if k != "music_jam"), None)
                if non_default:
                    picker = window.session_strip._mode_picker
                    idx = picker.findData(non_default)
                    if idx >= 0:
                        picker.setCurrentIndex(idx)

                controller._save_session_title()

                # Verify on-disk JSON
                path = f"{tmp}/.webjam_session.json"
                self.assertTrue(os.path.exists(path))
                data = json.loads(open(path).read())
                self.assertEqual(data["title"], "Saved Session")
                if non_default:
                    self.assertEqual(data["mode"], non_default)
            finally:
                controller.shutdown()
                # Restore HOME for subsequent tests
                if old_home:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)


if __name__ == "__main__":
    unittest.main()
