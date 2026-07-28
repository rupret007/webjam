"""macOS Reference Track route and second-client ownership tests."""

from __future__ import annotations

import base64
from pathlib import Path
import subprocess
import time
from xml.etree import ElementTree

import numpy as np
import pytest

from core.coreaudio_devices import CoreAudioDevice, CoreAudioScan
from core.coreaudio_process_route import (
    CoreAudioProcessRouteError,
    CoreAudioProcessRouteSnapshot,
)
from core.macos_audio_route import jamulus_macos_config_directory
from core.reference_track import ReferenceTrackLaunchContext
from services.reference_track_backend import (
    create_reference_audio_backend,
    MacOSBlackHoleReferenceBackend,
    REFERENCE_PARTICIPANT_NAME,
    REFERENCE_PROFILE_FILENAME,
    REFERENCE_SECRET_FILENAME,
    _ReferenceRpcControl,
)
import services.reference_track_backend as reference_backend


def _device(
    *,
    channels: int = 16,
    rate: float = 48_000.0,
    name: str = "BlackHole 16ch",
) -> CoreAudioDevice:
    return CoreAudioDevice(
        uid="BlackHole_UID",
        name=name,
        object_id=77,
        input_channels=channels,
        output_channels=channels,
        nominal_rate=rate,
    )


def _scan(*devices: CoreAudioDevice) -> CoreAudioScan:
    return CoreAudioScan(devices=tuple(devices))


class _OutputStream:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class _SoundDevice:
    def __init__(self, *, name: str = "BlackHole 16ch", channels: int = 16) -> None:
        self.name = name
        self.channels = channels
        self.streams: list[_OutputStream] = []

    def query_devices(self):
        return [
            {
                "name": self.name,
                "max_input_channels": self.channels,
                "max_output_channels": self.channels,
                "default_samplerate": 48_000.0,
            }
        ]

    def OutputStream(self, **kwargs):
        stream = _OutputStream(**kwargs)
        self.streams.append(stream)
        return stream


class _Process:
    def __init__(self) -> None:
        self.returncode = None
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated += 1
        self.returncode = 0

    def wait(self, timeout=None):
        del timeout
        return self.returncode

    def kill(self) -> None:
        self.killed += 1
        self.returncode = -9


class _UnstoppableProcess(_Process):
    def terminate(self) -> None:
        self.terminated += 1
        raise OSError("refused")

    def kill(self) -> None:
        self.killed += 1
        raise OSError("refused")


class _Rpc:
    def __init__(self, port: int, secret: str) -> None:
        self.port = port
        self.secret = secret
        self.connected = 0
        self.proofs = 0
        self.closed = 0

    def connect(self) -> None:
        self.connected += 1

    def prove_all_faders_zero(self) -> int:
        self.proofs += 1
        return 2

    def close(self) -> None:
        self.closed += 1
        self.secret = ""


class _LiveRouteProbe:
    def __init__(
        self,
        *,
        input_device: CoreAudioDevice | None = None,
        output_device: CoreAudioDevice | None = None,
        capability_error: str = "",
    ) -> None:
        self.input_device = input_device or CoreAudioDevice(
            uid="primary-input",
            name="Built-in Microphone",
            object_id=501,
            input_channels=2,
            output_channels=0,
            nominal_rate=48_000.0,
        )
        self.output_device = output_device or CoreAudioDevice(
            uid="primary-output",
            name="Built-in Output",
            object_id=502,
            input_channels=0,
            output_channels=2,
            nominal_rate=48_000.0,
        )
        self.error = ""
        self._capability_error = capability_error
        self.calls: list[tuple[int, CoreAudioScan]] = []

    def capability_error(self) -> str:
        return self._capability_error

    def snapshot(
        self, pid: int, scan: CoreAudioScan
    ) -> CoreAudioProcessRouteSnapshot:
        self.calls.append((pid, scan))
        if self.error:
            raise CoreAudioProcessRouteError(self.error)
        return CoreAudioProcessRouteSnapshot(
            pid=pid,
            process_object_id=601,
            input_device=self.input_device,
            output_device=self.output_device,
        )


@pytest.fixture(autouse=True)
def _use_live_process_route_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        reference_backend,
        "CoreAudioProcessRouteProbe",
        lambda **_kwargs: _LiveRouteProbe(),
    )


def _context(binary: Path, **changes) -> ReferenceTrackLaunchContext:
    values = {
        "server_address": "127.0.0.1:22124",
        "jamulus_binary": str(binary),
        "primary_udp_port": 22124,
        "primary_rpc_port": 22222,
        "primary_process_id": 4242,
        "primary_input_device_name": "Built-in Microphone",
        "primary_output_device_name": "Built-in Output",
    }
    values.update(changes)
    return ReferenceTrackLaunchContext(**values)


