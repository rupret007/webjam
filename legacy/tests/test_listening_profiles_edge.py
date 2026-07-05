"""Edge tests for listening-profile and mix load/save — rewritten against MixerService.

All methods tested here (_resolve_profile_name, save_mix, load_mix,
save_listening_profile, load_listening_profile, delete_listening_profile) were
refactored out of WebJamEnhancedApp into ui.mixer_service.MixerService.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ui.mixer_service import MixerService


def _make_service(current_user=None, mix_file_path: str = "~/.webjam_mix.json") -> MixerService:
    """Build a MixerService stub with all external dependencies mocked."""
    svc = MixerService(
        app_root=MagicMock(),
        repository=MagicMock(),
        auth_controller=MagicMock(authorize=MagicMock(return_value=True)),
        jamulus_controller=MagicMock(),
        metrics_service=MagicMock(),
        get_current_user=lambda: current_user,
        get_mode_key=lambda: "music_jam",
        refresh_readiness=MagicMock(),
        set_status_banner=MagicMock(),
        mixer_channels={},
        app_root_exists_check=lambda: True,
    )
    # Override the mix file path to a predictable location
    svc._mix_file = Path(mix_file_path)
    return svc


# Patch target for all messagebox / simpledialog calls inside mixer_service
_MB = "ui.mixer_service.tk_messagebox"
_SD = "ui.mixer_service.tk.simpledialog"


class TestListeningProfilesEdge(unittest.TestCase):

    # ------------------------------------------------------------------
    # _resolve_profile_name (static)
    # ------------------------------------------------------------------
    def test_resolve_profile_name_is_case_insensitive(self):
        profiles = [{"profile_name": "Focus Drums", "mode_key": "music_jam"}]
        self.assertEqual(
            MixerService._resolve_profile_name("focus drums", profiles),
            "Focus Drums",
        )

    # ------------------------------------------------------------------
    # save_listening_profile
    # ------------------------------------------------------------------
    @patch(_MB)
    @patch(f"{_SD}.askstring", return_value="Room Focus")
    def test_save_listening_profile_success(self, _ask, messagebox_mock):
        svc = _make_service()
        svc.jamulus_controller.get_participants.return_value = [SimpleNamespace(name="Alex")]
        svc.jamulus_controller.serialize_mix.return_value = {"participants": [{"channel_id": 0}]}
        svc.repository.get_mix_profile.return_value = None

        svc.save_listening_profile()

        svc.repository.save_mix_profile.assert_called_once_with(
            "Room Focus",
            "music_jam",
            {"participants": [{"channel_id": 0}]},
        )
        svc.metrics_service.increment.assert_any_call("metric_listening_profile_save_success")
        messagebox_mock.showinfo.assert_called_once()

    @patch(_MB)
    def test_save_listening_profile_warns_without_participants(self, messagebox_mock):
        svc = _make_service()
        svc.jamulus_controller.get_participants.return_value = []

        svc.save_listening_profile()

        svc.repository.save_mix_profile.assert_not_called()
        messagebox_mock.showwarning.assert_called_once()

    # ------------------------------------------------------------------
    # save_mix
    # ------------------------------------------------------------------
    @patch(_MB)
    def test_save_mix_warns_without_participants(self, messagebox_mock):
        svc = _make_service()
        svc.jamulus_controller.get_participants.return_value = []

        svc.save_mix()

        svc.repository.save_user_mix_default.assert_not_called()
        svc.jamulus_controller.save_mix.assert_not_called()
        messagebox_mock.showwarning.assert_called_once()
        messagebox_mock.showinfo.assert_not_called()

    @patch(_MB)
    def test_save_mix_signed_in_uses_repository_profile_default(self, messagebox_mock):
        user = SimpleNamespace(username="Casey")
        svc = _make_service(current_user=user)
        svc.jamulus_controller.serialize_mix.return_value = {
            "participants": [{"channel_id": 1, "name": "Casey"}]
        }

        svc.save_mix()

        svc.repository.save_user_mix_default.assert_called_once_with(
            "Casey",
            {"participants": [{"channel_id": 1, "name": "Casey"}]},
        )
        svc.jamulus_controller.save_mix.assert_not_called()
        self.assertIsNone(svc._pending_mix_restore_payload)
        self.assertIsNone(svc._pending_mix_restore_source)
        messagebox_mock.showinfo.assert_called_once()
        self.assertIn("WebJam profile", messagebox_mock.showinfo.call_args.args[1])

    @patch(_MB)
    def test_save_mix_anonymous_writes_local_default_file(self, messagebox_mock):
        svc = _make_service(mix_file_path="/tmp/webjam_mix.json")

        svc.save_mix()

        svc.jamulus_controller.save_mix.assert_called_once_with("/tmp/webjam_mix.json")
        svc.repository.save_user_mix_default.assert_not_called()
        self.assertIsNone(svc._pending_mix_restore_payload)
        self.assertIsNone(svc._pending_mix_restore_source)
        self.assertIn("restores automatically", messagebox_mock.showinfo.call_args.args[1])

    # ------------------------------------------------------------------
    # load_listening_profile
    # ------------------------------------------------------------------
    @patch(_MB)
    def test_load_listening_profile_shows_info_when_empty(self, messagebox_mock):
        svc = _make_service()
        svc.repository.list_mix_profiles.return_value = []

        svc.load_listening_profile()

        messagebox_mock.showinfo.assert_called_once()
        svc.jamulus_controller.apply_mix_data.assert_not_called()

    @patch(_MB)
    @patch(f"{_SD}.askstring", return_value="focus drums")
    def test_load_listening_profile_success(self, _ask, messagebox_mock):
        svc = _make_service()
        svc.repository.list_mix_profiles.return_value = [
            {"profile_name": "Focus Drums", "mode_key": "music_jam"}
        ]
        svc.repository.get_mix_profile.return_value = {
            "profile_name": "Focus Drums",
            "mode_key": "music_jam",
            "payload": {"participants": [{"channel_id": 0, "fader_level": 80}]},
        }
        svc.jamulus_controller.apply_mix_data.return_value = 1

        svc.load_listening_profile()

        svc.jamulus_controller.apply_mix_data.assert_called_once_with(
            {"participants": [{"channel_id": 0, "fader_level": 80}]}
        )
        svc.refresh_readiness.assert_called_once()
        svc.metrics_service.increment.assert_any_call("metric_listening_profile_load_success")
        messagebox_mock.showinfo.assert_called_once()

    @patch(_MB)
    @patch(f"{_SD}.askstring", return_value="Ghost")
    def test_load_listening_profile_warns_on_unknown_name(self, _ask, messagebox_mock):
        svc = _make_service()
        svc.repository.list_mix_profiles.return_value = [
            {"profile_name": "Focus Drums", "mode_key": "music_jam"}
        ]

        svc.load_listening_profile()

        messagebox_mock.showwarning.assert_called_once()
        svc.jamulus_controller.apply_mix_data.assert_not_called()

    @patch(_MB)
    @patch(f"{_SD}.askstring", return_value="Focus Drums")
    def test_load_listening_profile_corrupt_payload_fails(self, _ask, messagebox_mock):
        svc = _make_service()
        svc.repository.list_mix_profiles.return_value = [
            {"profile_name": "Focus Drums", "mode_key": "music_jam"}
        ]
        svc.repository.get_mix_profile.return_value = {
            "profile_name": "Focus Drums",
            "mode_key": "music_jam",
            "payload": {"bad": []},
        }
        svc.jamulus_controller.apply_mix_data.return_value = None

        svc.load_listening_profile()

        svc.metrics_service.increment.assert_any_call("metric_listening_profile_load_failed")
        messagebox_mock.showerror.assert_called_once()
        messagebox_mock.showinfo.assert_not_called()

    @patch(_MB)
    @patch(f"{_SD}.askstring", return_value="Focus Drums")
    def test_load_listening_profile_no_matches_warns(self, _ask, messagebox_mock):
        svc = _make_service()
        svc.repository.list_mix_profiles.return_value = [
            {"profile_name": "Focus Drums", "mode_key": "music_jam"}
        ]
        svc.repository.get_mix_profile.return_value = {
            "profile_name": "Focus Drums",
            "mode_key": "music_jam",
            "payload": {"participants": [{"channel_id": 2}]},
        }
        svc.jamulus_controller.apply_mix_data.return_value = 0

        svc.load_listening_profile()

        svc.metrics_service.increment.assert_any_call("metric_listening_profile_load_failed")
        messagebox_mock.showwarning.assert_called_once()
        messagebox_mock.showinfo.assert_not_called()

    # ------------------------------------------------------------------
    # delete_listening_profile
    # ------------------------------------------------------------------
    @patch(_MB)
    @patch(f"{_SD}.askstring", return_value="focus drums")
    def test_delete_listening_profile_success(self, _ask, messagebox_mock):
        svc = _make_service()
        svc.repository.list_mix_profiles.return_value = [
            {"profile_name": "Focus Drums", "mode_key": "music_jam"}
        ]
        svc.repository.delete_mix_profile.return_value = True
        messagebox_mock.askokcancel.return_value = True

        svc.delete_listening_profile()

        svc.repository.delete_mix_profile.assert_called_once_with("Focus Drums")
        svc.metrics_service.increment.assert_any_call("metric_listening_profile_delete_success")
        messagebox_mock.showinfo.assert_called_once()

    @patch(_MB)
    @patch(f"{_SD}.askstring", return_value="Focus Drums")
    def test_delete_listening_profile_failed_delete_increments_failure_metric(
        self, _ask, messagebox_mock
    ):
        svc = _make_service()
        svc.repository.list_mix_profiles.return_value = [
            {"profile_name": "Focus Drums", "mode_key": "music_jam"}
        ]
        svc.repository.delete_mix_profile.return_value = False
        messagebox_mock.askokcancel.return_value = True

        svc.delete_listening_profile()

        svc.metrics_service.increment.assert_any_call("metric_listening_profile_delete_failed")
        messagebox_mock.showerror.assert_called_once()

    # ------------------------------------------------------------------
    # load_mix — anonymous (file-based) path
    # ------------------------------------------------------------------
    @patch(_MB)
    def test_load_mix_invalid_payload_does_not_report_success(self, messagebox_mock):
        svc = _make_service()
        svc._mix_file = MagicMock()
        svc._mix_file.exists.return_value = True
        svc._load_mix_payload_from_file = MagicMock(return_value={"bad": []})
        svc.jamulus_controller.apply_mix_data.return_value = None

        svc.load_mix()

        svc.metrics_service.increment.assert_any_call("metric_load_mix_attempt")
        svc.metrics_service.increment.assert_any_call("metric_load_mix_failed")
        messagebox_mock.showerror.assert_called()
        messagebox_mock.showinfo.assert_not_called()

    @patch(_MB)
    def test_load_mix_no_matches_warns_and_skips_success(self, messagebox_mock):
        svc = _make_service()
        svc._mix_file = MagicMock()
        svc._mix_file.exists.return_value = True
        svc._load_mix_payload_from_file = MagicMock(
            return_value={"participants": [{"channel_id": 2}]}
        )
        svc.jamulus_controller.apply_mix_data.return_value = 0

        svc.load_mix()

        svc.metrics_service.increment.assert_any_call("metric_load_mix_attempt")
        svc.metrics_service.increment.assert_any_call("metric_load_mix_failed")
        messagebox_mock.showwarning.assert_called_once()
        messagebox_mock.showinfo.assert_not_called()

    @patch(_MB)
    def test_load_mix_invalid_file_still_reports_failure(self, messagebox_mock):
        svc = _make_service()
        svc._mix_file = MagicMock()
        svc._mix_file.exists.return_value = True
        svc._load_mix_payload_from_file = MagicMock(return_value=None)

        svc.load_mix()

        svc.metrics_service.increment.assert_any_call("metric_load_mix_failed")
        messagebox_mock.showerror.assert_called()
        messagebox_mock.showwarning.assert_not_called()
        messagebox_mock.showinfo.assert_not_called()

    # ------------------------------------------------------------------
    # load_mix — signed-in path
    # ------------------------------------------------------------------
    @patch(_MB)
    def test_save_mix_allows_anonymous_and_passes_parent(self, messagebox_mock):
        svc = _make_service()
        svc.auth_controller.authorize.return_value = False

        svc.save_mix()

        svc.auth_controller.authorize.assert_called_once_with(
            None,
            "save_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=svc.app_root,
        )

    @patch(_MB)
    def test_load_mix_signed_in_prefers_repository_default(self, messagebox_mock):
        user = SimpleNamespace(username="Casey")
        svc = _make_service(current_user=user)
        svc.repository.get_user_mix_default.return_value = {
            "username": "casey",
            "payload": {"participants": [{"channel_id": 4, "name": "Casey"}]},
        }
        svc.jamulus_controller.apply_mix_data.return_value = 1
        svc._load_mix_payload_from_file = MagicMock()

        svc.load_mix()

        svc.jamulus_controller.apply_mix_data.assert_called_once_with(
            {"participants": [{"channel_id": 4, "name": "Casey"}]}
        )
        svc._load_mix_payload_from_file.assert_not_called()
        svc.refresh_readiness.assert_called_once()
        self.assertIsNone(svc._pending_mix_restore_payload)
        self.assertIsNone(svc._pending_mix_restore_source)
        self.assertIn("Casey profile", messagebox_mock.showinfo.call_args.args[1])

    @patch(_MB)
    def test_load_mix_falls_back_to_file_when_user_default_missing(self, messagebox_mock):
        user = SimpleNamespace(username="Casey")
        svc = _make_service(current_user=user)
        svc.repository.get_user_mix_default.return_value = None
        svc.jamulus_controller.apply_mix_data.return_value = 1
        with tempfile.TemporaryDirectory() as tmpdir:
            mix_path = Path(tmpdir) / "webjam_mix.json"
            mix_path.write_text(
                json.dumps({"participants": [{"channel_id": 8, "name": "Alex"}]}),
                encoding="utf-8",
            )
            svc._mix_file = mix_path

            svc.load_mix()

        svc.jamulus_controller.apply_mix_data.assert_called_once_with(
            {"participants": [{"channel_id": 8, "name": "Alex"}]}
        )
        self.assertIn(str(mix_path), messagebox_mock.showinfo.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
