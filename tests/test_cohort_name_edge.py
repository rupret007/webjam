from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from webjam_app_enhanced import WebJamEnhancedApp


class TestCohortNameEdge(unittest.TestCase):
    def _base_app(self) -> WebJamEnhancedApp:
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.root = object()
        app.cohort_name = "mixed_discipline"
        app.repository = MagicMock()
        app.metrics_service = MagicMock()
        return app

    @patch("webjam_app_enhanced.simpledialog.askstring", return_value="   ")
    @patch("webjam_app_enhanced.messagebox.showwarning")
    def test_set_cohort_name_rejects_whitespace_only_input(self, warn_mock, _ask_mock):
        app = self._base_app()

        WebJamEnhancedApp.set_cohort_name(app)

        self.assertEqual(app.cohort_name, "mixed_discipline")
        app.repository.set_setting.assert_not_called()
        app.metrics_service.increment.assert_not_called()
        warn_mock.assert_called_once()

    @patch("webjam_app_enhanced.simpledialog.askstring", return_value=" Visual Artists ")
    @patch("webjam_app_enhanced.messagebox.showinfo")
    def test_set_cohort_name_normalizes_and_saves_valid_input(self, info_mock, _ask_mock):
        app = self._base_app()

        WebJamEnhancedApp.set_cohort_name(app)

        self.assertEqual(app.cohort_name, "visual_artists")
        app.repository.set_setting.assert_called_once_with("cohort_name", "visual_artists")
        app.metrics_service.increment.assert_called_once_with("metric_cohort_tagged_visual_artists")
        info_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
