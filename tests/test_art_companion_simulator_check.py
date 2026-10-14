from __future__ import annotations

from pathlib import Path

from tools import art_companion_simulator_check as simulator_check

ROOT = Path(__file__).resolve().parents[1]
UITESTS = ROOT / "ios/ArtCompanionUITests/ArtCompanionUITests.swift"


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


def _nudge_form_source() -> str:
    source = UITESTS.read_text(encoding="utf-8")
    marker = "func nudgeForm("
    assert marker in source, "Art companion UITests must keep nudgeForm"
    return source.split(marker, 1)[1].split("\n    @MainActor", 1)[0]


def test_nudge_form_page_swipes_when_software_keyboard_is_down():
    """Done stays in the keyboard-toolbar hierarchy after dismiss and on room
    screens that never showed a keyboard. Scrolling must not treat Done.exists
    as 'keyboard up' or AccessibilityXXXL on iPhone SE only gets tiny drags.
    """

    nudge = _nudge_form_source()
    assert "softwareKeyboardUp(app)" in nudge
    assert 'buttons["Done"].exists' not in nudge
    assert "swipeUp()" in nudge
    assert "swipeDown()" in nudge


def test_software_keyboard_up_is_the_keyboard_element():
    source = UITESTS.read_text(encoding="utf-8")
    marker = "func softwareKeyboardUp("
    assert marker in source
    body = source.split(marker, 1)[1].split("\n    @MainActor", 1)[0]
    assert "keyboards.element.exists" in body
    assert 'buttons["Done"]' not in body
