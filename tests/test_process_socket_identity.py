"""Exact, address-free process UDP ownership tests."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from core.process_socket_identity import (
    ProcessSocketIdentityError,
    exact_jamulus_client_udp_port,
    process_owned_udp_ports,
)


_PROC_HEADER = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when "
    "retrnsmt   uid  timeout inode\n"
)


def _proc_udp_row(port: int, inode: int) -> str:
    return (
        f"   5: 0100007F:{port:04X} 00000000:0000 07 "
        "00000000:00000000 00:00000000 00000000  1000        0 "
        f"{inode} 2 0000000000000000 0\n"
    )


def _fake_proc(
    root: Path,
    *,
    process_id: int,
    sockets: tuple[tuple[int, int], ...],
    foreign: tuple[tuple[int, int], ...] = (),
) -> None:
    process_root = root / str(process_id)
    fd_root = process_root / "fd"
    net_root = process_root / "net"
    fd_root.mkdir(parents=True)
    net_root.mkdir()
    for index, (_port, inode) in enumerate(sockets, start=3):
        (fd_root / str(index)).symlink_to(f"socket:[{inode}]")
    rows = "".join(_proc_udp_row(port, inode) for port, inode in sockets + foreign)
    (net_root / "udp").write_text(_PROC_HEADER + rows, encoding="ascii")
    (net_root / "udp6").write_text(_PROC_HEADER, encoding="ascii")


def test_linux_joins_exact_pid_fds_to_udp_table_and_ignores_foreign_socket(
    tmp_path: Path,
) -> None:
    _fake_proc(
        tmp_path,
        process_id=4321,
        sockets=((33_142, 111),),
        foreign=((33_101, 999),),
    )

    assert process_owned_udp_ports(
        4321,
        platform_name="linux",
        proc_root=tmp_path,
    ) == (33_142,)
    assert exact_jamulus_client_udp_port(
        4321,
        33_101,
        platform_name="linux",
        proc_root=tmp_path,
    ) == 33_142


def test_linux_missing_multiple_and_out_of_range_sockets_fail_closed(
    tmp_path: Path,
) -> None:
    missing_root = tmp_path / "missing"
    _fake_proc(missing_root, process_id=1001, sockets=())
    with pytest.raises(ProcessSocketIdentityError):
        exact_jamulus_client_udp_port(
            1001,
            33_101,
            platform_name="linux",
            proc_root=missing_root,
        )

    multiple_root = tmp_path / "multiple"
    _fake_proc(
        multiple_root,
        process_id=1002,
        sockets=((33_142, 201), (33_143, 202)),
    )
    with pytest.raises(ProcessSocketIdentityError):
        exact_jamulus_client_udp_port(
            1002,
            33_101,
            platform_name="linux",
            proc_root=multiple_root,
        )

    outside_root = tmp_path / "outside"
    _fake_proc(outside_root, process_id=1003, sockets=((40_000, 301),))
    with pytest.raises(ProcessSocketIdentityError):
        exact_jamulus_client_udp_port(
            1003,
            33_101,
            platform_name="linux",
            proc_root=outside_root,
        )


def test_linux_malformed_socket_table_fails_without_echoing_private_text(
    tmp_path: Path,
) -> None:
    _fake_proc(tmp_path, process_id=4321, sockets=((33_142, 111),))
    private_marker = "private-endpoint.example:33142"
    (tmp_path / "4321" / "net" / "udp").write_text(
        _PROC_HEADER + private_marker + "\n",
        encoding="ascii",
    )

    with pytest.raises(ProcessSocketIdentityError) as caught:
        process_owned_udp_ports(
            4321,
            platform_name="linux",
            proc_root=tmp_path,
        )
    assert private_marker not in str(caught.value)


def test_macos_lsof_is_exact_bounded_and_deduplicates_dual_stack_rows(
    tmp_path: Path,
) -> None:
    lsof = tmp_path / "lsof"
    lsof.write_text("test fixture", encoding="ascii")
    lsof.chmod(0o700)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="p4321\nf10\nn*:33142\nf11\nn[::]:33142\n",
            stderr="",
        )

    assert exact_jamulus_client_udp_port(
        4321,
        33_101,
        platform_name="darwin",
        runner=runner,
        lsof_path=lsof,
    ) == 33_142
    command, kwargs = calls[0]
    assert command == [
        str(lsof),
        "-nP",
        "-a",
        "-p",
        "4321",
        "-iUDP",
        "-Fpn",
    ]
    assert kwargs["timeout"] == 2.0
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == "/"


def test_macos_lsof_foreign_pid_missing_and_ambiguous_proof_fail_closed(
    tmp_path: Path,
) -> None:
    lsof = tmp_path / "lsof"
    lsof.write_text("test fixture", encoding="ascii")
    lsof.chmod(0o700)

    def result(stdout: str, returncode: int = 0):
        def runner(command: list[str], **_kwargs: object):
            return subprocess.CompletedProcess(
                command,
                returncode,
                stdout=stdout,
                stderr="private diagnostic must not escape",
            )

        return runner

    for runner in (
        result("p9999\nf10\nn*:33142\n"),
        result("p4321\nf10\nn*:33142\nf11\nn*:33143\n"),
        result("", returncode=1),
    ):
        with pytest.raises(ProcessSocketIdentityError):
            exact_jamulus_client_udp_port(
                4321,
                33_101,
                platform_name="darwin",
                runner=runner,
                lsof_path=lsof,
            )


@pytest.mark.parametrize("bad_value", [True, 0, -1, 65_337])
def test_invalid_jamulus_base_is_never_treated_as_ownership(
    tmp_path: Path,
    bad_value: int,
) -> None:
    _fake_proc(tmp_path, process_id=4321, sockets=((33_142, 111),))
    with pytest.raises(ProcessSocketIdentityError):
        exact_jamulus_client_udp_port(
            4321,
            bad_value,
            platform_name="linux",
            proc_root=tmp_path,
        )


def test_lsof_path_must_be_an_absolute_regular_file(tmp_path: Path) -> None:
    with pytest.raises(ProcessSocketIdentityError):
        process_owned_udp_ports(
            os.getpid(),
            platform_name="darwin",
            lsof_path=Path("relative-lsof"),
        )