def test_production_backend_locks_uncertified_route_before_any_native_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    binary = tmp_path / "Jamulus"
    # Even a seemingly helpful environment switch must not unlock playback.
    monkeypatch.setenv("WEBJAM_REFERENCE_TRACK_CERTIFIED", "1")
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: calls.append("scan") or _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: calls.append("version") or "3.12.2",
        headless_client_probe=lambda _binary: calls.append("headless") or True,
        popen_factory=lambda *_args, **_kwargs: calls.append("launch"),
        home=tmp_path,
    )

    capability = backend.capability()

    assert capability.available is False
    assert "engine is included" in capability.detail
    assert "playback is locked" in capability.detail
    assert "physical macOS pilot" in capability.detail
    assert backend.capability(audience_bridge_active=True).detail == capability.detail
    with pytest.raises(Exception, match="playback is locked"):
        backend.prepare(_context(binary))
    assert calls == []
    assert not jamulus_macos_config_directory(tmp_path).exists()


def test_physical_route_certification_seam_requires_an_explicit_boolean() -> None:
    with pytest.raises(TypeError, match="must be a boolean"):
        MacOSBlackHoleReferenceBackend(
            platform="darwin",
            physical_route_certified="true",  # type: ignore[arg-type]
        )


def test_production_factory_never_enables_the_source_pilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reference_backend.sys, "platform", "darwin")

    backend = create_reference_audio_backend()

    assert isinstance(backend, MacOSBlackHoleReferenceBackend)
    capability = backend.capability()
    assert capability.available is False
    assert "playback is locked" in capability.detail


def test_capability_is_macos_only_conflict_aware_and_requires_split_channels() -> None:
    sd = _SoundDevice()
    available = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=sd,
        physical_route_certified=True,
    )
    capability = available.capability()
    assert capability.available is True
    assert capability.route_name == "BlackHole 16ch"
    assert "verify" in capability.detail

    conflict = available.capability(audience_bridge_active=True)
    assert conflict.available is False
    assert "audience bridge" in conflict.detail

    two_channel = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device(channels=2, name="BlackHole 2ch")),
        sounddevice_module=_SoundDevice(name="BlackHole 2ch", channels=2),
        physical_route_certified=True,
    ).capability()
    assert two_channel.available is False
    assert "2ch cannot isolate" in two_channel.detail

    windows = MacOSBlackHoleReferenceBackend(
        platform="win32",
        scanner=lambda: _scan(_device()),
        sounddevice_module=sd,
    ).capability()
    assert windows.available is False
    assert "Windows" in windows.detail

    unsupported_live_proof = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=sd,
        process_route_probe=_LiveRouteProbe(
            capability_error=(
                "Reference Track requires macOS 14.2 or later because macOS 13 "
                "cannot prove the primary Jamulus route."
            )
        ),
        physical_route_certified=True,
    ).capability()
    assert unsupported_live_proof.available is False
    assert "macOS 14.2 or later" in unsupported_live_proof.detail


def test_capability_rejects_wrong_rate_and_ambiguous_device_name() -> None:
    wrong_rate = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device(rate=44_100.0)),
        sounddevice_module=_SoundDevice(),
        physical_route_certified=True,
    ).capability()
    assert wrong_rate.available is False
    assert "48 kHz" in wrong_rate.detail

    first = _device()
    duplicate = CoreAudioDevice(
        uid="Other_UID",
        name=first.name,
        object_id=78,
        input_channels=16,
        output_channels=16,
        nominal_rate=48_000.0,
    )
    ambiguous = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(first, duplicate),
        sounddevice_module=_SoundDevice(),
        physical_route_certified=True,
    ).capability()
    assert ambiguous.available is False
    assert "More than one" in ambiguous.detail


def test_zero_fader_proof_uses_array_positions_and_exact_pinned_result() -> None:
    rpc = _ReferenceRpcControl(12345, "private")
    calls = []

    def accepted(method, params):
        calls.append((method, params))
        if method == "jamulusclient/getClientList":
            return {"clients": [{"id": 7}, {"id": 2}]}
        return "ok"

    rpc.call = accepted  # type: ignore[method-assign]
    assert rpc.prove_all_faders_zero() == 2
    assert [
        call for call in calls if call[0] == "jamulusclient/setFaderLevel"
    ] == [
        (
            "jamulusclient/setFaderLevel",
            {"channelIndex": 0, "level": 0},
        ),
        (
            "jamulusclient/setFaderLevel",
            {"channelIndex": 1, "level": 0},
        ),
    ]

    rpc.call = (  # type: ignore[method-assign]
        lambda method, _params: (
            {"clients": [{"id": 1}]}
            if method == "jamulusclient/getClientList"
            else "acknowledged"
        )
    )
    with pytest.raises(Exception, match="couldn't prove"):
        rpc.prove_all_faders_zero()


