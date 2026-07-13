"""Supplemental host capture is atomic and failure-safe."""
from __future__ import annotations

import json
import os
import queue
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import soundfile as sf

from core.local_capture import (
    LocalCaptureError,
    LocalInputCapture,
    recover_stale_local_captures,
)


def _fake_sd(stream_factory=None):
    return SimpleNamespace(
        check_input_settings=lambda **_kwargs: None,
        InputStream=stream_factory or (lambda **kwargs: _FakeStream(**kwargs)),
    )


class _FakeStream:
    def __init__(self, *, callback, **_kwargs):
        self.callback = callback

    def start(self):
        block = np.column_stack((
            np.full(4800, 0.25, dtype="float32"),
            np.full(4800, -0.125, dtype="float32"),
        ))
        self.callback(block, len(block), None, "")

    def stop(self):
        return None

    def abort(self):
        return None

    def close(self):
        return None


class TestLocalInputCapture(TestCase):
    def test_writes_two_atomic_48k_mono_stems(self):
        fake_sd = SimpleNamespace(
            check_input_settings=lambda **_kwargs: None,
            InputStream=lambda **kwargs: _FakeStream(**kwargs),
            query_devices=lambda device, kind: {
                "name": "Test Interface",
                "hostapi": 0,
            },
            query_hostapis=lambda _index: {"name": "Test Audio"},
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": fake_sd}
        ):
            root = Path(d)
            capture = LocalInputCapture(root, samplerate=48000)
            capture.start()
            take = root / "take"
            result = capture.stop_into(take)
            paths = [take / "host-guitar.wav", take / "host-vocal.wav"]
            info = [sf.info(str(path)) for path in paths]
            take_mode = os.stat(take).st_mode & 0o777
            file_modes = [os.stat(path).st_mode & 0o777 for path in paths]
            leftovers = list(root.glob(".webjam-capture-*"))
        self.assertEqual(list(result.files), paths)
        self.assertFalse(result.errors)
        self.assertTrue(all(item.samplerate == 48000 for item in info))
        self.assertTrue(all(item.channels == 1 for item in info))
        self.assertEqual(take_mode, 0o700)
        self.assertEqual(file_modes, [0o600, 0o600])
        self.assertEqual(result.capture_device.display_name, "Test Interface")
        self.assertEqual(result.capture_device.backend, "Test Audio")
        self.assertEqual(result.capture_device.channel_indices, (0, 1))
        self.assertFalse(leftovers)

    def test_rejects_non_48k_without_creating_capture_folder(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            capture = LocalInputCapture(root, samplerate=44100)
            with self.assertRaises(LocalCaptureError):
                capture.start()
            self.assertFalse(list(root.glob(".webjam-capture-*")))

    def test_device_open_failure_cleans_partial_files(self):
        fake_sd = SimpleNamespace(
            check_input_settings=lambda **_kwargs: (_ for _ in ()).throw(
                ValueError("device busy")
            ),
            InputStream=lambda **kwargs: _FakeStream(**kwargs),
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": fake_sd}
        ):
            root = Path(d)
            capture = LocalInputCapture(root)
            with self.assertRaisesRegex(LocalCaptureError, "device busy"):
                capture.start()
            leftovers = list(root.glob(".webjam-capture-*"))
        self.assertFalse(leftovers)

    def test_disk_writes_happen_on_writer_thread_not_audio_callback(self):
        """The audio callback must only enqueue; every disk write belongs to
        the dedicated writer thread."""
        write_threads: list[str] = []

        class _RecordingWriter:
            def __init__(self, path, **_kwargs):
                self._path = Path(str(path))
                self._path.touch()

            def write(self, _data):
                write_threads.append(threading.current_thread().name)

            def flush(self):
                pass

            def close(self):
                pass

        fake_sf = SimpleNamespace(
            SoundFile=lambda path, **kwargs: _RecordingWriter(path, **kwargs)
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": _fake_sd(), "soundfile": fake_sf}
        ):
            capture = LocalInputCapture(Path(d), samplerate=48000)
            capture.start()
            result = capture.stop_into(Path(d) / "take")
        self.assertFalse(result.errors)
        self.assertTrue(write_threads)
        self.assertTrue(
            all(name == "local-capture-writer" for name in write_threads),
            write_threads,
        )

    def test_repeated_device_status_flags_stay_bounded(self):
        """A sustained overflow condition must not grow the error list one
        entry per audio block — the manifest embeds these strings."""

        class _SpammyStream(_FakeStream):
            def start(self):
                block = np.zeros((480, 2), dtype="float32")
                for _ in range(1000):
                    self.callback(block, len(block), None, "input overflow")

        fake_sd = _fake_sd(lambda **kwargs: _SpammyStream(**kwargs))
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": fake_sd}
        ):
            capture = LocalInputCapture(Path(d), samplerate=48000)
            capture.start()
            result = capture.stop_into(Path(d) / "take")
        self.assertLessEqual(len(result.errors), 21)
        self.assertTrue(any(
            "input overflow" in e and "×1000" in e for e in result.errors
        ), result.errors)

    def test_queue_overflow_drops_blocks_with_counted_error(self):
        release = threading.Event()
        write_started = threading.Event()

        class _BlockingWriter:
            def __init__(self, path, **_kwargs):
                Path(str(path)).touch()

            def write(self, _data):
                write_started.set()
                release.wait(timeout=10)

            def flush(self):
                pass

            def close(self):
                pass

        class _FloodingStream(_FakeStream):
            def start(self):
                block = np.zeros((64, 2), dtype="float32")
                for _ in range(600):
                    self.callback(block, len(block), None, "")

        fake_sf = SimpleNamespace(
            SoundFile=lambda path, **kwargs: _BlockingWriter(path, **kwargs)
        )
        fake_sd = _fake_sd(lambda **kwargs: _FloodingStream(**kwargs))
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": fake_sd, "soundfile": fake_sf}
        ):
            capture = LocalInputCapture(Path(d), samplerate=48000)
            capture.start()
            write_started.wait(timeout=10)
            release.set()
            result = capture.stop_into(Path(d) / "take")
        self.assertTrue(any(
            "blocks were dropped" in e for e in result.errors
        ), result.errors)

    def test_overflow_gaps_preserve_absolute_timeline_and_silence(self):
        """Separated mid-stream drops become exact zero intervals; later
        blocks must retain their original absolute frame positions."""
        first_started = threading.Event()
        release_first = threading.Event()
        second_written = threading.Event()
        fourth_started = threading.Event()
        release_fourth = threading.Event()
        fifth_written = threading.Event()

        class _GatedSoundFile:
            def __init__(self, path, **kwargs):
                self._guitar = "guitar" in str(path)
                self._inner = sf.SoundFile(str(path), **kwargs)

            def write(self, data):
                value = float(data[0]) if len(data) else 0.0
                if self._guitar and np.allclose(value, 0.1):
                    first_started.set()
                    if not release_first.wait(timeout=2):
                        raise RuntimeError("test did not release first block")
                if self._guitar and np.allclose(value, 0.3):
                    fourth_started.set()
                    if not release_fourth.wait(timeout=2):
                        raise RuntimeError("test did not release fourth block")
                self._inner.write(data)
                if self._guitar and np.allclose(value, 0.2):
                    second_written.set()
                if self._guitar and np.allclose(value, 0.4):
                    fifth_written.set()

            def tell(self):
                return self._inner.tell()

            def flush(self):
                self._inner.flush()

            def close(self):
                self._inner.close()

        class _SeparatedOverflowStream(_FakeStream):
            def _send(self, value):
                block = np.column_stack((
                    np.full(4, value, dtype="float32"),
                    np.full(4, -value, dtype="float32"),
                ))
                self.callback(block, len(block), None, "")

            def start(self):
                self._send(0.1)
                if not first_started.wait(timeout=2):
                    raise RuntimeError("writer did not start first block")
                self._send(0.2)  # queued
                self._send(0.9)  # dropped: frames 8..12
                release_first.set()
                if not second_written.wait(timeout=2):
                    raise RuntimeError("writer did not drain second block")

                self._send(0.3)
                if not fourth_started.wait(timeout=2):
                    raise RuntimeError("writer did not start fourth block")
                self._send(0.4)  # queued
                self._send(0.8)  # dropped: frames 20..24
                release_fourth.set()
                if not fifth_written.wait(timeout=2):
                    raise RuntimeError("writer did not drain fifth block")
                self._send(0.5)

        fake_sf = SimpleNamespace(
            SoundFile=lambda path, **kwargs: _GatedSoundFile(path, **kwargs)
        )
        fake_sd = _fake_sd(lambda **kwargs: _SeparatedOverflowStream(**kwargs))
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": fake_sd, "soundfile": fake_sf}
        ):
            root = Path(d)
            capture = LocalInputCapture(root, samplerate=48000)
            capture._queue = queue.Queue(maxsize=1)
            capture.start()
            result = capture.stop_into(root / "take")
            guitar, guitar_rate = sf.read(str(result.files[0]), dtype="float32")
            vocal, vocal_rate = sf.read(str(result.files[1]), dtype="float32")

        expected = np.concatenate((
            np.full(4, 0.1),
            np.full(4, 0.2),
            np.zeros(4),
            np.full(4, 0.3),
            np.full(4, 0.4),
            np.zeros(4),
            np.full(4, 0.5),
        ))
        self.assertEqual((guitar_rate, vocal_rate), (48000, 48000))
        self.assertEqual((len(guitar), len(vocal)), (28, 28))
        self.assertEqual(result.total_frames, 28)
        self.assertEqual(result.gap_count, 2)
        self.assertEqual(
            [
                (gap.start_frame, gap.frame_count, gap.channels, gap.reason)
                for gap in result.gaps
            ],
            [
                (8, 4, (0, 1), "queue_overflow"),
                (20, 4, (0, 1), "queue_overflow"),
            ],
        )
        np.testing.assert_allclose(guitar, expected, atol=2e-6)
        np.testing.assert_allclose(vocal, -expected, atol=2e-6)

    def test_write_failure_inserts_exact_silence_for_affected_stem(self):
        """A transient channel write error must not shift later audio."""

        class _FailOnceSoundFile:
            def __init__(self, path, **kwargs):
                self._guitar = "guitar" in str(path)
                self._failed = False
                self._inner = sf.SoundFile(str(path), **kwargs)

            def write(self, data):
                if (
                    self._guitar
                    and not self._failed
                    and len(data)
                    and np.allclose(float(data[0]), 0.2)
                ):
                    self._failed = True
                    raise OSError("transient test write failure")
                self._inner.write(data)

            def tell(self):
                return self._inner.tell()

            def flush(self):
                self._inner.flush()

            def close(self):
                self._inner.close()

        class _ThreeBlockStream(_FakeStream):
            def start(self):
                for value in (0.1, 0.2, 0.3):
                    block = np.column_stack((
                        np.full(4, value, dtype="float32"),
                        np.full(4, -value, dtype="float32"),
                    ))
                    self.callback(block, len(block), None, "")

        fake_sf = SimpleNamespace(
            SoundFile=lambda path, **kwargs: _FailOnceSoundFile(path, **kwargs)
        )
        fake_sd = _fake_sd(lambda **kwargs: _ThreeBlockStream(**kwargs))
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": fake_sd, "soundfile": fake_sf}
        ):
            root = Path(d)
            capture = LocalInputCapture(root, samplerate=48000)
            capture.start()
            result = capture.stop_into(root / "take")
            guitar, _ = sf.read(str(result.files[0]), dtype="float32")
            vocal, _ = sf.read(str(result.files[1]), dtype="float32")

        self.assertEqual(result.total_frames, 12)
        self.assertEqual(len(guitar), 12)
        self.assertEqual(len(vocal), 12)
        self.assertEqual(
            [
                (gap.start_frame, gap.frame_count, gap.channels, gap.reason)
                for gap in result.gaps
            ],
            [(4, 4, (0,), "write_failure")],
        )
        np.testing.assert_allclose(
            guitar,
            np.concatenate((np.full(4, 0.1), np.zeros(4), np.full(4, 0.3))),
            atol=2e-6,
        )
        np.testing.assert_allclose(
            vocal,
            np.concatenate((
                np.full(4, -0.1),
                np.full(4, -0.2),
                np.full(4, -0.3),
            )),
            atol=2e-6,
        )
        self.assertTrue(any(
            "transient test write failure" in error for error in result.errors
        ), result.errors)

    def test_writer_timeout_retains_open_parts_without_taking_ownership(self):
        """A timed-out finalizer cannot flush, close, or move a handle that
        remains inside the writer thread; a later retry may finish safely."""
        write_started = threading.Event()
        release_write = threading.Event()
        ownership_violations: list[str] = []
        flush_calls: list[str] = []
        close_calls: list[str] = []

        class _SlowWriter:
            def __init__(self, path, **_kwargs):
                self.path = Path(str(path))
                self.path.touch()
                self.in_write = False
                self.position = 0
                self.is_guitar = "guitar" in self.path.name

            def write(self, data):
                self.in_write = True
                try:
                    if self.is_guitar and not write_started.is_set():
                        write_started.set()
                        release_write.wait(timeout=2)
                    self.position += len(data)
                finally:
                    self.in_write = False

            def tell(self):
                return self.position

            def flush(self):
                if self.in_write:
                    ownership_violations.append("flush during write")
                flush_calls.append(self.path.name)

            def close(self):
                if self.in_write:
                    ownership_violations.append("close during write")
                close_calls.append(self.path.name)

        fake_sf = SimpleNamespace(
            SoundFile=lambda path, **kwargs: _SlowWriter(path, **kwargs)
        )
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": _fake_sd(), "soundfile": fake_sf}
        ), patch("core.local_capture._WRITER_JOIN_TIMEOUT_S", 0.02):
            root = Path(d)
            capture = LocalInputCapture(root, samplerate=48000)
            capture.start()
            self.assertTrue(write_started.wait(timeout=1))
            timed_out = capture.stop_into(root / "take")

            self.assertFalse(timed_out.files)
            self.assertIsNotNone(timed_out.recovery_dir)
            self.assertEqual(timed_out.total_frames, 4800)
            self.assertFalse(flush_calls)
            self.assertFalse(close_calls)
            self.assertFalse(ownership_violations)
            self.assertFalse((root / "take").exists())
            self.assertEqual(
                len(list(timed_out.recovery_dir.glob("*.part"))),  # type: ignore[union-attr]
                2,
            )
            self.assertTrue(any(
                "were not flushed, closed, or moved" in error
                for error in timed_out.errors
            ), timed_out.errors)

            release_write.set()
            capture._writer_thread.join(timeout=1)
            self.assertFalse(capture._writer_thread.is_alive())
            retried = capture.stop_into(root / "retry-take")

        self.assertEqual(len(retried.files), 2)
        self.assertEqual(len(flush_calls), 2)
        self.assertEqual(len(close_calls), 2)
        self.assertFalse(ownership_violations)

    def test_never_overwrites_existing_take_file(self):
        """A server track named like a local stem must not be clobbered."""
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": _fake_sd()}
        ):
            root = Path(d)
            take = root / "take"
            take.mkdir()
            existing = take / "host-guitar.wav"
            existing.write_bytes(b"server audio, do not touch")
            capture = LocalInputCapture(root, samplerate=48000)
            capture.start()
            result = capture.stop_into(take)
            existing_bytes = existing.read_bytes()
        self.assertEqual(existing_bytes, b"server audio, do not touch")
        attached = {p.name for p in result.files}
        self.assertIn("host-guitar-local.wav", attached)
        self.assertTrue(any("attached as" in e for e in result.errors))
        from core.take_library import is_local_stem_name
        self.assertTrue(is_local_stem_name("host-guitar-local.wav"))

    def test_permission_hardening_failure_never_hides_finalized_audio(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": _fake_sd()}
        ):
            root = Path(d)
            capture = LocalInputCapture(root, samplerate=48000)
            capture.start()
            take = root / "take"
            with patch(
                "core.local_capture.os.chmod",
                side_effect=OSError("simulated ACL failure"),
            ):
                result = capture.stop_into(take)
            files_exist = all(path.is_file() for path in result.files)

        self.assertEqual(len(result.files), 2)
        self.assertTrue(files_exist)
        self.assertTrue(
            any("Could not protect isolated stem" in error for error in result.errors),
            result.errors,
        )

    def test_failed_attach_preserves_audio_in_recovery_folder(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": _fake_sd()}
        ):
            root = Path(d)
            take = root / "take"
            take.mkdir()
            capture = LocalInputCapture(root, samplerate=48000)
            capture.start()
            os.chmod(take, 0o500)  # attach renames will fail
            try:
                result = capture.stop_into(take)
            finally:
                os.chmod(take, 0o700)
            recovered = list(root.glob("Recovered-local-*"))
            recovered_parts = (
                list(recovered[0].glob("*.recovered-partial.wav"))
                if recovered
                else []
            )
        self.assertTrue(any("Could not attach" in e for e in result.errors))
        self.assertTrue(any("preserved" in e for e in result.errors), result.errors)
        self.assertEqual(len(recovered), 1, "temp audio must be kept, not deleted")
        self.assertEqual(len(recovered_parts), 2)

    def test_startup_promotes_abandoned_parts_to_visible_playable_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            hidden = root / ".webjam-capture-abandoned"
            hidden.mkdir(mode=0o700)
            guitar = hidden / "host-guitar.wav.part"
            vocal = hidden / "host-vocal.wav.part"
            sf.write(
                guitar,
                np.full(480, 0.2, dtype="float32"),
                48000,
                format="WAV",
                subtype="PCM_24",
            )
            sf.write(
                vocal,
                np.full(480, -0.1, dtype="float32"),
                48000,
                format="WAV",
                subtype="PCM_24",
            )
            (hidden / "webjam-local-capture.json").write_text(
                '{"schema": 1, "pid": 99999999, "started_utc": '
                '"2026-07-13T00:00:00Z", "sample_rate": 48000}',
                encoding="utf-8",
            )

            recovered = recover_stale_local_captures(root, minimum_age_s=0)

            self.assertEqual(len(recovered), 1)
            item = recovered[0]
            self.assertFalse(hidden.exists())
            self.assertTrue(item.recovery_dir.name.startswith("Recovered-local-"))
            self.assertEqual(len(item.files), 2)
            self.assertEqual(os.stat(item.recovery_dir).st_mode & 0o777, 0o700)
            self.assertTrue(
                all(os.stat(path).st_mode & 0o777 == 0o600 for path in item.files)
            )
            self.assertTrue(
                all(path.name.endswith(".recovered-partial.wav") for path in item.files)
            )
            self.assertTrue(all(sf.info(path).frames == 480 for path in item.files))
            report = item.recovery_dir / "RECOVERY.json"
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "recovered_partial")
            self.assertEqual(payload["reason"], "startup_recovery")
            self.assertEqual(os.stat(report).st_mode & 0o777, 0o600)

    def test_startup_recovery_never_touches_a_live_writer_or_follows_symlink(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            active = root / ".webjam-capture-active"
            active.mkdir()
            (active / "webjam-local-capture.json").write_text(
                f'{{"schema": 1, "pid": {os.getpid()}}}', encoding="utf-8"
            )
            active_part = active / "host-guitar.wav.part"
            active_part.write_bytes(b"live")
            self.assertFalse(
                recover_stale_local_captures(root, minimum_age_s=0)
            )
            self.assertEqual(active_part.read_bytes(), b"live")

            # Once the checkpoint no longer names a live process, a malicious
            # or corrupt symlink is moved with the folder but never opened.
            outside = root / "outside.wav"
            outside.write_bytes(b"do not read or change")
            (active / "webjam-local-capture.json").write_text(
                '{"schema": 1, "pid": 99999999}', encoding="utf-8"
            )
            active_part.unlink()
            active_part.symlink_to(outside)
            recovered = recover_stale_local_captures(root, minimum_age_s=0)
            self.assertEqual(len(recovered), 1)
            self.assertEqual(outside.read_bytes(), b"do not read or change")
            self.assertFalse(recovered[0].files)
            self.assertTrue(any("unsafe" in error for error in recovered[0].errors))

    def test_stop_into_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(
            sys.modules, {"sounddevice": _fake_sd()}
        ):
            root = Path(d)
            capture = LocalInputCapture(root, samplerate=48000)
            capture.start()
            first = capture.stop_into(root / "take")
            second = capture.stop_into(root / "other")
            capture.abort()  # also a no-op after finalization
            guitar_kept = (root / "take" / "host-guitar.wav").is_file()
        self.assertEqual(len(first.files), 2)
        self.assertFalse(second.files)
        self.assertTrue(any("already finalized" in e for e in second.errors))
        self.assertTrue(guitar_kept)
