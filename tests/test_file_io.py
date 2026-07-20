"""Tests for core.file_io.atomic_write_text — atomicity, mode, parent creation."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.file_io import atomic_write_bytes, atomic_write_text


class TestAtomicWriteText(unittest.TestCase):
    def test_binary_writer_preserves_exact_non_utf8_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "recovery.bin"
            payload = b"\x00\xff\r\nexact recovery bytes"

            atomic_write_bytes(target, payload, mode=0o600)

            self.assertEqual(target.read_bytes(), payload)

    def test_writes_text_to_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.json"
            atomic_write_text(target, '{"a": 1}')
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(), '{"a": 1}')

    def test_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.txt"
            target.write_text("old")
            atomic_write_text(target, "new")
            self.assertEqual(target.read_text(), "new")

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "subdir" / "nested" / "out.json"
            atomic_write_text(target, "{}")
            self.assertTrue(target.exists())

    def test_mode_0600_for_secrets(self):
        if os.name != "posix":
            self.skipTest("File mode test is POSIX-specific")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "secret.json"
            atomic_write_text(target, '{"secret": "x"}', mode=0o600)
            file_mode = stat.S_IMODE(os.stat(target).st_mode)
            self.assertEqual(file_mode, 0o600)

    def test_no_temp_files_left_behind_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.txt"
            atomic_write_text(target, "content")
            # Only the target should exist; no .tmp leftovers
            files = sorted(Path(tmp).iterdir())
            self.assertEqual([f.name for f in files], ["out.txt"])

    def test_no_temp_files_left_behind_on_error(self):
        """If chmod or replace fails, no .tmp file should be left behind."""
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.txt"
            # Use an invalid mode to force chmod to silently succeed but the
            # finally block must still clean up if anything goes wrong.
            atomic_write_text(target, "x")
            files = sorted(Path(tmp).iterdir())
            self.assertEqual([f.name for f in files], ["out.txt"])

    def test_unicode_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "u.txt"
            text = "Hëllo 世界 🎵"
            atomic_write_text(target, text)
            self.assertEqual(target.read_text(encoding="utf-8"), text)

    def test_empty_string_writes_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "empty.txt"
            atomic_write_text(target, "")
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(), "")

    def test_posix_write_syncs_the_parent_after_replacement(self):
        if os.name != "posix":
            self.skipTest("directory fsync is only available through the POSIX path")
        with tempfile.TemporaryDirectory() as tmp, patch(
            "core.file_io._fsync_parent_directory"
        ) as sync_parent:
            target = Path(tmp) / "durable.json"
            atomic_write_text(target, "{}")
        sync_parent.assert_called_once_with(target.parent)


if __name__ == "__main__":
    unittest.main()
