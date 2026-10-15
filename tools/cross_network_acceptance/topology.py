"""Linux netns + iptables MASQUERADE topology for two simulated homes.

Cross-network: host behind NAT-A, guest behind NAT-B, a public internet
namespace between them. No DNAT, REDIRECT, or port forwarding.

Same-LAN control: host and guest share one L2 with RFC1918 addresses.

NAT modes for the two-home case:

- masquerade: original restricted-cone MASQUERADE (v2 LAN expected FAIL).
- cone: MASQUERADE plus a port-preserving UDP NETMAP reverse onto the
  actual LAN host or guest address (/32). That is full cone / 1:1 for
  the single endpoint, not a product port-forward of a service port.
  Inbound guest traffic must land on 192.168.120.10, not a WAN host-bit
  rewrite to 192.168.120.20.
- symmetric: NAT-A uses MASQUERADE --random-fully (random port mapping);
  NAT-B stays cone. A guest connect here is a fake connect and must fail
  the job. These modes do not prove product cone-vs-symmetric NAT
  behavior while packaged direct-stun discards TEST-NET-3 mappings.
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
STUN_UDP_ALT = 19302
NETEM_DELAY = "100ms"
NETEM_JITTER = "50ms"
NETEM_LOSS = "1%"
NAT_MODES = frozenset({"masquerade", "cone", "symmetric"})
COMPILED_STUN_NAMES = ("stun.cloudflare.com", "stun.l.google.com")


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
    nat_mode: str = "masquerade"

    def __post_init__(self) -> None:
        if self.case not in {"same-lan", "cross-network"}:
            raise TopologyError("Topology case must be same-lan or cross-network.")
        token = str(self.prefix or "")
        if not token.isalnum() or not 3 <= len(token) <= 6:
            raise TopologyError("Topology prefix must be a short alphanumeric token.")
        mode = str(self.nat_mode or "masquerade")
        if mode not in NAT_MODES:
            raise TopologyError("NAT mode must be masquerade, cone, or symmetric.")
        if self.case == "same-lan" and mode != "masquerade":
            raise TopologyError("Same-LAN does not use a NAT mode.")


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


def _full_cone_inbound(spec: TopologySpec, side: str) -> bool:
    if spec.nat_mode == "cone":
        return True
    if spec.nat_mode == "symmetric":
        return side == "b"
    return False


def cone_inbound_map_to(side: str) -> str:
    """NETMAP destination for inbound UDP. Preserve the real LAN endpoint.

    Mapping ``203.0.113.20/24`` onto ``192.168.120.0/24`` keeps WAN host
    bits and delivers to 192.168.120.20, which is not the guest.
    """

    if side == "a":
        return f"{HOST_ADDR}/32"
    if side == "b":
        return f"{GUEST_CROSS}/32"
    raise TopologyError("Cone inbound side must be a or b.")


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

    def configure_nat(
        *,
        ns_name: str,
        wan_dev: str,
        wan_addr: str,
        lan_dev: str,
        lan_addr: str,
        lan_net: str,
        side: str,
    ) -> None:
        ns(ns_name, ip, "link", "set", "lo", "up")
        ns(ns_name, ip, "addr", "add", f"{wan_addr}/24", "dev", wan_dev)
        ns(ns_name, ip, "link", "set", wan_dev, "up")
        ns(ns_name, ip, "addr", "add", f"{lan_addr}/24", "dev", lan_dev)
        ns(ns_name, ip, "link", "set", lan_dev, "up")
        ns(ns_name, ip, "route", "add", "default", "via", INET_ADDR)
        ns(ns_name, sysctl, "-w", "net.ipv4.ip_forward=1")
        ns(ns_name, sysctl, "-w", "net.ipv4.conf.all.rp_filter=2")
        masquerade = [
            iptables,
            "-t",
            "nat",
            "-A",
            "POSTROUTING",
            "-s",
            lan_net,
            "-o",
            wan_dev,
            "-j",
            "MASQUERADE",
        ]
        if spec.nat_mode == "symmetric" and side == "a":
            masquerade.append("--random-fully")
        ns(ns_name, *masquerade)
        ns(
            ns_name,
            iptables,
            "-A",
            "FORWARD",
            "-i",
            lan_dev,
            "-o",
            wan_dev,
            "-j",
            "ACCEPT",
        )
        ns(
            ns_name,
            iptables,
            "-A",
            "FORWARD",
            "-i",
            wan_dev,
            "-o",
            lan_dev,
            "-m",
            "conntrack",
            "--ctstate",
            "ESTABLISHED,RELATED",
            "-j",
            "ACCEPT",
        )
        if _full_cone_inbound(spec, side):
            ns(
                ns_name,
                iptables,
                "-t",
                "nat",
                "-A",
                "PREROUTING",
                "-i",
                wan_dev,
                "-p",
                "udp",
                "-j",
                "NETMAP",
                "--to",
                cone_inbound_map_to(side),
            )
            ns(
                ns_name,
                iptables,
                "-A",
                "FORWARD",
                "-i",
                wan_dev,
                "-o",
                lan_dev,
                "-p",
                "udp",
                "-j",
                "ACCEPT",
            )

    configure_nat(
        ns_name=names["nat_a"],
        wan_dev=f"{p}ai",
        wan_addr=NAT_A_WAN,
        lan_dev=f"{p}al",
        lan_addr=NAT_A_LAN,
        lan_net=LAN_A,
        side="a",
    )
    configure_nat(
        ns_name=names["nat_b"],
        wan_dev=f"{p}bi",
        wan_addr=NAT_B_WAN,
        lan_dev=f"{p}bl",
        lan_addr=NAT_B_LAN,
        lan_net=LAN_B,
        side="b",
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


def stun_override_hosts_text() -> str:
    """Test-only /etc/netns hosts body. Never used by packaged builds."""

    names = " ".join(COMPILED_STUN_NAMES)
    return f"127.0.0.1 localhost\n{INET_ADDR} {names}\n"


def install_stun_name_override(spec: TopologySpec) -> None:
    """Point #372's compiled STUN names at the public-ns STUN server.

    PR #372 has no STUN env/config hook: stunServers is compiled in
    transport/internal/directstun/path.go and the sidecar is started with
    an empty environment. Packaged apps never see these netns hosts files.
    """

    if spec.case != "cross-network":
        return
    body = stun_override_hosts_text()
    for name in ns_names(spec.prefix).values():
        folder = Path("/etc/netns") / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "hosts"
        path.write_text(body, encoding="utf-8")
        path.chmod(0o644)


def remove_stun_name_override(spec: TopologySpec) -> None:
    for name in ns_names(spec.prefix).values():
        folder = Path("/etc/netns") / name
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)


def apply_topology(spec: TopologySpec) -> AppliedTopology:
    commands = setup_commands(spec)
    assert_no_port_forward(commands)
    names = ns_names(spec.prefix)
    try:
        for argv in _resolve(commands):
            subprocess.run(argv, check=True, capture_output=True, text=True)
        install_stun_name_override(spec)
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
    remove_stun_name_override(spec)
