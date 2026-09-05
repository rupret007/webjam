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


def _room_received(revision=1, *, generation=7, mode="guest"):
    from core.room_state import RoomState
    return TransportEvent(
        event_id=0, event_type="room_state_received", code="ok", state="connected",
        mode=mode, profile_id="reference-local", generation=generation,
        room_state=RoomState(revision, "art", "paint_along"),
    )


def test_guest_room_state_is_delivered_during_enrollment_without_help(monkeypatch) -> None:
    from core.room_state import RoomIdentity
    class EarlyRoomProcess(FakeProcess):
        def open_guest(self, invitation, *, generation):
            self.on_event(_room_received(generation=generation))
            return super().open_guest(invitation, generation=generation)
    monkeypatch.setattr(native, "TransportProcess", EarlyRoomProcess)
    received = []
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric", expected_build="abc1234",
        on_room_state=received.append,
    )
    invitation = _invitation()
    backend.start_guest(invitation, generation=7)
    assert backend.connection_available
    assert backend.room_identity == RoomIdentity.from_invitation(invitation)
    assert [event.room_state.revision for event in received] == [1]
    process = FakeProcess.instances[-1]
    for event in (_room_received(2, generation=6), _room_received(2, mode="host"),
                  _room_received(1), _room_received(2)):
        process.on_event(event)
    assert [event.room_state.revision for event in received] == [1, 2]
    backend.stop()
    assert backend.room_identity is None
    assert not backend.connection_available


def test_queued_room_state_and_loss_are_fenced_by_process_generation_and_stop(monkeypatch) -> None:
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    pending, received, losses = [], [], []
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric", expected_build="abc1234",
        on_room_state=received.append, on_connection_lost=losses.append,
        schedule_callback=pending.append,
    )
    backend.start_guest(_invitation(), generation=7)
    old = FakeProcess.instances[-1]
    old.on_event(_room_received(1))
    old.on_event(_room_received(2))
    pending.pop(0)()
    assert not received  # queued older full state must not undo the newest snapshot
    old.on_event(TransportEvent(event_id=0, event_type="error", state="failed"))
    assert backend.room_identity is None
    assert not backend.connection_available
    pending.pop(0)()
    assert not received  # latest room snapshot is also retired on disconnect
    backend.stop()
    backend.start_guest(_invitation(), generation=7)
    pending.pop(0)()
    assert not losses  # old failed process reused the same integer generation
    old.on_event(_room_received(3))
    assert not pending
    current = FakeProcess.instances[-1]
    current.on_event(_room_received(1))
    pending.pop(0)()
    assert len(received) == 1
    current.running = False
    assert not backend.connection_available
    assert backend.room_identity is None
    pending.pop(0)()
    assert losses == [7]
    assert not backend.connection_available
    assert not pending  # one loss notification, not a repeated polling alarm
    backend.stop()


def test_host_caches_full_room_state_until_authenticated_and_rotates_identity(monkeypatch) -> None:
    from core.room_state import RoomIdentity, RoomState
    published = []
    sent = threading.Event()
    class RoomProcess(FakeProcess):
        def publish_room_state(self, state, *, generation):
            published.append((generation, state))
            sent.set()
            return TransportEvent(
                event_id=51, request_id=51, event_type="room_state_accepted", code="ok",
                state="connected", mode="host", profile_id="reference-local",
                generation=generation,
            )
    monkeypatch.setattr(native, "TransportProcess", RoomProcess)
    owner = native.NativeHostTransportOwner(target_port=22124, binary="/private/webjam-fabric",
                                            expected_build="abc1234")
    first_identity = owner.room_identity
    assert first_identity == RoomIdentity.from_invitation(owner.invitation)
    assert not owner.connection_available
    state = RoomState(1, "art", "talk_and_make")
    assert owner.publish_room_state(state)
    assert not published
    process = FakeProcess.instances[-1]
    process.emit_host_connected(1)
    assert sent.wait(1)
    assert published == [(1, state)]
    assert owner.connection_available
    assert owner.room_identity == first_identity  # consuming invite keeps existing signer
    sent.clear()
    latest = RoomState(2, "art", "paint_along")
    assert owner.publish_room_state(latest)
    assert sent.wait(1)
    assert published[-1] == (1, latest)
    assert not owner.publish_room_state(state)
    owner.reset()
    assert owner.room_identity != first_identity
    assert owner.room_identity == RoomIdentity.from_invitation(owner.invitation)
    assert not owner.connection_available
    # Reset retires the old media identities; no state signed with that key is replayed.
    process.emit_host_connected(2)
    assert published[-1] == (1, latest)
    owner.stop()
    assert owner.room_identity is None
    assert not owner.publish_room_state(RoomState(3, "art", "paint_along"))


