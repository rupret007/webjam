# Always Free TURN for WebJam Host/Join

Musicians do **zero setup**. The packaged sidecar fetches short-lived TURN
credentials from a public HTTPS URL compiled in at release (`-ldflags
-X github.com/rupret007/webjam/transport/internal/directstun.turnCredentialURL=…`).
The long-lived coturn `static-auth-secret` (or Cloudflare API token) stays on
this VM. Direct ICE is tried first; TURN is used only when that pair fails.

## Cost stop ($0)

| Control | Bound | What happens at the cap |
| --- | --- | --- |
| Sidecar compiled quota | **256 MiB per session** | Session closes. Credential JSON may only *lower* this. |
| Credential endpoint | **8 GiB/day** default | HTTP 429; new sessions stay on direct ICE only. |
| Oracle Always Free | **10 TB/month outbound**, 1 Ampere A1 VM | Stay on one VM, one public IPv4, no extra block volumes. |
| Cloudflare Realtime TURN | Use **only** with a **$0 spend cap** | If Cloudflare cannot cap spend at $0, do not enable it. |

8 GiB/day is about 240 GiB/month, far under 10 TB. A music session at
~300 kbps uses the 256 MiB cap in well under two hours and then stops.

## Tradeoff

- **Self-hosted coturn on Oracle Always Free Ampere A1:** $0 if you stay
  inside Always Free. You operate a VM, a secret, and HTTPS for the
  credential URL. This is the default.
- **Cloudflare Realtime TURN:** same sidecar JSON. The credential service
  holds the Cloudflare token and returns `iceServers`. Enable only with a
  $0 spend cap. Extra `allowed_relay_ips` may be required if allocations
  are not the TURN hostname.

Exact-pair policy: the sidecar CreatePermission/dials only the peer's
sealed relay candidate, and only when that IPv4 matches the credentialed
TURN server (or `allowed_relay_ips`). Stock TURN auth alone is not enough;
see `docs/security/remote-session-threat-model.md`.

## Oracle Cloud Always Free (ARM Ampere A1)

Stay within the Always Free shape so this cannot convert to paid:

1. Sign in to Oracle Cloud. Create or use an Always Free tenancy in a
   region that still has Ampere A1 capacity.
2. Compute → Create instance:
   - Image: **Ubuntu 22.04** (aarch64)
   - Shape: **VM.Standard.A1.Flex**, **1 OCPU**, **6 GB RAM**
     (do not take the full 4 OCPU / 24 GB; leave headroom in the tenancy)
   - Networking: one VCN, one public subnet, **one public IPv4** (the first
     is free). No extra VNICs, no extra block volumes.
   - SSH key: your admin key.
3. Note the public IPv4. Open the VCN security list for the ports in
   `firewall.md` (UDP 3478, UDP 49152–49351, TCP 443, SSH from your CIDR).
4. SSH in and install:

```
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y coturn python3 caddy
sudo mkdir -p /etc/webjam-turn /opt/webjam-turn /var/log/webjam-turn
sudo cp coturn.conf /etc/webjam-turn/coturn.conf
sudo cp turn_credentials.py /opt/webjam-turn/turn_credentials.py
sudo cp webjam-turn.service webjam-turn-credentials.service /etc/systemd/system/
install -m 0600 secret.env.example /tmp/secret.env
# edit TURN_STATIC_AUTH_SECRET (openssl rand -hex 32) and TURN_EXTERNAL_IP
sudo mv /tmp/secret.env /etc/webjam-turn/secret.env
sudo chown turnserver:turnserver /etc/webjam-turn/secret.env
sudo systemctl daemon-reload
sudo systemctl enable --now webjam-turn webjam-turn-credentials
```

5. Put HTTPS in front of `127.0.0.1:8787` with Caddy (Let's Encrypt on the
   public IPv4 / DNS name). Example Caddyfile:

```
turn-creds.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

6. Release-build the sidecar with that URL only:

```
go build -ldflags "-X github.com/rupret007/webjam/transport/internal/directstun.turnCredentialURL=https://turn-creds.example.com/credentials" ./cmd/...
```

Do not put `TURN_STATIC_AUTH_SECRET` or a Cloudflare token in ldflags,
settings, or the GitHub repo.

Confirm Always Free: one A1 VM, 1 OCPU, no paid load balancer, outbound
well under 10 TB/month (the daily credential budget enforces this).

## Harness

The two-NAT harness runs coturn in the public namespace with a per-run
secret. Packaged apps never see `WEBJAM_TEST_TURN_SECRET`.
