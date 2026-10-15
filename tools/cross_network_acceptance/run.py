"""Orchestrate topology, STUN, host, and guest for one acceptance case."""

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
    INET_ADDR,
    RENDEZVOUS_TCP,
    RENDEZVOUS_UDP,
    STUN_UDP,
    STUN_UDP_ALT,
    AppliedTopology,
    TopologySpec,
    apply_netem,
    apply_topology,
    cleanup_topology,
)
from tools.cross_network_acceptance.turn import argv as turn_argv
from tools.cross_network_acceptance.turn import new_secret, write_config

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


def receipt_case_name(case: str, nat_mode: str) -> str:
    if case == "same-lan":
        return "same-lan"
    if nat_mode == "cone":
        return "cross-network-cone"
    if nat_mode == "symmetric":
        return "cross-network-symmetric"
    return "cross-network"


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


def _user_env(
    uid: int,
    *,
    product_root: Path,
    transport_binary: str,
    build_id: str,
) -> dict[str, str]:
    record = pwd.getpwuid(uid)
    env = os.environ.copy()
    env["HOME"] = record.pw_dir
    env["USER"] = record.pw_name
    env["LOGNAME"] = record.pw_name
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(REPO_ROOT) if not existing else os.pathsep.join((str(REPO_ROOT), existing))
    )
    env["WEBJAM_PRODUCT_ROOT"] = str(product_root)
    env.pop("WEBJAM_ENABLE_REFERENCE_LOCAL", None)
    env["WEBJAM_TEST_HARNESS"] = "1"
    env["WEBJAM_TEST_STUN_SERVER"] = f"{INET_ADDR}:{STUN_UDP}"
    env["WEBJAM_TEST_TURN_SERVER"] = f"{INET_ADDR}:{STUN_UDP}"
    secret = str(os.environ.get("WEBJAM_TEST_TURN_SECRET", "") or "").strip()
    if secret:
        env["WEBJAM_TEST_TURN_SECRET"] = secret
    if transport_binary:
        env["WEBJAM_TRANSPORT_BINARY"] = transport_binary
        env["WEBJAM_BUILD_ID"] = build_id
    else:
        env.pop("WEBJAM_TRANSPORT_BINARY", None)
    return env


def ns_popen(
    ns: str,
    argv: list[str],
    *,
    cwd: Path,
    stderr_path: Path | None = None,
    env: dict[str, str] | None = None,
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
        env=env if env is not None else _user_env(uid, product_root=REPO_ROOT, transport_binary="", build_id=""),
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


def _invite_host_routable(ns: str, work_dir: Path, env: dict[str, str]) -> bool | None:
    invite = work_dir / "invite"
    if not invite.is_file():
        return None
    uid, gid = _drop_ids()
    script = (
        "import socket,sys\n"
        "from pathlib import Path\n"
        "text = Path(sys.argv[1]).read_text()\n"
        "try:\n"
        " from core.network_invite import parse_invite_link\n"
        " invite = parse_invite_link(text)\n"
        " host, port = invite.host, invite.peer_port\n"
        "except Exception:\n"
        " sys.exit(2)\n"
        "sock = socket.socket(); sock.settimeout(2)\n"
        "try:\n"
        " sock.connect((host, port)); sys.exit(0)\n"
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
        env=env,
    )
    if result.returncode == 2:
        return None
    return result.returncode == 0


def _probe_in_ns(ns: str, host: str, port: int, env: dict[str, str]) -> bool:
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
        env=env,
    )
    return result.returncode == 0


def _probe_stun_in_ns(ns: str, host: str, port: int, env: dict[str, str]) -> bool:
    uid, gid = _drop_ids()
    script = (
        "import sys\n"
        "from tools.cross_network_acceptance.stun import probe\n"
        "mapped = probe(sys.argv[1], int(sys.argv[2]))\n"
        "sys.exit(0 if mapped else 1)\n"
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
        env=env,
    )
    return result.returncode == 0