def test_guest_older_peer_has_bounded_update_reason(monkeypatch) -> None:
    from services.transport_runtime import TransportPeerProtocolError
    class OlderPeerProcess(FakeProcess):
        def open_guest(self, invitation, *, generation):
            raise TransportPeerProtocolError("The peer needs a compatible WebJam version.")
    monkeypatch.setattr(native, "TransportProcess", OlderPeerProcess)
    backend = native.NativeGuestTransportBackend(binary="/private/webjam-fabric", expected_build="abc1234")
    with pytest.raises(RemoteBackendError) as failure:
        backend.start_guest(_invitation(), generation=7)
    assert failure.value.code is RemoteSessionErrorCode.PEER_PROTOCOL_UNSUPPORTED
    assert backend.room_identity is None
    assert FakeProcess.instances[-1].stopped == 1


def test_runtime_loss_retires_queued_connected_proof_and_exposes_no_room_identity(monkeypatch) -> None:
    from services.remote_session_runtime import RemoteSessionRuntime
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    pending, seen = [], []
    owner = {}
    backend = native.NativeGuestTransportBackend(
        binary="/private/webjam-fabric", expected_build="abc1234",
        on_connection_lost=lambda generation: owner["runtime"].mark_connection_lost(
            expected_generation=generation),
    )
    runtime = RemoteSessionRuntime(backend, on_snapshot=seen.append, schedule_callback=pending.append)
    owner["runtime"] = runtime
    runtime.start_guest(_invitation())
    connected = runtime.wait_until_settled()
    assert connected.phase is RemoteSessionPhase.CONNECTED
    assert runtime.room_identity is not None
    assert runtime.connection_available
    process = FakeProcess.instances[-1]
    assert not runtime.mark_connection_lost(expected_generation=connected.generation + 1)
    process.on_event(TransportEvent(event_id=0, event_type="error", state="failed"))
    assert runtime.snapshot.phase is RemoteSessionPhase.FAILED
    assert runtime.snapshot.error_code is RemoteSessionErrorCode.TRANSPORT_FAILED
    assert runtime.room_identity is None
    assert not runtime.connection_available
    assert not runtime.snapshot.invitation_retry_safe
    for callback in pending:
        callback()
    assert [snapshot.phase for snapshot in seen] == [RemoteSessionPhase.FAILED]
    runtime.stop()


def test_host_cannot_lose_authenticated_first_peer_during_registration(monkeypatch) -> None:
    class EarlyHostProcess(FakeProcess):
        def open_host(self, invitation, *, target_port, generation):
            registered = super().open_host(invitation, target_port=target_port, generation=generation)
            self.emit_host_connected(generation)
            return registered
    monkeypatch.setattr(native, "TransportProcess", EarlyHostProcess)
    owner = native.NativeHostTransportOwner(target_port=22124, binary="/private/webjam-fabric",
                                            expected_build="abc1234")
    assert owner.snapshot.phase is RemoteSessionPhase.CONNECTED
    assert owner.connection_available
    assert not owner.invitation_available
    first = owner.room_identity
    owner.reset()
    assert owner.snapshot.generation == 2
    assert owner.snapshot.phase is RemoteSessionPhase.CONNECTED
    assert owner.connection_available
    assert not owner.invitation_available
    assert owner.room_identity != first
    owner.stop()


def _accepted_room_state(generation):
    return TransportEvent(
        event_id=71, request_id=71, event_type="room_state_accepted", code="ok",
        state="connected", mode="host", profile_id="reference-local", generation=generation,
    )


