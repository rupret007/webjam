"""Detect the native Webex app and hand users to Cisco's official installer.

WebJam does not redistribute, silently install, authenticate, or update Webex.
Cisco owns the proprietary application and its automatic updater.  This module
only verifies a locally installed Mac copy or opens an architecture-correct
official Cisco HTTPS installer URL after an explicit user action.
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys
import time
from typing import Callable, Iterable
from urllib.parse import urlsplit
import webbrowser


WEBEX_MAC_TEAM_ID = "DE8Y96K9QP"
WEBEX_MAC_BUNDLE_ID = "Cisco-Systems.Spark"
WEBEX_MAC_PROCESS_REQUIREMENT = (
    f'identifier "{WEBEX_MAC_BUNDLE_ID}" and anchor apple generic and '
    f'certificate leaf[subject.OU] = "{WEBEX_MAC_TEAM_ID}"'
)
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


class WebexActivationState(str, Enum):
    """Finite, privacy-safe outcomes for the explicit Show Webex App action."""

    ACTIVATED_RUNNING = "activated-running"
    REFUSED = "refused"
    FAILED = "failed"


_WEBEX_ACTIVATION_REASON_CODES = frozenset(
    {
        "",
        "activation-cancelled",
        "activation-exception",
        "ambiguous-running-instances",
        "app-not-running",
        "application-path-unverified",
        "invalid-activation-result",
        "native-activation-failed",
        "native-activation-unavailable",
        "process-publisher-unverified",
        "reverification-failed",
        "reverification-refused",
        "running-target-mismatch",
        "target-invalid",
        "verified-app-unavailable",
    }
)


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


@dataclass(frozen=True, slots=True)
class WebexActivationResult:
    """Truthful result that never carries a URL, meeting identity, or path."""

    state: WebexActivationState
    reason_code: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.state, WebexActivationState):
            raise TypeError("state must be a WebexActivationState")
        if self.reason_code not in _WEBEX_ACTIVATION_REASON_CODES:
            raise ValueError("unsupported Webex activation reason code")

    @property
    def succeeded(self) -> bool:
        return self.state is WebexActivationState.ACTIVATED_RUNNING

    def __bool__(self) -> bool:
        """Retain safe truthiness for one-cycle compatibility callers."""

        return self.succeeded

    def to_public_dict(self) -> dict[str, str]:
        result = {"state": self.state.value}
        if self.reason_code:
            result["reason_code"] = self.reason_code
        return result


class WebexAppError(RuntimeError):
    pass


CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]
WebexDetector = Callable[..., WebexAppInfo]
MacApplicationRuntimeFactory = Callable[[], "_MacOSApplicationRuntime"]
CancellationPredicate = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class _MacRunningApplication:
    """One AppKit object retained only inside its autorelease-pool session."""

    native_handle: int
    process_identifier: int
    path: Path | None


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


class _MacOSApplicationRuntime:
    """Small AppKit bridge that activates one exact bundle without Apple Events.

    PyObjC is intentionally not a runtime dependency.  These calls use public
    ``NSRunningApplication`` APIs directly, so showing Webex neither launches
    an application nor sends a URL/open-document event nor requests Automation
    permission. Every activation session owns one autorelease pool so the
    process identifier, path, and activation all refer to the same AppKit
    object.
    """

    _ID = ctypes.c_void_p
    _SEL = ctypes.c_void_p
    _ACTIVATE_ALL_WINDOWS = 1
    _ACTIVATE_IGNORING_OTHER_APPS = 2

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise WebexAppError("native Webex activation is unavailable")
        try:
            self._objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
            ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/AppKit.framework/AppKit"
            )
            self._objc.objc_getClass.restype = self._ID
            self._objc.objc_getClass.argtypes = [ctypes.c_char_p]
            self._objc.sel_registerName.restype = self._SEL
            self._objc.sel_registerName.argtypes = [ctypes.c_char_p]
            address = ctypes.cast(
                self._objc.objc_msgSend,
                ctypes.c_void_p,
            ).value
            if not address:
                raise OSError("objc_msgSend is unavailable")
        except (OSError, AttributeError) as exc:
            raise WebexAppError("native Webex activation is unavailable") from exc

        send = lambda signature: signature(address)  # noqa: E731
        self._send_id = send(ctypes.CFUNCTYPE(self._ID, self._ID, self._SEL))
        self._send_id_id = send(
            ctypes.CFUNCTYPE(self._ID, self._ID, self._SEL, self._ID)
        )
        self._send_id_cstr = send(
            ctypes.CFUNCTYPE(
                self._ID,
                self._ID,
                self._SEL,
                ctypes.c_char_p,
            )
        )
        self._send_id_ulong = send(
            ctypes.CFUNCTYPE(
                self._ID,
                self._ID,
                self._SEL,
                ctypes.c_ulong,
            )
        )
        self._send_ulong = send(
            ctypes.CFUNCTYPE(ctypes.c_ulong, self._ID, self._SEL)
        )
        self._send_bool_ulong = send(
            ctypes.CFUNCTYPE(
                ctypes.c_bool,
                self._ID,
                self._SEL,
                ctypes.c_ulong,
            )
        )
        self._send_bool = send(
            ctypes.CFUNCTYPE(ctypes.c_bool, self._ID, self._SEL)
        )
        self._send_int = send(
            ctypes.CFUNCTYPE(ctypes.c_int, self._ID, self._SEL)
        )
        self._send_cstr = send(
            ctypes.CFUNCTYPE(ctypes.c_char_p, self._ID, self._SEL)
        )
        self._send_void = send(
            ctypes.CFUNCTYPE(None, self._ID, self._SEL)
        )

    def _class(self, name: str) -> int:
        value = self._objc.objc_getClass(name.encode("ascii"))
        if not value:
            raise WebexAppError("native Webex activation is unavailable")
        return value

    def _selector(self, name: str) -> int:
        value = self._objc.sel_registerName(name.encode("ascii"))
        if not value:
            raise WebexAppError("native Webex activation is unavailable")
        return value

    @contextmanager
    def _pool(self):
        pool = self._send_id(
            self._send_id(
                self._class("NSAutoreleasePool"),
                self._selector("alloc"),
            ),
            self._selector("init"),
        )
        if not pool:
            raise WebexAppError("native Webex activation is unavailable")
        try:
            yield
        finally:
            self._send_void(pool, self._selector("drain"))

    def _ns_string(self, value: str) -> int:
        result = self._send_id_cstr(
            self._class("NSString"),
            self._selector("stringWithUTF8String:"),
            os.fsencode(value),
        )
        if not result:
            raise WebexAppError("native Webex activation is unavailable")
        return result

    def _application_path(self, application: int) -> Path | None:
        bundle_url = self._send_id(
            application,
            self._selector("bundleURL"),
        )
        if not bundle_url:
            return None
        path_string = self._send_id(bundle_url, self._selector("path"))
        if not path_string:
            return None
        raw = self._send_cstr(path_string, self._selector("UTF8String"))
        if not raw:
            return None
        try:
            return Path(os.fsdecode(raw))
        except (TypeError, ValueError):
            return None

    def _running_applications(self, bundle_identifier: str) -> list[int]:
        applications = self._send_id_id(
            self._class("NSRunningApplication"),
            self._selector("runningApplicationsWithBundleIdentifier:"),
            self._ns_string(bundle_identifier),
        )
        if not applications:
            return []
        count = int(self._send_ulong(applications, self._selector("count")))
        if count > 64:
            raise WebexAppError("native Webex activation result was invalid")
        return [
            self._send_id_ulong(
                applications,
                self._selector("objectAtIndex:"),
                index,
            )
            for index in range(count)
        ]

    @contextmanager
    def activation_session(self):
        """Keep enumerated NSRunningApplication objects valid through activation."""

        with self._pool():
            yield self

    def running_applications(
        self,
        bundle_identifier: str,
    ) -> tuple[_MacRunningApplication, ...]:
        return tuple(
            _MacRunningApplication(
                native_handle=application,
                process_identifier=int(
                    self._send_int(
                        application,
                        self._selector("processIdentifier"),
                    )
                ),
                path=self._application_path(application),
            )
            for application in self._running_applications(bundle_identifier)
        )

    def running_paths(self, bundle_identifier: str) -> tuple[Path, ...]:
        with self._pool():
            return tuple(
                application.path
                for application in self.running_applications(bundle_identifier)
                if application.path is not None
            )

    def activate(self, application: _MacRunningApplication) -> bool:
        """Activate the same running object that passed PID verification."""

        return self._activate(application.native_handle)

    def _activate(self, application: int) -> bool:
        # Unhide and activation are conservative app-level requests. AppKit can
        # prove that the exact process became active, but it cannot prove that
        # a minimized Webex window became visible.
        self._send_void(application, self._selector("unhide"))
        options = (
            self._ACTIVATE_ALL_WINDOWS
            | self._ACTIVATE_IGNORING_OTHER_APPS
        )
        accepted = bool(
            self._send_bool_ulong(
                application,
                self._selector("activateWithOptions:"),
                options,
            )
        )
        if not accepted:
            return False
        # AppKit documents the return value as request acceptance rather than
        # proof of foreground state. Confirm the exact running application
        # became active before reporting ACTIVATED_RUNNING to the controller.
        for _attempt in range(20):
            if self._send_bool(application, self._selector("isActive")):
                return True
            time.sleep(0.05)
        return False


def _same_application_path(left: Path, right: Path) -> bool:
    """Compare live and disk bundle identity without a textual fallback."""

    return os.path.samefile(left, right)


def _activation_cancelled(
    predicate: CancellationPredicate | None,
) -> bool:
    if predicate is None:
        return False
    try:
        return bool(predicate())
    except Exception:
        # Cancellation state is a safety boundary. An unreadable state fails
        # closed rather than activating a third-party process.
        return True


def _verify_running_webex_process(
    process_identifier: int,
    *,
    command_runner: CommandRunner,
) -> bool:
    """Validate the exact running PID against Cisco's designated requirement."""

    if (
        not isinstance(process_identifier, int)
        or isinstance(process_identifier, bool)
        or not 1 <= process_identifier <= 2**31 - 1
    ):
        return False
    arguments = [
        "/usr/bin/codesign",
        "--verify",
        "--strict",
        "--verbose=2",
        f"-R={WEBEX_MAC_PROCESS_REQUIREMENT}",
        str(process_identifier),
    ]
    try:
        result = command_runner(arguments, timeout=30.0)
    except Exception:
        return False
    # Captured verifier text is intentionally discarded. Bound it first so a
    # pathological tool response cannot enter diagnostics or consume memory
    # unchecked after capture.
    output_size = len(bytes(result.stdout or b"")) + len(
        bytes(result.stderr or b"")
    )
    return result.returncode == 0 and output_size <= 64 * 1024


