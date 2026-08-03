"""Address-free proof of UDP sockets owned by one exact local process.

Jamulus 3.12.x treats the client ``--port`` value as an allocation base, not
as the bound port: it starts at ``base + rand() % 100`` and can retry another
100 ports.  Code which needs to correlate a private Jamulus child with an
authenticated server roster must therefore inspect the child's real socket.

This module returns port numbers only.  It never returns, logs, or embeds local
addresses, command output, process paths, or socket-table text.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Callable


_MAX_FDS = 4_096
_MAX_SOCKET_TABLE_BYTES = 1_048_576
_MAX_LSOF_OUTPUT_BYTES = 65_536
_MAX_UDP_PORTS = 128
_JAMULUS_RANDOM_START_OFFSETS = 100
_JAMULUS_BIND_RETRIES = 100
JAMULUS_CLIENT_MAX_BASE_PORT = 65_535 - (
    _JAMULUS_RANDOM_START_OFFSETS - 1 + _JAMULUS_BIND_RETRIES
)


class ProcessSocketIdentityError(RuntimeError):
    """Raised when exact process/socket ownership cannot be proved."""


def _validated_process_id(process_id: int) -> int:
    if isinstance(process_id, bool):
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")
    try:
        value = int(process_id)
    except (TypeError, ValueError) as exc:
        raise ProcessSocketIdentityError(
            "Process socket identity is unavailable."
        ) from exc
    if value <= 0:
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")
    return value


def _bounded_text(path: Path, limit: int) -> str:
    try:
        with path.open("r", encoding="ascii", errors="strict") as handle:
            value = handle.read(limit + 1)
    except (OSError, UnicodeError) as exc:
        raise ProcessSocketIdentityError(
            "Process socket identity is unavailable."
        ) from exc
    if len(value) > limit:
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")
    return value


def _linux_socket_inodes(process_root: Path) -> frozenset[int]:
    try:
        entries = tuple((process_root / "fd").iterdir())
    except OSError as exc:
        raise ProcessSocketIdentityError(
            "Process socket identity is unavailable."
        ) from exc
    if len(entries) > _MAX_FDS:
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")
    inodes: set[int] = set()
    for entry in entries:
        try:
            target = os.readlink(entry)
        except FileNotFoundError:
            # Descriptor churn is caught by the second complete snapshot.
            continue
        except OSError as exc:
            raise ProcessSocketIdentityError(
                "Process socket identity is unavailable."
            ) from exc
        match = re.fullmatch(r"socket:\[(\d+)\]", target)
        if match is not None:
            inodes.add(int(match.group(1)))
    return frozenset(inodes)


def _linux_udp_table_ports(
    table: str,
    *,
    owned_inodes: frozenset[int],
) -> set[int]:
    lines = table.splitlines()
    if not lines or "local_address" not in lines[0] or "inode" not in lines[0]:
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")
    ports: set[int] = set()
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 10:
            raise ProcessSocketIdentityError(
                "Process socket identity is unavailable."
            )
        local_address = fields[1]
        inode_text = fields[9]
        if not inode_text.isdigit():
            raise ProcessSocketIdentityError(
                "Process socket identity is unavailable."
            )
        inode = int(inode_text)
        if inode not in owned_inodes:
            continue
        _address, separator, port_hex = local_address.rpartition(":")
        if separator != ":" or re.fullmatch(r"[0-9A-Fa-f]{4}", port_hex) is None:
            raise ProcessSocketIdentityError(
                "Process socket identity is unavailable."
            )
        port = int(port_hex, 16)
        if not 1 <= port <= 65_535:
            raise ProcessSocketIdentityError(
                "Process socket identity is unavailable."
            )
        ports.add(port)
    return ports


def _linux_process_udp_ports(process_id: int, proc_root: Path) -> tuple[int, ...]:
    process_root = proc_root / str(process_id)
    before = _linux_socket_inodes(process_root)
    ports: set[int] = set()
    for table_name in ("udp", "udp6"):
        table = _bounded_text(
            process_root / "net" / table_name,
            _MAX_SOCKET_TABLE_BYTES,
        )
        ports.update(_linux_udp_table_ports(table, owned_inodes=before))
    after = _linux_socket_inodes(process_root)
    if before != after or len(ports) > _MAX_UDP_PORTS:
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")
    return tuple(sorted(ports))


def _default_lsof_path() -> Path:
    for candidate in (Path("/usr/sbin/lsof"), Path("/usr/bin/lsof")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    raise ProcessSocketIdentityError("Process socket identity is unavailable.")


def _macos_process_udp_ports(
    process_id: int,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    lsof_path: Path | None,
) -> tuple[int, ...]:
    executable = _default_lsof_path() if lsof_path is None else Path(lsof_path)
    if (
        not executable.is_absolute()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")
    try:
        result = runner(
            [
                str(executable),
                "-nP",
                "-a",
                "-p",
                str(process_id),
                "-iUDP",
                "-Fpn",
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
            shell=False,
            cwd="/",
            env={
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    except (OSError, subprocess.SubprocessError, TypeError) as exc:
        raise ProcessSocketIdentityError(
            "Process socket identity is unavailable."
        ) from exc
    output = getattr(result, "stdout", None)
    returncode = getattr(result, "returncode", None)
    if not isinstance(output, str) or not isinstance(returncode, int):
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")
    if len(output.encode("utf-8", errors="replace")) > _MAX_LSOF_OUTPUT_BYTES:
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")
    if returncode != 0:
        # lsof uses 1 when the exact process has no matching UDP descriptor.
        # Partial output or any other status is not a trustworthy snapshot.
        if returncode == 1 and not output.strip():
            return ()
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")

    seen_process = False
    ports: set[int] = set()
    for line in output.splitlines():
        if not line:
            continue
        field = line[0]
        value = line[1:]
        if field == "p":
            if not value.isdigit() or int(value) != process_id:
                raise ProcessSocketIdentityError(
                    "Process socket identity is unavailable."
                )
            seen_process = True
        elif field == "n":
            local = value.split("->", 1)[0]
            _host, separator, port_text = local.rpartition(":")
            if separator != ":" or not port_text.isdigit():
                raise ProcessSocketIdentityError(
                    "Process socket identity is unavailable."
                )
            port = int(port_text)
            if not 1 <= port <= 65_535:
                raise ProcessSocketIdentityError(
                    "Process socket identity is unavailable."
                )
            ports.add(port)
    if ports and not seen_process:
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")
    if len(ports) > _MAX_UDP_PORTS:
        raise ProcessSocketIdentityError("Process socket identity is unavailable.")
    return tuple(sorted(ports))


def process_owned_udp_ports(
    process_id: int,
    *,
    platform_name: str | None = None,
    proc_root: Path = Path("/proc"),
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    lsof_path: Path | None = None,
) -> tuple[int, ...]:
    """Return deduplicated UDP ports proved to belong to one process.

    Linux joins the exact process's descriptor inodes to its kernel UDP
    tables.  macOS asks the absolute system ``lsof`` binary for that exact PID
    with bounded output and timeout.  Unsupported or ambiguous inspection
    fails closed.
    """

    pid = _validated_process_id(process_id)
    platform_value = str(platform_name or sys.platform).lower()
    if platform_value.startswith("linux"):
        return _linux_process_udp_ports(pid, Path(proc_root))
    if platform_value.startswith("darwin"):
        return _macos_process_udp_ports(
            pid,
            runner=runner,
            lsof_path=lsof_path,
        )
    raise ProcessSocketIdentityError("Process socket identity is unavailable.")


def exact_jamulus_client_udp_port(
    process_id: int,
    requested_port: int,
    *,
    platform_name: str | None = None,
    proc_root: Path = Path("/proc"),
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    lsof_path: Path | None = None,
) -> int:
    """Prove the one Jamulus 3.12.x UDP port owned by ``process_id``.

    The candidate interval mirrors the pinned upstream allocation algorithm.
    A missing socket, two candidates, an out-of-range socket, or an inspection
    failure is never replaced with the configured base port.
    """

    if isinstance(requested_port, bool):
        raise ProcessSocketIdentityError("Jamulus socket identity is unavailable.")
    try:
        base = int(requested_port)
    except (TypeError, ValueError) as exc:
        raise ProcessSocketIdentityError(
            "Jamulus socket identity is unavailable."
        ) from exc
    max_offset = _JAMULUS_RANDOM_START_OFFSETS - 1 + _JAMULUS_BIND_RETRIES
    if not 1 <= base <= JAMULUS_CLIENT_MAX_BASE_PORT:
        raise ProcessSocketIdentityError("Jamulus socket identity is unavailable.")
    owned = process_owned_udp_ports(
        process_id,
        platform_name=platform_name,
        proc_root=proc_root,
        runner=runner,
        lsof_path=lsof_path,
    )
    candidates = tuple(port for port in owned if base <= port <= base + max_offset)
    if len(candidates) != 1:
        raise ProcessSocketIdentityError("Jamulus socket identity is unavailable.")
    return candidates[0]


__all__ = [
    "JAMULUS_CLIENT_MAX_BASE_PORT",
    "ProcessSocketIdentityError",
    "exact_jamulus_client_udp_port",
    "process_owned_udp_ports",
]