def test_host_rate_limit_retries_only_latest_full_snapshot(monkeypatch) -> None:
    from core.room_state import RoomState
    from services.transport_runtime import TransportRoomRateLimitedError
    entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
    published = []

    class BusyRoomProcess(FakeProcess):
        def publish_room_state(self, state, *, generation):
            published.append((generation, state.revision, time.monotonic()))
            if len(published) == 1:
                entered.set()
                assert release.wait(2)
                raise TransportRoomRateLimitedError("The room update needs a brief retry.")
            delivered.set()
            return _accepted_room_state(generation)

    monkeypatch.setattr(native, "TransportProcess", BusyRoomProcess)
    owner = native.NativeHostTransportOwner(target_port=22124, binary="/private/webjam-fabric",
                                            expected_build="abc1234")
    process = FakeProcess.instances[-1]
    process.emit_host_connected(1)
    identity = owner.room_identity
    try:
        assert owner.publish_room_state(RoomState(1, "art", "paint_along"))
        assert entered.wait(1)
        for revision in range(2, 31):
            assert owner.publish_room_state(RoomState(revision, "art", "paint_along"))
        assert owner.connection_available
        assert owner.room_identity == identity
        released_at = time.monotonic()
        release.set()
        assert delivered.wait(2)
        assert [(generation, revision) for generation, revision, _ in published] == [(1, 1), (1, 30)]
        assert published[-1][2] - released_at >= 0.25
        assert owner.connection_available
        assert owner.send_help("Still here").code == "ok"
    finally:
        release.set()
        owner.stop()


@pytest.mark.parametrize("replace_invitation", [False, True])
def test_host_cancels_rate_limit_retry_at_stop_or_replacement(monkeypatch, replace_invitation) -> None:
    from core.room_state import RoomState
    from services.transport_runtime import TransportRoomRateLimitedError
    entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
    published = []

    class SlowRateLimitProcess(FakeProcess):
        def publish_room_state(self, state, *, generation):
            published.append((generation, state.revision))
            if generation == 1:
                entered.set()
                assert release.wait(2)
                raise TransportRoomRateLimitedError("The room update needs a brief retry.")
            delivered.set()
            return _accepted_room_state(generation)

    monkeypatch.setattr(native, "TransportProcess", SlowRateLimitProcess)
    owner = native.NativeHostTransportOwner(target_port=22124, binary="/private/webjam-fabric",
                                            expected_build="abc1234")
    process = FakeProcess.instances[-1]
    process.emit_host_connected(1)
    owner.publish_room_state(RoomState(1, "art", "paint_along"))
    assert entered.wait(1)
    try:
        if replace_invitation:
            owner.reset()
            replacement_identity = owner.room_identity
            process.emit_host_connected(2)
            owner.publish_room_state(RoomState(1, "art", "talk_and_make"))
            assert delivered.wait(1)
        else:
            owner.stop()
        release.set()
        # Longer than one retry tick: no old room state may be sent or turn a
        # replacement invitation into a failure when its late error arrives.
        time.sleep(0.4)
        expected = [(1, 1), (2, 1)] if replace_invitation else [(1, 1)]
        assert published == expected
        if replace_invitation:
            assert owner.connection_available
            assert owner.room_identity == replacement_identity
        else:
            assert owner.snapshot.phase is RemoteSessionPhase.STOPPED
            assert owner.room_identity is None
    finally:
        release.set()
        owner.stop()


def test_host_persistent_rate_limit_has_bounded_secret_free_failure(monkeypatch, caplog) -> None:
    from core.room_state import RoomState
    from core.session_transfer import SharedCanvasSessionSnapshot
    from services.transport_runtime import TransportRoomRateLimitedError
    failed = threading.Event()
    calls = []

    class NeverReadyRoomProcess(FakeProcess):
        def publish_room_state(self, state, *, generation):
            calls.append((generation, state.revision))
            raise TransportRoomRateLimitedError("PRIVATE-CANVAS-TOKEN")

    monkeypatch.setattr(native, "TransportProcess", NeverReadyRoomProcess)
    monkeypatch.setattr(native, "_ROOM_RETRY_DELAY_SECONDS", 0.001)
    snapshots = []

    def changed(snapshot):
        snapshots.append(snapshot)
        if snapshot.phase is RemoteSessionPhase.FAILED:
            failed.set()

    owner = native.NativeHostTransportOwner(target_port=22124, binary="/private/webjam-fabric",
                                            expected_build="abc1234", on_snapshot=changed)
    FakeProcess.instances[-1].emit_host_connected(1)
    state = RoomState(1, "art", "talk_and_make", shared_canvas=SharedCanvasSessionSnapshot(
        generation=1, shared=True, join_url="drawpile://studio.example/room?p=PRIVATE-CANVAS-TOKEN",
    ))
    try:
        assert owner.publish_room_state(state)
        assert failed.wait(1)
        assert calls == [(1, 1)] * 8
        assert owner.snapshot.error_code is RemoteSessionErrorCode.TRANSPORT_FAILED
        assert owner.room_identity is None
        assert not owner.connection_available
        assert not owner.publish_room_state(RoomState(2, "art", "talk_and_make"))
        assert "PRIVATE-CANVAS-TOKEN" not in repr(snapshots) + repr(owner) + caplog.text
    finally:
        owner.stop()


