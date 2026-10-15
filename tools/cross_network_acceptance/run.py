"""Orchestrate topology, STUN, host, and guest."""

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
from tools.cross_network_acceptance.stun import python_stun_command, stun_server_commands
from tools.cross_network_acceptance.topology import (
    INET_ADDR, RENDEZVOUS_TCP, RENDEZVOUS_UDP, STUN_UDP, STUN_UDP_ALT,
    AppliedTopology, TopologySpec, apply_netem, apply_topology,
    cleanup_topology, required_bin,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def receipt_case_name(case: str, nat_mode: str) -> str:
    if case == "same-lan":
        return "same-lan"
    if nat_mode == "cone":
        return "cross-network-cone"
    return "cross-network-symmetric" if nat_mode == "symmetric" else "cross-network"


def _drop_ids() -> tuple[int, int]:
    sudo_uid, sudo_gid = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    if sudo_uid and sudo_gid:
        return int(sudo_uid), int(sudo_gid)
    return os.getuid(), os.getgid()


def _chown_tree(path: Path) -> None:
    uid, gid = _drop_ids()
    if os.geteuid() != 0 or uid == 0:
        return
    items = [path] + (list(path.rglob("*")) if path.is_dir() else [])
    for item in items:
        try:
            os.chown(item, uid, gid)
        except OSError:
            continue


def _user_env(uid: int, *, product_root: Path, transport_binary: str, build_id: str) -> dict[str, str]:
    record = pwd.getpwuid(uid)
    env = os.environ.copy()
    env.update({
        "HOME": record.pw_dir, "USER": record.pw_name, "LOGNAME": record.pw_name,
        "QT_QPA_PLATFORM": "offscreen", "LIBGL_ALWAYS_SOFTWARE": "1",
        "WEBJAM_PRODUCT_ROOT": str(product_root),
    })
    extra = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(REPO_ROOT), extra)))
    env.pop("WEBJAM_ENABLE_REFERENCE_LOCAL", None)
    if transport_binary:
        env["WEBJAM_TRANSPORT_BINARY"] = transport_binary
        env["WEBJAM_BUILD_ID"] = build_id
    elif not str(env.get("WEBJAM_TRANSPORT_BINARY") or "").strip():
        env.pop("WEBJAM_TRANSPORT_BINARY", None)
    return env


_CLEAN_GUEST_CODES = frozenset({0, 1})
_NON_NETWORK_REASONS = frozenset({"startup_refused", "ingress_rejected", "clipboard_unavailable"})
_NON_NETWORK_CLASSES = frozenset(
    {"programming_error", "startup_failure", "unknown", "state_exchange_failure"})
_HONEST_NETWORK_CLASSES = frozenset({"honest_unreachable"})


def _non_network_guest_outcome(result: dict[str, Any]) -> bool:
    reason = str(result.get("reason") or "")
    failure_class = str(result.get("failure_class") or "")
    return reason in _NON_NETWORK_REASONS or failure_class in _NON_NETWORK_CLASSES


def completed_guest_result(result: dict[str, Any], *, timed_out: bool, returncode: int | None = 0) -> bool:
    if timed_out or returncode is None:
        return False
    if not isinstance(result, dict) or "guest_connected" not in result:
        return False
    if result.get("guest_connected"):
        return returncode == 0 and bool(result.get("session_state_exchanged"))
    if returncode not in _CLEAN_GUEST_CODES or _non_network_guest_outcome(result):
        return False
    if not result.get("attempt_finished"):
        return False
    return str(result.get("failure_class") or "") in _HONEST_NETWORK_CLASSES


def job_exit_code(*, status: str, expected_status: str, harness_ok: bool, fake_connect: bool = False) -> int:
    actual = str(status or "").strip().upper()
    expected = str(expected_status or "PASS").strip().upper()
    if actual not in {"PASS", "FAIL"} or expected not in {"PASS", "FAIL"}:
        return 1
    if not harness_ok or fake_connect:
        return 1
    return 0 if actual == expected else 1


