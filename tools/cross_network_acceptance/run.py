"""Orchestrate topology, rendezvous, host, and guest for one acceptance case."""

from __future__ import annotations

import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools.cross_network_acceptance.receipt import build_receipt, write_receipt
from tools.cross_network_acceptance.topology import (
    RENDEZVOUS_TCP,
    RENDEZVOUS_UDP,
    STUN_UDP,
    AppliedTopology,
    TopologySpec,
    apply_netem,
    apply_topology,
    cleanup_topology,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for folder in ("/usr/sbin", "/sbin", "/usr/bin"):
        candidate = Path(folder) / name
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError(f"Missing required tool: {name}")


def git_sha(root: Path = REPO_ROOT) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _drop_ids() -> tuple[int, int]:
    sudo_uid = os.environ.get("SUDO_UID")
    sudo_gid = os.environ.get("SUDO_GID")
    if sudo_uid and sudo_gid:
        return int(sudo_uid), int(sudo_gid)
    return os.getuid(), os.getgid()


def _chown_tree(path: Path) -> None:
    uid, gid = _drop_ids()
    if os.geteuid() != 0 or uid == 0:
        return
    items = [path]
    if path.is_dir():
        items.extend(path.rglob("*"))
    for item in items:
        try:
            os.chown(item, uid, gid)
        except OSError:
            continue


def _user_env(uid: int) -> dict[str, str]:
    record = pwd.getpwuid(uid)
    env = os.environ.copy()
    env["HOME"] = record.pw_dir
    env["USER"] = record.pw_name
    env["LOGNAME"] = record.pw_name
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    env["PYTHONPATH"] = str(REPO_ROOT)
    env.pop("WEBJAM_ENABLE_REFERENCE_LOCAL", None)
    return env


def ns_popen(
    ns: str,
    argv: list[str],
    *,
    cwd: Path,
    stderr_path: Path | None = None,
) -> subprocess.Popen[str]:
    uid, gid = _drop_ids()
    inner = list(argv)
    if os.geteuid() == 0 and uid != 0:
        inner = [
            "setpriv",
            f"--reuid={uid}",
            f"--regid={gid}",
            "--clear-groups",
            "--",
            *argv,
        ]
    cmd = [_bin("ip"), "netns", "exec", ns, *inner]
    stderr: Any = subprocess.DEVNULL
    handle = None
    if stderr_path is not None:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        handle = stderr_path.open("w", encoding="utf-8")
        stderr = handle
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=_user_env(uid),
        stdout=subprocess.DEVNULL,
        stderr=stderr,
        text=True,
    )
    proc._stderr_handle = handle  # type: ignore[attr-defined]
    return proc


