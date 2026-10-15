#!/usr/bin/env bash
set -euo pipefail
apt-get update
KERNEL="$(uname -r)"
env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  iproute2 iptables iputils-ping kmod coturn \
  libegl1 libxkbcommon0 libdbus-1-3 libxcb-cursor0 libportaudio2 libpulse0
env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  "linux-modules-extra-${KERNEL}" \
  || env DEBIAN_FRONTEND=noninteractive apt-get install -y linux-modules-extra-azure \
  || true
systemctl stop coturn || true
systemctl disable coturn || true
systemctl mask coturn || true
modprobe sch_netem
lsmod | grep -E '^sch_netem'
sysctl -w net.ipv4.ip_forward=1
PROBE="wjxci$$"
ip netns add "$PROBE"
ip netns exec "$PROBE" true
ip netns delete "$PROBE"
command -v turnserver
