"""Linux netns + iptables MASQUERADE topology for two simulated homes.

Cross-network: host behind NAT-A, guest behind NAT-B, a public internet
namespace between them. No DNAT, REDIRECT, or port forwarding.

Same-LAN control: host and guest share one L2 with RFC1918 addresses.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# TEST-NET-3 documents a fake public internet. RFC1918 stays on the LANs so
# ordinary v2 invites still mint a private address, which the other house
# cannot route to.
INET_NETWORK = "203.0.113.0/24"
INET_ADDR = "203.0.113.1"
NAT_A_WAN = "203.0.113.10"
NAT_B_WAN = "203.0.113.20"
LAN_A = "192.168.110.0/24"
NAT_A_LAN = "192.168.110.1"
HOST_ADDR = "192.168.110.10"
LAN_B = "192.168.120.0/24"
NAT_B_LAN = "192.168.120.1"
GUEST_CROSS = "192.168.120.10"
GUEST_LAN = "192.168.110.11"
RENDEZVOUS_TCP = 47131
RENDEZVOUS_UDP = 47132
STUN_UDP = 3478
NETEM_DELAY = "100ms"
NETEM_JITTER = "50ms"
NETEM_LOSS = "1%"


class TopologyError(RuntimeError):
    """The simulated networks could not be created or cleaned up."""


def _bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for folder in ("/usr/sbin", "/sbin", "/usr/bin"):
        candidate = Path(folder) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise TopologyError(f"This harness needs {name} on PATH or in /usr/sbin.")


@dataclass(frozen=True)
class TopologySpec:
    case: str
    prefix: str
    netem: bool = False

    def __post_init__(self) -> None:
        if self.case not in {"same-lan", "cross-network"}:
            raise TopologyError("Topology case must be same-lan or cross-network.")
        token = str(self.prefix or "")
        if not token.isalnum() or not 3 <= len(token) <= 6:
            raise TopologyError("Topology prefix must be a short alphanumeric token.")


@dataclass(frozen=True)
class AppliedTopology:
    spec: TopologySpec
    host_ns: str
    guest_ns: str
    inet_ns: str | None
    host_addr: str
    guest_addr: str
    inet_addr: str | None
    rendezvous_tcp: int | None
    commands: tuple[tuple[str, ...], ...]


def ns_names(prefix: str) -> dict[str, str]:
    return {
        "inet": f"{prefix}-inet",
        "nat_a": f"{prefix}-na",
        "nat_b": f"{prefix}-nb",
        "host": f"{prefix}-host",
        "guest": f"{prefix}-gst",
    }


def setup_commands(spec: TopologySpec) -> list[list[str]]:
    """Return ip/iptables/tc argv lists. Tests assert these; they are not run."""

    ip = "ip"
    iptables = "iptables"
    sysctl = "sysctl"
    names = ns_names(spec.prefix)
    p = spec.prefix
    cmds: list[list[str]] = []

    def add(*parts: str) -> None:
        cmds.append(list(parts))

    def ns(name: str, *parts: str) -> None:
        add(ip, "netns", "exec", name, *parts)

    if spec.case == "same-lan":
        add(ip, "netns", "add", names["host"])
        add(ip, "netns", "add", names["guest"])
        add(ip, "link", "add", f"{p}hg", "type", "veth", "peer", "name", f"{p}gh")
        add(ip, "link", "set", f"{p}hg", "netns", names["host"])
        add(ip, "link", "set", f"{p}gh", "netns", names["guest"])
        for target, dev, addr in (
            (names["host"], f"{p}hg", f"{HOST_ADDR}/24"),
            (names["guest"], f"{p}gh", f"{GUEST_LAN}/24"),
        ):
            ns(target, ip, "link", "set", "lo", "up")
            ns(target, ip, "addr", "add", addr, "dev", dev)
            ns(target, ip, "link", "set", dev, "up")
            ns(target, ip, "route", "add", "default", "dev", dev)
        return cmds

    add(ip, "netns", "add", names["inet"])
    add(ip, "netns", "add", names["nat_a"])
    add(ip, "netns", "add", names["nat_b"])
    add(ip, "netns", "add", names["host"])
    add(ip, "netns", "add", names["guest"])
    add(ip, "link", "add", f"{p}ia", "type", "veth", "peer", "name", f"{p}ai")
    add(ip, "link", "add", f"{p}ib", "type", "veth", "peer", "name", f"{p}bi")
    add(ip, "link", "add", f"{p}al", "type", "veth", "peer", "name", f"{p}ha")
    add(ip, "link", "add", f"{p}bl", "type", "veth", "peer", "name", f"{p}hb")
    add(ip, "link", "set", f"{p}ia", "netns", names["inet"])
    add(ip, "link", "set", f"{p}ib", "netns", names["inet"])
    add(ip, "link", "set", f"{p}ai", "netns", names["nat_a"])
    add(ip, "link", "set", f"{p}bi", "netns", names["nat_b"])
    add(ip, "link", "set", f"{p}al", "netns", names["nat_a"])
    add(ip, "link", "set", f"{p}ha", "netns", names["host"])
    add(ip, "link", "set", f"{p}bl", "netns", names["nat_b"])
    add(ip, "link", "set", f"{p}hb", "netns", names["guest"])

    ns(names["inet"], ip, "link", "add", "br-pub", "type", "bridge")
    ns(names["inet"], ip, "link", "set", "lo", "up")
    ns(names["inet"], ip, "link", "set", "br-pub", "up")
    ns(names["inet"], ip, "link", "set", f"{p}ia", "master", "br-pub")
    ns(names["inet"], ip, "link", "set", f"{p}ib", "master", "br-pub")
    ns(names["inet"], ip, "link", "set", f"{p}ia", "up")
    ns(names["inet"], ip, "link", "set", f"{p}ib", "up")
    ns(names["inet"], ip, "addr", "add", f"{INET_ADDR}/24", "dev", "br-pub")

    ns(names["nat_a"], ip, "link", "set", "lo", "up")
    ns(names["nat_a"], ip, "addr", "add", f"{NAT_A_WAN}/24", "dev", f"{p}ai")
    ns(names["nat_a"], ip, "link", "set", f"{p}ai", "up")
    ns(names["nat_a"], ip, "addr", "add", f"{NAT_A_LAN}/24", "dev", f"{p}al")
    ns(names["nat_a"], ip, "link", "set", f"{p}al", "up")
    ns(names["nat_a"], ip, "route", "add", "default", "via", INET_ADDR)
    ns(names["nat_a"], sysctl, "-w", "net.ipv4.ip_forward=1")
    ns(names["nat_a"], sysctl, "-w", "net.ipv4.conf.all.rp_filter=2")
    ns(
        names["nat_a"],
        iptables,
        "-t",
        "nat",
        "-A",
        "POSTROUTING",
        "-s",
        LAN_A,
        "-o",
        f"{p}ai",
        "-j",
        "MASQUERADE",
    )
    ns(
        names["nat_a"],
        iptables,
        "-A",
        "FORWARD",
        "-i",
        f"{p}al",
        "-o",
        f"{p}ai",
        "-j",
        "ACCEPT",
    )
    ns(
        names["nat_a"],
        iptables,
        "-A",
        "FORWARD",
        "-i",
        f"{p}ai",
        "-o",
        f"{p}al",
        "-m",
        "conntrack",
        "--ctstate",
        "ESTABLISHED,RELATED",
        "-j",
        "ACCEPT",
    )

    ns(names["nat_b"], ip, "link", "set", "lo", "up")
    ns(names["nat_b"], ip, "addr", "add", f"{NAT_B_WAN}/24", "dev", f"{p}bi")
    ns(names["nat_b"], ip, "link", "set", f"{p}bi", "up")
    ns(names["nat_b"], ip, "addr", "add", f"{NAT_B_LAN}/24", "dev", f"{p}bl")
    ns(names["nat_b"], ip, "link", "set", f"{p}bl", "up")
    ns(names["nat_b"], ip, "route", "add", "default", "via", INET_ADDR)
    ns(names["nat_b"], sysctl, "-w", "net.ipv4.ip_forward=1")
    ns(names["nat_b"], sysctl, "-w", "net.ipv4.conf.all.rp_filter=2")
    ns(
        names["nat_b"],
        iptables,
        "-t",
        "nat",
        "-A",
        "POSTROUTING",
        "-s",
        LAN_B,
        "-o",
        f"{p}bi",
        "-j",
        "MASQUERADE",
    )
    ns(
        names["nat_b"],
        iptables,
        "-A",
        "FORWARD",
        "-i",
        f"{p}bl",
        "-o",
        f"{p}bi",
        "-j",
        "ACCEPT",
    )
    ns(
        names["nat_b"],
        iptables,
        "-A",
        "FORWARD",
        "-i",
        f"{p}bi",
        "-o",
        f"{p}bl",
        "-m",
        "conntrack",
        "--ctstate",
        "ESTABLISHED,RELATED",
        "-j",
        "ACCEPT",
    )

    ns(names["host"], ip, "link", "set", "lo", "up")
    ns(names["host"], ip, "addr", "add", f"{HOST_ADDR}/24", "dev", f"{p}ha")
    ns(names["host"], ip, "link", "set", f"{p}ha", "up")
    ns(names["host"], ip, "route", "add", "default", "via", NAT_A_LAN)
    ns(names["guest"], ip, "link", "set", "lo", "up")
    ns(names["guest"], ip, "addr", "add", f"{GUEST_CROSS}/24", "dev", f"{p}hb")
    ns(names["guest"], ip, "link", "set", f"{p}hb", "up")
    ns(names["guest"], ip, "route", "add", "default", "via", NAT_B_LAN)
    return cmds


def netem_commands(spec: TopologySpec) -> list[list[str]]:
    if spec.case != "cross-network" or not spec.netem:
        return []
    ip = "ip"
    tc = "tc"
    names = ns_names(spec.prefix)
    p = spec.prefix
    cmds: list[list[str]] = []
    for nat, wan in ((names["nat_a"], f"{p}ai"), (names["nat_b"], f"{p}bi")):
        cmds.append(
            [
                ip,
                "netns",
                "exec",
                nat,
                tc,
                "qdisc",
                "add",
                "dev",
                wan,
                "root",
                "netem",
                "delay",
                NETEM_DELAY,
                NETEM_JITTER,
                "loss",
                NETEM_LOSS,
            ]
        )
    return cmds


def assert_no_port_forward(commands: list[list[str]]) -> None:
    joined = " ".join(" ".join(item) for item in commands)
    for banned in ("DNAT", "REDIRECT", "TPROXY", "--dport", "port-forward"):
        if banned in joined:
            raise TopologyError("The topology must not configure port forwarding.")


def _resolve(commands: list[list[str]]) -> list[list[str]]:
    mapping = {
        "ip": _bin("ip"),
        "iptables": _bin("iptables"),
        "tc": _bin("tc"),
        "sysctl": _bin("sysctl"),
    }
    resolved: list[list[str]] = []
    for argv in commands:
        row = [mapping.get(argv[0], argv[0]), *argv[1:]]
        for index, part in enumerate(row):
            if part in mapping:
                row[index] = mapping[part]
        resolved.append(row)
    return resolved


def apply_topology(spec: TopologySpec) -> AppliedTopology:
    commands = setup_commands(spec)
    assert_no_port_forward(commands)
    names = ns_names(spec.prefix)
    try:
        for argv in _resolve(commands):
            subprocess.run(argv, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        cleanup_topology(spec)
        raise TopologyError("Could not create the simulated networks.") from None
    host_addr = HOST_ADDR
    guest_addr = GUEST_LAN if spec.case == "same-lan" else GUEST_CROSS
    inet_ns = None if spec.case == "same-lan" else names["inet"]
    inet_addr = None if spec.case == "same-lan" else INET_ADDR
    rendezvous = None if spec.case == "same-lan" else RENDEZVOUS_TCP
    applied = AppliedTopology(
        spec=spec,
        host_ns=names["host"],
        guest_ns=names["guest"],
        inet_ns=inet_ns,
        host_addr=host_addr,
        guest_addr=guest_addr,
        inet_addr=inet_addr,
        rendezvous_tcp=rendezvous,
        commands=tuple(tuple(item) for item in commands),
    )
    return applied


def apply_netem(spec: TopologySpec) -> bool:
    commands = netem_commands(spec)
    if not commands:
        return False
    try:
        loader = _bin("modprobe")
    except TopologyError:
        loader = shutil.which("modprobe") or "/usr/sbin/modprobe"
    subprocess.run(
        [loader, "sch_netem"],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        for argv in _resolve(commands):
            subprocess.run(argv, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError, TopologyError):
        return False
    return True


def cleanup_topology(spec: TopologySpec) -> None:
    ip = _bin("ip")
    names = ns_names(spec.prefix)
    for name in names.values():
        subprocess.run(
            [ip, "netns", "del", name],
            check=False,
            capture_output=True,
            text=True,
        )
    leftover = subprocess.run(
        [ip, "link", "show"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout
    prefix = spec.prefix
    for token in leftover.split():
        if token.startswith(prefix) and ":" in token:
            iface = token.split("@", 1)[0].rstrip(":")
            subprocess.run(
                [ip, "link", "del", iface],
                check=False,
                capture_output=True,
                text=True,
            )
