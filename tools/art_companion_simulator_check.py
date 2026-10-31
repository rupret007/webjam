"""Unsigned iPhone/iPad UI gate; keeps real XCTest attachments for human review."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def run(*arguments: str) -> str:
    return subprocess.check_output(arguments, text=True).strip()


TRANSIENT_BACKGROUND_ASSERTION = "Failed to get background assertion for target app"


def _should_retry_simulator_run(*, exit_code: int, log_path: Path) -> bool:
    """Retry exactly once for known simulator infrastructure launch flakes."""

    if exit_code == 0:
        return False
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return TRANSIENT_BACKGROUND_ASSERTION in text


def main() -> None:
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from tools.art_companion_simulator_host import simulator_host

    with simulator_host(root):
        subprocess.run(
            [os.environ.get("ART_XCODEGEN", "xcodegen"), "generate",
             "--spec", str(root / "ios/art-companion.yml")], check=True,
        )
        check_devices(root, output)


class KeyboardSetupResult:
    """Result of attempting to configure simulator keyboard settings."""

    __slots__ = ("success", "already_configured", "error", "details")

    def __init__(
        self,
        *,
        success: bool,
        already_configured: bool = False,
        error: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.success = success
        self.already_configured = already_configured
        self.error = error
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "already_configured": self.already_configured,
            "error": self.error,
            "details": self.details,
        }


def _disable_hardware_keyboard(identifier: str) -> KeyboardSetupResult:
    """Disable hardware keyboard for a simulator so the software keyboard appears.

    iOS 18+ iPad simulators default to hardware keyboard connected, suppressing the
    software keyboard. This prevents UI tests from verifying keyboard focus and the
    keyboard toolbar. The fix uses defaults + PlistBuddy to set ConnectHardwareKeyboard
    to NO for this device before xcodebuild boots it.

    simctl and devicectl have no keyboard connect/disconnect command. The correct
    mechanism is documented at:
    - https://stackoverflow.com/questions/55381560 (Xcode 16 answer by Wes Campaigne)
    - https://github.com/getsentry/XcodeBuildMCP/issues/346
    """
    plist_path = Path.home() / "Library/Preferences/com.apple.iphonesimulator.plist"
    details: dict[str, Any] = {"identifier": identifier, "plist_path": str(plist_path)}

    with tempfile.NamedTemporaryFile(suffix=".plist", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        export_result = subprocess.run(
            ["defaults", "export", "com.apple.iphonesimulator", str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        details["export_exit_code"] = export_result.returncode
        if export_result.returncode != 0:
            details["export_stderr"] = export_result.stderr
            return KeyboardSetupResult(
                success=False,
                error=f"defaults export failed: exit {export_result.returncode}",
                details=details,
            )

        read_result = subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c",
             f"Print :DevicePreferences:{identifier}:ConnectHardwareKeyboard",
             str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        details["read_exit_code"] = read_result.returncode
        current_value = read_result.stdout.strip() if read_result.returncode == 0 else None
        details["current_value"] = current_value

        if current_value == "false":
            return KeyboardSetupResult(
                success=True, already_configured=True, details=details,
            )

        if current_value is None:
            plist_cmd = "Add"
            plist_args = f":DevicePreferences:{identifier}:ConnectHardwareKeyboard bool NO"
        else:
            plist_cmd = "Set"
            plist_args = f":DevicePreferences:{identifier}:ConnectHardwareKeyboard false"

        set_result = subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c", f"{plist_cmd} {plist_args}", str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        details["set_exit_code"] = set_result.returncode
        if set_result.returncode != 0:
            details["set_stderr"] = set_result.stderr
            return KeyboardSetupResult(
                success=False,
                error=f"PlistBuddy {plist_cmd} failed: exit {set_result.returncode}",
                details=details,
            )

        import_result = subprocess.run(
            ["defaults", "import", "com.apple.iphonesimulator", str(tmp_path)],
            capture_output=True, text=True, check=False,
        )
        details["import_exit_code"] = import_result.returncode
        if import_result.returncode != 0:
            details["import_stderr"] = import_result.stderr
            return KeyboardSetupResult(
                success=False,
                error=f"defaults import failed: exit {import_result.returncode}",
                details=details,
            )

        verify_result = subprocess.run(
            ["/usr/libexec/PlistBuddy", "-c",
             f"Print :DevicePreferences:{identifier}:ConnectHardwareKeyboard",
             str(plist_path)],
            capture_output=True, text=True, check=False,
        )
        details["verify_exit_code"] = verify_result.returncode
        details["verify_value"] = verify_result.stdout.strip() if verify_result.returncode == 0 else None

        if details["verify_value"] != "false":
            return KeyboardSetupResult(
                success=False,
                error=f"verification failed: expected false, got {details['verify_value']}",
                details=details,
            )

        return KeyboardSetupResult(success=True, details=details)

    finally:
        tmp_path.unlink(missing_ok=True)


def _cleanup_simulator(identifier: str) -> None:
    """Shutdown and delete a simulator, ignoring errors."""
    subprocess.run(["xcrun", "simctl", "shutdown", identifier], check=False, capture_output=True)
    subprocess.run(["xcrun", "simctl", "delete", identifier], check=False, capture_output=True)


def _write_evidence(output: Path, evidence: list[dict[str, Any]]) -> None:
    """Write devices.json, raising on failure."""
    (output / "devices.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def check_devices(root: Path, output: Path) -> None:
    inventory = json.loads(run("xcrun", "simctl", "list", "--json"))
    runtime = next(
        item["identifier"] for item in inventory["runtimes"]
        if item["name"] == "iOS 18.2" and item["isAvailable"]
    )
    evidence: list[dict[str, Any]] = []
    for label, name in (("phone", "iPhone SE (3rd generation)"), ("tablet", "iPad (10th generation)")):
        device_type = next(item["identifier"] for item in inventory["devicetypes"] if item["name"] == name)
        identifier = run("xcrun", "simctl", "create", f"WebJam Art {label}", device_type, runtime)
        result = output / f"{label}.xcresult"
        log = output / f"{label}.log"
        entry: dict[str, Any] = {
            "device": name,
            "status": "incomplete",
            "exit_code": None,
            "result": result.name,
        }
        try:
            keyboard_setup: KeyboardSetupResult | None = None
            if label == "tablet":
                try:
                    keyboard_setup = _disable_hardware_keyboard(identifier)
                    entry["keyboard_setup"] = keyboard_setup.to_dict()
                    if not keyboard_setup.success:
                        entry["status"] = "keyboard_setup_failed"
                        raise SystemExit(
                            f"iPad keyboard setup failed (fail-closed): {keyboard_setup.error}\n"
                            f"Details: {json.dumps(keyboard_setup.details, indent=2)}"
                        )
                except SystemExit:
                    raise
                except BaseException as exc:
                    import traceback
                    entry["status"] = "keyboard_setup_exception"
                    entry["keyboard_setup"] = {
                        "success": False,
                        "already_configured": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "details": {
                            "identifier": identifier,
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                            "traceback": traceback.format_exc(),
                        },
                    }
                    raise SystemExit(
                        f"iPad keyboard setup raised exception (fail-closed): {type(exc).__name__}: {exc}"
                    ) from exc
            max_attempts = 2
            for attempt in range(1, max_attempts + 1):
                if result.exists():
                    shutil.rmtree(result, ignore_errors=True)
                with log.open("a" if attempt > 1 else "w", encoding="utf-8") as handle:
                    if attempt > 1:
                        handle.write(
                            "\n--- retrying once after simulator background assertion flake ---\n\n"
                        )
                    try:
                        completed = subprocess.run(
                            [
                                "xcodebuild", "-project", str(root / "ios/WebJamArtCompanion.xcodeproj"),
                                "-scheme", "ArtCompanion", "-sdk", "iphonesimulator",
                                "-destination", f"platform=iOS Simulator,id={identifier}",
                                "-parallel-testing-enabled", "NO", "-resultBundlePath", str(result),
                                "CODE_SIGNING_ALLOWED=NO", "test",
                            ],
                            stdout=handle, stderr=subprocess.STDOUT, timeout=900, check=False,
                        )
                    except subprocess.TimeoutExpired:
                        entry.update(status="timed_out", exit_code=124)
                        break
                if completed.returncode == 0:
                    entry.update(status="passed", exit_code=0)
                    break
                if (attempt < max_attempts and _should_retry_simulator_run(
                    exit_code=completed.returncode, log_path=log
                )):
                    continue
                entry.update(status="failed", exit_code=completed.returncode)
                break
            export_code = 0
            if result.exists():
                with (output / f"{label}-export.log").open("w", encoding="utf-8") as handle:
                    try:
                        exported = subprocess.run(
                            ["xcrun", "xcresulttool", "export", "attachments", "--path", str(result),
                             "--output-path", str(output / f"{label}-screenshots")],
                            stdout=handle, stderr=subprocess.STDOUT, timeout=120, check=False,
                        )
                        export_code = exported.returncode
                    except subprocess.TimeoutExpired:
                        export_code = 124
                entry["attachment_export_exit_code"] = export_code
            if entry["exit_code"]:
                print(log.read_text(encoding="utf-8")[-18000:])
                raise SystemExit(entry["exit_code"])
            if export_code:
                raise SystemExit("Simulator tests passed, but attachment export failed; inspect the retained result bundle.")
        finally:
            evidence.append(entry)
            try:
                _write_evidence(output, evidence)
            finally:
                _cleanup_simulator(identifier)


if __name__ == "__main__":
    main()
