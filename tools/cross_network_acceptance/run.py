"""Orchestrate topology, mapping probe, proof peers. Proof-only: non-success is FAIL."""

from __future__ import annotations

import hashlib
import json
import os
import pwd
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools.cross_network_acceptance import CASE_TIMEOUT_S, SETUP_TRIES, HarnessError
from tools.cross_network_acceptance.nat_probe import verify_mapping_mode
from tools.cross_network_acceptance.proof import evaluate_proof
from tools.cross_network_acceptance.receipt import (
    build_receipt, clear_pass, write_receipt,
)
from tools.cross_network_acceptance.sidecar import require_transport_binary
from tools.cross_network_acceptance.stun import python_stun_command
from tools.cross_network_acceptance.topology import (
    STUN_UDP, STUN_UDP_ALT, AppliedTopology, TopologySpec, apply_netem, apply_topology,
    cleanup_topology, ns_names, required_bin,
)
from tools.cross_network_acceptance.turn import (
    CREDENTIAL_PORT, conntrack_dump, credential_script, credential_server_env,
    mint_credentials, relayed_bytes, stop_turn, turn_command,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def expect_path_for(case: str) -> str:
    return "relay" if case == "relay" else "direct"


def make_spec(case: str, job_id: str, attempt: int, netem: bool) -> TopologySpec:
    digest = hashlib.sha1(f"{job_id}:{attempt}".encode("utf-8")).hexdigest()
    prefix = "w" + digest[:5]
    salt = (int(digest[5:9], 16) % 220) + 1
    nat = "symmetric" if case == "relay" else "cone"
    return TopologySpec(
        case=case, prefix=prefix, salt=salt,
        netem=netem and case != "same-lan", nat_mode=nat,
    )


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
        "WEBJAM_PRODUCT_ROOT": str(product_root)})
    extra = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(REPO_ROOT), str(REPO_ROOT / "reference_service"), extra)))
    env.pop("WEBJAM_ENABLE_REFERENCE_LOCAL", None)
    env["WEBJAM_TEST_HARNESS"] = "1"
    if transport_binary:
        env["WEBJAM_TRANSPORT_BINARY"] = transport_binary
        env["WEBJAM_BUILD_ID"] = build_id
    elif str(env.get("WEBJAM_TRANSPORT_BINARY") or "").strip():
        require_transport_binary(str(env["WEBJAM_TRANSPORT_BINARY"]))
    else:
        env.pop("WEBJAM_TRANSPORT_BINARY", None)
    return env


def _drop_privs(argv: list[str]) -> list[str]:
    uid, gid = _drop_ids()
    if os.geteuid() == 0 and uid != 0:
        return ["setpriv", f"--reuid={uid}", f"--regid={gid}", "--clear-groups", "--", *argv]
    return list(argv)


def ns_popen(
    ns: str, argv: list[str], *, cwd: Path, env: dict[str, str],
    stderr_path: Path | None = None, drop_privs: bool = True,
) -> subprocess.Popen[str]:
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


def _reap(proc: subprocess.Popen[str], timeout_s: float = 10.0) -> int | None:
    try:
        return proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            return proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return proc.poll()


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


def _run_in_ns(ns: str, argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [required_bin("ip"), "netns", "exec", ns, *_drop_privs(argv)],
        check=False, capture_output=True, text=True, env=env,
    )