def test_zero_fader_proof_rejects_malformed_or_changing_rosters() -> None:
    rpc = _ReferenceRpcControl(12345, "private")
    rpc.call = (  # type: ignore[method-assign]
        lambda method, _params: (
            {"clients": [{"id": 1}, {"name": "missing id"}]}
            if method == "jamulusclient/getClientList"
            else "ok"
        )
    )
    with pytest.raises(Exception, match="couldn't verify"):
        rpc.prove_all_faders_zero()

    rosters = iter(
        (
            {"clients": [{"id": 9}, {"id": 3}]},
            {"clients": [{"id": 3}, {"id": 9}]},
        )
    )
    rpc.call = (  # type: ignore[method-assign]
        lambda method, _params: (
            next(rosters)
            if method == "jamulusclient/getClientList"
            else "ok"
        )
    )
    with pytest.raises(Exception, match="roster changed"):
        rpc.prove_all_faders_zero()

    # A reconnect can preserve array order while replacing a server identity.
    reconnect_rosters = iter(
        (
            {"clients": [{"id": 11}, {"id": 15}]},
            {"clients": [{"id": 11}, {"id": 22}]},
        )
    )
    rpc.call = (  # type: ignore[method-assign]
        lambda method, _params: (
            next(reconnect_rosters)
            if method == "jamulusclient/getClientList"
            else "ok"
        )
    )
    with pytest.raises(Exception, match="roster changed"):
        rpc.prove_all_faders_zero()

    rpc.call = (  # type: ignore[method-assign]
        lambda method, _params: (
            {"clients": [{"id": "1"}]}
            if method == "jamulusclient/getClientList"
            else "ok"
        )
    )
    with pytest.raises(Exception, match="couldn't verify"):
        rpc.prove_all_faders_zero()


def test_default_headless_probe_rejects_gui_and_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reference_backend.sys, "platform", "darwin")

    def probe_with(output: str, *, returncode: int = 0):
        return subprocess.CompletedProcess(
            args=["otool"],
            returncode=returncode,
            stdout=output,
            stderr="",
        )

    monkeypatch.setattr(
        reference_backend.subprocess,
        "run",
        lambda *_args, **_kwargs: probe_with(
            "/System/Library/Frameworks/QtWidgets.framework/QtWidgets"
        ),
    )
    assert reference_backend._default_headless_client_probe("/tmp/Jamulus") is False

    monkeypatch.setattr(
        reference_backend.subprocess,
        "run",
        lambda *_args, **_kwargs: probe_with(
            "/System/Library/Frameworks/QtCore.framework/QtCore"
        ),
    )
    assert reference_backend._default_headless_client_probe("/tmp/Jamulus") is True

    monkeypatch.setattr(
        reference_backend.subprocess,
        "run",
        lambda *_args, **_kwargs: probe_with("", returncode=1),
    )
    assert reference_backend._default_headless_client_probe("/tmp/Jamulus") is False


def test_gui_client_is_rejected_before_process_ports_or_files(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    launches = []
    port_calls = []
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: False,
        popen_factory=lambda *args, **kwargs: launches.append((args, kwargs)),
        port_allocator=lambda kind, excluded: (
            port_calls.append((kind, excluded)) or 33101
        ),
        home=tmp_path,
        physical_route_certified=True,
    )

    with pytest.raises(Exception, match="headless Jamulus"):
        backend.prepare(_context(binary))
    assert launches == []
    assert port_calls == []
    assert not jamulus_macos_config_directory(tmp_path).exists()


@pytest.mark.parametrize("direction", ("input", "output"))
def test_live_primary_blackhole_conflict_fails_before_binary_or_files(
    tmp_path: Path,
    direction: str,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    version_calls = []
    headless_calls = []
    launches = []
    blackhole = _device()
    physical_input = _LiveRouteProbe().input_device
    physical_output = _LiveRouteProbe().output_device
    live_probe = _LiveRouteProbe(
        input_device=blackhole if direction == "input" else physical_input,
        output_device=blackhole if direction == "output" else physical_output,
    )
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        process_route_probe=live_probe,
        version_probe=lambda value: version_calls.append(value) or "3.12.2",
        headless_client_probe=lambda value: headless_calls.append(value) or True,
        popen_factory=lambda *args, **kwargs: launches.append((args, kwargs)),
        home=tmp_path,
        physical_route_certified=True,
    )

    with pytest.raises(Exception, match="primary Jamulus client is using BlackHole"):
        backend.prepare(_context(binary))
    assert version_calls == []
    assert headless_calls == []
    assert launches == []
    assert not jamulus_macos_config_directory(tmp_path).exists()


