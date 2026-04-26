"""Tests for ``MixManager.save_to`` / ``MixManager.load_from``.

Verifies the multi-slot named-mix API: explicit-path save/load, plus
the same failure modes as the default-slot variants (missing file,
corrupted JSON).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webjam_qt.controllers.mix_manager import MixManager


def _make_mix_manager(serialize_payload=None):
    """Build a MixManager with a mocked Jamulus controller and flash recorder.

    Mirrors the helper in ``test_mix_manager.py`` — returns
    ``(manager, jamulus_mock, flash_calls_list)``.
    """
    jamulus = mock.MagicMock()
    jamulus.serialize_mix.return_value = (
        serialize_payload if serialize_payload is not None else {"participants": []}
    )

    flashes: list[tuple[str, int]] = []

    def _flash(text: str, ms: int) -> None:
        flashes.append((text, ms))

    return MixManager(jamulus, _flash), jamulus, flashes


class TestSaveTo(unittest.TestCase):
    def test_save_to_writes_to_explicit_path(self):
        """save_to(path) writes parseable JSON at the requested path."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rad_dad_song1.json"
            payload = {"participants": [{"channel_id": 0, "fader_level": 110}]}
            manager, jamulus, flashes = _make_mix_manager(serialize_payload=payload)

            manager.save_to(target)

            self.assertTrue(target.exists(), "save_to should create the file")
            on_disk = json.loads(target.read_text())
            self.assertEqual(on_disk, payload)
            jamulus.serialize_mix.assert_called_once()
            self.assertEqual(len(flashes), 1)
            text, _ms = flashes[0]
            self.assertIn("saved", text.lower())
            self.assertIn("rad_dad_song1.json", text)


class TestLoadFrom(unittest.TestCase):
    def test_load_from_applies_payload_from_explicit_path(self):
        """Valid file at an explicit path should be parsed and applied."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "stalemate_setup.json"
            payload = {"participants": [{"channel_id": 2, "fader_level": 95}]}
            target.write_text(json.dumps(payload), encoding="utf-8")
            manager, jamulus, flashes = _make_mix_manager()

            result = manager.load_from(target)

            self.assertTrue(result)
            jamulus.apply_mix_data.assert_called_once_with(payload)
            self.assertEqual(len(flashes), 1)
            self.assertIn("loaded", flashes[0][0].lower())

    def test_load_from_handles_missing_file(self):
        """Nonexistent path → returns False, flashes 'No saved mix' hint."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "does_not_exist.json"
            manager, jamulus, flashes = _make_mix_manager()

            result = manager.load_from(target)

            self.assertFalse(result)
            jamulus.apply_mix_data.assert_not_called()
            self.assertEqual(len(flashes), 1)
            self.assertIn("No saved mix", flashes[0][0])

    def test_load_from_handles_corrupt_json(self):
        """Garbage in the file → returns False, flash mentions 'corrupted'."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "broken.json"
            target.write_text("{not json at all", encoding="utf-8")
            manager, jamulus, flashes = _make_mix_manager()

            result = manager.load_from(target)

            self.assertFalse(result)
            jamulus.apply_mix_data.assert_not_called()
            self.assertEqual(len(flashes), 1)
            self.assertIn("corrupted", flashes[0][0].lower())


if __name__ == "__main__":
    unittest.main()
