"""
TakePlayer — multitrack mixing, transport, gain/mute/solo, offsets.

Fully headless: a synchronous test sink pulls blocks on demand and captures
the mixed output, so the whole engine is verified without audio hardware.
"""
from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import wave
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import List
from unittest.mock import patch

import numpy as np

from core.take_player import PlaybackError, SoundDeviceSink, TakePlayer


RATE = 8000  # small rate keeps tests fast


def _write_sine(path: Path, seconds: float, amp: float = 0.5, freq: float = 200.0):
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    samples = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    ints = np.int16(np.clip(samples, -1, 1) * 32767)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack("<%dh" % n, *ints.tolist()))


def _write_const(path: Path, seconds: float, value: float):
    n = int(seconds * RATE)
    ints = np.int16(np.full(n, np.clip(value, -1, 1) * 32767))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack("<%dh" % n, *ints.tolist()))


@dataclass
class _Track:
    path: Path
    name: str
    offset_s: float = 0.0
    duration_s: float = 0.0


@dataclass
class _Take:
    tracks: list


class CapturingSink:
    """Synchronous sink: pull the take (plus a little tail) into memory.

    Pulls exactly enough blocks to cover the player's known duration so
    leading-silence tracks aren't mistaken for the end.
    """

    def __init__(self, max_blocks=100000):
        self.blocks: List[np.ndarray] = []
        self.max_blocks = max_blocks
        self.started = False
        self.stopped = False

    def start(self, samplerate, blocksize, pull):
        self.started = True
        # blocks needed to render the whole take, + 2 tail blocks.
        # pull() itself stops advancing past the end, so over-pulling is safe.
        # We don't know duration here, so pull generously but bail on a long
        # trailing silence run (>= 4 blocks) once we've produced some audio.
        produced_audio = False
        silence_run = 0
        for _ in range(self.max_blocks):
            block = pull(blocksize)
            self.blocks.append(np.array(block))
            if np.abs(block).max() > 1e-6:
                produced_audio = True
                silence_run = 0
            else:
                silence_run += 1
            if produced_audio and silence_run >= 4:
                break

    def stop(self):
        self.stopped = True

    def mixed(self) -> np.ndarray:
        return np.concatenate(self.blocks) if self.blocks else np.zeros(0)


def _take_from(tmp: str, specs):
    """specs: list of (filename, seconds, value_or_None, offset)."""
    tracks = []
    for name, secs, value, offset in specs:
        p = Path(tmp) / name
        if value is None:
            _write_sine(p, secs)
        else:
            _write_const(p, secs, value)
        tracks.append(_Track(path=p, name=name, offset_s=offset,
                             duration_s=secs))
    return _Take(tracks=tracks)


class TestTransportAndDuration(unittest.TestCase):
    def test_load_computes_duration_from_offsets(self):
        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [
                ("a.wav", 1.0, 0.3, 0.0),
                ("b.wav", 1.0, 0.3, 2.0),   # ends at 3.0
            ])
            player = TakePlayer(samplerate=RATE, blocksize=256,
                                sink=CapturingSink())
            player.load(take)
        self.assertAlmostEqual(player.duration_s, 3.0, places=2)
        self.assertEqual(len(player.tracks), 2)
        self.assertEqual(player.position_s, 0.0)

    def test_play_pulls_audio_then_finishes(self):
        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [("a.wav", 0.5, 0.4, 0.0)])
            sink = CapturingSink()
            finished = []
            player = TakePlayer(samplerate=RATE, blocksize=256, sink=sink,
                                on_finished=lambda: finished.append(True))
            player.load(take)
            player.play()
        self.assertTrue(sink.started)
        # captured audio should be non-silent for the first ~0.5s
        mixed = sink.mixed()
        self.assertGreater(np.abs(mixed[:RATE // 4]).max(), 0.1)
        self.assertTrue(finished)


class TestMixing(unittest.TestCase):
    def _render(self, take, mutate=None, blocksize=256):
        sink = CapturingSink()
        player = TakePlayer(samplerate=RATE, blocksize=blocksize, sink=sink)
        player.load(take)
        if mutate:
            mutate(player)
        player.play()
        return player, sink.mixed()

    def test_two_tracks_sum(self):
        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [
                ("a.wav", 0.5, 0.3, 0.0),
                ("b.wav", 0.5, 0.3, 0.0),
            ])
            _, mixed = self._render(take)
        # two +0.3 constants sum to ~0.6 early in the take
        self.assertAlmostEqual(float(mixed[100]), 0.6, delta=0.05)

    def test_mute_removes_a_track(self):
        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [
                ("a.wav", 0.5, 0.3, 0.0),
                ("b.wav", 0.5, 0.3, 0.0),
            ])
            _, mixed = self._render(
                take, mutate=lambda p: p.set_muted(1, True))
        self.assertAlmostEqual(float(mixed[100]), 0.3, delta=0.05)

    def test_solo_isolates(self):
        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [
                ("a.wav", 0.5, 0.3, 0.0),
                ("b.wav", 0.5, 0.2, 0.0),
            ])
            _, mixed = self._render(
                take, mutate=lambda p: p.set_solo(1, True))
        # only track b (0.2) audible
        self.assertAlmostEqual(float(mixed[100]), 0.2, delta=0.05)

    def test_gain_scales(self):
        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [("a.wav", 0.5, 0.4, 0.0)])
            _, mixed = self._render(
                take, mutate=lambda p: p.set_gain(0, 0.5))
        self.assertAlmostEqual(float(mixed[100]), 0.2, delta=0.05)

    def test_offset_track_is_silent_before_start(self):
        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [("late.wav", 0.5, 0.4, 0.25)])
            _, mixed = self._render(take)
        # first 0.25s (2000 frames @ 8k) should be silent
        self.assertLess(np.abs(mixed[:1500]).max(), 0.01)
        # after the offset, audible
        self.assertGreater(np.abs(mixed[2200:2600]).max(), 0.1)

    def test_soft_clip_prevents_overflow(self):
        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [
                ("a.wav", 0.3, 0.9, 0.0),
                ("b.wav", 0.3, 0.9, 0.0),
                ("c.wav", 0.3, 0.9, 0.0),
            ])
            _, mixed = self._render(take)
        self.assertLessEqual(float(np.abs(mixed).max()), 1.0 + 1e-6)


