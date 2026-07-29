"""Detect the native Webex app and hand users to Cisco's official installer.

WebJam does not redistribute, silently install, authenticate, or update Webex.
Cisco owns the proprietary application and its automatic updater.  This module
only verifies a locally installed Mac copy or opens an architecture-correct
official Cisco HTTPS installer URL after an explicit user action.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
from typing import Callable, Iterable
from urllib.parse import urlsplit
import webbrowser


WEBEX_MAC_TEAM_ID = "DE8Y96K9QP"
WEBEX_MAC_BUNDLE_ID = "Cisco-Systems.Spark"
WEBEX_DOWNLOAD_PAGE = "https://www.webex.com/downloads.html"
WEBEX_INSTALLER_URLS = {
    "macos-arm64": (
        "https://binaries.webex.com/webex-macos-apple-silicon/Webex.pkg"
    ),
    "macos-x64": "https://binaries.webex.com/webex-macos-intel/Webex.pkg",
    "windows-x64": (
        "https://binaries.webex.com/"
        "WebexOfclDesktop-Win-64-Gold/Webex.msi"
    ),
}


class WebexAppState(str, Enum):
    INSTALLED = "installed"
    NOT_INSTALLED = "not-installed"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class WebexAppInfo:
    state: WebexAppState
    version: str = ""
    publisher_verified: bool = False
    path: Path | None = None
    reason_code: str = ""

    def to_public_dict(self) -> dict[str, object]:
        """Return diagnostics facts without the local application path."""

        return {
            "state": self.state.value,
            "version": self.version,
            "publisher_verified": self.publisher_verified,
            "reason_code": self.reason_code,
        }


class WebexAppError(RuntimeError):
    pass


CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]


def _run_command(
    arguments: list[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        arguments,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        shell=False,
    )


def webex_installer_url(
    *,
    platform_name: str | None = None,
    machine: str | None = None,
) -> str:
    platform_value = (platform_name or sys.platform).strip().lower()
    machine_value = (machine or platform.machine()).strip().lower()
    if platform_value == "darwin":
        key = (
            "macos-arm64"
            if machine_value in {"arm64", "aarch64"}
            else "macos-x64"
            if machine_value in {"x86_64", "amd64"}
            else ""
        )
    elif platform_value == "win32" and machine_value in {
        "x86_64",
        "amd64",
    }:
        key = "windows-x64"
    else:
        key = ""
    url = WEBEX_INSTALLER_URLS.get(key, WEBEX_DOWNLOAD_PAGE)
    _require_official_webex_url(url)
    return url


def open_official_webex_installer(
    *,
    platform_name: str | None = None,
    machine: str | None = None,
    opener: Callable[[str], bool] = webbrowser.open,
) -> bool:
    """Open Cisco's installer URL; never download or execute it silently."""

    url = webex_installer_url(
        platform_name=platform_name,
        machine=machine,
    )
    try:
        return bool(opener(url))
    except Exception as exc:  # noqa: BLE001 - browser handoff is best effort
        raise WebexAppError(
            "the official Cisco Webex installer could not be opened"
        ) from exc


def detect_webex_app(
    *,
    platform_name: str | None = None,
    home: str | Path | None = None,
    environ: dict[str, str] | None = None,
    command_runner: CommandRunner = _run_command,
) -> WebexAppInfo:
    platform_value = (platform_name or sys.platform).strip().lower()
    home_path = Path(home) if home is not None else Path.home()
    environment = os.environ if environ is None else environ
    if platform_value == "darwin":
        return _detect_macos_webex(
            (
                Path("/Applications/Webex.app"),
                home_path / "Applications" / "Webex.app",
            ),
            command_runner=command_runner,
        )
    if platform_value == "win32":
        local = str(environment.get("LOCALAPPDATA", "") or "").strip()
        program_files = str(environment.get("ProgramFiles", "") or "").strip()
        candidates = []
        if local:
            candidates.append(
                Path(local)
                / "Programs"
                / "Cisco Spark"
                / "CiscoCollabHost.exe"
            )
        if program_files:
            candidates.append(
                Path(program_files)
                / "Cisco Spark"
                / "CiscoCollabHost.exe"
            )
        return _detect_regular_executable(candidates)
    if platform_value.startswith("linux"):
        return _detect_regular_executable(
            (
                Path("/opt/Webex/bin/CiscoCollabHost"),
                Path("/usr/bin/webex"),
            )
        )
    return WebexAppInfo(
        state=WebexAppState.UNSUPPORTED,
        reason_code="unsupported-platform",
    )


