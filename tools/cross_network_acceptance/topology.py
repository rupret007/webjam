"""Two-home netns. No DNAT, NETMAP, REDIRECT, or port forwarding.

RFC 4787: masquerade/cone = endpoint-independent mapping +
address-and-port-dependent filtering. Not endpoint-independent
filtering (static inbound rewrite / NETMAP). symmetric =
endpoint-dependent mapping on NAT-A (`--random-fully`). These modes
do not prove product cone-vs-symmetric NAT behavior while packaged
direct-stun discards TEST-NET-3 mappings.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

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
# RFC 4787: (mapping behavior, filtering behavior)
RFC4787 = {
    "masquerade": ("endpoint-independent", "address-and-port-dependent"),
    "cone": ("endpoint-independent", "address-and-port-dependent"),
    "symmetric": ("endpoint-dependent", "address-and-port-dependent"),
}


class TopologyError(RuntimeError):
    pass


def required_bin(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for folder in ("/usr/sbin", "/sbin", "/usr/bin"):
        candidate = Path(folder) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise TopologyError(f"This harness needs {name} on PATH or /usr/sbin.")


@dataclass(frozen=True)
class TopologySpec:
    case: str
    prefix: str
    netem: bool = False
    nat_mode: str = "masquerade"

    def __post_init__(self) -> None:
        if self.case not in {"same-lan", "cross-network"}:
            raise TopologyError("Case must be same-lan or cross-network.")
        token = str(self.prefix or "")
        if not token.isalnum() or not 3 <= len(token) <= 6:
            raise TopologyError("Prefix must be a short alphanumeric token.")
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
    inet_addr: str | None


def ns_names(prefix: str) -> dict[str, str]:
    return {
        "inet": f"{prefix}-inet", "nat_a": f"{prefix}-na", "nat_b": f"{prefix}-nb",
        "host": f"{prefix}-host", "guest": f"{prefix}-gst",
    }


def setup_commands(spec: TopologySpec) -> list[list[str]]:

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
        addr_up(names["host"], f"{p}hg", f"{HOST_ADDR}/24")
        addr_up(names["guest"], f"{p}gh", f"{GUEST_LAN}/24")
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
    ns(inet, ip, "addr", "add", f"{INET_ADDR}/24", "dev", "br-pub")

    def configure_nat(ns_name: str, wan_dev: str, wan_addr: str,
                      lan_dev: str, lan_addr: str, lan_net: str, side: str) -> None:
        ns(ns_name, ip, "link", "set", "lo", "up")
        ns(ns_name, ip, "addr", "add", f"{wan_addr}/24", "dev", wan_dev)
        ns(ns_name, ip, "link", "set", wan_dev, "up")
        ns(ns_name, ip, "addr", "add", f"{lan_addr}/24", "dev", lan_dev)
        ns(ns_name, ip, "link", "set", lan_dev, "up")
        ns(ns_name, ip, "route", "add", "default", "via", INET_ADDR)
        ns(ns_name, sysctl, "-w", "net.ipv4.ip_forward=1")
        ns(ns_name, sysctl, "-w", "net.ipv4.conf.all.rp_filter=2")
        masquerade = [
            iptables, "-t", "nat", "-A", "POSTROUTING",
            "-s", lan_net, "-o", wan_dev, "-j", "MASQUERADE",
        ]
        if spec.nat_mode == "symmetric" and side == "a":
            masquerade.append("--random-fully")
        ns(ns_name, *masquerade)
        ns(ns_name, iptables, "-A", "FORWARD", "-i", lan_dev, "-o", wan_dev, "-j", "ACCEPT")
        ns(
            ns_name, iptables, "-A", "FORWARD", "-i", wan_dev, "-o", lan_dev,
            "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT",
        )
        ns(ns_name, iptables, "-A", "FORWARD", "-i", wan_dev, "-o", lan_dev, "-j", "DROP")

    configure_nat(names["nat_a"], f"{p}ai", NAT_A_WAN, f"{p}al", NAT_A_LAN, LAN_A, "a")
    configure_nat(names["nat_b"], f"{p}bi", NAT_B_WAN, f"{p}bl", NAT_B_LAN, LAN_B, "b")
    addr_up(names["host"], f"{p}ha", f"{HOST_ADDR}/24", NAT_A_LAN)
    addr_up(names["guest"], f"{p}hb", f"{GUEST_CROSS}/24", NAT_B_LAN)
    return cmds


def netem_commands(spec: TopologySpec) -> list[list[str]]:
    if spec.case != "cross-network" or not spec.netem:
        return []
    names = ns_names(spec.prefix)
    p = spec.prefix
    return [
        [
            "ip", "netns", "exec", nat, "tc", "qdisc", "add", "dev", wan,
            "root", "netem", "delay", NETEM_DELAY, NETEM_JITTER, "loss", NETEM_LOSS,
        ]
        for nat, wan in ((names["nat_a"], f"{p}ai"), (names["nat_b"], f"{p}bi"))
    ]


def assert_no_port_forward(commands: list[list[str]]) -> None:
    joined = " ".join(" ".join(item) for item in commands)
    for banned in ("DNAT", "REDIRECT", "TPROXY", "--dport", "port-forward"):
        if banned in joined:
            raise TopologyError("No port forwarding.")


def assert_unsolicited_inbound_udp_dropped(commands: list[list[str]]) -> None:
    """APDF: no static inbound rewrite; NEW UDP is not forwarded."""

    for argv in commands:
        if "NETMAP" in argv or "DNAT" in argv:
            raise TopologyError("Inbound rewrite is endpoint-independent filtering.")
        if (
            "FORWARD" in argv and "ACCEPT" in argv and "udp" in argv
            and "conntrack" not in argv
        ):
            raise TopologyError("Unsolicited inbound UDP must not be forwarded.")


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


def stun_override_hosts_text() -> str:

    names = " ".join(COMPILED_STUN_NAMES)
    return f"127.0.0.1 localhost\n{INET_ADDR} {names}\n"


def install_stun_name_override(spec: TopologySpec) -> None:
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
    assert_unsolicited_inbound_udp_dropped(commands)
    names = ns_names(spec.prefix)
    try:
        for argv in _resolve(commands):
            subprocess.run(argv, check=True, capture_output=True, text=True)
        install_stun_name_override(spec)
    except subprocess.CalledProcessError:
        cleanup_topology(spec)
        raise TopologyError("Could not create the simulated networks.") from None
    inet_ns = None if spec.case == "same-lan" else names["inet"]
    inet_addr = None if spec.case == "same-lan" else INET_ADDR
    return AppliedTopology(
        spec=spec, host_ns=names["host"], guest_ns=names["guest"],
        inet_ns=inet_ns, inet_addr=inet_addr,
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