def discarded_test_net3_hides_nat_type(nat_mode: str) -> bool:
    return nat_mode in {"cone", "symmetric"}


def honest_product_fail_notes(*, case: str, nat_mode: str) -> str:
    if discarded_test_net3_hides_nat_type(nat_mode):
        return (
            "Packaged direct-stun discards TEST-NET-3; does not prove "
            "cone-versus-symmetric product NAT behavior."
        )
    if case == "cross-network":
        return "Expected until v3 rendezvous/relay (docs/REMOTE-SESSIONS-PLAN.md)."
    return "Guest completed without connecting."


def _incomplete_guest_notes(profile_rows: list[dict[str, Any]]) -> str:
    reasons = {str(row.get("reason") or "") for row in profile_rows}
    if "guest_timeout" in reasons:
        return "Guest timed out before a completed result."
    if reasons & {"guest_crash", "guest_never_started"}:
        return "Guest crashed or never started."
    if reasons & {"guest_result_missing", "startup_refused"}:
        return "Guest did not finish a network connect attempt."
    if "host_crash" in reasons:
        return "Host exited during the guest attempt."
    if "host_not_ready" in reasons:
        return "Harness could not start the host."
    return "Harness did not complete an honest guest result."


def _drop_privs(argv: list[str]) -> list[str]:
    uid, gid = _drop_ids()
    if os.geteuid() == 0 and uid != 0:
        return ["setpriv", f"--reuid={uid}", f"--regid={gid}", "--clear-groups", "--", *argv]
    return list(argv)


def ns_popen(
    ns: str, argv: list[str], *, cwd: Path, env: dict[str, str],
    stderr_path: Path | None = None, drop_privs: bool = True) -> subprocess.Popen[str]:
    inner = _drop_privs(argv) if drop_privs else list(argv)
    handle = None
    stderr: Any = subprocess.DEVNULL
    if stderr_path is not None:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        handle = stderr_path.open("w", encoding="utf-8")
        stderr = handle
    proc = subprocess.Popen(
        [required_bin("ip"), "netns", "exec", ns, *inner],
        cwd=cwd, env=env, stdout=subprocess.DEVNULL, stderr=stderr, text=True)
    proc._stderr_handle = handle  # type: ignore[attr-defined]
    return proc


def _run_in_ns(ns: str, argv: list[str], env: dict[str, str]) -> int:
    return subprocess.run(
        [required_bin("ip"), "netns", "exec", ns, *_drop_privs(argv)],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
    ).returncode


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
    script = (
        "import socket,sys\nfrom pathlib import Path\n"
        "text=Path(sys.argv[1]).read_text()\n"
        "try:\n from core.network_invite import parse_invite_link\n"
        " invite=parse_invite_link(text); host,port=invite.host,invite.peer_port\n"
        "except Exception:\n sys.exit(2)\n"
        "s=socket.socket(); s.settimeout(2)\n"
        "try:\n s.connect((host,port)); sys.exit(0)\n"
        "except Exception:\n sys.exit(1)\n"
    )
    code = _run_in_ns(ns, [sys.executable, "-c", script, str(invite)], env)
    if code == 2:
        return None
    return code == 0


def _probe_in_ns(ns: str, host: str, port: int, env: dict[str, str]) -> bool:
    script = (
        "import socket,sys\n"
        "s=socket.create_connection((sys.argv[1], int(sys.argv[2])), 2)\n"
        "s.close()\n"
    )
    return _run_in_ns(ns, [sys.executable, "-c", script, host, str(port)], env) == 0


def _probe_stun_in_ns(ns: str, host: str, port: int, env: dict[str, str]) -> bool:
    script = (
        "import sys\n"
        "from tools.cross_network_acceptance.stun import probe\n"
        "mapped = probe(sys.argv[1], int(sys.argv[2]))\n"
        "sys.exit(0 if mapped else 1)\n"
    )
    return _run_in_ns(ns, [sys.executable, "-c", script, host, str(port)], env) == 0


