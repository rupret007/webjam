"""Concrete desktop ownership for WebJam's bundled remote transport.

The native process owns networking and the loopback UDP proxy. This module
keeps the Qt/controller boundary small: typed invitations and bounded help
text go in; only allowlisted connection facts and ephemeral help events come
back. The built-in profile is deliberately lab-only until a public rendezvous
profile is provisioned.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import sys
import threading
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

from core.build_info import build_id
from core.remote_invitation import RemoteInvitation
from core.room_state import RoomIdentity, RoomState
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
    TransportPeerProtocolError,
    TransportRoomRateLimitedError,
)

REFERENCE_LOCAL_OPT_IN = "WEBJAM_ENABLE_REFERENCE_LOCAL"
TRANSPORT_BINARY_OVERRIDE = "WEBJAM_TRANSPORT_BINARY"
DEFAULT_REMOTE_START_TIMEOUT_SECONDS = 30.0
DEFAULT_REMOTE_CONNECT_TIMEOUT_SECONDS = 30.0
_ROOM_RETRY_DELAY_SECONDS = 0.3  # The room bucket refills one token per 250 ms.
_ROOM_RETRY_LIMIT = 8
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
LOGGER = logging.getLogger("webjam.services.native_remote_transport")


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
    """One-use guest backend for authenticated loopback and help facts.

    A guest capability is one-use at the reference service. The only failure
    that is safe to retry with the same invitation is one before
    :meth:`TransportProcess.open_guest` is entered. Once that call begins,
    cleanup intentionally reports the invitation as unusable even when the
    sidecar cannot prove whether the service consumed it.
    """

    def __init__(
        self,
        *,
        binary: str | Path | None = None,
        expected_build: str | None = None,
        connect_timeout: float = DEFAULT_REMOTE_CONNECT_TIMEOUT_SECONDS,
        on_help: Callable[[TransportEvent], None] | None = None,
        on_room_state: Callable[[TransportEvent], None] | None = None,
        on_connection_lost: Callable[[int], None] | None = None,
        schedule_callback: Callable[[Callable[[], None]], None] = lambda fn: fn(),
        schedule_help_callback: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        if on_help is not None and not callable(on_help):
            raise TypeError("on_help must be callable")
        if schedule_help_callback is not None and not callable(schedule_help_callback):
            raise TypeError("schedule_help_callback must be callable")
        self._binary = Path(binary) if binary is not None else transport_binary_path()
        self._expected_build = expected_build or _expected_build_id()
        self._connect_timeout = float(connect_timeout)
        if on_room_state is not None and not callable(on_room_state):
            raise TypeError("on_room_state must be callable")
        if on_connection_lost is not None and not callable(on_connection_lost):
            raise TypeError("on_connection_lost must be callable")
        self._on_room_state = on_room_state
        self._on_connection_lost = on_connection_lost
        self._room_identity: RoomIdentity | None = None
        self._room_revision = 0
        self._on_help = on_help
        self._schedule_callback = schedule_callback
        self._schedule_help_callback = (
            schedule_callback if schedule_help_callback is None else schedule_help_callback
        )
        self._lock = threading.RLock()
        self._process: TransportProcess | None = None
        self._phase = "idle"
        self._generation = 0

    def start_guest(
        self,
        invitation: RemoteInvitation,
        *,
        generation: int,
    ) -> RemoteGuestConnection:
        if not isinstance(invitation, RemoteInvitation):
            raise TypeError("invitation must be a RemoteInvitation")
        try:
            with self._lock:
                if self._process is not None:
                    raise RemoteBackendError(RemoteSessionErrorCode.UNAVAILABLE)
                process = TransportProcess(
                    self._binary,
                    expected_build=self._expected_build,
                    # A packaged sidecar can spend several seconds in the OS
                    # cold-launch path before it emits its first IPC event.
                    # This budget expires before open_guest sends the one-use
                    # enrollment capability, so a timeout remains retry-safe.
                    start_timeout=DEFAULT_REMOTE_START_TIMEOUT_SECONDS,
                    command_timeout=self._connect_timeout,
                    on_event=lambda event: self._handle_event(event, source=process),
                    **_integrity_options(self._binary),
                )
                self._process = process
                self._phase = "starting"
        except RemoteBackendError:
            raise
        except Exception:  # noqa: BLE001 - fixed safe failure boundary
            raise RemoteBackendError(RemoteSessionErrorCode.UNAVAILABLE) from None

        try:
            process.start()
        except Exception:  # noqa: BLE001 - no enrollment command was sent
            self._discard_failed_process(process)
            raise RemoteBackendError(RemoteSessionErrorCode.UNAVAILABLE) from None

        with self._lock:
            if self._process is process and self._phase == "starting":
                self._phase = "enrolling"
                self._generation = generation
                self._room_identity = RoomIdentity.from_invitation(invitation)
                self._room_revision = 0
                cancelled = False
            else:
                cancelled = True
        if cancelled:
            self._discard_failed_process(process)
            raise RemoteBackendError(RemoteSessionErrorCode.UNAVAILABLE) from None

        try:
            # From this call onward the service may have atomically consumed
            # the enrollment value. Never retry the invitation on uncertainty.
            connected = process.open_guest(invitation, generation=generation)
        except TransportPeerProtocolError:
            self._discard_failed_process(process)
            raise RemoteBackendError(RemoteSessionErrorCode.PEER_PROTOCOL_UNSUPPORTED) from None
        except Exception:  # noqa: BLE001 - preserve no sidecar detail
            self._discard_failed_process(process)
            raise RemoteBackendError(
                RemoteSessionErrorCode.INVITATION_UNUSABLE
            ) from None
        with self._lock:
            if self._process is process and self._phase == "enrolling":
                self._phase = "connected"
                cancelled = False
            else:
                cancelled = True
        if cancelled:
            self._discard_failed_process(process)
            raise RemoteBackendError(
                RemoteSessionErrorCode.INVITATION_UNUSABLE
            ) from None
        return RemoteGuestConnection(
            loopback_port=connected.loopback_port,
            path=TransportPath.SECURE_RELAY,
            quality=ConnectionQuality.UNKNOWN,
            generation=connected.generation,
        )

    @property
    def room_identity(self) -> RoomIdentity | None:
        with self._lock:
            return self._room_identity

    @property
    def connection_available(self) -> bool:
        with self._lock:
            process = self._process
            available = self._help_available_locked()
            lost = process is not None and self._phase == "connected" and not available
        if lost:
            self._mark_connection_lost(process)
        return available

    def _mark_connection_lost(self, source: TransportProcess) -> None:
        with self._lock:
            if source is not self._process or self._phase not in {"enrolling", "connected"}:
                return
            self._phase = "failed"
            self._room_identity = None
            self._room_revision = 0
            generation = self._generation
            callback = self._on_connection_lost
        if callback is not None:
            def deliver() -> None:
                with self._lock:
                    if (source is not self._process or self._phase != "failed"
                            or self._generation != generation):
                        return
                callback(generation)
            self._schedule_callback(deliver)

    @property
    def help_available(self) -> bool:
        """Whether this generation still has a proved, live help transport."""

        return self.connection_available

    def _help_available_locked(self) -> bool:
        process = self._process
        if process is None or self._phase != "connected" or self._generation == 0:
            return False
        return bool(process.running)

    def send_help(
        self, text: str, *, expected_generation: int | None = None
    ) -> TransportEvent:
        """Send help only through the current authenticated guest generation."""

        with self._lock:
            process = self._process
            generation = self._generation
            ready = self._help_available_locked()
            if expected_generation is not None and (
                type(expected_generation) is not int or expected_generation != generation
            ):
                ready = False
        if not ready or process is None or generation == 0:
            raise RemoteBackendError(RemoteSessionErrorCode.TRANSPORT_FAILED)
        try:
            accepted = process.send_help(text, generation=generation)
        except ValueError:
            raise ValueError("help text must be bounded plain text") from None
        except Exception:  # noqa: BLE001 - fixed help failure boundary
            raise RemoteBackendError(
                RemoteSessionErrorCode.TRANSPORT_FAILED
            ) from None
        with self._lock:
            if (
                self._process is not process
                or self._generation != generation
                or not self._help_available_locked()
                or not isinstance(accepted, TransportEvent)
                or accepted.event_type != "help_accepted"
                or accepted.mode != "guest"
                or accepted.generation != generation
            ):
                raise RemoteBackendError(RemoteSessionErrorCode.TRANSPORT_FAILED)
        return accepted

    def _handle_event(self, event: TransportEvent, *, source: TransportProcess) -> None:
        with self._lock:
            if source is not self._process:
                return
            lost = (
                event.event_type == "stopped"
                or (event.event_type == "error" and event.event_id == 0
                    and event.state in {"failed", "closed"})
                or (event.event_type == "peer_closed" and event.mode == "guest"
                    and event.generation == self._generation)
            )
            if not lost:
                if (self._phase not in {"enrolling", "connected"}
                        or event.mode != "guest" or event.generation != self._generation
                        or not source.running or event.code != "ok"
                        or event.state != "connected"):
                    return
                if event.event_type == "room_state_received":
                    if (type(event.room_state) is not RoomState
                            or event.room_state.revision <= self._room_revision):
                        return
                    self._room_revision = event.room_state.revision
                    callback = self._on_room_state
                    schedule = self._schedule_callback
                elif event.event_type in {"help_received", "help_delivered"}:
                    callback = self._on_help
                    schedule = self._schedule_help_callback
                else:
                    return
        if lost:
            self._mark_connection_lost(source)
            return
        if callback is not None:
            def deliver() -> None:
                with self._lock:
                    if (source is not self._process
                            or self._phase not in {"enrolling", "connected"}
                            or event.generation != self._generation or not source.running):
                        return
                    if (event.room_state is not None
                            and event.room_state.revision != self._room_revision):
                        return
                callback(event)
            schedule(deliver)

    def _discard_failed_process(self, process: TransportProcess) -> None:
        """Keep an unreaped child owned for a subsequent cleanup retry."""

        stopped = False
        try:
            process.stop()
            stopped = True
        except Exception as exc:  # noqa: BLE001 - child cleanup is best effort
            LOGGER.error(
                "Remote guest transport cleanup failed; exception_type=%s",
                type(exc).__name__,
            )
        finally:
            with self._lock:
                if self._process is process:
                    if stopped:
                        self._process = None
                    self._phase = "idle" if stopped else "failed"
                    self._generation = 0
                    self._room_identity = None
                    self._room_revision = 0

    def stop(self) -> None:
        with self._lock:
            process = self._process
            self._phase = "stopping"
            self._generation = 0
            self._room_identity = None
            self._room_revision = 0
        if process is None:
            with self._lock:
                self._phase = "idle"
            return
        # Retire queued callbacks immediately, but retain the child handle
        # until bounded whole-process shutdown proves cleanup succeeded.
        process.stop()
        with self._lock:
            if self._process is process:
                self._process = None
                self._phase = "idle"


class NativeHostTransportOwner:
    """Invitation owner plus live host transport and ephemeral help."""

    def __init__(
        self,
        *,
        target_port: int,
        profile_id: str = "reference-local",
        binary: str | Path | None = None,
        expected_build: str | None = None,
        on_snapshot: Callable[[RemoteSessionSnapshot], None] | None = None,
        on_help: Callable[[TransportEvent], None] | None = None,
        schedule_callback: Callable[[Callable[[], None]], None] = lambda fn: fn(),
        schedule_help_callback: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        if not 1 <= int(target_port) <= 65_535:
            raise ValueError("target_port is out of range")
        DEFAULT_RENDEZVOUS_PROFILES.resolve(profile_id)
        self._target_port = int(target_port)
        self._profile_id = str(profile_id)
        self._on_snapshot = on_snapshot or (lambda _snapshot: None)
        if on_help is not None and not callable(on_help):
            raise TypeError("on_help must be callable")
        if schedule_help_callback is not None and not callable(schedule_help_callback):
            raise TypeError("schedule_help_callback must be callable")
        self._on_help = on_help
        self._schedule_callback = schedule_callback
        self._schedule_help_callback = (
            schedule_callback if schedule_help_callback is None else schedule_help_callback
        )
        self._lock = threading.RLock()
        self._generation = 0
        self._active_invitation: RemoteInvitation | None = None
        self._room_identity: RoomIdentity | None = None
        self._room_state: RoomState | None = None
        self._room_sent_revision = 0
        self._room_worker_generation = 0
        self._room_cancel = threading.Event()
        self._registering_generation = 0
        self._pending_connected: TransportEvent | None = None
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
            self._drain_pending_connection()
        except Exception:
            self._process.stop()
            self._owner = None
            self._stopped = True
            raise

    @property
    def invitation_available(self) -> bool:
        owner = self._owner
        return bool(not self._stopped and self.snapshot.phase is not RemoteSessionPhase.FAILED
                    and owner is not None and owner.invitation_available)

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
        if (owner is None or self._stopped
                or self.snapshot.phase is RemoteSessionPhase.FAILED):
            raise RuntimeError("No remote invitation is active.")
        return owner.copy_for_clipboard()

    def reset(self) -> None:
        owner = self._owner
        if self._stopped or owner is None:
            raise RuntimeError("No remote invitation is active.")
        owner.reset()
        self._drain_pending_connection()

    @property
    def room_identity(self) -> RoomIdentity | None:
        with self._lock:
            return self._room_identity

    @property
    def connection_available(self) -> bool:
        with self._lock:
            old_snapshot = self._snapshot
            available = self._help_available_locked()
            snapshot = self._snapshot
        if snapshot is not old_snapshot:
            self._publish(snapshot)
        return available

    def publish_room_state(self, state: RoomState) -> bool:
        """Cache a full host state and schedule its bounded, coalesced send."""

        if type(state) is not RoomState:
            raise ValueError("room state must be typed")
        with self._lock:
            if self._stopped or self._snapshot.phase is RemoteSessionPhase.FAILED:
                return False
            if self._room_state is not None and state.revision <= self._room_state.revision:
                return state == self._room_state
            self._room_state = state
        self._flush_room_state()
        return True

    def _flush_room_state(self) -> None:
        with self._lock:
            generation = self._generation
            if (not self._help_available_locked() or self._room_state is None
                    or self._room_worker_generation == generation
                    or self._room_state.revision <= self._room_sent_revision):
                return
            self._room_worker_generation = generation
            cancel = self._room_cancel
        threading.Thread(target=self._room_state_worker, args=(generation, cancel),
                         name="webjam-host-room-state", daemon=True).start()

    def _room_state_worker(self, generation: int, cancel: threading.Event) -> None:
        retries = 0
        try:
            while True:
                with self._lock:
                    state = self._room_state
                    if (generation != self._generation or cancel.is_set()
                            or not self._help_available_locked() or state is None
                            or state.revision <= self._room_sent_revision):
                        return
                try:
                    accepted = self._process.publish_room_state(state, generation=generation)
                    if (not isinstance(accepted, TransportEvent)
                            or accepted.event_type != "room_state_accepted"
                            or accepted.mode != "host" or accepted.generation != generation
                            or accepted.code != "ok" or accepted.state != "connected"
                            or accepted.event_id < 1 or accepted.request_id != accepted.event_id):
                        raise TransportProcessError("The room state could not be sent.")
                except TransportRoomRateLimitedError:
                    retries += 1
                    if retries >= _ROOM_RETRY_LIMIT:
                        self._fail_room_state(generation, cancel)
                        return
                    # One slot holds the full newest state. Every retry reads
                    # it again, so old playback positions never form a queue.
                    if cancel.wait(_ROOM_RETRY_DELAY_SECONDS):
                        return
                    continue
                except Exception:
                    self._fail_room_state(generation, cancel)
                    return
                retries = 0
                with self._lock:
                    if generation != self._generation or cancel.is_set():
                        return
                    self._room_sent_revision = state.revision
        finally:
            with self._lock:
                if self._room_worker_generation == generation:
                    self._room_worker_generation = 0
            # A newer snapshot may have arrived as this worker was leaving.
            self._flush_room_state()

    def _fail_room_state(self, generation: int, cancel: threading.Event) -> None:
        # No state payload or child detail belongs in diagnostics. A temporary
        # rate limit only becomes a failure after bounded consecutive retries.
        with self._lock:
            if generation != self._generation or cancel.is_set() or self._stopped:
                return
            self._room_identity = None
            self._room_state = None
            self._snapshot = RemoteSessionSnapshot(
                phase=RemoteSessionPhase.FAILED, role=SessionRole.HOST,
                generation=generation, path=TransportPath.SECURE_RELAY,
                error_code=RemoteSessionErrorCode.TRANSPORT_FAILED,
            )
            snapshot = self._snapshot
        self._publish(snapshot)

    @property
    def help_available(self) -> bool:
        """Whether the current host peer remains proved and live."""

        return self.connection_available

    def _help_available_locked(self) -> bool:
        if (
            self._stopped
            or self._snapshot.phase is not RemoteSessionPhase.CONNECTED
            or self._active_invitation is None
            or self._generation == 0
        ):
            return False
        if not self._process.running:
            self._room_identity = None
            self._room_state = None
            self._snapshot = RemoteSessionSnapshot(
                phase=RemoteSessionPhase.FAILED,
                role=SessionRole.HOST,
                generation=self._generation,
                path=TransportPath.SECURE_RELAY,
                error_code=RemoteSessionErrorCode.TRANSPORT_FAILED,
            )
            return False
        return True

    def send_help(
        self, text: str, *, expected_generation: int | None = None
    ) -> TransportEvent:
        """Send help only through the current authenticated host generation."""

        with self._lock:
            generation = self._generation
            ready = self._help_available_locked()
            if expected_generation is not None and (
                type(expected_generation) is not int or expected_generation != generation
            ):
                ready = False
        if not ready or generation == 0:
            raise RemoteBackendError(RemoteSessionErrorCode.TRANSPORT_FAILED)
        try:
            accepted = self._process.send_help(text, generation=generation)
        except ValueError:
            raise ValueError("help text must be bounded plain text") from None
        except Exception:  # noqa: BLE001 - fixed help failure boundary
            raise RemoteBackendError(
                RemoteSessionErrorCode.TRANSPORT_FAILED
            ) from None
        with self._lock:
            if (
                self._generation != generation
                or not self._help_available_locked()
                or not isinstance(accepted, TransportEvent)
                or accepted.event_type != "help_accepted"
                or accepted.mode != "host"
                or accepted.generation != generation
            ):
                raise RemoteBackendError(RemoteSessionErrorCode.TRANSPORT_FAILED)
        return accepted

    def register_invitation(self, invitation: RemoteInvitation) -> None:
        with self._lock:
            if self._stopped or self._active_invitation is not None:
                raise RuntimeError("A remote invitation is already registered.")
            self._room_cancel.set()
            self._room_cancel = threading.Event()
            self._generation += 1
            generation = self._generation
            self._registering_generation = generation
        registered = self._process.open_host(
            invitation,
            target_port=self._target_port,
            generation=generation,
        )
        with self._lock:
            if self._stopped or generation != self._generation:
                raise TransportProcessError("The transport process was stopped.")
            self._registering_generation = 0
            self._active_invitation = invitation
            self._room_identity = RoomIdentity.from_invitation(invitation)
            self._room_sent_revision = 0
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
            if self._registering_generation:
                self._registering_generation = 0
                self._pending_connected = None
            if invitation is not self._active_invitation:
                return
            self._pending_connected = None
            self._room_cancel.set()
            self._active_invitation = None
            self._room_identity = None
            self._room_state = None
            self._room_sent_revision = 0
        if self._process.running:
            self._process.close_peer()

    def stop(self) -> None:
        with self._lock:
            if self._snapshot.phase is RemoteSessionPhase.STOPPED:
                return
            self._stopped = True
            self._room_cancel.set()
            self._room_identity = None
            self._room_state = None
            owner = self._owner
            self._snapshot = RemoteSessionSnapshot(
                phase=RemoteSessionPhase.STOPPING, role=SessionRole.HOST,
                generation=max(1, self._generation), path=TransportPath.SECURE_RELAY,
            )
            snapshot = self._snapshot
        self._publish(snapshot)
        try:
            # A failed reap keeps both process and invitation owners reachable.
            # Retrying End Room must attempt the real cleanup again.
            self._process.stop()
            if owner is not None:
                owner.stop()
        except Exception:
            with self._lock:
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.FAILED, role=SessionRole.HOST,
                    generation=max(1, self._generation), path=TransportPath.SECURE_RELAY,
                    error_code=RemoteSessionErrorCode.STOP_FAILED,
                )
                snapshot = self._snapshot
            self._publish(snapshot)
            raise TransportProcessError("The transport process did not stop.") from None
        with self._lock:
            self._owner = None
            self._active_invitation = None
            self._room_sent_revision = 0
            self._snapshot = RemoteSessionSnapshot(
                phase=RemoteSessionPhase.STOPPED,
                role=SessionRole.HOST,
                generation=max(1, self._generation),
                path=TransportPath.SECURE_RELAY,
            )
            snapshot = self._snapshot
        self._publish(snapshot)

    def _drain_pending_connection(self) -> None:
        with self._lock:
            event = self._pending_connected
            self._pending_connected = None
        if event is not None:
            self._handle_event(event)

    def _handle_event(self, event: TransportEvent) -> None:
        if event.event_type in {"help_received", "help_delivered"}:
            with self._lock:
                if (
                    not self._help_available_locked()
                    or event.mode != "host"
                    or event.generation != self._generation
                ):
                    return
                callback = self._on_help
            if callback is not None:
                def deliver() -> None:
                    with self._lock:
                        if (
                            not self._help_available_locked()
                            or event.mode != "host"
                            or event.generation != self._generation
                        ):
                            return
                    callback(event)

                self._schedule_help_callback(deliver)
            return
        if event.event_type in {"stopped", "peer_closed"}:
            with self._lock:
                if self._stopped:
                    return
                if event.event_type == "peer_closed" and (
                    event.mode != "host" or event.generation != self._generation
                ):
                    return
                self._room_identity = None
                self._room_state = None
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.FAILED,
                    role=SessionRole.HOST,
                    generation=max(1, self._generation),
                    path=TransportPath.SECURE_RELAY,
                    error_code=RemoteSessionErrorCode.TRANSPORT_FAILED,
                )
                snapshot = self._snapshot
            self._publish(snapshot)
            return
        if event.event_type not in {"peer_connected", "error"} or event.event_id != 0:
            return
        consumed_owner: RemoteInvitationOwner | None = None
        consumed_invitation: RemoteInvitation | None = None
        with self._lock:
            if self._stopped:
                return
            if event.event_type == "peer_connected" and event.mode == "host":
                if (event.generation == self._generation
                        and self._registering_generation == event.generation
                        and self._active_invitation is None):
                    self._pending_connected = event
                    return
                if (
                    event.generation != self._generation
                    or self._active_invitation is None
                    or self._owner is None
                ):
                    return
                consumed_owner = self._owner
                consumed_invitation = self._active_invitation
            else:
                self._room_identity = None
                self._room_state = None
                self._snapshot = RemoteSessionSnapshot(
                    phase=RemoteSessionPhase.FAILED,
                    role=SessionRole.HOST,
                    generation=max(1, self._generation),
                    path=TransportPath.SECURE_RELAY,
                    error_code=(
                        RemoteSessionErrorCode.PEER_PROTOCOL_UNSUPPORTED
                        if event.code == "peer_protocol_unsupported"
                        else RemoteSessionErrorCode.TRANSPORT_FAILED
                    ),
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
        if snapshot.phase is RemoteSessionPhase.CONNECTED:
            self._flush_room_state()

    def _publish(self, snapshot: RemoteSessionSnapshot) -> None:
        def deliver() -> None:
            with self._lock:
                if snapshot is not self._snapshot:
                    return
            self._on_snapshot(deepcopy(snapshot))

        self._schedule_callback(deliver)

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
