from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import build_webjam


class TestBuildPromptHelpers(unittest.TestCase):
    def test_wait_for_enter_handles_eof(self):
        with patch("builtins.input", side_effect=EOFError):
            build_webjam._wait_for_enter("prompt")

    def test_prompt_yes_no_defaults_on_eof(self):
        with patch("builtins.input", side_effect=EOFError):
            self.assertFalse(build_webjam._prompt_yes_no("prompt", default=False))
        with patch("builtins.input", side_effect=EOFError):
            self.assertTrue(build_webjam._prompt_yes_no("prompt", default=True))

    def test_prompt_yes_no_parses_values(self):
        with patch("builtins.input", return_value="yes"):
            self.assertTrue(build_webjam._prompt_yes_no("prompt", default=False))
        with patch("builtins.input", return_value=""):
            self.assertTrue(build_webjam._prompt_yes_no("prompt", default=True))


class TestBuildMainNonInteractive(unittest.TestCase):
    def test_main_non_interactive_uses_defaults_without_crashing(self):
        with (
            patch("builtins.input", side_effect=[EOFError(), EOFError()]),
            patch.object(build_webjam, "check_pyinstaller", return_value=True),
            patch.object(build_webjam, "build_app", return_value=True),
            patch.object(build_webjam, "build_installer", return_value=True),
            patch.object(build_webjam, "create_distribution", return_value=True),
            patch.object(Path, "exists", return_value=False),
            patch.object(build_webjam.shutil, "rmtree"),
            patch.object(build_webjam.shutil, "make_archive") as make_archive,
        ):
            build_webjam.main()
        make_archive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
