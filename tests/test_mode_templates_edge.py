from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from core.creative_modes import get_mode_by_label_or_default
from webjam_app_enhanced import CUSTOM_TEMPLATE_OPTION, WebJamEnhancedApp


class _VarStub:
    def __init__(self, value: str = ""):
        self._value = value

    def get(self) -> str:
        return self._value

    def set(self, value: str) -> None:
        self._value = value


class TestModeTemplateFlowEdge(unittest.TestCase):
    def _base_app(self) -> WebJamEnhancedApp:
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.mode_key = "music_jam"
        app.template_var = _VarStub("")
        app.session_goal_var = _VarStub("")
        app.mode_var = _VarStub("Music Jam")
        app.cohort_name = "mixed_discipline"
        app.repository = MagicMock()
        app.metrics_service = MagicMock()
        app.session_canvas = MagicMock()
        app.save_room_context = MagicMock()
        app._refresh_quick_template_menu = MagicMock()
        return app

    def test_on_mode_selected_refreshes_quick_templates(self):
        app = self._base_app()
        app.on_mode_selected("Visual Studio")
        selected = get_mode_by_label_or_default("Visual Studio")

        self.assertEqual(app.mode_key, selected.key)
        self.assertEqual(app.template_var.get(), selected.default_template)
        self.assertEqual(app.session_goal_var.get(), selected.default_goal)
        app._refresh_quick_template_menu.assert_called_once()
        app.repository.append_cohort_event.assert_called_once()

    def test_quick_template_selection_updates_mode_and_menu_when_needed(self):
        app = self._base_app()
        app._on_quick_template_selected("Design Review")

        self.assertEqual(app.mode_key, "design_critique")
        self.assertEqual(app.mode_var.get(), get_mode_by_label_or_default("Design Critique").label)
        app._refresh_quick_template_menu.assert_called_once()
        app.save_room_context.assert_called_once()
        app.session_canvas.refresh.assert_called_once()

    def test_custom_template_option_is_noop(self):
        app = self._base_app()
        app._on_quick_template_selected(CUSTOM_TEMPLATE_OPTION)
        app.save_room_context.assert_not_called()
        app.session_canvas.refresh.assert_not_called()
        app.metrics_service.increment.assert_not_called()


if __name__ == "__main__":
    unittest.main()
