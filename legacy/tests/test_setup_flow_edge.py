from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile

from webjam_app_enhanced import WebJamEnhancedApp
from ui.mixer_service import MixerService


def _make_mixer_service() -> MixerService:
    """Build a MixerService stub with all external dependencies mocked."""
    return MixerService(
        app_root=MagicMock(),
        repository=MagicMock(),
        auth_controller=MagicMock(authorize=MagicMock(return_value=True)),
        jamulus_controller=MagicMock(),
        metrics_service=MagicMock(),
        get_current_user=lambda: None,
        get_mode_key=lambda: "music_jam",
        refresh_readiness=MagicMock(),
        set_status_banner=MagicMock(),
        mixer_channels={},
        app_root_exists_check=lambda: True,
    )


class TestSetupFlowEdge(unittest.TestCase):
    def _base_app(self, setup_seen: object, auto_setup_enabled: bool = True) -> WebJamEnhancedApp:
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.auto_setup_enabled = auto_setup_enabled
        app.repository = MagicMock()
        app.repository.get_setting.return_value = setup_seen
        app.show_setup_wizard = MagicMock()
        return app

    def test_show_setup_once_skips_when_setup_seen_is_integer(self):
        app = self._base_app(setup_seen=1)

        WebJamEnhancedApp._show_setup_once(app)

        app.show_setup_wizard.assert_not_called()

    def test_show_setup_once_skips_when_setup_seen_is_boolean(self):
        app = self._base_app(setup_seen=True)

        WebJamEnhancedApp._show_setup_once(app)

        app.show_setup_wizard.assert_not_called()

    def test_show_setup_once_runs_when_setup_not_seen(self):
        app = self._base_app(setup_seen="0")

        WebJamEnhancedApp._show_setup_once(app)

        app.show_setup_wizard.assert_called_once_with(mark_complete=True)

    def test_restore_startup_mix_default_queues_existing_file_payload(self):
        """_restore_startup_mix_default now lives on MixerService."""
        svc = _make_mixer_service()
        svc._load_mix_payload_from_file = MagicMock(return_value={"participants": [{"channel_id": 1}]})
        svc._queue_mix_restore = MagicMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            mix_path = Path(tmpdir) / "mix.json"
            mix_path.write_text("{}", encoding="utf-8")
            svc._mix_file = mix_path

            svc._restore_startup_mix_default()

        svc._queue_mix_restore.assert_called_once_with(
            {"participants": [{"channel_id": 1}]}, str(mix_path)
        )

    def test_sign_in_restores_signed_in_mix_default(self):
        """sign_in() now delegates mix restore to mixer_service."""
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = object()
        app.auth_controller = MagicMock()
        app.auth_controller.sign_in_interactive.return_value = SimpleNamespace(username="alex")
        app.mixer_service = MagicMock()

        signed_in = WebJamEnhancedApp.sign_in(app)

        self.assertTrue(signed_in)
        self.assertEqual(app.current_user.username, "alex")
        app.auth_controller.sign_in_interactive.assert_called_once_with(parent=app.root)
        app.mixer_service._restore_signed_in_mix_default.assert_called_once()

    @patch("webjam_app_enhanced.SetupWizard")
    def test_show_setup_wizard_refocuses_existing_window(self, wizard_cls):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        existing = MagicMock()
        existing.winfo_exists.return_value = True
        app._setup_wizard_window = existing
        app.metrics_service = MagicMock()

        WebJamEnhancedApp.show_setup_wizard(app, mark_complete=False)

        wizard_cls.assert_not_called()
        app.metrics_service.increment.assert_not_called()
        existing.deiconify.assert_called_once_with()
        existing.lift.assert_called_once_with()
        existing.focus_force.assert_called_once_with()
        existing.grab_set.assert_called_once_with()

    @patch("webjam_app_enhanced.SetupWizard")
    def test_show_setup_wizard_tracks_new_window_and_clears_destroyed_reference(self, wizard_cls):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app._setup_wizard_window = None
        app.metrics_service = MagicMock()
        app.mode_key = "music_jam"
        app.root = object()
        app.repository = MagicMock()
        app._settings_for_checks = MagicMock(return_value=object())
        app.find_jamulus = MagicMock(return_value="Jamulus.exe")
        app.jamulus_controller = MagicMock()
        window = MagicMock()
        window.winfo_exists.return_value = True
        wizard = MagicMock()
        wizard.show.return_value = window
        wizard_cls.return_value = wizard

        WebJamEnhancedApp.show_setup_wizard(app, mark_complete=False)

        self.assertIs(app._setup_wizard_window, window)
        bind_args = window.bind.call_args
        self.assertEqual(bind_args.args[0], "<Destroy>")
        bind_args.args[1]()
        self.assertIsNone(app._setup_wizard_window)

    def test_attempt_pending_mix_restore_clears_pending_on_success(self):
        """_attempt_pending_mix_restore now lives on MixerService."""
        svc = _make_mixer_service()
        svc._pending_mix_restore_payload = {"participants": [{"channel_id": 2}]}
        svc._pending_mix_restore_source = "saved mix"
        svc.jamulus_controller.apply_mix_data.return_value = 1

        svc._attempt_pending_mix_restore()

        self.assertIsNone(svc._pending_mix_restore_payload)
        self.assertIsNone(svc._pending_mix_restore_source)
        svc.refresh_readiness.assert_called_once()
        svc.set_status_banner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
