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

    def test_join_video_signal_emits(self):
        s = self._strip()
        results = []
        s.join_video_requested.connect(lambda: results.append(1))
        s._video_button.click()
        self.assertEqual(len(results), 1)

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
            s._mute_self_button, s._audio_button, s._video_button,
        ]
        visible = [control for control in controls if control.isVisible()]
        self.assertLess(max(control.geometry().right() for control in visible), 1100)
        for left, right in zip(visible, visible[1:]):
            self.assertLess(left.geometry().right(), right.geometry().left())
        s.close()

    def test_long_live_states_remain_readable_at_supported_width(self):
        s = self._strip()
        s.set_webex_audio_mode("video_only")
        s.set_self_muted(True)
        s.set_recording_phase("validating", detail="WAITING FOR SERVER FILES…")
        s.set_audio_state("Stop Audio")
        s.set_video_state("Open Again")
        s.resize(1100, s.STRIP_HEIGHT)
        s.show()
        _qapp().processEvents()

        buttons = [
            s._record_button,
            s._test_button,
            s._mute_self_button,
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
        g._empty_primary.click()
        g._empty_ready.click()
        self.assertEqual(events, ["audio", "ready"])

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
        self.assertEqual(buttons["takes"].property("utility"), "true")
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
        self.assertTrue(w.windowTitle().startswith("WebJam — Conductor"))
        from webjam_qt import __version__
        self.assertIn(__version__, w.windowTitle())

    def test_minimum_size(self):
        w = self._window()
        self.assertGreaterEqual(w.minimumWidth(), 1100)
        self.assertGreaterEqual(w.minimumHeight(), 720)

    def test_tab_order_follows_stage_then_webex_then_canvas(self):
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

        self.assertLess(
            focusable.index(w.participant_grid._empty_ready),
            focusable.index(w.webex_embed.fallback_button()),
        )
        self.assertLess(
            focusable.index(w.webex_embed.fallback_button()),
            focusable.index(w.session_canvas._toolbar_buttons[0]),
        )
        self.assertLess(
            focusable.index(w.session_canvas._toolbar_buttons[-1]),
            focusable.index(w.session_canvas._notes),
        )
        w.close()

    def test_set_status_audio(self):
        w = self._window()
        w.set_status_audio("Connected")
        self.assertIn("Connected", w._status_audio.text())

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

    def test_fullscreen_toggle_roundtrip(self):
        w = self._window()
        # just ensure the methods exist and don't raise
        w._toggle_fullscreen()
        w._exit_fullscreen()

    def test_settings_shortcut_exists(self):
        w = self._window()
        self.assertIsNotNone(w._settings_shortcut)

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

    def test_center_splitter_panes_are_not_collapsible(self):
        w = self._window()
        self.assertFalse(w.center_splitter.isCollapsible(0))
        self.assertFalse(w.center_splitter.isCollapsible(1))


if __name__ == "__main__":
    unittest.main()
