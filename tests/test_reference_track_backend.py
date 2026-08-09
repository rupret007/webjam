"""macOS Shared Track route and second-client ownership tests."""

from __future__ import annotations

import base64
import errno
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
import traceback
from xml.etree import ElementTree

import numpy as np
import pytest

from core.coreaudio_devices import CoreAudioDevice, CoreAudioScan
from core.coreaudio_process_route import (
    CoreAudioProcessRouteError,
    CoreAudioProcessRouteSnapshot,
)
from core.component_lock import InterProcessComponentLock
from core.reference_track import ReferenceTrackLaunchContext
import core.secure_runtime as secure_runtime
from services.reference_track_backend import (
    create_reference_audio_backend,
    MacOSBlackHoleReferenceBackend,
    REFERENCE_PARTICIPANT_NAME,
    REFERENCE_PROFILE_FILENAME,
    _claim_blackhole_route,
    _ReferenceRpcControl,
    _reference_track_lock_path,
    reference_track_runtime_directory,
)
import services.reference_track_backend as reference_backend


def _legacy_jamulus_container_directory(home: Path) -> Path:
    return (
        home
        / "Library"
        / "Containers"
        / "app.jamulussoftware.Jamulus"
        / "Data"
        / ".config"
        / "Jamulus"
    )


def _hold_reference_track_lock(
    home: str,
    ready,
    release,
) -> None:
    lease = _claim_blackhole_route(
        "BlackHole16ch_UID",
        home=Path(home),
    )
    try:
        ready.set()
        release.wait(timeout=10.0)
    finally:
        lease.release()


def _device(
    *,
    channels: int = 16,
    rate: float = 48_000.0,
    name: str = "BlackHole 16ch",
    uid: str | None = None,
) -> CoreAudioDevice:
    if uid is None:
        uid = {
            "BlackHole 16ch": "BlackHole16ch_UID",
            "BlackHole 64ch": "BlackHole64ch_UID",
            "BlackHole 2ch": "BlackHole2ch_UID",
        }.get(name, "custom-device-uid")
    return CoreAudioDevice(
        uid=uid,
        name=name,
        object_id=77,
        input_channels=channels,
        output_channels=channels,
        nominal_rate=rate,
    )


def _scan(*devices: CoreAudioDevice) -> CoreAudioScan:
    return CoreAudioScan(devices=tuple(devices))


def test_reference_runtime_belongs_to_webjam_not_jamulus_container(
    tmp_path: Path,
) -> None:
    runtime = reference_track_runtime_directory(tmp_path)

    assert runtime == (
        tmp_path
        / "Library"
        / "Application Support"
        / "WebJam"
        / "runtime"
        / "reference-track"
    )
    assert runtime != _legacy_jamulus_container_directory(tmp_path)
    assert "Containers" not in runtime.parts


def test_reference_version_probe_uses_bounded_environment_and_neutral_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "JamulusHeadlessClient"
    observed: dict[str, object] = {}
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/tmp/injected.dylib")
    monkeypatch.setenv("LD_PRELOAD", "/tmp/injected.so")
    monkeypatch.setenv("QML2_IMPORT_PATH", "/tmp/qml")
    monkeypatch.setenv("QTWEBENGINEPROCESS_PATH", "/tmp/qt-helper")
    monkeypatch.setenv("WEBJAM_DIAGNOSTIC", "safe")
    monkeypatch.setenv("PATH", "/tmp/untrusted")

    def run(arguments, **kwargs):
        observed["arguments"] = list(arguments)
        observed["kwargs"] = dict(kwargs)
        return subprocess.CompletedProcess(
            arguments,
            0,
            "Jamulus version 3.12.2\n",
            "",
        )

    monkeypatch.setattr(reference_backend.subprocess, "run", run)

    assert reference_backend._default_version_probe(str(binary)) == "3.12.2"
    assert observed["arguments"] == [str(binary), "--version"]
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == (
        str(binary.parent) if sys.platform.startswith("win") else "/"
    )
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["WEBJAM_DIAGNOSTIC"] == "safe"
    assert not any(
        key.upper().startswith(("DYLD_", "LD_", "QML", "QT"))
        for key in environment
    )
    assert environment["PATH"] != "/tmp/untrusted"