def _until(timeout_s: float, check) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.1)


def _reap(proc: subprocess.Popen[str], timeout_s: float = 10.0) -> int | None:
    try:
        return proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            return proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return proc.poll()


def _run_profile(
    topology: AppliedTopology, profile: str, work_dir: Path, env: dict[str, str], cwd: Path,
) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chown_tree(work_dir)
    python = sys.executable

    def role_cmd(role: str) -> list[str]:
        return [python, "-m", "tools.cross_network_acceptance", "role",
                "--role", role, "--profile", profile, "--work-dir", str(work_dir)]

    host_proc = ns_popen(
        topology.host_ns, role_cmd("host"), cwd=cwd, stderr_path=work_dir / "host.stderr", env=env)
    guest_proc = None
    row: dict[str, Any] = {}
    try:
        ready = _wait_json(work_dir / "host.ready", 45.0)
        if not ready.get("ok"):
            row = {
                "profile": profile, "guest_connected": False,
                "session_state_exchanged": False, "harness_ok": False,
                "reason": "host_not_ready", "transport": ready.get("transport", ""),
                "share_reaches_internet": ready.get("share_reaches_internet"),
                "share_route": ready.get("share_route", ""),
                "failure_class": ready.get("failure_class", ""),
            }
            return row
        guest_proc = ns_popen(
            topology.guest_ns, role_cmd("guest"), cwd=cwd,
            stderr_path=work_dir / "guest.stderr", env=env)
        timed_out = False
        deadline = time.monotonic() + 60.0
        while guest_proc.poll() is None and host_proc.poll() is None:
            if time.monotonic() >= deadline:
                guest_proc.kill()
                timed_out = True
                _reap(guest_proc, 5.0)
                break
            time.sleep(0.1)
        if guest_proc.poll() is None and host_proc.poll() is not None:
            _reap(guest_proc, 5.0)
        host_rc = host_proc.poll()
        result = _wait_json(work_dir / "guest.result", 2.0)
        routable = _invite_host_routable(topology.guest_ns, work_dir, env)
        connected = bool(result.get("guest_connected"))
        rc = guest_proc.returncode
        completed = completed_guest_result(result, timed_out=timed_out, returncode=rc)
        reason = ""
        if host_rc is not None:
            completed = False
            reason = "host_crash"
        elif timed_out:
            reason = "guest_timeout"
        elif rc is None:
            reason = "guest_never_started"
        elif result.get("guest_connected") and rc != 0:
            reason = "guest_crash"
        elif rc not in _CLEAN_GUEST_CODES:
            reason = "guest_crash"
        elif not completed:
            reason = str(result.get("reason") or "guest_result_missing")
        row = {
            "profile": profile, "guest_connected": connected,
            "session_state_exchanged": bool(result.get("session_state_exchanged")),
            "harness_ok": completed, "reason": reason, "host_returncode": host_rc,
            "invite_host_routable_from_guest": routable,
            "transport": result.get("transport") or ready.get("transport") or "",
            "share_reaches_internet": ready.get("share_reaches_internet"),
            "share_route": ready.get("share_route", ""),
            "failure_class": result.get("failure_class", ""),
            "fake_connect": connected and topology.spec.nat_mode == "symmetric"}
        return row
    finally:
        (work_dir / "host.stop").write_text("1\n", encoding="utf-8")
        for proc in (guest_proc, host_proc):
            if proc is None:
                continue
            reaped = _reap(proc)
            if proc is host_proc and proc.returncode is None:
                proc.returncode = reaped
            handle = getattr(proc, "_stderr_handle", None)
            if handle is not None:
                handle.close()
        host_final = host_proc.returncode
        if row and host_final != 0:
            row["harness_ok"] = False
            row["reason"] = "host_crash"
            row["host_returncode"] = host_final
        invite = work_dir / "invite"
        if invite.exists():
            invite.write_text("", encoding="utf-8")
            invite.unlink(missing_ok=True)


