"""Pure unit tests for stable-identity macOS CoreAudio discovery."""

from __future__ import annotations

from dataclasses import replace

import pytest

from core.coreaudio_devices import (
    CoreAudioDevice,
    CoreAudioDirection,
    CoreAudioRouteError,
    CoreAudioScan,
    coreaudio_device_generation,
    resolve_coreaudio_device,
    scan_coreaudio_devices,
)


class _FakeCoreAudioBackend:
    def __init__(
        self,
        devices: tuple[CoreAudioDevice, ...],
        *,
        default_input_id: int | None = None,
        default_output_id: int | None = None,
        fail_enumeration: bool = False,
        fail_object_ids: tuple[int, ...] = (),
    ) -> None:
        self._devices = {device.object_id: device for device in devices}
        self._default_input_id = default_input_id
        self._default_output_id = default_output_id
        self._fail_enumeration = fail_enumeration
        self._fail_object_ids = set(fail_object_ids)

    def device_ids(self) -> tuple[int, ...]:
        if self._fail_enumeration:
            raise OSError("private HAL detail")
        return tuple(self._devices)

    def _device(self, object_id: int) -> CoreAudioDevice:
        if object_id in self._fail_object_ids:
            raise RuntimeError("stale device")
        return self._devices[object_id]

    def device_uid(self, object_id: int) -> str:
        return self._device(object_id).uid

    def device_name(self, object_id: int) -> str:
        return self._device(object_id).name

    def channel_count(
        self, object_id: int, direction: CoreAudioDirection
    ) -> int:
        return self._device(object_id).channel_count(direction)

    def nominal_sample_rate(self, object_id: int) -> float | None:
        return self._device(object_id).nominal_rate

    def default_device_id(self, direction: CoreAudioDirection) -> int | None:
        return (
            self._default_input_id
            if direction is CoreAudioDirection.INPUT
            else self._default_output_id
        )


def _device(
    uid: str,
    name: str,
    object_id: int,
    *,
    input_channels: int = 2,
    output_channels: int = 2,
    nominal_rate: float | None = 48_000.0,
) -> CoreAudioDevice:
    return CoreAudioDevice(
        uid=uid,
        name=name,
        object_id=object_id,
        input_channels=input_channels,
        output_channels=output_channels,
        nominal_rate=nominal_rate,
    )


def _scan(*devices: CoreAudioDevice) -> CoreAudioScan:
    return CoreAudioScan(devices=devices)


def test_non_macos_scan_is_empty_and_unavailable() -> None:
    scan = scan_coreaudio_devices(platform="linux")

    assert scan.devices == ()
    assert scan.available is False
    assert "macOS" in scan.error


def test_scan_uses_persistent_uids_and_marks_current_defaults() -> None:
    input_device = _device("input-uid", "Studio Interface", 42)
    output_device = _device(
        "output-uid", "Studio Speakers", 73, input_channels=0
    )
    scan = scan_coreaudio_devices(
        backend=_FakeCoreAudioBackend(
            (input_device, output_device),
            default_input_id=42,
            default_output_id=73,
        ),
        platform="darwin",
    )

    assert scan.available is True
    assert [device.uid for device in scan.devices] == ["input-uid", "output-uid"]
    assert scan.default_input_uid == "input-uid"
    assert scan.default_output_uid == "output-uid"
    assert scan.devices[0].is_default_input is True
    assert scan.devices[1].is_default_output is True
    assert scan.devices[0].object_id == 42


def test_scan_hides_backend_details_when_enumeration_fails() -> None:
    scan = scan_coreaudio_devices(
        backend=_FakeCoreAudioBackend((), fail_enumeration=True),
        platform="darwin",
    )

    assert scan.available is False
    assert scan.devices == ()
    assert "private HAL detail" not in scan.error
    assert "Audio devices" in scan.error


def test_scan_skips_one_stale_object_but_keeps_other_devices() -> None:
    healthy = _device("healthy", "Healthy Device", 1)
    stale = _device("stale", "Stale Device", 2)
    scan = scan_coreaudio_devices(
        backend=_FakeCoreAudioBackend((healthy, stale), fail_object_ids=(2,)),
        platform="darwin",
    )

    assert scan.available is True
    assert [device.uid for device in scan.devices] == ["healthy"]


def test_resolve_returns_preflightable_directional_device() -> None:
    interface = _device("interface-uid", "Studio Interface", 10)
    scan = _scan(interface)

    assert resolve_coreaudio_device(scan, "interface-uid", "input") is interface
    assert (
        resolve_coreaudio_device(scan, "interface-uid", CoreAudioDirection.OUTPUT)
        is interface
    )


def test_resolve_rejects_missing_persisted_uid_without_default_fallback() -> None:
    scan = _scan(_device("present", "Studio Interface", 10))

    with pytest.raises(CoreAudioRouteError, match="no longer connected"):
        resolve_coreaudio_device(scan, "unplugged", "input")
    with pytest.raises(CoreAudioRouteError, match="Choose a band audio input"):
        resolve_coreaudio_device(scan, "", "input")


def test_resolve_rejects_direction_without_channels() -> None:
    output_only = _device(
        "speakers", "Studio Speakers", 10, input_channels=0, output_channels=2
    )

    with pytest.raises(CoreAudioRouteError, match="not available as a band audio input"):
        resolve_coreaudio_device(_scan(output_only), "speakers", "input")


def test_resolve_rejects_non_48khz_device() -> None:
    device = _device("usb", "USB Interface", 10, nominal_rate=44_100.0)

    with pytest.raises(CoreAudioRouteError, match="not running at 48 kHz"):
        resolve_coreaudio_device(_scan(device), "usb", "input")


def test_resolve_rejects_ambiguous_jamulus_selector_name() -> None:
    one = _device("one", "USB Audio", 10)
    two = _device("two", "USB Audio", 11)

    with pytest.raises(CoreAudioRouteError, match="More than one input device"):
        resolve_coreaudio_device(_scan(one, two), "one", "input")


@pytest.mark.parametrize("name", ["Input/Output", "Input\nOutput", "Input\x00Output"])
def test_resolve_rejects_unsafe_jamulus_selector_name(name: str) -> None:
    device = _device("unsafe", name, 10)

    with pytest.raises(CoreAudioRouteError, match="cannot be used by Jamulus"):
        resolve_coreaudio_device(_scan(device), "unsafe", "input")


def test_resolve_rejects_unavailable_scan_without_exposing_backend_error() -> None:
    scan = CoreAudioScan(error="private native error")

    with pytest.raises(CoreAudioRouteError, match="Audio devices could not be read") as error:
        resolve_coreaudio_device(scan, "anything", "input")
    assert "private native error" not in str(error.value)


def test_device_generation_is_deterministic_and_excludes_transient_object_id() -> None:
    original = _device("uid", "Studio Interface", 10)
    moved_object = replace(original, object_id=99)
    changed_rate = replace(original, nominal_rate=44_100.0)

    assert coreaudio_device_generation(_scan(original)) == coreaudio_device_generation(
        _scan(moved_object)
    )
    assert coreaudio_device_generation(_scan(original)) != coreaudio_device_generation(
        _scan(changed_rate)
    )


def test_invalid_direction_is_rejected() -> None:
    with pytest.raises(ValueError, match="'input' or 'output'"):
        resolve_coreaudio_device(_scan(_device("uid", "Interface", 1)), "uid", "send")