def _copy_logs(work_root: Path, output_dir: Path, case: str) -> None:
    dest = output_dir / "logs" / case
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path in work_root.rglob("*"):
        if path.suffix not in {".stderr", ".proof", ".cand", ".log", ".pcap", ".conntrack"}:
            continue
        target = dest / path.relative_to(work_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
        except OSError:
            continue
    _chown_tree(dest)


def _capture_router_state(spec: TopologySpec, work_root: Path) -> None:
    names = ns_names(spec.prefix)
    for key in ("nat_a", "nat_b"):
        ns = names[key]
        text = conntrack_dump(ns)
        (work_root / f"{key}.conntrack").write_text(text, encoding="utf-8")


def _start_pcap(spec: TopologySpec, work_root: Path, env: dict[str, str]) -> list[subprocess.Popen[str]]:
    if spec.case == "same-lan":
        return []
    tcpdump = shutil.which("tcpdump") or "/usr/sbin/tcpdump"
    if not Path(tcpdump).is_file():
        raise HarnessError("Need tcpdump.")
    names, p = ns_names(spec.prefix), spec.prefix
    procs = []
    for key, dev in (("nat_a", f"{p}ai"), ("nat_b", f"{p}bi")):
        out = work_root / f"{key}.pcap"
        procs.append(ns_popen(
            names[key], [tcpdump, "-n", "-i", dev, "-w", str(out)],
            cwd=REPO_ROOT, env=env, drop_privs=False,
        ))
    return procs


def _mapping_ports(topology: AppliedTopology, env: dict[str, str]) -> tuple[int, int]:
    inet = topology.inet_addr or ""
    script = (
        "import json,socket,sys\n"
        "from tools.cross_network_acceptance.nat_probe import dual_stun_ports\n"
        "from tools.cross_network_acceptance import PROOF_PORT\n"
        "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\n"
        "s.bind((sys.argv[1], PROOF_PORT))\n"
        "a,b=dual_stun_ports(s,(sys.argv[2],int(sys.argv[3])),(sys.argv[2],int(sys.argv[4])))\n"
        "print(json.dumps([a,b]))\n"
    )
    result = _run_in_ns(
        topology.host_ns,
        [sys.executable, "-c", script, topology.host_addr, inet, str(STUN_UDP), str(STUN_UDP_ALT)],
        env,
    )
    if result.returncode != 0:
        raise HarnessError("NAT mapping probe mismatch")
    try:
        ports = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessError("NAT mapping probe mismatch") from exc
    if not (isinstance(ports, list) and len(ports) == 2):
        raise HarnessError("NAT mapping probe mismatch")
    return int(ports[0]), int(ports[1])


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


def _pause(seconds: float) -> None:
    time.sleep(seconds)


def _post_in_ns(ns: str, url: str, env: dict[str, str]) -> dict[str, Any]:
    script = (
        "import json,secrets,sys,urllib.request\n"
        "session=secrets.token_hex(32)\n"
        "token=secrets.token_urlsafe(32).replace('=','A')\n"
        "body=json.dumps({'session_id':session,'invite_token':token}).encode()\n"
        "req=urllib.request.Request(sys.argv[1],data=body,method='POST',"
        "headers={'Content-Type':'application/json','Accept':'application/json'})\n"
        "payload=json.loads(urllib.request.urlopen(req,timeout=3).read().decode())\n"
        "urls=payload.get('urls') or payload.get('uris')\n"
        "ok=bool(payload.get('username') and payload.get('credential') and urls)\n"
        "print(json.dumps({'ok':ok,'has_urls':bool(urls)}))\n"
    )
    result = _run_in_ns(ns, [sys.executable, "-c", script, url], env)
    try:
        payload = json.loads(result.stdout) if result.returncode == 0 else {}
    except json.JSONDecodeError:
        payload = {}
    if not payload.get("ok"):
        raise HarnessError("turn credentials missing")
    return payload


def _wait_port(ns: str, host: str, port: int, env: dict[str, str], timeout_s: float = 8.0) -> None:
    script = (
        "import socket,sys,time\n"
        "deadline=time.monotonic()+float(sys.argv[3])\n"
        "while time.monotonic()<deadline:\n"
        "    try:\n"
        "        socket.create_connection((sys.argv[1],int(sys.argv[2])),0.2).close()\n"
        "        raise SystemExit(0)\n"
        "    except OSError:\n"
        "        time.sleep(0.05)\n"
        "raise SystemExit(1)\n"
    )
    result = _run_in_ns(
        ns, [sys.executable, "-c", script, host, str(port), str(timeout_s)], env,
    )
    if result.returncode != 0:
        raise HarnessError("reference control unavailable")


def _arm_timeout(seconds: float) -> None:
    def handle(_signum: int, _frame: Any) -> None:
        raise TimeoutError("case timeout")

    signal.signal(signal.SIGALRM, handle)
    signal.setitimer(signal.ITIMER_REAL, seconds)


def _disarm_timeout() -> None:
    signal.setitimer(signal.ITIMER_REAL, 0)


def run_case(
    *, case: str, output_dir: Path, netem: bool, sha: str,
    keep_workdir: bool = False, product_root: Path | None = None,
    transport_binary: str = "", build_id: str = "", job_id: str = "",
) -> int:
    if case not in {"same-lan", "cone", "relay"}:
        raise HarnessError("unknown case")
    product = (product_root or REPO_ROOT).resolve()
    binary = require_transport_binary(transport_binary)
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    clear_pass(output_dir, sha, case)
    uid, _gid = _drop_ids()
    env = _user_env(
        uid, product_root=product, transport_binary=binary, build_id=build_id or sha,
    )
    expect_path = expect_path_for(case)
    kernel = os.uname().release
    work_root = output_dir / "work" / case
    if work_root.exists():
        shutil.rmtree(work_root)
    work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _chown_tree(output_dir)
    success = False
    notes = ""
    netem_applied = False
    profiles: list[dict[str, Any]] = []
    relay_count = 0
    host_reach = guest_reach = None
    stun_host = stun_guest = None
    spec: TopologySpec | None = None
    _arm_timeout(CASE_TIMEOUT_S)
    try:
        last_error: Exception | None = None
        topology: AppliedTopology | None = None
        for attempt in range(SETUP_TRIES):
            spec = make_spec(case, job_id or str(os.getpid()), attempt, netem)
            stun_procs: list[subprocess.Popen[str]] = []
            pcap_procs: list[subprocess.Popen[str]] = []
            turn_proc: subprocess.Popen[str] | None = None
            cred_proc: subprocess.Popen[str] | None = None
            turn_log = work_root / "turn.log"
            asserting = False
            try:
                topology = apply_topology(spec)
                pcap_procs = _start_pcap(spec, work_root, env)
                if topology.inet_ns and topology.inet_addr:
                    stun_ports = (STUN_UDP, STUN_UDP_ALT) if case != "relay" else (STUN_UDP_ALT,)
                    stun_procs = [
                        ns_popen(
                            topology.inet_ns, python_stun_command(topology.inet_addr, stun_ports),
                            cwd=REPO_ROOT, env=env, drop_privs=False,
                            stderr_path=work_root / "stun.stderr",
                        )
                    ]
                    if case == "relay":
                        secret = secrets.token_hex(16)
                        cred_env = env.copy()
                        cred_env.update(credential_server_env(
                            secret, topology.inet_addr, STUN_UDP))
                        cred_proc = ns_popen(
                            topology.inet_ns,
                            [sys.executable, credential_script()],
                            cwd=REPO_ROOT, env=cred_env, drop_privs=False,
                            stderr_path=work_root / "turn-credentials.stderr",
                        )
                        _wait_port(topology.inet_ns, topology.inet_addr, CREDENTIAL_PORT, env)
                        posted = _post_in_ns(
                            topology.inet_ns,
                            f"http://{topology.inet_addr}:{CREDENTIAL_PORT}/turn-credentials",
                            env,
                        )
                        if not posted.get("ok"):
                            raise HarnessError("turn credentials missing")
                        minted = mint_credentials(secret, topology.inet_addr, STUN_UDP)
                        if not minted.get("username") or not minted.get("urls"):
                            raise HarnessError("turn credentials missing")
                        turn_proc = ns_popen(
                            topology.inet_ns,
                            turn_command(
                                bind_host=topology.inet_addr, port=STUN_UDP, secret=secret,
                                log_path=turn_log, pid_path=work_root / "turn.pid",
                            ),
                            cwd=REPO_ROOT, env=env, drop_privs=False,
                            stderr_path=work_root / "turn.stderr",
                        )
                    _pause(1.0)
                    ports = _mapping_ports(topology, env)
                    verify_mapping_mode(spec.nat_mode, ports[0], ports[1])
                    host_reach = guest_reach = stun_host = stun_guest = True
                if spec.netem:
                    netem_applied = apply_netem(spec)
                    if not netem_applied:
                        raise HarnessError("Requested netem missing.")
                music_nonce, art_nonce = secrets.token_hex(16), secrets.token_hex(16)
                env["WEBJAM_TEST_HARNESS"] = "1"
                if topology.inet_addr:
                    env["WEBJAM_TEST_STUN_SERVER"] = f"{topology.inet_addr}:{STUN_UDP}"
                else:
                    env.pop("WEBJAM_TEST_STUN_SERVER", None)
                if case == "relay" and topology.inet_addr:
                    env["WEBJAM_TEST_TURN_SERVER"] = f"{topology.inet_addr}:{STUN_UDP}"
                    env["WEBJAM_TEST_TURN_CREDENTIAL_URL"] = (
                        f"http://{topology.inet_addr}:{CREDENTIAL_PORT}/turn-credentials"
                    )
                else:
                    env.pop("WEBJAM_TEST_TURN_SERVER", None)
                    env.pop("WEBJAM_TEST_TURN_CREDENTIAL_URL", None)
                    env.pop("WEBJAM_TEST_TURN_SECRET", None)
                peer_dir = work_root / "peers"
                if peer_dir.exists():
                    shutil.rmtree(peer_dir)
                peer_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                cfg = {
                    "path": expect_path,
                    "music_nonce": music_nonce, "art_nonce": art_nonce,
                    "binds": {"host": topology.host_addr, "guest": topology.guest_addr},
                }
                (peer_dir / "peer-config.json").write_text(json.dumps(cfg), encoding="utf-8")
                (peer_dir / "peer-config.json").chmod(0o600)
                _chown_tree(peer_dir)
                asserting = True
                host_proc = ns_popen(
                    topology.host_ns,
                    [sys.executable, "-m", "tools.cross_network_acceptance", "peer",
                     "--role", "host", "--work-dir", str(peer_dir)],
                    cwd=REPO_ROOT, env=env, stderr_path=peer_dir / "host.stderr",
                )
                guest_proc = ns_popen(
                    topology.guest_ns,
                    [sys.executable, "-m", "tools.cross_network_acceptance", "peer",
                     "--role", "guest", "--work-dir", str(peer_dir)],
                    cwd=REPO_ROOT, env=env, stderr_path=peer_dir / "guest.stderr",
                )
                host_rc = _reap(host_proc, 55.0)
                guest_rc = _reap(guest_proc, 20.0)
                host_proof = _wait_json(peer_dir / "host.proof", 1.0)
                guest_proof = _wait_json(peer_dir / "guest.proof", 1.0)
                if turn_proc is not None:
                    _pause(1.0)
                    stop_turn(work_root / "turn.pid")
                    _stop_procs([turn_proc])
                    turn_proc = None
                relay_count = 0
                if turn_log.is_file():
                    relay_count = relayed_bytes(
                        turn_log.read_text(encoding="utf-8", errors="replace"))
                reason = evaluate_proof(
                    host_proof, guest_proof, expect_path=expect_path,
                    music_nonce=music_nonce, art_nonce=art_nonce, relay_bytes=relay_count,
                )
                if host_rc != 0 or guest_rc != 0:
                    reason = reason or "peer exit"
                profiles = [
                    {k: row.get(k) for k in (
                        "role", "local_type", "remote_type", "music_echo", "art_echo")}
                    for row in (host_proof, guest_proof)
                ]
                if reason:
                    raise HarnessError(reason)
                notes = "Proof exchanged."
                success = True
                last_error = None
                break
            except TimeoutError as exc:
                last_error = exc
                notes = "case timeout"
                break
            except (HarnessError, OSError) as exc:
                last_error = exc
                notes = str(exc)[:120]
                if asserting:
                    break
            finally:
                _stop_procs(
                    [*pcap_procs, turn_proc, cred_proc, *stun_procs],
                )
                if spec is not None:
                    try:
                        _capture_router_state(spec, work_root)
                    except OSError:
                        pass
                    _copy_logs(work_root, output_dir, case)
                    cleanup_topology(spec)
        if not success:
            raise last_error or HarnessError("setup failed")
    except Exception as exc:
        success = False
        notes = notes or str(exc)[:120]
    finally:
        _disarm_timeout()
        if spec is not None and not keep_workdir:
            shutil.rmtree(work_root, ignore_errors=True)
        if not success:
            clear_pass(output_dir, sha, case)
        else:
            receipt = build_receipt(
                sha=sha, case=case, status="PASS", profiles=profiles,
                rendezvous_reachable_from_host=host_reach,
                rendezvous_reachable_from_guest=guest_reach,
                netem=netem_applied, notes=notes,
                nat_mode="" if case == "same-lan" else spec.nat_mode if spec else "",
                stun_reachable_from_host=stun_host, stun_reachable_from_guest=stun_guest,
                kernel=kernel, expect_path=expect_path, relay_bytes=relay_count,
            )
            write_receipt(output_dir, receipt)
            print(receipt["conductor_line"])
            _chown_tree(output_dir)
        if not success:
            try:
                receipt = build_receipt(
                    sha=sha, case=case, status="FAIL", profiles=profiles,
                    rendezvous_reachable_from_host=host_reach,
                    rendezvous_reachable_from_guest=guest_reach,
                    netem=netem_applied, notes=notes or "FAIL",
                    nat_mode="" if case == "same-lan" else (spec.nat_mode if spec else ""),
                    stun_reachable_from_host=stun_host, stun_reachable_from_guest=stun_guest,
                    kernel=kernel, expect_path=expect_path, relay_bytes=relay_count,
                )
                write_receipt(output_dir, receipt)
                print(receipt["conductor_line"])
            except Exception:
                clear_pass(output_dir, sha, case)
            _chown_tree(output_dir)
    return 0 if success else 1
