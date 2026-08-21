"""Detect, safely show, or hand off installation of the native Webex app.

WebJam does not redistribute, silently install, authenticate, or update Webex.
Cisco owns the proprietary application and its automatic updater.  This module
verifies a locally installed Mac copy, asks NSWorkspace to show an
identity-bound reference without a URL/document argument, or opens an
architecture-correct
official Cisco HTTPS installer URL after an explicit user action.
"""

from __future__ import annotations

import ctypes
import os
import platform
import plistlib
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

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
    LAUNCHED_APP = "launched-app"
    REFUSED = "refused"
    FAILED = "failed"


_WEBEX_ACTIVATION_REASON_CODES = frozenset(
    {
        "",
        "activation-cancelled",
        "activation-exception",
        "ambiguous-running-instances",
        "app-not-running",
        "application-reference-unverified",
        "application-path-unverified",
        "invalid-activation-result",
        "native-activation-failed",
        "native-activation-unavailable",
        "native-launch-failed",
        "native-launch-unconfirmed",
        "process-publisher-unverified",
        "reverification-failed",
        "reverification-refused",
        "running-target-changed",
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
        return self.state in {
            WebexActivationState.ACTIVATED_RUNNING,
            WebexActivationState.LAUNCHED_APP,
        }

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
MacPathMatcher = Callable[[Path], bool]

_WEBEX_LAUNCH_CONFIRM_TIMEOUT_SECONDS = 15.0
_WEBEX_LAUNCH_POLL_INTERVAL_SECONDS = 0.1
_MAX_COMMAND_OUTPUT_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class _MacRunningApplication:
    """One AppKit object retained only inside its autorelease-pool session."""

    native_handle: int
    process_identifier: int
    path: Path | None


@dataclass(frozen=True, slots=True)
class _MacApplicationReference:
    """One retained Core Foundation URL bound to a filesystem object."""

    native_url: int


def _run_command(
    arguments: list[str],
    *,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        arguments,
        capture_output=True,
        timeout=timeout,
        check=False,
        shell=False,
    )


class _MacOSApplicationRuntime:
    """Small AppKit bridge for fresh exact-process foreground observations.

    PyObjC is intentionally not a runtime dependency.  These calls use public
    ``NSRunningApplication`` and ``NSWorkspace`` APIs directly without Apple
    Events or Automation permission. Every observation owns one autorelease
    pool so the process identifier, path, and frontmost state come from a fresh
    AppKit snapshot.
    """

    _ID = ctypes.c_void_p
    _SEL = ctypes.c_void_p
    _CF_STRING_ENCODING_UTF8 = 0x08000100
    _SEC_STATIC_VALIDATION_FLAGS = (
        (1 << 0)  # kSecCSCheckAllArchitectures
        | (1 << 3)  # kSecCSCheckNestedCode
        | (1 << 4)  # kSecCSStrictValidate
        | (1 << 7)  # kSecCSRestrictSymlinks
        | (1 << 8)  # kSecCSRestrictToAppLike
        | (1 << 9)  # kSecCSRestrictSidebandData
    )

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise WebexAppError("native Webex activation is unavailable")
        try:
            self._objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
            self._core_foundation = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/"
                "CoreFoundation.framework/CoreFoundation"
            )
            self._security = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/Security.framework/Security"
            )
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

            self._core_foundation.CFURLCreateFromFileSystemRepresentation.restype = (
                self._ID
            )
            self._core_foundation.CFURLCreateFromFileSystemRepresentation.argtypes = [
                self._ID,
                ctypes.c_char_p,
                ctypes.c_long,
                ctypes.c_bool,
            ]
            self._core_foundation.CFURLCreateFileReferenceURL.restype = self._ID
            self._core_foundation.CFURLCreateFileReferenceURL.argtypes = [
                self._ID,
                self._ID,
                ctypes.POINTER(self._ID),
            ]
            self._core_foundation.CFURLIsFileReferenceURL.restype = ctypes.c_bool
            self._core_foundation.CFURLIsFileReferenceURL.argtypes = [self._ID]
            self._core_foundation.CFEqual.restype = ctypes.c_bool
            self._core_foundation.CFEqual.argtypes = [self._ID, self._ID]
            self._core_foundation.CFStringCreateWithCString.restype = self._ID
            self._core_foundation.CFStringCreateWithCString.argtypes = [
                self._ID,
                ctypes.c_char_p,
                ctypes.c_uint32,
            ]
            self._core_foundation.CFRelease.restype = None
            self._core_foundation.CFRelease.argtypes = [self._ID]

            self._security.SecStaticCodeCreateWithPath.restype = ctypes.c_int32
            self._security.SecStaticCodeCreateWithPath.argtypes = [
                self._ID,
                ctypes.c_uint32,
                ctypes.POINTER(self._ID),
            ]
            self._security.SecRequirementCreateWithString.restype = (
                ctypes.c_int32
            )
            self._security.SecRequirementCreateWithString.argtypes = [
                self._ID,
                ctypes.c_uint32,
                ctypes.POINTER(self._ID),
            ]
            self._security.SecStaticCodeCheckValidity.restype = ctypes.c_int32
            self._security.SecStaticCodeCheckValidity.argtypes = [
                self._ID,
                ctypes.c_uint32,
                self._ID,
            ]
        except (OSError, AttributeError) as exc:
            raise WebexAppError("native Webex activation is unavailable") from exc

        def send(signature):
            return signature(address)

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
        self._send_id_id_ulong_id_ptr = send(
            ctypes.CFUNCTYPE(
                self._ID,
                self._ID,
                self._SEL,
                self._ID,
                ctypes.c_ulong,
                self._ID,
                ctypes.POINTER(self._ID),
            )
        )
        self._send_bool_ulong = send(
            ctypes.CFUNCTYPE(
                ctypes.c_bool,
                self._ID,
                self._SEL,
                ctypes.c_ulong,
            )
        )
        self._send_ulong = send(
            ctypes.CFUNCTYPE(ctypes.c_ulong, self._ID, self._SEL)
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
        """Keep one fresh AppKit observation valid inside its pool."""

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

    # Raise Webex as it already stands.  NSApplicationActivateAllWindows is
    # deliberately NOT set: it would raise every Webex window, so the
    # Messaging window would come forward on top of the meeting -- which is
    # the behaviour musicians reported.  Without it, only the app's own
    # frontmost window rises, and during a call that is the meeting.
    _ACTIVATE_IGNORING_OTHER_APPS = 1 << 1

    def activate_application(
        self, application: _MacRunningApplication
    ) -> bool:
        """Foreground an already-running Webex without a LaunchServices reopen.

        ``NSWorkspace.openApplicationAtURL`` re-opens an app that is already
        running, and a reopen is what makes Webex present its main Messaging
        window.  Activating the exact running process instead leaves Webex's
        own window order alone.
        """

        return bool(
            self._send_bool_ulong(
                application.native_handle,
                self._selector("activateWithOptions:"),
                self._ACTIVATE_IGNORING_OTHER_APPS,
            )
        )

    def is_frontmost(self, application: _MacRunningApplication) -> bool:
        """Prove that a fresh exact Webex snapshot owns the foreground."""

        workspace = self._send_id(
            self._class("NSWorkspace"),
            self._selector("sharedWorkspace"),
        )
        if not workspace:
            return False
        frontmost = self._send_id(
            workspace,
            self._selector("frontmostApplication"),
        )
        if not frontmost:
            return False
        frontmost_pid = int(
            self._send_int(
                frontmost,
                self._selector("processIdentifier"),
            )
        )
        return frontmost_pid == application.process_identifier

    def _create_file_reference(self, path: Path) -> _MacApplicationReference:
        encoded = os.fsencode(path)
        path_url = self._core_foundation.CFURLCreateFromFileSystemRepresentation(
            None,
            encoded,
            len(encoded),
            True,
        )
        if not path_url:
            raise WebexAppError("native Webex target reference is unavailable")
        error = self._ID()
        try:
            reference_url = (
                self._core_foundation.CFURLCreateFileReferenceURL(
                    None,
                    path_url,
                    ctypes.byref(error),
                )
            )
        finally:
            self._core_foundation.CFRelease(path_url)
        if error:
            self._core_foundation.CFRelease(error)
        if (
            not reference_url
            or not self._core_foundation.CFURLIsFileReferenceURL(
                reference_url
            )
        ):
            if reference_url:
                self._core_foundation.CFRelease(reference_url)
            raise WebexAppError("native Webex target reference is unavailable")
        return _MacApplicationReference(native_url=reference_url)

    @contextmanager
    def application_reference(self, path: Path):
        """Retain an identity-bound URL through verification and launch."""

        reference = self._create_file_reference(path)
        try:
            yield reference
        finally:
            self._core_foundation.CFRelease(reference.native_url)

    def application_reference_matches_path(
        self,
        reference: _MacApplicationReference,
        path: Path,
    ) -> bool:
        """Compare a live app path with the retained filesystem object."""

        try:
            other = self._create_file_reference(path)
        except Exception:
            return False
        try:
            return bool(
                self._core_foundation.CFEqual(
                    reference.native_url,
                    other.native_url,
                )
            )
        finally:
            self._core_foundation.CFRelease(other.native_url)

    def verify_application_reference(
        self,
        reference: _MacApplicationReference,
    ) -> bool:
        """Validate the identity-bound bundle against Cisco's requirement."""

        static_code = self._ID()
        requirement = self._ID()
        requirement_text = (
            self._core_foundation.CFStringCreateWithCString(
                None,
                WEBEX_MAC_PROCESS_REQUIREMENT.encode("utf-8"),
                self._CF_STRING_ENCODING_UTF8,
            )
        )
        if not requirement_text:
            return False
        try:
            if (
                self._security.SecStaticCodeCreateWithPath(
                    reference.native_url,
                    0,
                    ctypes.byref(static_code),
                )
                != 0
                or not static_code
            ):
                return False
            if (
                self._security.SecRequirementCreateWithString(
                    requirement_text,
                    0,
                    ctypes.byref(requirement),
                )
                != 0
                or not requirement
            ):
                return False
            return (
                self._security.SecStaticCodeCheckValidity(
                    static_code,
                    self._SEC_STATIC_VALIDATION_FLAGS,
                    requirement,
                )
                == 0
            )
        finally:
            if requirement:
                self._core_foundation.CFRelease(requirement)
            if static_code:
                self._core_foundation.CFRelease(static_code)
            self._core_foundation.CFRelease(requirement_text)

    def launch_application(
        self,
        reference: _MacApplicationReference,
    ) -> int | None:
        """Launch or reopen the bound app URL with no document or URL input."""

        with self._pool():
            workspace = self._send_id(
                self._class("NSWorkspace"),
                self._selector("sharedWorkspace"),
            )
            configuration = self._send_id(
                self._class("NSDictionary"),
                self._selector("dictionary"),
            )
            if not workspace or not configuration:
                return None
            error = self._ID()
            application = self._send_id_id_ulong_id_ptr(
                workspace,
                self._selector(
                    "launchApplicationAtURL:options:configuration:error:"
                ),
                reference.native_url,
                0,
                configuration,
                ctypes.byref(error),
            )
            if not application or error:
                return None
            process_identifier = int(
                self._send_int(
                    application,
                    self._selector("processIdentifier"),
                )
            )
            if not 1 <= process_identifier <= 2**31 - 1:
                return None
            return process_identifier


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
    return result.returncode == 0 and output_size <= _MAX_COMMAND_OUTPUT_BYTES


def _select_exact_running_macos_app(
    applications: tuple[_MacRunningApplication, ...],
    path_matches: MacPathMatcher,
) -> tuple[_MacRunningApplication | None, WebexActivationResult | None]:
    """Select one running app whose path resolves to the bound object."""

    if not applications:
        return None, None
    if len(applications) != 1:
        return None, WebexActivationResult(
            WebexActivationState.REFUSED,
            "ambiguous-running-instances",
        )
    application = applications[0]
    if application.path is None:
        return None, WebexActivationResult(
            WebexActivationState.REFUSED,
            "application-path-unverified",
        )
    try:
        same_path = bool(path_matches(application.path))
    except Exception:
        return None, WebexActivationResult(
            WebexActivationState.REFUSED,
            "application-path-unverified",
        )
    if not same_path:
        return None, WebexActivationResult(
            WebexActivationState.REFUSED,
            "running-target-mismatch",
        )
    return application, None


def _show_or_launch_verified_macos_app(
    candidate: Path,
    *,
    runtime_factory: MacApplicationRuntimeFactory | None = None,
    command_runner: CommandRunner = _run_command,
    cancelled: CancellationPredicate | None = None,
) -> WebexActivationResult:
    """Launch the bound Cisco object, then prove its foreground process.

    A retained Core Foundation file-reference URL survives path replacement
    and is both code-validated and passed directly to NSWorkspace. This binds
    verification and launch to one filesystem object rather than a mutable
    pathname. NSWorkspace's returned process is only request acceptance; fresh
    exact-path, PID-publisher, and foreground postconditions still decide
    success.
    """

    runtime = (
        runtime_factory()
        if runtime_factory is not None
        else _MacOSApplicationRuntime()
    )
    with runtime.application_reference(candidate) as reference:
        if _activation_cancelled(cancelled):
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "activation-cancelled",
            )
        if not runtime.verify_application_reference(reference):
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "application-reference-unverified",
            )

        def path_matches(path):
            return runtime.application_reference_matches_path(reference, path)

        initial_pid: int | None = None
        with runtime.activation_session() as active_runtime:
            applications = active_runtime.running_applications(
                WEBEX_MAC_BUNDLE_ID
            )
            application, failure = _select_exact_running_macos_app(
                applications,
                path_matches,
            )
            if failure is not None:
                return failure
            was_stopped = application is None
            if application is not None:
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
                initial_pid = application.process_identifier

        if _activation_cancelled(cancelled):
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "activation-cancelled",
            )
        if initial_pid is not None:
            # Webex is already running, which is the case during a jam.
            # Activate the exact verified process rather than asking
            # LaunchServices to reopen it: a reopen can re-present app state,
            # while activation leaves Webex's own window order alone.
            #
            # Measured limitation, not a fix for the reported symptom: both
            # paths raise the *application*, so whichever window Webex has in
            # front is what the musician sees. With Messaging in front they
            # still get Messaging. Raising the meeting window specifically
            # needs macOS Accessibility to enumerate Webex's windows and
            # AXRaise the meeting; until that exists this button cannot
            # promise the meeting.
            with runtime.activation_session() as active_runtime:
                applications = active_runtime.running_applications(
                    WEBEX_MAC_BUNDLE_ID
                )
                application, failure = _select_exact_running_macos_app(
                    applications,
                    path_matches,
                )
                if failure is not None:
                    return failure
                if (
                    application is None
                    or application.process_identifier != initial_pid
                ):
                    return WebexActivationResult(
                        WebexActivationState.REFUSED,
                        "running-target-changed",
                    )
                if not active_runtime.activate_application(application):
                    return WebexActivationResult(
                        WebexActivationState.FAILED,
                        "native-activation-failed",
                    )
            launched_pid = initial_pid
        else:
            launched_pid = runtime.launch_application(reference)
            if launched_pid is None:
                return WebexActivationResult(
                    WebexActivationState.FAILED,
                    "native-launch-failed",
                )
        if _activation_cancelled(cancelled):
            return WebexActivationResult(
                WebexActivationState.REFUSED,
                "activation-cancelled",
            )

        verified_post_pid: int | None = None
        deadline = (
            time.monotonic() + _WEBEX_LAUNCH_CONFIRM_TIMEOUT_SECONDS
        )
        while True:
            if _activation_cancelled(cancelled):
                return WebexActivationResult(
                    WebexActivationState.REFUSED,
                    "activation-cancelled",
                )
            with runtime.activation_session() as active_runtime:
                applications = active_runtime.running_applications(
                    WEBEX_MAC_BUNDLE_ID
                )
                application, failure = _select_exact_running_macos_app(
                    applications,
                    path_matches,
                )
                if failure is not None:
                    return failure
                if application is not None:
                    process_identifier = application.process_identifier
                    if process_identifier != launched_pid:
                        return WebexActivationResult(
                            WebexActivationState.REFUSED,
                            "running-target-changed",
                        )
                    if verified_post_pid != process_identifier:
                        if _activation_cancelled(cancelled):
                            return WebexActivationResult(
                                WebexActivationState.REFUSED,
                                "activation-cancelled",
                            )
                        if not _verify_running_webex_process(
                            process_identifier,
                            command_runner=command_runner,
                        ):
                            return WebexActivationResult(
                                WebexActivationState.REFUSED,
                                "process-publisher-unverified",
                            )
                        verified_post_pid = process_identifier
                    if active_runtime.is_frontmost(application):
                        foreground_pid = process_identifier
                    else:
                        foreground_pid = None
                else:
                    foreground_pid = None
            if foreground_pid is not None:
                if _activation_cancelled(cancelled):
                    return WebexActivationResult(
                        WebexActivationState.REFUSED,
                        "activation-cancelled",
                    )
                if not _verify_running_webex_process(
                    foreground_pid,
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
                with runtime.activation_session() as final_runtime:
                    final_applications = final_runtime.running_applications(
                        WEBEX_MAC_BUNDLE_ID
                    )
                    final_application, failure = (
                        _select_exact_running_macos_app(
                            final_applications,
                            path_matches,
                        )
                    )
                    if failure is not None:
                        return failure
                    if final_application is None:
                        return WebexActivationResult(
                            WebexActivationState.FAILED,
                            "native-launch-unconfirmed",
                        )
                    if (
                        final_application.process_identifier
                        != foreground_pid
                    ):
                        return WebexActivationResult(
                            WebexActivationState.REFUSED,
                            "running-target-changed",
                        )
                    if not final_runtime.is_frontmost(final_application):
                        return WebexActivationResult(
                            WebexActivationState.FAILED,
                            "native-launch-unconfirmed",
                        )
                state = (
                    WebexActivationState.LAUNCHED_APP
                    if was_stopped
                    else WebexActivationState.ACTIVATED_RUNNING
                )
                return WebexActivationResult(state)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(
                min(_WEBEX_LAUNCH_POLL_INTERVAL_SECONDS, remaining)
            )

        return WebexActivationResult(
            WebexActivationState.FAILED,
            "native-launch-unconfirmed",
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
    never sent to a shell. On macOS, one exact running bundle is activated; if
    Webex is stopped, NSWorkspace launches that identity-bound app reference
    without a URL and WebJam then verifies the exact running publisher and
    foreground state.
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
        return _show_or_launch_verified_macos_app(
            candidate,
            runtime_factory=mac_runtime_factory,
            command_runner=command_runner,
            cancelled=cancelled,
        )
    except Exception:  # noqa: BLE001 - external app showing is best effort
        return WebexActivationResult(
            WebexActivationState.FAILED,
            "activation-exception",
        )


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
    except Exception as exc:
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
