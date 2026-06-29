"""End-to-end-ish integration test: drive the REAL ConductorWindow +
ApplicationController through a full session lifecycle, offscreen.

This goes beyond the unit tests: it builds the actual app objects and pushes
them through connect -> participants join/leave -> fader/mute/solo -> mute-all
-> reset faders -> mix save/load -> reconnect tick -> shutdown, asserting no
exception escapes and that shutdown leaves no timer running.

Runs headless (QT_QPA_PLATFORM=offscreen).
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile  # noqa: E402
import unittest  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.settings import AppSettings  # noqa: E402
from webjam_qt.controllers.application_controller import ApplicationController  # noqa: E402
from webjam_qt.windows.conductor_window import ConductorWindow  # noqa: E402


def _jp(channel_id, name, instrument="Guitar", *, is_local=False,
        fader_level=100, muted=False, solo=False, is_connected=True):
    """Build a fake Jamulus participant matching the attrs the controller reads."""
    return SimpleNamespace(
        channel_id=channel_id, name=name, instrument=instrument,
        is_local=is_local, fader_level=fader_level, muted=muted,
        solo=solo, is_connected=is_connected,
    )


class TestFullSessionFlow(unittest.TestCase):
    def setUp(self):
        # Redirect HOME so mix/session/metrics files never touch the real home.
        self._tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("HOME")
        os.environ["HOME"] = self._tmp.name
        self.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Integration",
        )
        self.controller = ApplicationController(self.window, settings=AppSettings())

    def tearDown(self):
        try:
            self.controller.shutdown()
        finally:
            if self._old_home is not None:
                os.environ["HOME"] = self._old_home
            else:
                os.environ.pop("HOME", None)
            self._tmp.cleanup()

    def test_full_session_lifecycle_no_crash_and_clean_shutdown(self):
        c = self.controller

        # 1. Jamulus comes up.
        c.bridge.jamulus_state = "Running"
        c._refresh_readiness()

        # 2. First real participant arrives (the local user) — flips to "connected".
        c._apply_jamulus_participants([_jp(0, "Me", "Bass", is_local=True)])
        self.assertTrue(c._jamulus_connected)
        self.assertIn(0, c.participants)

        # 3. Bandmates join.
        c._apply_jamulus_participants([
            _jp(0, "Me", "Bass", is_local=True),
            _jp(1, "Alice", "Guitar"),
            _jp(2, "Bob", "Drums"),
        ])
        self.assertEqual(len(c.participants), 3)

        # 4. Mixer interactions through the real grid signal handlers.
        c._on_fader_changed(1, 80)
        c._on_mute_toggled(2, True)
        c._on_solo_toggled(1, True)
        c._on_solo_toggled(1, False)
        self.assertEqual(c.participants[1].fader_level, 80)
        self.assertTrue(c.participants[2].muted)
        self.assertTrue(c._mix_dirty)

        # 5. Bulk actions.  Reset pops a modal confirm dialog — auto-accept it.
        c._on_mute_all()
        with mock.patch.object(
            QMessageBox, "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            c._on_reset_all_faders()
        self.assertTrue(all(p.fader_level == 100 for p in c.participants.values()))

        # 6. Self-mute (local channel present).
        c._on_mute_self()

        # 7. A bandmate leaves.
        c._apply_jamulus_participants([
            _jp(0, "Me", "Bass", is_local=True),
            _jp(1, "Alice", "Guitar"),
        ])
        self.assertEqual(len(c.participants), 2)
        self.assertNotIn(2, c.participants)

        # 8. Persist + restore the mix.
        c._on_save_mix()
        c._on_load_mix()

        # 9. Background ticks fire without a live server.
        c._poll_levels()
        c._on_reconnect_tick()
        c.window.participant_grid.tick_all_meters()

        # 10. Shutdown must stop every timer it owns.
        c.shutdown()
        for name in (
            "_demo_timer", "_level_timer", "_reconnect_timer",
            "_meter_tick_timer", "_token_refresh_timer",
        ):
            timer = getattr(c, name)
            self.assertFalse(timer.isActive(), f"{name} still active after shutdown")

    def test_participant_churn_does_not_leak_grid_cards(self):
        """Repeated join/leave cycles must not accumulate cards in the grid."""
        c = self.controller
        c._apply_jamulus_participants([_jp(0, "Me", is_local=True)])
        for _ in range(25):
            c._apply_jamulus_participants([
                _jp(0, "Me", is_local=True), _jp(1, "A"), _jp(2, "B"), _jp(3, "C"),
            ])
            c._apply_jamulus_participants([_jp(0, "Me", is_local=True)])
        # Back to just the local user — grid should reflect exactly one.
        self.assertEqual(len(c.participants), 1)
        grid = c.window.participant_grid
        n_cards = len(getattr(grid, "_cards", {}))
        self.assertLessEqual(n_cards, 1, f"grid leaked cards: {n_cards}")


if __name__ == "__main__":
    unittest.main()
