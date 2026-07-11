"""Supplemental host capture is atomic and failure-safe."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import soundfile as sf

from core.local_capture import LocalCaptureError, LocalInputCapture


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
            leftovers = list(root.glob(".webjam-capture-*"))
        self.assertEqual(list(result.files), paths)
        self.assertFalse(result.errors)
        self.assertTrue(all(item.samplerate == 48000 for item in info))
        self.assertTrue(all(item.channels == 1 for item in info))
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
                list(recovered[0].glob("*.part")) if recovered else []
            )
        self.assertTrue(any("Could not attach" in e for e in result.errors))
        self.assertTrue(any("preserved" in e for e in result.errors), result.errors)
        self.assertEqual(len(recovered), 1, "temp audio must be kept, not deleted")
        self.assertEqual(len(recovered_parts), 2)

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
