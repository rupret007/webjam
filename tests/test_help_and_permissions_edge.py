from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from webjam_app_enhanced import WebJamEnhancedApp


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

        app.show_help()

        help_text = info_mock.call_args.args[1]
        self.assertIn("Signed-in users save to their WebJam profile.", help_text)
        self.assertIn("Anonymous use saves a local default mix on this computer.", help_text)
        self.assertIn("Saved defaults restore automatically", help_text)

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
        app = self._app_stub()
        app.auth_controller.authorize.return_value = False

        app.save_mix()

        app.auth_controller.authorize.assert_called_once_with(
            app.current_user,
            "save_mix",
            require_sign_in=False,
            allow_anonymous=True,
            parent=app.root,
        )

    def test_reset_all_faders_requires_sign_in_and_passes_parent(self):
        app = self._app_stub()
        app.auth_controller.authorize.return_value = False

        app.reset_all_faders()

        app.auth_controller.authorize.assert_called_once_with(
            app.current_user,
            "bulk_reset",
            require_sign_in=False,
            parent=app.root,
        )
        app.repository.add_audit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
