"""Activate one already-running macOS application by exact process identity.

This module deliberately has no launch API.  It uses ``NSRunningApplication``
through AppKit so callers can foreground the exact child they already own
without Apple Events, Automation permission, or LaunchServices selecting a
different installed bundle with the same identifier.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class MacOSProcessActivationError(RuntimeError):
    """Raised when the small native AppKit bridge is unavailable."""


class JamulusForegroundReason(str, Enum):
    """Bounded, path-free result of one Jamulus foreground request."""

    NOT_REQUESTED = "not-requested"
    FOREGROUNDED = "foregrounded"
    NOT_RUNNING = "not-running"
    IDENTITY_UNVERIFIED = "identity-unverified"
    NATIVE_ACTIVATION_UNAVAILABLE = "native-activation-unavailable"
    ACTIVATION_REFUSED = "activation-refused"
    FRONTMOST_UNCONFIRMED = "frontmost-unconfirmed"
    PROCESS_CHANGED = "process-changed"
    PLATFORM_NOT_MANAGED = "platform-not-managed"


@dataclass(frozen=True, slots=True)
class JamulusForegroundOutcome:
    """Typed internal result whose public form contains one reason code."""

    foregrounded: bool
    reason: JamulusForegroundReason | str

    def __post_init__(self) -> None:
        if not isinstance(self.foregrounded, bool):
            raise TypeError("foregrounded must be a boolean")
        reason = JamulusForegroundReason(self.reason)
        successful = reason in {
            JamulusForegroundReason.FOREGROUNDED,
            JamulusForegroundReason.PLATFORM_NOT_MANAGED,
        }
        if self.foregrounded is not successful:
            raise ValueError("foreground outcome and reason are inconsistent")
        object.__setattr__(self, "reason", reason)

    @property
    def reason_code(self) -> str:
        return JamulusForegroundReason(self.reason).value

    def __bool__(self) -> bool:
        return self.foregrounded

    def to_public_dict(self) -> dict[str, str]:
        return {"reason_code": self.reason_code}


@dataclass(frozen=True, slots=True)
class MacOSRunningApplication:
    """Fresh AppKit identity for one exact running process."""

    native_handle: int
    process_identifier: int
    bundle_path: Path | None


class MacOSProcessActivationRuntime:
    """Minimal AppKit bridge that can observe and activate one exact PID."""

    _ID = ctypes.c_void_p
    _SEL = ctypes.c_void_p
    _ACTIVATE_ALL_WINDOWS = 1 << 0
    _ACTIVATE_IGNORING_OTHER_APPS = 1 << 1

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise MacOSProcessActivationError(
                "native application activation is unavailable"
            )
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
            raise MacOSProcessActivationError(
                "native application activation is unavailable"
            ) from exc

        def send(signature):
            return signature(address)

        self._send_id = send(ctypes.CFUNCTYPE(self._ID, self._ID, self._SEL))
        self._send_id_int = send(
            ctypes.CFUNCTYPE(self._ID, self._ID, self._SEL, ctypes.c_int)
        )
        self._send_bool_ulong = send(
            ctypes.CFUNCTYPE(
                ctypes.c_bool,
                self._ID,
                self._SEL,
                ctypes.c_ulong,
            )
        )
        self._send_int = send(ctypes.CFUNCTYPE(ctypes.c_int, self._ID, self._SEL))
        self._send_cstr = send(ctypes.CFUNCTYPE(ctypes.c_char_p, self._ID, self._SEL))
        self._send_void = send(ctypes.CFUNCTYPE(None, self._ID, self._SEL))

    def _class(self, name: str) -> int:
        value = self._objc.objc_getClass(name.encode("ascii"))
        if not value:
            raise MacOSProcessActivationError(
                "native application activation is unavailable"
            )
        return value

    def _selector(self, name: str) -> int:
        value = self._objc.sel_registerName(name.encode("ascii"))
        if not value:
            raise MacOSProcessActivationError(
                "native application activation is unavailable"
            )
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
            raise MacOSProcessActivationError(
                "native application activation is unavailable"
            )
        try:
            yield
        finally:
            self._send_void(pool, self._selector("drain"))

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

    @contextmanager
    def activation_session(self):
        """Retain the native application object inside one autorelease pool."""

        with self._pool():
            yield self

    def running_application(
        self,
        process_identifier: int,
    ) -> MacOSRunningApplication | None:
        application = self._send_id_int(
            self._class("NSRunningApplication"),
            self._selector("runningApplicationWithProcessIdentifier:"),
            process_identifier,
        )
        if not application:
            return None
        return MacOSRunningApplication(
            native_handle=application,
            process_identifier=int(
                self._send_int(
                    application,
                    self._selector("processIdentifier"),
                )
            ),
            bundle_path=self._application_path(application),
        )

    def activate(self, application: MacOSRunningApplication) -> bool:
        return bool(
            self._send_bool_ulong(
                application.native_handle,
                self._selector("activateWithOptions:"),
                self._ACTIVATE_ALL_WINDOWS | self._ACTIVATE_IGNORING_OTHER_APPS,
            )
        )

    def is_frontmost(self, application: MacOSRunningApplication) -> bool:
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
        return (
            int(
                self._send_int(
                    frontmost,
                    self._selector("processIdentifier"),
                )
            )
            == application.process_identifier
        )


MacOSProcessActivationRuntimeFactory = Callable[[], MacOSProcessActivationRuntime]


def activate_running_macos_application_outcome(
    process_identifier: int,
    expected_bundle_path: str | Path,
    *,
    runtime_factory: MacOSProcessActivationRuntimeFactory | None = None,
) -> JamulusForegroundOutcome:
    """Activate an exact live process and return only bounded outcome facts."""

    if (
        not isinstance(process_identifier, int)
        or isinstance(process_identifier, bool)
        or not 1 <= process_identifier <= 2**31 - 1
    ):
        return JamulusForegroundOutcome(
            False,
            JamulusForegroundReason.IDENTITY_UNVERIFIED,
        )
    try:
        expected = Path(expected_bundle_path)
    except (TypeError, ValueError):
        return JamulusForegroundOutcome(
            False,
            JamulusForegroundReason.IDENTITY_UNVERIFIED,
        )
    if expected.suffix.casefold() != ".app":
        return JamulusForegroundOutcome(
            False,
            JamulusForegroundReason.IDENTITY_UNVERIFIED,
        )
    try:
        runtime = (
            runtime_factory()
            if runtime_factory is not None
            else MacOSProcessActivationRuntime()
        )
    except Exception:  # noqa: BLE001 - native availability fails closed
        return JamulusForegroundOutcome(
            False,
            JamulusForegroundReason.NATIVE_ACTIVATION_UNAVAILABLE,
        )
    try:
        with runtime.activation_session() as active_runtime:
            application = active_runtime.running_application(process_identifier)
            if (
                application is None
                or application.process_identifier != process_identifier
            ):
                return JamulusForegroundOutcome(
                    False,
                    JamulusForegroundReason.PROCESS_CHANGED,
                )
            if application.bundle_path is None:
                return JamulusForegroundOutcome(
                    False,
                    JamulusForegroundReason.IDENTITY_UNVERIFIED,
                )
            try:
                identity_matches = os.path.samefile(
                    application.bundle_path,
                    expected,
                )
            except OSError:
                identity_matches = False
            if not identity_matches:
                return JamulusForegroundOutcome(
                    False,
                    JamulusForegroundReason.IDENTITY_UNVERIFIED,
                )
            if not bool(active_runtime.activate(application)):
                return JamulusForegroundOutcome(
                    False,
                    JamulusForegroundReason.ACTIVATION_REFUSED,
                )
            deadline = time.monotonic() + 1.0
            while True:
                if bool(active_runtime.is_frontmost(application)):
                    return JamulusForegroundOutcome(
                        True,
                        JamulusForegroundReason.FOREGROUNDED,
                    )
                if time.monotonic() >= deadline:
                    return JamulusForegroundOutcome(
                        False,
                        JamulusForegroundReason.FRONTMOST_UNCONFIRMED,
                    )
                time.sleep(0.05)
    except Exception:  # noqa: BLE001 - best-effort UI boundary fails closed
        return JamulusForegroundOutcome(
            False,
            JamulusForegroundReason.NATIVE_ACTIVATION_UNAVAILABLE,
        )


def activate_running_macos_application(
    process_identifier: int,
    expected_bundle_path: str | Path,
    *,
    runtime_factory: MacOSProcessActivationRuntimeFactory | None = None,
) -> bool:
    """Boolean compatibility wrapper for existing foreground callers."""

    return bool(
        activate_running_macos_application_outcome(
            process_identifier,
            expected_bundle_path,
            runtime_factory=runtime_factory,
        )
    )