@pytest.mark.parametrize("host", [False, True])
def test_failed_native_stop_retains_process_for_real_cleanup_retry(monkeypatch, host) -> None:
    from services.transport_runtime import TransportProcessError

    class RetryStopProcess(FakeProcess):
        def stop(self):
            self.stopped += 1
            if self.stopped == 1:
                raise TransportProcessError("The transport process did not stop.")
            self.running = False

    monkeypatch.setattr(native, "TransportProcess", RetryStopProcess)
    if host:
        owner = native.NativeHostTransportOwner(target_port=22124, binary="/private/webjam-fabric",
                                                expected_build="abc1234")
        FakeProcess.instances[-1].emit_host_connected(1)
    else:
        owner = native.NativeGuestTransportBackend(binary="/private/webjam-fabric", expected_build="abc1234")
        owner.start_guest(_invitation(), generation=7)
    process = FakeProcess.instances[-1]
    assert owner.connection_available
    with pytest.raises(TransportProcessError, match="did not stop"):
        owner.stop()
    assert process.running
    assert not owner.connection_available
    assert owner.room_identity is None
    if host:
        assert owner.snapshot.error_code is RemoteSessionErrorCode.STOP_FAILED
        assert not owner.invitation_available
        with pytest.raises(RuntimeError):
            owner.copy_for_clipboard()
    else:
        with pytest.raises(RemoteBackendError):
            owner.start_guest(_invitation(), generation=8)
    owner.stop()
    assert process.stopped == 2
    assert not process.running
    owner.stop()
    assert process.stopped == 2


def test_failed_enrollment_cleanup_keeps_guest_process_owned(monkeypatch) -> None:
    from services.transport_runtime import TransportProcessError

    class EnrollmentCleanupProcess(FakeProcess):
        def open_guest(self, invitation, *, generation):
            raise TransportProcessError("Cannot enroll")

        def stop(self):
            self.stopped += 1
            if self.stopped == 1:
                raise TransportProcessError("Cannot reap yet")
            self.running = False

    monkeypatch.setattr(native, "TransportProcess", EnrollmentCleanupProcess)
    backend = native.NativeGuestTransportBackend(binary="/private/webjam-fabric", expected_build="abc1234")
    with pytest.raises(RemoteBackendError):
        backend.start_guest(_invitation(), generation=7)
    process = FakeProcess.instances[-1]
    assert process.running
    assert backend.room_identity is None
    assert not backend.connection_available
    with pytest.raises(RemoteBackendError):
        backend.start_guest(_invitation(), generation=8)
    backend.stop()
    assert process.stopped == 2
    assert not process.running


def test_host_older_peer_requires_update_and_replacement_invitation(monkeypatch) -> None:
    monkeypatch.setattr(native, "TransportProcess", FakeProcess)
    owner = native.NativeHostTransportOwner(target_port=22124, binary="/private/webjam-fabric",
                                            expected_build="abc1234")
    process = FakeProcess.instances[-1]
    first_identity = owner.room_identity
    first_invitation = owner.invitation
    assert owner.invitation_available
    # Enrollment can consume the capability before protocol proof completes.
    process.on_event(TransportEvent(
        event_id=0, event_type="error", code="peer_protocol_unsupported", state="failed",
    ))
    assert owner.snapshot.error_code is RemoteSessionErrorCode.PEER_PROTOCOL_UNSUPPORTED
    assert owner.room_identity is None
    assert not owner.connection_available
    assert not owner.invitation_available
    with pytest.raises(RuntimeError):
        owner.copy_for_clipboard()
    owner.reset()
    assert owner.invitation_available
    assert owner.invitation is not first_invitation
    assert owner.room_identity != first_identity
    process.emit_host_connected(2)
    assert owner.connection_available
    owner.stop()
