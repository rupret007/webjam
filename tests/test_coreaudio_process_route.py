"""Pure tests for PID-bound live CoreAudio route evidence."""

from __future__ import annotations

from collections import deque

import pytest

from core.coreaudio_devices import CoreAudioDevice, CoreAudioDirection, CoreAudioScan
from core.coreaudio_process_route import (
    CoreAudioProcessRouteError,
    CoreAudioProcessRouteProbe,
)


def _device(
    object_id: int,
    *,
    uid: str,
    name: str,
    inputs: int = 2,
    outputs: int = 2,
) -> CoreAudioDevice:
    return CoreAudioDevice(
        uid=uid,
        name=name,
        object_id=object_id,
        input_channels=inputs,
        output_channels=outputs,
        nominal_rate=48_000.0,
    )


class _Backend:
    def __init__(
        self,
        *,
        supported: bool = True,
        process_objects=(91, 91),
        input_devices=((10,), (10,)),
        output_devices=((20,), (20,)),
        input_running=(True, True),
        output_running=(True, True),
    ) -> None:
        self.supported = supported
        self.process_objects = deque(process_objects)
        self.devices = {
            CoreAudioDirection.INPUT: deque(input_devices),
            CoreAudioDirection.OUTPUT: deque(output_devices),
        }
        self.running = {
            CoreAudioDirection.INPUT: deque(input_running),
            CoreAudioDirection.OUTPUT: deque(output_running),
        }

    def supports_process_route_query(self) -> bool:
        return self.supported

    def process_object_id(self, _pid: int) -> int | None:
        value = self.process_objects[0]
        if len(self.process_objects) > 1:
            value = self.process_objects.popleft()
        return value

    def process_device_ids(
        self, _process_object_id: int, direction: CoreAudioDirection
    ) -> tuple[int, ...]:
        values = self.devices[direction]
        value = values[0]
        if len(values) > 1:
            value = values.popleft()
        return tuple(value)

    def process_io_running(
        self, _process_object_id: int, direction: CoreAudioDirection
    ) -> bool:
        values = self.running[direction]
        value = values[0]
        if len(values) > 1:
            value = values.popleft()
        return bool(value)


def _scan() -> CoreAudioScan:
    return CoreAudioScan(
        devices=(
            _device(10, uid="mic-uid", name="Band Microphone", outputs=0),
            _device(20, uid="phones-uid", name="Band Headphones", inputs=0),
        )
    )


def _probe(backend: _Backend, *, version: str = "14.2"):
    return CoreAudioProcessRouteProbe(
        backend=backend,
        platform_name="darwin",
        macos_version=version,
    )


def test_process_route_capability_keeps_macos_13_unavailable() -> None:
    backend = _Backend()
    error = _probe(backend, version="13.6").capability_error()

    assert "macOS 14.2 or later" in error
    assert backend.process_objects == deque((91, 91))


def test_process_route_capability_fails_closed_when_hal_property_is_missing() -> None:
    error = _probe(_Backend(supported=False)).capability_error()

    assert "cannot provide live primary Jamulus route evidence" in error


def test_process_route_snapshot_proves_stable_active_directional_devices() -> None:
    snapshot = _probe(_Backend()).snapshot(4321, _scan())

    assert snapshot.pid == 4321
    assert snapshot.process_object_id == 91
    assert snapshot.input_device.uid == "mic-uid"
    assert snapshot.output_device.uid == "phones-uid"


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"process_objects": (91, None)}, "changed while WebJam"),
        ({"input_devices": ((10,), (11,))}, "changed while WebJam"),
        ({"output_devices": ((20,), (21,))}, "changed while WebJam"),
        ({"input_running": (True, False)}, "changed while WebJam"),
        ({"input_running": (False, False)}, "actively running"),
        ({"output_running": (False, False)}, "actively running"),
        ({"input_devices": ((), ())}, "missing or ambiguous"),
        ({"output_devices": ((20, 21), (20, 21))}, "missing or ambiguous"),
    ),
)
def test_process_route_snapshot_rejects_changed_inactive_or_ambiguous_truth(
    changes: dict,
    message: str,
) -> None:
    with pytest.raises(CoreAudioProcessRouteError, match=message):
        _probe(_Backend(**changes)).snapshot(4321, _scan())


def test_process_route_snapshot_rejects_device_missing_from_fresh_scan() -> None:
    scan = CoreAudioScan(
        devices=(
            _device(10, uid="mic-uid", name="Band Microphone", outputs=0),
        )
    )

    with pytest.raises(CoreAudioProcessRouteError, match="matched unambiguously"):
        _probe(_Backend()).snapshot(4321, scan)


@pytest.mark.parametrize("pid", (0, -1, True))
def test_process_route_snapshot_rejects_invalid_pid(pid: object) -> None:
    with pytest.raises(CoreAudioProcessRouteError, match="identify"):
        _probe(_Backend()).snapshot(pid, _scan())  # type: ignore[arg-type]
