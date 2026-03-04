"""Tests for utils.installer_helpers."""

import platform
import tempfile
import unittest
from pathlib import Path

from utils.installer_helpers import find_jamulus, is_admin, vb_cable_present


class TestIsAdmin(unittest.TestCase):
    def test_non_windows_returns_false(self):
        if platform.system() != "Windows":
            self.assertFalse(is_admin())

    @unittest.skipUnless(platform.system() == "Windows", "Windows-only")
    def test_windows_returns_bool(self):
        self.assertIsInstance(is_admin(), bool)


class TestVbCablePresent(unittest.TestCase):
    def test_non_windows_returns_false(self):
        if platform.system() != "Windows":
            self.assertFalse(vb_cable_present())


class TestFindJamulus(unittest.TestCase):
    def test_empty_candidates(self):
        self.assertIsNone(find_jamulus([]))

    def test_no_match(self):
        self.assertIsNone(find_jamulus(["/nonexistent/path/jamulus"]))

    def test_first_match_wins(self):
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            path = f.name
        try:
            result = find_jamulus(["/nonexistent", path, "/also_missing"])
            self.assertEqual(result, path)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_single_valid_candidate(self):
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            path = f.name
        try:
            self.assertEqual(find_jamulus([path]), path)
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
