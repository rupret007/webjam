from __future__ import annotations

from pathlib import Path
import threading
import time
from unittest import mock

import pytest

from core.remote_invitation import issue_remote_invitation
from core.session_transport import SessionRole, TransportPath
from services import native_remote_transport as native
from services.remote_invitation_owner import RemoteInvitationOwnerError
from services.remote_session_runtime import (
    RemoteBackendError,
    RemoteSessionErrorCode,
    RemoteSessionPhase,
)
from services.transport_runtime import TransportEvent


HOST_PIN = bytes.fromhex("44" * 32)


def _invitation():
    return issue_remote_invitation(
        "reference-local",
        allowed_profiles={"reference-local"},
        host_spki_sha256=HOST_PIN,
    ).invitation


class FakeProcess:
    instances = []

    def __init__(
        self,
        binary,
        *,
        expected_build,
        start_timeout=5,
        command_timeout=5,
        on_event=None,
        **_kw,
    ):
        self.binary = Path(binary)
        self.expected_build = expected_build
        self.start_timeout = start_timeout
        self.command_timeout = command_timeout
        self.on_event = on_event
        self.running = False
        self.host_generations = []
        self.guest_generations = []
        self.closed = 0
        self.stopped = 0
        FakeProcess.instances.append(self)

    def start(self):
        self.running = True
        return TransportEvent(event_id=0, event_type="ready", code="ok", state="idle")

    def prepare_host(self):
        return HOST_PIN

    def open_host(self, invitation, *, target_port, generation):
        assert invitation.host_spki_sha256 == HOST_PIN
        assert target_port == 22124
        self.host_generations.append(generation)
        return TransportEvent(
            event_id=generation,
            event_type="host_registered",
            code="ok",
            state="host_waiting",
            mode="host",
            profile_id="reference-local",
            generation=generation,
            loopback_port=43000 + generation,
        )

    def open_guest(self, invitation, *, generation):
        assert invitation.host_spki_sha256 == HOST_PIN
        self.guest_generations.append(generation)
        return TransportEvent(
            event_id=generation,
            event_type="peer_connected",
            code="ok",
            state="connected",
            mode="guest",
            profile_id="reference-local",
            generation=generation,
            loopback_port=43123,
        )

    def close_peer(self):
        self.closed += 1
        return TransportEvent(
            event_id=self.closed,
            event_type="peer_closed",
            code="ok",
            state="closed",
        )

    def stop(self):
        self.stopped += 1
        self.running = False

    def emit_host_connected(self, generation: int):
        assert self.on_event is not None
        self.on_event(
            TransportEvent(
                event_id=0,
                event_type="peer_connected",
                code="ok",
                state="connected",
                mode="host",
                profile_id="reference-local",
                generation=generation,
                loopback_port=43000 + generation,
            )
        )


def test_guest_backend_returns_only_authenticated_loopback_facts(monkeypatch) -> None:
    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric",
        expected_build="abc1234",
    )

    connected = backend.start_guest(_invitation(), generation=7)

    assert connected.loopback_port == 43123
    assert connected.path is TransportPath.SECURE_RELAY
    assert connected.generation == 7
    assert native.DEFAULT_REMOTE_START_TIMEOUT_SECONDS == 30.0
    assert FakeProcess.instances[-1].start_timeout == 30.0
    assert FakeProcess.instances[-1].guest_generations == [7]
    backend.stop()
    assert FakeProcess.instances[-1].closed == 0
    assert FakeProcess.instances[-1].stopped == 1


