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


class TestDisableHardwareKeyboard:
    """iPad keyboard setup uses defaults + PlistBuddy, not simctl io keyboard."""

    def test_success_when_setting_is_new(self, tmp_path, monkeypatch):
        """First-time setup adds ConnectHardwareKeyboard = NO."""
        plist_file = tmp_path / "simulator.plist"
        plist_path = tmp_path / "Library/Preferences/com.apple.iphonesimulator.plist"
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        calls = []
        print_call_count = [0]

        def mock_run(args, **kwargs):
            calls.append(args)
            class Result:
                returncode = 0
                stdout = ""
                stderr = ""
            result = Result()
            if args[0] == "defaults" and args[1] == "export":
                plist_file.write_text("<plist></plist>")
            elif args[0] == "/usr/libexec/PlistBuddy":
                if "Print" in args[2]:
                    print_call_count[0] += 1
                    if print_call_count[0] == 1:
                        result.returncode = 1
                        result.stdout = ""
                    else:
                        result.stdout = "false"
            return result

        monkeypatch.setattr(simulator_check.subprocess, "run", mock_run)
        monkeypatch.setattr(simulator_check.Path, "home", lambda: tmp_path)

        result = simulator_check._disable_hardware_keyboard("TEST-UUID")

        assert result.success is True
        assert result.already_configured is False
        assert result.error is None
        assert any("defaults" in str(c) and "export" in str(c) for c in calls)
        assert any("PlistBuddy" in str(c) and "Add" in str(c) for c in calls)
        assert any("defaults" in str(c) and "import" in str(c) for c in calls)

    def test_success_when_already_configured(self, tmp_path, monkeypatch):
        """Returns already_configured=True without re-importing when value is false."""
        calls = []

        def mock_run(args, **kwargs):
            calls.append(args)
            class Result:
                returncode = 0
                stdout = "false"
                stderr = ""
            return Result()

        monkeypatch.setattr(simulator_check.subprocess, "run", mock_run)
        monkeypatch.setattr(simulator_check.Path, "home", lambda: tmp_path)

        result = simulator_check._disable_hardware_keyboard("TEST-UUID")

        assert result.success is True
        assert result.already_configured is True
        assert not any("import" in str(c) for c in calls)

    def test_failure_on_export_error(self, tmp_path, monkeypatch):
        """Fails closed with diagnostics when defaults export fails."""
        def mock_run(args, **kwargs):
            class Result:
                returncode = 1 if args[1] == "export" else 0
                stdout = ""
                stderr = "cfprefsd error"
            return Result()

        monkeypatch.setattr(simulator_check.subprocess, "run", mock_run)
        monkeypatch.setattr(simulator_check.Path, "home", lambda: tmp_path)

        result = simulator_check._disable_hardware_keyboard("TEST-UUID")

        assert result.success is False
        assert "defaults export failed" in result.error
        assert result.details["export_exit_code"] == 1
        assert result.details["export_stderr"] == "cfprefsd error"

    def test_failure_on_plistbuddy_set_error(self, tmp_path, monkeypatch):
        """Fails closed with diagnostics when PlistBuddy Set fails."""
        plist_file = tmp_path / "simulator.plist"

        def mock_run(args, **kwargs):
            class Result:
                returncode = 0
                stdout = ""
                stderr = ""
            result = Result()
            if args[0] == "defaults" and args[1] == "export":
                plist_file.write_text("<plist></plist>")
            elif args[0] == "/usr/libexec/PlistBuddy":
                if "Print" in args[2]:
                    result.stdout = "true"
                elif "Set" in args[2]:
                    result.returncode = 1
                    result.stderr = "invalid path"
            return result

        monkeypatch.setattr(simulator_check.subprocess, "run", mock_run)
        monkeypatch.setattr(simulator_check.Path, "home", lambda: tmp_path)

        result = simulator_check._disable_hardware_keyboard("TEST-UUID")

        assert result.success is False
        assert "PlistBuddy Set failed" in result.error
        assert result.details["set_exit_code"] == 1

    def test_failure_on_verification_mismatch(self, tmp_path, monkeypatch):
        """Fails closed when final verification shows wrong value."""
        plist_file = tmp_path / "simulator.plist"
        verify_call_count = [0]

        def mock_run(args, **kwargs):
            class Result:
                returncode = 0
                stdout = ""
                stderr = ""
            result = Result()
            if args[0] == "defaults" and args[1] == "export":
                plist_file.write_text("<plist></plist>")
            elif args[0] == "/usr/libexec/PlistBuddy" and "Print" in args[2]:
                verify_call_count[0] += 1
                if verify_call_count[0] == 1:
                    result.returncode = 1
                else:
                    result.stdout = "true"
            return result

        monkeypatch.setattr(simulator_check.subprocess, "run", mock_run)
        monkeypatch.setattr(simulator_check.Path, "home", lambda: tmp_path)

        result = simulator_check._disable_hardware_keyboard("TEST-UUID")

        assert result.success is False
        assert "verification failed" in result.error
        assert result.details["verify_value"] == "true"

    def test_check_devices_fails_closed_on_keyboard_setup_failure(self, monkeypatch):
        """check_devices raises SystemExit when tablet keyboard setup fails."""
        import inspect
        source = inspect.getsource(simulator_check.check_devices)
        assert "_disable_hardware_keyboard" in source
        assert 'label == "tablet"' in source
        assert "keyboard_setup.success" in source
        assert "fail-closed" in source
        assert "SystemExit" in source

    def test_keyboard_setup_recorded_in_device_entry(self):
        """Device entry includes keyboard_setup diagnostics for tablets."""
        import inspect
        source = inspect.getsource(simulator_check.check_devices)
        assert 'entry["keyboard_setup"]' in source
        assert "keyboard_setup.to_dict()" in source

    def test_uses_defaults_and_plistbuddy_not_simctl_io(self):
        """The fix uses defaults export/import + PlistBuddy, not simctl io keyboard."""
        import inspect
        source = inspect.getsource(simulator_check._disable_hardware_keyboard)
        assert "defaults" in source
        assert "export" in source
        assert "import" in source
        assert "PlistBuddy" in source
        assert "ConnectHardwareKeyboard" in source
        assert "simctl io" not in source
        assert 'keyboard", "disconnect' not in source