def _show_verified_running_macos_app(
    candidate: Path,
    *,
    runtime_factory: MacApplicationRuntimeFactory | None = None,
    command_runner: CommandRunner = _run_command,
    cancelled: CancellationPredicate | None = None,
) -> WebexActivationResult:
    runtime = (
        runtime_factory()
        if runtime_factory is not None
        else _MacOSApplicationRuntime()
    )
    with runtime.activation_session() as active_runtime:
        applications = active_runtime.running_applications(
            WEBEX_MAC_BUNDLE_ID
        )
        if not applications:
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "app-not-running",
            )
        if len(applications) != 1:
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "ambiguous-running-instances",
            )
        application = applications[0]
        if application.path is None:
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "application-path-unverified",
            )
        try:
            same_path = _same_application_path(application.path, candidate)
        except OSError:
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "application-path-unverified",
            )
        if not same_path:
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "running-target-mismatch",
            )
        if _activation_cancelled(cancelled):
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "activation-cancelled",
            )
        if not _verify_running_webex_process(
            application.process_identifier,
            command_runner=command_runner,
        ):
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "process-publisher-unverified",
            )
        if _activation_cancelled(cancelled):
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "activation-cancelled",
            )
        if active_runtime.activate(application):
            return WebexActivationResult(
                WebexActivationState.ACTIVATED_RUNNING
            )
        return WebexActivationResult(
            WebexActivationState.FAILED,
            "native-activation-failed",
        )


