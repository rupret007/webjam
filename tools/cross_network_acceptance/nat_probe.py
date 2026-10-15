"""Live RFC 4787 UDP probes. Privileged CI runs this fail-closed.

punch_admitted is APDF reply-from-contacted-endpoint, not Linux simultaneous-open.
"""

import json
import os
import subprocess
import sys
import time
from typing import Any

from tools.cross_network_acceptance.topology import (
    INET_ADDR, NAT_A_WAN, TopologySpec,
    apply_topology, cleanup_topology, required_bin,
)

_COLLECT = (
    "import json,socket,sys,time\n"
    "h,p,n,lim=sys.argv[1],int(sys.argv[2]),int(sys.argv[3]),float(sys.argv[4])\n"
    "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\n"
    "s.bind((h,p)); s.settimeout(0.15); end=time.monotonic()+lim; seen=[]\n"
    "while time.monotonic()<end and len(seen)<n:\n"
    " try:\n"
    "  d,a=s.recvfrom(256); seen.append({'src':list(a),'data':d.decode()})\n"
    " except TimeoutError:\n"
    "  pass\n"
    "print(json.dumps(seen))\n"
)
_SEND = (
    "import socket,sys\n"
    "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\n"
    "s.bind((sys.argv[1],int(sys.argv[2])))\n"
    "s.sendto(sys.argv[5].encode(),(sys.argv[3],int(sys.argv[4])))\n"
)


class ProbeError(RuntimeError):
    pass


def expected_behavior(nat_mode: str) -> dict[str, bool]:
    cone = nat_mode in {"cone", "masquerade"}
    return {"unsolicited_dropped": True, "punch_admitted": True, "mapping_reused": cone}


def _exec(ns: str, script: str, args: list[str], *, popen: bool = False, timeout: float = 6):
    argv = [required_bin("ip"), "netns", "exec", ns, sys.executable, "-c", script, *args]
    if popen:
        return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    completed = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, check=False,
    )
    if completed.returncode != 0:
        raise ProbeError("subprocess failed")
    return completed


def _read(proc: subprocess.Popen[str]) -> list[dict[str, Any]]:
    out, _err = proc.communicate(timeout=6)
    if proc.returncode != 0:
        raise ProbeError("subprocess failed")
    try:
        payload = json.loads("" if out is None else out)
    except json.JSONDecodeError as exc:
        raise ProbeError("unparseable mapping") from exc
    if not isinstance(payload, list):
        raise ProbeError("unparseable mapping")
    return payload


def _ipv4(value: str) -> bool:
    parts = str(value or "").split(".")
    try:
        return len(parts) == 4 and all(0 <= int(part) <= 255 for part in parts)
    except ValueError:
        return False


def mapping_observed(row: dict[str, Any]) -> bool:
    ports = row.get("observed_ports")
    mapped = int(row.get("host_mapped") or 0)
    guest = int(row.get("guest_mapped") or 0)
    if not (isinstance(ports, list) and len(ports) >= 2 and _ipv4(str(row.get("mapped_addr") or ""))):
        return False
    return all(1 <= n <= 65535 for n in (mapped, guest, *ports) if isinstance(n, int)) and all(
        isinstance(p, int) for p in ports
    )


def _row(mode: str, **fields: Any) -> dict[str, Any]:
    base = {
        "nat_mode": mode, "unsolicited_dropped": False, "punch_admitted": False,
        "mapping_reused": False, "host_mapped": 0, "guest_mapped": 0,
        "observed_ports": [], "mapped_addr": "",
    }
    base.update(fields)
    return base


def probe_nat_behavior(nat_mode: str, *, prefix: str = "") -> dict[str, Any]:
    spec = TopologySpec(
        case="cross-network",
        prefix=prefix or f"w{nat_mode[0]}{os.getpid() % 100:02d}",
        nat_mode=nat_mode,
    )
    topology = apply_topology(spec)
    host_ns, guest_ns, inet_ns = topology.host_ns, topology.guest_ns, topology.inet_ns
    if inet_ns is None:
        cleanup_topology(spec)
        raise RuntimeError("Cross-network probe needs the public namespace.")
    host, guest = "192.168.110.10", "192.168.120.10"
    hp, gp, p1, p2, stranger = 41000, 42000, 51001, 51002, 51999
    try:
        def collect(ns: str, addr: str, port: int, limit: float = 2.0):
            return _exec(ns, _COLLECT, [addr, str(port), "1", str(limit)], popen=True)

        def send(ns: str, src: str, sport: int, dest: str, dport: int, token: str) -> None:
            _exec(ns, _SEND, [src, str(sport), dest, str(dport), token])

        c1, c2 = collect(inet_ns, INET_ADDR, p1), collect(inet_ns, INET_ADDR, p2)
        time.sleep(0.15)
        send(host_ns, host, hp, INET_ADDR, p1, "m1")
        send(host_ns, host, hp, INET_ADDR, p2, "m2")
        seen = _read(c1) + _read(c2)
        ports = [row["src"][1] for row in seen if row.get("src")]
        if len(seen) < 2 or not seen[0].get("src") or len(ports) < 2:
            raise ProbeError("missing mapping")
        reused = len(set(ports)) == 1
        mapped_addr = str(seen[0]["src"][0])
        host_mapped = int(seen[0]["src"][1])
        gwait = collect(inet_ns, INET_ADDR, p1)
        time.sleep(0.1)
        send(guest_ns, guest, gp, INET_ADDR, p1, "gm")
        grown = _read(gwait)
        if not grown or not grown[0].get("src"):
            raise ProbeError("missing mapping")
        guest_mapped = int(grown[0]["src"][1])
        if not mapping_observed({
            "observed_ports": ports, "host_mapped": host_mapped,
            "guest_mapped": guest_mapped, "mapped_addr": mapped_addr,
        }):
            raise ProbeError("missing mapping")
        inside = collect(host_ns, host, hp, 1.2)
        time.sleep(0.1)
        send(inet_ns, INET_ADDR, stranger, NAT_A_WAN, host_mapped, "no")
        dropped = _read(inside) == []
        reply = collect(host_ns, host, hp, 1.4)
        time.sleep(0.1)
        send(inet_ns, INET_ADDR, p1, NAT_A_WAN, host_mapped, "ok")
        punched = any(row.get("data") == "ok" for row in _read(reply))
        return _row(
            nat_mode, unsolicited_dropped=dropped, punch_admitted=punched,
            mapping_reused=reused, host_mapped=host_mapped, guest_mapped=guest_mapped,
            observed_ports=ports, mapped_addr=mapped_addr,
        )
    finally:
        cleanup_topology(spec)


def check_probe(row: dict[str, Any]) -> bool:
    if not mapping_observed(row):
        return False
    want = expected_behavior(str(row.get("nat_mode") or ""))
    return all(bool(row.get(key)) is want[key] for key in want)


def run_probes(modes: tuple[str, ...] = ("masquerade", "cone", "symmetric")) -> int:
    failed = 0
    for mode in modes:
        try:
            row = probe_nat_behavior(mode)
            ok = check_probe(row)
            print(json.dumps({"ok": ok, **row}, sort_keys=True))
        except Exception:
            print(json.dumps({"ok": False, "nat_mode": mode, "error": "probe_failed"}))
            ok = False
        failed += 0 if ok else 1
    return 0 if failed == 0 else 1
