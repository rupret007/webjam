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
        self.help_requests = []
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

    def send_help(self, text, *, generation):
        self.help_requests.append((generation, text))
        mode = (
            "host"
            if self.host_generations and self.host_generations[-1] == generation
            else "guest"
        )
        return TransportEvent(
            event_id=31,
            event_type="help_accepted",
            code="ok",
            state="connected",
            mode=mode,
            profile_id="reference-local",
            generation=generation,
            request_id=31,
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

    def emit_help(
        self, event_type: str, generation: int, text: str = "", mode: str = "guest"
    ):
        assert self.on_event is not None
        self.on_event(
            TransportEvent(
                event_id=0,
                event_type=event_type,
                code="ok",
                state="connected",
                mode=mode,
                profile_id="reference-local",
                generation=generation,
                request_id=41,
                help_text=text,
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


def test_guest_help_uses_current_generation_and_drops_stale_events(
    monkeypatch,
) -> None:
    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    received = []
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric",
        expected_build="abc1234",
        on_help=received.append,
    )
    backend.start_guest(_invitation(), generation=7)
    process = FakeProcess.instances[-1]

    accepted = backend.send_help("Try headphones")
    assert accepted.event_type == "help_accepted"
    assert process.help_requests == [(7, "Try headphones")]

    process.emit_help("help_received", 6, "stale")
    process.emit_help("help_received", 7, "I can hear you")
    process.emit_help("help_delivered", 7)
    assert [event.event_type for event in received] == [
        "help_received",
        "help_delivered",
    ]
    assert received[0].help_text == "I can hear you"
    assert "I can hear you" not in repr(received[0])

    backend.stop()
    with pytest.raises(RemoteBackendError) as stopped:
        backend.send_help("too late")
    assert stopped.value.code is RemoteSessionErrorCode.TRANSPORT_FAILED


def test_guest_queued_help_cannot_cross_stop_or_reused_generation(monkeypatch) -> None:
    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    pending = []
    received = []
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric",
        expected_build="abc1234",
        on_help=received.append,
        schedule_callback=pending.append,
    )
    assert not backend.help_available
    backend.start_guest(_invitation(), generation=7)
    old_process = FakeProcess.instances[-1]
    old_process.emit_help("help_received", 7, "old room")
    assert len(pending) == 1
    backend.stop()
    backend.start_guest(_invitation(), generation=7)
    new_process = FakeProcess.instances[-1]
    pending.pop()()
    old_process.emit_help("help_received", 7, "late old room")
    assert not pending
    assert received == []
    assert backend.help_available
    new_process.emit_help("help_received", 7, "current room")
    pending.pop()()
    assert [event.help_text for event in received] == ["current room"]
    backend.stop()


def test_guest_dedicated_help_scheduler_keeps_ingress_out_of_ui_queue(monkeypatch) -> None:
    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    ui_queue = []
    received = []
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric",
        expected_build="abc1234",
        on_help=received.append,
        schedule_callback=ui_queue.append,
        schedule_help_callback=lambda callback: callback(),
    )
    backend.start_guest(_invitation(), generation=7)
    FakeProcess.instances[-1].emit_help("help_received", 7, "bounded ingress")
    assert len(received) == 1
    assert ui_queue == []
    backend.stop()


@pytest.mark.parametrize("terminal", ["process_death", "peer_closed", "stopped", "error"])
def test_guest_help_retires_on_post_proof_transport_loss(monkeypatch, terminal) -> None:
    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    pending = []
    received = []
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric",
        expected_build="abc1234",
        on_help=received.append,
        schedule_callback=pending.append,
    )
    backend.start_guest(_invitation(), generation=7)
    process = FakeProcess.instances[-1]
    assert backend.help_available
    process.emit_help("help_received", 7, "queued before loss")
    if terminal == "process_death":
        process.running = False
    else:
        process.on_event(TransportEvent(
            event_id=0, event_type=terminal,
            code="protocol_violation" if terminal == "error" else "ok",
            state={"peer_closed": "closed", "stopped": "stopped", "error": "failed"}[terminal],
            mode="guest" if terminal == "peer_closed" else "",
            generation=7 if terminal == "peer_closed" else 0,
        ))
    assert not backend.help_available
    assert backend._phase == "failed"
    pending.pop()()
    assert received == []
    with pytest.raises(RemoteBackendError, match="^transport_failed$"):
        backend.send_help("not sent")
    assert process.help_requests == []
    backend.stop()


def test_guest_help_ignores_wrong_generation_close_and_request_backpressure(monkeypatch) -> None:
    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric", expected_build="abc1234"
    )
    backend.start_guest(_invitation(), generation=7)
    process = FakeProcess.instances[-1]
    for mode, generation in [("host", 7), ("guest", 6)]:
        process.on_event(TransportEvent(
            event_id=0, event_type="peer_closed", code="ok", state="closed",
            mode=mode, generation=generation,
        ))
    process.on_event(TransportEvent(
        event_id=31, event_type="error", code="help_rate_limited", state="connected"
    ))
    assert backend.help_available
    assert backend.send_help("current room").generation == 7
    backend.stop()


def test_guest_send_rejects_acceptance_after_owner_stop(monkeypatch) -> None:
    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric", expected_build="abc1234"
    )
    backend.start_guest(_invitation(), generation=7)
    process = FakeProcess.instances[-1]
    send = process.send_help

    def finish_after_stop(text, *, generation):
        accepted = send(text, generation=generation)
        backend.stop()
        return accepted

    process.send_help = finish_after_stop
    with pytest.raises(RemoteBackendError, match="^transport_failed$"):
        backend.send_help("retired completion")


