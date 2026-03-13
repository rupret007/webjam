from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from webjam_app_enhanced import DEFAULT_MODE_LAYOUT, WebJamEnhancedApp


class _RootSizeStub:
    def __init__(self, width: int, req_width: int):
        self._width = width
        self._req_width = req_width

    def winfo_width(self) -> int:
        return self._width

    def winfo_reqwidth(self) -> int:
        return self._req_width


class _RootAfterStub(_RootSizeStub):
    def __init__(self, width: int = 1600, req_width: int = 1600):
        super().__init__(width=width, req_width=req_width)
        self.after_calls: list[tuple[int, object]] = []

    def after(self, delay_ms: int, callback):
        self.after_calls.append((delay_ms, callback))


class TestModeLayoutSpecEdge(unittest.TestCase):
    def test_mode_layout_spec_uses_mode_override(self):
        spec = WebJamEnhancedApp._mode_layout_spec("music_jam")
        self.assertGreater(float(spec["mixer_ratio"]), float(DEFAULT_MODE_LAYOUT["mixer_ratio"]))
        self.assertEqual(int(spec["canvas_width"]), 360)

    def test_mode_layout_spec_falls_back_to_default(self):
        spec = WebJamEnhancedApp._mode_layout_spec("unknown_mode")
        self.assertEqual(spec, DEFAULT_MODE_LAYOUT)

    def test_compute_sash_x_honors_bounds(self):
        sash = WebJamEnhancedApp._compute_sash_x(total_width=1000, mixer_ratio=0.8, min_mixer=900, min_canvas=320)
        self.assertEqual(sash, 900)

    def test_compute_sash_x_clamps_ratio(self):
        sash = WebJamEnhancedApp._compute_sash_x(total_width=1600, mixer_ratio=1.5, min_mixer=700, min_canvas=420)
        self.assertEqual(sash, 1180)


class TestApplyModeLayoutEdge(unittest.TestCase):
    def test_apply_mode_layout_updates_canvas_width_and_splitter_sash(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.mode_key = "writers_room"
        app.root = _RootSizeStub(width=1600, req_width=1600)
        app.session_canvas = MagicMock()
        app.main_splitter = MagicMock()
        app.left_content = object()
        app.right_content = object()

        WebJamEnhancedApp._apply_mode_layout(app)

        spec = WebJamEnhancedApp._mode_layout_spec("writers_room")
        expected_sash = WebJamEnhancedApp._compute_sash_x(
            total_width=1600,
            mixer_ratio=float(spec["mixer_ratio"]),
            min_mixer=int(spec["min_mixer"]),
            min_canvas=int(spec["min_canvas"]),
        )
        app.session_canvas.configure.assert_called_once_with(width=int(spec["canvas_width"]))
        app.main_splitter.sash_place.assert_called_once_with(0, expected_sash, 1)

    def test_apply_mode_layout_uses_reqwidth_when_current_width_unset(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app.mode_key = "design_critique"
        app.root = _RootSizeStub(width=1, req_width=1400)
        app.session_canvas = MagicMock()
        app.main_splitter = MagicMock()
        app.left_content = object()
        app.right_content = object()

        WebJamEnhancedApp._apply_mode_layout(app)

        spec = WebJamEnhancedApp._mode_layout_spec("design_critique")
        expected_sash = WebJamEnhancedApp._compute_sash_x(
            total_width=1400,
            mixer_ratio=float(spec["mixer_ratio"]),
            min_mixer=int(spec["min_mixer"]),
            min_canvas=int(spec["min_canvas"]),
        )
        app.main_splitter.sash_place.assert_called_once_with(0, expected_sash, 1)

    def test_schedule_mode_layout_refresh_without_root(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app._apply_mode_layout = MagicMock()
        WebJamEnhancedApp._schedule_mode_layout_refresh(app)
        app._apply_mode_layout.assert_called_once()

    def test_schedule_mode_layout_refresh_with_root_after(self):
        app = WebJamEnhancedApp.__new__(WebJamEnhancedApp)
        app._apply_mode_layout = MagicMock()
        app.root = _RootAfterStub()
        WebJamEnhancedApp._schedule_mode_layout_refresh(app)
        app._apply_mode_layout.assert_called_once()
        self.assertEqual(len(app.root.after_calls), 1)
        self.assertEqual(app.root.after_calls[0][0], 120)


if __name__ == "__main__":
    unittest.main()
