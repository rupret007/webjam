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


def _function_body(name: str) -> str:
    source = UITESTS.read_text(encoding="utf-8")
    marker = f"func {name}("
    assert marker in source, f"Art companion UITests must keep {name}"
    return source.split(marker, 1)[1].split("\n    @MainActor", 1)[0]


def _nudge_form_source() -> str:
    return _function_body("nudgeForm")


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


def test_nudge_form_page_swipes_only_when_the_target_is_far():
    """A reverse page swipe after a near miss bounced canvas-elsewhere off
    iPhone SE at AccessibilityXXXL (run 38610587063). Nearby targets must use
    a short drag, not swipeUp/swipeDown.
    """

    nudge = _nudge_form_source()
    assert "far: Bool" in nudge
    assert "else if far" in nudge
    assert "press(forDuration:" in nudge
    after_far = nudge.split("else if far", 1)[1]
    swipe_block, correction = after_far.split("Short correction", 1)
    assert "swipeUp()" in swipe_block
    assert "swipeDown()" in swipe_block
    assert "swipeUp()" not in correction
    assert "swipeDown()" not in correction


def test_reveal_does_not_page_swipe_a_nearby_or_zero_spin():
    """Zero frames used to `continue` without scrolling. Nearby frames must
    not use a page swipe; only far off-screen (or a zero frame below the
    fold) may.
    """

    body = _function_body("reveal")
    assert "offscreenDistance(target, from: visible)" in body
    assert "far: far" in body
    assert "target == .zero" in body
    assert "towardTop: true, far: true" in body
    assert "nudgeForm(scroll, in: app, towardTop: target.midY > visible.midY, far: far)" in body
    assert "nudgeForm(scroll, in: app, towardTop: target.midY > visible.midY)" not in body.replace(
        "nudgeForm(scroll, in: app, towardTop: target.midY > visible.midY, far: far)", ""
    )


def test_comfortably_on_screen_uses_the_hit_point():
    """Requiring the whole short frame inside the inset band made XXXL titles
    near the edge look unfinished, then a page swipe threw them off-screen.
    """

    body = _function_body("comfortablyOnScreen")
    assert "contains(hit)" in body or "contains(CGPoint" in body
    assert "contains(target)" not in body


def test_software_keyboard_up_is_the_keyboard_element():
    body = _function_body("softwareKeyboardUp")
    assert "keyboards.element.exists" in body
    assert 'buttons["Done"]' not in body


def test_tablet_disconnects_hardware_keyboard():
    """iPad 10th gen on iOS 18.2 ships with hardware keyboard connected.

    The connected hardware keyboard suppresses the software keyboard and causes
    hasKeyboardFocus to report incorrectly after tapping text fields. The check
    script must disconnect the hardware keyboard on iPad before running tests.
    """
    import inspect
    source = inspect.getsource(simulator_check.check_devices)
    assert "_disconnect_hardware_keyboard" in source
    assert 'label == "tablet"' in source

    disconnect_source = inspect.getsource(simulator_check._disconnect_hardware_keyboard)
    assert "simctl" in disconnect_source
    assert "boot" in disconnect_source
    assert "keyboard" in disconnect_source
    assert "disconnect" in disconnect_source
