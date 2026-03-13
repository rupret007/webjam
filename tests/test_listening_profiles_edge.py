from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from webjam_app_enhanced import WebJamEnhancedApp


class _MixFileStub:
    def __init__(self, path: str, exists: bool = True):
        self._path = path
        self._exists = exists

    def exists(self) -> bool:
        return self._exists

    def __str__(self) -> str:
        return self._path


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
        app._show_actionable_error = MagicMock()
        app._pending_mix_restore_payload = {"participants": [{"channel_id": 99}]}
        app._pending_mix_restore_source = "queued"
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
    @patch("webjam_app_enhanced.simpledialog.askstring", return_value="Focus Drums")
    def test_load_listening_profile_corrupt_payload_fails(self, _ask, messagebox_mock):
        app = self._app_stub()
        app.repository.list_mix_profiles.side_effect = [
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
        ]
        app.repository.get_mix_profile.return_value = {
            "profile_name": "Focus Drums",
            "mode_key": "music_jam",
            "payload": {"bad": []},
        }
        app.jamulus_controller.apply_mix_data.return_value = None

        app.load_listening_profile()

        app.metrics_service.increment.assert_called_once_with("metric_listening_profile_load_failed")
        messagebox_mock.showerror.assert_called_once()
        messagebox_mock.showinfo.assert_not_called()

    @patch("webjam_app_enhanced.messagebox")
    @patch("webjam_app_enhanced.simpledialog.askstring", return_value="Focus Drums")
    def test_load_listening_profile_no_matches_warns(self, _ask, messagebox_mock):
        app = self._app_stub()
        app.repository.list_mix_profiles.side_effect = [
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
            [{"profile_name": "Focus Drums", "mode_key": "music_jam"}],
        ]
        app.repository.get_mix_profile.return_value = {
            "profile_name": "Focus Drums",
            "mode_key": "music_jam",
            "payload": {"participants": [{"channel_id": 2}]},
        }
        app.jamulus_controller.apply_mix_data.return_value = 0

        app.load_listening_profile()

        app.metrics_service.increment.assert_called_once_with("metric_listening_profile_load_failed")
        messagebox_mock.showwarning.assert_called_once()
        messagebox_mock.showinfo.assert_not_called()

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

    @patch("webjam_app_enhanced.MIX_FILE", new=_MixFileStub("C:/tmp/webjam_mix.json", exists=True))
    @patch("webjam_app_enhanced.messagebox")
    def test_load_mix_invalid_payload_does_not_report_success(self, messagebox_mock):
        app = self._app_stub()
        app._load_mix_payload_from_file = MagicMock(return_value={"bad": []})
        app.jamulus_controller.apply_mix_data.return_value = None

        app.load_mix()

        app.metrics_service.increment.assert_any_call("metric_load_mix_attempt")
        app.metrics_service.increment.assert_any_call("metric_load_mix_failed")
        app._show_actionable_error.assert_called_once()
        messagebox_mock.showinfo.assert_not_called()

    @patch("webjam_app_enhanced.MIX_FILE", new=_MixFileStub("C:/tmp/webjam_mix.json", exists=True))
    @patch("webjam_app_enhanced.messagebox")
    def test_load_mix_no_matches_warns_and_skips_success(self, messagebox_mock):
        app = self._app_stub()
        app._load_mix_payload_from_file = MagicMock(return_value={"participants": [{"channel_id": 2}]})
        app.jamulus_controller.apply_mix_data.return_value = 0

        app.load_mix()

        app.metrics_service.increment.assert_any_call("metric_load_mix_attempt")
        app.metrics_service.increment.assert_any_call("metric_load_mix_failed")
        messagebox_mock.showwarning.assert_called_once()
        messagebox_mock.showinfo.assert_not_called()
        app._refresh_mixer_controls_from_participants.assert_not_called()

    @patch("webjam_app_enhanced.messagebox")
    def test_save_mix_signed_in_uses_repository_profile_default(self, messagebox_mock):
        app = self._app_stub()
        app.current_user = SimpleNamespace(username="Casey")
        app.jamulus_controller.serialize_mix.return_value = {"participants": [{"channel_id": 1, "name": "Casey"}]}

        app.save_mix()

        app.repository.save_user_mix_default.assert_called_once_with(
            "Casey",
            {"participants": [{"channel_id": 1, "name": "Casey"}]},
        )
        app.jamulus_controller.save_mix.assert_not_called()
        self.assertIsNone(app._pending_mix_restore_payload)
        self.assertIsNone(app._pending_mix_restore_source)
        messagebox_mock.showinfo.assert_called_once()
        self.assertIn("WebJam profile", messagebox_mock.showinfo.call_args.args[1])

    @patch("webjam_app_enhanced.MIX_FILE", new=_MixFileStub("C:/tmp/webjam_mix.json", exists=True))
    @patch("webjam_app_enhanced.messagebox")
    def test_save_mix_anonymous_writes_local_default_file(self, messagebox_mock):
        app = self._app_stub()

        app.save_mix()

        app.jamulus_controller.save_mix.assert_called_once_with("C:/tmp/webjam_mix.json")
        app.repository.save_user_mix_default.assert_not_called()
        self.assertIsNone(app._pending_mix_restore_payload)
        self.assertIsNone(app._pending_mix_restore_source)
        self.assertIn("restores automatically", messagebox_mock.showinfo.call_args.args[1])

    @patch("webjam_app_enhanced.messagebox")
    def test_load_mix_signed_in_prefers_repository_default(self, messagebox_mock):
        app = self._app_stub()
        app.current_user = SimpleNamespace(username="Casey")
        app.repository.get_user_mix_default.return_value = {
            "username": "casey",
            "payload": {"participants": [{"channel_id": 4, "name": "Casey"}]},
        }
        app.jamulus_controller.apply_mix_data.return_value = 1
        app._load_mix_payload_from_file = MagicMock()

        app.load_mix()

        app.jamulus_controller.apply_mix_data.assert_called_once_with(
            {"participants": [{"channel_id": 4, "name": "Casey"}]}
        )
        app._load_mix_payload_from_file.assert_not_called()
        app._refresh_mixer_controls_from_participants.assert_called_once()
        app._refresh_readiness.assert_called_once()
        self.assertIsNone(app._pending_mix_restore_payload)
        self.assertIsNone(app._pending_mix_restore_source)
        self.assertIn("Casey profile", messagebox_mock.showinfo.call_args.args[1])

    @patch("webjam_app_enhanced.messagebox")
    def test_load_mix_falls_back_to_file_when_user_default_missing(self, messagebox_mock):
        app = self._app_stub()
        app.current_user = SimpleNamespace(username="Casey")
        app.repository.get_user_mix_default.return_value = None
        app.jamulus_controller.apply_mix_data.return_value = 1
        with tempfile.TemporaryDirectory() as tmpdir:
            mix_path = Path(tmpdir) / "webjam_mix.json"
            mix_path.write_text(json.dumps({"participants": [{"channel_id": 8, "name": "Alex"}]}), encoding="utf-8")
            with patch("webjam_app_enhanced.MIX_FILE", mix_path):
                app.load_mix()

        app.jamulus_controller.apply_mix_data.assert_called_once_with(
            {"participants": [{"channel_id": 8, "name": "Alex"}]}
        )
        self.assertIn(str(mix_path), messagebox_mock.showinfo.call_args.args[1])

    @patch("webjam_app_enhanced.MIX_FILE", new=_MixFileStub("C:/tmp/webjam_mix.json", exists=True))
    @patch("webjam_app_enhanced.messagebox")
    def test_load_mix_invalid_file_still_reports_failure(self, messagebox_mock):
        app = self._app_stub()
        app._load_mix_payload_from_file = MagicMock(return_value=None)

        app.load_mix()

        app.metrics_service.increment.assert_any_call("metric_load_mix_failed")
        app._show_actionable_error.assert_called_once()
        messagebox_mock.showwarning.assert_not_called()
        messagebox_mock.showinfo.assert_not_called()


if __name__ == "__main__":
    unittest.main()
