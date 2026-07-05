from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from webjam_app_enhanced import WebJamEnhancedApp
from ui.mixer_service import MixerService


def _make_mixer_service() -> MixerService:
    """Build a MixerService stub with all external dependencies mocked."""
    svc = MixerService(
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
    return svc


class TestHelpAndPermissionsEdge(unittest.TestCase):
    def _app_stub(self) -> WebJamEnhancedApp:
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = object()
        app.current_user = None
        app.auth_controller = MagicMock()
        app.metrics_service = MagicMock()
        app.jamulus_controller = MagicMock()
        app.repository = MagicMock()
        app.mixer_channels = {}
        return app

    @patch("webjam_app_enhanced.messagebox.showinfo")
    def test_show_help_describes_signed_in_and_local_mix_restore(self, info_mock):
        app = self._app_stub()
        app.repository.get_bootstrap_admin_credentials_path.return_value = "C:/tmp/webjam_admin_bootstrap.txt"

        app.show_help()

        help_text = info_mock.call_args.args[1]
        self.assertIn("Signed-in users save to their WebJam profile.", help_text)
        self.assertIn("Anonymous use saves a local default mix on this computer.", help_text)
        self.assertIn("Saved defaults restore automatically", help_text)
        self.assertIn("Bootstrap credentials are stored at", help_text)
        self.assertIn("C:/tmp/webjam_admin_bootstrap.txt", help_text)

    def test_show_audio_diagnostics_passes_parent_when_authorizing(self):
        app = self._app_stub()
        app.auth_controller.authorize.return_value = False

        app.show_audio_diagnostics()

        app.auth_controller.authorize.assert_called_once_with(
            app.current_user,
            "view_diagnostics",
            require_sign_in=False,
            parent=app.root,
        )
        app.metrics_service.increment.assert_not_called()

    def test_save_mix_allows_anonymous_and_passes_parent(self):
        """MixerService.save_mix() calls authorize with allow_anonymous=True."""
        svc = _make_mixer_service()
        svc.auth_controller.authorize.return_value = False

        svc.save_mix()

        svc.auth_controller.authorize.assert_called_once_with(
            None,  # get_current_user() returns None
            "save_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=svc.app_root,
        )

    def test_reset_all_faders_requires_bulk_reset_permission(self):
        """MixerService.reset_all_faders() calls authorize with 'bulk_reset'."""
        svc = _make_mixer_service()
        svc.auth_controller.authorize.return_value = False

        svc.reset_all_faders()

        svc.auth_controller.authorize.assert_called_once_with(
            None,  # get_current_user() returns None
            "bulk_reset",
            require_sign_in=False,
            parent=svc.app_root,
        )
        svc.repository.add_audit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
