"""Concrete desktop ownership for WebJam's bundled remote transport.

The native process owns networking and the loopback UDP proxy.  This module
keeps the Qt/controller boundary small: typed invitations go in and only
allowlisted connection facts come back.  The built-in profile is deliberately
lab-only until a public rendezvous profile is provisioned.
"""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import platform
import re
import sys
import threading
from typing import Callable

from core.build_info import build_id
from core.remote_invitation import RemoteInvitation
from core.rendezvous_profiles import DEFAULT_RENDEZVOUS_PROFILES
from core.session_transport import ConnectionQuality, SessionRole, TransportPath
from services.remote_invitation_owner import RemoteInvitationOwner
from services.remote_session_runtime import (
    RemoteBackendError,
    RemoteGuestConnection,
    RemoteSessionErrorCode,
    RemoteSessionPhase,
    RemoteSessionSnapshot,
)
from services.transport_runtime import (
    TransportEvent,
    TransportProcess,
    TransportProcessError,
)


REFERENCE_LOCAL_OPT_IN = "WEBJAM_ENABLE_REFERENCE_LOCAL"
TRANSPORT_BINARY_OVERRIDE = "WEBJAM_TRANSPORT_BINARY"
DEFAULT_REMOTE_CONNECT_TIMEOUT_SECONDS = 30.0
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def reference_local_host_requested() -> bool:
    """Return the explicit developer/lab opt-in, never a saved user setting."""

    return str(os.environ.get(REFERENCE_LOCAL_OPT_IN, "")).strip() == "1"


def transport_binary_path() -> Path:
    """Locate only the sidecar belonging to this checkout or frozen app."""

    executable_name = "webjam-fabric.exe" if os.name == "nt" else "webjam-fabric"
    if getattr(sys, "frozen", False):
        # Frozen builds must use the sibling covered by the app's signature;
        # environment overrides are a source-checkout convenience only.
        return Path(sys.executable).resolve().parent / executable_name

    override = str(os.environ.get(TRANSPORT_BINARY_OVERRIDE, "") or "").strip()
    if override:
        return Path(override).expanduser()

    machine = platform.machine().lower()
    if sys.platform == "darwin":
        architecture = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
        target = f"darwin-{architecture}"
    elif sys.platform == "win32":
        target = "windows-amd64"
    else:
        target = f"linux-{'arm64' if machine in {'arm64', 'aarch64'} else 'amd64'}"
    return Path(__file__).resolve().parents[1] / "transport" / "build" / target / executable_name


def _expected_build_id() -> str:
    value = build_id()
    if not value:
        raise TransportProcessError("WebJam could not verify its secure transport build.")
    return value


def _transport_manifest_path(binary: Path) -> Path:
    """Return the signed-bundle data path for the transport hash manifest."""

    # macOS treats every item in Contents/MacOS as executable code during
    # strict verification.  Keep the signed binary there, but seal its hash as
    # ordinary bundle data under Contents/Resources.  Windows keeps the
    # manifest beside the executable in the flat PyInstaller directory.
    if (
        sys.platform == "darwin"
        and binary.parent.name == "MacOS"
        and binary.parent.parent.name == "Contents"
    ):
        return binary.parent.parent / "Resources" / "webjam-fabric.sha256"
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        raise TransportProcessError(
            "WebJam could not verify its secure transport build."
        )
    return binary.parent / "webjam-fabric.sha256"


def _integrity_options(binary: Path) -> dict[str, object]:
    """Return mandatory signed-bundle integrity checks for frozen builds."""

    if not getattr(sys, "frozen", False):
        return {}
    manifest = _transport_manifest_path(binary)
    try:
        encoded = manifest.read_bytes()
    except OSError as exc:
        raise TransportProcessError(
            "WebJam could not verify its secure transport build."
        ) from exc
    if len(encoded) not in {64, 65} or (len(encoded) == 65 and encoded[-1:] != b"\n"):
        raise TransportProcessError(
            "WebJam could not verify its secure transport build."
        )
    try:
        digest = encoded.rstrip(b"\n").decode("ascii")
    except UnicodeDecodeError as exc:
        raise TransportProcessError(
            "WebJam could not verify its secure transport build."
        ) from exc
    if _SHA256.fullmatch(digest) is None:
        raise TransportProcessError(
            "WebJam could not verify its secure transport build."
        )
    machine = platform.machine().lower()
    expected_machine = (
        "arm64" if machine in {"arm64", "aarch64"} else "x86_64"
    )
    return {
        "expected_sha256": digest,
        "expected_machine": expected_machine,
        "require_platform_signature": True,
    }


