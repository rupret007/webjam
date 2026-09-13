"""Ephemeral real LAN host for the app-hosted iOS runtime Join gate.

The invitation is written only to a private generated Swift test source. It is
never a command argument, environment variable, log entry, or app override.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from core.network_invite import create_invite_link
from core.session_transfer import (
    EnrollmentRegistry,
    SessionControlState,
    SessionCredentials,
    SessionPeerServer,
    TransferStore,
)

_PRIVATE_NETWORKS = tuple(map(ipaddress.IPv4Network, (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
)))


class SimulatorHostError(RuntimeError):
    """Fixed diagnostics deliberately omit private transport details."""


@dataclass(frozen=True)
class SimulatorHostEvidence:
    _authenticated_poll: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def authenticated_poll_seen(self) -> bool:
        return self._authenticated_poll.is_set()


def _private_ipv4_addresses() -> tuple[str, ...]:
    try:
        listing = subprocess.check_output(
            ["/sbin/ifconfig", "-a"], text=True, stderr=subprocess.DEVNULL, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise SimulatorHostError("The iOS runtime gate could not inspect local interfaces.") from None
    addresses = []
    for block in re.split(r"(?m)(?=^[^\s:]+: flags=)", listing):
        first_line = block.partition("\n")[0]
        flags = re.search(r"<([^>]+)>", first_line)
        if flags is None:
            continue
        interface_flags = set(flags.group(1).split(","))
        if "UP" not in interface_flags or "LOOPBACK" in interface_flags or "status: inactive" in block:
            continue
        for value in re.findall(r"(?m)^\s+inet (\d+(?:\.\d+){3})\s", block):
            try:
                address = ipaddress.IPv4Address(value)
            except ipaddress.AddressValueError:
                continue
            if any(address in network for network in _PRIVATE_NETWORKS) and value not in addresses:
                addresses.append(value)
    if not addresses:
        raise SimulatorHostError("The iOS runtime gate requires an active RFC1918 IPv4 interface.")
    return tuple(addresses)


def _start_host(storage: Path, credentials: SessionCredentials) -> SessionPeerServer:
    control = SessionControlState(
        storage, credentials.session_id, creator_profile_key="art", art_start_key="paint_along",
    )
    registry = EnrollmentRegistry(storage, credentials)
    transfers = TransferStore(storage, credentials.session_id)
    for address in _private_ipv4_addresses():
        try:
            server = SessionPeerServer(
                address, 0, registry=registry, control=control, transfers=transfers,
            )
        except OSError:
            continue
        server.start()
        return server
    raise SimulatorHostError("The iOS runtime gate could not bind its private LAN host.")


def _observe_reads(
    server: SessionPeerServer, evidence: SimulatorHostEvidence, stop: threading.Event,
) -> None:
    # Presence is created only by an authenticated /v1/state read. Retain that
    # observation after Leave and the host's normal five-second presence expiry.
    while not stop.is_set():
        if server.room_participants():
            evidence._authenticated_poll.set()
            return
        stop.wait(0.05)


@contextmanager
def simulator_host(root: Path) -> Iterator[SimulatorHostEvidence]:
    """Generate the fixture before XcodeGen; require a real iOS room read.

    Wrap the unsigned simulator gate in this context. A normal exit without an
    authenticated room poll fails. Simulator failures still retire the server
    and delete both the generated invitation and temporary host storage.
    """
    fixture = root / "ios/ArtCompanionRuntimeTests/GeneratedHost.swift"
    storage = None
    server = None
    observer = None
    fixture_created = False
    stop = threading.Event()
    evidence = SimulatorHostEvidence()
    try:
        try:
            storage = tempfile.TemporaryDirectory(prefix="webjam-ios-runtime-")
            credentials = SessionCredentials.create()
            server = _start_host(Path(storage.name), credentials)
            invitation = create_invite_link(
                server.address[0], session_name="iOS Runtime Paint along",
                session_id=credentials.session_id, peer_port=server.address[1],
                invite_token=credentials.invite_token,
            )
            # URL encoding guarantees ASCII; JSON quoting is therefore also a
            # valid Swift literal, without Swift interpolation or Unicode escapes.
            if not invitation.isascii() or "\\" in invitation:
                raise SimulatorHostError("The iOS runtime gate could not generate its fixture.")
            fixture.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(fixture, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            fixture_created = True
            with os.fdopen(descriptor, "w", encoding="utf-8") as source:
                source.write(
                    "// Ephemeral private runtime fixture. Never commit or publish.\n"
                    "enum GeneratedHost {\n"
                    f"    static let invitation = {json.dumps(invitation)}\n"
                    "}\n"
                )
            observer = threading.Thread(
                target=_observe_reads, args=(server, evidence, stop),
                name="webjam-ios-runtime-evidence", daemon=True,
            )
            observer.start()
        except SimulatorHostError:
            raise
        except Exception:
            raise SimulatorHostError("The iOS runtime gate could not prepare its private host.") from None

        yield evidence
        # Check once synchronously as well, so an immediate test completion
        # cannot race the observer's next scheduling opportunity.
        if server.room_participants():
            evidence._authenticated_poll.set()
        if not evidence.authenticated_poll_seen:
            raise SimulatorHostError("The iOS runtime gate observed no authenticated room read.")
    finally:
        stop.set()
        if observer is not None and observer.ident is not None:
            observer.join(timeout=5)
        cleanup_failed = False
        for cleanup in (
            server.stop if server is not None else None,
            fixture.unlink if fixture_created else None,
            storage.cleanup if storage is not None else None,
        ):
            if cleanup is not None:
                try:
                    cleanup()
                except Exception:
                    cleanup_failed = True
        if cleanup_failed:
            raise SimulatorHostError("The iOS runtime gate could not finish private host cleanup.") from None
