from __future__ import annotations

from pathlib import Path

from tools import art_companion_simulator_check as simulator_check


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_retry_is_allowed_for_known_background_assertion_flake(tmp_path):
    log = tmp_path / "phone.log"
    _write(log, "error: Failed to get background assertion for target app with pid 123")

    assert simulator_check._should_retry_simulator_run(exit_code=65, log_path=log) is True


def test_retry_is_not_allowed_when_marker_is_absent(tmp_path):
    log = tmp_path / "phone.log"
    _write(log, "error: deterministic assertion failure in test body")

    assert simulator_check._should_retry_simulator_run(exit_code=65, log_path=log) is False


def test_retry_is_not_allowed_when_exit_code_is_success(tmp_path):
    log = tmp_path / "phone.log"
    _write(log, simulator_check.TRANSIENT_BACKGROUND_ASSERTION)

    assert simulator_check._should_retry_simulator_run(exit_code=0, log_path=log) is False
