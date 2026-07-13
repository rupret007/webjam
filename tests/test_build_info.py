from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.build_info import build_id


def test_explicit_build_id_is_validated_and_normalized() -> None:
    with patch.dict("os.environ", {"WEBJAM_BUILD_ID": "ABCDEF123"}):
        assert build_id() == "abcdef123"


def test_invalid_explicit_value_never_enters_diagnostics() -> None:
    result = SimpleNamespace(returncode=1, stdout="", stderr="")
    with patch.dict("os.environ", {"WEBJAM_BUILD_ID": "/Users/person/private"}), patch(
        "core.build_info.Path.read_text", side_effect=OSError
    ), patch("core.build_info.subprocess.run", return_value=result):
        assert build_id() == ""


def test_source_checkout_reports_only_exact_git_head() -> None:
    result = SimpleNamespace(returncode=0, stdout="a" * 40 + "\n", stderr="")
    with patch.dict("os.environ", {}, clear=True), patch(
        "core.build_info.Path.read_text", side_effect=OSError
    ), patch("core.build_info.subprocess.run", return_value=result) as run:
        assert build_id() == "a" * 40
    assert run.call_args.args[0] == ["git", "rev-parse", "HEAD"]
    assert isinstance(run.call_args.kwargs["cwd"], Path)


def test_frozen_build_ignores_environment_and_uses_bundled_provenance() -> None:
    with patch.dict("os.environ", {"WEBJAM_BUILD_ID": "b" * 40}), patch(
        "core.build_info.Path.read_text", return_value="a" * 40 + "\n"
    ), patch("core.build_info.sys.frozen", True, create=True), patch(
        "core.build_info.subprocess.run"
    ) as run:
        assert build_id() == "a" * 40
    run.assert_not_called()


def test_frozen_build_fails_closed_without_bundled_provenance() -> None:
    with patch.dict("os.environ", {"WEBJAM_BUILD_ID": "b" * 40}), patch(
        "core.build_info.Path.read_text", side_effect=OSError
    ), patch("core.build_info.sys.frozen", True, create=True):
        assert build_id() == ""
