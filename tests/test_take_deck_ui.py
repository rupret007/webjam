"""
Take Deck dialog — take list, transport, console wiring, empty state.

Uses a headless CapturingSink-style stub player so no audio device is needed.
"""
from __future__ import annotations

import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

_app = QApplication.instance() or QApplication([])

from core.take_library import write_take_manifest  # noqa: E402
from core.take_player import TakePlayer  # noqa: E402
from core.take_project import (  # noqa: E402
    RecoveryStatus,
    SessionEvidence,
    new_project_id,
)
from webjam_qt.windows.take_deck import TakeDeck, _fmt_time  # noqa: E402


RATE = 8000


def _write_wav(path: Path, seconds=0.5, value=0.3):
    n = int(seconds * RATE)
    ints = np.int16(np.full(n, value * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack("<%dh" % n, *ints.tolist()))


class _SilentSink:
    """Sink that does nothing (no audio thread) — the UI drives the player."""
    def start(self, samplerate, blocksize, pull):
        self.started = True

    def stop(self):
        pass


def _make_take_dir(root: Path, name: str, tracks=("guitar.wav", "bass.wav")):
    take = root / name
    take.mkdir()
    for t in tracks:
        _write_wav(take / t)
    return take


class TestFmtTime(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(_fmt_time(0), "0:00")
        self.assertEqual(_fmt_time(65), "1:05")
        self.assertEqual(_fmt_time(-4), "0:00")


class TestTakeDeckEmpty(unittest.TestCase):
    def test_empty_dir_shows_hint(self):
        with tempfile.TemporaryDirectory() as d:
            deck = TakeDeck(d, player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
            try:
                self.assertFalse(deck._play_btn.isEnabled())
                self.assertIn("No takes found", deck._hint.text())
                self.assertNotIn(d, deck._hint.text())
            finally:
                deck.close()

    def test_unset_dir_shows_hint(self):
        deck = TakeDeck("", player=TakePlayer(samplerate=RATE, sink=_SilentSink()))
        try:
            self.assertIn("No Takes folder is set", deck._hint.text())
        finally:
            deck.close()

    def test_empty_hint_mentions_recovered_folder_when_present(self):
        with tempfile.TemporaryDirectory() as d, \
                tempfile.TemporaryDirectory() as home:
            (Path(home) / "Music" / "WebJam Recovered Takes").mkdir(parents=True)
            with mock.patch(
                "webjam_qt.windows.take_deck.Path.home",
                return_value=Path(home),
            ):
                deck = TakeDeck(
                    d, player=TakePlayer(samplerate=RATE, sink=_SilentSink())
                )
            try:
                self.assertIn("No takes found", deck._hint.text())
                self.assertIn("WebJam Recovered Takes", deck._hint.text())
            finally:
                deck.close()

    def test_evidence_only_recovery_is_visible_but_transport_is_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            write_take_manifest(
                root / "Recovered evidence",
                expected_tracks=0,
                required_local_stems=0,
                capture_errors=("Interrupted recording evidence recovered.",),
                session_id=new_project_id(),
                take_id=new_project_id(),
                session_evidence=SessionEvidence(
                    recovery_status=RecoveryStatus.NEEDS_ATTENTION,
                    recovery_notes=("No audio survived.",),
                ),
            )
            player = TakePlayer(samplerate=RATE, sink=_SilentSink())
            deck = TakeDeck(d, player=player)
            try:
                self.assertEqual(deck._take_list.count(), 1)
                self.assertIn("Review Only", deck._take_list.item(0).text())
                self.assertFalse(deck._play_btn.isEnabled())
                self.assertFalse(deck._stop_btn.isEnabled())
                self.assertFalse(deck._scrub.isEnabled())
                self.assertEqual(player.tracks, [])
                self.assertIn("Review only", deck._title.text())
                self.assertIn("cannot be exported", deck._hint.text())
            finally:
                deck.close()


class TestTakeDeckPopulated(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        _make_take_dir(root, "take_A")
        _make_take_dir(root, "take_B", tracks=("vox.wav",))
        self.player = TakePlayer(samplerate=RATE, sink=_SilentSink())
        self.deck = TakeDeck(self.tmp.name, player=self.player)

    def tearDown(self):
        self.deck.close()
        self.tmp.cleanup()

    def test_lists_takes(self):
        self.assertEqual(self.deck._take_list.count(), 2)

    def test_selecting_take_populates_console(self):
        # First take auto-selected on load.
        self.assertTrue(self.deck._play_btn.isEnabled())
        # take_A or take_B is row 0 (newest first) — whichever, console has
        # the right number of cards for the selected take.
        n_tracks = len(self.player.tracks)
        self.assertGreaterEqual(n_tracks, 1)
        self.assertEqual(len(self.deck._console._cards), n_tracks)

    def test_console_fader_sets_player_gain(self):
        cid = self.player.tracks[0].channel_id
        self.deck._on_fader(cid, 50)
        track = next(t for t in self.player.tracks if t.channel_id == cid)
        self.assertAlmostEqual(track.gain, 0.5)

    def test_console_mute_and_solo_route_to_player(self):
        cid = self.player.tracks[0].channel_id
        self.deck._on_mute(cid, True)
        self.deck._on_solo(cid, True)
        track = next(t for t in self.player.tracks if t.channel_id == cid)
        self.assertTrue(track.muted)
        self.assertTrue(track.solo)

    def test_play_button_toggles_player(self):
        self.deck._toggle_play()
        self.assertTrue(self.player.is_playing)
        self.assertEqual(self.deck._play_btn.text(), "⏸ Pause")
        self.deck._toggle_play()
        self.assertFalse(self.player.is_playing)

    def test_stop_resets_position(self):
        self.deck._toggle_play()
        self.player.seek(0.2)
        self.deck._on_stop()
        self.assertEqual(self.player.position_s, 0.0)
        self.assertEqual(self.deck._scrub.value(), 0)

    def test_scrub_release_seeks(self):
        self.deck._scrub.setValue(500)
        self.deck._on_scrub_released()
        # 50% of duration
        self.assertAlmostEqual(
            self.player.position_s, self.player.duration_s * 0.5, delta=0.1
        )

    def test_refresh_repopulates(self):
        _make_take_dir(Path(self.tmp.name), "take_C", tracks=("keys.wav",))
        self.deck.reload()
        self.assertEqual(self.deck._take_list.count(), 3)

    def test_output_choice_is_reported_and_reveal_is_available(self):
        changed = []
        with mock.patch(
            "webjam_qt.windows.take_deck.list_output_devices",
            return_value=[{"index": 3, "name": "SSL 2+", "channels": 2}],
        ):
            deck = TakeDeck(
                self.tmp.name,
                player=TakePlayer(samplerate=RATE, sink=_SilentSink()),
                on_output_device_changed=changed.append,
            )
        try:
            deck._output_picker.setCurrentIndex(1)
            self.assertEqual(changed, ["SSL 2+"])
            self.assertTrue(deck._reveal_btn.isEnabled())
        finally:
            deck.close()


class TestTakeHealthPanel(unittest.TestCase):
    """Selecting takes must not probe audio on the UI thread when a final
    manifest exists, and must label transient 'validating' manifests."""

    def _deck(self, root: str) -> TakeDeck:
        return TakeDeck(root, player=TakePlayer(samplerate=RATE, sink=_SilentSink()))

    def test_manifest_final_status_skips_reprobing(self):
        import json

        with tempfile.TemporaryDirectory() as d:
            take = _make_take_dir(Path(d), "take_A")
            private_path = "/Users/jeff/private/recordings/bass.wav"
            secret = "Bearer deck-secret-123"
            (take / "webjam-take.json").write_text(json.dumps({
                "schema_version": 1,
                "status": "needs_attention",
                "errors": [f"capture failed at {private_path}: {secret}"],
                "warnings": [f"warning at {private_path}: {secret}"],
                "tracks": [],
            }), encoding="utf-8")
            with mock.patch(
                "webjam_qt.windows.take_deck.validate_take"
            ) as probe:
                deck = self._deck(d)
                try:
                    probe.assert_not_called()
                    rendered = "\n".join(
                        widget.text()
                        for widget in deck.findChildren(QLabel)
                    )
                    self.assertIn("needs review", rendered.lower())
                    self.assertNotIn(private_path, rendered)
                    self.assertNotIn(secret, rendered)
                    self.assertNotIn("capture failed", rendered)
                    row = deck._take_list.item(0).text()
                    self.assertIn("Needs Attention", row)
                finally:
                    deck.close()

    def test_verified_take_with_warnings_shows_count(self):
        import json

        with tempfile.TemporaryDirectory() as d:
            take = _make_take_dir(Path(d), "take_A")
            (take / "webjam-take.json").write_text(json.dumps({
                "schema_version": 1,
                "status": "complete",
                "errors": [],
                "warnings": ["guitar.wav appears silent."],
                "tracks": [],
            }), encoding="utf-8")
            deck = self._deck(d)
            try:
                row = deck._take_list.item(0).text()
                self.assertIn("Verified · 1 warning", row)
            finally:
                deck.close()

    def test_manifest_less_take_still_gets_probed(self):
        with tempfile.TemporaryDirectory() as d:
            _make_take_dir(Path(d), "take_A")
            deck = self._deck(d)
            try:
                self.assertIn("2 tracks", deck._title.text())
            finally:
                deck.close()

    def test_validating_status_is_labelled_checking(self):
        import json

        with tempfile.TemporaryDirectory() as d:
            take = _make_take_dir(Path(d), "take_A")
            (take / "webjam-take.json").write_text(json.dumps({
                "schema_version": 1,
                "status": "validating",
                "tracks": [],
            }), encoding="utf-8")
            deck = self._deck(d)
            try:
                self.assertIn("Checking…", deck._take_list.item(0).text())
            finally:
                deck.close()


class TestControllerOpensDeck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.settings import AppSettings
        from webjam_qt.controllers.application_controller import (
            ApplicationController,
        )
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

    def test_takes_rail_key_opens_integrated_studio(self):
        c = self.controller
        c._on_rail_view_changed("takes")
        self.assertIs(
            c.window.workspace_stack.currentWidget(),
            c.window.recording_studio,
        )

    def test_compatibility_open_take_deck_reveals_studio(self):
        c = self.controller
        c._open_take_deck()
        self.assertIs(
            c.window.workspace_stack.currentWidget(),
            c.window.recording_studio,
        )


if __name__ == "__main__":
    unittest.main()
