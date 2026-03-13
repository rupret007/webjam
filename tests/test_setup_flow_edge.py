from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock
from pathlib import Path
import tempfile

from webjam_app_enhanced import WebJamEnhancedApp


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
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app._load_mix_payload_from_file = MagicMock(return_value={"participants": [{"channel_id": 1}]})
        app._queue_mix_restore = MagicMock()
        with tempfile.TemporaryDirectory() as tmpdir:
            mix_path = Path(tmpdir) / "mix.json"
            mix_path.write_text("{}", encoding="utf-8")
            from unittest.mock import patch

            with patch("webjam_app_enhanced.MIX_FILE", mix_path):
                WebJamEnhancedApp._restore_startup_mix_default(app)

        app._queue_mix_restore.assert_called_once_with({"participants": [{"channel_id": 1}]}, str(mix_path))

    def test_sign_in_restores_signed_in_mix_default(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.auth_controller = MagicMock()
        app.auth_controller.sign_in_interactive.return_value = SimpleNamespace(username="alex")
        app._restore_signed_in_mix_default = MagicMock()

        signed_in = WebJamEnhancedApp.sign_in(app)

        self.assertTrue(signed_in)
        self.assertEqual(app.current_user.username, "alex")
        app._restore_signed_in_mix_default.assert_called_once()

    def test_attempt_pending_mix_restore_clears_pending_on_success(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app._pending_mix_restore_payload = {"participants": [{"channel_id": 2}]}
        app._pending_mix_restore_source = "saved mix"
        app.jamulus_controller = MagicMock()
        app.jamulus_controller.apply_mix_data.return_value = 1
        app._refresh_mixer_controls_from_participants = MagicMock()
        app._refresh_readiness = MagicMock()
        app._set_status_banner = MagicMock()

        WebJamEnhancedApp._attempt_pending_mix_restore(app)

        self.assertIsNone(app._pending_mix_restore_payload)
        self.assertIsNone(app._pending_mix_restore_source)
        app._refresh_mixer_controls_from_participants.assert_called_once()
        app._refresh_readiness.assert_called_once()
        app._set_status_banner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
