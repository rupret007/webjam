"""Supplemental host capture is atomic and failure-safe."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import soundfile as sf

from core.local_capture import LocalCaptureError, LocalInputCapture


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
