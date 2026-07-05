"""Take library — discovery + .lof offset parsing."""
from __future__ import annotations

import struct
import tempfile
import unittest
import wave
from pathlib import Path

from core.take_library import (
    discover_takes,
    load_take,
    parse_lof_offsets,
)


def _write_wav(path: Path, seconds: float = 1.0, rate: int = 48000):
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % frames, *([0] * frames)))


class TestLofParsing(unittest.TestCase):
    def test_parses_files_and_offsets(self):
        with tempfile.TemporaryDirectory() as d:
            lof = Path(d) / "take.lof"
            lof.write_text(
                'file "guitar.wav" offset 0\n'
                'file "bass.wav" offset 3.5\n'
                'file "vocals.wav"\n',
                encoding="utf-8",
            )
            offsets = parse_lof_offsets(lof)
        self.assertEqual(offsets["guitar.wav"], 0.0)
        self.assertEqual(offsets["bass.wav"], 3.5)
        self.assertEqual(offsets["vocals.wav"], 0.0)

    def test_basename_keying_and_bad_lines(self):
        with tempfile.TemporaryDirectory() as d:
            lof = Path(d) / "take.lof"
            lof.write_text(
                'file "/srv/recordings/x/drums.wav" offset 1.25\n'
                'garbage line\n'
                'file "keys.wav" offset notanumber\n',
                encoding="utf-8",
            )
            offsets = parse_lof_offsets(lof)
        self.assertEqual(offsets["drums.wav"], 1.25)
        self.assertEqual(offsets.get("keys.wav"), 0.0)  # bad offset -> 0
        self.assertNotIn("garbage", offsets)

    def test_missing_file_returns_empty(self):
        self.assertEqual(parse_lof_offsets(Path("/nonexistent.lof")), {})


class TestLoadTake(unittest.TestCase):
    def test_builds_tracks_with_offsets_and_durations(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "2026-07-05_take1"
            take.mkdir()
            _write_wav(take / "guitar.wav", seconds=2.0)
            _write_wav(take / "bass_guitar.wav", seconds=1.0)
            (take / "take.lof").write_text(
                'file "guitar.wav" offset 0\n'
                'file "bass_guitar.wav" offset 1.5\n',
                encoding="utf-8",
            )
            (take / "take.rpp").write_text("<REAPER_PROJECT>\n", encoding="utf-8")
            info = load_take(take)

        self.assertIsNotNone(info)
        self.assertEqual(info.track_count, 2)
        self.assertIsNotNone(info.reaper_project)
        by_name = {t.name: t for t in info.tracks}
        self.assertIn("Guitar", by_name)
        self.assertIn("Bass Guitar", by_name)  # prettified from bass_guitar
        self.assertAlmostEqual(by_name["Bass Guitar"].offset_s, 1.5)
        self.assertAlmostEqual(by_name["Guitar"].duration_s, 2.0, places=1)
        # take duration = latest end: bass starts 1.5, lasts 1.0 -> 2.5;
        # guitar 0..2.0 -> 2.0; max = 2.5
        self.assertAlmostEqual(info.duration_s, 2.5, places=1)

    def test_folder_without_audio_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            empty = Path(d) / "notes_only"
            empty.mkdir()
            (empty / "readme.txt").write_text("hi", encoding="utf-8")
            self.assertIsNone(load_take(empty))

    def test_no_lof_defaults_offsets_zero(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "a.wav")
            info = load_take(take)
        self.assertEqual(info.tracks[0].offset_s, 0.0)


class TestDiscoverTakes(unittest.TestCase):
    def test_discovers_multiple_and_sorts_newest_first(self):
        import os
        import time
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old = root / "old_take"
            new = root / "new_take"
            old.mkdir()
            new.mkdir()
            _write_wav(old / "g.wav")
            _write_wav(new / "g.wav")
            past = time.time() - 1000
            os.utime(old, (past, past))
            takes = discover_takes(root)
        names = [t.name for t in takes]
        self.assertIn("old_take", names)
        self.assertIn("new_take", names)
        self.assertEqual(names[0], "new_take")  # newest first

    def test_missing_root_is_empty(self):
        self.assertEqual(discover_takes("/nonexistent/path/xyz"), [])

    def test_root_itself_as_single_take(self):
        with tempfile.TemporaryDirectory() as d:
            _write_wav(Path(d) / "solo.wav")
            takes = discover_takes(d)
        self.assertEqual(len(takes), 1)
        self.assertEqual(takes[0].track_count, 1)


if __name__ == "__main__":
    unittest.main()
