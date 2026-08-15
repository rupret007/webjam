from __future__ import annotations

from pathlib import Path
import py_compile


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

    app_file = ROOT / "webjam_qt_main.py"
    repo_file = ROOT / "storage" / "repository.py"
    checklist_file = ROOT / "UX_ACCEPTANCE_CHECKLIST.md"
    help_map_file = ROOT / "HELP_ROUTING_MAP.md"
    launch_file = ROOT / "webjam_qt" / "windows" / "launch_dialog.py"
    session_state_file = ROOT / "webjam_qt" / "session_state.py"
    permission_file = ROOT / "webjam_qt" / "platform_permissions.py"
    invite_file = ROOT / "core" / "network_invite.py"
    take_export_file = ROOT / "core" / "take_export.py"
    tokens_file = ROOT / "webjam_qt" / "theme" / "tokens.py"
    studio_file = ROOT / "webjam_qt" / "widgets" / "recording_studio.py"
    recording_setup_file = ROOT / "webjam_qt" / "windows" / "recording_setup.py"
    recording_guide = ROOT / "RECORDING_AND_STUDIO.md"

    for required in (
        app_file,
        repo_file,
        checklist_file,
        help_map_file,
        launch_file,
        session_state_file,
        permission_file,
        invite_file,
        take_export_file,
        tokens_file,
        studio_file,
        recording_setup_file,
        recording_guide,
    ):
        require_file(required, failures)

    # Ensure Qt entry point delegates to the Conductor UI.
    require_contains(app_file, "from webjam_qt.app import run", failures)

    # Ensure the current simple-session checklist cannot silently regress to
    # the legacy setup/start-audio flow.
    for marker in (
        "Launch: understandable in five seconds",
        "Host and invitation",
        "Permission and error states",
        "End, leave, and cleanup truth",
        "Release validation",
    ):
        require_contains(checklist_file, marker, failures)

    require_contains(repo_file, "def increment_setting", failures)
    for marker in (
        "What are you creating?",
        "_CREATOR_LAUNCH_COPY",
        'host="Host"',
        'join="Join"',
        'local="New Music Project"',
        'host="Host Remote Recording"',
        'join="Join Recording"',
        'local="New Local Recording"',
        'host="Host Review"',
        'join="Join Review"',
    ):
        require_contains(launch_file, marker, failures)
    require_contains(session_state_file, "PERMISSION_DENIED", failures)
    require_contains(tokens_file, '#BF5700', failures)
    require_contains(studio_file, "Export Tracks", failures)
    require_contains(take_export_file, "all_stems_start_at_zero", failures)
    require_contains(recording_guide, "Export never rewrites the original take.", failures)

    for target in (
        app_file,
        repo_file,
        launch_file,
        session_state_file,
        permission_file,
        invite_file,
        take_export_file,
        tokens_file,
        studio_file,
        recording_setup_file,
    ):
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