def test_saved_profile_names_are_not_accepted_over_changed_live_route(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    live_probe = _LiveRouteProbe(
        input_device=CoreAudioDevice(
            uid="new-input",
            name="Different Live Input",
            object_id=701,
            input_channels=2,
            output_channels=0,
            nominal_rate=48_000.0,
        )
    )
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        process_route_probe=live_probe,
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        home=tmp_path,
        physical_route_certified=True,
    )

    with pytest.raises(Exception, match="does not match its current launch profile"):
        backend.prepare(_context(binary))
    assert not jamulus_macos_config_directory(tmp_path).exists()


def test_owned_second_client_has_separate_profile_ports_secret_and_route(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    sd = _SoundDevice()
    process = _Process()
    popen_calls = []
    rpc_instances: list[_Rpc] = []
    ports = iter((33101, 33102))

    def popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return process

    def rpc_factory(port: int, secret: str):
        rpc = _Rpc(port, secret)
        rpc_instances.append(rpc)
        return rpc

    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=sd,
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=popen,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=rpc_factory,
        home=tmp_path,
        physical_route_certified=True,
    )

    session = backend.prepare(_context(binary))

    config_dir = jamulus_macos_config_directory(tmp_path)
    config_path = config_dir / REFERENCE_PROFILE_FILENAME
    secret_path = config_dir / REFERENCE_SECRET_FILENAME
    assert config_path.is_file()
    assert secret_path.is_file()
    assert secret_path.stat().st_mode & 0o777 == 0o600
    command, kwargs = popen_calls[0]
    assert command[0] == str(binary)
    assert "--nogui" in command
    assert "--mutemyown" in command
    assert command[command.index("--port") + 1] == "33101"
    assert command[command.index("--jsonrpcport") + 1] == "33102"
    assert command[command.index("--jsonrpcbindip") + 1] == "127.0.0.1"
    assert command[command.index("--inifile") + 1] == REFERENCE_PROFILE_FILENAME
    assert command[command.index("--clientname") + 1] == REFERENCE_PARTICIPANT_NAME
    assert kwargs["cwd"] == str(config_dir)
    assert "22124" not in {
        command[command.index("--port") + 1],
        command[command.index("--jsonrpcport") + 1],
    }
    assert "22222" not in {
        command[command.index("--port") + 1],
        command[command.index("--jsonrpcport") + 1],
    }

    settings = {
        child.tag: child.text or ""
        for child in ElementTree.fromstring(config_path.read_bytes())
    }
    assert base64.b64decode(settings["name_base64"]).decode() == (
        REFERENCE_PARTICIPANT_NAME
    )
    assert base64.b64decode(settings["auddev_base64"]).decode() == (
        "in: BlackHole 16ch/out: BlackHole 16ch"
    )
    assert settings["sndcrdinlch"] == "0"
    assert settings["sndcrdinrch"] == "1"
    assert settings["sndcrdoutlch"] == "2"
    assert settings["sndcrdoutrch"] == "3"
    assert settings["audiochannels"] == "2"
    assert rpc_instances[0].port == 33102
    assert rpc_instances[0].connected == 0
    assert rpc_instances[0].proofs == 0

    session.start(lambda frames: np.full((frames, 2), 0.125, dtype=np.float32))
    assert rpc_instances[0].connected == 1
    assert rpc_instances[0].proofs >= 1
    stream = sd.streams[-1]
    assert stream.started is True
    assert stream.kwargs["device"] == 0
    assert stream.kwargs["channels"] == 2
    out = np.zeros((256, 2), dtype=np.float32)
    stream.kwargs["callback"](out, 256, None, None)
    np.testing.assert_allclose(out, 0.125)

    session.stop()
    assert stream.stopped is True
    assert stream.closed is True
    assert process.terminated == 1
    assert rpc_instances[0].closed == 1
    assert not secret_path.exists()
    assert not config_path.exists()


def test_existing_owned_profile_is_restored_after_session(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    config_dir = jamulus_macos_config_directory(tmp_path)
    config_dir.mkdir(parents=True)
    config_path = config_dir / REFERENCE_PROFILE_FILENAME
    config_path.write_bytes(b"previous owned profile\n")
    process = _Process()
    ports = iter((33201, 33202))

    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: process,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )
    session = backend.prepare(_context(binary))

    assert config_path.read_bytes().startswith(b"<?xml")
    session.stop()
    assert config_path.read_bytes() == b"previous owned profile\n"
    assert not config_path.with_name(config_path.name + ".bak").exists()


