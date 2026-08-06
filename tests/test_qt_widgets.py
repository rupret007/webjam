"""Smoke tests for webjam_qt widget layer.

Runs headlessly with QT_QPA_PLATFORM=offscreen (set in CI).
Skipped when PySide6 is not available.
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

skip_no_pyside6 = unittest.skipUnless(
    __import__("importlib").util.find_spec("PySide6") is not None,
    "PySide6 not installed",
)


def _qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1])
    return app


# ---------------------------------------------------------------------------
# LevelMeter
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestLevelMeter(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_constructs(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter()
        self.assertIsNotNone(m)

    def test_initial_level_is_zero(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter()
        self.assertEqual(m._level, 0.0)
        self.assertEqual(m._peak, 0.0)

    def test_set_level_clamps_below_zero(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter()
        m.set_level(-0.5)
        self.assertEqual(m._level, 0.0)

    def test_set_level_clamps_above_one(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter()
        m.set_level(2.0)
        self.assertEqual(m._level, 1.0)

    def test_set_level_updates_peak(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter()
        m.set_level(0.6)
        self.assertEqual(m._peak, 0.6)

    def test_peak_does_not_drop_when_new_level_lower(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter()
        m.set_level(0.8)
        m.set_level(0.2)
        self.assertEqual(m._peak, 0.8)

    def test_decay_reduces_level(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter()
        m.set_level(0.5)
        m._decay()
        self.assertLess(m._level, 0.5)

    def test_accessible_name_set(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter()
        self.assertEqual(m.accessibleName(), "Audio level meter")

    def test_custom_height(self):
        from webjam_qt.widgets.level_meter import LevelMeter
        m = LevelMeter(height=8)
        self.assertEqual(m.height(), 8)


# ---------------------------------------------------------------------------
# ParticipantCard
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestParticipantCard(unittest.TestCase):
    def setUp(self):
        _qapp()
        from webjam_qt.widgets.participant_card import ParticipantPresentation
        self.pres = ParticipantPresentation(channel_id=0, name="Alice", role="Guitar")

    def test_constructs(self):
        from webjam_qt.widgets.participant_card import ParticipantCard
        card = ParticipantCard(self.pres)
        self.assertIsNotNone(card)

    def test_name_label_shows_name(self):
        from webjam_qt.widgets.participant_card import ParticipantCard
        card = ParticipantCard(self.pres)
        self.assertEqual(card._name_label.text(), "Alice")

    def test_remote_names_render_as_plain_text_not_markup(self):
        """Jamulus roster names are untrusted; markup in a name must render
        literally instead of being interpreted as rich text."""
        from PySide6.QtCore import Qt
        from webjam_qt.widgets.participant_card import (
            ParticipantCard,
            ParticipantPresentation,
        )
        hostile = ParticipantPresentation(
            channel_id=0, name="<b>Dave</b>", role="<img src='x'> Drums"
        )
        card = ParticipantCard(hostile)
        self.assertEqual(card._name_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(card._role_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(card._name_label.text(), "<b>Dave</b>")
        # Updates keep the guarantee.
        card.update_presentation(ParticipantPresentation(
            channel_id=0, name="<i>Eve</i>", role="Bass"
        ))
        self.assertEqual(card._name_label.text(), "<i>Eve</i>")
        self.assertEqual(card._name_label.textFormat(), Qt.TextFormat.PlainText)

    def test_fader_changed_signal_emits(self):
        from webjam_qt.widgets.participant_card import ParticipantCard
        card = ParticipantCard(self.pres)
        results = []
        card.fader_changed.connect(lambda ch, lv: results.append((ch, lv)))
        card._fader.setValue(80)
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[-1][0], 0)  # channel_id

    def test_mute_button_toggles_signal(self):
        from webjam_qt.widgets.participant_card import ParticipantCard
        card = ParticipantCard(self.pres)
        results = []
        card.mute_toggled.connect(lambda ch, m: results.append((ch, m)))
        card._mute_button.click()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0], 0)

    def test_solo_button_toggles_signal(self):
        from webjam_qt.widgets.participant_card import ParticipantCard
        card = ParticipantCard(self.pres)
        results = []
        card.solo_toggled.connect(lambda ch, s: results.append((ch, s)))
        card._solo_button.click()
        self.assertEqual(len(results), 1)

    def test_minimum_size_set(self):
        from webjam_qt.widgets.participant_card import ParticipantCard, ParticipantCard as PC
        card = ParticipantCard(self.pres)
        self.assertGreaterEqual(card.minimumWidth(), PC.CARD_MIN_WIDTH)
        self.assertGreaterEqual(card.minimumHeight(), PC.CARD_MIN_HEIGHT)

    def test_local_participant_has_distinct_name(self):
        from webjam_qt.widgets.participant_card import ParticipantCard, ParticipantPresentation
        pres = ParticipantPresentation(channel_id=0, name="You", role="Drums", is_local=True)
        card = ParticipantCard(pres)
        self.assertEqual(card._name_label.text(), "You")


# ---------------------------------------------------------------------------
# SessionStrip
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestSessionStrip(unittest.TestCase):
    def setUp(self):
        _qapp()
        from core.creative_modes import CREATIVE_MODES
        self._mode_entries = [(m.key, m.label) for m in CREATIVE_MODES]

    def _strip(self, title="Band Rehearsal"):
        from webjam_qt.widgets.session_strip import SessionStrip
        return SessionStrip(
            mode_entries=self._mode_entries,
            initial_mode_key=self._mode_entries[0][0],
            initial_title=title,
        )

    def test_reset_invite_is_progressive_disclosure_and_emits_once(self):
        from unittest.mock import MagicMock

        strip = self._strip()
        reset = MagicMock()
        strip.reset_invite_requested.connect(reset)

        self.assertFalse(strip._reset_invite_action.isVisible())
        strip.set_reset_invite_available(True)
        self.assertTrue(strip._reset_invite_action.isVisible())
        self.assertIn("Revoke", strip._reset_invite_action.toolTip())
        strip._reset_invite_action.trigger()
        reset.assert_called_once_with()
        strip.set_reset_invite_available(False)
        self.assertFalse(strip._reset_invite_action.isVisible())

    def test_constructs(self):
        s = self._strip()
        self.assertIsNotNone(s)

    def test_fixed_height(self):
        from webjam_qt.widgets.session_strip import SessionStrip
        s = self._strip()
        self.assertEqual(s.height(), SessionStrip.STRIP_HEIGHT)

    def test_initial_title_shown(self):
        s = self._strip("My Session")
        self.assertEqual(s._title_input.text(), "My Session")

    def test_mode_picker_populated(self):
        s = self._strip()
        self.assertGreater(s._mode_picker.count(), 0)

    def test_focus_title_focuses_widget(self):
        s = self._strip()
        s.focus_title()
        # Just check it doesn't raise

    def test_launch_audio_signal_emits(self):
        s = self._strip()
        results = []
        s.launch_audio_requested.connect(lambda: results.append(1))
        s._audio_button.click()
        self.assertEqual(len(results), 1)

    def test_main_webex_button_navigates_without_requesting_a_meeting_launch(self):
        s = self._strip()
        tools = []
        launches = []
        s.tool_requested.connect(tools.append)
        s.join_video_requested.connect(lambda: launches.append(1))
        s._video_button.click()
        self.assertEqual(tools, ["conversation"])
        self.assertEqual(launches, [])
        self.assertEqual(s._video_button.text(), "Webex Controls")

    def test_main_studio_button_uses_the_canonical_workspace_route(self):
        s = self._strip()
        tools = []
        s.tool_requested.connect(tools.append)

        s._studio_button.click()

        self.assertEqual(tools, ["takes"])
        self.assertEqual(s._studio_button.accessibleName(), "Open Studio")

    def test_main_reference_track_button_is_host_only_and_uses_canonical_route(self):
        s = self._strip()
        tools = []
        s.tool_requested.connect(tools.append)

        self.assertTrue(s._reference_track_button.isHidden())
        s.set_reference_track_available(True)
        self.assertFalse(s._reference_track_button.isHidden())
        s._reference_track_button.click()

        self.assertEqual(tools, ["reference_track"])
        self.assertEqual(
            s._reference_track_button.accessibleName(),
            "Open Reference Track",
        )
        s.set_reference_track_available(False)
        self.assertTrue(s._reference_track_button.isHidden())

    def test_webex_menu_label_recovers_after_link_is_configured(self):
        s = self._strip()
        s.set_video_configured(False)
        self.assertEqual(s._video_action.text(), "Set Up Webex Controls")
        s.set_video_configured(True)
        self.assertEqual(s._video_action.text(), "Webex Controls")

    def test_external_handoff_progress_never_disables_conversation_navigation(self):
        s = self._strip()

        s.set_video_state("Opening…", enabled=False)

        self.assertTrue(s._video_button.isEnabled())
        self.assertTrue(s._video_action.isEnabled())
        self.assertEqual(s._video_button.text(), "Webex Controls")
        self.assertEqual(
            s._video_button.property("webexLaunchAction"),
            "Opening…",
        )

    def test_every_more_menu_action_emits_its_semantic_request(self):
        s = self._strip()
        tools: list[str] = []
        video: list[bool] = []
        s.tool_requested.connect(tools.append)
        s.join_video_requested.connect(lambda: video.append(True))
        expected_tools = {
            # Labels name the musician's goal, not the component that
            # implements it. Studio is deliberately absent: it is a
            # first-class button on the session bar, not a menu duplicate.
            "Sound Settings…": "audio_settings",
            "Check for Updates…": "jamulus_updates",
            "Webex Controls": "conversation",
            "Recording Setup…": "recording_setup",
            "Reference Track…": "reference_track",
            "Notes": "canvas",
            "Use iPhone as Pocket Stage…": "pocket_stage",
            "Band Check / Verify Sound\tF2": "diagnostics",
            "Help": "help",
            "Support": "support",
            "About WebJam": "about",
            "Settings…": "settings",
        }
        actions = {
            action.text(): action
            for action in s._tools_button.menu().actions()
            if not action.isSeparator()
        }

        self.assertTrue(expected_tools.keys() <= actions.keys())
        for label, request in expected_tools.items():
            with self.subTest(label=label):
                tools.clear()
                actions[label].trigger()
                self.assertEqual(tools, [request])

        self.assertEqual(video, [])

    def test_band_check_menu_actions_emit_once(self):
        s = self._strip()
        ready: list[bool] = []
        practice: list[bool] = []
        s.ready_check_requested.connect(lambda: ready.append(True))
        s.practice_requested.connect(lambda: practice.append(True))

        s._ready_action.trigger()
        s._practice_action.trigger()

        self.assertEqual(ready, [True])
        self.assertEqual(practice, [True])

    def test_mode_changed_signal_emits_on_picker_change(self):
        s = self._strip()
        results = []
        s.mode_changed.connect(lambda key: results.append(key))
        if s._mode_picker.count() > 1:
            s._mode_picker.setCurrentIndex(1)
            self.assertEqual(len(results), 1)
            self.assertIsInstance(results[0], str)

    def test_current_mode_key_returns_string(self):
        s = self._strip()
        self.assertIsInstance(s.current_mode_key(), str)

    def test_current_title_strips_whitespace(self):
        s = self._strip("  Hello  ")
        s._title_input.setText("  Hello  ")
        self.assertEqual(s.current_title(), "Hello")

    def test_controls_fit_supported_width_while_recording(self):
        s = self._strip()
        s.set_recording_phase("recording")
        s.resize(1100, s.STRIP_HEIGHT)
        s.show()
        _qapp().processEvents()
        controls = [
            s._logo, s._title_input, s._record_elapsed, s._timer_label,
            s._mode_picker, s._record_button, s._test_button,
            s._audio_button, s._video_button, s._reference_track_button,
        ]
        visible = [control for control in controls if control.isVisible()]
        self.assertLess(max(control.geometry().right() for control in visible), 1100)
        for left, right in zip(visible, visible[1:]):
            self.assertLess(left.geometry().right(), right.geometry().left())
        s.close()

    def test_long_live_states_remain_readable_at_supported_width(self):
        s = self._strip()
        s.set_recording_phase("validating", detail="WAITING FOR SERVER FILES…")
        s.set_audio_state("Stop Audio")
        s.set_video_state("Open Again")
        s.resize(1100, s.STRIP_HEIGHT)
        s.show()
        _qapp().processEvents()

        buttons = [
            s._record_button,
            s._test_button,
            s._audio_button,
            s._video_button,
        ]
        self.assertLessEqual(s.minimumSizeHint().width(), 1100)
        for button in buttons:
            self.assertGreaterEqual(button.width(), button.sizeHint().width())
        self.assertGreaterEqual(
            s._mode_picker.width(),
            s._mode_picker.fontMetrics().horizontalAdvance(
                s._mode_picker.currentText()
            ) + 40,
        )
        s.close()

    def test_cleanup_retry_action_is_visible_and_named(self):
        s = self._strip()
        s.set_audio_state("Try Leave Jam", enabled=True)

        self.assertFalse(s._audio_button.isHidden())
        self.assertTrue(s._audio_button.isEnabled())
        self.assertEqual(s._audio_button.accessibleName(), "Try Leave Jam")

    def test_recording_validation_and_attention_states_are_explicit(self):
        s = self._strip()
        s.set_recording_phase("validating")
        self.assertEqual(s._record_button.text(), "Validating…")
        self.assertFalse(s._record_button.isEnabled())
        self.assertEqual(s._record_elapsed.text(), "CHECKING TRACKS…")
        s.set_recording_phase("needs_attention")
        self.assertEqual(s._record_button.text(), "● Record Again")
        self.assertTrue(s._record_button.isEnabled())
        self.assertEqual(s._record_elapsed.text(), "NEEDS ATTENTION")

    def test_validating_detail_overrides_chip_and_default_survives(self):
        s = self._strip()
        s.set_recording_phase("validating", detail="WAITING FOR SERVER FILES…")
        self.assertEqual(s._record_elapsed.text(), "WAITING FOR SERVER FILES…")
        self.assertIn(
            "Waiting for server files",
            s._record_button.accessibleDescription(),
        )
        s.set_recording_phase("validating")
        self.assertEqual(s._record_elapsed.text(), "CHECKING TRACKS…")


# ---------------------------------------------------------------------------
# ParticipantGrid
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestParticipantGrid(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_constructs(self):
        from webjam_qt.widgets.participant_grid import ParticipantGrid
        g = ParticipantGrid()
        self.assertIsNotNone(g)

    def test_empty_grid_has_no_cards(self):
        from webjam_qt.widgets.participant_grid import ParticipantGrid
        g = ParticipantGrid()
        self.assertEqual(len(g._cards), 0)
        self.assertFalse(g._empty_state.isHidden())
        self.assertEqual(g._empty_title.text(), "Ready when you are")

    def test_empty_state_actions_emit_semantic_signals(self):
        from webjam_qt.widgets.participant_grid import ParticipantGrid
        g = ParticipantGrid()
        events = []
        g.start_audio_requested.connect(lambda: events.append("audio"))
        g.ready_check_requested.connect(lambda: events.append("ready"))
        g.practice_requested.connect(lambda: events.append("practice"))
        g._empty_primary.click()
        g._empty_practice.click()
        g._empty_ready.click()
        self.assertEqual(events, ["audio", "practice", "ready"])

    def test_hero_lobby_is_centered_in_stage(self):
        from webjam_qt.widgets.participant_grid import ParticipantGrid
        g = ParticipantGrid()
        g.resize(1000, 640)
        g.show()
        _qapp().processEvents()
        try:
            geo = g._empty_state.geometry()
            viewport_center = g.viewport().width() // 2
            card_center = geo.x() + geo.width() // 2
            self.assertLessEqual(abs(card_center - viewport_center), 40)
            self.assertGreaterEqual(geo.width(), 560)
            # Tall enough to show the whole lobby content, not a clipped strip.
            self.assertGreaterEqual(
                geo.height(), g._empty_state.minimumSizeHint().height()
            )
        finally:
            g.close()

    def test_hero_lobby_has_one_primary_action_and_no_endpoint(self):
        from webjam_qt.session_state import SessionUiState
        from webjam_qt.widgets.participant_grid import ParticipantGrid
        g = ParticipantGrid()
        g.set_session_state(SessionUiState.idle(server="192.168.1.20:22124"))
        self.assertTrue(g._empty_practice.isHidden())
        self.assertTrue(g._empty_ready.isHidden())
        self.assertNotIn("192.168.1.20:22124", g._empty_hint.text())
        self.assertIn("separate tracks", g._empty_hint.text())
        self.assertFalse(g._empty_hint.isHidden())
        g.set_session_state(SessionUiState.connecting("192.168.1.20:22124"))
        self.assertTrue(g._empty_practice.isHidden())
        self.assertTrue(g._empty_hint.isHidden())

    def test_webex_bar_stays_slim(self):
        from webjam_qt.widgets.webex_embed import WebexEmbed
        embed = WebexEmbed()
        # Two compact action rows keep distinct focus/open/mute/settings
        # semantics visible without becoming a second workspace.
        self.assertLessEqual(embed.maximumHeight(), 152)

    def test_webex_launch_status_updates_accessible_truth(self):
        from unittest.mock import patch

        from PySide6.QtGui import QAccessible

        from webjam_qt.widgets.webex_embed import WebexEmbed

        embed = WebexEmbed()
        with patch.object(QAccessible, "updateAccessibility") as announce:
            embed.set_launch_status("Opened externally")

        self.assertEqual(
            embed._status_label.accessibleName(),
            "Webex launch status",
        )
        self.assertEqual(
            embed._status_label.accessibleDescription(),
            "Opened externally—finish joining in Webex.",
        )
        self.assertEqual(
            embed._fallback_btn.accessibleDescription(),
            "Opened externally—finish joining in Webex.",
        )
        announce.assert_called_once()

    def test_set_participants_creates_cards(self):
        from webjam_qt.widgets.participant_grid import ParticipantGrid
        from webjam_qt.widgets.participant_card import ParticipantPresentation
        g = ParticipantGrid()
        participants = [
            ParticipantPresentation(channel_id=0, name="Alice"),
            ParticipantPresentation(channel_id=1, name="Bob"),
        ]
        g.set_participants(participants)
        self.assertEqual(len(g._cards), 2)
        self.assertTrue(g._empty_state.isHidden())

    def test_one_participant_is_large_and_centered(self):
        from webjam_qt.widgets.participant_grid import ParticipantGrid
        from webjam_qt.widgets.participant_card import ParticipantPresentation
        g = ParticipantGrid()
        g.resize(1200, 700)
        g.set_participants([ParticipantPresentation(channel_id=0, name="Alice")])
        g.show()
        _qapp().processEvents()
        try:
            card = g.cards()[0]
            viewport_center = g.viewport().rect().center()
            self.assertLessEqual(abs(card.geometry().center().x() - viewport_center.x()), 24)
            self.assertLessEqual(abs(card.geometry().center().y() - viewport_center.y()), 24)
            self.assertGreaterEqual(card.width(), 700)
            self.assertGreaterEqual(card.height(), 400)
        finally:
            g.close()

    def test_six_participants_form_balanced_centered_grid(self):
        from webjam_qt.widgets.participant_grid import ParticipantGrid
        from webjam_qt.widgets.participant_card import ParticipantPresentation
        g = ParticipantGrid()
        g.resize(1200, 700)
        g.set_participants([
            ParticipantPresentation(channel_id=i, name=f"Person {i}")
            for i in range(6)
        ])
        g.show()
        _qapp().processEvents()
        try:
            geometries = [card.geometry() for card in g.cards()]
            self.assertEqual(len({rect.y() for rect in geometries}), 2)
            self.assertEqual(len({rect.x() for rect in geometries}), 3)
            self.assertEqual(len({(rect.width(), rect.height()) for rect in geometries}), 1)
            union = geometries[0]
            for rect in geometries[1:]:
                union = union.united(rect)
            self.assertLessEqual(
                abs(union.center().x() - g.viewport().rect().center().x()), 24
            )
        finally:
            g.close()

    def test_set_participants_replaces_cards(self):
        from webjam_qt.widgets.participant_grid import ParticipantGrid
        from webjam_qt.widgets.participant_card import ParticipantPresentation
        g = ParticipantGrid()
        g.set_participants([ParticipantPresentation(channel_id=0, name="Alice")])
        g.set_participants([
            ParticipantPresentation(channel_id=0, name="Alice"),
            ParticipantPresentation(channel_id=1, name="Bob"),
            ParticipantPresentation(channel_id=2, name="Carl"),
        ])
        self.assertEqual(len(g._cards), 3)

    def test_clear_participants_removes_cards(self):
        from webjam_qt.widgets.participant_grid import ParticipantGrid
        from webjam_qt.widgets.participant_card import ParticipantPresentation
        g = ParticipantGrid()
        g.set_participants([ParticipantPresentation(channel_id=0, name="Alice")])
        g.set_participants([])
        self.assertEqual(len(g._cards), 0)
        self.assertFalse(g._empty_state.isHidden())

    def test_update_level_does_not_raise_for_unknown_channel(self):
        from webjam_qt.widgets.participant_grid import ParticipantGrid
        g = ParticipantGrid()
        g.update_level(999, 0.5)  # unknown channel — should not raise


# ---------------------------------------------------------------------------
# SideRail
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestSideRail(unittest.TestCase):
    def setUp(self):
        _qapp()

    def test_constructs(self):
        from webjam_qt.widgets.side_rail import SideRail
        r = SideRail()
        self.assertIsNotNone(r)

    def test_view_changed_signal_emits_on_button_click(self):
        from webjam_qt.widgets.side_rail import SideRail
        r = SideRail()
        results = []
        r.view_changed.connect(lambda key: results.append(key))
        # Click a non-current button to trigger view_changed
        buttons = r._group.buttons()
        for btn in buttons:
            if not btn.isChecked():
                btn.click()
                break
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], str)

    def test_settings_item_in_default_rail(self):
        from webjam_qt.widgets.side_rail import SideRail
        r = SideRail()
        keys = [btn.property("railKey") for btn in r._group.buttons()]
        self.assertIn("settings", keys)

    def test_initial_key_is_checked(self):
        from webjam_qt.widgets.side_rail import SideRail
        r = SideRail(initial_key="canvas")
        checked = [btn for btn in r._group.buttons() if btn.isChecked()]
        self.assertEqual(len(checked), 1)
        self.assertEqual(checked[0].property("railKey"), "canvas")

    def test_workspace_and_utility_items_have_distinct_semantics(self):
        from webjam_qt.widgets.side_rail import SideRail
        r = SideRail()
        buttons = {btn.property("railKey"): btn for btn in r._group.buttons()}
        self.assertEqual(buttons["canvas"].text(), "Notes")
        self.assertEqual(buttons["takes"].property("utility"), "false")
        self.assertEqual(buttons["takes"].text(), "Studio")
        self.assertEqual(buttons["settings"].accessibleName(), "Open Settings")

    def test_trigger_uses_normal_signal_path(self):
        from webjam_qt.widgets.side_rail import SideRail
        r = SideRail()
        events = []
        r.view_changed.connect(events.append)
        r.trigger("canvas")
        self.assertEqual(events, ["canvas"])


# ---------------------------------------------------------------------------
# ConductorWindow (integration smoke)
# ---------------------------------------------------------------------------
@skip_no_pyside6
class TestConductorWindow(unittest.TestCase):
    def setUp(self):
        _qapp()
        from core.creative_modes import CREATIVE_MODES
        self._mode_entries = [(m.key, m.label) for m in CREATIVE_MODES]

    def _window(self):
        from webjam_qt.windows.conductor_window import ConductorWindow
        return ConductorWindow(
            mode_entries=self._mode_entries,
            initial_mode_key=self._mode_entries[0][0],
            initial_title="Test Session",
        )

    def test_constructs(self):
        w = self._window()
        self.assertIsNotNone(w)

    def test_window_title(self):
        w = self._window()
        # Title now includes the WebJam version string (v0.4.4 etc.)
        self.assertTrue(w.windowTitle().startswith("WebJam — Band Session"))
        from webjam_qt import __version__
        self.assertIn(__version__, w.windowTitle())

    def test_supported_narrow_minimum_size(self):
        w = self._window()
        # Leave room for native frame/title-bar chrome inside a physical
        # 760x600 display. A client-area minimum equal to the entire screen
        # silently clips the bottom meeting controls.
        self.assertLessEqual(w.minimumWidth(), 720)
        self.assertLessEqual(w.minimumHeight(), 560)

    def test_hidden_session_tools_are_not_in_initial_focus_chain(self):
        from PySide6.QtCore import Qt

        w = self._window()
        w.show()
        _qapp().processEvents()
        current = w.session_strip._title_input
        focusable = []
        visited = set()
        while current not in visited:
            visited.add(current)
            if (
                current.focusPolicy() != Qt.FocusPolicy.NoFocus
                and current.isVisibleTo(w)
                and current.isEnabled()
            ):
                focusable.append(current)
            current = current.nextInFocusChain()

        self.assertIn(w.session_strip._title_input, focusable)
        self.assertIn(w.session_strip._tools_button, focusable)
        self.assertIn(w.session_strip._video_button, focusable)
        self.assertIn(w.session_strip._studio_button, focusable)
        if w.session_strip._reference_track_button.isVisibleTo(w):
            self.assertIn(w.session_strip._reference_track_button, focusable)
        self.assertIn(w.participant_grid._empty_primary, focusable)
        self.assertNotIn(w.webex_embed.fallback_button(), focusable)
        self.assertNotIn(w.session_canvas._toolbar_buttons[0], focusable)
        self.assertNotIn(w.session_canvas._notes, focusable)
        w.close()

    def test_set_status_audio(self):
        w = self._window()
        w.set_status_audio("Connected")
        self.assertIn("Connected", w._status_audio.text())

    def test_meeting_controls_are_bottom_aligned_and_end_is_destructive(self):
        w = self._window()
        w.resize(1200, 800)
        w.show()
        _qapp().processEvents()
        try:
            self.assertGreater(
                w.session_controls.geometry().top(),
                w.centralWidget().height() // 2,
            )
            self.assertEqual(
                w.session_strip._audio_button.property("destructive"), "true"
            )
            self.assertTrue(w.session_strip._video_button.isVisibleTo(w))
            self.assertTrue(w.session_strip._studio_button.isVisibleTo(w))
            self.assertEqual(w.session_strip._tools_button.text(), "More ▾")
        finally:
            w.close()

    def test_conversation_actions_fit_supported_compact_window(self):
        from services.webex_app import WebexAppState

        w = self._window()
        w.resize(720, 560)
        w.webex_embed.set_meeting_configured(True)
        w.webex_embed.set_app_status(
            WebexAppState.INSTALLED,
            version="46.7.0",
            publisher_verified=True,
        )
        w.webex_embed.show()
        w.show()
        _qapp().processEvents()
        try:
            actions = (
                w.webex_embed.bring_forward_button(),
                w.webex_embed.mute_button(),
                w.webex_embed.fallback_button(),
                w.webex_embed.change_link_button(),
            )
            for action in actions:
                with self.subTest(action=action.text()):
                    self.assertTrue(action.isVisibleTo(w))
                    self.assertGreaterEqual(action.geometry().left(), 0)
                    self.assertLess(
                        action.geometry().right(),
                        w.webex_embed.width(),
                    )
                    self.assertLess(
                        action.geometry().bottom(),
                        w.webex_embed.height(),
                    )
            for index, first in enumerate(actions):
                for second in actions[index + 1 :]:
                    self.assertFalse(
                        first.geometry().intersects(second.geometry())
                    )
            empty = w.participant_grid._empty_state
            self.assertLess(
                empty.geometry().bottom(),
                w.participant_grid.viewport().height(),
            )
            self.assertFalse(w.participant_grid._empty_hint.isVisibleTo(w))
            self.assertLess(
                empty.mapTo(w, empty.rect().bottomLeft()).y(),
                w.webex_embed.mapTo(w, w.webex_embed.rect().topLeft()).y(),
            )

            w.webex_embed.set_app_status(WebexAppState.NOT_INSTALLED)
            _qapp().processEvents()
            recovery_actions = (
                w.webex_embed.install_button(),
                w.webex_embed.recheck_button(),
            )
            for action in recovery_actions:
                with self.subTest(recovery=action.text()):
                    self.assertTrue(action.isVisibleTo(w))
                    self.assertGreaterEqual(action.geometry().left(), 0)
                    self.assertLess(
                        action.geometry().right(),
                        w.webex_embed.width(),
                    )
                    self.assertLess(
                        action.geometry().bottom(),
                        w.webex_embed.height(),
                    )
            self.assertFalse(
                recovery_actions[0].geometry().intersects(
                    recovery_actions[1].geometry()
                )
            )
            self.assertLess(
                w.participant_grid._empty_state.geometry().bottom(),
                w.participant_grid.viewport().height(),
            )
        finally:
            w.close()

    def test_production_styled_conversation_and_lobby_fit_supported_sizes(self):
        from services.webex_app import WebexAppState
        from webjam_qt.session_state import SessionUiState
        from webjam_qt.theme import load_stylesheet

        webex_states = (
            (WebexAppState.INSTALLED, "", True),
            (WebexAppState.NOT_INSTALLED, "", False),
            (WebexAppState.INVALID, "", False),
            (WebexAppState.UNSUPPORTED, "detection-failed", False),
        )
        lobby_states = (
            SessionUiState.idle(),
            SessionUiState.reconnect_failed(),
            SessionUiState.permission_denied(),
            SessionUiState.stop_failed(),
        )
        for width, height in ((720, 560), (760, 600)):
            for webex_state, reason, verified in webex_states:
                with self.subTest(
                    size=(width, height),
                    webex=webex_state.value,
                ):
                    w = self._window()
                    w.setStyleSheet(load_stylesheet())
                    w.resize(width, height)
                    w.webex_embed.set_meeting_configured(True)
                    w.webex_embed.set_app_status(
                        webex_state,
                        publisher_verified=verified,
                        reason_code=reason,
                    )
                    w.webex_embed.show()
                    w.show()
                    _qapp().processEvents()
                    try:
                        visible_actions = [
                            action
                            for action in (
                                w.webex_embed.bring_forward_button(),
                                w.webex_embed.mute_button(),
                                w.webex_embed.fallback_button(),
                                w.webex_embed.change_link_button(),
                                w.webex_embed.install_button(),
                                w.webex_embed.recheck_button(),
                            )
                            if action.isVisibleTo(w)
                        ]
                        for action in visible_actions:
                            self.assertGreaterEqual(action.geometry().left(), 0)
                            self.assertLess(
                                action.geometry().right(),
                                w.webex_embed.width(),
                            )
                            self.assertLess(
                                action.geometry().bottom(),
                                w.webex_embed.height(),
                            )
                        for index, first in enumerate(visible_actions):
                            for second in visible_actions[index + 1 :]:
                                self.assertFalse(
                                    first.geometry().intersects(
                                        second.geometry()
                                    )
                                )

                        for lobby_state in lobby_states:
                            w.participant_grid.set_session_state(lobby_state)
                            _qapp().processEvents()
                            frame = w.participant_grid._empty_state
                            self.assertLess(
                                frame.geometry().bottom(),
                                w.participant_grid.viewport().height(),
                            )
                            for action in (
                                w.participant_grid._empty_primary,
                                w.participant_grid._empty_practice,
                                w.participant_grid._empty_ready,
                            ):
                                if action.isVisibleTo(w):
                                    self.assertLess(
                                        action.geometry().bottom(),
                                        frame.height(),
                                    )
                    finally:
                        w.close()

    def test_conversation_focus_chain_matches_visual_action_order(self):
        from PySide6.QtCore import Qt

        from services.webex_app import WebexAppState

        w = self._window()
        w.webex_embed.set_meeting_configured(True)
        w.webex_embed.set_app_status(
            WebexAppState.INSTALLED,
            publisher_verified=True,
        )
        w.session_strip.set_invite_available(True)
        w.session_strip.set_recording_available(True)
        w.session_strip.set_audio_state("End Session")
        w.webex_embed.show()
        w.show()
        w._setup_tab_order()
        _qapp().processEvents()
        try:
            expected = [
                w.webex_embed.bring_forward_button(),
                w.webex_embed.mute_button(),
                w.webex_embed.fallback_button(),
                w.webex_embed.change_link_button(),
                w.session_strip._invite_button,
                w.session_strip._record_button,
                w.session_strip._video_button,
                w.session_strip._studio_button,
                w.session_strip._tools_button,
                w.session_strip._audio_button,
            ]
            current = expected[0]
            actual = [current]
            for _ in range(50):
                current = current.nextInFocusChain()
                if (
                    current.focusPolicy() != Qt.FocusPolicy.NoFocus
                    and current.isVisibleTo(w)
                    and current.isEnabled()
                ):
                    actual.append(current)
                    if len(actual) == len(expected):
                        break
            self.assertEqual(actual, expected)
        finally:
            w.close()

    def test_narrow_live_controls_fit_with_all_safety_states_visible(self):
        w = self._window()
        strip = w.session_strip
        strip.set_invite_available(True)
        strip.set_recording_available(True)
        strip.set_reference_track_available(True)
        strip.set_recording_phase("stop_failed")
        strip.set_audio_state("Try End Session")
        w.resize(720, 560)
        w.show()
        _qapp().processEvents()
        try:
            self.assertTrue(strip._reference_track_button.isVisibleTo(w))
            self.assertGreaterEqual(
                strip._reference_track_button.geometry().left(),
                0,
            )
            self.assertLess(
                strip._reference_track_button.geometry().right(),
                strip.width(),
            )
            controls = (
                strip._invite_button,
                strip._record_elapsed,
                strip._record_button,
                strip._video_button,
                strip._studio_button,
                strip._tools_button,
                strip._audio_button,
            )
            visible = [control for control in controls if control.isVisibleTo(w)]
            self.assertEqual(visible, list(controls))
            for control in visible:
                with self.subTest(control=control.text()):
                    self.assertGreaterEqual(control.geometry().left(), 0)
                    self.assertLess(
                        control.geometry().right(),
                        w.session_controls.width(),
                    )
            for first, second in zip(visible, visible[1:]):
                self.assertLess(first.geometry().right(), second.geometry().left())
        finally:
            w.close()

    def test_hosting_status_never_becomes_a_floating_window(self):
        w = self._window()
        w.show()
        w.set_status_server("Hosting")
        _qapp().processEvents()
        try:
            self.assertIs(w._status_server.parentWidget(), w._status_bar)
            self.assertFalse(w._status_server.isWindow())
            self.assertFalse(w._status_server.isVisible())
        finally:
            w.close()

    def test_set_status_video(self):
        w = self._window()
        w.set_status_video("Joined")
        self.assertIn("Joined", w._status_video.text())

    def test_set_status_latency(self):
        w = self._window()
        w.set_status_latency("12 ms")
        self.assertIn("12 ms", w._status_latency.text())

    def test_flash_message_does_not_raise(self):
        w = self._window()
        w.flash_message("Test message", ms=100)

    def test_help_copy_names_the_discoverable_studio_menu_item(self):
        w = self._window()
        from unittest import mock

        with mock.patch(
            "PySide6.QtWidgets.QMessageBox.exec",
            return_value=0,
        ), mock.patch(
            "PySide6.QtWidgets.QMessageBox.setText",
        ) as set_text:
            w.show_help()

        body = set_text.call_args.args[0]
        self.assertIn("Choose <b>Studio</b>", body)
        self.assertIn("build a song project", body)
        self.assertIn("review completed session takes", body)
        self.assertIn("Choose <b>Webex Controls</b> to show Conversation", body)
        self.assertIn(
            "<b>Show Webex App</b> brings the verified application forward",
            body,
        )
        self.assertIn("Webex chooses which of its windows is shown", body)
        self.assertIn("Only <b>Join / Open Meeting</b> opens", body)
        self.assertIn("<b>Reference Track</b> to load", body)
        self.assertIn("Play stays locked until", body)
        self.assertNotIn("Multitrack Studio", body)

    def test_help_copy_uses_real_macos_shortcut_modifiers(self):
        w = self._window()
        from unittest import mock

        with mock.patch(
            "sys.platform",
            "darwin",
        ), mock.patch(
            "PySide6.QtWidgets.QMessageBox.exec",
            return_value=0,
        ), mock.patch(
            "PySide6.QtWidgets.QMessageBox.setText",
        ) as set_text:
            w.show_help()

        body = set_text.call_args.args[0]
        self.assertIn("⌘1 / ⌘2 / ⌘3 — Live / Notes / Studio", body)
        self.assertIn(
            "⌘S / ⌘O — Save / load your monitor mix while Live is open",
            body,
        )
        self.assertIn("Control+Shift+R — Reset every fader", body)
        self.assertNotIn("Ctrl+1", body)

    def test_about_copy_reports_version_build_target_and_trust(self):
        w = self._window()
        from unittest import mock

        with mock.patch(
            "core.build_info.build_id",
            return_value="a" * 40,
        ), mock.patch(
            "core.build_info.desktop_target",
            return_value="macos-arm64",
        ), mock.patch(
            "PySide6.QtWidgets.QMessageBox.exec",
            return_value=0,
        ), mock.patch(
            "PySide6.QtWidgets.QMessageBox.setText",
        ) as set_text, mock.patch(
            "PySide6.QtWidgets.QMessageBox.setDetailedText",
        ) as set_detail:
            w.show_about()

        body = set_text.call_args.args[0]
        self.assertIn("WebJam v0.22.4", body)
        self.assertIn("aaaaaaaaaaaa", body)
        self.assertIn("macos-arm64", body)
        self.assertIn("Private test candidate", body)
        self.assertIn("not Apple-notarized", body)
        set_detail.assert_called_once_with(f"Full build ID: {'a' * 40}")

    def test_about_trust_copy_matches_the_packaged_desktop_target(self):
        w = self._window()
        from unittest import mock

        expected = {
            "macos-arm64": ("ad-hoc signed", "not Apple-notarized"),
            "macos-x64": ("ad-hoc signed", "not Apple-notarized"),
            "windows-x64": ("Windows test build is unsigned", "private testing only"),
            "linux-x64": ("Linux build is an unsigned portable", "test candidate"),
            "": ("untrusted private test candidate", "verify its package identity"),
        }
        for target, phrases in expected.items():
            with self.subTest(target=target or "unknown"), mock.patch(
                "core.build_info.build_id",
                return_value="b" * 40,
            ), mock.patch(
                "core.build_info.desktop_target",
                return_value=target,
            ), mock.patch(
                "PySide6.QtWidgets.QMessageBox.exec",
                return_value=0,
            ), mock.patch(
                "PySide6.QtWidgets.QMessageBox.setText",
            ) as set_text:
                w.show_about()

            body = set_text.call_args.args[0]
            for phrase in phrases:
                self.assertIn(phrase, body)
            if not target.startswith("macos-"):
                self.assertNotIn("Apple-notarized", body)

    def test_about_uses_parentless_screen_executor(self):
        w = self._window()
        from unittest import mock

        with mock.patch.object(
            w,
            "_exec_message_box_on_screen",
            return_value=0,
        ) as execute:
            w.show_about()

        box = execute.call_args.args[0]
        try:
            self.assertIsNone(box.parent())
            # The executor, not Cocoa's parent-sheet placement, owns final
            # modality and screen clamping. Prove About did not regain the
            # off-screen main window as its native parent.
            execute.assert_called_once_with(box)
        finally:
            box.close()

    def test_message_box_executor_is_application_modal_and_on_screen(self):
        w = self._window()
        from unittest import mock

        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox()
        box.setText("Visible package information")
        with mock.patch(
            "PySide6.QtWidgets.QMessageBox.exec",
            return_value=0,
        ), mock.patch(
            "webjam_qt.windows.conductor_window.QTimer.singleShot",
            side_effect=lambda _delay, callback: callback(),
        ):
            w._exec_message_box_on_screen(box)

        screen = QGuiApplication.primaryScreen()
        try:
            self.assertEqual(
                box.windowModality(),
                Qt.WindowModality.ApplicationModal,
            )
            if screen is not None:
                self.assertTrue(screen.availableGeometry().contains(box.geometry()))
        finally:
            box.close()

    def test_fullscreen_toggle_roundtrip(self):
        w = self._window()
        # just ensure the methods exist and don't raise
        w._toggle_fullscreen()
        w._exit_fullscreen()

    def test_settings_shortcut_exists(self):
        w = self._window()
        self.assertIsNotNone(w._settings_shortcut)

    def test_live_session_shortcuts_cannot_compete_with_studio_commands(self):
        from PySide6.QtCore import Qt

        w = self._window()
        for shortcut in (
            w._save_mix_shortcut,
            w._load_mix_shortcut,
            w._save_mix_as_shortcut,
            w._load_mix_from_shortcut,
            w._timestamp_shortcut,
            w._practice_shortcut,
            w._mute_all_shortcut,
            w._reset_faders_shortcut,
        ):
            self.assertIs(shortcut.parent(), w.center_splitter)
            self.assertEqual(
                shortcut.context(),
                Qt.ShortcutContext.WidgetWithChildrenShortcut,
            )

    def test_offline_reference_studio_disables_hidden_live_navigation(self):
        w = self._window()
        w.show_reference_studio_only()
        self.assertTrue(w._reference_studio_only)
        self.assertFalse(w.session_strip.isVisible())
        self.assertFalse(w.session_hud.isVisible())
        self.assertFalse(w.session_controls.isVisible())
        self.assertFalse(w.side_rail.isVisible())
        self.assertFalse(w._title_shortcut.isEnabled())
        self.assertFalse(w._ready_check_shortcut.isEnabled())
        self.assertTrue(
            all(not shortcut.isEnabled() for shortcut in w._navigation_shortcuts)
        )
        self.assertIs(
            w.workspace_stack.currentWidget(),
            w.reference_studio,
        )

    def test_offline_reference_studio_help_does_not_describe_live_setup(self):
        w = self._window()
        from unittest import mock

        w.show_reference_studio_only()
        with mock.patch(
            "PySide6.QtWidgets.QMessageBox.exec",
            return_value=0,
        ), mock.patch(
            "PySide6.QtWidgets.QMessageBox.setText",
        ) as set_text:
            w.show_help()

        body = set_text.call_args.args[0]
        self.assertIn("Build and rehearse a song offline", body)
        self.assertIn("Import a backing track", body)
        self.assertIn("separate from Jamulus", body)
        self.assertNotIn("Copy Invite", body)

    def test_close_event_respects_confirm_close_veto(self):
        w = self._window()
        emitted = []
        w.close_requested.connect(lambda: emitted.append(True))
        w.confirm_close = lambda: False
        w.show()
        self.assertFalse(w.close())
        self.assertEqual(emitted, [])
        self.assertTrue(w.isVisible())
        w.confirm_close = None
        w.close()

    def test_close_event_default_emits_close_requested(self):
        w = self._window()
        emitted = []
        w.close_requested.connect(lambda: emitted.append(True))
        w.show()
        self.assertTrue(w.close())
        self.assertEqual(emitted, [True])

    def test_close_event_respects_late_teardown_veto(self):
        w = self._window()
        emitted = []
        w.close_requested.connect(lambda: emitted.append(True))
        w.confirm_close = lambda: True
        w.finalize_close = lambda: False
        w.show()
        self.assertFalse(w.close())
        self.assertEqual(emitted, [])
        self.assertTrue(w.isVisible())
        w.finalize_close = None
        w.close()

    def test_center_splitter_panes_are_not_collapsible(self):
        w = self._window()
        self.assertFalse(w.center_splitter.isCollapsible(0))
        self.assertFalse(w.center_splitter.isCollapsible(1))


if __name__ == "__main__":
    unittest.main()
