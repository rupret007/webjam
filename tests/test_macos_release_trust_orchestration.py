"""Behavioral rehearsal of macOS release orchestration with inert Apple tools."""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TRUST_SCRIPT = ROOT / "packaging" / "macos" / "release-trust.sh"
TEAM_ID = "TEAMID1234"


def _write_plist(path: Path, values: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as stream:
        plistlib.dump(values, stream)


def _write_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"inert Mach-O fixture\n")
    path.chmod(0o755)


def _make_app(root: Path) -> Path:
    app = root / "WebJam.app"
    _write_plist(
        app / "Contents" / "Info.plist",
        {
            "CFBundleExecutable": "WebJam",
            "NSMicrophoneUsageDescription": "Record a rehearsal.",
        },
    )
    _write_executable(app / "Contents" / "MacOS" / "WebJam")
    _write_executable(app / "Contents" / "MacOS" / "webjam-fabric")
    (app / "Contents" / "Resources").mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Resources" / "webjam-fabric.sha256").write_text(
        "pre-sign-placeholder\n", encoding="utf-8"
    )
    (
        app / "Contents" / "Resources" / "JamulusHeadlessClient.sha256"
    ).write_text("pre-sign-placeholder\n", encoding="utf-8")

    for name, executable in (
        ("Jamulus.app", "Jamulus"),
        ("JamulusServer.app", "JamulusServer"),
        ("JamulusHeadlessClient.app", "JamulusHeadlessClient"),
    ):
        nested = app / "Contents" / "Resources" / name
        _write_plist(
            nested / "Contents" / "Info.plist",
            {
                "CFBundleExecutable": executable,
                "NSMicrophoneUsageDescription": "Use the audio interface.",
            },
        )
        _write_executable(nested / "Contents" / "MacOS" / executable)
        if name == "JamulusHeadlessClient.app":
            source = (
                nested
                / "Contents"
                / "Resources"
                / "THIRD_PARTY_LICENSES"
                / "JamulusHeadlessClient-CORRESPONDING-SOURCE.tar.gz"
            )
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(b"inert corresponding-source fixture\n")

    return app


