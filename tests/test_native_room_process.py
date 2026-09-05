"""Opt-in Art room proof through real desktop owners and two native processes.

Uses only the loopback reference service. No application audio process, meeting,
canvas process, public service, or file/media transfer is started by this gate.
"""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import time

import pytest

from core.room_state import RoomState
from core.session_transfer import (
    ReferenceVideoPlaybackState,
    ReferenceVideoSessionSnapshot,
    SharedCanvasSessionSnapshot,
)
from services.native_remote_transport import (
    NativeGuestTransportBackend,
    NativeHostTransportOwner,
)
from services.remote_session_runtime import (
    RemoteBackendError,
    RemoteSessionErrorCode,
    RemoteSessionPhase,
    RemoteSessionRuntime,
)
from services.transport_runtime import (
    TransportEvent,
    TransportProcessError,
    TransportRoomRateLimitedError,
)


pytestmark = [
    pytest.mark.requires_local_socket,
    pytest.mark.skipif(
        os.environ.get("WEBJAM_RUN_REMOTE_SIDECAR_INTEGRATION") != "1",
        reason="real native sidecar integration is opt-in",
    ),
]
ROOT = Path(__file__).resolve().parents[1]


def _wait(predicate, message: str, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(message)


def _event(events: queue.Queue, predicate, timeout: float = 8.0):
    deadline = time.monotonic() + timeout
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            event = events.get(timeout=remaining)
        except queue.Empty:
            break
        if predicate(event):
            return event
    # Do not print private room or help payloads on timeout.
    pytest.fail("The expected authenticated room event did not arrive.")


@pytest.fixture
def native_room_environment():
    binary = os.environ.get("WEBJAM_TRANSPORT_BINARY", "")
    build = os.environ.get("WEBJAM_TEST_TRANSPORT_BUILD_ID", "")
    if not binary or not build or not Path(binary).is_file():
        pytest.fail("sidecar integration requires an exact binary and build ID")
    # Share the Go integration gate's lock. Never remove another test's lock
    # on an age guess or adopt a service already listening on these ports.
    lock = Path(tempfile.gettempdir()) / "webjam-reference-v3-fixed-ports.lock"
    deadline = time.monotonic() + 30
    while True:
        try:
            lock.mkdir(mode=0o700)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                pytest.fail("reference service ports are owned by another test")
            time.sleep(0.025)
    service = None
    try:
        for port, kind in ((47131, socket.SOCK_STREAM),
                           (47132, socket.SOCK_DGRAM),
                           (47133, socket.SOCK_STREAM)):
            with socket.socket(socket.AF_INET, kind) as probe:
                if kind == socket.SOCK_STREAM:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
        service = subprocess.Popen(
            [sys.executable, "-m", "webjam_reference"],
            cwd=ROOT / "reference_service",
            env={"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        def ready() -> bool:
            if service.poll() is not None:
                pytest.fail("reference service exited before readiness")
            try:
                with socket.create_connection(("127.0.0.1", 47131), timeout=0.1):
                    return True
            except OSError:
                return False

        _wait(ready, "reference service did not become ready")
        yield Path(binary).resolve(), build
    finally:
        if service is not None and service.poll() is None:
            service.send_signal(signal.SIGINT)
            try:
                service.wait(timeout=3)
            except subprocess.TimeoutExpired:
                service.kill()
                service.wait(timeout=3)
        lock.rmdir()


def _shared_state(revision: int = 1) -> RoomState:
    return RoomState(
        revision, "art", "paint_along",
        reference_video=ReferenceVideoSessionSnapshot(
            generation=3, playback_generation=4,
            state=ReferenceVideoPlaybackState.PLAYING, shared=True,
            source_display_name="PRIVATE Étude.mp4", identity_digest="a" * 64,
            position_s=4.5, duration_s=120,
        ),
        shared_canvas=SharedCanvasSessionSnapshot(
            generation=2, shared=True,
            join_url="drawpile://canvas.local/session:PRIVATE?v1&w&p=PRIVATE",
            server_label="canvas.local", session_label="session",
        ),
    )


def _roundtrip_udp(host_audio: socket.socket, guest_port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as guest_audio:
        guest_audio.settimeout(5)
        guest_audio.sendto(b"guest-after-room-update", ("127.0.0.1", guest_port))
        received, proxy = host_audio.recvfrom(2048)
        assert received == b"guest-after-room-update"
        host_audio.sendto(b"host-after-room-update", proxy)
        received, _ = guest_audio.recvfrom(2048)
        assert received == b"host-after-room-update"


def test_real_owners_deliver_initial_and_live_art_without_help_or_audio_proof(
    native_room_environment,
) -> None:
    binary, build = native_room_environment
    room_events: queue.Queue[TransportEvent] = queue.Queue()
    host_help: queue.Queue[TransportEvent] = queue.Queue()
    guest_help: queue.Queue[TransportEvent] = queue.Queue()
    losses: queue.Queue[int] = queue.Queue()
    host = None
    guest = None
    guest_process = None
    fresh_runtime = None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as host_audio:
        host_audio.bind(("127.0.0.1", 0))
        host_audio.settimeout(5)
        try:
            host = NativeHostTransportOwner(
                target_port=host_audio.getsockname()[1], binary=binary,
                expected_build=build, on_help=host_help.put,
            )
            invitation = host.invitation
            assert invitation is not None
            original_identity = host.room_identity
            assert original_identity is not None
            initial = _shared_state()
            # A host can share before anyone arrives. The owner must retain a
            # full snapshot and flush it on authentication without a help send.
            assert host.publish_room_state(initial)
            assert host.snapshot.phase is RemoteSessionPhase.PREPARING
            assert not host.connection_available
            assert not any(e.event_type == "room_state_accepted"
                           for e in host._process.timeline)
            generation = host.snapshot.generation
            guest = NativeGuestTransportBackend(
                binary=binary, expected_build=build,
                on_room_state=room_events.put, on_help=guest_help.put,
                on_connection_lost=losses.put,
            )
            connected = guest.start_guest(invitation, generation=1)
            guest_process = guest._process
            assert guest_process is not None
            assert host._process.process_id and guest_process.process_id
            assert host._process.process_id != guest_process.process_id
            received = _event(room_events, lambda e: e.room_state == initial)
            assert received.generation == generation
            assert guest.room_identity == original_identity
            _wait(lambda: host.connection_available, "host did not connect")
            assert guest.connection_available

            # Exercise the native authority boundary directly, beyond the
            # desktop owner's own duplicate-revision guard.
            with pytest.raises(TransportProcessError):
                host._process.publish_room_state(initial, generation=generation)
            with pytest.raises(TransportProcessError):
                guest_process.publish_room_state(initial, generation=generation)
            live = replace(initial, revision=2, reference_video=replace(
                initial.reference_video, playback_generation=5, position_s=12.5,
            ))
            assert host.publish_room_state(live)
            _event(room_events, lambda e: e.room_state == live)
            withdrawn = RoomState(3, "art", "paint_along")
            assert host.publish_room_state(withdrawn)
            _event(room_events, lambda e: e.room_state == withdrawn)

            # Exhaust the real channel's eight-frame burst. The desktop must
            # coalesce its newest state and retry the typed rate limit while
            # keeping the authenticated room alive. More than four accepted
            # frames also exercises the native stream-credit retirement.
            for revision in range(4, 36):
                try:
                    host._process.publish_room_state(
                        replace(live, revision=revision), generation=generation,
                    )
                except TransportRoomRateLimitedError:
                    break
            else:
                pytest.fail("the real room burst was not bounded")
            assert host.publish_room_state(replace(live, revision=100))
            newest = replace(live, revision=101, reference_video=replace(
                live.reference_video, playback_generation=6, position_s=19,
            ))
            assert host.publish_room_state(newest)
            _event(room_events, lambda e: e.room_state == newest)
            assert host.connection_available and guest.connection_available
            assert any(e.code == "room_state_rate_limited"
                       for e in host._process.timeline)

            # Help uses the same authorized connection but its own dispatcher
            # and allowance. Audio datagrams still pass in both directions.
            accepted = host.send_help("PRIVATE optional help", expected_generation=generation)
            _event(guest_help, lambda e: e.event_type == "help_received"
                   and e.request_id == accepted.request_id
                   and e.help_text == "PRIVATE optional help")
            _event(host_help, lambda e: e.event_type == "help_delivered"
                   and e.request_id == accepted.request_id)
            accepted = guest.send_help("PRIVATE guest reply", expected_generation=generation)
            _event(host_help, lambda e: e.event_type == "help_received"
                   and e.request_id == accepted.request_id
                   and e.help_text == "PRIVATE guest reply")
            _event(guest_help, lambda e: e.event_type == "help_delivered"
                   and e.request_id == accepted.request_id)
            _roundtrip_udp(host_audio, connected.loopback_port)

            # Reset changes the signer identity, clears pre-reset media, and
            # makes the old guest lose current connection evidence promptly.
            host.reset()
            _event(losses, lambda value: value == generation)
            assert not guest.connection_available
            assert guest.room_identity is None
            assert host.room_identity is not None
            assert host.room_identity != original_identity
            fresh_invitation = host.invitation
            assert fresh_invitation is not None
            assert fresh_invitation.session_reference == invitation.session_reference
            assert fresh_invitation.invite_reference != invitation.invite_reference
            guest.stop()
            assert not guest_process.running
            guest = None

            # Consumed invitations cannot enroll again, even after reset.
            replay = NativeGuestTransportBackend(binary=binary, expected_build=build,
                                                connect_timeout=5)
            try:
                with pytest.raises(RemoteBackendError) as rejected:
                    replay.start_guest(invitation, generation=generation)
                assert rejected.value.code is RemoteSessionErrorCode.INVITATION_UNUSABLE
            finally:
                replay.stop()

            fresh_generation = host.snapshot.generation
            assert fresh_generation > generation
            fresh = RoomState(1, "art", "talk_and_make")
            assert host.publish_room_state(fresh)
            guest = NativeGuestTransportBackend(
                binary=binary, expected_build=build, on_room_state=room_events.put,
                on_connection_lost=losses.put, connect_timeout=5,
            )
            # A new desktop has its own local generation one. The private
            # invitation contains no host counter, so the real runtime must
            # authenticate without receiving that hidden value out of band.
            fresh_runtime = RemoteSessionRuntime(guest, on_snapshot=lambda _snapshot: None)
            assert fresh_runtime.start_guest(fresh_invitation)
            connected = fresh_runtime.wait_until_settled(timeout=8)
            assert connected.phase is RemoteSessionPhase.CONNECTED
            assert connected.generation == 1 < fresh_generation
            fresh_process = guest._process
            assert fresh_process is not None
            received = _event(room_events, lambda e: e.generation == connected.generation)
            assert received.room_state == fresh
            assert guest.room_identity == host.room_identity
            _wait(lambda: host.connection_available, "reset host did not connect")
            with pytest.raises(TransportProcessError):
                host._process.publish_room_state(_shared_state(2), generation=generation)
            _roundtrip_udp(host_audio, connected.loopback_port)

            # Receipt payloads are useful only in callbacks. Neither transport
            # diagnostic timeline may retain canvas passwords, names, or help.
            for process in (host._process, guest_process, fresh_process):
                assert "PRIVATE" not in repr(process.timeline)
                assert all(e.room_state is None and not e.help_text for e in process.timeline)
            host.stop()
            _event(losses, lambda value: value == connected.generation)
            assert not fresh_runtime.connection_available
            assert fresh_runtime.snapshot.phase is RemoteSessionPhase.FAILED
            assert not guest.connection_available
            assert guest.room_identity is None
            assert host.room_identity is None
            assert not host._process.running
        finally:
            if fresh_runtime is not None:
                fresh_runtime.stop()
            elif guest is not None:
                guest.stop()
            if host is not None:
                host.stop()
