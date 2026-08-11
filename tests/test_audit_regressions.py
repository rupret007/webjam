"""Regression tests closing blind spots found in the v0.4.7 pre-ship audit.

Each test here guards a behavior that was correct in code but had no test —
so a future change couldn't silently regress it.  Covers:

* WCAG AA contrast of the text tokens on their real backgrounds
* the fader's keyboard-step + accessible-name attributes
* absence of the unsupported Jamulus live-send mute UI and shortcut
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

    def test_inverse_labels_pass_AA_on_filled_accent_buttons(self):
        """Filled buttons (Start Audio, Open Webex, MUTE-active) render
        TEXT_INVERSE on accent backgrounds."""
        for accent in ("ACCENT_VIDEO", "ACCENT_AUDIO", "ACCENT_DANGER"):
            ratio = _contrast(self.c.TEXT_INVERSE, getattr(self.c, accent))
            self.assertGreaterEqual(
                ratio, self.AA, f"TEXT_INVERSE on {accent} = {ratio:.2f}:1"
            )


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
# Jamulus live-send mute is unsupported and absent
# ----------------------------------------------------------------------
class TestLiveSendMuteCapability(unittest.TestCase):
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

    def test_session_ui_has_no_live_send_mute_control(self):
        strip = self.window.session_strip
        self.assertFalse(hasattr(strip, "mute_self_requested"))
        self.assertFalse(hasattr(strip, "_mute_self_button"))
        self.assertFalse(hasattr(strip, "_talk_action"))
        self.assertFalse(hasattr(self.window, "_mute_self_shortcut"))

    def test_stale_compatibility_state_can_only_be_cleared(self):
        self.controller._self_transmit_muted = True
        self.controller._talk_break_intended = True
        with mock.patch.object(self.controller.jamulus, "set_self_muted") as rpc:
            self.controller._sync_self_mute_button()
        rpc.assert_not_called()
        self.assertFalse(self.controller._self_transmit_muted)
        self.assertFalse(self.controller._talk_break_intended)

    def test_webex_guidance_names_real_safe_actions(self):
        self.window.webex_embed.set_audio_mode("talkback")
        guidance = self.window.webex_embed._mode_label.text()
        self.assertIn("audio interface", guidance)
        self.assertIn("end the WebJam session", guidance)
        self.assertNotIn("Talk Break", guidance)


# ----------------------------------------------------------------------
# macOS literal-Control shortcut bindings (Qt.MetaModifier branch)
# ----------------------------------------------------------------------
class TestMacShortcutBindings(unittest.TestCase):
    """On macOS the diagnostics / reset / save-as shortcuts must
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
            seq = win._diagnostics_shortcut.key().toString()
            self.assertIn("Ctrl", seq)
            self.assertNotIn("Meta", seq)
        finally:
            win.close()


# ----------------------------------------------------------------------
# External Webex launch-card truth
# ----------------------------------------------------------------------
class TestExternalWebexLaunchCard(unittest.TestCase):
    def _make_embed(self):
        from webjam_qt.widgets.webex_embed import WebexEmbed
        return WebexEmbed()

    def test_external_card_title_matches_audio_role(self):
        embed = self._make_embed()
        expected = {
            "talkback": "Conversation",
            "video_only": "Conversation video",
            "audience_bridge": "Conversation audience feed",
        }
        for mode, title in expected.items():
            embed.set_audio_mode(mode)
            self.assertEqual(embed._title_label.text(), title)

    def test_external_card_has_truthful_accessible_launch_name(self):
        embed = self._make_embed()
        embed.set_launch_status("Opened externally")
        self.assertEqual(embed.fallback_button().text(), "Open Again")
        self.assertEqual(
            embed.fallback_button().accessibleName(),
            "Open the meeting link again",
        )
        self.assertIn(
            "externally", embed.fallback_button().accessibleDescription()
        )
        embed.set_launch_status("Opening…")
        self.assertEqual(
            embed.fallback_button().accessibleName(),
            "Opening the meeting link",
        )

    def test_external_card_disables_launch_while_opening(self):
        embed = self._make_embed()
        embed.set_launch_status("Opening…")
        self.assertEqual(embed.fallback_button().text(), "Opening…")
        self.assertFalse(embed.fallback_button().isEnabled())


if __name__ == "__main__":
    unittest.main()