def _make_fake_tools(root: Path) -> dict[str, Path]:
    tools = root / "tools"
    tools.mkdir()
    implementation = tools / "fake-apple-tool"
    implementation.write_text(
        f"""#!/usr/bin/env python3
import hashlib
import json
import os
import pathlib
import plistlib
import shutil
import sys

name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
log_path = pathlib.Path(os.environ["FAKE_TOOL_LOG"])
with log_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({{"tool": name, "args": args}}) + "\\n")


def entitlement_path(target):
    normalized = target.replace(os.sep, "/")
    repo = pathlib.Path(os.environ["FAKE_REPO_ROOT"])
    if "JamulusHeadlessClient.app" in normalized:
        return repo / "packaging" / "macos" / "Jamulus.entitlements"
    if "JamulusServer.app" in normalized:
        return repo / "packaging" / "macos" / "Jamulus.entitlements"
    if "Jamulus.app" in normalized:
        return repo / "packaging" / "macos" / "Jamulus.entitlements"
    if normalized.endswith("/WebJam.app") or normalized.endswith("/Contents/MacOS/WebJam"):
        return repo / "packaging" / "macos" / "WebJam.entitlements"
    return None


if name == "codesign":
    target = args[-1]
    if "-d" in args and "--entitlements" in args:
        if os.environ.get("FAKE_ENTITLEMENTS_ERROR") == "1":
            raise SystemExit(9)
        source = entitlement_path(target)
        if source is not None:
            with source.open("rb") as stream:
                value = plistlib.load(stream)
            sys.stdout.buffer.write(plistlib.dumps(value, fmt=plistlib.FMT_XML))
    elif "-d" in args:
        print(f"Executable={{target}}")
        print("CodeDirectory v=20500 size=100 flags=0x10000(runtime) hashes=1+1 location=embedded")
        print("Authority=Developer ID Application: WebJam Test ({TEAM_ID})")
        print("Authority=Developer ID Certification Authority")
        print("Authority=Apple Root CA")
        print("Timestamp=Jul 16, 2026 at 12:00:00")
        print("TeamIdentifier={TEAM_ID}")
    raise SystemExit(0)

if name == "file":
    if os.environ.get("FAKE_FILE_ERROR") == "1":
        raise SystemExit(9)
    target = pathlib.Path(args[-1])
    if target.is_file() and (os.access(target, os.X_OK) or target.suffix == ".dylib"):
        print("Mach-O 64-bit executable")
    else:
        print("data")
    raise SystemExit(0)

if name == "shasum":
    target = pathlib.Path(args[-1])
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"{{digest}}  {{target}}")
    raise SystemExit(0)

if name == "ditto":
    if "-c" in args:
        pathlib.Path(args[-1]).write_bytes(b"inert zip fixture\\n")
    elif "-x" in args:
        destination = pathlib.Path(args[-1]) / "WebJam.app"
        shutil.copytree(
            pathlib.Path(os.environ["FAKE_APP_SOURCE"]),
            destination,
            symlinks=True,
        )
    else:
        raise SystemExit("unsupported fake ditto operation")
    raise SystemExit(0)

if name == "xcrun":
    if args[:2] == ["notarytool", "submit"]:
        status = os.environ.get("FAKE_NOTARY_STATUS", "Accepted")
        print(json.dumps({{"id": "12345678-1234-1234-1234-123456789abc", "status": status}}))
    elif args[:2] == ["notarytool", "log"]:
        status = os.environ.get("FAKE_NOTARY_STATUS", "Accepted")
        pathlib.Path(args[-1]).write_text(
            json.dumps({{"status": status, "issues": None}}),
            encoding="utf-8",
        )
    elif args and args[0] == "stapler":
        pass
    else:
        raise SystemExit(f"unsupported fake xcrun operation: {{args!r}}")
    raise SystemExit(0)

if name in {{"spctl", "syspolicy_check", "hdiutil"}}:
    raise SystemExit(0)

raise SystemExit(f"unknown fake tool: {{name}}")
""",
        encoding="utf-8",
    )
    implementation.chmod(0o755)
    result = {}
    for name in (
        "codesign",
        "xcrun",
        "spctl",
        "syspolicy_check",
        "ditto",
        "hdiutil",
        "file",
        "shasum",
    ):
        path = tools / name
        path.symlink_to(implementation.name)
        result[name] = path
    return result