def _copy_logs(work_root: Path, output_dir: Path, receipt_case: str) -> None:
    dest = output_dir / "logs" / receipt_case
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in work_root.rglob("*"):
        if path.suffix not in {".stderr", ".ready", ".result"}:
            continue
        target = dest / path.relative_to(work_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
        except OSError:
            continue
    _chown_tree(dest)


def _emit_receipt(
    output_dir: Path, receipt: dict[str, Any], *, expected_status: str,
    harness_ok: bool, fake_connect: bool = False,
) -> int:
    write_receipt(output_dir, receipt)
    _chown_tree(output_dir)
    print(receipt["conductor_line"])
    code = job_exit_code(
        status=str(receipt["status"]), expected_status=expected_status,
        harness_ok=harness_ok, fake_connect=fake_connect)
    if code == 0 and str(receipt["status"]).upper() == "FAIL":
        print("job=ok expected=FAIL")
    return code


def _stop_procs(procs: list[subprocess.Popen[str] | None]) -> None:
    for proc in procs:
        if proc is None:
            continue
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        handle = getattr(proc, "_stderr_handle", None)
        if handle is not None:
            handle.close()
            proc._stderr_handle = None  # type: ignore[attr-defined]


def _allow_unprivileged_stun(ns: str) -> None:
    subprocess.run(
        [required_bin("ip"), "netns", "exec", ns,
         required_bin("sysctl"), "-w", "net.ipv4.ip_unprivileged_port_start=0"],
        check=False, capture_output=True, text=True)


def _start_stun(
    topology: AppliedTopology, env: dict[str, str], commands: list[list[str]],
) -> list[subprocess.Popen[str]]:
    assert topology.inet_ns is not None
    return [
        ns_popen(topology.inet_ns, argv, cwd=REPO_ROOT, env=env, drop_privs=False)
        for argv in commands
    ]


def run_case(
    *, case: str, profiles: tuple[str, ...], output_dir: Path, netem: bool, sha: str,
    keep_workdir: bool = False, nat_mode: str = "masquerade",
    product_root: Path | None = None, transport_binary: str = "",
    build_id: str = "", expected_status: str = "PASS",
) -> int:
    prefix = f"wjx{os.getpid() % 1000:03d}"
    mode = nat_mode if case == "cross-network" else "masquerade"
    spec = TopologySpec(
        case=case, prefix=prefix, netem=netem and case == "cross-network",
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
        uid, product_root=product, transport_binary=transport_binary,
        build_id=build_id or sha,
    )
    try:
        topology = apply_topology(spec)
    except Exception:
        receipt = build_receipt(
            sha=sha, case=receipt_case, status="FAIL", profiles=[],
            rendezvous_reachable_from_host=None, rendezvous_reachable_from_guest=None,
            invite_host_routable_from_guest=None, netem=spec.netem,
            notes="Could not create simulated networks.", nat_mode=mode,
        )
        return _emit_receipt(
            output_dir, receipt, expected_status=expected_status, harness_ok=False,
        )
    rendezvous: subprocess.Popen[str] | None = None
    stun_procs: list[subprocess.Popen[str]] = []
    host_reach = guest_reach = None
    stun_host = stun_guest = None
    profile_rows: list[dict[str, Any]] = []
    notes = ""
    runnable = False
    try:
        netem_applied = apply_netem(spec) if spec.netem else False
    except Exception:
        netem_applied = False
    try:
        if topology.inet_ns and topology.inet_addr:
            _allow_unprivileged_stun(topology.inet_ns)
            stun_ports = (STUN_UDP, STUN_UDP_ALT)
            stun_procs = _start_stun(
                topology, env, stun_server_commands(topology.inet_addr, stun_ports)
            )
            rendezvous = ns_popen(
                topology.inet_ns,
                [sys.executable, "-c",
                 "import select,socket,sys\nh,t=sys.argv[1],int(sys.argv[2]);s=[]\n"
                 "tcp=socket.socket();tcp.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)\n"
                 "tcp.bind((h,t));tcp.listen(8);s.append(tcp)\n"
                 "for p in sys.argv[3:]:\n"
                 " u=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);u.bind((h,int(p)));s.append(u)\n"
                 "while True:\n"
                 " for i in select.select(s,[],[],1.0)[0]:\n"
                 "  if i is tcp:\n"
                 "   c,_=i.accept()\n"
                 "   try:c.sendall(b'ok\\n')\n"
                 "   finally:c.close()\n"
                 "  else:\n"
                 "   d,a=i.recvfrom(256)\n"
                 "   if d:i.sendto(d[:8] or b'ok',a)\n",
                 topology.inet_addr, str(RENDEZVOUS_TCP), str(RENDEZVOUS_UDP)],
                cwd=REPO_ROOT, env=env,
            )

            def _reach(*, stun_only: bool = False) -> bool:
                nonlocal host_reach, guest_reach, stun_host, stun_guest
                if not stun_only:
                    host_reach = _probe_in_ns(
                        topology.host_ns, topology.inet_addr, RENDEZVOUS_TCP, env)
                    guest_reach = _probe_in_ns(
                        topology.guest_ns, topology.inet_addr, RENDEZVOUS_TCP, env)
                stun_host = _probe_stun_in_ns(
                    topology.host_ns, topology.inet_addr, STUN_UDP, env)
                stun_guest = _probe_stun_in_ns(
                    topology.guest_ns, INET_ADDR, STUN_UDP, env)
                return bool(
                    (stun_only or (host_reach and guest_reach))
                    and stun_host and stun_guest
                )

            _until(5.0, _reach)
            if (not stun_host or not stun_guest) and stun_procs:
                _stop_procs(stun_procs)
                stun_procs = _start_stun(
                    topology, env, [python_stun_command(topology.inet_addr, stun_ports)],
                )
                _until(5.0, lambda: _reach(stun_only=True))
            if not host_reach or not guest_reach:
                notes = "Simulated internet is not reachable from both homes."
            elif not stun_host or not stun_guest:
                notes = "Public STUN is not reachable from both homes."
            else:
                runnable = True
        else:
            runnable = True
        if runnable:
            profile_rows.extend(
                _run_profile(topology, profile, work_root / profile, env, REPO_ROOT)
                for profile in profiles
            )
    finally:
        _stop_procs([*stun_procs, rendezvous])
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
    if fake:
        harness_ok = False
        notes = "Symmetric NAT produced a fake connect; the job must fail."
    if spec.netem and not netem_applied:
        harness_ok = False
        notes = "Requested netem missing."
    status = (
        "FAIL" if mode == "symmetric"
        else ("PASS" if harness_ok and exchanged and connected else "FAIL")
    )
    if not notes:
        honest_fail = harness_ok and (
            (mode == "symmetric" and not connected)
            or (mode != "symmetric" and case == "cross-network" and status == "FAIL")
        )
        if honest_fail:
            notes = honest_product_fail_notes(case=case, nat_mode=mode)
        elif not harness_ok:
            notes = _incomplete_guest_notes(profile_rows)
    routable_values = [
        row.get("invite_host_routable_from_guest")
        for row in profile_rows if "invite_host_routable_from_guest" in row]
    routable = None
    if routable_values:
        routable = all(bool(item) for item in routable_values)
    receipt = build_receipt(
        sha=sha, case=receipt_case, status=status,
        profiles=[{key: row.get(key) for key in (
            "profile", "guest_connected", "session_state_exchanged",
            "harness_ok", "transport", "share_reaches_internet",
            "share_route", "failure_class", "fake_connect")} for row in profile_rows],
        rendezvous_reachable_from_host=host_reach,
        rendezvous_reachable_from_guest=guest_reach,
        invite_host_routable_from_guest=routable, netem=netem_applied,
        notes=notes, nat_mode=mode,
        stun_reachable_from_host=stun_host, stun_reachable_from_guest=stun_guest)
    return _emit_receipt(
        output_dir, receipt, expected_status=expected_status,
        harness_ok=harness_ok, fake_connect=fake)

