"""Behavior contracts for the clickable ad-hoc macOS candidate installer."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "packaging" / "macos" / "Install WebJam.command"
ADVANCED = (
    ROOT / "packaging" / "macos" / "Install WebJam - Remove Quarantine.command"
)
BUILD_ID = "a" * 40
FABRIC_HASH = "b" * 64


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _make_package(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    package = tmp_path / "Candidate Package With Spaces"
    package.mkdir()
    shutil.copy2(INSTALLER, package / INSTALLER.name)
    shutil.copy2(ADVANCED, package / ADVANCED.name)

    app = package / "WebJam.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    (app / "Contents" / "Info.plist").write_text("plist", encoding="utf-8")
    for name in ("WebJam", "webjam-fabric"):
        _write_executable(app / "Contents" / "MacOS" / name, "#!/bin/sh\nexit 0\n")
    (app / "Contents" / "Resources" / "webjam-fabric.sha256").write_text(
        f"{FABRIC_HASH}\n", encoding="utf-8"
    )
    (app / "Contents" / "Resources" / "webjam-build-id.txt").write_text(
        f"{BUILD_ID}\n", encoding="utf-8"
    )
    (app / ".quarantine").write_text("present", encoding="utf-8")
    (package / "WebJam Candidate Info.txt").write_text(
        "\n".join(
            (
                "format=1",
                "version=0.18.1",
                f"build_id={BUILD_ID}",
                "target=macos-arm64",
                "architecture=arm64",
                "trust=ad-hoc-unnotarized",
                "",
            )
        ),
        encoding="utf-8",
    )

    tools = tmp_path / "Fake Tools"
    tools.mkdir()
    _write_executable(
        tools / "codesign",
        "#!/bin/sh\n"
        'if [ "${FAKE_CODESIGN_FAIL:-0}" = 1 ]; then exit 1; fi\n'
        "exit 0\n",
    )
    _write_executable(
        tools / "ditto",
        "#!/bin/sh\n"
        'cp -a "$1" "$2"\n',
    )
    _write_executable(
        tools / "file",
        "#!/bin/sh\n"
        'printf "%s: Mach-O 64-bit executable %s\\n" "$1" "${FAKE_ARCH:-arm64}"\n',
    )
    _write_executable(
        tools / "open",
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$FAKE_OPEN_LOG"\n',
    )
    _write_executable(
        tools / "plistbuddy",
        "#!/bin/sh\n"
        'printf "%s\\n" "${FAKE_VERSION:-0.18.1}"\n',
    )
    _write_executable(
        tools / "shasum",
        "#!/bin/sh\n"
        f'printf "%s  %s\\n" "${{FAKE_FABRIC_HASH:-{FABRIC_HASH}}}" "$3"\n',
    )
    _write_executable(
        tools / "xattr",
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$FAKE_XATTR_LOG"\n'
        'if [ "$1" = "-dr" ]; then\n'
        '  find "$3" -name .quarantine -delete\n'
        'elif [ "$1" = "-lr" ] && find "$2" -name .quarantine -print -quit | grep -q .; then\n'
        '  printf "%s: com.apple.quarantine\\n" "$2"\n'
        "fi\n",
    )

    home = tmp_path / "Home With Spaces"
    home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "WEBJAM_CODESIGN_BIN": str(tools / "codesign"),
            "WEBJAM_DITTO_BIN": str(tools / "ditto"),
            "WEBJAM_FILE_BIN": str(tools / "file"),
            "WEBJAM_OPEN_BIN": str(tools / "open"),
            "WEBJAM_PLIST_BUDDY_BIN": str(tools / "plistbuddy"),
            "WEBJAM_SHASUM_BIN": str(tools / "shasum"),
            "WEBJAM_XATTR_BIN": str(tools / "xattr"),
            "WEBJAM_INSTALL_ASSUME_YES": "1",
            "WEBJAM_INSTALL_MACHINE": "arm64",
            "WEBJAM_INSTALL_NO_PAUSE": "1",
            "WEBJAM_INSTALL_TEST_MODE": "1",
            "WEBJAM_SYSTEM_APPLICATIONS_DIR": str(tmp_path / "Missing Applications"),
            "FAKE_OPEN_LOG": str(tmp_path / "open.log"),
            "FAKE_XATTR_LOG": str(tmp_path / "xattr.log"),
        }
    )
    return package, env


def _run(package: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(package / INSTALLER.name), *args],
        capture_output=True,
        check=False,
        env=env,
        text=True,
        timeout=10,
    )


def test_guided_install_handles_spaces_preserves_quarantine_and_guides_open_anyway(
    tmp_path: Path,
) -> None:
    package, env = _make_package(tmp_path)

    result = _run(package, env)

    assert result.returncode == 0, result.stderr
    installed = Path(env["HOME"]) / "Applications" / "WebJam.app"
    assert (installed / ".quarantine").is_file()
    assert "Apple quarantine was preserved" in result.stdout
    assert "Open Anyway" in result.stdout
    open_log = Path(env["FAKE_OPEN_LOG"]).read_text(encoding="utf-8")
    assert str(installed) in open_log
    assert "-a System Settings" in open_log


def test_advanced_install_removes_only_installed_webjam_quarantine(
    tmp_path: Path,
) -> None:
    package, env = _make_package(tmp_path)
    unrelated = tmp_path / "Unrelated.app" / ".quarantine"
    unrelated.parent.mkdir()
    unrelated.write_text("keep", encoding="utf-8")

    result = _run(package, env, "--remove-quarantine")

    assert result.returncode == 0, result.stderr
    installed = Path(env["HOME"]) / "Applications" / "WebJam.app"
    assert not (installed / ".quarantine").exists()
    assert unrelated.is_file()
    xattr_log = Path(env["FAKE_XATTR_LOG"]).read_text(encoding="utf-8")
    assert f"-dr com.apple.quarantine {installed}" in xattr_log
    assert str(unrelated.parent) not in xattr_log


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("missing-app", "must be beside this helper"),
        ("wrong-version", "packaged version does not match"),
        ("wrong-build", "packaged build ID does not match"),
        ("wrong-architecture", "download the Apple-silicon"),
        ("invalid-signature", "bundle signature is invalid"),
    ),
)
def test_candidate_validation_fails_closed(
    tmp_path: Path, mutation: str, message: str
) -> None:
    package, env = _make_package(tmp_path)
    if mutation == "missing-app":
        shutil.rmtree(package / "WebJam.app")
    elif mutation == "wrong-version":
        env["FAKE_VERSION"] = "0.18.0"
    elif mutation == "wrong-build":
        (package / "WebJam.app" / "Contents" / "Resources" / "webjam-build-id.txt").write_text(
            f"{'c' * 40}\n", encoding="utf-8"
        )
    elif mutation == "wrong-architecture":
        info = package / "WebJam Candidate Info.txt"
        info.write_text(
            info.read_text(encoding="utf-8")
            .replace("target=macos-arm64", "target=macos-x64")
            .replace("architecture=arm64", "architecture=x86_64"),
            encoding="utf-8",
        )
        env["FAKE_ARCH"] = "x86_64"
    elif mutation == "invalid-signature":
        env["FAKE_CODESIGN_FAIL"] = "1"

    result = _run(package, env)

    assert result.returncode != 0
    assert message in result.stderr


def test_existing_install_is_replaced_after_staged_validation(tmp_path: Path) -> None:
    package, env = _make_package(tmp_path)
    destination = tmp_path / "Custom Applications" / "WebJam.app"
    destination.mkdir(parents=True)
    (destination / "old-version.txt").write_text("old", encoding="utf-8")
    env["WEBJAM_INSTALL_DESTINATION"] = str(destination)

    result = _run(package, env)

    assert result.returncode == 0, result.stderr
    assert not (destination / "old-version.txt").exists()
    assert (destination / "Contents" / "MacOS" / "WebJam").is_file()
    assert not list(destination.parent.glob(".WebJam.backup.*.app"))


def test_existing_install_is_restored_after_post_backup_failure(tmp_path: Path) -> None:
    package, env = _make_package(tmp_path)
    destination = tmp_path / "Custom Applications" / "WebJam.app"
    destination.mkdir(parents=True)
    sentinel = destination / "old-version.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    env["WEBJAM_INSTALL_DESTINATION"] = str(destination)
    env["WEBJAM_INSTALL_FAIL_AFTER_BACKUP"] = "1"

    result = _run(package, env)

    assert result.returncode != 0
    assert "injected post-backup failure" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert not list(destination.parent.glob(".WebJam.backup.*.app"))


def test_helpers_are_executable_and_never_disable_global_gatekeeper() -> None:
    assert os.access(INSTALLER, os.X_OK)
    assert os.access(ADVANCED, os.X_OK)
    source = INSTALLER.read_text(encoding="utf-8")
    wrapper = ADVANCED.read_text(encoding="utf-8")
    assert "sudo" not in source
    assert "spctl" not in source
    assert "--master-disable" not in source
    assert '"$XATTR_BIN" -dr com.apple.quarantine "$destination"' in source
    assert "--remove-quarantine" in wrapper