def _run_profile(
    topology: AppliedTopology,
    profile: str,
    work_dir: Path,
    env: dict[str, str],
    cwd: Path,
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
        topology.host_ns,
        host_cmd,
        cwd=cwd,
        stderr_path=host_err,
        env=env,
    )
    guest_proc = None
    try:
        ready = _wait_json(work_dir / "host.ready", 45.0)
        if not ready.get("ok"):
            return {
                "profile": profile,
                "guest_connected": False,
                "session_state_exchanged": False,
                "harness_ok": False,
                "reason": "host_not_ready",
                "transport": ready.get("transport", ""),
                "share_reaches_internet": ready.get("share_reaches_internet"),
                "share_route": ready.get("share_route", ""),
                "failure_class": ready.get("failure_class", ""),
            }
        guest_proc = ns_popen(
            topology.guest_ns,
            guest_cmd,
            cwd=cwd,
            stderr_path=guest_err,
            env=env,
        )
        try:
            guest_proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            guest_proc.kill()
        result = _wait_json(work_dir / "guest.result", 2.0)
        routable = _invite_host_routable(topology.guest_ns, work_dir, env)
        connected = bool(result.get("guest_connected"))
        return {
            "profile": profile,
            "guest_connected": connected,
            "session_state_exchanged": bool(result.get("session_state_exchanged")),
            "harness_ok": True,
            "invite_host_routable_from_guest": routable,
            "transport": result.get("transport") or ready.get("transport") or "",
            "share_reaches_internet": ready.get("share_reaches_internet"),
            "share_route": ready.get("share_route", ""),
            "failure_class": result.get("failure_class", ""),
            "failure_text": result.get("failure_text", ""),
            "error_code": result.get("error_code", ""),
            "session_path": str(result.get("session_path") or ""),
            "fake_connect": connected
            and topology.spec.nat_mode == "symmetric"
            and str(result.get("session_path") or "") != "relay",
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


def _copy_logs(work_root: Path, output_dir: Path, receipt_case: str) -> None:
    dest = output_dir / "logs" / receipt_case
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in work_root.rglob("*"):
        if path.suffix in {".stderr", ".ready", ".result"} or path.name in {
            "host.ready",
            "guest.result",
            "host.stderr",
            "guest.stderr",
        }:
            target = dest / path.relative_to(work_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(path, target)
            except OSError:
                continue
    _chown_tree(dest)


def run_case(
    *,
    case: str,
    profiles: tuple[str, ...],
    output_dir: Path,
    netem: bool,
    sha: str,
    keep_workdir: bool = False,
    nat_mode: str = "masquerade",
    product_root: Path | None = None,
    transport_binary: str = "",
    build_id: str = "",
) -> int:
    prefix = f"wjx{os.getpid() % 1000:03d}"
    mode = nat_mode if case == "cross-network" else "masquerade"
    spec = TopologySpec(
        case=case,
        prefix=prefix,
        netem=netem and case == "cross-network",
        nat_mode=mode,
    )
    receipt_case = receipt_case_name(case, mode)
    product = (product_root or REPO_ROOT).resolve()
    work_root = output_dir / "work" / receipt_case
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chown_tree(output_dir)
    uid, _gid = _drop_ids()
    env = _user_env(
        uid,
        product_root=product,
        transport_binary=transport_binary,
        build_id=build_id or sha,
    )
    try:
        topology = apply_topology(spec)
    except Exception:
        receipt = build_receipt(
            sha=sha,
            case=receipt_case,
            status="FAIL",
            profiles=[],
            rendezvous_reachable_from_host=None,
            rendezvous_reachable_from_guest=None,
            invite_host_routable_from_guest=None,
            netem=spec.netem,
            notes="Could not create the simulated networks.",
            nat_mode=mode,
        )
        write_receipt(output_dir, receipt)
        print(receipt["conductor_line"])
        return 1
    rendezvous: subprocess.Popen[str] | None = None
    stun_proc: subprocess.Popen[str] | None = None
    turn_proc: subprocess.Popen[str] | None = None
    host_reach = guest_reach = None
    stun_host = stun_guest = None
    profile_rows: list[dict[str, Any]] = []
    notes = ""
    try:
        netem_applied = apply_netem(spec) if spec.netem else False
    except Exception:
        netem_applied = False
    try:
        if topology.inet_ns and topology.inet_addr:
            turn_secret = new_secret()
            env["WEBJAM_TEST_TURN_SECRET"] = turn_secret
            try:
                config = write_config(work_root / "coturn", secret=turn_secret)
                # turnserver runs as the dropped-privilege user; the config
                # directory is created as root and must be readable/writable.
                _chown_tree(config.parent)
                turn_proc = ns_popen(
                    topology.inet_ns,
                    turn_argv(config),
                    cwd=REPO_ROOT,
                    env=env,
                    stderr_path=work_root / "coturn.stderr",
                )
            except Exception:
                turn_proc = None
                if not notes:
                    notes = "The public TURN server could not be started."
            stun_proc = ns_popen(
                topology.inet_ns,
                [
                    sys.executable,
                    "-m",
                    "tools.cross_network_acceptance.stun",
                    topology.inet_addr,
                    str(STUN_UDP_ALT),
                ],
                cwd=REPO_ROOT,
                env=env,
            )
            rendezvous = ns_popen(
                topology.inet_ns,
                [
                    sys.executable,
                    "-m",
                    "tools.cross_network_acceptance.rendezvous",
                    topology.inet_addr,
                    str(RENDEZVOUS_TCP),
                    str(RENDEZVOUS_UDP),
                ],
                cwd=REPO_ROOT,
                env=env,
            )
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                host_reach = _probe_in_ns(
                    topology.host_ns, topology.inet_addr, RENDEZVOUS_TCP, env
                )
                guest_reach = _probe_in_ns(
                    topology.guest_ns, topology.inet_addr, RENDEZVOUS_TCP, env
                )
                stun_host = _probe_stun_in_ns(
                    topology.host_ns, topology.inet_addr, STUN_UDP, env
                )
                stun_guest = _probe_stun_in_ns(
                    topology.guest_ns, INET_ADDR, STUN_UDP, env
                )
                if host_reach and guest_reach and stun_host and stun_guest:
                    break
                time.sleep(0.1)
            if not host_reach or not guest_reach:
                notes = "The simulated internet is not reachable from both homes."
            elif not stun_host or not stun_guest:
                notes = "The public STUN server is not reachable from both homes."
            else:
                for profile in profiles:
                    profile_rows.append(
                        _run_profile(
                            topology, profile, work_root / profile, env, REPO_ROOT
                        )
                    )
        else:
            for profile in profiles:
                profile_rows.append(
                    _run_profile(
                        topology, profile, work_root / profile, env, REPO_ROOT
                    )
                )
    finally:
        for proc in (turn_proc, stun_proc, rendezvous):
            if proc is None:
                continue
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        _copy_logs(work_root, output_dir, receipt_case)
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
    fake = any(row.get("fake_connect") for row in profile_rows)
    paths = [str(row.get("session_path") or "") for row in profile_rows]
    if mode == "symmetric":
        relayed = bool(paths) and all(item == "relay" for item in paths)
        status = "PASS" if harness_ok and exchanged and connected and relayed else "FAIL"
        if fake or (connected and not relayed):
            notes = (
                "Symmetric NAT connected without the TURN relay. The fallback "
                "must be the only path that succeeds on random port mapping."
            )
        elif status == "PASS":
            notes = "Symmetric NAT joined through the TURN relay."
        elif not notes:
            notes = "Symmetric NAT did not connect through the TURN relay."
    elif mode == "cone":
        direct = bool(paths) and all(item == "direct" for item in paths)
        status = "PASS" if harness_ok and exchanged and connected and direct else "FAIL"
        if connected and not direct:
            notes = (
                "Cone NAT used the TURN relay; it must stay on the direct path."
            )
        elif not notes:
            if status == "FAIL" and harness_ok:
                notes = (
                    "Cone NAT Host/Join did not connect. See share_route and "
                    "failure_class in the receipt profiles."
                )
            elif not harness_ok:
                notes = "The harness could not start the host on the simulated network."
    else:
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
        case=receipt_case,
        status=status,
        profiles=[
            {
                "profile": row["profile"],
                "guest_connected": bool(row.get("guest_connected")),
                "session_state_exchanged": bool(row.get("session_state_exchanged")),
                "harness_ok": bool(row.get("harness_ok")),
                "transport": str(row.get("transport") or ""),
                "share_reaches_internet": row.get("share_reaches_internet"),
                "share_route": str(row.get("share_route") or ""),
                "failure_class": str(row.get("failure_class") or ""),
                "session_path": str(row.get("session_path") or ""),
                "fake_connect": bool(row.get("fake_connect")),
            }
            for row in profile_rows
        ],
        rendezvous_reachable_from_host=host_reach,
        rendezvous_reachable_from_guest=guest_reach,
        invite_host_routable_from_guest=routable,
        netem=netem_applied,
        notes=notes,
        nat_mode=mode,
        stun_reachable_from_host=stun_host,
        stun_reachable_from_guest=stun_guest,
    )
    write_receipt(output_dir, receipt)
    print(receipt["conductor_line"])
    return 0 if status == "PASS" else 1
