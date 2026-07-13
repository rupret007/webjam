"""Take library — discovery + .lof offset parsing."""
from __future__ import annotations

import struct
import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace

from core.take_project import CaptureDevice

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

    def test_manifest_declared_missing_track_remains_visible_and_invalidates_take(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "host.wav", seconds=0.1)
            (take / "webjam-take.json").write_text(json.dumps({
                "schema_version": 1,
                "status": "complete",
                "tracks": [
                    {
                        "filename": "host.wav",
                        "name": "Host",
                        "source": "jamulus_server",
                    },
                    {
                        "filename": "guest.wav",
                        "name": "Guest",
                        "source": "jamulus_server",
                        "duration_s": 2.0,
                        "sample_rate": 48000,
                        "offset_s": 0.5,
                    },
                ],
            }), encoding="utf-8")

            info = load_take(take)

        self.assertIsNotNone(info)
        self.assertEqual([track.name for track in info.tracks], ["Host", "Guest"])
        guest = info.tracks[1]
        self.assertEqual(guest.path.name, "guest.wav")
        self.assertEqual(guest.media_status, "missing")
        self.assertEqual(guest.duration_s, 2.0)
        self.assertEqual(guest.samplerate, 48000)
        self.assertEqual(info.duration_s, 2.5)
        self.assertEqual(info.validation_status, "needs_attention")
        self.assertTrue(any("Guest is missing" in error for error in info.manifest_errors))


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
    def test_manifest_uses_live_musician_names_for_server_tracks(self):
        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "____-127_0_0_1_50000-0-1.wav", seconds=0.1)
            result = write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=0,
                participant_names={0: "Jeff — Guitar"},
                session_title="Sunday Rehearsal",
            )
        self.assertIsNotNone(result.take)
        self.assertEqual(result.take.tracks[0].name, "Jeff — Guitar")
        self.assertEqual(result.take.session_title, "Sunday Rehearsal")
        self.assertEqual(result.take.display_name, "Sunday Rehearsal")

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
            len([
                track for track in loaded.tracks
                if track.source == "local_isolated"
            ]),
            2,
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

    def test_final_manifest_v2_has_stable_ids_exact_media_hash_and_capture_gap(self):
        import hashlib
        import uuid

        with tempfile.TemporaryDirectory() as d:
            take = Path(d) / "take"
            take.mkdir()
            _write_wav(take / "host-guitar.wav", seconds=0.1)
            _write_wav(take / "host-vocal.wav", seconds=0.1)
            _write_wav(take / "Band-127_0_0_1_50000-0-1.wav", seconds=0.1)
            session_id = str(uuid.uuid4())
            take_id = str(uuid.uuid4())
            participant_id = str(uuid.uuid4())
            local_id = str(uuid.uuid4())
            gap = SimpleNamespace(
                start_frame=1200,
                frame_count=240,
                channels=(0,),
                reason="queue_overflow",
            )
            device = CaptureDevice(
                device_id="coreaudio:test-input",
                display_name="Test Input",
                backend="Core Audio",
                sample_rate=48000,
                channel_indices=(0, 1),
                channel_labels=("Guitar", "Vocal"),
            )

            write_take_manifest(
                take,
                expected_tracks=1,
                required_local_stems=2,
                session_id=session_id,
                take_id=take_id,
                participant_names={0: "Jeff"},
                participant_ids={0: participant_id},
                local_participant_id=local_id,
                local_participant_name="Jeff",
                capture_device=device,
                capture_gaps=(gap,),
                local_total_frames=4800,
            )
            data = json.loads((take / "webjam-take.json").read_text())
            expected_guitar_size = (take / "host-guitar.wav").stat().st_size
            expected_guitar_hash = hashlib.sha256(
                (take / "host-guitar.wav").read_bytes()
            ).hexdigest()

        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["session_id"], session_id)
        self.assertEqual(data["take_id"], take_id)
        self.assertEqual(len({item["participant_id"] for item in data["participants"]}), 2)
        self.assertEqual(data["devices"][0]["device_id"], device.device_id)
        server = next(item for item in data["tracks"] if item["source"] == "jamulus_server")
        guitar = next(item for item in data["tracks"] if item["filename"] == "host-guitar.wav")
        vocal = next(item for item in data["tracks"] if item["filename"] == "host-vocal.wav")
        self.assertEqual(server["participant_id"], participant_id)
        self.assertEqual(guitar["participant_id"], local_id)
        self.assertEqual(vocal["participant_id"], local_id)
        segment = guitar["segments"][0]
        self.assertEqual(segment["frame_count"], 4800)
        self.assertEqual(segment["sample_rate"], 48000)
        self.assertEqual(segment["channels"], 1)
        self.assertEqual(segment["sample_format"], "PCM_16")
        self.assertEqual(segment["size_bytes"], expected_guitar_size)
        self.assertEqual(segment["sha256"], expected_guitar_hash)
        self.assertEqual(
            segment["gaps"],
            [{
                "start_frame": 1200,
                "frame_count": 240,
                "reason": "queue_overflow",
                "channels": [0],
            }],
        )
        self.assertEqual(vocal["segments"][0]["gaps"], [])

    def test_load_schema2_retains_nested_reconnect_segments_rates_and_gaps(self):
        import hashlib

        import numpy as np
        import soundfile as sf

        from core.take_project import (
            AlignmentState,
            GapInterval,
            MediaSegment,
            MediaStatus,
            Participant,
            ProjectStatus,
            ProjectTrack,
            SourceQuality,
            SourceType,
            TakeProject,
            new_project_id,
            write_take_project,
        )

        with tempfile.TemporaryDirectory() as d:
            take_dir = Path(d) / "take"
            nested = take_dir / "transferred-isolated"
            nested.mkdir(parents=True)
            first = nested / "first.wav"
            second = nested / "second.wav"
            sf.write(first, np.full(4410, 0.2, dtype="float32"), 44100)
            sf.write(second, np.full(4800, 0.3, dtype="float32"), 48000)
            participant_id = new_project_id()
            segments = (
                MediaSegment(
                    new_project_id(),
                    "transferred-isolated/first.wav",
                    4800,
                    4410,
                    44100,
                    1,
                    "PCM_16",
                    sha256=hashlib.sha256(first.read_bytes()).hexdigest(),
                    size_bytes=first.stat().st_size,
                    gaps=(GapInterval(100, 50, "network interruption"),),
                ),
                MediaSegment(
                    new_project_id(),
                    "transferred-isolated/second.wav",
                    14400,
                    4800,
                    48000,
                    1,
                    "PCM_16",
                    sha256=hashlib.sha256(second.read_bytes()).hexdigest(),
                    size_bytes=second.stat().st_size,
                ),
            )
            track = ProjectTrack(
                new_project_id(),
                new_project_id(),
                participant_id,
                "Guest Guitar",
                "Guitar",
                SourceType.LOCAL_ISOLATED,
                SourceQuality.VERIFIED_ISOLATED,
                MediaStatus.AVAILABLE,
                0,
                segments,
                AlignmentState(
                    automatic_offset_s=0.01,
                    drift_ppm=500.0,
                    confidence=0.9,
                    method="test-alignment",
                ),
            )
            project = TakeProject(
                new_project_id(),
                new_project_id(),
                "Session",
                "Take",
                ProjectStatus.COMPLETE,
                48000,
                (Participant(participant_id, "Guest", "Guitar"),),
                (track,),
            )
            write_take_project(take_dir, project)
            loaded = load_take(take_dir)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.project_samplerate, 48000)
        self.assertEqual(len(loaded.tracks), 1)
        loaded_track = loaded.tracks[0]
        self.assertEqual(len(loaded_track.segments), 2)
        self.assertEqual(
            [segment.samplerate for segment in loaded_track.segments],
            [44100, 48000],
        )
        self.assertEqual(loaded_track.segments[1].project_start_frame, 14400)
        self.assertEqual(
            loaded_track.segments[0].gaps,
            ((100, 50, (), "network interruption"),),
        )
        self.assertEqual(loaded_track.offset_s, 0.01)
        self.assertEqual(loaded_track.drift_ppm, 500.0)
        self.assertEqual(loaded_track.alignment_method, "test-alignment")
        self.assertAlmostEqual(loaded_track.duration_s, 0.40005, places=4)

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
