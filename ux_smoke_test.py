from __future__ import annotations

from pathlib import Path
import py_compile
import sys


ROOT = Path(__file__).resolve().parent


def require_file(path: Path, failures: list[str]) -> None:
    if not path.exists():
        failures.append(f"Missing required file: {path.name}")


def require_contains(path: Path, needle: str, failures: list[str]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        failures.append(f"Cannot read {path.name}: {exc}")
        return
    if needle not in text:
        failures.append(f"{path.name} missing expected text: {needle!r}")


def require_compiles(path: Path, failures: list[str]) -> None:
    try:
        py_compile.compile(str(path), doraise=True)
    except Exception as exc:
        failures.append(f"Compile failed for {path.name}: {exc}")


def main() -> int:
    failures: list[str] = []

    app_file = ROOT / "webjam_app_enhanced.py"
    repo_file = ROOT / "storage" / "repository.py"
    checklist_file = ROOT / "UX_ACCEPTANCE_CHECKLIST.md"
    help_map_file = ROOT / "HELP_ROUTING_MAP.md"

    for required in (app_file, repo_file, checklist_file, help_map_file):
        require_file(required, failures)

    # Ensure key UX affordances are still present.
    for marker in (
        "Run Setup Wizard",
        "Start Guided Tour",
        "Open Diagnostics Panel",
        "Reset All UI Preferences",
        "View Usage Metrics",
        "Run Setup Wizard on startup",
    ):
        require_contains(app_file, marker, failures)

    # Ensure checklist still covers release validation.
    for marker in (
        "Install and First Run",
        "Session Launch and Status Clarity",
        "Error Recovery",
        "Regression and Validation",
        "python -m unittest test_modernization.py",
        "python -m unittest test_webjam.py",
    ):
        require_contains(checklist_file, marker, failures)

    require_contains(repo_file, "def increment_setting", failures)

    for target in (app_file, repo_file):
        require_compiles(target, failures)

    if failures:
        print("UX smoke gate failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("UX smoke gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
