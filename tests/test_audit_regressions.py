"""Regression tests closing blind spots found in the v0.4.7 pre-ship audit.

Each test here guards a behavior that was correct in code but had no test —
so a future change couldn't silently regress it.  Covers:

* WCAG AA contrast of the text tokens on their real backgrounds
* the fader's keyboard-step + accessible-name attributes
* Talk Break muting only the Jamulus send, never the Webex microphone
* the macOS literal-Control shortcut bindings (Qt.MetaModifier branch)
* the Webex placeholder auto-restore on a real load failure

Runs headless (QT_QPA_PLATFORM=offscreen).
"""
from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])


# ----------------------------------------------------------------------
# Accessibility — contrast
# ----------------------------------------------------------------------
def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    srgb = [int(hex_color[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]

    def _lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (_lin(c) for c in srgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg: str, bg: str) -> float:
    lf, lb = _relative_luminance(fg), _relative_luminance(bg)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


class TestTextContrastTokens(unittest.TestCase):
    """Body text must clear WCAG AA (4.5:1) on the surfaces it renders on."""

    AA = 4.5

    def setUp(self):
        from webjam_qt.theme.tokens import Color
        self.c = Color

    def test_text_muted_passes_AA_on_card(self):
        ratio = _contrast(self.c.TEXT_MUTED, self.c.BG_CARD)
        self.assertGreaterEqual(ratio, self.AA, f"TEXT_MUTED on BG_CARD = {ratio:.2f}:1")

    def test_text_muted_passes_AA_on_input(self):
        ratio = _contrast(self.c.TEXT_MUTED, self.c.BG_INPUT)
        self.assertGreaterEqual(ratio, self.AA, f"TEXT_MUTED on BG_INPUT = {ratio:.2f}:1")

    def test_text_secondary_passes_AA_on_card(self):
        ratio = _contrast(self.c.TEXT_SECONDARY, self.c.BG_CARD)
        self.assertGreaterEqual(ratio, self.AA, f"TEXT_SECONDARY on BG_CARD = {ratio:.2f}:1")

    def test_text_primary_passes_AA_on_panel(self):
        ratio = _contrast(self.c.TEXT_PRIMARY, self.c.BG_PANEL)
        self.assertGreaterEqual(ratio, self.AA, f"TEXT_PRIMARY on BG_PANEL = {ratio:.2f}:1")


# ----------------------------------------------------------------------
# Accessibility — fader keyboard step + accessible name
# ----------------------------------------------------------------------
class TestFaderAccessibility(unittest.TestCase):
    def setUp(self):
        from webjam_qt.widgets.participant_card import ParticipantPresentation
        self.pres = ParticipantPresentation(channel_id=0, name="Alice", role="Guitar")

    def test_fader_keyboard_steps(self):
        from webjam_qt.widgets.participant_card import ParticipantCard
        card = ParticipantCard(self.pres)
        self.assertEqual(card._fader.singleStep(), 5)
        self.assertEqual(card._fader.pageStep(), 15)

    def test_fader_accessible_name_includes_participant(self):
        from webjam_qt.widgets.participant_card import ParticipantCard
        card = ParticipantCard(self.pres)
        self.assertEqual(
            card._fader.accessibleName(), "Volume fader for Alice (decibels)"
        )


# ----------------------------------------------------------------------
# Talk Break is Jamulus-only
# ----------------------------------------------------------------------
class TestTalkBreakIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.settings import AppSettings
        from webjam_qt.controllers.application_controller import ApplicationController
        from webjam_qt.windows.conductor_window import ConductorWindow
        cls.window = ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Test",
        )
        cls.controller = ApplicationController(cls.window, settings=AppSettings())

    @classmethod
    def tearDownClass(cls):
        cls.controller.shutdown()

    def _install_local_participant(self):
        c = self.controller
        local = mock.MagicMock()
        local.is_local = True
        local.muted = False
        c.participants.clear()
        c.participants[0] = local
        c._jamulus_connected = True
        c._self_transmit_muted = False
        c._talk_break_intended = False
        c.jamulus = mock.MagicMock()              # don't hit real RPC
        c._push_participants_to_grid = lambda: None
        return local

    def test_talk_break_never_controls_webex(self):
        local = self._install_local_participant()
        self.controller.bridge.webex_state = "Opened externally"
        self.controller._on_mute_self()
        self.assertFalse(local.muted)
        self.assertTrue(self.controller._self_transmit_muted)
        self.assertFalse(
            hasattr(self.controller.window.webex_embed, "mute_webex_self")
        )

    def test_mute_self_uses_jamulus_global_setmuted(self):
        """Talk Break must call the global self-mute, not the local fader
        channel fader (which would only mute us in our own monitor)."""
        self._install_local_participant()
        self.controller.bridge.webex_state = "Not opened"
        self.controller._on_mute_self()
        self.controller.jamulus.set_self_muted.assert_called_once_with(True)

    def test_failed_talk_break_stays_in_play(self):
        local = self._install_local_participant()
        self.controller.jamulus.set_self_muted.return_value = False
        self.controller._on_mute_self()
        self.assertFalse(local.muted)
        self.assertFalse(self.controller._talk_break_intended)


# ----------------------------------------------------------------------
# macOS literal-Control shortcut bindings (Qt.MetaModifier branch)
# ----------------------------------------------------------------------
class TestMacShortcutBindings(unittest.TestCase):
    """On macOS the self-mute / diagnostics / reset / save-as shortcuts must
    bind to literal Control (Qt.MetaModifier) so they don't collide with Cmd."""

    def _build_window(self):
        from webjam_qt.controllers.application_controller import ApplicationController
        from webjam_qt.windows.conductor_window import ConductorWindow
        return ConductorWindow(
            mode_entries=ApplicationController.mode_entries(),
            initial_mode_key="music_jam",
            initial_title="Test",
        )

    def test_darwin_uses_meta_modifier(self):
        # conductor_window does a local `import sys`, so patch the global.
        with mock.patch("sys.platform", "darwin"):
            win = self._build_window()
        try:
            for attr in (
                "_mute_self_shortcut",
                "_diagnostics_shortcut",
                "_reset_faders_shortcut",
                "_save_mix_as_shortcut",
            ):
                seq = getattr(win, attr).key().toString()
                self.assertIn("Meta", seq, f"{attr} should bind Meta on macOS, got {seq!r}")
        finally:
            win.close()

    def test_non_darwin_uses_ctrl(self):
        with mock.patch("sys.platform", "linux"):
            win = self._build_window()
        try:
            seq = win._mute_self_shortcut.key().toString()
            self.assertIn("Ctrl", seq)
            self.assertNotIn("Meta", seq)
        finally:
            win.close()


# ----------------------------------------------------------------------
# Webex placeholder auto-restore on real load failure
# ----------------------------------------------------------------------
class TestWebexLoadFailureRestore(unittest.TestCase):
    def _make_embed(self):
        from webjam_qt.widgets.webex_embed import WebexEmbed
        return WebexEmbed()

    def test_real_load_failure_emits_error(self):
        embed = self._make_embed()
        embed._view = mock.MagicMock()
        embed._view.url.return_value.toString.return_value = (
            "https://example.webex.com/meet/bad"
        )
        states = []
        embed.meeting_state_changed.connect(states.append)
        embed._on_view_load_finished(False)
        self.assertIn("error", states)

    def test_load_failure_log_redacts_meeting_destination(self):
        embed = self._make_embed()
        embed._view = mock.MagicMock()
        embed._view.url.return_value.toString.return_value = (
            "https://example.webex.com/meet/private-room?token=secret#lobby"
        )
        with self.assertLogs("webjam.qt.webex_embed", level="WARNING") as logs:
            embed._on_view_load_finished(False)
        output = "\n".join(logs.output)
        self.assertIn("https://example.webex.com/[redacted]", output)
        self.assertNotIn("private-room", output)
        self.assertNotIn("secret", output)

    def test_blank_and_data_urls_do_not_emit_error(self):
        embed = self._make_embed()
        for url in ("", "about:blank", "data:text/html,foo"):
            embed._view = mock.MagicMock()
            embed._view.url.return_value.toString.return_value = url
            states = []
            embed.meeting_state_changed.connect(states.append)
            embed._on_view_load_finished(False)
            self.assertNotIn("error", states, f"{url!r} should be ignored")

    def test_successful_load_emits_nothing(self):
        embed = self._make_embed()
        embed._view = mock.MagicMock()
        states = []
        embed.meeting_state_changed.connect(states.append)
        embed._on_view_load_finished(True)
        self.assertEqual(states, [])

    def test_external_card_title_matches_audio_role(self):
        embed = self._make_embed()
        expected = {
            "talkback": "Webex talkback",
            "video_only": "Webex video",
            "audience_bridge": "Webex audience feed",
        }
        for mode, title in expected.items():
            embed.set_audio_mode(mode)
            self.assertEqual(embed._title_label.text(), title)

    def test_external_card_has_truthful_accessible_launch_name(self):
        embed = self._make_embed()
        embed.set_launch_status("Opened externally")
        self.assertEqual(embed.fallback_button().text(), "Open Again")
        self.assertEqual(
            embed.fallback_button().accessibleName(), "Open Again externally"
        )

    def test_external_card_disables_launch_while_opening(self):
        embed = self._make_embed()
        embed.set_launch_status("Opening…")
        self.assertEqual(embed.fallback_button().text(), "Opening…")
        self.assertFalse(embed.fallback_button().isEnabled())


if __name__ == "__main__":
    unittest.main()