def test_surviving_owned_process_keeps_evidence_and_stop_fails_closed(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _UnstoppableProcess()
    ports = iter((33301, 33302))
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: process,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )
    session = backend.prepare(_context(binary))
    config_dir = jamulus_macos_config_directory(tmp_path)
    config_path = config_dir / REFERENCE_PROFILE_FILENAME
    secret_path = config_dir / REFERENCE_SECRET_FILENAME

    with pytest.raises(Exception, match="couldn't confirm"):
        session.stop()

    assert process.poll() is None
    # Keep exact control/profile evidence while an owned process may still
    # read it; never publish a clean stop or silently erase recovery facts.
    assert config_path.exists()
    assert secret_path.exists()
    assert "couldn't confirm" in session.health_error()

    process.returncode = 0
    session.stop()
    assert not config_path.exists()
    assert not secret_path.exists()


def test_route_loss_silences_stream_and_becomes_unhealthy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reference_backend, "_FADER_RECHECK_SECONDS", 0.01)
    monkeypatch.setattr(reference_backend, "_ROUTE_RECHECK_SECONDS", 0.01)
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    current = {"scan": _scan(_device())}
    process = _Process()
    ports = iter((33401, 33402))
    sd = _SoundDevice()
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: current["scan"],
        sounddevice_module=sd,
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: process,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )
    session = backend.prepare(_context(binary))
    session.start(lambda frames: np.ones((frames, 2), dtype=np.float32))
    current["scan"] = _scan()

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not session.health_error():
        time.sleep(0.01)
    assert "route changed" in session.health_error()

    out = np.ones((128, 2), dtype=np.float32)
    sd.streams[-1].kwargs["callback"](out, 128, None, None)
    assert np.count_nonzero(out) == 0
    session.stop()


def test_live_primary_route_change_silences_without_owning_primary_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reference_backend, "_FADER_RECHECK_SECONDS", 0.01)
    monkeypatch.setattr(reference_backend, "_ROUTE_RECHECK_SECONDS", 0.01)
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    reference_process = _Process()
    ports = iter((33501, 33502))
    sd = _SoundDevice()
    live_probe = _LiveRouteProbe()
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=sd,
        process_route_probe=live_probe,
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: reference_process,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )
    session = backend.prepare(_context(binary, primary_process_id=9876))
    session.start(lambda frames: np.ones((frames, 2), dtype=np.float32))

    live_probe.output_device = CoreAudioDevice(
        uid="changed-output",
        name="Changed Live Output",
        object_id=799,
        input_channels=0,
        output_channels=2,
        nominal_rate=48_000.0,
    )
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not session.health_error():
        time.sleep(0.01)

    assert session.health_error()
    assert all(call[0] == 9876 for call in live_probe.calls)
    out = np.ones((128, 2), dtype=np.float32)
    sd.streams[-1].kwargs["callback"](out, 128, None, None)
    assert np.count_nonzero(out) == 0
    # The route session owns only its separately launched process. It has no
    # primary Popen handle to terminate or mutate.
    assert reference_process.terminated == 0
    session.stop()
    assert reference_process.terminated == 1


def test_stale_live_route_proof_silences_callback_even_before_monitor_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reference_backend, "_FADER_RECHECK_SECONDS", 5.0)
    monkeypatch.setattr(reference_backend, "_ROUTE_PROOF_MAX_AGE_SECONDS", 0.001)
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    ports = iter((33601, 33602))
    sd = _SoundDevice()
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=sd,
        process_route_probe=_LiveRouteProbe(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: process,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )
    session = backend.prepare(_context(binary))
    session.start(lambda frames: np.ones((frames, 2), dtype=np.float32))
    time.sleep(0.01)

    out = np.ones((128, 2), dtype=np.float32)
    sd.streams[-1].kwargs["callback"](out, 128, None, None)

    assert np.count_nonzero(out) == 0
    assert "proof became stale" in session.health_error()
    session.stop()


def test_audience_bridge_conflict_fails_before_process_or_files(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    launches = []
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        popen_factory=lambda *args, **kwargs: launches.append((args, kwargs)),
        home=tmp_path,
        physical_route_certified=True,
    )

    with pytest.raises(Exception, match="audience bridge"):
        backend.prepare(_context(binary, audience_bridge_active=True))
    assert not launches
    assert not jamulus_macos_config_directory(tmp_path).exists()
