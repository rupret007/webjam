"""Take library — discovery + .lof offset parsing."""
from __future__ import annotations

import struct
import json
import tempfile
import unittest
import wave
from pathlib import Path

from core.take_library import (
    discover_takes,
    find_changed_take,
    load_take,
    parse_lof_offsets,
    snapshot_take_directories,
    validate_take,
    write_take_manifest,
    estimate_local_alignment,
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


class TestTakeValidation(unittest.TestCase):
    def test_reports_expected_track_shortfall_and_silence(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "guitar.wav", seconds=0.1)
            result = validate_take(take, expected_tracks=2)
        self.assertFalse(result.ok)
        self.assertIn("Expected at least 2", result.errors[0])
        self.assertTrue(any("silent" in warning for warning in result.warnings))

    def test_reports_mixed_samplerates(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "one.wav", seconds=0.1, rate=48000)
            _write_wav(take / "two.wav", seconds=0.1, rate=44100)
            result = validate_take(take)
        self.assertFalse(result.ok)
        self.assertTrue(any("sample rates" in error for error in result.errors))

    def test_rejects_single_non_48k_track(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "one.wav", seconds=0.1, rate=44100)
            result = validate_take(take)
        self.assertFalse(result.ok)
        self.assertTrue(any("48 kHz" in error for error in result.errors))

    def test_manifest_classifies_local_stems_and_blocks_silent_alignment(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "server-host.wav", seconds=0.1)
            _write_wav(take / "host-guitar.wav", seconds=0.1)
            _write_wav(take / "host-vocal.wav", seconds=0.1)
            result = write_take_manifest(
                take, expected_tracks=1, required_local_stems=2,
                app_version="test",
            )
            manifest = json.loads((take / "webjam-take.json").read_text())
            loaded = load_take(take)
        self.assertFalse(result.ok)
        self.assertEqual(manifest["status"], "needs_attention")
        self.assertTrue(any("aligned confidently" in e for e in result.errors))
        self.assertEqual(
            len([track for track in loaded.tracks if track.source == "local_ssl"]), 2
        )
        self.assertEqual(loaded.validation_status, "needs_attention")

    def test_manifest_does_not_store_capture_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "server.wav", seconds=0.1)
            write_take_manifest(take, expected_tracks=1, required_local_stems=0)
            text = (take / "webjam-take.json").read_text()
        self.assertNotIn("secret", text.lower())

    def test_alignment_recovers_known_server_delay(self):
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            rate = 48000
            rng = np.random.default_rng(7)
            guitar = np.zeros(rate * 5, dtype="float32")
            guitar[rate:rate * 3] = rng.normal(0, 0.1, rate * 2)
            vocal = np.zeros_like(guitar)
            server = np.concatenate((np.zeros(rate // 2, dtype="float32"), guitar))
            sf.write(take / "host-guitar.wav", guitar, rate)
            sf.write(take / "host-vocal.wav", vocal, rate)
            sf.write(take / "server-host.wav", server, rate)
            offset, confidence = estimate_local_alignment(take)
        self.assertAlmostEqual(offset, 0.5, delta=0.02)
        self.assertGreater(confidence, 0.9)

    def test_alignment_recovers_negative_offset_when_local_leads(self):
        """Local capture arms before the server recorder starts, so the local
        stems normally lead the server take: the offset must come back
        negative, not clamped to zero."""
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            rate = 48000
            rng = np.random.default_rng(11)
            performance = rng.normal(0, 0.1, rate * 3).astype("float32")
            lead_in = int(rate * 0.75)
            guitar = np.concatenate(
                (np.zeros(lead_in, dtype="float32"), performance)
            )
            vocal = np.zeros_like(guitar)
            sf.write(take / "host-guitar.wav", guitar, rate)
            sf.write(take / "host-vocal.wav", vocal, rate)
            sf.write(take / "server-host.wav", performance, rate)
            offset, confidence = estimate_local_alignment(take)
        self.assertAlmostEqual(offset, -0.75, delta=0.02)
        self.assertGreater(confidence, 0.9)

    def test_alignment_is_sample_accurate_off_the_envelope_grid(self):
        """An offset that is not a multiple of the 10 ms envelope block must
        still be recovered to within a millisecond by the refinement pass."""
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            rate = 48000
            rng = np.random.default_rng(3)
            guitar = np.zeros(rate * 5, dtype="float32")
            guitar[rate:rate * 3] = rng.normal(0, 0.1, rate * 2)
            vocal = np.zeros_like(guitar)
            delay = 24_137  # ≈0.5028 s: deliberately off the 480-sample grid
            server = np.concatenate((np.zeros(delay, dtype="float32"), guitar))
            sf.write(take / "host-guitar.wav", guitar, rate)
            sf.write(take / "host-vocal.wav", vocal, rate)
            sf.write(take / "server-host.wav", server, rate)
            offset, confidence = estimate_local_alignment(take)
        self.assertAlmostEqual(offset, delay / rate, delta=0.001)
        self.assertGreater(confidence, 0.9)

    def test_alignment_reports_low_confidence_for_unrelated_audio(self):
        """Uncorrelated program material must not manufacture confidence."""
        import numpy as np
        import soundfile as sf

        from core.take_library import ALIGNMENT_CONFIDENCE_MIN

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            rate = 48000
            rng = np.random.default_rng(5)
            guitar = rng.normal(0, 0.1, rate * 5).astype("float32")
            vocal = np.zeros_like(guitar)
            unrelated = rng.normal(0, 0.1, rate * 5).astype("float32")
            sf.write(take / "host-guitar.wav", guitar, rate)
            sf.write(take / "host-vocal.wav", vocal, rate)
            sf.write(take / "server-host.wav", unrelated, rate)
            _offset, confidence = estimate_local_alignment(take)
        self.assertLess(confidence, ALIGNMENT_CONFIDENCE_MIN)

    def test_alignment_handles_stereo_server_track(self):
        import numpy as np
        import soundfile as sf

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            rate = 48000
            rng = np.random.default_rng(9)
            guitar = np.zeros(rate * 5, dtype="float32")
            guitar[rate:rate * 3] = rng.normal(0, 0.1, rate * 2)
            vocal = np.zeros_like(guitar)
            delayed = np.concatenate((np.zeros(rate // 4, dtype="float32"), guitar))
            stereo = np.stack((delayed, delayed), axis=1)
            sf.write(take / "host-guitar.wav", guitar, rate)
            sf.write(take / "host-vocal.wav", vocal, rate)
            sf.write(take / "server-host.wav", stereo, rate)
            offset, confidence = estimate_local_alignment(take)
        self.assertAlmostEqual(offset, 0.25, delta=0.02)
        self.assertGreater(confidence, 0.9)

    def test_load_take_round_trips_negative_manifest_offset(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "host-guitar.wav", seconds=0.1)
            _write_wav(take / "server-host.wav", seconds=0.1)
            (take / "webjam-take.json").write_text(json.dumps({
                "schema_version": 1,
                "status": "complete",
                "tracks": [
                    {"filename": "host-guitar.wav", "source": "local_ssl",
                     "offset_s": -1.25},
                    {"filename": "server-host.wav",
                     "source": "jamulus_server", "offset_s": None},
                ],
            }), encoding="utf-8")
            loaded = load_take(take)
        stem = next(t for t in loaded.tracks if t.source == "local_ssl")
        self.assertAlmostEqual(stem.offset_s, -1.25)

    def test_snapshot_finds_new_take(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            before = snapshot_take_directories(root)
            new = root / "new"
            new.mkdir()
            _write_wav(new / "track.wav", seconds=0.1)
            self.assertEqual(find_changed_take(root, before), new)


if __name__ == "__main__":
    unittest.main()
