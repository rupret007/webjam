# Firewall ports for the Always Free TURN VM

Open only these on the Oracle Security List **and** the guest firewall
(`iptables`/`firewalld`). Do not add SSH to the public security list from
the whole Internet if you can use a Bastion or a single admin CIDR.

| Direction | Protocol | Ports | Why |
| --- | --- | --- | --- |
| Ingress | UDP | 3478 | STUN/TURN allocate |
| Ingress | UDP | 49152–49351 | Relayed media (keep this range small) |
| Ingress | TCP | 443 | Optional HTTPS for the credential endpoint behind Caddy/nginx |
| Ingress | TCP | 22 | SSH from your admin CIDR only |
| Egress | UDP | 3478, 49152–49351 | TURN replies and peer permissions |
| Egress | TCP | 443 | Optional Cloudflare ICE-servers fetch |

Do **not** open TCP 3478, TLS/DTLS 5349, or a wide 10000–65535 relay range.
WebJam's sidecar uses UDP TURN only.

Example iptables (replace `ens3` with the VNIC):

```
iptables -A INPUT -p udp --dport 3478 -j ACCEPT
iptables -A INPUT -p udp --dport 49152:49351 -j ACCEPT
iptables -A INPUT -p tcp --dport 443 -j ACCEPT
iptables -A INPUT -p tcp --dport 22 -s ADMIN_CIDR -j ACCEPT
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -P INPUT DROP
```
