"""Edge tests for mode-layout spec and sash computation — rewritten against ModeController.

_mode_layout_spec and _compute_sash_x were static helpers on WebJamEnhancedApp;
_apply_mode_layout and _schedule_mode_layout_refresh delegated to self.mode_controller.
All four are now on ModeController as get_layout_spec, compute_sash_x, apply_layout,
and schedule_refresh respectively.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from ui.mode_controller import DEFAULT_MODE_LAYOUT, ModeController


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


def _make_ctrl(root=None) -> ModeController:
    """Create a ModeController with a MagicMock app stub."""
    app_stub = MagicMock()
    if root is not None:
        app_stub.root = root
    ctrl = ModeController(app_stub)
    return ctrl


class TestModeLayoutSpecEdge(unittest.TestCase):
    def test_mode_layout_spec_uses_mode_override(self):
        ctrl = _make_ctrl()
        spec = ctrl.get_layout_spec("music_jam")
        self.assertGreater(float(spec["mixer_ratio"]), float(DEFAULT_MODE_LAYOUT["mixer_ratio"]))
        self.assertEqual(int(spec["canvas_width"]), 360)

    def test_mode_layout_spec_falls_back_to_default(self):
        ctrl = _make_ctrl()
        spec = ctrl.get_layout_spec("unknown_mode")
        self.assertEqual(spec, DEFAULT_MODE_LAYOUT)

    def test_compute_sash_x_honors_bounds(self):
        ctrl = _make_ctrl()
        sash = ctrl.compute_sash_x(total_width=1000, mixer_ratio=0.8, min_mixer=900, min_canvas=320)
        self.assertEqual(sash, 900)

    def test_compute_sash_x_clamps_ratio(self):
        ctrl = _make_ctrl()
        sash = ctrl.compute_sash_x(total_width=1600, mixer_ratio=1.5, min_mixer=700, min_canvas=420)
        self.assertEqual(sash, 1180)


class TestApplyModeLayoutEdge(unittest.TestCase):
    def test_apply_mode_layout_updates_canvas_width_and_splitter_sash(self):
        root_stub = _RootSizeStub(width=1600, req_width=1600)
        app_stub = MagicMock()
        app_stub.root = root_stub
        ctrl = ModeController(app_stub)

        ctrl.apply_layout("writers_room")

        spec = ctrl.get_layout_spec("writers_room")
        expected_sash = ctrl.compute_sash_x(
            total_width=1600,
            mixer_ratio=float(spec["mixer_ratio"]),
            min_mixer=int(spec["min_mixer"]),
            min_canvas=int(spec["min_canvas"]),
        )
        app_stub.session_canvas.configure.assert_called_once_with(width=int(spec["canvas_width"]))
        app_stub.main_splitter.sash_place.assert_called_once_with(0, expected_sash, 1)

    def test_apply_mode_layout_uses_reqwidth_when_current_width_unset(self):
        root_stub = _RootSizeStub(width=1, req_width=1400)
        app_stub = MagicMock()
        app_stub.root = root_stub
        ctrl = ModeController(app_stub)

        ctrl.apply_layout("design_critique")

        spec = ctrl.get_layout_spec("design_critique")
        expected_sash = ctrl.compute_sash_x(
            total_width=1400,
            mixer_ratio=float(spec["mixer_ratio"]),
            min_mixer=int(spec["min_mixer"]),
            min_canvas=int(spec["min_canvas"]),
        )
        app_stub.main_splitter.sash_place.assert_called_once_with(0, expected_sash, 1)

    def test_schedule_mode_layout_refresh_without_root(self):
        # Use a root that has no .after() method — schedule_refresh falls back gracefully
        root_stub = _RootSizeStub(width=1600, req_width=1600)
        app_stub = MagicMock()
        app_stub.root = root_stub
        ctrl = ModeController(app_stub)
        apply_mock = MagicMock()
        ctrl.apply_layout = apply_mock

        ctrl.schedule_refresh("writers_room")

        # apply_layout called once synchronously; root.after() raises AttributeError (no such method)
        apply_mock.assert_called_once_with("writers_room")

    def test_schedule_mode_layout_refresh_with_root_after(self):
        root_stub = _RootAfterStub()
        app_stub = MagicMock()
        app_stub.root = root_stub
        ctrl = ModeController(app_stub)
        apply_mock = MagicMock()
        ctrl.apply_layout = apply_mock

        ctrl.schedule_refresh("writers_room")

        apply_mock.assert_called_once_with("writers_room")
        self.assertEqual(len(root_stub.after_calls), 1)
        self.assertEqual(root_stub.after_calls[0][0], 120)


if __name__ == "__main__":
    unittest.main()
