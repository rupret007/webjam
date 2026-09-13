"""Unsigned iPhone/iPad UI gate; keeps real XCTest attachments for human review."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run(*arguments: str) -> str:
    return subprocess.check_output(arguments, text=True).strip()


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


def check_devices(root: Path, output: Path) -> None:
    inventory = json.loads(run("xcrun", "simctl", "list", "--json"))
    runtime = next(
        item["identifier"] for item in inventory["runtimes"]
        if item["name"] == "iOS 18.2" and item["isAvailable"]
    )
    evidence = []
    for label, name in (("phone", "iPhone SE (3rd generation)"), ("tablet", "iPad (10th generation)")):
        device_type = next(item["identifier"] for item in inventory["devicetypes"] if item["name"] == name)
        identifier = run("xcrun", "simctl", "create", f"WebJam Art {label}", device_type, runtime)
        result = output / f"{label}.xcresult"
        log = output / f"{label}.log"
        entry = {"device": name, "status": "incomplete", "exit_code": None, "result": result.name}
        try:
            with log.open("w", encoding="utf-8") as handle:
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
                    entry.update(status="passed" if completed.returncode == 0 else "failed",
                                 exit_code=completed.returncode)
                except subprocess.TimeoutExpired:
                    entry.update(status="timed_out", exit_code=124)
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
                # UI fixture data is synthetic and contains no private invitation.
                print(log.read_text(encoding="utf-8")[-18000:])
                raise SystemExit(entry["exit_code"])
            if export_code:
                raise SystemExit("Simulator tests passed, but attachment export failed; inspect the retained result bundle.")
        finally:
            evidence.append(entry)
            (output / "devices.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
            subprocess.run(["xcrun", "simctl", "shutdown", identifier], check=False, capture_output=True)
            subprocess.run(["xcrun", "simctl", "delete", identifier], check=False, capture_output=True)


if __name__ == "__main__":
    main()