def show_webex_app(
    info: WebexAppInfo,
    *,
    platform_name: str | None = None,
    detector: WebexDetector | None = None,
    mac_runtime_factory: MacApplicationRuntimeFactory | None = None,
    command_runner: CommandRunner = _run_command,
    cancelled: CancellationPredicate | None = None,
) -> WebexActivationResult:
    """Show the exact verified Webex app without handing off a meeting URL.

    The caller must supply the result of :func:`detect_webex_app`; missing,
    invalid, stale, or unverified macOS applications fail closed. Paths are
    never sent to a shell. On macOS only one already-running exact bundle can
    be activated. A stopped application is never launched by this action.
    """

    platform_value = (platform_name or sys.platform).strip().lower()
    path = info.path
    if (
        info.state is not WebexAppState.INSTALLED
        or path is None
        or not info.publisher_verified
    ):
        return WebexActivationResult(
            WebexActivationState.REFUSED,
            "verified-app-unavailable",
        )
    candidate = Path(path)
    try:
        if candidate.is_symlink():
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "target-invalid",
            )
        if platform_value == "darwin":
            if not info.publisher_verified or not candidate.is_dir():
                return WebexActivationResult(
                    WebexActivationState.REFUSED,
                    "target-invalid",
                )
        else:
            # Windows remains deliberately unavailable until Authenticode
            # publisher verification and exact foreground activation are both
            # implemented. A detected executable is never enough.
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "native-activation-unavailable",
            )
    except OSError:
        return WebexActivationResult(
            WebexActivationState.REFUSED,
            "target-invalid",
        )

    # Detection normally happens at startup, but an application can be
    # replaced at the same path afterward. Re-run the complete platform
    # identity/publisher check immediately before activation and require the
    # verified target to be the exact path the UI displayed.
    fresh_detector = detector or detect_webex_app
    try:
        fresh = fresh_detector(platform_name=platform_value)
    except Exception:
        return WebexActivationResult(
            WebexActivationState.FAILED,
            "reverification-failed",
        )
    fresh_path = getattr(fresh, "path", None)
    if (
        getattr(fresh, "state", None) is not WebexAppState.INSTALLED
        or fresh_path is None
        or not bool(getattr(fresh, "publisher_verified", False))
    ):
        return WebexActivationResult(
            WebexActivationState.REFUSED,
            "reverification-refused",
        )
    try:
        fresh_matches = _same_application_path(Path(fresh_path), candidate)
    except OSError:
        fresh_matches = False
    if not fresh_matches:
        return WebexActivationResult(
            WebexActivationState.REFUSED,
            "reverification-refused",
        )
    if _activation_cancelled(cancelled):
        return WebexActivationResult(
            WebexActivationState.REFUSED,
            "activation-cancelled",
        )
    try:
        return _show_verified_running_macos_app(
            candidate,
            runtime_factory=mac_runtime_factory,
            command_runner=command_runner,
            cancelled=cancelled,
        )
    except Exception as exc:  # noqa: BLE001 - activation is best effort
        raise WebexAppError("the installed Webex app could not be activated") from exc


def bring_webex_forward(
    info: WebexAppInfo,
    **kwargs,
) -> WebexActivationResult:
    """Compatibility alias for :func:`show_webex_app`."""

    return show_webex_app(info, **kwargs)


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
    "WEBEX_MAC_PROCESS_REQUIREMENT",
    "WEBEX_MAC_TEAM_ID",
    "WebexActivationResult",
    "WebexActivationState",
    "WebexAppError",
    "WebexAppInfo",
    "WebexAppState",
    "bring_webex_forward",
    "detect_webex_app",
    "open_official_webex_installer",
    "show_webex_app",
    "webex_installer_url",
]