@pytest.mark.skipif(
    not sys.platform.startswith("darwin"),
    reason="otool proof is macOS-only",
)
def test_headless_probe_uses_system_otool_with_bounded_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setenv("DYLD_INSERT_LIBRARIES", "/tmp/injected.dylib")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/tmp/plugins")
    monkeypatch.setenv("PATH", "/tmp/untrusted")

    def run(arguments, **kwargs):
        observed["arguments"] = list(arguments)
        observed["kwargs"] = dict(kwargs)
        return subprocess.CompletedProcess(arguments, 0, "libSystem.B.dylib\n", "")

    monkeypatch.setattr(reference_backend.subprocess, "run", run)

    binary = "/Applications/WebJam.app/Contents/MacOS/JamulusHeadlessClient"
    assert reference_backend._default_headless_client_probe(binary) is True
    assert observed["arguments"] == ["/usr/bin/otool", "-L", binary]
    kwargs = observed["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == "/"
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert "DYLD_INSERT_LIBRARIES" not in environment
    assert "QT_PLUGIN_PATH" not in environment
    assert environment["PATH"] == "/usr/bin:/bin"


@pytest.mark.skipif(os.name == "nt", reason="no-follow dirfd test needs POSIX")
@pytest.mark.parametrize(
    "linked_component",
    ("WebJam", "runtime", "reference-track"),
)
def test_reference_runtime_rejects_every_managed_symlink_without_touching_target(
    tmp_path: Path,
    linked_component: str,
) -> None:
    support = tmp_path / "Library" / "Application Support"
    support.mkdir(parents=True)
    webjam = support / "WebJam"
    runtime = webjam / "runtime"
    reference = runtime / "reference-track"
    outside = tmp_path / f"outside-{linked_component}"
    outside.mkdir(mode=0o751)
    outside.chmod(0o751)
    marker = outside / "keep.txt"
    marker.write_bytes(b"outside stays untouched\n")

    if linked_component == "WebJam":
        link = webjam
    elif linked_component == "runtime":
        webjam.mkdir(mode=0o700)
        link = runtime
    else:
        webjam.mkdir(mode=0o700)
        runtime.mkdir(mode=0o700)
        link = reference
    link.symlink_to(outside, target_is_directory=True)
    outside_mode = stat.S_IMODE(outside.stat().st_mode)

    with pytest.raises(Exception, match="establish"):
        reference_backend._ReferencePrivateFiles.open(
            reference_track_runtime_directory(tmp_path),
            home=tmp_path,
        )

    assert link.is_symlink()
    assert stat.S_IMODE(outside.stat().st_mode) == outside_mode == 0o751
    assert marker.read_bytes() == b"outside stays untouched\n"
    assert tuple(outside.iterdir()) == (marker,)


@pytest.mark.skipif(os.name == "nt", reason="no-follow dirfd test needs POSIX")
def test_reference_runtime_rejects_owner_mismatch_before_chmod(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = reference_track_runtime_directory(tmp_path).parent
    runtime.mkdir(parents=True, mode=0o755)
    runtime.chmod(0o755)
    runtime_identity = (runtime.stat().st_dev, runtime.stat().st_ino)
    original_owner_check = secure_runtime._owned

    def reject_runtime_owner(details: os.stat_result) -> bool:
        if (details.st_dev, details.st_ino) == runtime_identity:
            return False
        return original_owner_check(details)

    monkeypatch.setattr(
        secure_runtime,
        "_owned",
        reject_runtime_owner,
    )

    with pytest.raises(Exception, match="establish"):
        reference_backend._ReferencePrivateFiles.open(
            reference_track_runtime_directory(tmp_path),
            home=tmp_path,
        )

    assert stat.S_IMODE(runtime.stat().st_mode) == 0o755
    assert not (runtime / "reference-track").exists()


@pytest.mark.skipif(os.name == "nt", reason="no-follow dirfd test needs POSIX")
def test_reference_lifecycle_lock_rejects_symlink_without_touching_target(
    tmp_path: Path,
) -> None:
    pinned = reference_backend._open_webjam_runtime_directory(
        tmp_path,
        reference_track=False,
    )
    pinned.close()
    outside = tmp_path / "unrelated-lock-target"
    outside.write_bytes(b"unrelated lock bytes\n")
    outside.chmod(0o644)
    lock_path = _reference_track_lock_path(tmp_path)
    lock_path.symlink_to(outside)

    with pytest.raises(Exception, match="couldn't reserve"):
        _claim_blackhole_route("BlackHole16ch_UID", home=tmp_path)

    assert lock_path.is_symlink()
    assert outside.read_bytes() == b"unrelated lock bytes\n"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644

    lock_path.unlink()
    lease = _claim_blackhole_route("BlackHole16ch_UID", home=tmp_path)
    lease.release()


def test_reference_lifecycle_lock_is_private_and_single_link(
    tmp_path: Path,
) -> None:
    lease = _claim_blackhole_route("BlackHole16ch_UID", home=tmp_path)
    try:
        details = _reference_track_lock_path(tmp_path).stat()
        assert stat.S_ISREG(details.st_mode)
        assert stat.S_IMODE(details.st_mode) == 0o600
        assert details.st_nlink == 1
    finally:
        lease.release()


@pytest.mark.skipif(os.name == "nt", reason="no-follow dirfd test needs POSIX")
def test_reference_lifecycle_lock_rejects_symlinked_parent_and_recovers(
    tmp_path: Path,
) -> None:
    webjam = (
        tmp_path
        / "Library"
        / "Application Support"
        / "WebJam"
    )
    webjam.mkdir(parents=True, mode=0o700)
    outside = tmp_path / "outside-runtime"
    outside.mkdir(mode=0o751)
    outside.chmod(0o751)
    marker = outside / "keep.txt"
    marker.write_bytes(b"outside runtime stays untouched\n")
    runtime = webjam / "runtime"
    runtime.symlink_to(outside, target_is_directory=True)

    with pytest.raises(Exception, match="couldn't reserve"):
        _claim_blackhole_route("BlackHole16ch_UID", home=tmp_path)

    assert runtime.is_symlink()
    assert stat.S_IMODE(outside.stat().st_mode) == 0o751
    assert marker.read_bytes() == b"outside runtime stays untouched\n"
    assert tuple(outside.iterdir()) == (marker,)

    runtime.unlink()
    lease = _claim_blackhole_route("BlackHole16ch_UID", home=tmp_path)
    lease.release()


def test_reference_child_environment_rejects_native_loader_and_plugin_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dangerous = {
        "DYLD_INSERT_LIBRARIES": "/tmp/untrusted.dylib",
        "DYLD_FRAMEWORK_PATH": "/tmp/untrusted-frameworks",
        "LD_PRELOAD": "/tmp/untrusted.so",
        "LD_LIBRARY_PATH": "/tmp/untrusted-libraries",
        "QT_PLUGIN_PATH": "/tmp/untrusted-qt",
        "QT_QPA_PLATFORM_PLUGIN_PATH": "/tmp/untrusted-qpa",
        "QTWEBENGINEPROCESS_PATH": "/tmp/untrusted-webengine",
        "QT_LOGGING_RULES": "jamulus.rpc.debug=true",
        "QML2_IMPORT_PATH": "/tmp/untrusted-qml2",
        "QML_IMPORT_PATH": "/tmp/untrusted-qml",
        "GCONV_PATH": "/tmp/untrusted-gconv",
        "dyld_insert_libraries": "/tmp/untrusted-lowercase.dylib",
    }
    for key, value in dangerous.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("WEBJAM_REFERENCE_SAFE_TEST", "preserved")

    environment = MacOSBlackHoleReferenceBackend._child_environment(
        Path(sys.executable).resolve()
    )

    assert all(
        key not in environment
        for key in dangerous
        if key != "QT_LOGGING_RULES"
    )
    assert environment["QT_LOGGING_RULES"] == "default.warning=false"
    assert environment["WEBJAM_REFERENCE_SAFE_TEST"] == "preserved"
    assert environment.get("HOME") == os.environ.get("HOME")
    assert "/tmp/untrusted" not in environment["PATH"]


class _OutputStream:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.device = kwargs["device"]
        self.samplerate = kwargs["samplerate"]
        self.channels = kwargs["channels"]
        self.blocksize = kwargs["blocksize"]
        self.dtype = kwargs["dtype"]
        self.started = False
        self.stopped = False
        self.closed = False
        self.stop_failures = 0
        self.close_failures = 0

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        if self.stop_failures:
            self.stop_failures -= 1
            raise RuntimeError("synthetic stop failure")
        self.stopped = True

    def close(self) -> None:
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError("synthetic close failure")
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
        self.pid = 9999
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


def _fill_audio(value: float = 1.0):
    def fill(output: np.ndarray) -> int:
        output.fill(np.float32(value))
        return int(output.shape[0])

    return fill


def _owned_paths(session) -> tuple[Path, Path]:
    owner = session._owned_files  # type: ignore[attr-defined]
    return owner.profile_path, owner.secret_path


class _LiveRouteProbe:
    def __init__(
        self,
        *,
        input_device: CoreAudioDevice | None = None,
        output_device: CoreAudioDevice | None = None,
        owned_input_device: CoreAudioDevice | None = None,
        owned_output_device: CoreAudioDevice | None = None,
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
        self.owned_input_device = owned_input_device
        self.owned_output_device = owned_output_device
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
        if pid == 9999:
            blackhole = next(
                device
                for device in scan.devices
                if device.uid in {"BlackHole16ch_UID", "BlackHole64ch_UID"}
            )
            return CoreAudioProcessRouteSnapshot(
                pid=pid,
                process_object_id=602,
                input_device=self.owned_input_device or blackhole,
                output_device=self.owned_output_device or blackhole,
            )
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


def test_production_backend_locks_route_without_a_proven_blackhole_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Certification stays fail-closed on hardware that cannot isolate.

    This replaces an earlier test that asserted the production backend could
    *never* certify.  That pin outlived its purpose: it made playback
    impossible on every machine, including ones whose hardware satisfies the
    documented requirement, because nothing outside tests could set the
    constructor flag.  The safety properties it was protecting are kept
    below -- no environment switch may unlock playback, no launch happens,
    and no runtime directory is created -- while authority now comes from the
    device actually being present.
    """

    calls: list[str] = []
    binary = tmp_path / "Jamulus"
    # A seemingly helpful environment switch must still not unlock playback.
    monkeypatch.setenv("WEBJAM_REFERENCE_TRACK_CERTIFIED", "1")
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(),  # no BlackHole on this machine
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: calls.append("version") or "3.12.2",
        headless_client_probe=lambda _binary: calls.append("headless") or True,
        popen_factory=lambda *_args, **_kwargs: calls.append("launch"),
        home=tmp_path,
    )

    capability = backend.capability()

    assert capability.available is False
    assert capability.backend == "blackhole"
    assert capability.reason_code == "physical_certification_required"
    # The musician is told the prerequisite they can act on, not an internal
    # release milestone they cannot.
    assert "BlackHole" in capability.detail
    assert "physical macOS pilot" not in capability.detail
    assert backend.capability(audience_bridge_active=True).detail == capability.detail
    with pytest.raises(Exception, match="BlackHole"):
        backend.prepare(_context(binary))
    assert calls == []
    assert not reference_track_runtime_directory(tmp_path).exists()


def test_production_backend_certifies_route_when_blackhole_is_proven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hardware that meets the requirement earns playback without a flag."""

    monkeypatch.setenv("WEBJAM_REFERENCE_TRACK_CERTIFIED", "0")
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        home=tmp_path,
    )

    capability = backend.capability()

    assert capability.available is True
    assert capability.reason_code == "ready"
    assert capability.backend == "blackhole"
    # Read-only inspection only; nothing is launched by asking.
    assert not reference_track_runtime_directory(tmp_path).exists()


def test_explicit_certification_override_still_locks_the_route(
    tmp_path: Path,
) -> None:
    """Tests may still pin the locked state on capable hardware."""

    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        home=tmp_path,
        physical_route_certified=False,
    )

    capability = backend.capability()

    assert capability.available is False
    assert capability.reason_code == "physical_certification_required"


def test_physical_route_certification_seam_requires_an_explicit_boolean() -> None:
    with pytest.raises(TypeError, match="must be a boolean"):
        MacOSBlackHoleReferenceBackend(
            platform="darwin",
            physical_route_certified="true",  # type: ignore[arg-type]
        )


def test_production_factory_derives_authority_from_the_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The factory hardcodes neither answer; the hardware decides.

    The previous version asserted the factory always reported unavailable,
    which was both the bug this replaces and machine-dependent: it passed on
    CI (no CoreAudio) and failed on any Mac with BlackHole installed.  Assert
    the contract instead -- no baked-in override -- so the result is
    deterministic everywhere.
    """

    monkeypatch.setattr(reference_backend.sys, "platform", "darwin")

    backend = create_reference_audio_backend()

    assert isinstance(backend, MacOSBlackHoleReferenceBackend)
    assert backend._physical_route_certified_override is None


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
    assert capability.backend == "blackhole"
    assert capability.reason_code == "ready"
    assert "verify" in capability.detail

    sixty_four = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device(channels=64, name="BlackHole 64ch")),
        sounddevice_module=_SoundDevice(name="BlackHole 64ch", channels=64),
        physical_route_certified=True,
    ).capability()
    assert sixty_four.available is True
    assert sixty_four.route_name == "BlackHole 64ch"

    conflict = available.capability(audience_bridge_active=True)
    assert conflict.available is False
    assert "audience bridge" in conflict.detail
    assert conflict.reason_code == "audience_bridge_conflict"

    two_channel = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device(channels=2, name="BlackHole 2ch")),
        sounddevice_module=_SoundDevice(name="BlackHole 2ch", channels=2),
        physical_route_certified=True,
    ).capability()
    assert two_channel.available is False
    assert "2ch cannot isolate" in two_channel.detail

    aggregate = CoreAudioDevice(
        uid="com.webjam.audio.bridge",
        name="WebJam Bridge",
        object_id=88,
        input_channels=4,
        output_channels=4,
        nominal_rate=48_000.0,
    )
    aggregate_only = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(aggregate),
        sounddevice_module=_SoundDevice(name="WebJam Bridge", channels=4),
        physical_route_certified=True,
    ).capability()
    assert aggregate_only.available is False
    assert "WebJam Bridge" in aggregate_only.detail
    assert aggregate_only.reason_code == "blackhole_unavailable"

    windows = MacOSBlackHoleReferenceBackend(
        platform="win32",
        scanner=lambda: _scan(_device()),
        sounddevice_module=sd,
    ).capability()
    assert windows.available is False
    assert "Windows" in windows.detail
    assert windows.backend == "vb-cable-jack"
    assert windows.reason_code == "windows_backend_unavailable"

    unsupported_live_proof = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=sd,
        process_route_probe=_LiveRouteProbe(
            capability_error=(
                "Shared Track requires macOS 14.2 or later because macOS 13 "
                "cannot prove the primary Jamulus route."
            )
        ),
        physical_route_certified=True,
    ).capability()
    assert unsupported_live_proof.available is False
    assert "macOS 14.2 or later" in unsupported_live_proof.detail
    assert unsupported_live_proof.reason_code == "live_route_unavailable"


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


@pytest.mark.parametrize(
    ("device", "sounddevice"),
    (
        (
            _device(uid="spoofed-blackhole-uid"),
            _SoundDevice(),
        ),
        (
            _device(name="My BlackHole 16ch", uid="BlackHole16ch_UID"),
            _SoundDevice(name="My BlackHole 16ch"),
        ),
        (
            _device(channels=8, uid="BlackHole16ch_UID"),
            _SoundDevice(channels=8),
        ),
        (
            _device(),
            _SoundDevice(channels=8),
        ),
    ),
)
def test_capability_rejects_spoofed_custom_and_partial_blackhole_routes(
    device: CoreAudioDevice,
    sounddevice: _SoundDevice,
) -> None:
    capability = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(device),
        sounddevice_module=sounddevice,
        physical_route_certified=True,
    ).capability()

    assert capability.available is False
    assert capability.reason_code == "blackhole_unavailable"
    assert (
        "exact UID" in capability.detail
        or "unambiguous 48-kHz BlackHole output" in capability.detail
    )


def test_zero_fader_proof_uses_sparse_client_local_ids_and_exact_result() -> None:
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
            {"channelIndex": 7, "level": 0},
        ),
        (
            "jamulusclient/setFaderLevel",
            {"channelIndex": 2, "level": 0},
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
    assert not reference_track_runtime_directory(tmp_path).exists()


def test_reference_track_rejects_port_base_that_cannot_fit_jamulus_retry_window(
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
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *args, **kwargs: launches.append((args, kwargs)),
        port_allocator=lambda kind, _excluded: (
            65_337 if kind == "udp" else 33_102
        ),
        home=tmp_path,
        physical_route_certified=True,
    )

    with pytest.raises(Exception, match="safe Jamulus audio port"):
        backend.prepare(_context(binary))
    assert launches == []
    assert not reference_track_runtime_directory(tmp_path).exists()


def test_default_udp_allocator_retries_ephemeral_base_above_safe_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = iter((65_400, 33_101))
    probes = []

    class Probe:
        def __init__(self, *_args) -> None:
            self.port = next(ports)
            self.closed = False
            probes.append(self)

        def bind(self, _endpoint) -> None:
            return None

        def getsockname(self):
            return ("127.0.0.1", self.port)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(reference_backend.socket, "socket", Probe)

    assert reference_backend._default_port_allocator("udp", set()) == 33_101
    assert len(probes) == 2
    assert all(probe.closed for probe in probes)


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
    assert not reference_track_runtime_directory(tmp_path).exists()


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
    assert not reference_track_runtime_directory(tmp_path).exists()


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
    resolved_udp_ports = [33142]
    regular_jamulus_dir = _legacy_jamulus_container_directory(tmp_path)
    regular_jamulus_dir.mkdir(parents=True)
    regular_profile = regular_jamulus_dir / "Jamulus.ini"
    regular_profile.write_bytes(b"regular musician profile stays untouched\n")

    def popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return process

    def rpc_factory(port: int, secret: str):
        rpc = _Rpc(port, secret)
        rpc_instances.append(rpc)
        return rpc

    def resolve_udp_port(process_id: int, requested_port: int) -> int:
        assert process_id == process.pid
        assert requested_port == 33101
        if not resolved_udp_ports:
            raise RuntimeError("synthetic native inspection failure")
        return resolved_udp_ports[0]

    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=sd,
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=popen,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=rpc_factory,
        # Jamulus uses 33101 only as its allocation base; this exact child
        # bound 33142, proved by the production resolver seam.
        udp_port_resolver=resolve_udp_port,
        home=tmp_path,
        physical_route_certified=True,
    )

    session = backend.prepare(_context(binary))

    config_dir = reference_track_runtime_directory(tmp_path)
    config_path, secret_path = _owned_paths(session)
    assert config_path.parent == config_dir
    assert secret_path.parent == config_dir
    assert config_path.name.startswith("WebJam-reference-track-v1-")
    assert secret_path.name.startswith(".WebJam-reference-track-v1-")
    assert config_path.is_file()
    assert secret_path.is_file()
    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    assert config_path.stat().st_mode & 0o777 == 0o600
    assert secret_path.stat().st_mode & 0o777 == 0o600
    command, kwargs = popen_calls[0]
    assert command[0] == str(binary)
    assert "--nogui" in command
    assert "--mutemyown" in command
    assert command[command.index("--port") + 1] == "33101"
    assert command[command.index("--jsonrpcport") + 1] == "33102"
    assert command[command.index("--jsonrpcbindip") + 1] == "127.0.0.1"
    assert command[command.index("--inifile") + 1] == str(config_path)
    assert command[command.index("--jsonrpcsecretfile") + 1] == str(secret_path)
    assert command[command.index("--clientname") + 1] == REFERENCE_PARTICIPANT_NAME
    assert kwargs["cwd"] == str(config_dir)
    assert kwargs["env"].get("HOME") == os.environ.get("HOME")
    assert kwargs["env"].get("XDG_CONFIG_HOME") == os.environ.get(
        "XDG_CONFIG_HOME"
    )
    assert "Library/Containers" not in str(config_path)
    assert "Library/Containers" not in str(secret_path)
    assert (
        regular_profile.read_bytes()
        == b"regular musician profile stays untouched\n"
    )
    assert tuple(regular_jamulus_dir.iterdir()) == (regular_profile,)
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
    # A live PID alone is insufficient before authenticated control, zero
    # returns, and both audio routes have been proved.
    assert session.recording_ownership_claim() is None

    session.start(_fill_audio(0.125))
    assert rpc_instances[0].connected == 1
    assert rpc_instances[0].proofs >= 1
    claim = session.recording_ownership_claim()
    assert claim is not None
    assert claim.udp_port == 33142
    assert claim.process_id == process.pid
    assert len(claim.generation) == 32
    resolved_udp_ports.clear()
    assert session.recording_ownership_claim() is None
    resolved_udp_ports.append(33142)
    stream = sd.streams[-1]
    assert stream.started is True
    assert stream.kwargs["device"] == 0
    assert stream.kwargs["channels"] == 2
    out = np.zeros((256, 2), dtype=np.float32)
    stream.kwargs["callback"](out, 256, None, None)
    np.testing.assert_allclose(out, 0.125)

    lock_held = threading.Event()
    release_lock = threading.Event()

    def hold_session_lock() -> None:
        with session._lock:  # type: ignore[attr-defined]
            lock_held.set()
            assert release_lock.wait(timeout=2.0)

    holder = threading.Thread(target=hold_session_lock, daemon=True)
    holder.start()
    assert lock_held.wait(timeout=1.0)
    try:
        out.fill(0.0)
        started = time.perf_counter()
        stream.kwargs["callback"](out, 256, None, None)
        assert time.perf_counter() - started < 0.1
        np.testing.assert_allclose(out, 0.125)
    finally:
        release_lock.set()
        holder.join(timeout=1.0)

    out.fill(1.0)
    stream.kwargs["callback"](out, 256, None, "synthetic callback status")
    assert np.count_nonzero(out) == 0
    assert session.realtime_stats() == {"callback_faults": 1}
    assert "audio fault" in session.health_error()

    session.stop()
    assert session.recording_ownership_claim() is None
    assert stream.stopped is True
    assert stream.closed is True
    assert process.terminated == 1
    assert rpc_instances[0].closed == 1
    assert not secret_path.exists()
    assert not config_path.exists()
    assert (
        regular_profile.read_bytes()
        == b"regular musician profile stays untouched\n"
    )


@pytest.mark.skipif(os.name == "nt", reason="hard-link test needs POSIX dirfds")
def test_private_launch_rejects_hardlinked_secret_before_popen_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    launches: list[bool] = []
    linked: dict[str, Path] = {}
    ports = iter((33111, 33112))
    original = reference_backend._ReferencePrivateFiles.launch_files_are_exact

    def hardlink_before_check(owner) -> bool:
        if not linked:
            attack = owner.secret_path.with_name(f"{owner.secret_path.name}.linked")
            os.link(owner.secret_path, attack)
            linked["path"] = attack
        return original(owner)

    monkeypatch.setattr(
        reference_backend._ReferencePrivateFiles,
        "launch_files_are_exact",
        hardlink_before_check,
    )
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: launches.append(True),
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )

    with pytest.raises(Exception, match="cleanup could not be confirmed") as failure:
        backend.prepare(_context(binary))
    assert str(tmp_path) not in str(failure.value)
    assert launches == []
    linked["path"].unlink()
    backend.retry_cleanup()
    assert backend.capability().reason_code == "ready"


@pytest.mark.parametrize(
    "mutation",
    ("mode", "hardlink", "replacement"),
)
def test_private_launch_revalidates_files_after_popen_and_terminates(
    tmp_path: Path,
    mutation: str,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    captured: dict[str, Path] = {}
    ports = iter((33121, 33122))

    def popen(command, **_kwargs):
        secret = Path(command[command.index("--jsonrpcsecretfile") + 1])
        captured["secret"] = secret
        if mutation == "mode":
            secret.chmod(0o644)
        elif mutation == "hardlink":
            attack = secret.with_name(f"{secret.name}.linked")
            os.link(secret, attack)
            captured["attack"] = attack
        else:
            backup = secret.with_name(f"{secret.name}.owned-backup")
            os.link(secret, backup)
            replacement = secret.with_name(f"{secret.name}.replacement")
            replacement.write_bytes(b"attacker-controlled replacement\n")
            replacement.chmod(0o600)
            os.replace(replacement, secret)
            captured["backup"] = backup
        return process

    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=popen,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )

    with pytest.raises(Exception, match="cleanup could not be confirmed") as failure:
        backend.prepare(_context(binary))
    assert str(tmp_path) not in str(failure.value)
    assert process.terminated == 1
    secret = captured["secret"]
    if mutation == "mode":
        secret.chmod(0o600)
    elif mutation == "hardlink":
        captured["attack"].unlink()
    else:
        secret.unlink()
        captured["backup"].rename(secret)
    backend.retry_cleanup()
    assert not secret.exists()
    assert backend.capability().reason_code == "ready"


def test_private_launch_rewalks_ancestor_chain_after_popen(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    moved: dict[str, Path] = {}
    ports = iter((33131, 33132))

    def popen(_command, **_kwargs):
        webjam = (
            tmp_path
            / "Library"
            / "Application Support"
            / "WebJam"
        )
        owned = webjam.with_name("WebJam-owned")
        webjam.rename(owned)
        webjam.symlink_to(owned, target_is_directory=True)
        moved["link"] = webjam
        moved["owned"] = owned
        return process

    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=popen,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )

    with pytest.raises(Exception, match="launch profile changed") as failure:
        backend.prepare(_context(binary))
    assert str(tmp_path) not in str(failure.value)
    assert process.terminated == 1
    moved["link"].unlink()
    moved["owned"].rename(moved["link"])
    assert backend.capability().reason_code == "ready"


def test_private_popen_os_error_cannot_leak_runtime_path_in_traceback(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    ports = iter((33141, 33142))

    def popen(command, **_kwargs):
        secret = command[command.index("--jsonrpcsecretfile") + 1]
        raise FileNotFoundError(
            errno.ENOENT,
            "synthetic private launch failure",
            secret,
        )

    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=popen,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )

    with pytest.raises(Exception, match="prepare a safe Shared Track") as failure:
        backend.prepare(_context(binary))
    formatted = "".join(
        traceback.format_exception(
            failure.type,
            failure.value,
            failure.tb,
        )
    )
    assert str(tmp_path) not in formatted
    assert "synthetic private launch failure" not in formatted
    assert backend.capability().reason_code == "ready"


def test_ownership_generation_failure_before_spawn_cleans_private_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    ports = iter((33151, 33152))
    launches: list[tuple[object, ...]] = []
    token_calls = 0

    def token_hex(byte_count: int) -> str:
        nonlocal token_calls
        token_calls += 1
        if token_calls == 2:
            raise OSError("synthetic entropy failure")
        return "a" * (byte_count * 2)

    monkeypatch.setattr(reference_backend.secrets, "token_hex", token_hex)
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *args, **kwargs: launches.append((args, kwargs)),
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )

    with pytest.raises(Exception, match="prepare a safe Shared Track") as failure:
        backend.prepare(_context(binary))

    runtime = reference_track_runtime_directory(tmp_path)
    assert launches == []
    assert str(tmp_path) not in str(failure.value)
    assert not runtime.exists() or not tuple(runtime.iterdir())
    assert backend._pending_cleanup is None  # type: ignore[attr-defined]
    assert backend.capability().reason_code == "ready"


def test_reference_process_poll_failure_is_unknown_not_an_exception() -> None:
    class PollFailureProcess:
        def __init__(self) -> None:
            self.terminated = 0

        def poll(self):
            raise OSError("synthetic process-state failure")

        def terminate(self) -> None:
            self.terminated += 1
            raise OSError("synthetic termination failure")

    process = PollFailureProcess()

    assert MacOSBlackHoleReferenceBackend._terminate_process(process) is False
    assert process.terminated == 1


def test_legacy_fixed_profile_is_never_replaced_by_unique_session_profile(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    config_dir = reference_track_runtime_directory(tmp_path)
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
    private_profile, _private_secret = _owned_paths(session)

    assert config_path.read_bytes() == b"previous owned profile\n"
    assert private_profile.read_bytes().startswith(b"<?xml")
    assert private_profile != config_path
    session.stop()
    assert config_path.read_bytes() == b"previous owned profile\n"
    assert not private_profile.exists()
    assert not config_path.with_name(config_path.name + ".bak").exists()


def test_new_owned_profile_rewritten_by_jamulus_is_removed_after_session(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    ports = iter((33211, 33212))
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
    config_path, secret_path = _owned_paths(session)

    # The real Jamulus 3.12.2 client persists runtime settings on exit, so the
    # owned file no longer has the provisioning checksum at cleanup time. An
    # in-place rewrite retains the inode WebJam created.
    config_path.write_bytes(b"Jamulus rewrote its private runtime profile\n")

    session.stop()

    assert process.terminated == 1
    assert not config_path.exists()
    assert not secret_path.exists()


def test_replaced_config_directory_cannot_redirect_dirfd_cleanup(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    ports = iter((33221, 33222))
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
    config_path, secret_path = _owned_paths(session)
    config_dir = reference_track_runtime_directory(tmp_path)
    original_dir = config_dir.with_name(config_dir.name + ".owned")
    config_dir.rename(original_dir)
    config_dir.mkdir(mode=0o700)
    unrelated_profile = config_dir / config_path.name
    unrelated_secret = config_dir / secret_path.name
    unrelated_profile.write_bytes(b"unrelated replacement profile\n")
    unrelated_secret.write_bytes(b"unrelated replacement secret\n")

    session.stop()

    assert unrelated_profile.read_bytes() == b"unrelated replacement profile\n"
    assert unrelated_secret.read_bytes() == b"unrelated replacement secret\n"
    assert process.terminated == 1
    assert not (original_dir / config_path.name).exists()
    assert not (original_dir / secret_path.name).exists()


def test_atomic_jamulus_profile_rewrite_is_validated_before_cleanup(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    ports = iter((33231, 33232))
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
    profile_path, secret_path = _owned_paths(session)
    root = ElementTree.fromstring(profile_path.read_bytes())
    ElementTree.SubElement(root, "runtime_setting").text = "Jamulus persisted this"
    replacement = profile_path.with_name(f".{profile_path.name}.replacement")
    replacement.write_bytes(ElementTree.tostring(root, encoding="utf-8"))
    replacement.chmod(0o600)
    os.replace(replacement, profile_path)

    session.stop()

    assert process.terminated == 1
    assert not profile_path.exists()
    assert not secret_path.exists()


def test_world_readable_jamulus_profile_rewrite_fails_closed_then_recovers(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    ports = iter((33236, 33237))
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
    profile_path, secret_path = _owned_paths(session)
    root = ElementTree.fromstring(profile_path.read_bytes())
    ElementTree.SubElement(root, "runtime_setting").text = "Jamulus persisted this"
    replacement = profile_path.with_name(f".{profile_path.name}.replacement")
    replacement.write_bytes(ElementTree.tostring(root, encoding="utf-8"))
    replacement.chmod(0o644)
    os.replace(replacement, profile_path)

    with pytest.raises(Exception, match="cleanup could not be confirmed") as failure:
        session.stop()
    assert str(tmp_path) not in str(failure.value)
    assert process.terminated == 1
    assert profile_path.exists()
    assert secret_path.exists() is False

    profile_path.chmod(0o600)
    session.stop()
    assert not profile_path.exists()


def test_unrelated_atomic_profile_replacement_is_retained_until_safe_retry(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    ports = iter((33241, 33242))
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
    profile_path, _secret_path = _owned_paths(session)
    original_profile = profile_path.read_bytes()
    replacement = profile_path.with_name(f".{profile_path.name}.replacement")
    replacement.write_bytes(b"unrelated replacement\n")
    os.replace(replacement, profile_path)

    with pytest.raises(Exception, match="cleanup could not be confirmed"):
        session.stop()

    assert profile_path.read_bytes() == b"unrelated replacement\n"
    with pytest.raises(Exception):
        with InterProcessComponentLock(
            _reference_track_lock_path(tmp_path),
            timeout=0.0,
        ):
            pass

    recovery = profile_path.with_name(f".{profile_path.name}.recovery")
    recovery.write_bytes(original_profile)
    recovery.chmod(0o600)
    os.replace(recovery, profile_path)
    session.stop()
    assert not profile_path.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO test needs POSIX")
def test_fifo_profile_replacement_never_blocks_or_gets_followed(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    ports = iter((33251, 33252))
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
    profile_path, _secret_path = _owned_paths(session)
    replacement = profile_path.with_name(f".{profile_path.name}.fifo")
    os.mkfifo(replacement, mode=0o600)
    os.replace(replacement, profile_path)

    started = time.monotonic()
    with pytest.raises(Exception, match="cleanup could not be confirmed"):
        session.stop()
    assert time.monotonic() - started < 1.0
    assert stat.S_ISFIFO(profile_path.lstat().st_mode)

    profile_path.unlink()
    session.stop()


@pytest.mark.parametrize("cleanup_failure", ("returns-false", "raises"))
def test_failed_startup_retains_cleanup_owner_and_retries_without_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_failure: str,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    launches = {"count": 0}
    ports = iter((33261, 33262, 33263, 33264))

    def popen(*_args, **_kwargs):
        launches["count"] += 1
        if launches["count"] == 1:
            raise OSError("synthetic launch failure")
        return process

    original_cleanup = reference_backend._ReferencePrivateFiles.cleanup
    cleanup_attempts = {"count": 0}

    def fail_first_cleanup(owner):
        cleanup_attempts["count"] += 1
        if cleanup_attempts["count"] == 1:
            if cleanup_failure == "raises":
                raise LookupError("synthetic cleanup parser failure")
            return False
        return original_cleanup(owner)

    monkeypatch.setattr(
        reference_backend._ReferencePrivateFiles,
        "cleanup",
        fail_first_cleanup,
    )
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=popen,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )

    with pytest.raises(Exception, match="cleanup could not be confirmed") as failure:
        backend.prepare(_context(binary))
    assert str(tmp_path) not in str(failure.value)
    capability = backend.capability()
    assert capability.available is False
    assert capability.reason_code == "cleanup_pending"
    with pytest.raises(Exception):
        with InterProcessComponentLock(
            _reference_track_lock_path(tmp_path),
            timeout=0.0,
        ):
            pass

    session = backend.prepare(_context(binary))
    assert cleanup_attempts["count"] >= 2
    assert launches["count"] == 2
    session.stop()


def test_cross_process_lifecycle_lock_blocks_before_any_private_launch(
    tmp_path: Path,
) -> None:
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    lock_path = _reference_track_lock_path(tmp_path)
    holder = context.Process(
        target=_hold_reference_track_lock,
        args=(str(tmp_path), ready, release),
    )
    holder.start()
    assert ready.wait(timeout=5.0)
    # Even deleting the advisory lock pathname cannot split ownership: the
    # kernel loopback lease remains bound by the other process.
    lock_path.unlink()
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    launches: list[bool] = []
    ports = iter((33271, 33272, 33273, 33274))
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: (
            launches.append(True) or process
        ),
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )
    try:
        with pytest.raises(Exception, match="Another WebJam window"):
            backend.prepare(_context(binary))
        assert launches == []
    finally:
        release.set()
        holder.join(timeout=5.0)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=2.0)
    assert holder.exitcode == 0

    session = backend.prepare(_context(binary))
    assert launches == [True]
    session.stop()


def test_lifecycle_socket_construction_failure_rolls_back_local_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_socket = reference_backend.socket.socket

    def fail_socket(*_args, **_kwargs):
        raise OSError(errno.EMFILE, "synthetic descriptor exhaustion")

    monkeypatch.setattr(reference_backend.socket, "socket", fail_socket)
    with pytest.raises(Exception, match="couldn't reserve"):
        _claim_blackhole_route("BlackHole16ch_UID", home=tmp_path)

    monkeypatch.setattr(reference_backend.socket, "socket", real_socket)
    lease = _claim_blackhole_route("BlackHole16ch_UID", home=tmp_path)
    lease.release()


@pytest.mark.skipif(os.name == "nt", reason="pass_fds is POSIX-only")
def test_inherited_kernel_lease_blocks_relaunch_while_child_survives(
    tmp_path: Path,
) -> None:
    lease = _claim_blackhole_route("BlackHole16ch_UID", home=tmp_path)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        pass_fds=lease.child_pass_fds,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Model abrupt WebJam parent loss: its descriptors close, while the
        # separately owned Jamulus child remains alive with the kernel lease.
        lease.release()
        with pytest.raises(Exception, match="Another WebJam window"):
            _claim_blackhole_route("BlackHole16ch_UID", home=tmp_path)
    finally:
        child.terminate()
        child.wait(timeout=5.0)

    recovered = _claim_blackhole_route("BlackHole16ch_UID", home=tmp_path)
    recovered.release()


def test_sixteen_and_sixty_four_channel_routes_share_one_lifecycle_lock(
    tmp_path: Path,
) -> None:
    first = _claim_blackhole_route("BlackHole16ch_UID", home=tmp_path)
    try:
        with pytest.raises(Exception, match="Another WebJam"):
            _claim_blackhole_route("BlackHole64ch_UID", home=tmp_path)
    finally:
        first.release()
    second = _claim_blackhole_route("BlackHole64ch_UID", home=tmp_path)
    second.release()


def test_exact_blackhole_uid_has_one_process_local_owner_until_clean_stop(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    first_process = _Process()
    second_process = _Process()
    first_ports = iter((34101, 34102))
    second_ports = iter((34201, 34202, 34203, 34204))
    second_launches = []
    first_home = tmp_path / "first"
    second_home = tmp_path / "second"
    first_home.mkdir()
    second_home.mkdir()
    first = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: first_process,
        port_allocator=lambda _kind, _excluded: next(first_ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=first_home,
        physical_route_certified=True,
    )

    def launch_second(*_args, **_kwargs):
        second_launches.append(True)
        return second_process

    second = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=launch_second,
        port_allocator=lambda _kind, _excluded: next(second_ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=second_home,
        physical_route_certified=True,
    )

    first_session = first.prepare(_context(binary))
    with pytest.raises(Exception, match="already owns"):
        second.prepare(_context(binary))
    assert second_launches == []

    first_session.stop()
    second_session = second.prepare(_context(binary))
    assert second_launches == [True]
    second_session.stop()


def test_opened_portaudio_stream_must_match_proved_device_and_format(
    tmp_path: Path,
) -> None:
    class WrongDeviceSoundDevice(_SoundDevice):
        def OutputStream(self, **kwargs):
            stream = super().OutputStream(**kwargs)
            stream.device = 99
            return stream

    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    ports = iter((34301, 34302))
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=WrongDeviceSoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: process,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )
    session = backend.prepare(_context(binary))

    with pytest.raises(Exception, match="different device or stream format"):
        session.start(_fill_audio())
    assert session.health_error()
    session.stop()
    assert process.terminated == 1


def test_close_failure_retains_stream_route_lease_and_supports_stop_retry(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    first_process = _Process()
    second_process = _Process()
    first_sd = _SoundDevice()
    first_rpc: list[_Rpc] = []
    first_ports = iter((34401, 34402))
    second_ports = iter((34501, 34502, 34503, 34504))
    first_home = tmp_path / "first"
    second_home = tmp_path / "second"
    first_home.mkdir()
    second_home.mkdir()
    first = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=first_sd,
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: first_process,
        port_allocator=lambda _kind, _excluded: next(first_ports),
        rpc_factory=lambda port, secret: (
            first_rpc.append(_Rpc(port, secret)) or first_rpc[-1]
        ),
        home=first_home,
        physical_route_certified=True,
    )
    second = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: second_process,
        port_allocator=lambda _kind, _excluded: next(second_ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=second_home,
        physical_route_certified=True,
    )
    session = first.prepare(_context(binary))
    session.start(_fill_audio())
    stream = first_sd.streams[-1]
    stream.close_failures = 1
    config_path, secret_path = _owned_paths(session)

    with pytest.raises(Exception, match="couldn't close"):
        session.stop()

    assert stream.closed is False
    assert session._stream is stream  # type: ignore[attr-defined]
    assert first_process.poll() is None
    assert first_rpc[0].closed == 0
    assert config_path.exists()
    assert secret_path.exists()
    with pytest.raises(Exception, match="already owns"):
        second.prepare(_context(binary))

    session.stop()
    assert stream.closed is True
    assert first_process.terminated == 1
    assert first_rpc[0].closed == 1
    assert not config_path.exists()
    assert not secret_path.exists()

    second_session = second.prepare(_context(binary))
    second_session.stop()


def test_stream_stop_failure_is_recovered_only_by_proven_close(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    sd = _SoundDevice()
    ports = iter((34601, 34602))
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
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
    session.start(_fill_audio())
    stream = sd.streams[-1]
    stream.stop_failures = 1

    session.stop()

    assert stream.stopped is False
    assert stream.closed is True
    assert process.terminated == 1


@pytest.mark.parametrize(
    "monitor_failure",
    (
        "Shared Track stopped because its BlackHole route changed.",
        "Shared Track stopped because zero return faders could no longer be proved.",
    ),
    ids=("route-failure", "fader-failure"),
)
def test_monitor_failure_during_pull_discards_entire_audio_block(
    tmp_path: Path,
    monitor_failure: str,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    sd = _SoundDevice()
    ports = iter((34701, 34702))
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
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
    pull_entered = threading.Event()
    release_pull = threading.Event()

    def blocking_pull(output: np.ndarray) -> int:
        output.fill(0.625)
        pull_entered.set()
        assert release_pull.wait(timeout=2.0)
        return int(output.shape[0])

    session.start(blocking_pull)
    output = np.ones((256, 2), dtype=np.float32)
    callback = sd.streams[-1].kwargs["callback"]
    worker = threading.Thread(
        target=lambda: callback(output, 256, None, None),
        daemon=True,
    )
    try:
        worker.start()
        assert pull_entered.wait(timeout=1.0)
        # Route and fader monitor failures both publish through this bounded
        # path; latch one deterministically while the callback owns song data.
        session._set_health_error(monitor_failure)  # type: ignore[attr-defined]
        release_pull.set()
        worker.join(timeout=1.0)

        assert not worker.is_alive()
        assert np.count_nonzero(output) == 0
        assert session.health_error() == monitor_failure
    finally:
        release_pull.set()
        worker.join(timeout=1.0)
        session.stop()


def test_teardown_during_pull_discards_entire_audio_block(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    sd = _SoundDevice()
    ports = iter((34801, 34802))
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
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
    pull_entered = threading.Event()
    release_pull = threading.Event()

    def blocking_pull(output: np.ndarray) -> int:
        output.fill(0.875)
        pull_entered.set()
        assert release_pull.wait(timeout=2.0)
        return int(output.shape[0])

    session.start(blocking_pull)
    output = np.ones((256, 2), dtype=np.float32)
    callback = sd.streams[-1].kwargs["callback"]
    worker = threading.Thread(
        target=lambda: callback(output, 256, None, None),
        daemon=True,
    )
    try:
        worker.start()
        assert pull_entered.wait(timeout=1.0)
        session.stop()
        assert process.terminated == 1
        release_pull.set()
        worker.join(timeout=1.0)

        assert not worker.is_alive()
        assert np.count_nonzero(output) == 0
    finally:
        release_pull.set()
        worker.join(timeout=1.0)
        if process.poll() is None:
            session.stop()


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
    config_path, secret_path = _owned_paths(session)

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
    session.start(_fill_audio())
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
    session.start(_fill_audio())

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

    try:
        assert session.health_error()
        assert {call[0] for call in live_probe.calls} == {9876, 9999}
        out = np.ones((128, 2), dtype=np.float32)
        sd.streams[-1].kwargs["callback"](out, 128, None, None)
        assert np.count_nonzero(out) == 0
        # The route session owns only its separately launched process. It has
        # no primary Popen handle to terminate or mutate.
        assert reference_process.terminated == 0
    finally:
        session.stop()
    assert reference_process.terminated == 1


def test_owned_jamulus_must_prove_exact_blackhole_route_before_audio(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "Jamulus"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o700)
    process = _Process()
    ports = iter((33511, 33512))
    wrong_output = CoreAudioDevice(
        uid="fallback-output",
        name="Built-in Output",
        object_id=811,
        input_channels=0,
        output_channels=2,
        nominal_rate=48_000.0,
    )
    backend = MacOSBlackHoleReferenceBackend(
        platform="darwin",
        scanner=lambda: _scan(_device()),
        sounddevice_module=_SoundDevice(),
        process_route_probe=_LiveRouteProbe(
            owned_output_device=wrong_output,
        ),
        version_probe=lambda _binary: "3.12.2",
        headless_client_probe=lambda _binary: True,
        popen_factory=lambda *_args, **_kwargs: process,
        port_allocator=lambda _kind, _excluded: next(ports),
        rpc_factory=lambda port, secret: _Rpc(port, secret),
        home=tmp_path,
        physical_route_certified=True,
    )
    session = backend.prepare(_context(binary))
    try:
        with pytest.raises(Exception, match="exact isolated BlackHole"):
            session.start(_fill_audio())
        assert session.health_error()
    finally:
        session.stop()


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
    session.start(_fill_audio())
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
    assert not reference_track_runtime_directory(tmp_path).exists()


def test_route_proof_budget_absorbs_one_timeout_bounded_proof_round() -> None:
    # One honest monitor cycle can take a full wait period plus a fader-proof
    # RPC round bounded by the socket timeout. The freshness budget must not
    # latch a permanent fault for that; it must still bound unproved playback
    # to a few seconds after a genuine monitor stall.
    assert reference_backend._ROUTE_PROOF_MAX_AGE_SECONDS >= (
        reference_backend._FADER_RECHECK_SECONDS
        + reference_backend._ROUTE_RECHECK_SECONDS
        + reference_backend._RPC_CALL_TIMEOUT_S
    )
    assert reference_backend._ROUTE_PROOF_MAX_AGE_SECONDS <= 5.0