class NativeGuestTransportBackend:
    """One-use guest backend that returns only authenticated loopback facts."""

    def __init__(
        self,
        *,
        binary: str | Path | None = None,
        expected_build: str | None = None,
        connect_timeout: float = DEFAULT_REMOTE_CONNECT_TIMEOUT_SECONDS,
    ) -> None:
        self._binary = Path(binary) if binary is not None else transport_binary_path()
        self._expected_build = expected_build or _expected_build_id()
        self._connect_timeout = float(connect_timeout)
        self._lock = threading.RLock()
        self._process: TransportProcess | None = None

    def start_guest(
        self,
        invitation: RemoteInvitation,
        *,
        generation: int,
    ) -> RemoteGuestConnection:
        if not isinstance(invitation, RemoteInvitation):
            raise TypeError("invitation must be a RemoteInvitation")
        with self._lock:
            if self._process is not None:
                raise RemoteBackendError(RemoteSessionErrorCode.UNAVAILABLE)
            process = TransportProcess(
                self._binary,
                expected_build=self._expected_build,
                command_timeout=self._connect_timeout,
                **_integrity_options(self._binary),
            )
            self._process = process
        try:
            process.start()
            connected = process.open_guest(invitation, generation=generation)
            return RemoteGuestConnection(
                loopback_port=connected.loopback_port,
                path=TransportPath.SECURE_RELAY,
                quality=ConnectionQuality.UNKNOWN,
                generation=connected.generation,
            )
        except Exception:
            process.stop()
            with self._lock:
                if self._process is process:
                    self._process = None
            raise

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._process = None
        if process is None:
            return
        if process.running:
            try:
                process.close_peer()
            except TransportProcessError:
                pass
        process.stop()