class TestSeekAndLevels(unittest.TestCase):
    def test_seek_sets_position(self):
        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [("a.wav", 2.0, 0.3, 0.0)])
            player = TakePlayer(samplerate=RATE, blocksize=256,
                                sink=CapturingSink())
            player.load(take)
            player.seek(1.0)
        self.assertAlmostEqual(player.position_s, 1.0, places=2)

    def test_seek_clamped_to_duration(self):
        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [("a.wav", 1.0, 0.3, 0.0)])
            player = TakePlayer(samplerate=RATE, blocksize=256,
                                sink=CapturingSink())
            player.load(take)
            player.seek(999.0)
        self.assertLessEqual(player.position_s, player.duration_s + 0.01)

    def test_level_callback_fires(self):
        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [("a.wav", 0.3, 0.5, 0.0)])
            seen = {}
            player = TakePlayer(
                samplerate=RATE, blocksize=256, sink=CapturingSink(),
                on_levels=lambda lv: seen.update(lv),
            )
            player.load(take)
            player.play()
        self.assertIn(0, seen)
        self.assertGreater(seen[0], 0.0)


class TestMissingFilesAreGraceful(unittest.TestCase):
    def test_missing_track_file_plays_silence(self):
        with tempfile.TemporaryDirectory() as d:
            take = _Take(tracks=[_Track(
                path=Path(d) / "gone.wav", name="gone",
                offset_s=0.0, duration_s=0.5,
            )])
            # loader records duration 0 (no probe); force a total so pull runs
            player = TakePlayer(samplerate=RATE, blocksize=256,
                                sink=CapturingSink())
            player.load(take)
            player._total_frames = int(0.3 * RATE)
            player.play()  # must not raise

    def test_playback_open_failure_is_actionable_and_resets_state(self):
        class _FailingSink:
            def start(self, *_args):
                raise OSError("device busy")

            def stop(self):
                pass

        with tempfile.TemporaryDirectory() as d:
            take = _take_from(d, [("a.wav", 0.1, 0.2, 0.0)])
            player = TakePlayer(samplerate=RATE, sink=_FailingSink())
            player.load(take)
            with self.assertRaisesRegex(PlaybackError, "device busy"):
                player.play()
        self.assertFalse(player.is_playing)


class TestSoundDeviceStereoSink(unittest.TestCase):
    def test_duplicates_mono_mix_to_both_output_channels(self):
        captured = {}

        class _Stream:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def start(self):
                out = np.zeros((4, 2), dtype=np.float32)
                captured["callback"](out, 4, None, None)
                captured["out"] = out

            def stop(self):
                pass

            def close(self):
                pass

        fake = SimpleNamespace(OutputStream=lambda **kwargs: _Stream(**kwargs))
        with patch.dict(sys.modules, {"sounddevice": fake}):
            sink = SoundDeviceSink("SSL 2+")
            sink.start(48000, 128, lambda frames: np.arange(frames, dtype=np.float32))
            sink.stop()
        self.assertEqual(captured["channels"], 2)
        self.assertEqual(captured["device"], "SSL 2+")
        np.testing.assert_array_equal(captured["out"][:, 0], captured["out"][:, 1])


if __name__ == "__main__":
    unittest.main()
