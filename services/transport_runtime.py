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
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import unicodedata
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from core.remote_invitation import RemoteInvitation
from core.room_state import RoomState

IPC_VERSION = 1
MAX_IPC_LINE_BYTES = 64 * 1024
MAX_EVENT_LINE_BYTES = 4 * 1024
MAX_ROOM_EVENT_LINE_BYTES = 12 * 1024
MAX_TIMELINE_EVENTS = 64
MAX_HELP_TEXT_BYTES = 500
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
        "request_id",
        "text",
        "room_state",
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
        "help_accepted",
        "help_received",
        "help_delivered",
        "room_state_accepted",
        "room_state_received",
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
        "help_not_ready",
        "help_invalid",
        "help_rate_limited",
        "help_queue_full",
        "room_state_not_ready",
        "room_state_invalid",
        "room_state_rate_limited",
        "peer_protocol_unsupported",
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


class TransportPeerProtocolError(TransportProcessError):
    """The authenticated peer needs a compatible room protocol."""


class TransportRoomRateLimitedError(TransportProcessError):
    """A matched room publish may retry its newest snapshot after a short delay."""


class TransportLaunchError(TransportProcessError):
    """The packaged sidecar could not be launched or identified safely."""


class TransportProtocolError(TransportProcessError):
    """The sidecar violated its bounded, allowlisted IPC contract."""


class TransportTimeoutError(TransportProcessError):
    """The sidecar did not complete a bounded lifecycle operation."""


@dataclass(frozen=True)
class TransportEvent:
    """One allowlisted sidecar event.

    Bounded help and typed room payloads are intentionally omitted from
    representations and the process diagnostic timeline.
    """

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
    request_id: int = 0
    help_text: str = field(default="", repr=False)
    room_state: RoomState | None = field(default=None, repr=False)


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


def _normalize_help_text(value: Any, *, require_canonical: bool = False) -> str:
    """Return the sole bounded plain-text spelling accepted by the sidecar."""

    if not isinstance(value, str):
        raise TransportProtocolError("The transport process sent invalid data.")
    text = unicodedata.normalize("NFC", value)
    if require_canonical and text != value:
        raise TransportProtocolError("The transport process sent invalid data.")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TransportProtocolError(
            "The transport process sent invalid data."
        ) from exc
    if not text or not text.strip() or len(encoded) > MAX_HELP_TEXT_BYTES:
        raise TransportProtocolError("The transport process sent invalid data.")
    for character in text:
        if character in "<>\n\r\t" or unicodedata.category(character).startswith("C"):
            raise TransportProtocolError("The transport process sent invalid data.")
    return text


