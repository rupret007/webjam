"""Tests for ``webjam_qt.controllers.mix_manager.MixManager``.

Exercises the save/load/restore round-trip plus failure modes (missing
file, corrupted JSON) and the silent best-effort ``auto_restore`` path.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from webjam_qt.controllers.mix_manager import MixManager


class _TempHome:
    """Context manager that points $HOME at a fresh temp dir."""

    def __enter__(self):
        self._old_home = os.environ.get("HOME", "")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["HOME"] = self._tmp.name
        return Path(self._tmp.name)

    def __exit__(self, *exc):
        if self._old_home:
            os.environ["HOME"] = self._old_home
        else:
            os.environ.pop("HOME", None)
        self._tmp.cleanup()
        return False


def _make_mix_manager(serialize_payload=None, apply_side_effect=None):
    """Build a MixManager with a mocked Jamulus controller and flash recorder.

    Returns ``(manager, jamulus_mock, flash_calls_list)``.  ``flash_calls_list``
    is a list of ``(text, ms)`` tuples appended to as the manager calls the
    flash callback.
    """
    jamulus = mock.MagicMock()
    jamulus.serialize_mix.return_value = (
        serialize_payload if serialize_payload is not None else {"participants": []}
    )
    if apply_side_effect is not None:
        jamulus.apply_mix_data.side_effect = apply_side_effect

    flashes: list[tuple[str, int]] = []

    def _flash(text: str, ms: int) -> None:
        flashes.append((text, ms))

    return MixManager(jamulus, _flash), jamulus, flashes


class TestMixManagerSave(unittest.TestCase):
    def test_save_writes_atomic_json(self):
        """save() should write a parseable JSON file at ~/.webjam_mix.json."""
        with _TempHome() as home:
            payload = {"participants": [{"channel_id": 0, "fader_level": 100}]}
            manager, jamulus, flashes = _make_mix_manager(serialize_payload=payload)

            manager.save()

            mix_path = home / ".webjam_mix.json"
            self.assertTrue(mix_path.exists(), "save() should create the mix file")
            on_disk = json.loads(mix_path.read_text())
            self.assertEqual(on_disk, payload)
            jamulus.serialize_mix.assert_called_once()
            # Confirmation flash fires
            self.assertEqual(len(flashes), 1)
            self.assertIn("saved", flashes[0][0].lower())


class TestMixManagerLoad(unittest.TestCase):
    def test_load_with_no_file_flashes_hint(self):
        """No mix file → load() returns False and flashes the 'no saved mix' hint."""
        with _TempHome():
            manager, jamulus, flashes = _make_mix_manager()

            result = manager.load()

            self.assertFalse(result)
            jamulus.apply_mix_data.assert_not_called()
            self.assertEqual(len(flashes), 1)
            self.assertIn("No saved mix", flashes[0][0])

    def test_load_with_corrupt_json_flashes_corruption_message(self):
        """Corrupt JSON → load() returns False and flash mentions 'corrupted'."""
        with _TempHome() as home:
            (home / ".webjam_mix.json").write_text("{not json", encoding="utf-8")
            manager, jamulus, flashes = _make_mix_manager()

            result = manager.load()

            self.assertFalse(result)
            jamulus.apply_mix_data.assert_not_called()
            self.assertEqual(len(flashes), 1)
            self.assertIn("corrupted", flashes[0][0].lower())

    def test_load_success_applies_payload_and_returns_true(self):
        """A valid mix file should be parsed, applied, and reported via flash."""
        with _TempHome() as home:
            payload = {"participants": [{"channel_id": 1, "fader_level": 80}]}
            (home / ".webjam_mix.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            manager, jamulus, flashes = _make_mix_manager()

            result = manager.load()

            self.assertTrue(result)
            jamulus.apply_mix_data.assert_called_once_with(payload)
            self.assertEqual(len(flashes), 1)
            self.assertIn("loaded", flashes[0][0].lower())


class TestMixManagerSaveResult(unittest.TestCase):
    """save()/save_to() must report success/failure so the caller can decide
    whether to clear its dirty flag (the shutdown auto-save safety net)."""

    def test_save_returns_true_on_success(self):
        with _TempHome():
            manager, _jamulus, _flashes = _make_mix_manager()
            self.assertTrue(manager.save())

    def test_save_returns_false_when_write_fails(self):
        with _TempHome():
            manager, _jamulus, flashes = _make_mix_manager()
            with mock.patch(
                "webjam_qt.controllers.mix_manager.atomic_write_text",
                side_effect=OSError("disk full"),
            ):
                result = manager.save()
            self.assertFalse(result, "save() must report failure on write error")
            # User still gets a flash explaining the failure.
            self.assertTrue(flashes)
            self.assertIn("couldn't save", flashes[-1][0].lower())

    def test_save_to_returns_true_on_success(self):
        with _TempHome() as home:
            manager, _jamulus, _flashes = _make_mix_manager()
            self.assertTrue(manager.save_to(home / "song1.json"))

    def test_save_to_returns_false_when_write_fails(self):
        with _TempHome() as home:
            manager, _jamulus, _flashes = _make_mix_manager()
            with mock.patch(
                "webjam_qt.controllers.mix_manager.atomic_write_text",
                side_effect=OSError("permission denied"),
            ):
                self.assertFalse(manager.save_to(home / "song1.json"))


class TestMixManagerAutoRestore(unittest.TestCase):
    def test_auto_restore_silent_on_missing_file(self):
        """No mix file → auto_restore() returns without flashing anything."""
        with _TempHome():
            manager, jamulus, flashes = _make_mix_manager()

            manager.auto_restore()

            jamulus.apply_mix_data.assert_not_called()
            self.assertEqual(flashes, [])

    def test_auto_restore_silent_on_corrupt_file(self):
        """Corrupt file → auto_restore swallows the error and never flashes."""
        with _TempHome() as home:
            (home / ".webjam_mix.json").write_text("{not json", encoding="utf-8")
            manager, jamulus, flashes = _make_mix_manager()

            manager.auto_restore()  # must not raise

            jamulus.apply_mix_data.assert_not_called()
            self.assertEqual(flashes, [])


if __name__ == "__main__":
    unittest.main()
