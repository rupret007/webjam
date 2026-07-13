"""Strict desktop ownership for the bundled WebJam transport sidecar.

The Go sidecar is deliberately treated as an untrusted child process boundary:
it receives no command-line configuration, no inherited environment, and only
the small versioned JSON-lines protocol defined by ``transport/internal/ipc``.
Invitation material crosses this boundary only in one bounded stdin command.
It is never placed in argv, the environment, an event, or the diagnostic
timeline.  The public host pin is decoded into a fixed-size value before the
desktop may issue an invitation.
"""

from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from typing import Any, BinaryIO, Callable

from core.remote_invitation import RemoteInvitation


IPC_VERSION = 1
MAX_IPC_LINE_BYTES = 64 * 1024
MAX_EVENT_LINE_BYTES = 4 * 1024
MAX_TIMELINE_EVENTS = 64
DEFAULT_START_TIMEOUT_SECONDS = 5.0
DEFAULT_COMMAND_TIMEOUT_SECONDS = 5.0
DEFAULT_STOP_TIMEOUT_SECONDS = 3.0

_BUILD_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,95}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_EVENT_FIELDS = frozenset(
    {
        "version",
        "id",
        "type",
        "code",
        "state",
        "mode",
        "generation",
        "loopback_port",
        "profile_id",
        "host_spki_sha256",
        "build",
    }
)
_EVENT_TYPES = frozenset(
    {
        "ready",
        "hello",
        "host_prepared",
        "host_registered",
        "peer_connected",
        "peer_closed",
        "stopped",
        "error",
    }
)
_EVENT_CODES = frozenset(
    {
        "",
        "ok",
        "protocol_violation",
        "peer_already_open",
        "peer_not_open",
        "open_failed",
        "identity_not_prepared",
        "unsupported_profile",
        "enrollment_invalid",
    }
)
_EVENT_STATES = frozenset(
    {
        "",
        "idle",
        "identity_ready",
        "connecting",
        "host_waiting",
        "connected",
        "closed",
        "stopped",
        "failed",
    }
)
_EVENT_MODES = frozenset({"", "host", "guest"})
_PROFILE_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")


class TransportProcessError(RuntimeError):
    """Base class for fixed, privacy-safe sidecar ownership failures."""


class TransportLaunchError(TransportProcessError):
    """The packaged sidecar could not be launched or identified safely."""


class TransportProtocolError(TransportProcessError):
    """The sidecar violated its bounded, allowlisted IPC contract."""


class TransportTimeoutError(TransportProcessError):
    """The sidecar did not complete a bounded lifecycle operation."""


@dataclass(frozen=True)
class TransportEvent:
    """One allowlisted sidecar event, containing no free-form text."""

    event_id: int
    event_type: str
    code: str = ""
    state: str = ""
    mode: str = ""
    generation: int = 0
    loopback_port: int = 0
    profile_id: str = ""
    host_spki_sha256: bytes = b""
    build: str = ""


def _decode_fixed_public(value: Any, size: int) -> bytes:
    if not isinstance(value, str) or "=" in value:
        raise TransportProtocolError("The transport process sent invalid data.")
    if len(value) != (size * 8 + 5) // 6:
        raise TransportProtocolError("The transport process sent invalid data.")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, UnicodeError) as exc:
        raise TransportProtocolError(
            "The transport process sent invalid data."
        ) from exc
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if len(decoded) != size or canonical != value or not any(decoded):
        raise TransportProtocolError("The transport process sent invalid data.")
    return decoded


def _encode_fixed_private(value: bytes, size: int) -> str:
    raw = bytes(value)
    if len(raw) != size or not any(raw):
        raise ValueError("transport enrollment value has an invalid size")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _pairs_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TransportProtocolError("The transport process sent invalid data.")
        result[key] = value
    return result