class NativeHostTransportOwner:
    """Invitation owner plus a live host-side native transport registration."""

    def __init__(
        self,
        *,
        target_port: int,
        profile_id: str = "reference-local",
        binary: str | Path | None = None,
        expected_build: str | None = None,
        on_snapshot: Callable[[RemoteSessionSnapshot], None] | None = None,
        schedule_callback: Callable[[Callable[[], None]], None] = lambda fn: fn(),
    ) -> None:
        if not 1 <= int(target_port) <= 65_535:
            raise ValueError("target_port is out of range")
        DEFAULT_RENDEZVOUS_PROFILES.resolve(profile_id)
        self._target_port = int(target_port)
        self._profile_id = str(profile_id)
        self._on_snapshot = on_snapshot or (lambda _snapshot: None)
        self._schedule_callback = schedule_callback
        self._lock = threading.RLock()
        self._generation = 0
        self._active_invitation: RemoteInvitation | None = None
        self._owner: RemoteInvitationOwner | None = None
        self._stopped = False
        self._snapshot = RemoteSessionSnapshot(
            phase=RemoteSessionPhase.PREPARING,
            role=SessionRole.HOST,
            generation=1,
            path=TransportPath.SECURE_RELAY,
        )
        transport_binary = (
            Path(binary) if binary is not None else transport_binary_path()
        )
        self._process = TransportProcess(
            transport_binary,
            expected_build=expected_build or _expected_build_id(),
            command_timeout=DEFAULT_REMOTE_CONNECT_TIMEOUT_SECONDS,
            on_event=self._handle_event,
            **_integrity_options(transport_binary),
        )

        try:
            self._process.start()
            pin = self._process.prepare_host()
            owner = RemoteInvitationOwner(
                self,
                profile_id=self._profile_id,
                allowed_profiles=DEFAULT_RENDEZVOUS_PROFILES.profile_ids,
                host_spki_sha256=pin,
            )
            self._owner = owner
            owner.start()
        except Exception:
            self._process.stop()
            self._owner = None
            self._stopped = True
            raise

    @property
    def invitation_available(self) -> bool:
        owner = self._owner
        return bool(owner is not None and owner.invitation_available)

    @property
    def invitation(self) -> RemoteInvitation | None:
        owner = self._owner
        return owner.invitation if owner is not None else None

    @property
    def snapshot(self) -> RemoteSessionSnapshot:
        with self._lock:
            return self._snapshot

    def copy_for_clipboard(self) -> str:
        owner = self._owner
        if owner is None:
            raise RuntimeError("No remote invitation is active.")
        return owner.copy_for_clipboard()

    def reset(self) -> None:
        owner = self._owner
        if owner is None:
            raise RuntimeError("No remote invitation is active.")
        owner.reset()

    def register_invitation(self, invitation: RemoteInvitation) -> None:
        with self._lock:
            if self._stopped or self._active_invitation is not None:
                raise RuntimeError("A remote invitation is already registered.")
            self._generation += 1
            generation = self._generation
        registered = self._process.open_host(
            invitation,
            target_port=self._target_port,
            generation=generation,
        )
        with self._lock:
            self._active_invitation = invitation
            self._snapshot = RemoteSessionSnapshot(
                phase=RemoteSessionPhase.PREPARING,
                role=SessionRole.HOST,
                generation=registered.generation,
                loopback_port=registered.loopback_port,
                path=TransportPath.SECURE_RELAY,
                quality=ConnectionQuality.UNKNOWN,
            )
            snapshot = self._snapshot
        self._publish(snapshot)

    def revoke_invitation(self, invitation: RemoteInvitation) -> None:
        with self._lock:
            if invitation is not self._active_invitation:
                return
            self._active_invitation = None
        if self._process.running:
            self._process.close_peer()

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            owner = self._owner
            self._owner = None
        if owner is not None:
            owner.stop()
        self._process.stop()
        with self._lock:
            self._active_invitation = None
            self._snapshot = RemoteSessionSnapshot(
                phase=RemoteSessionPhase.STOPPED,
                role=SessionRole.HOST,
                generation=max(1, self._generation),
                path=TransportPath.SECURE_RELAY,
            )
            snapshot = self._snapshot
        self._publish(snapshot)

    def _handle_event(self, event: TransportEvent) -> None:
        if event.event_type not in {"peer_connected", "error"} or event.event_id != 0:
            return
        consumed_owner: RemoteInvitationOwner | None = None
        consumed_invitation: RemoteInvitation | None = None
        with self._lock:
            if self._stopped:
                return
            if event.event_type == "peer_connected" and event.mode == "host":
                if (
                    event.generation != self._generation
                    or self._active_invitation is None
                    or self._owner is None
                ):
                    return
                consumed_owner = self._owner
                consumed_invitation = self._active_invitation
            else:
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.FAILED,
                    role=SessionRole.HOST,
                    generation=max(1, self._generation),
                    path=TransportPath.SECURE_RELAY,
                    error_code=RemoteSessionErrorCode.TRANSPORT_FAILED,
                )
                snapshot = self._snapshot

        if consumed_owner is not None and consumed_invitation is not None:
            if not consumed_owner.mark_enrollment_consumed(consumed_invitation):
                return
            with self._lock:
                if (
                    self._stopped
                    or event.generation != self._generation
                    or self._active_invitation is not consumed_invitation
                ):
                    return
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.CONNECTED,
                    role=SessionRole.HOST,
                    generation=event.generation,
                    loopback_port=event.loopback_port,
                    path=TransportPath.SECURE_RELAY,
                    quality=ConnectionQuality.UNKNOWN,
                )
                snapshot = self._snapshot
        self._publish(snapshot)

    def _publish(self, snapshot: RemoteSessionSnapshot) -> None:
        self._schedule_callback(
            lambda value=deepcopy(snapshot): self._on_snapshot(value)
        )

    def __repr__(self) -> str:
        return (
            "NativeHostTransportOwner("
            f"phase={self.snapshot.phase.value!r}, private=[redacted])"
        )


__all__ = [
    "NativeGuestTransportBackend",
    "NativeHostTransportOwner",
    "reference_local_host_requested",
    "transport_binary_path",
]