def test_guest_backend_allows_same_invitation_retry_only_before_open_guest(
    monkeypatch,
) -> None:
    class StartFailsProcess(FakeProcess):
        def start(self):
            self.running = False
            raise RuntimeError("sidecar did not start")

    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", StartFailsProcess)
    unavailable = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric",
        expected_build="abc1234",
    )

    with pytest.raises(RemoteBackendError) as start_error:
        unavailable.start_guest(_invitation(), generation=1)

    assert start_error.value.code is RemoteSessionErrorCode.UNAVAILABLE
    start_process = FakeProcess.instances[-1]
    assert start_process.guest_generations == []
    assert start_process.stopped == 1

    class OpenGuestFailsProcess(FakeProcess):
        def open_guest(self, invitation, *, generation):
            self.guest_generations.append(generation)
            raise RuntimeError("enrollment outcome is private")

    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", OpenGuestFailsProcess)
    uncertain = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric",
        expected_build="abc1234",
    )

    with pytest.raises(RemoteBackendError) as open_error:
        uncertain.start_guest(_invitation(), generation=2)

    assert open_error.value.code is RemoteSessionErrorCode.INVITATION_UNUSABLE
    open_process = FakeProcess.instances[-1]
    assert open_process.guest_generations == [2]
    assert open_process.stopped == 1


def test_guest_stop_cancels_pre_ready_start_without_enrollment(
    monkeypatch,
) -> None:
    process_created = threading.Event()
    created_processes = []

    class BlockingStartProcess(FakeProcess):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.start_entered = threading.Event()
            self.stop_released = threading.Event()
            self.reaped = False
            created_processes.append(self)
            process_created.set()

        def start(self):
            self.running = True
            self.start_entered.set()
            if not self.stop_released.wait(2):
                raise RuntimeError("test cancellation did not arrive")
            raise RuntimeError("sidecar start was cancelled")

        def stop(self):
            super().stop()
            self.reaped = True
            self.stop_released.set()

    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", BlockingStartProcess)
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric",
        expected_build="abc1234",
    )
    failures = []

    def start_guest() -> None:
        try:
            backend.start_guest(_invitation(), generation=1)
        except Exception as exc:  # noqa: BLE001 - assertion captures type below
            failures.append(exc)

    worker = threading.Thread(target=start_guest)
    worker.start()
    assert process_created.wait(1)
    process = created_processes[0]
    assert isinstance(process, BlockingStartProcess)
    assert process.start_entered.wait(1)

    started = time.monotonic()
    backend.stop()
    worker.join(1)

    assert time.monotonic() - started < 1
    assert not worker.is_alive()
    assert failures and isinstance(failures[0], RemoteBackendError)
    assert failures[0].code is RemoteSessionErrorCode.UNAVAILABLE
    assert process.guest_generations == []
    assert process.closed == 0
    assert process.reaped


def test_host_owner_registers_before_copy_and_reset_rotates_one_use_bearer(
    monkeypatch,
) -> None:
    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    snapshots = []
    owner = native.NativeHostTransportOwner(
        target_port=22124,
        binary="/private/webjam-fabric",
        expected_build="abc1234",
        on_snapshot=snapshots.append,
    )
    process = FakeProcess.instances[-1]
    assert process.start_timeout == 5

    first = owner.copy_for_clipboard()
    assert first.startswith("webjam://join?v=3")
    assert process.host_generations == [1]
    assert owner.snapshot.phase is RemoteSessionPhase.PREPARING

    owner.reset()
    second = owner.copy_for_clipboard()

    assert second != first
    assert process.closed == 1
    assert process.host_generations == [1, 2]
    assert owner.snapshot.generation == 2
    assert first not in repr(owner)

    process.emit_host_connected(1)
    assert owner.invitation_available
    assert owner.snapshot.phase is RemoteSessionPhase.PREPARING

    process.emit_host_connected(2)
    assert owner.snapshot.phase is RemoteSessionPhase.CONNECTED
    assert owner.snapshot.role is SessionRole.HOST
    assert snapshots[-1].phase is RemoteSessionPhase.CONNECTED
    assert not owner.invitation_available
    with pytest.raises(RemoteInvitationOwnerError, match="fresh"):
        owner.copy_for_clipboard()

    owner.reset()
    assert owner.invitation_available
    assert process.host_generations == [1, 2, 3]
    assert process.closed == 2
    owner.stop()
    assert process.closed == 2
    assert process.stopped == 1
    assert owner.snapshot.phase is RemoteSessionPhase.STOPPED