def _strict_int(value: Any, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TransportProtocolError("The transport process sent invalid data.")
    if value < minimum or value > maximum:
        raise TransportProtocolError("The transport process sent invalid data.")
    return value


def parse_transport_event(encoded: bytes) -> TransportEvent:
    """Parse one complete event line without reflecting attacker-controlled data."""

    if not encoded or len(encoded) > MAX_EVENT_LINE_BYTES or not encoded.endswith(b"\n"):
        raise TransportProtocolError("The transport process sent invalid data.")
    if b"\x00" in encoded:
        raise TransportProtocolError("The transport process sent invalid data.")
    try:
        raw = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_pairs_without_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except TransportProtocolError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise TransportProtocolError(
            "The transport process sent invalid data."
        ) from exc
    if not isinstance(raw, dict) or set(raw) - _EVENT_FIELDS:
        raise TransportProtocolError("The transport process sent invalid data.")

    if _strict_int(raw.get("version"), minimum=0, maximum=2**31 - 1) != IPC_VERSION:
        raise TransportProtocolError("The transport process is not compatible.")
    event_id = _strict_int(raw.get("id"), minimum=0, maximum=2**63 - 1)
    event_type = raw.get("type")
    code = raw.get("code", "")
    state = raw.get("state", "")
    mode = raw.get("mode", "")
    profile_id = raw.get("profile_id", "")
    encoded_host_pin = raw.get("host_spki_sha256", "")
    build = raw.get("build", "")
    if not isinstance(event_type, str) or not isinstance(code, str):
        raise TransportProtocolError("The transport process sent invalid data.")
    if not isinstance(state, str) or not isinstance(mode, str):
        raise TransportProtocolError("The transport process sent invalid data.")
    if event_type not in _EVENT_TYPES or code not in _EVENT_CODES:
        raise TransportProtocolError("The transport process sent invalid data.")
    if state not in _EVENT_STATES or mode not in _EVENT_MODES:
        raise TransportProtocolError("The transport process sent invalid data.")
    if not isinstance(profile_id, str) or (
        profile_id and _PROFILE_PATTERN.fullmatch(profile_id) is None
    ):
        raise TransportProtocolError("The transport process sent invalid data.")
    if not isinstance(encoded_host_pin, str):
        raise TransportProtocolError("The transport process sent invalid data.")
    host_pin = (
        _decode_fixed_public(encoded_host_pin, 32) if encoded_host_pin else b""
    )
    if not isinstance(build, str) or (build and not _BUILD_PATTERN.fullmatch(build)):
        raise TransportProtocolError("The transport process sent invalid data.")
    generation = _strict_int(
        raw.get("generation", 0), minimum=0, maximum=2**32 - 1
    )
    loopback_port = _strict_int(
        raw.get("loopback_port", 0), minimum=0, maximum=65_535
    )

    if event_type == "ready":
        if (
            event_id != 0
            or code != "ok"
            or state != "idle"
            or not build
            or mode
            or profile_id
            or host_pin
            or generation
            or loopback_port
        ):
            raise TransportProtocolError("The transport process sent invalid data.")
    elif event_id == 0 and event_type not in {"peer_connected", "error", "stopped"}:
        raise TransportProtocolError("The transport process sent invalid data.")
    if event_type == "host_prepared" and (
        code != "ok"
        or state != "identity_ready"
        or not host_pin
        or mode
        or profile_id
        or generation
        or loopback_port
        or build
    ):
        raise TransportProtocolError("The transport process sent invalid data.")
    if event_type == "host_registered":
        if (
            code != "ok"
            or state != "host_waiting"
            or mode != "host"
            or not profile_id
            or generation == 0
            or loopback_port == 0
            or host_pin
            or event_id == 0
            or build
        ):
            raise TransportProtocolError("The transport process sent invalid data.")
    if event_type == "peer_connected":
        if (
            code != "ok"
            or state != "connected"
            or mode not in {"host", "guest"}
            or not profile_id
            or generation == 0
            or loopback_port == 0
            or host_pin
            or ((mode == "host") != (event_id == 0))
            or build
        ):
            raise TransportProtocolError("The transport process sent invalid data.")
    if event_type == "peer_closed" and (
        code != "ok"
        or state != "closed"
        or mode not in {"host", "guest"}
        or not profile_id
        or generation == 0
        or loopback_port
        or host_pin
        or build
    ):
        raise TransportProtocolError("The transport process sent invalid data.")
    if event_type == "hello":
        active = state in {"connecting", "host_waiting", "connected"}
        if (
            code != "ok"
            or not build
            or host_pin
            or (
                active
                and (
                    mode not in {"host", "guest"}
                    or not profile_id
                    or generation == 0
                    or loopback_port
                )
            )
            or (
                not active
                and (
                    state not in {"idle", "identity_ready", "closed"}
                    or mode
                    or profile_id
                    or generation
                    or loopback_port
                )
            )
        ):
            raise TransportProtocolError("The transport process sent invalid data.")
    if event_type == "stopped" and (
        code != "ok"
        or state != "stopped"
        or mode
        or profile_id
        or generation
        or loopback_port
        or host_pin
        or build
    ):
        raise TransportProtocolError("The transport process sent invalid data.")
    if event_type == "error" and (
        not code
        or code == "ok"
        or not state
        or state == "stopped"
        or mode
        or profile_id
        or generation
        or loopback_port
        or host_pin
        or build
    ):
        raise TransportProtocolError("The transport process sent invalid data.")

    return TransportEvent(
        event_id=event_id,
        event_type=event_type,
        code=code,
        state=state,
        mode=mode,
        generation=generation,
        loopback_port=loopback_port,
        profile_id=profile_id,
        host_spki_sha256=host_pin,
        build=build,
    )


def _validate_binary_architecture(path: Path, expected_machine: str) -> None:
    """Reject a sibling executable for the wrong packaged desktop target."""

    expected = str(expected_machine or "").strip().lower()
    if expected not in {"arm64", "x86_64"}:
        raise TransportLaunchError("The transport process is not installed safely.")
    try:
        with path.open("rb") as handle:
            header = handle.read(4096)
    except OSError as exc:
        raise TransportLaunchError(
            "The transport process is not installed safely."
        ) from exc
    if sys.platform == "darwin":
        if len(header) < 8 or header[:4] != b"\xcf\xfa\xed\xfe":
            raise TransportLaunchError("The transport process is not installed safely.")
        cpu_type = int.from_bytes(header[4:8], "little")
        wanted = 0x0100000C if expected == "arm64" else 0x01000007
        if cpu_type != wanted:
            raise TransportLaunchError("The transport process is not installed safely.")
    elif os.name == "nt":
        if len(header) < 64 or header[:2] != b"MZ":
            raise TransportLaunchError("The transport process is not installed safely.")
        pe_offset = int.from_bytes(header[60:64], "little")
        if pe_offset < 64 or pe_offset + 6 > len(header):
            raise TransportLaunchError("The transport process is not installed safely.")
        if header[pe_offset : pe_offset + 4] != b"PE\x00\x00":
            raise TransportLaunchError("The transport process is not installed safely.")
        machine = int.from_bytes(header[pe_offset + 4 : pe_offset + 6], "little")
        wanted = 0xAA64 if expected == "arm64" else 0x8664
        if machine != wanted:
            raise TransportLaunchError("The transport process is not installed safely.")


def _verify_platform_signature(path: Path) -> None:
    """Verify the executable's native signature in a frozen desktop build."""

    if sys.platform == "darwin":
        command = [
            "/usr/bin/codesign",
            "--verify",
            "--strict",
            "--verbose=2",
            str(path),
        ]
        environment: dict[str, str] = {}
    elif os.name == "nt":
        system_root = str(os.environ.get("SystemRoot", r"C:\Windows"))
        powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        command = [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "if ((Get-AuthenticodeSignature -LiteralPath $env:WEBJAM_VERIFY_BINARY).Status -eq 'Valid') { exit 0 } else { exit 1 }",
        ]
        environment = {
            "SystemRoot": system_root,
            "WEBJAM_VERIFY_BINARY": str(path),
        }
    else:
        return
    try:
        result = subprocess.run(
            command,
            cwd=str(path.parent),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TransportLaunchError(
            "The transport process is not installed safely."
        ) from exc
    if result.returncode != 0:
        raise TransportLaunchError("The transport process is not installed safely.")


def _validated_binary(
    path: str | Path,
    *,
    expected_sha256: str | None = None,
    expected_machine: str | None = None,
    require_platform_signature: bool = False,
) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise TransportLaunchError("The transport process is not installed safely.")
    try:
        if candidate.is_symlink():
            raise TransportLaunchError(
                "The transport process is not installed safely."
            )
        resolved = candidate.resolve(strict=True)
        details = resolved.stat()
    except TransportLaunchError:
        raise
    except OSError as exc:
        raise TransportLaunchError(
            "The transport process is not installed safely."
        ) from exc
    if not stat.S_ISREG(details.st_mode):
        raise TransportLaunchError("The transport process is not installed safely.")
    if details.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise TransportLaunchError("The transport process is not installed safely.")
    if os.name != "nt" and hasattr(os, "getuid") and details.st_uid not in {
        os.getuid(),
        0,
    }:
        raise TransportLaunchError("The transport process is not installed safely.")
    if os.name != "nt" and not os.access(resolved, os.X_OK):
        raise TransportLaunchError("The transport process is not installed safely.")
    if expected_sha256 is not None:
        digest = str(expected_sha256).lower()
        if _SHA256_PATTERN.fullmatch(digest) is None or details.st_size > 128 * 1024 * 1024:
            raise TransportLaunchError("The transport process is not installed safely.")
        hasher = hashlib.sha256()
        try:
            with resolved.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    hasher.update(chunk)
        except OSError as exc:
            raise TransportLaunchError(
                "The transport process is not installed safely."
            ) from exc
        if not secrets_compare(hasher.hexdigest(), digest):
            raise TransportLaunchError("The transport process is not installed safely.")
    if expected_machine is not None:
        _validate_binary_architecture(resolved, expected_machine)
    if require_platform_signature:
        _verify_platform_signature(resolved)
    return resolved


def secrets_compare(left: str, right: str) -> bool:
    """Constant-time compare without retaining binary contents."""

    import hmac

    return hmac.compare_digest(left, right)


class TransportProcess:
    """Own exactly one constant-argv transport process and its bounded IPC."""

    def __init__(
        self,
        binary: str | Path,
        *,
        expected_build: str,
        start_timeout: float = DEFAULT_START_TIMEOUT_SECONDS,
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
        on_event: Callable[[TransportEvent], None] | None = None,
        expected_sha256: str | None = None,
        expected_machine: str | None = None,
        require_platform_signature: bool = False,
    ) -> None:
        if not _BUILD_PATTERN.fullmatch(str(expected_build or "")):
            raise ValueError("expected_build is not valid")
        for timeout in (start_timeout, command_timeout, stop_timeout):
            if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
                raise ValueError("transport timeouts must be numeric")
            if timeout <= 0 or timeout > 60:
                raise ValueError("transport timeouts must be between 0 and 60 seconds")
        self._binary_input = Path(binary)
        self._expected_build = str(expected_build)
        self._start_timeout = float(start_timeout)
        self._command_timeout = float(command_timeout)
        self._stop_timeout = float(stop_timeout)
        if on_event is not None and not callable(on_event):
            raise TypeError("on_event must be callable")
        self._on_event = on_event
        if expected_sha256 is not None and _SHA256_PATTERN.fullmatch(
            str(expected_sha256).lower()
        ) is None:
            raise ValueError("expected_sha256 is not valid")
        self._expected_sha256 = (
            str(expected_sha256).lower() if expected_sha256 is not None else None
        )
        self._expected_machine = expected_machine
        self._require_platform_signature = bool(require_platform_signature)
        self._process: subprocess.Popen[bytes] | None = None
        self._reader: threading.Thread | None = None
        self._condition = threading.Condition(threading.RLock())
        self._write_lock = threading.Lock()
        self._next_id = 1
        self._pending: dict[int, TransportEvent] = {}
        self._waiting: set[int] = set()
        self._ready: TransportEvent | None = None
        self._prepared_host_pin: bytes | None = None
        self._failure: TransportProcessError | None = None
        self._timeline: deque[TransportEvent] = deque(maxlen=MAX_TIMELINE_EVENTS)

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None and self._failure is None

    @property
    def process_id(self) -> int | None:
        process = self._process
        return process.pid if process is not None and process.poll() is None else None

    @property
    def timeline(self) -> tuple[TransportEvent, ...]:
        with self._condition:
            return tuple(self._timeline)

    def start(self) -> TransportEvent:
        if self._process is not None:
            raise TransportLaunchError("The transport process is already started.")
        binary = _validated_binary(
            self._binary_input,
            expected_sha256=self._expected_sha256,
            expected_machine=self._expected_machine,
            require_platform_signature=self._require_platform_signature,
        )
        popen_options: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "cwd": str(binary.parent),
            "env": {},
            "shell": False,
            "close_fds": True,
            "bufsize": 0,
        }
        if os.name == "nt":
            popen_options["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            popen_options["start_new_session"] = True
        try:
            process = subprocess.Popen([str(binary)], **popen_options)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise TransportLaunchError(
                "WebJam could not start its secure transport process."
            ) from exc
        if process.stdin is None or process.stdout is None:
            self._terminate_process(process)
            raise TransportLaunchError(
                "WebJam could not start its secure transport process."
            )
        self._process = process
        self._reader = threading.Thread(
            target=self._read_events,
            args=(process, process.stdout),
            name="webjam-transport-events",
            daemon=True,
        )
        self._reader.start()
        try:
            ready = self._wait_ready(self._start_timeout)
            if ready.build != self._expected_build:
                raise TransportLaunchError(
                    "The transport process does not match this WebJam build."
                )
            return ready
        except BaseException:
            self._force_stop()
            raise

    def hello(self) -> TransportEvent:
        return self._request("hello")

    def prepare_host(self) -> bytes:
        """Create one ephemeral host identity inside the sidecar.

        Only the public SPKI SHA-256 pin leaves the process.  Calling this a
        second time is a local lifecycle error even before the sidecar's own
        fail-closed state machine rejects it.
        """

        if self._prepared_host_pin is not None:
            raise TransportProcessError("The host identity is already prepared.")
        event = self._request("prepare_host")
        if event.event_type != "host_prepared" or not event.host_spki_sha256:
            self._record_failure(
                TransportProtocolError("The transport process sent invalid data.")
            )
            raise TransportProtocolError("The transport process sent invalid data.")
        self._prepared_host_pin = event.host_spki_sha256
        return event.host_spki_sha256

    def open_host(
        self,
        invitation: RemoteInvitation,
        *,
        target_port: int,
        generation: int,
    ) -> TransportEvent:
        invitation = self._invitation(invitation)
        port = self._command_int(target_port, minimum=1, maximum=65_535)
        current_generation = self._command_int(
            generation, minimum=1, maximum=2**32 - 1
        )
        if self._prepared_host_pin is None:
            raise TransportProcessError("Prepare the host identity first.")
        if invitation.host_spki_sha256 != self._prepared_host_pin:
            raise TransportProcessError(
                "The invitation does not match this host identity."
            )
        event = self._request_enrollment(
            invitation,
            mode="host",
            generation=current_generation,
            target_port=port,
        )
        if event.event_type != "host_registered":
            self._record_failure(
                TransportProtocolError("The transport process sent invalid data.")
            )
            raise TransportProtocolError("The transport process sent invalid data.")
        return event

    def open_guest(
        self,
        invitation: RemoteInvitation,
        *,
        generation: int,
    ) -> TransportEvent:
        invitation = self._invitation(invitation)
        current_generation = self._command_int(
            generation, minimum=1, maximum=2**32 - 1
        )
        event = self._request_enrollment(
            invitation,
            mode="guest",
            generation=current_generation,
        )
        if event.event_type != "peer_connected":
            self._record_failure(
                TransportProtocolError("The transport process sent invalid data.")
            )
            raise TransportProtocolError("The transport process sent invalid data.")
        return event

    def close_peer(self) -> TransportEvent:
        return self._request("close_peer")

    def stop(self) -> None:
        process = self._process
        if process is None:
            self._prepared_host_pin = None
            return
        if process.poll() is None and self._failure is None:
            try:
                self._request("shutdown", timeout=self._stop_timeout)
            except TransportProcessError:
                pass
        self._force_stop()
        self._prepared_host_pin = None

    def __enter__(self) -> "TransportProcess":
        self.start()
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.stop()

    @staticmethod
    def _command_int(value: Any, *, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("transport command values must be integers")
        if value < minimum or value > maximum:
            raise ValueError("transport command value is out of range")
        return value

    @staticmethod
    def _invitation(invitation: RemoteInvitation) -> RemoteInvitation:
        if not isinstance(invitation, RemoteInvitation):
            raise TypeError("invitation must be a RemoteInvitation")
        if invitation.advisory_expired():
            raise TransportProcessError("The remote invitation expired.")
        return invitation

    def _request_enrollment(
        self,
        invitation: RemoteInvitation,
        *,
        mode: str,
        generation: int,
        target_port: int = 0,
    ) -> TransportEvent:
        fields: dict[str, Any] = {
            "mode": mode,
            "generation": generation,
            "profile_id": invitation.profile_id,
            "session_reference": _encode_fixed_private(
                invitation.session_reference, 16
            ),
            "invite_reference": _encode_fixed_private(
                invitation.invite_reference, 16
            ),
            "enrollment_capability": _encode_fixed_private(
                invitation.capability_for_enrollment(), 32
            ),
            "expires_at_unix": invitation.expires_at_unix,
        }
        if mode == "host":
            fields["target_port"] = target_port
        elif mode == "guest":
            fields["host_spki_sha256"] = _encode_fixed_private(
                invitation.host_spki_sha256, 32
            )
        else:
            raise ValueError("transport mode is invalid")
        try:
            return self._request("open_peer", **fields)
        finally:
            # Drop every private spelling immediately after the bounded write
            # and response. Strings cannot be zeroed in CPython, so never
            # retain them on the process, event, exception, or timeline.
            fields.clear()

    def _request(
        self, command_type: str, *, timeout: float | None = None, **fields: Any
    ) -> TransportEvent:
        process = self._process
        if process is None or process.poll() is not None:
            raise TransportProcessError("The transport process is not running.")
        with self._condition:
            if self._failure is not None:
                raise self._failure
            command_id = self._next_id
            self._next_id += 1
            self._waiting.add(command_id)
        command: dict[str, Any] = {
            "version": IPC_VERSION,
            "id": command_id,
            "type": command_type,
        }
        command.update(fields)
        encoded = json.dumps(
            command, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii") + b"\n"
        if len(encoded) > MAX_IPC_LINE_BYTES:
            with self._condition:
                self._waiting.discard(command_id)
            raise TransportProtocolError("The transport command exceeded its limit.")
        try:
            with self._write_lock:
                stdin = process.stdin
                if stdin is None:
                    raise BrokenPipeError
                stdin.write(encoded)
                stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            with self._condition:
                self._waiting.discard(command_id)
            self._record_failure(
                TransportProcessError("The transport process stopped unexpectedly.")
            )
            raise TransportProcessError(
                "The transport process stopped unexpectedly."
            ) from exc
        return self._wait_response(
            command_id,
            self._command_timeout if timeout is None else float(timeout),
        )

    def _wait_ready(self, timeout: float) -> TransportEvent:
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._ready is None and self._failure is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportTimeoutError(
                        "The transport process did not become ready in time."
                    )
                self._condition.wait(remaining)
            if self._failure is not None:
                raise self._failure
            assert self._ready is not None
            return self._ready

    def _wait_response(self, command_id: int, timeout: float) -> TransportEvent:
        deadline = time.monotonic() + timeout
        with self._condition:
            while command_id not in self._pending and self._failure is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._waiting.discard(command_id)
                    self._record_failure_locked(
                        TransportTimeoutError(
                            "The transport process did not respond in time."
                        )
                    )
                    raise self._failure
                self._condition.wait(remaining)
            self._waiting.discard(command_id)
            if self._failure is not None:
                raise self._failure
            event = self._pending.pop(command_id)
        if event.event_type == "error":
            raise TransportProcessError("The secure transport could not continue.")
        return event

    def _read_events(self, process: subprocess.Popen[bytes], stdout: BinaryIO) -> None:
        try:
            while True:
                line = stdout.readline(MAX_EVENT_LINE_BYTES + 1)
                if not line:
                    if process.poll() is None:
                        raise TransportProtocolError(
                            "The transport process closed its event channel."
                        )
                    break
                event = parse_transport_event(line)
                with self._condition:
                    if event.event_type == "ready":
                        if self._ready is not None or self._timeline:
                            raise TransportProtocolError(
                                "The transport process sent invalid data."
                            )
                        self._ready = event
                    elif event.event_id > 0:
                        if (
                            event.event_id not in self._waiting
                            or event.event_id in self._pending
                        ):
                            raise TransportProtocolError(
                                "The transport process sent an unexpected response."
                            )
                        self._pending[event.event_id] = event
                    self._timeline.append(event)
                    self._condition.notify_all()
                callback = self._on_event
                if callback is not None:
                    try:
                        callback(event)
                    except Exception:
                        # Event observers receive only immutable allowlisted
                        # facts and cannot own the reader or child lifecycle.
                        pass
        except TransportProcessError as exc:
            self._record_failure(exc)
            self._terminate_process(process)
        except (OSError, ValueError):
            self._record_failure(
                TransportProtocolError("The transport process event channel failed.")
            )
            self._terminate_process(process)
        finally:
            if process.poll() is not None:
                with self._condition:
                    if self._failure is None and not any(
                        event.event_type == "stopped" for event in self._timeline
                    ):
                        self._record_failure_locked(
                            TransportProcessError(
                                "The transport process stopped unexpectedly."
                            )
                        )
                    self._condition.notify_all()

    def _record_failure(self, failure: TransportProcessError) -> None:
        with self._condition:
            self._record_failure_locked(failure)

    def _record_failure_locked(self, failure: TransportProcessError) -> None:
        if self._failure is None:
            self._failure = failure
        self._condition.notify_all()

    def _force_stop(self) -> None:
        process = self._process
        if process is None:
            self._prepared_host_pin = None
            return
        with self._condition:
            if self._waiting:
                self._record_failure_locked(
                    TransportProcessError(
                        "The transport process stopped before the operation completed."
                    )
                )
        self._terminate_process(process)
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=self._stop_timeout)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        self._reader = None
        self._process = None
        self._prepared_host_pin = None
        with self._condition:
            self._waiting.clear()
            self._pending.clear()
            self._condition.notify_all()

    def _terminate_process(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=self._stop_timeout)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=self._stop_timeout)
        except (OSError, subprocess.TimeoutExpired):
            pass


def bundled_transport_binary() -> Path | None:
    """Locate the sidecar from a frozen app without consulting PATH or env."""

    if not getattr(sys, "frozen", False):
        return None
    try:
        executable = Path(sys.executable).resolve(strict=True)
    except OSError:
        return None
    if sys.platform == "darwin":
        candidate = executable.parent / "webjam-fabric"
    elif sys.platform == "win32":
        candidate = executable.parent / "webjam-fabric.exe"
    else:
        candidate = executable.parent / "webjam-fabric"
    try:
        return candidate if candidate.is_file() and not candidate.is_symlink() else None
    except OSError:
        return None