def _detect_macos_webex(
    candidates: Iterable[Path],
    *,
    command_runner: CommandRunner,
) -> WebexAppInfo:
    for candidate in candidates:
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        info_path = candidate / "Contents" / "Info.plist"
        executable = candidate / "Contents" / "MacOS" / "Webex"
        try:
            info = plistlib.loads(info_path.read_bytes())
        except (OSError, plistlib.InvalidFileException):
            return WebexAppInfo(
                state=WebexAppState.INVALID,
                reason_code="metadata-invalid",
            )
        if (
            not isinstance(info, dict)
            or str(info.get("CFBundleIdentifier", "")) != WEBEX_MAC_BUNDLE_ID
            or not executable.is_file()
            or executable.is_symlink()
            or not os.access(executable, os.X_OK)
        ):
            return WebexAppInfo(
                state=WebexAppState.INVALID,
                reason_code="identity-invalid",
            )
        version = str(info.get("CFBundleShortVersionString", "") or "")
        signature = command_runner(
            [
                "/usr/bin/codesign",
                "--verify",
                "--deep",
                "--strict",
                "--verbose=2",
                str(candidate),
            ],
            timeout=60.0,
        )
        details = command_runner(
            ["/usr/bin/codesign", "-d", "--verbose=4", str(candidate)],
            timeout=30.0,
        )
        assessment = command_runner(
            ["/usr/sbin/spctl", "-a", "-vv", "-t", "execute", str(candidate)],
            timeout=60.0,
        )
        output = _bounded_text(details) + "\n" + _bounded_text(assessment)
        publisher_ok = bool(
            signature.returncode == 0
            and details.returncode == 0
            and assessment.returncode == 0
            and f"Identifier={WEBEX_MAC_BUNDLE_ID}" in output
            and f"TeamIdentifier={WEBEX_MAC_TEAM_ID}" in output
            and (
                f"Authority=Developer ID Application: Cisco ({WEBEX_MAC_TEAM_ID})"
                in output
            )
            and "source=Notarized Developer ID" in output
        )
        return WebexAppInfo(
            state=(
                WebexAppState.INSTALLED
                if publisher_ok
                else WebexAppState.INVALID
            ),
            version=version,
            publisher_verified=publisher_ok,
            path=candidate if publisher_ok else None,
            reason_code="" if publisher_ok else "publisher-unverified",
        )
    return WebexAppInfo(state=WebexAppState.NOT_INSTALLED)


def _detect_regular_executable(candidates: Iterable[Path]) -> WebexAppInfo:
    for candidate in candidates:
        try:
            if (
                candidate.is_symlink()
                or not candidate.is_file()
                or (os.name == "posix" and not os.access(candidate, os.X_OK))
            ):
                continue
        except OSError:
            continue
        return WebexAppInfo(
            state=WebexAppState.INSTALLED,
            publisher_verified=False,
            path=candidate,
            reason_code="publisher-check-deferred",
        )
    return WebexAppInfo(state=WebexAppState.NOT_INSTALLED)


def _require_official_webex_url(url: str) -> None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise WebexAppError("the Cisco Webex installer URL is invalid") from exc
    if (
        parts.scheme != "https"
        or parts.hostname not in {"binaries.webex.com", "www.webex.com"}
        or parts.username is not None
        or parts.password is not None
        or port not in {None, 443}
        or parts.fragment
        or not parts.path.startswith("/")
    ):
        raise WebexAppError("the Cisco Webex installer URL is not approved")


def _bounded_text(
    result: subprocess.CompletedProcess[bytes],
    *,
    maximum: int = 64 * 1024,
) -> str:
    raw = bytes(result.stdout or b"") + b"\n" + bytes(result.stderr or b"")
    if len(raw) > maximum:
        raise WebexAppError("Webex publisher verification output was too large")
    return raw.decode("utf-8", errors="replace")


__all__ = [
    "WEBEX_DOWNLOAD_PAGE",
    "WEBEX_INSTALLER_URLS",
    "WEBEX_MAC_BUNDLE_ID",
    "WEBEX_MAC_TEAM_ID",
    "WebexAppError",
    "WebexAppInfo",
    "WebexAppState",
    "detect_webex_app",
    "open_official_webex_installer",
    "webex_installer_url",
]