def test_lab_hosting_requires_explicit_process_local_opt_in(monkeypatch) -> None:
    monkeypatch.delenv(native.REFERENCE_LOCAL_OPT_IN, raising=False)
    assert not native.reference_local_host_requested()
    monkeypatch.setenv(native.REFERENCE_LOCAL_OPT_IN, "1")
    assert native.reference_local_host_requested()
    monkeypatch.setenv(native.REFERENCE_LOCAL_OPT_IN, "true")
    assert not native.reference_local_host_requested()


def test_frozen_sidecar_is_resolved_beside_main_executable(monkeypatch) -> None:
    monkeypatch.setenv(
        native.TRANSPORT_BINARY_OVERRIDE,
        "/tmp/attacker-selected-fabric",
    )
    monkeypatch.setattr(native.sys, "frozen", True, raising=False)
    monkeypatch.setattr(native.sys, "executable", "/App/WebJam.app/Contents/MacOS/WebJam")
    with mock.patch.object(native.os, "name", "posix"):
        assert native.transport_binary_path() == Path(
            "/App/WebJam.app/Contents/MacOS/webjam-fabric"
        )


def test_macos_frozen_manifest_is_sealed_as_bundle_data(monkeypatch) -> None:
    monkeypatch.setattr(native.sys, "platform", "darwin")
    binary = Path("/App/WebJam.app/Contents/MacOS/webjam-fabric")
    assert native._transport_manifest_path(binary) == Path(
        "/App/WebJam.app/Contents/Resources/webjam-fabric.sha256"
    )


def test_frozen_integrity_manifest_is_mandatory_and_canonical(
    tmp_path, monkeypatch
) -> None:
    binary = tmp_path / "webjam-fabric"
    binary.write_bytes(b"binary")
    monkeypatch.setattr(native.sys, "frozen", True, raising=False)
    monkeypatch.setattr(native.sys, "platform", "linux")

    with pytest.raises(Exception, match="verify"):
        native._integrity_options(binary)

    (tmp_path / "webjam-fabric.sha256").write_text("a" * 64 + "\n", encoding="ascii")
    options = native._integrity_options(binary)
    assert options["expected_sha256"] == "a" * 64
    assert options["require_platform_signature"] is True


def test_frozen_macos_integrity_rejects_non_bundle_layout(
    tmp_path, monkeypatch
) -> None:
    binary = tmp_path / "webjam-fabric"
    binary.write_bytes(b"binary")
    (tmp_path / "webjam-fabric.sha256").write_text(
        "a" * 64 + "\n", encoding="ascii"
    )
    monkeypatch.setattr(native.sys, "frozen", True, raising=False)
    monkeypatch.setattr(native.sys, "platform", "darwin")

    with pytest.raises(Exception, match="verify"):
        native._integrity_options(binary)


def test_frozen_macos_integrity_uses_resources_manifest(
    tmp_path, monkeypatch
) -> None:
    binary = tmp_path / "WebJam.app" / "Contents" / "MacOS" / "webjam-fabric"
    manifest = (
        tmp_path
        / "WebJam.app"
        / "Contents"
        / "Resources"
        / "webjam-fabric.sha256"
    )
    binary.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True)
    binary.write_bytes(b"binary")
    manifest.write_text("b" * 64 + "\n", encoding="ascii")
    (binary.parent / "webjam-fabric.sha256").write_text(
        "a" * 64 + "\n", encoding="ascii"
    )
    monkeypatch.setattr(native.sys, "frozen", True, raising=False)
    monkeypatch.setattr(native.sys, "platform", "darwin")

    options = native._integrity_options(binary)
    assert options["expected_sha256"] == "b" * 64