def parse_transport_event(encoded: bytes) -> TransportEvent:
    """Parse one complete event line without reflecting attacker-controlled data."""

    if not encoded or len(encoded) > MAX_ROOM_EVENT_LINE_BYTES or not encoded.endswith(b"\n"):
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
    if len(encoded) > MAX_EVENT_LINE_BYTES and event_type not in {
        "room_state_received", "room_state_accepted"
    }:
        raise TransportProtocolError("The transport process sent invalid data.")
    room_state = None
    if "room_state" in raw:
        if event_type != "room_state_received":
            raise TransportProtocolError("The transport process sent invalid data.")
        try:
            room_state = RoomState.from_mapping(raw["room_state"])
        except ValueError:
            raise TransportProtocolError("The transport process sent invalid data.") from None
    code = raw.get("code", "")
    state = raw.get("state", "")
    mode = raw.get("mode", "")
    profile_id = raw.get("profile_id", "")
    encoded_host_pin = raw.get("host_spki_sha256", "")
    build = raw.get("build", "")
    request_id = _strict_int(
        raw.get("request_id", 0), minimum=0, maximum=2**64 - 1
    )
    help_text = raw.get("text", "")
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
    if not isinstance(help_text, str):
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
    elif event_id == 0 and event_type not in {
        "peer_connected",
        "help_received",
        "help_delivered",
        "room_state_received",
        "error",
        "stopped",
    }:
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
    if event_type == "host_registered" and (
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
    if event_type == "peer_connected" and (
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
    if event_type in {"help_accepted", "help_received", "help_delivered"}:
        if (
            code != "ok"
            or state != "connected"
            or mode not in {"host", "guest"}
            or not profile_id
            or generation == 0
            or loopback_port
            or host_pin
            or build
            or request_id == 0
        ):
            raise TransportProtocolError("The transport process sent invalid data.")
        if event_type == "help_accepted":
            if event_id == 0 or request_id != event_id or help_text:
                raise TransportProtocolError("The transport process sent invalid data.")
        elif event_type == "help_received":
            if event_id != 0:
                raise TransportProtocolError("The transport process sent invalid data.")
            help_text = _normalize_help_text(help_text, require_canonical=True)
        elif event_id != 0 or help_text:
            raise TransportProtocolError("The transport process sent invalid data.")
    if event_type in {"room_state_accepted", "room_state_received"}:
        required = {"version", "id", "type", "code", "state", "mode",
                    "profile_id", "generation"}
        required.add("request_id" if event_type == "room_state_accepted" else "room_state")
        if (set(raw) != required or code != "ok" or state != "connected"
                or not profile_id or generation == 0):
            raise TransportProtocolError("The transport process sent invalid data.")
        if event_type == "room_state_accepted":
            if event_id == 0 or request_id != event_id or mode != "host":
                raise TransportProtocolError("The transport process sent invalid data.")
        elif event_id != 0 or mode != "guest" or room_state is None:
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
    if event_type not in {"help_accepted", "help_received", "help_delivered", "room_state_accepted"} and (
        request_id or help_text
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
        request_id=request_id,
        help_text=help_text,
        room_state=room_state,
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
    elif sys.platform.startswith("linux"):
        # ELF64 e_machine is a stable native target assertion. Linux has no
        # universal executable equivalent, so accepting a different machine
        # here would defer a packaging error until process launch.
        if (
            len(header) < 20
            or header[:4] != b"\x7fELF"
            or header[4] != 2  # ELFCLASS64
            or header[5] != 1  # ELFDATA2LSB
        ):
            raise TransportLaunchError("The transport process is not installed safely.")
        machine = int.from_bytes(header[18:20], "little")
        wanted = 0xB7 if expected == "arm64" else 0x3E
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
        self._lifecycle_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._start_in_progress = False
        self._stop_requested = False
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
        with self._condition:
            if self._process is not None or self._start_in_progress:
                raise TransportLaunchError(
                    "The transport process is already started."
                )
            if self._stop_requested:
                raise TransportLaunchError("The transport process was stopped.")
            self._start_in_progress = True
        try:
            binary = _validated_binary(
                self._binary_input,
                expected_sha256=self._expected_sha256,
                expected_machine=self._expected_machine,
                require_platform_signature=self._require_platform_signature,
            )
            with self._condition:
                if self._stop_requested:
                    raise self._stopped_error_locked()
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
            with self._lifecycle_lock:
                with self._condition:
                    if self._stop_requested:
                        cancelled = self._stopped_error_locked()
                    else:
                        cancelled = None
                        self._process = process
                        self._reader = threading.Thread(
                            target=self._read_events,
                            args=(process, process.stdout),
                            name="webjam-transport-events",
                            daemon=True,
                        )
                        reader = self._reader
                if cancelled is not None:
                    self._terminate_process(process)
                    self._close_process_streams(process)
                    raise cancelled
                assert reader is not None
                reader.start()
            ready = self._wait_ready(self._start_timeout)
            if ready.build != self._expected_build:
                raise TransportLaunchError(
                    "The transport process does not match this WebJam build."
                )
            with self._condition:
                if self._stop_requested:
                    raise self._stopped_error_locked()
                self._start_in_progress = False
                self._condition.notify_all()
            return ready
        except BaseException:
            self._force_stop()
            with self._condition:
                self._start_in_progress = False
                self._condition.notify_all()
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
        if (event.event_type != "host_registered" or event.mode != "host"
                or event.generation != current_generation
                or event.profile_id != invitation.profile_id):
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
        if (event.event_type != "peer_connected" or event.mode != "guest"
                or event.generation != current_generation
                or event.profile_id != invitation.profile_id):
            self._record_failure(
                TransportProtocolError("The transport process sent invalid data.")
            )
            raise TransportProtocolError("The transport process sent invalid data.")
        return event

    def close_peer(self) -> TransportEvent:
        return self._request("close_peer")

    def send_help(self, text: str, *, generation: int) -> TransportEvent:
        """Send one ephemeral help message after authenticated peer proof."""

        try:
            normalized = _normalize_help_text(text)
        except TransportProtocolError as exc:
            raise ValueError("help text must be bounded plain text") from exc
        current_generation = self._command_int(
            generation, minimum=1, maximum=2**32 - 1
        )
        event = self._request(
            "send_help", generation=current_generation, text=normalized
        )
        if event.event_type != "help_accepted":
            self._record_failure(
                TransportProtocolError("The transport process sent invalid data.")
            )
            raise TransportProtocolError("The transport process sent invalid data.")
        return event

    def publish_room_state(self, state: RoomState, *, generation: int) -> TransportEvent:
        """Publish a typed full room snapshot through the authenticated host peer."""

        if type(state) is not RoomState:
            raise ValueError("room state must be typed")
        current_generation = self._command_int(generation, minimum=1, maximum=2**32 - 1)
        event = self._request("publish_room_state", generation=current_generation,
                              room_state=state.to_mapping())
        if (event.event_type != "room_state_accepted" or event.mode != "host"
                or event.generation != generation):
            self._record_failure(TransportProtocolError("The transport process sent invalid data."))
            raise TransportProtocolError("The transport process sent invalid data.")
        return event

    def stop(self) -> None:
        with self._condition:
            self._stop_requested = True
            starting = self._start_in_progress
            process = self._process
            if starting:
                self._record_failure_locked(self._stopped_error_locked())
        if process is None:
            if starting:
                deadline = time.monotonic() + self._stop_timeout
                with self._condition:
                    while self._start_in_progress:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._condition.wait(remaining)
            self._prepared_host_pin = None
            return
        if not starting and process.poll() is None and self._failure is None:
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
            if self._stop_requested and command_type != "shutdown":
                raise self._stopped_error_locked()
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
            command, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8") + b"\n"
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
            command_type=command_type,
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

    def _wait_response(
        self, command_id: int, timeout: float, *, command_type: str = ""
    ) -> TransportEvent:
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
            if (command_type == "publish_room_state"
                    and event.code == "room_state_rate_limited"
                    and event.state == "connected"):
                raise TransportRoomRateLimitedError("The room update needs a brief retry.")
            if event.code == "peer_protocol_unsupported":
                raise TransportPeerProtocolError("The peer needs a compatible WebJam version.")
            raise TransportProcessError("The secure transport could not continue.")
        return event

    def _read_events(self, process: subprocess.Popen[bytes], stdout: BinaryIO) -> None:
        try:
            while True:
                line = stdout.readline(MAX_ROOM_EVENT_LINE_BYTES + 1)
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
                    if not event.event_type.startswith(("help_", "room_state_")):
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

            with self._condition:
                failed = self._failure is not None and not self._stop_requested
            if failed and self._on_event is not None:
                try:
                    self._on_event(TransportEvent(
                        event_id=0, event_type="error", code="protocol_violation", state="failed"
                    ))
                except Exception:
                    pass

    def _record_failure(self, failure: TransportProcessError) -> None:
        with self._condition:
            self._record_failure_locked(failure)

    def _record_failure_locked(self, failure: TransportProcessError) -> None:
        if self._failure is None:
            self._failure = failure
        self._condition.notify_all()

    def _stopped_error_locked(self) -> TransportProcessError:
        return self._failure or TransportProcessError(
            "The transport process was stopped."
        )

    def _force_stop(self) -> None:
        with self._lifecycle_lock:
            process = self._process
            if process is None:
                self._prepared_host_pin = None
                return
            reader = self._reader
            with self._condition:
                if self._waiting:
                    self._record_failure_locked(
                        TransportProcessError(
                            "The transport process stopped before the operation "
                            "completed."
                        )
                    )
            self._terminate_process(process)
            self._process = None
            self._reader = None
            if reader is not None and reader is not threading.current_thread():
                reader.join(timeout=self._stop_timeout)
            self._close_process_streams(process)
            self._prepared_host_pin = None
            with self._condition:
                self._waiting.clear()
                self._pending.clear()
                self._condition.notify_all()

    @staticmethod
    def _close_process_streams(process: subprocess.Popen[bytes]) -> None:
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

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

        if process.poll() is None:
            raise TransportProcessError("The transport process did not stop.")


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
