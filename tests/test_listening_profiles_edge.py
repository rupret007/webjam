from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from webjam_app_enhanced import WebJamEnhancedApp


class TestListeningProfilesEdge(unittest.TestCase):
    def _app_stub(self) -> WebJamEnhancedApp:
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = object()
        app.mode_key = "music_jam"
        app.current_user = None
        app.repository = MagicMock()
        app.metrics_service = MagicMock()
        app.auth_controller = MagicMock()
        app.auth_controller.authorize.return_value = True
        app.jamulus_controller = MagicMock()
        app._refresh_mixer_controls_from_participants = MagicMock()
        app._refresh_readiness = MagicMock()
        return app

    def test_resolve_profile_name_is_case_insensitive(self):
        profiles = [{"profile_name": "Focus Drums", "mode_key": "music_jam"}]
        self.assertEqual(
            WebJamEnhancedApp._resolve_profile_name("focus drums", profiles),
            "Focus Drums",
        )

    @patch("webjam_app_enhanced.messagebox")
    @patch("webjam_app_enhanced.simpledialog.askstring", return_value="Room Focus")
    def test_save_listening_profile_success(self, _ask, messagebox_mock):
        app = self._app_stub()
        app.jamulus_controller.get_participants.return_value = [SimpleNamespace(name="Alex")]
        app.jamulus_controller.serialize_mix.return_value = {"participants": [{"channel_id": 0}]}
        app.repository.get_mix_profile.return_value = None

        app.save_listening_profile()

        app.repository.save_mix_profile.assert_called_once_with(
            "Room Focus",
            "music_jam",
            {"participants": [{"channel_id": 0}]},
        )
        app.metrics_service.increment.assert_called_once_with("metric_listening_profile_save_success")
        messagebox_mock.showinfo.assert_called_once()

    @patch("webjam_app_enhanced.messagebox")
    def test_save_listening_profile_warns_without_participants(self, messagebox_mock):
        app = self._app_stub()
        app.jamulus_controller.get_participants.return_value = []

        app.save_listening_profile()

        app.repository.save_mix_profile.assert_not_called()
        messagebox_mock.showwarning.assert_called_once()

    @patch("webjam_app_enhanced.messagebox")
    def test_load_listening_profile_shows_info_when_empty(self, messagebox_mock):
        app = self._app_stub()
        app.repository.list_mix_profiles.return_value = []

        app.load_listening_profile()

        messagebox_mock.showinfo.assert_called_once()
        app.jamulus_controller.apply_mix_data.assert_not_called()

    @patch("webjam_app_enhanced.messagebox")
    @patch("webjam_app_enhanced.simpledialog.askstring", return_value="focus drums")
    def test_load_listening_profile_success(self, _ask, messagebox_mock):
        app = self._app_stub()
        app.repository.list_mix_profiles.side_effect = [
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
        ]
        app.repository.get_mix_profile.return_value = {
            "profile_name": "Focus Drums",
            "mode_key": "music_jam",
            "payload": {"participants": [{"channel_id": 0, "fader_level": 80}]},
        }

        app.load_listening_profile()

        app.jamulus_controller.apply_mix_data.assert_called_once_with(
            {"participants": [{"channel_id": 0, "fader_level": 80}]}
        )
        app._refresh_mixer_controls_from_participants.assert_called_once()
        app._refresh_readiness.assert_called_once()
        app.metrics_service.increment.assert_called_once_with("metric_listening_profile_load_success")
        messagebox_mock.showinfo.assert_called_once()

    @patch("webjam_app_enhanced.messagebox")
    @patch("webjam_app_enhanced.simpledialog.askstring", return_value="Ghost")
    def test_load_listening_profile_warns_on_unknown_name(self, _ask, messagebox_mock):
        app = self._app_stub()
        app.repository.list_mix_profiles.side_effect = [
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
        ]

        app.load_listening_profile()

        messagebox_mock.showwarning.assert_called_once()
        app.jamulus_controller.apply_mix_data.assert_not_called()

    @patch("webjam_app_enhanced.messagebox")
    @patch("webjam_app_enhanced.simpledialog.askstring", return_value="focus drums")
    def test_delete_listening_profile_success(self, _ask, messagebox_mock):
        app = self._app_stub()
        app.repository.list_mix_profiles.side_effect = [
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
        ]
        app.repository.delete_mix_profile.return_value = True
        messagebox_mock.askokcancel.return_value = True

        app.delete_listening_profile()

        app.repository.delete_mix_profile.assert_called_once_with("Focus Drums")
        app.metrics_service.increment.assert_called_once_with("metric_listening_profile_delete_success")
        messagebox_mock.showinfo.assert_called_once()

    @patch("webjam_app_enhanced.messagebox")
    @patch("webjam_app_enhanced.simpledialog.askstring", return_value="Focus Drums")
    def test_delete_listening_profile_failed_delete_increments_failure_metric(self, _ask, messagebox_mock):
        app = self._app_stub()
        app.repository.list_mix_profiles.side_effect = [
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
        ]
        app.repository.delete_mix_profile.return_value = False
        messagebox_mock.askokcancel.return_value = True

        app.delete_listening_profile()

        app.metrics_service.increment.assert_called_once_with("metric_listening_profile_delete_failed")
        messagebox_mock.showerror.assert_called_once()


if __name__ == "__main__":
    unittest.main()
