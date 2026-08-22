"""Read-only CoreAudio device discovery for the Jamulus route boundary.

The CoreAudio ``AudioDeviceID`` is deliberately kept transient: it can change
between boots.  Callers persist the accompanying ``uid`` instead, then resolve
that UID again immediately before a Jamulus launch.  Jamulus itself selects a
macOS device by display name, so :func:`resolve_coreaudio_device` additionally
rejects ambiguous or unsafe selector names rather than pretending a UID can be
passed through to Jamulus.

This module only queries CoreAudio.  It neither opens an audio stream nor
changes a macOS device setting.  It uses the system CoreAudio and
CoreFoundation frameworks through ``ctypes`` so a packaged WebJam app does not
need PyObjC, a Swift toolchain, or a bundled helper executable.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import math
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

_LOGGER = logging.getLogger("webjam.coreaudio_devices")


class CoreAudioDirection(str, Enum):
    """A CoreAudio device direction used by the Jamulus route."""

    INPUT = "input"
    OUTPUT = "output"


class CoreAudioRouteError(ValueError):
    """An actionable, safe-to-display CoreAudio route validation failure."""


class CoreAudioBackendError(RuntimeError):
    """A private CoreAudio/HAL operation failed during a read-only scan."""


@dataclass(frozen=True, slots=True)
class CoreAudioDevice:
    """A current CoreAudio device snapshot.

    ``uid`` is the persistent CoreAudio identity suitable for storing in
    settings.  ``object_id`` is intentionally included only for current-scan
    bookkeeping and must never be persisted: CoreAudio documents it as a live
    object identifier, not a stable identity.
    """

    uid: str
    name: str
    object_id: int
    input_channels: int
    output_channels: int
    nominal_rate: float | None
    is_default_input: bool = False
    is_default_output: bool = False

    def __post_init__(self) -> None:
        uid = str(self.uid or "").strip()
        name = str(self.name or "").strip()
        if not uid:
            raise ValueError("CoreAudio device UID must not be empty")
        if not name:
            raise ValueError("CoreAudio device name must not be empty")
        if isinstance(self.object_id, bool) or int(self.object_id) <= 0:
            raise ValueError("CoreAudio object_id must be a positive integer")

        channels: dict[str, int] = {}
        for field_name in ("input_channels", "output_channels"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or int(value) < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
            channels[field_name] = int(value)

        rate = self.nominal_rate
        if rate is not None:
            rate = float(rate)
            if not math.isfinite(rate) or rate <= 0:
                rate = None

        object.__setattr__(self, "uid", uid)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "object_id", int(self.object_id))
        object.__setattr__(self, "input_channels", channels["input_channels"])
        object.__setattr__(self, "output_channels", channels["output_channels"])
        object.__setattr__(self, "nominal_rate", rate)

    def channel_count(self, direction: CoreAudioDirection | str) -> int:
        """Return the number of hardware channels available in ``direction``."""

        normalized = _normalize_direction(direction)
        return (
            self.input_channels
            if normalized is CoreAudioDirection.INPUT
            else self.output_channels
        )

    def supports(self, direction: CoreAudioDirection | str) -> bool:
        """Whether this device has at least one channel in ``direction``."""

        return self.channel_count(direction) > 0


@dataclass(frozen=True, slots=True)
class CoreAudioScan:
    """A complete best-effort CoreAudio snapshot.

    A non-empty ``error`` means the scan could not be trusted at all.  A scan
    with devices and no error is still only an OS-level preflight; it is not
    evidence that Jamulus has opened a particular route.
    """

    devices: tuple[CoreAudioDevice, ...] = ()
    default_input_uid: str | None = None
    default_output_uid: str | None = None
    error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "devices", tuple(self.devices))
        for field_name in ("default_input_uid", "default_output_uid"):
            value = getattr(self, field_name)
            normalized = str(value or "").strip() or None
            object.__setattr__(self, field_name, normalized)
        object.__setattr__(self, "error", str(self.error or "").strip())

    @property
    def available(self) -> bool:
        """Whether CoreAudio returned a trustworthy device snapshot."""

        return not self.error

    @property
    def has_devices(self) -> bool:
        """Whether this scan contains one or more normal audio devices."""

        return bool(self.devices)

    def default_uid(self, direction: CoreAudioDirection | str) -> str | None:
        """Return the current default device UID for ``direction``."""

        normalized = _normalize_direction(direction)
        return (
            self.default_input_uid
            if normalized is CoreAudioDirection.INPUT
            else self.default_output_uid
        )


class CoreAudioBackend(Protocol):
    """Small injectable boundary around CoreAudio's C API.

    Production uses :class:`_CtypesCoreAudioBackend`; tests and callers that
    provide their own discovery source can pass a simple fake implementation to
    :func:`scan_coreaudio_devices` without mocking ``ctypes``.
    """

    def device_ids(self) -> tuple[int, ...]:
        """Return the current transient CoreAudio object identifiers."""

    def device_uid(self, object_id: int) -> str:
        """Return the persistent CoreAudio UID for ``object_id``."""

    def device_name(self, object_id: int) -> str:
        """Return the musician-facing CoreAudio display name."""

    def channel_count(
        self, object_id: int, direction: CoreAudioDirection
    ) -> int:
        """Return channels available to the hardware in ``direction``."""

    def nominal_sample_rate(self, object_id: int) -> float | None:
        """Return the current device sample rate, if CoreAudio reports one."""

    def default_device_id(self, direction: CoreAudioDirection) -> int | None:
        """Return the current default CoreAudio object identifier."""


def scan_coreaudio_devices(
    *,
    backend: CoreAudioBackend | None = None,
    platform: str | None = None,
) -> CoreAudioScan:
    """Return stable-identity CoreAudio devices without changing device state.

    On non-macOS hosts the normal result is an empty unavailable scan.  Passing
    ``backend`` is intentionally supported on every platform so the pure
    snapshot and resolver behavior can be tested without native audio hardware.
    No raw operating-system exception or path is exposed through ``error``.
    """

    current_platform = sys.platform if platform is None else str(platform)
    if backend is None:
        if current_platform != "darwin":
            return CoreAudioScan(error="CoreAudio device discovery is only available on macOS.")
        try:
            backend = _CtypesCoreAudioBackend()
        except Exception as exc:  # noqa: BLE001 - discovery must never crash UI
            _LOGGER.debug("CoreAudio framework could not be loaded: %s", exc)
            return CoreAudioScan(
                error="Audio devices could not be read. Check macOS Sound settings, then try again."
            )

    try:
        object_ids = backend.device_ids()
    except Exception as exc:  # noqa: BLE001 - HAL failures are expected at hot-plug
        _LOGGER.debug("CoreAudio device enumeration failed: %s", exc)
        return CoreAudioScan(
            error="Audio devices could not be read. Check macOS Sound settings, then try again."
        )

    try:
        default_input_id = backend.default_device_id(CoreAudioDirection.INPUT)
    except Exception as exc:  # noqa: BLE001 - defaults are optional metadata
        _LOGGER.debug("CoreAudio default input query failed: %s", exc)
        default_input_id = None
    try:
        default_output_id = backend.default_device_id(CoreAudioDirection.OUTPUT)
    except Exception as exc:  # noqa: BLE001 - defaults are optional metadata
        _LOGGER.debug("CoreAudio default output query failed: %s", exc)
        default_output_id = None

    devices: list[CoreAudioDevice] = []
    failures = 0
    for raw_object_id in object_ids:
        try:
            object_id = int(raw_object_id)
            if object_id <= 0:
                raise CoreAudioBackendError("invalid CoreAudio object identifier")
            device = CoreAudioDevice(
                uid=backend.device_uid(object_id),
                name=backend.device_name(object_id),
                object_id=object_id,
                input_channels=backend.channel_count(
                    object_id, CoreAudioDirection.INPUT
                ),
                output_channels=backend.channel_count(
                    object_id, CoreAudioDirection.OUTPUT
                ),
                nominal_rate=backend.nominal_sample_rate(object_id),
                is_default_input=object_id == default_input_id,
                is_default_output=object_id == default_output_id,
            )
        except Exception as exc:  # noqa: BLE001 - one stale HAL object is skippable
            failures += 1
            _LOGGER.debug("CoreAudio device snapshot failed: %s", exc)
            continue
        devices.append(device)

    if not devices and object_ids and failures:
        return CoreAudioScan(
            error="Audio devices could not be read. Check macOS Sound settings, then try again."
        )

    devices.sort(key=lambda device: (device.name.casefold(), device.uid))
    return CoreAudioScan(
        devices=tuple(devices),
        default_input_uid=_uid_for_object_id(devices, default_input_id),
        default_output_uid=_uid_for_object_id(devices, default_output_id),
    )


def resolve_coreaudio_device(
    scan: CoreAudioScan,
    uid: str,
    direction: CoreAudioDirection | str,
) -> CoreAudioDevice:
    """Resolve and preflight one persisted CoreAudio device UID.

    This verifies only what macOS can prove before Jamulus launches: the saved
    stable UID is present, has the requested hardware direction, is currently
    48 kHz, and can be translated to an unambiguous Jamulus CoreAudio selector.
    It intentionally does not assert that Jamulus subsequently opened it.
    """

    normalized_direction = _normalize_direction(direction)
    label = normalized_direction.value
    selected_uid = str(uid or "").strip()

    if scan.error:
        raise CoreAudioRouteError(
            "Audio devices could not be read. Check macOS Sound settings, then try again."
        )
    if not selected_uid:
        raise CoreAudioRouteError(
            f"Choose a band audio {label} device before starting the jam."
        )

    matches = [device for device in scan.devices if device.uid == selected_uid]
    if not matches:
        raise CoreAudioRouteError(
            f"The saved band audio {label} device is no longer connected. "
            "Choose it again in Audio Setup."
        )
    if len(matches) != 1:
        raise CoreAudioRouteError(
            f"More than one CoreAudio device reports the saved {label} identity. "
            "Reconnect the device, then choose it again in Audio Setup."
        )

    device = matches[0]
    if not device.supports(normalized_direction):
        raise CoreAudioRouteError(
            f"{_quoted(device.name)} is not available as a band audio {label}. "
            "Choose another device in Audio Setup."
        )
    if not _is_48khz(device.nominal_rate):
        raise CoreAudioRouteError(
            f"{_quoted(device.name)} is not running at 48 kHz. Set it to 48 kHz "
            "in Audio MIDI Setup, then try again."
        )
    if _has_unsafe_selector_name(device.name):
        raise CoreAudioRouteError(
            f"{_quoted(device.name)} cannot be used by Jamulus because of its name. "
            "Rename it in Audio MIDI Setup, then choose it again."
        )

    same_name = [
        candidate
        for candidate in scan.devices
        if candidate.name == device.name and candidate.supports(normalized_direction)
    ]
    if len(same_name) != 1:
        raise CoreAudioRouteError(
            f"More than one {label} device is named {_quoted(device.name)}. "
            "Rename one in Audio MIDI Setup, then choose it again."
        )
    return device


def coreaudio_device_generation(scan: CoreAudioScan) -> str:
    """Return a deterministic fingerprint of current route-affecting hardware.

    The fingerprint excludes transient object IDs and current system-default
    choices.  It changes when a device UID, Jamulus selector name, directional
    channel capability, or nominal rate changes.
    """

    snapshot = [
        {
            "uid": device.uid,
            "name": device.name,
            "input_channels": device.input_channels,
            "output_channels": device.output_channels,
            "nominal_rate": device.nominal_rate,
        }
        for device in sorted(scan.devices, key=lambda item: item.uid)
    ]
    payload = json.dumps(
        snapshot,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "coreaudio-" + hashlib.sha256(payload).hexdigest()


def _normalize_direction(direction: CoreAudioDirection | str) -> CoreAudioDirection:
    if isinstance(direction, CoreAudioDirection):
        return direction
    try:
        return CoreAudioDirection(str(direction).strip().lower())
    except ValueError as exc:
        raise ValueError("CoreAudio direction must be 'input' or 'output'") from exc


def _uid_for_object_id(
    devices: list[CoreAudioDevice], object_id: int | None
) -> str | None:
    if object_id is None:
        return None
    for device in devices:
        if device.object_id == object_id:
            return device.uid
    return None


def _is_48khz(rate: float | None) -> bool:
    return rate is not None and math.isclose(float(rate), 48_000.0, abs_tol=0.5)


def _has_unsafe_selector_name(name: str) -> bool:
    return "/" in name or any(character in name for character in ("\x00", "\r", "\n"))


def _quoted(value: str) -> str:
    return f"“{value}”"


_UInt32 = ctypes.c_uint32
_OSStatus = ctypes.c_int32
_SYSTEM_OBJECT = 1
_ELEMENT_MAIN = 0
_CF_STRING_ENCODING_UTF8 = 0x08000100


def _fourcc(value: str) -> int:
    return int.from_bytes(value.encode("ascii"), "big")


_SCOPE_GLOBAL = _fourcc("glob")
_SCOPE_INPUT = _fourcc("inpt")
_SCOPE_OUTPUT = _fourcc("outp")
_PROPERTY_DEVICES = _fourcc("dev#")
_PROPERTY_DEFAULT_INPUT = _fourcc("dIn ")
_PROPERTY_DEFAULT_OUTPUT = _fourcc("dOut")
_PROPERTY_NAME = _fourcc("lnam")
_PROPERTY_UID = _fourcc("uid ")
_PROPERTY_STREAM_CONFIGURATION = _fourcc("slay")
_PROPERTY_NOMINAL_SAMPLE_RATE = _fourcc("nsrt")


class _AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", _UInt32),
        ("mScope", _UInt32),
        ("mElement", _UInt32),
    ]


class _AudioBuffer(ctypes.Structure):
    _fields_ = [
        ("mNumberChannels", _UInt32),
        ("mDataByteSize", _UInt32),
        ("mData", ctypes.c_void_p),
    ]


class _AudioBufferListOne(ctypes.Structure):
    _fields_ = [
        ("mNumberBuffers", _UInt32),
        ("mBuffers", _AudioBuffer * 1),
    ]


class _CtypesCoreAudioBackend:
    """Small, read-only ctypes binding for macOS CoreAudio."""

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise CoreAudioBackendError("CoreAudio is only available on macOS")
        self._coreaudio = ctypes.CDLL(
            "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
        )
        self._corefoundation = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )

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
        self._corefoundation.CFStringGetCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_long,
            _UInt32,
        ]
        self._corefoundation.CFStringGetCString.restype = ctypes.c_bool
        self._corefoundation.CFRelease.argtypes = [ctypes.c_void_p]
        self._corefoundation.CFRelease.restype = None

    def device_ids(self) -> tuple[int, ...]:
        size = self._property_size(_SYSTEM_OBJECT, _PROPERTY_DEVICES, _SCOPE_GLOBAL)
        if size == 0:
            return ()
        if size % ctypes.sizeof(_UInt32):
            raise CoreAudioBackendError("invalid CoreAudio device list size")
        values = (_UInt32 * (size // ctypes.sizeof(_UInt32)))()
        actual = _UInt32(size)
        self._check_status(
            self._coreaudio.AudioObjectGetPropertyData(
                _UInt32(_SYSTEM_OBJECT),
                ctypes.byref(self._address(_PROPERTY_DEVICES, _SCOPE_GLOBAL)),
                _UInt32(0),
                None,
                ctypes.byref(actual),
                ctypes.cast(values, ctypes.c_void_p),
            )
        )
        if actual.value != size:
            raise CoreAudioBackendError("CoreAudio device list changed during query")
        return tuple(int(value) for value in values)

    def device_uid(self, object_id: int) -> str:
        return self._string_property(object_id, _PROPERTY_UID)

    def device_name(self, object_id: int) -> str:
        return self._string_property(object_id, _PROPERTY_NAME)

    def channel_count(
        self, object_id: int, direction: CoreAudioDirection
    ) -> int:
        scope = (
            _SCOPE_INPUT
            if direction is CoreAudioDirection.INPUT
            else _SCOPE_OUTPUT
        )
        raw = self._property_bytes(object_id, _PROPERTY_STREAM_CONFIGURATION, scope)
        if not raw:
            return 0
        if len(raw) < ctypes.sizeof(_UInt32):
            raise CoreAudioBackendError("invalid CoreAudio stream configuration")
        buffer_count = int(_UInt32.from_buffer_copy(raw[:4]).value)
        offset = _AudioBufferListOne.mBuffers.offset
        stride = ctypes.sizeof(_AudioBuffer)
        required = offset + (buffer_count * stride)
        if required > len(raw):
            raise CoreAudioBackendError("truncated CoreAudio stream configuration")
        return sum(
            int(
                _UInt32.from_buffer_copy(
                    raw[offset + (index * stride) : offset + (index * stride) + 4]
                ).value
            )
            for index in range(buffer_count)
        )

    def nominal_sample_rate(self, object_id: int) -> float | None:
        value = ctypes.c_double()
        self._read_scalar(
            object_id,
            _PROPERTY_NOMINAL_SAMPLE_RATE,
            _SCOPE_GLOBAL,
            value,
        )
        rate = float(value.value)
        return rate if math.isfinite(rate) and rate > 0 else None

    def default_device_id(self, direction: CoreAudioDirection) -> int | None:
        selector = (
            _PROPERTY_DEFAULT_INPUT
            if direction is CoreAudioDirection.INPUT
            else _PROPERTY_DEFAULT_OUTPUT
        )
        value = _UInt32()
        self._read_scalar(_SYSTEM_OBJECT, selector, _SCOPE_GLOBAL, value)
        return int(value.value) or None

    @staticmethod
    def _address(selector: int, scope: int) -> _AudioObjectPropertyAddress:
        return _AudioObjectPropertyAddress(
            _UInt32(selector), _UInt32(scope), _UInt32(_ELEMENT_MAIN)
        )

    def _property_size(self, object_id: int, selector: int, scope: int) -> int:
        address = self._address(selector, scope)
        size = _UInt32()
        self._check_status(
            self._coreaudio.AudioObjectGetPropertyDataSize(
                _UInt32(object_id),
                ctypes.byref(address),
                _UInt32(0),
                None,
                ctypes.byref(size),
            )
        )
        return int(size.value)

    def _property_bytes(self, object_id: int, selector: int, scope: int) -> bytes:
        size = self._property_size(object_id, selector, scope)
        if size == 0:
            return b""
        raw = (ctypes.c_ubyte * size)()
        actual = _UInt32(size)
        address = self._address(selector, scope)
        self._check_status(
            self._coreaudio.AudioObjectGetPropertyData(
                _UInt32(object_id),
                ctypes.byref(address),
                _UInt32(0),
                None,
                ctypes.byref(actual),
                ctypes.cast(raw, ctypes.c_void_p),
            )
        )
        if actual.value > size:
            raise CoreAudioBackendError("invalid CoreAudio property size")
        return bytes(raw[: actual.value])

    def _read_scalar(
        self,
        object_id: int,
        selector: int,
        scope: int,
        value: ctypes._SimpleCData,  # type: ignore[name-defined]
    ) -> None:
        actual = _UInt32(ctypes.sizeof(value))
        address = self._address(selector, scope)
        self._check_status(
            self._coreaudio.AudioObjectGetPropertyData(
                _UInt32(object_id),
                ctypes.byref(address),
                _UInt32(0),
                None,
                ctypes.byref(actual),
                ctypes.byref(value),
            )
        )
        if actual.value != ctypes.sizeof(value):
            raise CoreAudioBackendError("invalid CoreAudio scalar property size")

    def _string_property(self, object_id: int, selector: int) -> str:
        reference = ctypes.c_void_p()
        self._read_scalar(object_id, selector, _SCOPE_GLOBAL, reference)
        if not reference.value:
            raise CoreAudioBackendError("CoreAudio returned an empty string property")
        try:
            buffer = ctypes.create_string_buffer(2048)
            decoded = self._corefoundation.CFStringGetCString(
                reference,
                buffer,
                ctypes.c_long(len(buffer)),
                _UInt32(_CF_STRING_ENCODING_UTF8),
            )
            if not decoded:
                raise CoreAudioBackendError("CoreAudio string could not be decoded")
            return buffer.value.decode("utf-8")
        finally:
            self._corefoundation.CFRelease(reference)

    @staticmethod
    def _check_status(status: int) -> None:
        if int(status) != 0:
            raise CoreAudioBackendError("CoreAudio property query failed")
