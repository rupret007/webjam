"""Opt-in real-process desktop/sidecar/reference integration gate."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest

from core.remote_invitation import issue_remote_invitation
from services.transport_runtime import TransportProcess


pytestmark = [
    pytest.mark.requires_local_socket,
    pytest.mark.skipif(
        os.environ.get("WEBJAM_RUN_REMOTE_SIDECAR_INTEGRATION") != "1",
        reason="real native sidecar integration is opt-in",
    ),
]


ROOT = Path(__file__).resolve().parents[1]


def _wait_control_ready(process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail("reference service exited before readiness")
        try:
            with socket.create_connection(("127.0.0.1", 47131), timeout=0.1):
                return
        except OSError:
            time.sleep(0.03)
    pytest.fail("reference service did not become ready")


def _wait_host_connected(process: TransportProcess) -> None:
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if any(
            event.event_type == "peer_connected"
            and event.event_id == 0
            and event.mode == "host"
            for event in process.timeline
        ):
            return
        time.sleep(0.01)
    pytest.fail("host sidecar did not publish authenticated peer connection")


def test_two_real_sidecars_carry_bidirectional_jamulus_datagrams() -> None:
    binary_text = os.environ.get("WEBJAM_TRANSPORT_BINARY", "")
    build = os.environ.get("WEBJAM_TEST_TRANSPORT_BUILD_ID", "")
    if not binary_text or not build:
        pytest.fail("sidecar integration requires exact binary and build ID")
    binary = Path(binary_text).resolve()

    service = subprocess.Popen(
        [sys.executable, "-m", "webjam_reference"],
        cwd=ROOT / "reference_service",
        env={"PYTHONUNBUFFERED": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    host_audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    host_audio.bind(("127.0.0.1", 0))
    host_audio.settimeout(5)
    host = TransportProcess(binary, expected_build=build, command_timeout=15)
    guest = TransportProcess(binary, expected_build=build, command_timeout=15)
    try:
        _wait_control_ready(service)
        host.start()
        guest.start()
        pin = host.prepare_host()
        issued = issue_remote_invitation(
            "reference-local",
            allowed_profiles={"reference-local"},
            host_spki_sha256=pin,
            ttl_seconds=120,
        )
        invitation = issued.invitation

        registered = host.open_host(
            invitation,
            target_port=host_audio.getsockname()[1],
            generation=1,
        )
        assert registered.event_type == "host_registered"
        connected = guest.open_guest(invitation, generation=1)
        assert connected.event_type == "peer_connected"
        _wait_host_connected(host)

        guest_audio = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        guest_audio.settimeout(5)
        try:
            outbound = b"real-guest-jamulus-datagram"
            guest_audio.sendto(outbound, ("127.0.0.1", connected.loopback_port))
            received, host_proxy = host_audio.recvfrom(2048)
            assert received == outbound

            inbound = b"real-host-jamulus-datagram"
            host_audio.sendto(inbound, host_proxy)
            received, _source = guest_audio.recvfrom(2048)
            assert received == inbound
        finally:
            guest_audio.close()

        assert invitation.capability_for_enrollment().hex() not in repr(host.timeline)
        assert invitation.capability_for_enrollment().hex() not in repr(guest.timeline)
        assert guest.close_peer().event_type == "peer_closed"
        assert host.close_peer().event_type == "peer_closed"
    finally:
        guest.stop()
        host.stop()
        host_audio.close()
        if service.poll() is None:
            try:
                os.killpg(service.pid, signal.SIGINT)
                service.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(service.pid, signal.SIGKILL)
                except OSError:
                    pass
                service.wait(timeout=3)
