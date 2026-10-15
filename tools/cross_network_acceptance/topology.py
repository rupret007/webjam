"""Two-home netns. Cone: explicit SNAT. Symmetric: --random-fully + probe.
Routers default-DROP INPUT/FORWARD; ESTABLISHED,RELATED only inbound. No REJECT/ICMP.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from tools.cross_network_acceptance import HarnessError

COMPILED_STUN_NAMES = ("stun.cloudflare.com", "stun.l.google.com")
NAT_MODES = frozenset({"cone", "symmetric"})
RFC4787 = {
    "cone": ("endpoint-independent", "address-and-port-dependent"),
    "symmetric": ("endpoint-dependent", "address-and-port-dependent"),
}
NETEM_DELAY, NETEM_JITTER, NETEM_LOSS = "100ms", "50ms", "1%"
RENDEZVOUS_TCP, RENDEZVOUS_UDP = 47131, 47132
STUN_UDP, STUN_UDP_ALT = 3478, 19302


class TopologyError(HarnessError):
    pass


def required_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for folder in ("/usr/sbin", "/sbin", "/usr/bin"):
        candidate = Path(folder) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise TopologyError(f"Need {name}.")


def addrs(salt: int) -> dict[str, str]:
    s = int(salt)
    if not 1 <= s <= 220:
        raise TopologyError("salt out of range")
    return {
        "inet": f"27.{s}.0.1",
        "nat_a_wan": f"27.{s}.0.10",
        "nat_b_wan": f"27.{s}.0.20",
        "lan_a": f"10.{s}.10.0/24",
        "nat_a_lan": f"10.{s}.10.1",
        "host": f"10.{s}.10.10",
        "lan_b": f"10.{s}.20.0/24",
        "nat_b_lan": f"10.{s}.20.1",
        "guest_cross": f"10.{s}.20.10",
        "guest_lan": f"10.{s}.10.11",
    }


@dataclass(frozen=True)
class TopologySpec:
    case: str
    prefix: str
    salt: int = 1
    netem: bool = False
    nat_mode: str = "cone"

    def __post_init__(self) -> None:
        if self.case not in {"same-lan", "cone", "relay"}:
            raise TopologyError("Case must be same-lan, cone, or relay.")
        token = str(self.prefix or "")
        if not token.isalnum() or not 3 <= len(token) <= 6:
            raise TopologyError("Prefix must be alphanumeric.")
        addrs(self.salt)
        if self.case == "same-lan":
            return
        if self.nat_mode not in NAT_MODES:
            raise TopologyError("NAT mode must be cone or symmetric.")
        if self.case == "cone" and self.nat_mode != "cone":
            raise TopologyError("cone case needs cone NAT.")
        if self.case == "relay" and self.nat_mode != "symmetric":
            raise TopologyError("relay case needs symmetric NAT.")

    @property
    def a(self) -> dict[str, str]:
        return addrs(self.salt)


@dataclass(frozen=True)
class AppliedTopology:
    spec: TopologySpec
    host_ns: str
    guest_ns: str
    inet_ns: str | None
    inet_addr: str | None
    host_addr: str
    guest_addr: str
    nat_a_wan: str
    nat_b_wan: str


def ns_names(prefix: str) -> dict[str, str]:
    return {
        "inet": f"{prefix}-inet", "nat_a": f"{prefix}-na", "nat_b": f"{prefix}-nb",
        "host": f"{prefix}-host", "guest": f"{prefix}-gst",
    }


def setup_commands(spec: TopologySpec) -> list[list[str]]:
    ip, iptables, sysctl = "ip", "iptables", "sysctl"
    names, p, a = ns_names(spec.prefix), spec.prefix, spec.a
    cmds: list[list[str]] = []

    def add(*parts: str) -> None:
        cmds.append(list(parts))

    def ns(name: str, *parts: str) -> None:
        add(ip, "netns", "exec", name, *parts)

    def veth(left: str, right: str, left_ns: str, right_ns: str) -> None:
        add(ip, "link", "add", left, "type", "veth", "peer", "name", right)
        add(ip, "link", "set", left, "netns", left_ns)
        add(ip, "link", "set", right, "netns", right_ns)

    def addr_up(name: str, dev: str, addr: str, via: str = "") -> None:
        ns(name, ip, "link", "set", "lo", "up")
        ns(name, ip, "addr", "add", addr, "dev", dev)
        ns(name, ip, "link", "set", dev, "up")
        if via:
            ns(name, ip, "route", "add", "default", "via", via)
        else:
            ns(name, ip, "route", "add", "default", "dev", dev)

    if spec.case == "same-lan":
        add(ip, "netns", "add", names["host"])
        add(ip, "netns", "add", names["guest"])
        veth(f"{p}hg", f"{p}gh", names["host"], names["guest"])
        addr_up(names["host"], f"{p}hg", f"{a['host']}/24")
        addr_up(names["guest"], f"{p}gh", f"{a['guest_lan']}/24")
        return cmds

    for key in ("inet", "nat_a", "nat_b", "host", "guest"):
        add(ip, "netns", "add", names[key])
    veth(f"{p}ia", f"{p}ai", names["inet"], names["nat_a"])
    veth(f"{p}ib", f"{p}bi", names["inet"], names["nat_b"])
    veth(f"{p}al", f"{p}ha", names["nat_a"], names["host"])
    veth(f"{p}bl", f"{p}hb", names["nat_b"], names["guest"])
    inet = names["inet"]
    ns(inet, ip, "link", "add", "br-pub", "type", "bridge")
    ns(inet, ip, "link", "set", "lo", "up")
    ns(inet, ip, "link", "set", "br-pub", "up")
    for dev in (f"{p}ia", f"{p}ib"):
        ns(inet, ip, "link", "set", dev, "master", "br-pub")
        ns(inet, ip, "link", "set", dev, "up")
    ns(inet, ip, "addr", "add", f"{a['inet']}/24", "dev", "br-pub")

    def configure_nat(
        ns_name: str, wan_dev: str, wan_addr: str, lan_dev: str, lan_addr: str,
        lan_net: str,
    ) -> None:
        ns(ns_name, ip, "link", "set", "lo", "up")
        ns(ns_name, ip, "addr", "add", f"{wan_addr}/24", "dev", wan_dev)
        ns(ns_name, ip, "link", "set", wan_dev, "up")
        ns(ns_name, ip, "addr", "add", f"{lan_addr}/24", "dev", lan_dev)
        ns(ns_name, ip, "link", "set", lan_dev, "up")
        ns(ns_name, ip, "route", "add", "default", "via", a["inet"])
        ns(ns_name, sysctl, "-w", "net.ipv4.ip_forward=1")
        ns(ns_name, sysctl, "-w", "net.ipv4.conf.all.rp_filter=2")
        ns(ns_name, iptables, "-P", "INPUT", "DROP")
        ns(ns_name, iptables, "-P", "FORWARD", "DROP")
        ns(ns_name, iptables, "-A", "INPUT", "-i", "lo", "-j", "ACCEPT")
        ns(ns_name, iptables, "-A", "INPUT", "-m", "conntrack",
           "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT")
        ns(ns_name, iptables, "-A", "INPUT", "-i", lan_dev, "-j", "ACCEPT")
        ns(ns_name, iptables, "-A", "FORWARD", "-m", "conntrack",
           "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT")
        ns(ns_name, iptables, "-A", "FORWARD", "-i", lan_dev, "-o", wan_dev, "-j", "ACCEPT")
        if spec.nat_mode == "cone":
            ns(ns_name, iptables, "-t", "nat", "-A", "POSTROUTING", "-s", lan_net,
               "-o", wan_dev, "-j", "SNAT", "--to-source", wan_addr)
        else:
            ns(ns_name, iptables, "-t", "nat", "-A", "POSTROUTING", "-s", lan_net,
               "-o", wan_dev, "-j", "SNAT", "--to-source", wan_addr, "--random-fully")

    configure_nat(names["nat_a"], f"{p}ai", a["nat_a_wan"], f"{p}al", a["nat_a_lan"],
                  a["lan_a"])
    configure_nat(names["nat_b"], f"{p}bi", a["nat_b_wan"], f"{p}bl", a["nat_b_lan"],
                  a["lan_b"])
    addr_up(names["host"], f"{p}ha", f"{a['host']}/24", a["nat_a_lan"])
    addr_up(names["guest"], f"{p}hb", f"{a['guest_cross']}/24", a["nat_b_lan"])
    return cmds


def netem_commands(spec: TopologySpec) -> list[list[str]]:
    if spec.case == "same-lan" or not spec.netem:
        return []
    names, p = ns_names(spec.prefix), spec.prefix
    return [
        ["ip", "netns", "exec", nat, "tc", "qdisc", "add", "dev", wan,
         "root", "netem", "delay", NETEM_DELAY, NETEM_JITTER, "loss", NETEM_LOSS]
        for nat, wan in ((names["nat_a"], f"{p}ai"), (names["nat_b"], f"{p}bi"))
    ]


def assert_no_port_forward(commands: list[list[str]]) -> None:
    joined = " ".join(" ".join(item) for item in commands)
    for banned in ("DNAT", "REDIRECT", "TPROXY", "--dport", "port-forward", "REJECT", "ICMP"):
        if banned in joined:
            raise TopologyError("No port forwarding or REJECT/ICMP.")


def assert_unsolicited_inbound_udp_dropped(commands: list[list[str]]) -> None:
    for argv in commands:
        if "NETMAP" in argv or "DNAT" in argv:
            raise TopologyError("Inbound rewrite is endpoint-independent filtering.")
        if "FORWARD" in argv and "ACCEPT" in argv and "udp" in argv and "conntrack" not in argv:
            raise TopologyError("Unsolicited inbound UDP must not be forwarded.")


def assert_router_drop(commands: list[list[str]]) -> None:
    text = " ".join(" ".join(item) for item in commands)
    if "REJECT" in text or "ICMP" in text:
        raise TopologyError("No REJECT/ICMP.")
    if text.count("-P INPUT DROP") < 2 or text.count("-P FORWARD DROP") < 2:
        raise TopologyError("Routers must default-DROP INPUT/FORWARD.")
    if "ESTABLISHED,RELATED" not in text:
        raise TopologyError("Need ESTABLISHED,RELATED allow.")


def _resolve(commands: list[list[str]]) -> list[list[str]]:
    mapping = {
        "ip": required_bin("ip"), "iptables": required_bin("iptables"),
        "tc": required_bin("tc"), "sysctl": required_bin("sysctl"),
    }
    resolved: list[list[str]] = []
    for argv in commands:
        row = [mapping.get(argv[0], argv[0]), *argv[1:]]
        for index, part in enumerate(row):
            if part in mapping:
                row[index] = mapping[part]
        resolved.append(row)
    return resolved


def stun_override_hosts_text(inet: str) -> str:
    names = " ".join(COMPILED_STUN_NAMES)
    return f"127.0.0.1 localhost\n{inet} {names}\n"


def install_stun_name_override(spec: TopologySpec) -> None:
    if spec.case == "same-lan":
        return
    body = stun_override_hosts_text(spec.a["inet"])
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
    if spec.case != "same-lan":
        assert_unsolicited_inbound_udp_dropped(commands)
        assert_router_drop(commands)
    names, a = ns_names(spec.prefix), spec.a
    try:
        for argv in _resolve(commands):
            subprocess.run(argv, check=True, capture_output=True, text=True)
        install_stun_name_override(spec)
    except subprocess.CalledProcessError:
        cleanup_topology(spec)
        raise TopologyError("Could not create networks.") from None
    inet_ns = None if spec.case == "same-lan" else names["inet"]
    guest = a["guest_lan"] if spec.case == "same-lan" else a["guest_cross"]
    return AppliedTopology(
        spec=spec, host_ns=names["host"], guest_ns=names["guest"],
        inet_ns=inet_ns, inet_addr=None if inet_ns is None else a["inet"],
        host_addr=a["host"], guest_addr=guest,
        nat_a_wan=a["nat_a_wan"], nat_b_wan=a["nat_b_wan"],
    )


def apply_netem(spec: TopologySpec) -> bool:
    commands = netem_commands(spec)
    if not commands:
        return False
    try:
        loader = required_bin("modprobe")
    except TopologyError:
        loader = shutil.which("modprobe") or "/usr/sbin/modprobe"
    subprocess.run([loader, "sch_netem"], check=False, capture_output=True, text=True)
    try:
        for argv in _resolve(commands):
            subprocess.run(argv, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError, TopologyError):
        return False
    return True


def cleanup_topology(spec: TopologySpec) -> None:
    ip = required_bin("ip")
    for name in ns_names(spec.prefix).values():
        subprocess.run([ip, "netns", "del", name], check=False, capture_output=True, text=True)
    leftover = subprocess.run(
        [ip, "link", "show"], check=False, capture_output=True, text=True,
    ).stdout
    prefix = spec.prefix
    for token in leftover.split():
        if token.startswith(prefix) and ":" in token:
            iface = token.split("@", 1)[0].rstrip(":")
            subprocess.run(
                [ip, "link", "del", iface], check=False, capture_output=True, text=True,
            )
    remove_stun_name_override(spec)
