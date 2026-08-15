"""Supplemental host capture is atomic and failure-safe."""

from __future__ import annotations

import builtins
from contextlib import ExitStack
import json
import logging
import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import soundfile as sf

import core.local_capture as local_capture
from core.local_capture import (
    LocalCaptureError,
    LocalCaptureTrack,
    LocalInputCapture,
    local_capture_track_map_fingerprint,
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
        block = np.column_stack(
            (
                np.full(4800, 0.25, dtype="float32"),
                np.full(4800, -0.125, dtype="float32"),
            )
        )
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
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": fake_sd}),
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

    def test_durable_checkpoint_binds_capture_to_take_and_session(self):
        """A live capture publishes only fsynced frames as crash-recoverable."""

        class _OneSecondStream(_FakeStream):
            def start(self):
                block = np.column_stack(
                    (
                        np.full(4_800, 0.25, dtype="float32"),
                        np.full(4_800, -0.125, dtype="float32"),
                    )
                )
                for _ in range(10):
                    self.callback(block, len(block), None, "")

        take_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(
                sys.modules,
                {"sounddevice": _fake_sd(lambda **kwargs: _OneSecondStream(**kwargs))},
            ),
        ):
            root = Path(d)
            capture = LocalInputCapture(
                root,
                samplerate=48_000,
                take_id=take_id,
                session_id=session_id,
            )
            capture.start()
            checkpoint = next(root.glob(".webjam-capture-*/webjam-local-capture.json"))
            deadline = time.time() + 2
            payload: dict = {}
            while time.time() < deadline:
                payload = json.loads(checkpoint.read_text(encoding="utf-8"))
                if payload.get("durable_frames", 0) >= 48_000:
                    break
                time.sleep(0.01)
            result = capture.stop_into(root / "take")

        self.assertFalse(result.errors)
        self.assertEqual(payload["take_id"], take_id)
        self.assertEqual(payload["session_id"], session_id)
        self.assertEqual(payload["durable_frames"], 48_000)
        self.assertEqual(payload["writer_frames"], [48_000, 48_000])
        self.assertEqual(payload["schema"], 2)
        self.assertEqual(
            payload["tracks"],
            [
                {"source_channels": [0], "stem": "host-guitar"},
                {"source_channels": [1], "stem": "host-vocal"},
            ],
        )

    def test_device_open_failure_cleans_partial_files(self):
        private_detail = "/Users/private-musician/Secret Interface"
        fake_sd = SimpleNamespace(
            check_input_settings=lambda **_kwargs: (_ for _ in ()).throw(
                ValueError(f"device busy at {private_detail}")
            ),
            InputStream=lambda **kwargs: _FakeStream(**kwargs),
        )
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": fake_sd}),
        ):
            root = Path(d)
            capture = LocalInputCapture(root)
            with self.assertRaises(LocalCaptureError) as raised:
                capture.start()
            leftovers = list(root.glob(".webjam-capture-*"))
        message = str(raised.exception)
        self.assertFalse(leftovers)
        self.assertIn("Check the selected input", message)
        self.assertNotIn("device busy", message)
        self.assertNotIn(private_detail, message)

    def test_disk_writes_happen_on_writer_thread_not_audio_callback(self):
        """The callback only hands off; every disk write belongs to the writer."""
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
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": _fake_sd(), "soundfile": fake_sf}),
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

    def test_callback_hot_path_uses_only_the_preallocated_ring(self):
        """Callback ingress must not allocate a block, lock, wait, log, or do I/O."""

        class _ManualStream(_FakeStream):
            def start(self):
                return None

        class _PositionWriter:
            def __init__(self, path, **_kwargs):
                self._path = Path(str(path))
                self._path.touch()
                self._position = 0

            def write(self, data):
                self._position += len(data)

            def tell(self):
                return self._position

            def flush(self):
                return None

            def close(self):
                return None

        class _NoCopyArray(np.ndarray):
            def copy(self, *_args, **_kwargs):
                raise AssertionError("callback attempted to allocate a block copy")

        class _Status:
            input_overflow = True

            def __bool__(self):
                return True

            def __str__(self):
                raise AssertionError("callback attempted to format device status")

        class _ForbiddenLock:
            def __enter__(self):
                raise AssertionError("callback attempted to acquire a lock")

            def __exit__(self, *_args):
                return None

        def forbidden(*_args, **_kwargs):
            raise AssertionError("forbidden operation reached the audio callback")

        fake_sd = _fake_sd(lambda **kwargs: _ManualStream(**kwargs))
        fake_sf = SimpleNamespace(
            SoundFile=lambda path, **kwargs: _PositionWriter(path, **kwargs)
        )
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(
                sys.modules,
                {"sounddevice": fake_sd, "soundfile": fake_sf},
            ),
        ):
            root = Path(d)
            capture = LocalInputCapture(root, samplerate=48_000, blocksize=8)
            capture.start()
            callback = capture._stream.callback
            source = np.ones((8, 2), dtype=np.float32).view(_NoCopyArray)
            prior_diagnostics_lock = capture._diagnostics_lock
            prior_finalize_lock = capture._finalize_lock
            capture._diagnostics_lock = _ForbiddenLock()
            capture._finalize_lock = _ForbiddenLock()
            try:
                import queue as queue_module

                with ExitStack() as hot:
                    for name in (
                        "array",
                        "asarray",
                        "concatenate",
                        "empty",
                        "ones",
                        "stack",
                        "zeros",
                    ):
                        hot.enter_context(
                            patch.object(local_capture.np, name, forbidden)
                        )
                    for name in ("open", "read", "write"):
                        hot.enter_context(
                            patch.object(local_capture.os, name, forbidden)
                        )
                    for name in ("lstat", "open"):
                        hot.enter_context(patch.object(Path, name, forbidden))
                    hot.enter_context(patch.object(builtins, "open", forbidden))
                    hot.enter_context(patch.object(logging.Logger, "_log", forbidden))
                    hot.enter_context(patch.object(threading.Event, "wait", forbidden))
                    hot.enter_context(patch.object(threading.Event, "set", forbidden))
                    hot.enter_context(
                        patch.object(queue_module.Queue, "put_nowait", forbidden)
                    )
                    callback(source, 8, None, _Status())
            finally:
                capture._diagnostics_lock = prior_diagnostics_lock
                capture._finalize_lock = prior_finalize_lock
            result = capture.stop_into(root / "take")

        self.assertEqual(result.total_frames, 8)
        self.assertEqual(len(result.files), 2)
        self.assertTrue(
            any("input overflow" in error for error in result.errors),
            result.errors,
        )

    def test_stale_callback_generation_cannot_append_after_stop(self):
        class _ManualStream(_FakeStream):
            def start(self):
                return None

        fake_sd = _fake_sd(lambda **kwargs: _ManualStream(**kwargs))
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(
                sys.modules,
                {"sounddevice": fake_sd},
            ),
        ):
            root = Path(d)
            capture = LocalInputCapture(root, samplerate=48_000, blocksize=8)
            capture.start()
            callback = capture._stream.callback
            source = np.ones((8, 2), dtype=np.float32)
            callback(source, 8, None, "")
            result = capture.stop_into(root / "take")
            completed_frames = capture._next_input_frame

            callback(source, 8, None, "")

            attached_frames = tuple(sf.info(path).frames for path in result.files)

        self.assertEqual(completed_frames, 8)
        self.assertEqual(capture._next_input_frame, 8)
        self.assertEqual(attached_frames, (8, 8))

    def test_repeated_device_status_flags_stay_bounded(self):
        """A sustained overflow condition must not grow the error list one
        entry per audio block — the manifest embeds these strings."""

        class _SpammyStream(_FakeStream):
            def start(self):
                block = np.zeros((480, 2), dtype="float32")
                status = SimpleNamespace(input_overflow=True)
                for _ in range(1000):
                    self.callback(block, len(block), None, status)

        fake_sd = _fake_sd(lambda **kwargs: _SpammyStream(**kwargs))
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": fake_sd}),
        ):
            capture = LocalInputCapture(Path(d), samplerate=48000)
            capture.start()
            result = capture.stop_into(Path(d) / "take")
        self.assertLessEqual(len(result.errors), 21)
        self.assertTrue(
            any("input overflow" in e and "×1000" in e for e in result.errors),
            result.errors,
        )

    def test_capture_ring_overflow_drops_blocks_with_counted_error(self):
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
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": fake_sd, "soundfile": fake_sf}),
        ):
            capture = LocalInputCapture(Path(d), samplerate=48000)
            capture.start()
            write_started.wait(timeout=10)
            release.set()
            result = capture.stop_into(Path(d) / "take")
        self.assertTrue(
            any("blocks were dropped" in e for e in result.errors), result.errors
        )

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
                block = np.column_stack(
                    (
                        np.full(4, value, dtype="float32"),
                        np.full(4, -value, dtype="float32"),
                    )
                )
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
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": fake_sd, "soundfile": fake_sf}),
        ):
            root = Path(d)
            capture = LocalInputCapture(root, samplerate=48000)
            capture._ring_capacity = 1
            capture.start()
            result = capture.stop_into(root / "take")
            guitar, guitar_rate = sf.read(str(result.files[0]), dtype="float32")
            vocal, vocal_rate = sf.read(str(result.files[1]), dtype="float32")

        expected = np.concatenate(
            (
                np.full(4, 0.1),
                np.full(4, 0.2),
                np.zeros(4),
                np.full(4, 0.3),
                np.full(4, 0.4),
                np.zeros(4),
                np.full(4, 0.5),
            )
        )
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
                    block = np.column_stack(
                        (
                            np.full(4, value, dtype="float32"),
                            np.full(4, -value, dtype="float32"),
                        )
                    )
                    self.callback(block, len(block), None, "")

        fake_sf = SimpleNamespace(
            SoundFile=lambda path, **kwargs: _FailOnceSoundFile(path, **kwargs)
        )
        fake_sd = _fake_sd(lambda **kwargs: _ThreeBlockStream(**kwargs))
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": fake_sd, "soundfile": fake_sf}),
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
            np.concatenate(
                (
                    np.full(4, -0.1),
                    np.full(4, -0.2),
                    np.full(4, -0.3),
                )
            ),
            atol=2e-6,
        )
        self.assertIn("Local capture write failed on channel 1.", result.errors)
        self.assertFalse(
            any("transient test write failure" in error for error in result.errors),
            result.errors,
        )

    def test_stereo_write_failure_inserts_two_channel_silence_without_shift(self):
        class _FailOnceStereoSoundFile:
            def __init__(self, path, **kwargs):
                self._failed = False
                self._inner = sf.SoundFile(str(path), **kwargs)

            def write(self, data):
                if (
                    not self._failed
                    and len(data)
                    and np.allclose(float(data[0, 0]), 0.2)
                ):
                    self._failed = True
                    raise OSError("private stereo writer detail")
                self._inner.write(data)

            def tell(self):
                return self._inner.tell()

            def flush(self):
                self._inner.flush()

            def close(self):
                self._inner.close()

        class _ThreeStereoBlockStream(_FakeStream):
            def start(self):
                for value in (0.1, 0.2, 0.3):
                    block = np.column_stack(
                        (
                            np.full(4, value, dtype="float32"),
                            np.full(4, -value, dtype="float32"),
                        )
                    )
                    self.callback(block, len(block), None, "")

        fake_sf = SimpleNamespace(
            SoundFile=lambda path, **kwargs: _FailOnceStereoSoundFile(path, **kwargs)
        )
        fake_sd = _fake_sd(lambda **kwargs: _ThreeStereoBlockStream(**kwargs))
        tracks = (LocalCaptureTrack("local-Room", (0, 1)),)
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": fake_sd, "soundfile": fake_sf}),
        ):
            root = Path(d)
            capture = LocalInputCapture(
                root,
                samplerate=48_000,
                tracks=tracks,
            )
            capture.start()
            result = capture.stop_into(root / "take")
            recovered, rate = sf.read(
                str(result.files[0]),
                dtype="float32",
                always_2d=True,
            )

        self.assertEqual(rate, 48_000)
        self.assertEqual(result.tracks, tracks)
        self.assertEqual(result.total_frames, 12)
        self.assertEqual(recovered.shape, (12, 2))
        self.assertEqual(
            [
                (gap.start_frame, gap.frame_count, gap.channels, gap.reason)
                for gap in result.gaps
            ],
            [(4, 4, (0,), "write_failure")],
        )
        np.testing.assert_allclose(
            recovered,
            np.column_stack(
                (
                    np.concatenate((np.full(4, 0.1), np.zeros(4), np.full(4, 0.3))),
                    np.concatenate((np.full(4, -0.1), np.zeros(4), np.full(4, -0.3))),
                )
            ),
            atol=2e-6,
        )
        self.assertFalse(
            any("private stereo writer detail" in item for item in result.errors)
        )

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
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": _fake_sd(), "soundfile": fake_sf}),
            patch("core.local_capture._WRITER_JOIN_TIMEOUT_S", 0.02),
        ):
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
            self.assertTrue(
                any(
                    "were not flushed, closed, or moved" in error
                    for error in timed_out.errors
                ),
                timed_out.errors,
            )

            release_write.set()
            capture._writer_thread.join(timeout=1)
            self.assertFalse(capture._writer_thread.is_alive())
            # Once the writer owns no active write, it safely flushes its own
            # final durable checkpoint before the caller retries attachment.
            self.assertEqual(len(flush_calls), 2)
            retried = capture.stop_into(root / "retry-take")

        self.assertEqual(len(retried.files), 2)
        self.assertEqual(len(flush_calls), 4)
        self.assertEqual(len(close_calls), 2)
        self.assertFalse(ownership_violations)

    def test_never_overwrites_existing_take_file(self):
        """A server track named like a local stem must not be clobbered."""
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": _fake_sd()}),
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
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": _fake_sd()}),
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
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": _fake_sd()}),
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
                list(recovered[0].glob("*.recovered-partial.wav")) if recovered else []
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

    def test_startup_recovery_preserves_opaque_ids_durable_frames_and_gaps(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            hidden = root / ".webjam-capture-linked"
            hidden.mkdir(mode=0o700)
            for name, value in (
                ("host-guitar.wav.part", 0.2),
                ("host-vocal.wav.part", -0.1),
            ):
                sf.write(
                    hidden / name,
                    np.full(480, value, dtype="float32"),
                    48_000,
                    format="WAV",
                    subtype="PCM_24",
                )
            take_id = str(uuid.uuid4())
            session_id = str(uuid.uuid4())
            (hidden / "webjam-local-capture.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "pid": 99999999,
                        "started_utc": "2026-07-14T00:00:00Z",
                        "sample_rate": 48_000,
                        "take_id": take_id,
                        "session_id": session_id,
                        "total_frames": 600,
                        "durable_frames": 480,
                        "tracks": [
                            {"stem": "host-guitar", "channel": 0},
                            {"stem": "host-vocal", "channel": 1},
                        ],
                        "gaps": [
                            {
                                "start_frame": 480,
                                "frame_count": 120,
                                "channels": [0, 1],
                                "reason": "queue_overflow",
                            }
                        ],
                        "capture_device": {
                            "device_id": "portaudio:test:0:Interface",
                            "display_name": "Interface",
                            "backend": "Test",
                            "sample_rate": 48_000,
                            "channel_indices": [0, 1],
                            "channel_labels": ["Input 1", "Input 2"],
                        },
                    }
                ),
                encoding="utf-8",
            )

            recovered = recover_stale_local_captures(root, minimum_age_s=0)

            self.assertEqual(len(recovered), 1)
            item = recovered[0]
            self.assertEqual(item.take_id, take_id)
            self.assertEqual(item.session_id, session_id)
            self.assertEqual(item.total_frames, 600)
            self.assertEqual(item.durable_frames, 480)
            self.assertEqual(item.sample_rate, 48_000)
            self.assertEqual(
                item.tracks,
                (
                    LocalCaptureTrack("host-guitar", (0,)),
                    LocalCaptureTrack("host-vocal", (1,)),
                ),
            )
            self.assertEqual(
                [
                    (gap.start_frame, gap.frame_count, gap.channels, gap.reason)
                    for gap in item.gaps
                ],
                [(480, 120, (0, 1), "queue_overflow")],
            )
            self.assertEqual(item.capture_device.display_name, "Interface")
            report = json.loads((item.recovery_dir / "RECOVERY.json").read_text())
            self.assertEqual(report["take_id"], take_id)
            self.assertEqual(report["durable_frames"], 480)
            self.assertEqual(report["schema"], 2)
            self.assertEqual(
                report["tracks"],
                [
                    {"source_channels": [0], "stem": "host-guitar"},
                    {"source_channels": [1], "stem": "host-vocal"},
                ],
            )

    def test_startup_recovery_round_trips_a_stereo_logical_channel_map(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            hidden = root / ".webjam-capture-stereo"
            hidden.mkdir(mode=0o700)
            stereo_part = hidden / "local-Room.wav.part"
            sf.write(
                stereo_part,
                np.column_stack(
                    (
                        np.full(480, 0.2, dtype="float32"),
                        np.full(480, -0.25, dtype="float32"),
                    )
                ),
                48_000,
                format="WAV",
                subtype="PCM_24",
            )
            (hidden / "webjam-local-capture.json").write_text(
                json.dumps(
                    {
                        "schema": 2,
                        "pid": 99999999,
                        "started_utc": "2026-08-15T00:00:00Z",
                        "sample_rate": 48_000,
                        "tracks": [
                            {
                                "stem": "local-Room",
                                "source_channels": [4, 5],
                            }
                        ],
                        "total_frames": 480,
                        "durable_frames": 480,
                    }
                ),
                encoding="utf-8",
            )

            recovered = recover_stale_local_captures(root, minimum_age_s=0)

            self.assertEqual(len(recovered), 1)
            item = recovered[0]
            self.assertEqual(
                item.tracks,
                (LocalCaptureTrack("local-Room", (4, 5)),),
            )
            self.assertEqual(len(item.files), 1)
            self.assertEqual(sf.info(item.files[0]).channels, 2)
            report = json.loads(
                (item.recovery_dir / "RECOVERY.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                report["tracks"],
                [
                    {
                        "source_channels": [4, 5],
                        "stem": "local-Room",
                    }
                ],
            )

    def test_visible_recovery_without_final_project_is_reoffered_for_reconciliation(
        self,
    ):
        """A crash after promotion must not leave playable PCM orphaned."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            recovered_dir = root / "Recovered-local-existing"
            recovered_dir.mkdir(mode=0o700)
            audio = recovered_dir / "host-guitar.recovered-partial.wav"
            sf.write(
                audio,
                np.full(480, 0.2, dtype="float32"),
                48_000,
                format="WAV",
                subtype="PCM_24",
            )
            take_id = str(uuid.uuid4())
            session_id = str(uuid.uuid4())
            (recovered_dir / "webjam-local-capture.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "take_id": take_id,
                        "session_id": session_id,
                        "started_utc": "2026-07-14T00:00:00Z",
                        "sample_rate": 48_000,
                        "total_frames": 600,
                        "durable_frames": 480,
                    }
                ),
                encoding="utf-8",
            )

            first = recover_stale_local_captures(root, minimum_age_s=0)
            self.assertEqual(len(first), 1)
            item = first[0]
            self.assertEqual(item.recovery_dir, recovered_dir)
            self.assertEqual(item.take_id, take_id)
            self.assertEqual(item.session_id, session_id)
            self.assertEqual(item.durable_frames, 480)
            self.assertEqual(item.files, (audio,))

            (recovered_dir / "webjam-take.json").write_text(
                json.dumps({"schema_version": 2}), encoding="utf-8"
            )
            self.assertFalse(recover_stale_local_captures(root, minimum_age_s=0))

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
            self.assertFalse(recover_stale_local_captures(root, minimum_age_s=0))
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
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": _fake_sd()}),
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


class TestConfigurableCaptureTracks(TestCase):
    def test_mapped_tracks_publish_named_stems_from_their_own_channels(self):
        class _FourChannelStream(_FakeStream):
            def start(self):
                block = np.column_stack(
                    (
                        np.full(4800, 0.1, dtype="float32"),
                        np.full(4800, 0.2, dtype="float32"),
                        np.full(4800, 0.3, dtype="float32"),
                        np.full(4800, 0.4, dtype="float32"),
                    )
                )
                self.callback(block, len(block), None, "")

        seen = {}

        def _check(**kwargs):
            seen.update(kwargs)

        fake_sd = SimpleNamespace(
            check_input_settings=_check,
            InputStream=lambda **kwargs: (
                seen.update(stream_channels=kwargs.get("channels"))
                or _FourChannelStream(**kwargs)
            ),
            query_devices=lambda device, kind: {"name": "Quad", "hostapi": 0},
            query_hostapis=lambda _index: {"name": "Test Audio"},
        )
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": fake_sd}),
        ):
            root = Path(d)
            capture = LocalInputCapture(
                root,
                samplerate=48000,
                tracks=(("bass-di", 2), ("room-mic", 0)),
            )
            capture.start()
            checkpoint = json.loads(
                next(
                    root.glob(".webjam-capture-*/webjam-local-capture.json")
                ).read_text(encoding="utf-8")
            )
            result = capture.stop_into(root / "take")
            audio = {path.name: sf.read(str(path))[0] for path in result.files}
        self.assertEqual(seen["channels"], 3)
        self.assertEqual(seen["stream_channels"], 3)
        self.assertEqual(sorted(audio), ["bass-di.wav", "room-mic.wav"])
        # Each stem carries its own mapped device channel, not channel 0/1.
        self.assertAlmostEqual(float(audio["bass-di.wav"][0]), 0.3, places=3)
        self.assertAlmostEqual(float(audio["room-mic.wav"][0]), 0.1, places=3)
        self.assertEqual(checkpoint["channels"], 3)
        self.assertEqual(
            checkpoint["tracks"],
            [
                {"source_channels": [2], "stem": "bass-di"},
                {"source_channels": [0], "stem": "room-mic"},
            ],
        )
        self.assertEqual(result.capture_device.channel_indices, (2, 0))
        self.assertFalse(result.errors)

    def test_typed_stereo_track_stays_one_true_two_channel_pcm24_wav(self):
        class _FourChannelStream(_FakeStream):
            def start(self):
                block = np.column_stack(
                    (
                        np.full(480, 0.1, dtype="float32"),
                        np.full(480, 0.2, dtype="float32"),
                        np.full(480, -0.3, dtype="float32"),
                        np.full(480, 0.4, dtype="float32"),
                    )
                )
                self.callback(block, len(block), None, "")

        fake_sd = SimpleNamespace(
            check_input_settings=lambda **_kwargs: None,
            InputStream=lambda **kwargs: _FourChannelStream(**kwargs),
            query_devices=lambda device, kind: {"name": "Quad", "hostapi": 0},
            query_hostapis=lambda _index: {"name": "Test Audio"},
        )
        tracks = (
            LocalCaptureTrack("local-Keys", (1, 2)),
            LocalCaptureTrack("local-Talkback", (3,)),
        )
        with (
            tempfile.TemporaryDirectory() as d,
            patch.dict(sys.modules, {"sounddevice": fake_sd}),
        ):
            root = Path(d)
            capture = LocalInputCapture(
                root,
                samplerate=48_000,
                tracks=tracks,
            )
            capture.start()
            checkpoint = json.loads(
                next(
                    root.glob(".webjam-capture-*/webjam-local-capture.json")
                ).read_text(encoding="utf-8")
            )
            result = capture.stop_into(root / "take")
            stereo, rate = sf.read(
                str(root / "take" / "local-Keys.wav"),
                dtype="float32",
                always_2d=True,
            )
            mono_info = sf.info(str(root / "take" / "local-Talkback.wav"))
            stereo_info = sf.info(str(root / "take" / "local-Keys.wav"))

        self.assertEqual(
            [path.name for path in result.files],
            [
                "local-Keys.wav",
                "local-Talkback.wav",
            ],
        )
        self.assertEqual(rate, 48_000)
        self.assertEqual(stereo.shape, (480, 2))
        np.testing.assert_allclose(stereo[:, 0], 0.2, atol=2e-6)
        np.testing.assert_allclose(stereo[:, 1], -0.3, atol=2e-6)
        self.assertEqual(stereo_info.channels, 2)
        self.assertEqual(stereo_info.subtype, "PCM_24")
        self.assertEqual(mono_info.channels, 1)
        self.assertEqual(mono_info.subtype, "PCM_24")
        self.assertEqual(result.capture_device.channel_indices, (1, 2, 3))
        self.assertEqual(
            checkpoint["tracks"],
            [
                {"source_channels": [1, 2], "stem": "local-Keys"},
                {"source_channels": [3], "stem": "local-Talkback"},
            ],
        )
        self.assertEqual(checkpoint["writer_frames"], [0, 0])
        self.assertEqual(result.tracks, tracks)
        self.assertFalse(result.errors)

    def test_accepts_32_logical_mono_tracks_and_rejects_the_33rd(self):
        tracks = tuple(
            LocalCaptureTrack(f"local-Track-{index + 1}", (index,))
            for index in range(32)
        )

        capture = LocalInputCapture(Path("."), samplerate=48_000, tracks=tracks)

        self.assertEqual(capture._tracks, tracks)
        self.assertEqual(capture._required_input_channels, 32)
        self.assertEqual(len(capture._track_channels), 32)
        with self.assertRaises(LocalCaptureError):
            LocalInputCapture(
                Path("."),
                samplerate=48_000,
                tracks=(*tracks, LocalCaptureTrack("local-Extra", (0,))),
            )

    def test_track_map_fingerprint_binds_topology_without_names(self):
        typed = (
            LocalCaptureTrack("private-Artist-Name", (0, 1)),
            LocalCaptureTrack("private-Microphone-Name", (3,)),
        )
        renamed_legacy = (
            ("anonymous-stereo", (0, 1)),
            ("anonymous-mono", 3),
        )

        fingerprint = local_capture_track_map_fingerprint(typed)

        self.assertRegex(fingerprint, r"^[0-9a-f]{64}$")
        self.assertEqual(hash(LocalCaptureTrack("mono", (3,))), hash(("mono", 3)))
        self.assertEqual(
            hash(LocalCaptureTrack("stereo", (0, 1))),
            hash(("stereo", (0, 1))),
        )
        self.assertEqual(
            fingerprint,
            local_capture_track_map_fingerprint(renamed_legacy),
        )
        self.assertNotEqual(
            fingerprint,
            local_capture_track_map_fingerprint(
                (
                    LocalCaptureTrack("private-Artist-Name", (0,)),
                    LocalCaptureTrack("private-Microphone-Name", (1, 2)),
                )
            ),
        )
        with self.assertRaises(LocalCaptureError):
            local_capture_track_map_fingerprint(
                (
                    LocalCaptureTrack("one", (0, 1)),
                    LocalCaptureTrack("overlap", (1,)),
                )
            )

    def test_default_tracks_remain_the_fixed_host_pair(self):
        capture = LocalInputCapture(Path("."), samplerate=48000)
        self.assertEqual(
            capture._tracks,
            (
                LocalCaptureTrack("host-guitar", (0,)),
                LocalCaptureTrack("host-vocal", (1,)),
            ),
        )
        self.assertEqual(capture._required_input_channels, 2)

    def test_invalid_track_specifications_fail_closed(self):
        hostile = (
            (),  # empty
            (("ok", 0),) * 33,  # too many
            (("../evil", 0),),  # unsafe stem
            (("name\n", 0),),
            (("", 0),),
            (("dup", 0), ("dup", 1)),  # duplicate stems
            (("a", 3), ("b", 3)),  # duplicate channels
            (("a", -1),),
            (("a", 64),),
            (("a", True),),
            (("a", "0"),),
            (
                LocalCaptureTrack("stereo-a", (0, 1)),
                LocalCaptureTrack("mono-overlap", (1,)),
            ),
        )
        for tracks in hostile:
            with self.assertRaises(LocalCaptureError, msg=repr(tracks)):
                LocalInputCapture(Path("."), samplerate=48000, tracks=tracks)

        with self.assertRaises(LocalCaptureError, msg="non-adjacent stereo"):
            LocalCaptureTrack("wide-pair", (0, 2))
        with self.assertRaises(LocalCaptureError, msg="too many channels"):
            LocalCaptureTrack("surround", (0, 1, 2))
        with self.assertRaises(LocalCaptureError, msg="casefold collision"):
            LocalInputCapture(
                Path("."),
                samplerate=48000,
                tracks=(
                    LocalCaptureTrack("Keys", (0,)),
                    LocalCaptureTrack("keys", (1,)),
                ),
            )