def test_guest_expected_generation_blocks_old_text_before_dispatch(monkeypatch) -> None:
    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric", expected_build="abc1234"
    )
    backend.start_guest(_invitation(), generation=7)
    backend.stop()
    backend.start_guest(_invitation(), generation=8)
    process = FakeProcess.instances[-1]
    with pytest.raises(RemoteBackendError, match="^transport_failed$"):
        backend.send_help("old room draft", expected_generation=7)
    assert process.help_requests == []
    backend.send_help("new room draft", expected_generation=8)
    assert process.help_requests == [(8, "new room draft")]
    backend.stop()


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
    help_events = []
    owner = native.NativeHostTransportOwner(
        target_port=22124,
        binary="/private/webjam-fabric",
        expected_build="abc1234",
        on_snapshot=snapshots.append,
        on_help=help_events.append,
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
    accepted = owner.send_help("Try headphones")
    assert accepted.event_type == "help_accepted"
    assert process.help_requests == [(2, "Try headphones")]
    process.emit_help("help_received", 1, "stale")
    process.emit_help("help_received", 2, "That worked", mode="host")
    assert [event.help_text for event in help_events] == ["That worked"]
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


def _help_host(monkeypatch, **kwargs):
    FakeProcess.instances.clear()
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    owner = native.NativeHostTransportOwner(
        target_port=22124,
        binary="/private/webjam-fabric",
        expected_build="abc1234",
        **kwargs,
    )
    return owner, FakeProcess.instances[-1]


def test_host_help_scheduler_rechecks_generation_after_reset(monkeypatch) -> None:
    pending = []
    received = []
    owner, process = _help_host(
        monkeypatch,
        on_help=received.append,
        schedule_help_callback=pending.append,
    )
    assert not owner.help_available
    process.emit_host_connected(1)
    assert owner.help_available
    process.emit_help("help_received", 1, "old room", mode="host")
    owner.reset()
    assert not owner.help_available
    process.emit_host_connected(2)
    assert owner.help_available
    pending.pop()()
    assert received == []
    process.emit_help("help_received", 2, "current room", mode="host")
    pending.pop()()
    assert [event.help_text for event in received] == ["current room"]
    process.emit_help("help_received", 2, "queued before stop", mode="host")
    owner.stop()
    pending.pop()()
    assert len(received) == 1
    assert not owner.help_available


def test_host_help_can_use_inline_ingress_without_scheduling_ui_text(monkeypatch) -> None:
    ui_queue = []
    received = []
    owner, process = _help_host(
        monkeypatch,
        on_help=received.append,
        schedule_callback=ui_queue.append,
        schedule_help_callback=lambda callback: callback(),
    )
    process.emit_host_connected(1)
    snapshot_count = len(ui_queue)
    process.emit_help("help_received", 1, "bounded ingress", mode="host")
    assert len(received) == 1
    assert len(ui_queue) == snapshot_count
    owner.stop()


@pytest.mark.parametrize("terminal", ["process_death", "peer_closed", "stopped", "error"])
def test_host_help_retires_on_post_proof_transport_loss(monkeypatch, terminal) -> None:
    pending = []
    received = []
    owner, process = _help_host(
        monkeypatch, on_help=received.append, schedule_help_callback=pending.append
    )
    process.emit_host_connected(1)
    process.emit_help("help_received", 1, "queued before loss", mode="host")
    if terminal == "process_death":
        process.running = False
    else:
        process.on_event(TransportEvent(
            event_id=0, event_type=terminal,
            code="protocol_violation" if terminal == "error" else "ok",
            state={"peer_closed": "closed", "stopped": "stopped", "error": "failed"}[terminal],
            mode="host" if terminal == "peer_closed" else "",
            generation=1 if terminal == "peer_closed" else 0,
        ))
    assert not owner.help_available
    assert owner.snapshot.phase is RemoteSessionPhase.FAILED
    pending.pop()()
    assert received == []
    with pytest.raises(RemoteBackendError, match="^transport_failed$"):
        owner.send_help("not sent")
    assert process.help_requests == []
    owner.stop()


def test_host_help_ignores_old_close_and_rejects_acceptance_after_reset(monkeypatch) -> None:
    owner, process = _help_host(monkeypatch)
    process.emit_host_connected(1)
    owner.reset()
    process.emit_host_connected(2)
    process.on_event(TransportEvent(
        event_id=0, event_type="peer_closed", code="ok", state="closed",
        mode="host", generation=1,
    ))
    process.on_event(TransportEvent(
        event_id=31, event_type="error", code="help_rate_limited", state="connected"
    ))
    assert owner.help_available
    send = process.send_help

    def finish_after_reset(text, *, generation):
        accepted = send(text, generation=generation)
        owner.reset()
        process.emit_host_connected(3)
        return accepted

    process.send_help = finish_after_reset
    with pytest.raises(RemoteBackendError, match="^transport_failed$"):
        owner.send_help("old generation completion")
    assert owner.help_available
    owner.stop()


def test_host_expected_generation_blocks_old_text_before_dispatch(monkeypatch) -> None:
    owner, process = _help_host(monkeypatch)
    process.emit_host_connected(1)
    owner.reset()
    process.emit_host_connected(2)
    for old_generation in [1, True]:
        with pytest.raises(RemoteBackendError, match="^transport_failed$"):
            owner.send_help("old room draft", expected_generation=old_generation)
    assert process.help_requests == []
    owner.send_help("new room draft", expected_generation=2)
    assert process.help_requests == [(2, "new room draft")]
    owner.stop()