@pytest.fixture
def trust_rehearsal(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    app = _make_app(tmp_path)
    tools = _make_fake_tools(tmp_path)
    log = tmp_path / "fake-tools.jsonl"
    keychain = tmp_path / "release.keychain-db"
    notary_key = tmp_path / "AuthKey.p8"
    keychain.write_bytes(b"inert keychain")
    notary_key.write_bytes(b"inert key")
    env = os.environ.copy()
    env.update(
        {
            "RUNNER_TEMP": str(tmp_path / "runner-temp"),
            "WEBJAM_MACOS_CODESIGN_IDENTITY": "A" * 40,
            "WEBJAM_MACOS_CODESIGN_TEAM_ID": TEAM_ID,
            "WEBJAM_MACOS_KEYCHAIN": str(keychain),
            "WEBJAM_NOTARY_KEY_P8": str(notary_key),
            "WEBJAM_NOTARY_KEY_ID": "ABC123DEF4",
            "WEBJAM_NOTARY_ISSUER_ID": "01234567-89ab-cdef-0123-456789abcdef",
            "WEBJAM_CODESIGN_BIN": str(tools["codesign"]),
            "WEBJAM_XCRUN_BIN": str(tools["xcrun"]),
            "WEBJAM_SPCTL_BIN": str(tools["spctl"]),
            "WEBJAM_SYSPOLICY_CHECK_BIN": str(tools["syspolicy_check"]),
            "WEBJAM_DITTO_BIN": str(tools["ditto"]),
            "WEBJAM_HDIUTIL_BIN": str(tools["hdiutil"]),
            "WEBJAM_FILE_BIN": str(tools["file"]),
            "WEBJAM_SHASUM_BIN": str(tools["shasum"]),
            "WEBJAM_PYTHON_BIN": sys.executable,
            "FAKE_APP_SOURCE": str(app),
            "FAKE_REPO_ROOT": str(ROOT),
            "FAKE_TOOL_LOG": str(log),
        }
    )
    Path(env["RUNNER_TEMP"]).mkdir()
    return app, env, log


def _run(
    tmp_path: Path,
    env: dict[str, str],
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(TRUST_SCRIPT), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _events(log: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


@pytest.mark.skipif(os.name == "nt", reason="release trust orchestration is Bash")
def test_app_and_dmg_release_rehearsal_is_inside_out_and_fail_closed(
    tmp_path: Path,
    trust_rehearsal: tuple[Path, dict[str, str], Path],
) -> None:
    app, env, log = trust_rehearsal
    final_zip = tmp_path / "out" / "WebJam-macos-x64.zip"
    evidence = tmp_path / "out" / "notarization" / "macos-x64"
    app_result = _run(
        tmp_path,
        env,
        "app",
        str(app.relative_to(tmp_path)),
        str(final_zip.relative_to(tmp_path)),
        str(evidence.relative_to(tmp_path)),
    )
    assert app_result.returncode == 0, app_result.stdout + app_result.stderr
    assert final_zip.is_file()

    dmg = tmp_path / "out" / "WebJam-v0.16.3-macos-x64.dmg"
    dmg.write_bytes(b"inert dmg fixture\n")
    dmg_result = _run(
        tmp_path,
        env,
        "dmg",
        str(dmg.relative_to(tmp_path)),
        str(evidence.relative_to(tmp_path)),
    )
    assert dmg_result.returncode == 0, dmg_result.stdout + dmg_result.stderr

    events = _events(log)
    sign_events = [
        event
        for event in events
        if event["tool"] == "codesign" and "--sign" in event["args"]
    ]
    app_sign_events = [
        event for event in sign_events if not str(event["args"][-1]).endswith(".dmg")
    ]
    assert app_sign_events
    outer_sign = app_sign_events[-1]
    assert str(outer_sign["args"][-1]).endswith("WebJam.app")
    assert str(ROOT / "packaging" / "macos" / "WebJam.entitlements") in outer_sign["args"]
    for event in app_sign_events:
        args = event["args"]
        assert "--deep" not in args
        assert "--options" in args and "runtime" in args
        assert "--timestamp" in args
        assert "--keychain" in args

    jamulus_signs = [
        event
        for event in app_sign_events
        if str(event["args"][-1]).endswith(
            ("Jamulus.app", "JamulusServer.app", "JamulusHeadlessClient.app")
        )
    ]
    assert len(jamulus_signs) == 3
    for event in jamulus_signs:
        assert str(ROOT / "packaging" / "macos" / "Jamulus.entitlements") in event["args"]
    assert not any(
        "QtWebEngine" in str(event["args"][-1])
        for event in app_sign_events
    )

    notary_submits = [
        event
        for event in events
        if event["tool"] == "xcrun" and event["args"][:2] == ["notarytool", "submit"]
    ]
    notary_logs = [
        event
        for event in events
        if event["tool"] == "xcrun" and event["args"][:2] == ["notarytool", "log"]
    ]
    assert len(notary_submits) == 2
    assert len(notary_logs) == 2
    assert (evidence / "app-notary-submit.json").is_file()
    assert (evidence / "app-notary-log.json").is_file()
    assert (evidence / "dmg-notary-submit.json").is_file()
    assert (evidence / "dmg-notary-log.json").is_file()
    assert (evidence / "app-final-zip.sha256").is_file()
    assert (evidence / "dmg-final-stapled.sha256").is_file()


@pytest.mark.skipif(os.name == "nt", reason="release trust orchestration is Bash")
def test_retired_webengine_runtime_is_rejected_before_signing(
    tmp_path: Path,
    trust_rehearsal: tuple[Path, dict[str, str], Path],
) -> None:
    app, env, log = trust_rehearsal
    retired = (
        app
        / "Contents"
        / "Frameworks"
        / "PySide6"
        / "QtWebEngineCore.framework"
    )
    _write_executable(retired / "Versions" / "A" / "QtWebEngineCore")

    result = _run(
        tmp_path,
        env,
        "app",
        str(app.relative_to(tmp_path)),
        "WebJam-macos-x64.zip",
        "evidence",
    )

    assert result.returncode != 0
    assert "retired Qt WebEngine runtime is present" in result.stderr
    assert not log.exists()


@pytest.mark.skipif(os.name == "nt", reason="release trust orchestration is Bash")
def test_missing_headless_corresponding_source_is_rejected_before_signing(
    tmp_path: Path,
    trust_rehearsal: tuple[Path, dict[str, str], Path],
) -> None:
    app, env, log = trust_rehearsal
    source = (
        app
        / "Contents"
        / "Resources"
        / "JamulusHeadlessClient.app"
        / "Contents"
        / "Resources"
        / "THIRD_PARTY_LICENSES"
        / "JamulusHeadlessClient-CORRESPONDING-SOURCE.tar.gz"
    )
    source.unlink()

    result = _run(
        tmp_path,
        env,
        "app",
        str(app.relative_to(tmp_path)),
        "WebJam-macos-x64.zip",
        "evidence",
    )

    assert result.returncode != 0
    assert "Jamulus HEADLESS corresponding source is missing" in result.stderr
    assert not log.exists()


@pytest.mark.skipif(os.name == "nt", reason="release trust orchestration is Bash")
def test_rejected_notarization_retains_log_and_never_staples(
    tmp_path: Path,
    trust_rehearsal: tuple[Path, dict[str, str], Path],
) -> None:
    app, env, log = trust_rehearsal
    env["FAKE_NOTARY_STATUS"] = "Rejected"
    evidence = tmp_path / "evidence"
    result = _run(
        tmp_path,
        env,
        "app",
        str(app.relative_to(tmp_path)),
        "WebJam-macos-x64.zip",
        str(evidence.relative_to(tmp_path)),
    )
    assert result.returncode != 0
    assert (evidence / "app-notary-submit.json").is_file()
    assert (evidence / "app-notary-log.json").is_file()
    events = _events(log)
    assert any(
        event["tool"] == "xcrun" and event["args"][:2] == ["notarytool", "log"]
        for event in events
    )
    assert not any(
        event["tool"] == "xcrun" and event["args"][:2] == ["stapler", "staple"]
        for event in events
    )


@pytest.mark.skipif(os.name == "nt", reason="release trust orchestration is Bash")
@pytest.mark.parametrize("failure_mode", ("FAKE_FILE_ERROR", "FAKE_ENTITLEMENTS_ERROR"))
def test_uninspectable_code_fails_before_notarization(
    tmp_path: Path,
    trust_rehearsal: tuple[Path, dict[str, str], Path],
    failure_mode: str,
) -> None:
    app, env, log = trust_rehearsal
    env[failure_mode] = "1"
    result = _run(
        tmp_path,
        env,
        "app",
        str(app.relative_to(tmp_path)),
        "WebJam-macos-x64.zip",
        "evidence",
    )
    assert result.returncode != 0
    events = _events(log)
    assert not any(
        event["tool"] == "xcrun" and event["args"][:2] == ["notarytool", "submit"]
        for event in events
    )
