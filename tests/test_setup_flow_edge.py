from __future__ import annotations

import unittest
from unittest.mock import MagicMock

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


if __name__ == "__main__":
    unittest.main()