def _wait_json(path: Path, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                time.sleep(0.05)
                continue
            if isinstance(payload, dict):
                return payload
        time.sleep(0.05)
    return {}


def _invite_host_routable(ns: str, work_dir: Path) -> bool | None:
    invite = work_dir / "invite"
    if not invite.is_file():
        return None
    uid, gid = _drop_ids()
    script = (
        "import socket,sys\n"
        "from pathlib import Path\n"
        "from core.network_invite import parse_invite_link\n"
        "invite = parse_invite_link(Path(sys.argv[1]).read_text())\n"
        "sock = socket.socket(); sock.settimeout(2)\n"
        "try:\n"
        " sock.connect((invite.host, invite.peer_port)); sys.exit(0)\n"
        "except Exception:\n"
        " sys.exit(1)\n"
    )
    inner = [sys.executable, "-c", script, str(invite)]
    if os.geteuid() == 0 and uid != 0:
        inner = [
            "setpriv",
            f"--reuid={uid}",
            f"--regid={gid}",
            "--clear-groups",
            "--",
            *inner,
        ]
    result = subprocess.run(
        [_bin("ip"), "netns", "exec", ns, *inner],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_user_env(uid),
    )
    return result.returncode == 0


def _probe_in_ns(ns: str, host: str, port: int) -> bool:
    uid, gid = _drop_ids()
    script = (
        "import socket,sys\n"
        "s=socket.create_connection((sys.argv[1], int(sys.argv[2])), 2)\n"
        "s.close()\n"
    )
    inner = [sys.executable, "-c", script, host, str(port)]
    if os.geteuid() == 0 and uid != 0:
        inner = [
            "setpriv",
            f"--reuid={uid}",
            f"--regid={gid}",
            "--clear-groups",
            "--",
            *inner,
        ]
    result = subprocess.run(
        [_bin("ip"), "netns", "exec", ns, *inner],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_user_env(uid),
    )
    return result.returncode == 0


def _run_profile(
    topology: AppliedTopology,
    profile: str,
    work_dir: Path,
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chown_tree(work_dir)
    python = sys.executable
    host_cmd = [
        python,
        "-m",
        "tools.cross_network_acceptance",
        "role",
        "--role",
        "host",
        "--profile",
        profile,
        "--work-dir",
        str(work_dir),
    ]
    guest_cmd = [
        python,
        "-m",
        "tools.cross_network_acceptance",
        "role",
        "--role",
        "guest",
        "--profile",
        profile,
        "--work-dir",
        str(work_dir),
    ]
    host_err = work_dir / "host.stderr"
    guest_err = work_dir / "guest.stderr"
    host_proc = ns_popen(
        topology.host_ns, host_cmd, cwd=REPO_ROOT, stderr_path=host_err
    )
    guest_proc = None
    try:
        ready = _wait_json(work_dir / "host.ready", 25.0)
        if not ready.get("ok"):
            return {
                "profile": profile,
                "guest_connected": False,
                "session_state_exchanged": False,
                "harness_ok": False,
                "reason": "host_not_ready",
            }
        guest_proc = ns_popen(
            topology.guest_ns, guest_cmd, cwd=REPO_ROOT, stderr_path=guest_err
        )
        try:
            guest_proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            guest_proc.kill()
        result = _wait_json(work_dir / "guest.result", 2.0)
        routable = _invite_host_routable(topology.guest_ns, work_dir)
        return {
            "profile": profile,
            "guest_connected": bool(result.get("guest_connected")),
            "session_state_exchanged": bool(result.get("session_state_exchanged")),
            "harness_ok": True,
            "invite_host_routable_from_guest": routable,
        }
    finally:
        (work_dir / "host.stop").write_text("1\n", encoding="utf-8")
        for proc in (guest_proc, host_proc):
            if proc is None:
                continue
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            handle = getattr(proc, "_stderr_handle", None)
            if handle is not None:
                handle.close()
        invite = work_dir / "invite"
        if invite.exists():
            invite.write_text("", encoding="utf-8")
            invite.unlink(missing_ok=True)


def run_case(
    *,
    case: str,
    profiles: tuple[str, ...],
    output_dir: Path,
    netem: bool,
    sha: str,
    keep_workdir: bool = False,
) -> int:
    prefix = f"wjx{os.getpid() % 1000:03d}"
    spec = TopologySpec(case=case, prefix=prefix, netem=netem and case == "cross-network")
    work_root = output_dir / "work" / case
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chown_tree(output_dir)
    try:
        topology = apply_topology(spec)
    except Exception:
        receipt = build_receipt(
            sha=sha,
            case=case,
            status="FAIL",
            profiles=[],
            rendezvous_reachable_from_host=None,
            rendezvous_reachable_from_guest=None,
            invite_host_routable_from_guest=None,
            netem=spec.netem,
            notes="Could not create the simulated networks.",
        )
        write_receipt(output_dir, receipt)
        print(receipt["conductor_line"])
        return 1
    rendezvous: subprocess.Popen[str] | None = None
    host_reach = guest_reach = None
    profile_rows: list[dict[str, Any]] = []
    notes = ""
    try:
        netem_applied = apply_netem(spec) if spec.netem else False
    except Exception:
        netem_applied = False
    try:
        if topology.inet_ns and topology.inet_addr:
            rendezvous = ns_popen(
                topology.inet_ns,
                [
                    sys.executable,
                    "-m",
                    "tools.cross_network_acceptance.rendezvous",
                    topology.inet_addr,
                    str(RENDEZVOUS_TCP),
                    str(RENDEZVOUS_UDP),
                    str(STUN_UDP),
                ],
                cwd=REPO_ROOT,
            )
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                host_reach = _probe_in_ns(
                    topology.host_ns, topology.inet_addr, RENDEZVOUS_TCP
                )
                guest_reach = _probe_in_ns(
                    topology.guest_ns, topology.inet_addr, RENDEZVOUS_TCP
                )
                if host_reach and guest_reach:
                    break
                time.sleep(0.1)
            if not host_reach or not guest_reach:
                notes = "The simulated internet is not reachable from both homes."
            else:
                for profile in profiles:
                    profile_rows.append(
                        _run_profile(topology, profile, work_root / profile)
                    )
        else:
            for profile in profiles:
                profile_rows.append(
                    _run_profile(topology, profile, work_root / profile)
                )
    finally:
        if rendezvous is not None:
            rendezvous.send_signal(signal.SIGTERM)
            try:
                rendezvous.wait(timeout=3)
            except subprocess.TimeoutExpired:
                rendezvous.kill()
        cleanup_topology(spec)
        if not keep_workdir:
            for invite in work_root.rglob("invite"):
                invite.write_text("", encoding="utf-8")
                invite.unlink(missing_ok=True)
            shutil.rmtree(work_root, ignore_errors=True)
            parent = work_root.parent
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()

    harness_ok = all(row.get("harness_ok") for row in profile_rows) if profile_rows else False
    exchanged = all(row.get("session_state_exchanged") for row in profile_rows)
    connected = all(row.get("guest_connected") for row in profile_rows)
    status = "PASS" if harness_ok and exchanged and connected else "FAIL"
    if not notes:
        if case == "cross-network" and status == "FAIL" and harness_ok:
            notes = (
                "Expected until ordinary Host/Join uses the v3 rendezvous/relay "
                "described in docs/REMOTE-SESSIONS-PLAN.md (usability PR #372)."
            )
        elif not harness_ok:
            notes = "The harness could not start the host on the simulated network."
    routable_values = [
        row.get("invite_host_routable_from_guest")
        for row in profile_rows
        if "invite_host_routable_from_guest" in row
    ]
    routable = None
    if routable_values:
        routable = all(bool(item) for item in routable_values)
    receipt = build_receipt(
        sha=sha,
        case=case,
        status=status,
        profiles=[
            {
                "profile": row["profile"],
                "guest_connected": bool(row.get("guest_connected")),
                "session_state_exchanged": bool(row.get("session_state_exchanged")),
                "harness_ok": bool(row.get("harness_ok")),
            }
            for row in profile_rows
        ],
        rendezvous_reachable_from_host=host_reach,
        rendezvous_reachable_from_guest=guest_reach,
        invite_host_routable_from_guest=routable,
        netem=netem_applied,
        notes=notes,
    )
    write_receipt(output_dir, receipt)
    print(receipt["conductor_line"])
    return 0 if status == "PASS" else 1
