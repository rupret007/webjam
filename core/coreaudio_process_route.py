"""Read-only, PID-bound CoreAudio route evidence for macOS.

Device configuration files are not live routing evidence.  On supported
macOS releases, CoreAudio exposes a transient Process AudioObject for a PID
and the input/output device AudioObjectIDs currently used by that process.
This module turns those properties into a deliberately strict snapshot:

* the PID must still resolve to one Process AudioObject;
* input and output I/O must both be running;
* each direction must report exactly one current device;
* both devices must exist unambiguously in the same fresh device scan; and
* a second read must match the first so a mid-query route change is rejected.

The process-object API is capability-gated to macOS 14.2 or later.  macOS 13
therefore remains unavailable rather than falling back to saved display names.
No setting is changed and no device identity is persisted.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import platform as platform_module
import sys
from typing import Protocol

from core.coreaudio_devices import CoreAudioDevice, CoreAudioDirection, CoreAudioScan


_MINIMUM_MACOS_VERSION = (14, 2)


class CoreAudioProcessRouteError(RuntimeError):
    """A musician-safe failure to establish current process-route evidence."""


class CoreAudioProcessBackendError(RuntimeError):
    """A private CoreAudio process-property query failed."""


@dataclass(frozen=True, slots=True)
class CoreAudioProcessRouteSnapshot:
    """One immutable, non-persistent live route proof for an audio process."""

    pid: int
    process_object_id: int
    input_device: CoreAudioDevice
    output_device: CoreAudioDevice

    def __post_init__(self) -> None:
        for name in ("pid", "process_object_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or int(value) <= 0:
                raise ValueError(f"{name} must be a positive integer")
            object.__setattr__(self, name, int(value))
        if not isinstance(self.input_device, CoreAudioDevice):
            raise ValueError("input_device must be a CoreAudioDevice")
        if not isinstance(self.output_device, CoreAudioDevice):
            raise ValueError("output_device must be a CoreAudioDevice")


class CoreAudioProcessBackend(Protocol):
    """Injectable boundary around the CoreAudio process-object properties."""

    def supports_process_route_query(self) -> bool:
        """Whether PID-to-process translation is exposed by the current HAL."""

    def process_object_id(self, pid: int) -> int | None:
        """Return the current Process AudioObject for ``pid``, if any."""

    def process_device_ids(
        self, process_object_id: int, direction: CoreAudioDirection
    ) -> tuple[int, ...]:
        """Return devices currently used by the process in ``direction``."""

    def process_io_running(
        self, process_object_id: int, direction: CoreAudioDirection
    ) -> bool:
        """Whether the process currently runs I/O in ``direction``."""


class CoreAudioProcessRouteProbe:
    """Capability-gated producer of double-read process-route snapshots."""

    def __init__(
        self,
        *,
        backend: CoreAudioProcessBackend | None = None,
        platform_name: str | None = None,
        macos_version: str | tuple[int, ...] | None = None,
    ) -> None:
        self._platform = str(
            sys.platform if platform_name is None else platform_name
        ).lower()
        self._version = _normalized_macos_version(macos_version)
        self._backend = backend

    def capability_error(self) -> str:
        """Return empty text only when live PID route inspection is available."""

        if not self._platform.startswith("darwin"):
            return "Live CoreAudio process routing is only available on macOS."
        if self._version < _MINIMUM_MACOS_VERSION:
            return (
                "Shared Track requires macOS 14.2 or later because macOS 13 "
                "cannot prove the primary Jamulus client's live input and output "
                "devices."
            )
        try:
            backend = self._get_backend()
            supported = backend.supports_process_route_query()
        except Exception:  # noqa: BLE001 - native capability boundary
            supported = False
        if not supported:
            return (
                "This Mac cannot provide live primary Jamulus route evidence. "
                "Shared Track stays unavailable to prevent an audio loop."
            )
        return ""

    def snapshot(
        self,
        pid: int,
        scan: CoreAudioScan,
    ) -> CoreAudioProcessRouteSnapshot:
        """Prove one stable, active input/output route for ``pid``.

        The query is intentionally double-read.  If the Process AudioObject,
        directional device arrays, or running flags change during inspection,
        the result is ambiguous and rejected.
        """

        capability_error = self.capability_error()
        if capability_error:
            raise CoreAudioProcessRouteError(capability_error)
        if isinstance(pid, bool) or int(pid) <= 0:
            raise CoreAudioProcessRouteError(
                "Shared Track couldn't identify the active primary "
                "Jamulus process."
            )
        if not isinstance(scan, CoreAudioScan) or scan.error or not scan.devices:
            raise CoreAudioProcessRouteError(
                "Shared Track couldn't read a fresh CoreAudio device snapshot."
            )

        safe_pid = int(pid)
        backend = self._get_backend()
        try:
            first_process = backend.process_object_id(safe_pid)
            if first_process is None or int(first_process) <= 0:
                raise CoreAudioProcessBackendError("missing process object")
            process_object = int(first_process)
            first_input = tuple(
                int(value)
                for value in backend.process_device_ids(
                    process_object, CoreAudioDirection.INPUT
                )
            )
            first_output = tuple(
                int(value)
                for value in backend.process_device_ids(
                    process_object, CoreAudioDirection.OUTPUT
                )
            )
            first_running = (
                bool(
                    backend.process_io_running(
                        process_object, CoreAudioDirection.INPUT
                    )
                ),
                bool(
                    backend.process_io_running(
                        process_object, CoreAudioDirection.OUTPUT
                    )
                ),
            )
            second_input = tuple(
                int(value)
                for value in backend.process_device_ids(
                    process_object, CoreAudioDirection.INPUT
                )
            )
            second_output = tuple(
                int(value)
                for value in backend.process_device_ids(
                    process_object, CoreAudioDirection.OUTPUT
                )
            )
            second_running = (
                bool(
                    backend.process_io_running(
                        process_object, CoreAudioDirection.INPUT
                    )
                ),
                bool(
                    backend.process_io_running(
                        process_object, CoreAudioDirection.OUTPUT
                    )
                ),
            )
            # Re-translate the PID last so a process exit/replacement racing
            # any directional read invalidates the entire snapshot.
            second_process = backend.process_object_id(safe_pid)
        except CoreAudioProcessRouteError:
            raise
        except Exception as exc:  # noqa: BLE001 - native read boundary
            raise CoreAudioProcessRouteError(
                "Shared Track couldn't inspect the primary Jamulus live "
                "audio route."
            ) from exc

        if (
            second_process != process_object
            or first_input != second_input
            or first_output != second_output
            or first_running != second_running
        ):
            raise CoreAudioProcessRouteError(
                "The primary Jamulus audio route changed while WebJam was "
                "checking it. Try Shared Track again after the route settles."
            )
        if first_running != (True, True):
            raise CoreAudioProcessRouteError(
                "Shared Track needs the primary Jamulus input and output to "
                "be actively running."
            )
        if len(first_input) != 1 or len(first_output) != 1:
            raise CoreAudioProcessRouteError(
                "The primary Jamulus live audio route is missing or ambiguous. "
                "Choose one input and one output device, then try again."
            )

        input_device = _unique_scanned_device(
            scan, first_input[0], CoreAudioDirection.INPUT
        )
        output_device = _unique_scanned_device(
            scan, first_output[0], CoreAudioDirection.OUTPUT
        )
        return CoreAudioProcessRouteSnapshot(
            pid=safe_pid,
            process_object_id=process_object,
            input_device=input_device,
            output_device=output_device,
        )

    def _get_backend(self) -> CoreAudioProcessBackend:
        if self._backend is None:
            self._backend = _CtypesCoreAudioProcessBackend()
        return self._backend


def _unique_scanned_device(
    scan: CoreAudioScan,
    object_id: int,
    direction: CoreAudioDirection,
) -> CoreAudioDevice:
    matches = [
        device
        for device in scan.devices
        if device.object_id == object_id and device.supports(direction)
    ]
    if len(matches) != 1:
        raise CoreAudioProcessRouteError(
            "The primary Jamulus live audio device could not be matched "
            "unambiguously to the current CoreAudio devices."
        )
    return matches[0]


def _normalized_macos_version(
    value: str | tuple[int, ...] | None,
) -> tuple[int, int]:
    if value is None:
        value = platform_module.mac_ver()[0]
    if isinstance(value, tuple):
        pieces = tuple(int(piece) for piece in value[:2])
    else:
        raw = str(value or "").strip()
        try:
            pieces = tuple(int(piece) for piece in raw.split(".")[:2])
        except ValueError:
            pieces = ()
    return (pieces + (0, 0))[:2]


_UInt32 = ctypes.c_uint32
_OSStatus = ctypes.c_int32
_Pid = ctypes.c_int32
_SYSTEM_OBJECT = 1
_ELEMENT_MAIN = 0


def _fourcc(value: str) -> int:
    return int.from_bytes(value.encode("ascii"), "big")


_SCOPE_GLOBAL = _fourcc("glob")
_SCOPE_INPUT = _fourcc("inpt")
_SCOPE_OUTPUT = _fourcc("outp")
_PROPERTY_TRANSLATE_PID = _fourcc("id2p")
_PROPERTY_PROCESS_DEVICES = _fourcc("pdv#")
_PROPERTY_PROCESS_RUNNING_INPUT = _fourcc("piri")
_PROPERTY_PROCESS_RUNNING_OUTPUT = _fourcc("piro")


class _AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", _UInt32),
        ("mScope", _UInt32),
        ("mElement", _UInt32),
    ]


class _CtypesCoreAudioProcessBackend:
    """Read-only ctypes binding for CoreAudio Process AudioObjects."""

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise CoreAudioProcessBackendError(
                "CoreAudio process routes are only available on macOS"
            )
        self._coreaudio = ctypes.CDLL(
            "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
        )
        self._coreaudio.AudioObjectHasProperty.argtypes = [
            _UInt32,
            ctypes.POINTER(_AudioObjectPropertyAddress),
        ]
        self._coreaudio.AudioObjectHasProperty.restype = ctypes.c_bool
        self._coreaudio.AudioObjectGetPropertyDataSize.argtypes = [
            _UInt32,
            ctypes.POINTER(_AudioObjectPropertyAddress),
            _UInt32,
            ctypes.c_void_p,
            ctypes.POINTER(_UInt32),
        ]
        self._coreaudio.AudioObjectGetPropertyDataSize.restype = _OSStatus
        self._coreaudio.AudioObjectGetPropertyData.argtypes = [
            _UInt32,
            ctypes.POINTER(_AudioObjectPropertyAddress),
            _UInt32,
            ctypes.c_void_p,
            ctypes.POINTER(_UInt32),
            ctypes.c_void_p,
        ]
        self._coreaudio.AudioObjectGetPropertyData.restype = _OSStatus

    def supports_process_route_query(self) -> bool:
        address = self._address(_PROPERTY_TRANSLATE_PID, _SCOPE_GLOBAL)
        return bool(
            self._coreaudio.AudioObjectHasProperty(
                _UInt32(_SYSTEM_OBJECT), ctypes.byref(address)
            )
        )

    def process_object_id(self, pid: int) -> int | None:
        qualifier = _Pid(int(pid))
        value = _UInt32()
        size = _UInt32(ctypes.sizeof(value))
        address = self._address(_PROPERTY_TRANSLATE_PID, _SCOPE_GLOBAL)
        self._check_status(
            self._coreaudio.AudioObjectGetPropertyData(
                _UInt32(_SYSTEM_OBJECT),
                ctypes.byref(address),
                _UInt32(ctypes.sizeof(qualifier)),
                ctypes.byref(qualifier),
                ctypes.byref(size),
                ctypes.byref(value),
            )
        )
        if size.value != ctypes.sizeof(value):
            raise CoreAudioProcessBackendError(
                "invalid CoreAudio process object size"
            )
        return int(value.value) or None

    def process_device_ids(
        self, process_object_id: int, direction: CoreAudioDirection
    ) -> tuple[int, ...]:
        scope = (
            _SCOPE_INPUT
            if direction is CoreAudioDirection.INPUT
            else _SCOPE_OUTPUT
        )
        address = self._address(_PROPERTY_PROCESS_DEVICES, scope)
        size = _UInt32()
        self._check_status(
            self._coreaudio.AudioObjectGetPropertyDataSize(
                _UInt32(process_object_id),
                ctypes.byref(address),
                _UInt32(0),
                None,
                ctypes.byref(size),
            )
        )
        if size.value == 0:
            return ()
        if size.value % ctypes.sizeof(_UInt32):
            raise CoreAudioProcessBackendError(
                "invalid CoreAudio process device list size"
            )
        values = (_UInt32 * (size.value // ctypes.sizeof(_UInt32)))()
        actual = _UInt32(size.value)
        self._check_status(
            self._coreaudio.AudioObjectGetPropertyData(
                _UInt32(process_object_id),
                ctypes.byref(address),
                _UInt32(0),
                None,
                ctypes.byref(actual),
                ctypes.cast(values, ctypes.c_void_p),
            )
        )
        if actual.value != size.value:
            raise CoreAudioProcessBackendError(
                "CoreAudio process device list changed during query"
            )
        return tuple(int(value) for value in values)

    def process_io_running(
        self, process_object_id: int, direction: CoreAudioDirection
    ) -> bool:
        selector = (
            _PROPERTY_PROCESS_RUNNING_INPUT
            if direction is CoreAudioDirection.INPUT
            else _PROPERTY_PROCESS_RUNNING_OUTPUT
        )
        value = _UInt32()
        size = _UInt32(ctypes.sizeof(value))
        address = self._address(selector, _SCOPE_GLOBAL)
        self._check_status(
            self._coreaudio.AudioObjectGetPropertyData(
                _UInt32(process_object_id),
                ctypes.byref(address),
                _UInt32(0),
                None,
                ctypes.byref(size),
                ctypes.byref(value),
            )
        )
        if size.value != ctypes.sizeof(value) or value.value not in (0, 1):
            raise CoreAudioProcessBackendError(
                "invalid CoreAudio process running state"
            )
        return bool(value.value)

    @staticmethod
    def _address(selector: int, scope: int) -> _AudioObjectPropertyAddress:
        return _AudioObjectPropertyAddress(
            _UInt32(selector), _UInt32(scope), _UInt32(_ELEMENT_MAIN)
        )

    @staticmethod
    def _check_status(status: int) -> None:
        if int(status) != 0:
            raise CoreAudioProcessBackendError(
                "CoreAudio process property query failed"
            )


__all__ = [
    "CoreAudioProcessBackend",
    "CoreAudioProcessRouteError",
    "CoreAudioProcessRouteProbe",
    "CoreAudioProcessRouteSnapshot",
]
