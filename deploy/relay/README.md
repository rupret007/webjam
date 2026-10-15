# Always Free TURN for WebJam Host/Join

Musicians do **zero setup**. The packaged sidecar fetches short-lived TURN
credentials from a public HTTPS URL compiled in at release (`-ldflags
-X github.com/rupret007/webjam/transport/internal/directstun.turnCredentialURL=…`).
Direct ICE is tried first; TURN is used only when that pair fails. The
compiled Host accepts on **both** the direct mux and the TURN listener at
once. Join probes the host's published candidates first, then dials the
host's relay candidate. The host does not wait for a guest reply before
accepting; Paste Reply is only the punch fallback when both sides are
behind hard NAT.

TURN credentials start in parallel with STUN gather. A LAN-only host
(no public mapping) does not wait for that fetch. The sidecar client
timeout is the Worker worst case (two 5s upstreams) plus 2s headroom.

The cross-network relay is **Cloudflare Realtime TURN** (key name
`webjam-relay`). A Cloudflare Worker mints `iceServers`. The TURN key ID
and API token are Worker secrets. They must never be committed or compiled
into the client.

Self-hosted coturn on Oracle Always Free remains available behind the same
`POST /turn-credentials` JSON interface.

## Cost stop ($0)

| Control | Bound | What happens at the cap |
| --- | --- | --- |
| Sidecar compiled quota | **256 MiB per session** | Session closes. Credential JSON may only *lower* this. |
| Cloudflare Worker | **800 GB / UTC month** of the **1,000 GB** Realtime TURN free allowance | HTTP 429; new sessions stay on direct ICE only. |
| Worker fail-closed | Usage analytics unreadable | HTTP 503; same as cap — no new credentials. |
| Coturn credential endpoint | **8 GiB/day** default | HTTP 429; new sessions stay on direct ICE only. |
| Oracle Always Free | **10 TB/month outbound**, 1 Ampere A1 VM | Stay on one VM, one public IPv4, no extra block volumes. |

### Cloudflare monthly cap formula

Cloudflare Realtime TURN counts **ingress + egress** bytes. The Worker
reads month-to-date usage for `TURN_KEY_ID` from the GraphQL analytics
API (`callsTurnUsageAdaptiveGroups`) and refuses new credentials when:

```
usage_bytes = sum(ingressBytes) + sum(egressBytes)
              over the UTC calendar month [first-of-month, today]
              filtered by keyId = TURN_KEY_ID

cap_bytes   = TURN_MONTHLY_CAP_GB * 1_000_000_000
              (default 800; free allowance is 1,000 GB)

refuse when usage_bytes >= cap_bytes
         or the analytics query errors / cannot be parsed
```

1 GB = 1,000,000,000 bytes (Cloudflare billing). 800 GB leaves 200 GB of
the free tier for in-flight allocations and analytics lag (~30 seconds).
`TURN_MONTHLY_CAP_GB` is a Worker var; secrets stay out of the repo.

Exact-pair policy: the sidecar CreatePermission/dials only the peer's
sealed relay candidate, and only when that IPv4 matches the credentialed
TURN server (or `allowed_relay_ips`). Stock TURN auth alone is not enough;
see `docs/security/remote-session-threat-model.md`.

## Cloudflare Worker (current relay)

Free-plan Worker at `deploy/relay/cloudflare-worker/`.
`POST /turn-credentials` requires a WebJam session/invite **token shape**
(canonical UUID or 64-hex `session_id`, plus `invite_token` matching
`^[A-Za-z0-9_-]{32,256}$`) and rate-limits per IP.

### Deploy

From a machine that can reach Cloudflare (secrets are typed at the prompt,
never stored in git):

```
cd deploy/relay/cloudflare-worker
npx wrangler login
npx wrangler secret put TURN_KEY_ID
npx wrangler secret put TURN_KEY_API_TOKEN
npx wrangler secret put CLOUDFLARE_ACCOUNT_ID
npx wrangler secret put CLOUDFLARE_ANALYTICS_API_TOKEN
npx wrangler deploy
```

`TURN_KEY_API_TOKEN` is the Realtime TURN key token. It can mint
`generate-ice-servers` and is **rejected** by GraphQL (code 10000).
`CLOUDFLARE_ANALYTICS_API_TOKEN` is a required account API token with
**Account Analytics Read**. The Worker uses it only for the usage query.
If any of the four secrets is missing, the Worker returns 503
`misconfigured`. GraphQL and `generate-ice-servers` each have a 5-second
outbound timeout and fail closed.

Required permissions:

1. TURN key token: `POST https://rtc.live.cloudflare.com/v1/turn/keys/$TURN_KEY_ID/credentials/generate-ice-servers`
2. Account token: `POST https://api.cloudflare.com/client/v4/graphql` with
   **Account Analytics Read** (`callsTurnUsageAdaptiveGroups`)

TTL is 600 seconds so a minted credential cannot overshoot the monthly
cap for long. Confirm the deployed URL, then release-build the
sidecar with that path only:

```
go build -ldflags "-X github.com/rupret007/webjam/transport/internal/directstun.turnCredentialURL=https://webjam-turn-credentials.<account>.workers.dev/turn-credentials" ./cmd/webjam-fabric
```

Do not put `TURN_KEY_ID`, `TURN_KEY_API_TOKEN`,
`CLOUDFLARE_ANALYTICS_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, or
`TURN_STATIC_AUTH_SECRET` in ldflags, settings, or the GitHub repo.

### Remaining human steps

1. Confirm the `webjam-relay` TURN key exists in the Cloudflare dashboard
   (Realtime → TURN) and that the account is on the **free** 1,000 GB/month
   allowance with no paid TURN add-on.
2. Put the TURN key ID and TURN key API token with `wrangler secret put`
   (never in the repo).
3. Create a **separate** account API token with **Account Analytics Read**
   and `wrangler secret put CLOUDFLARE_ANALYTICS_API_TOKEN`. A TURN key
   token cannot query GraphQL (code 10000).
4. Put `CLOUDFLARE_ACCOUNT_ID` the same way.
5. `npx wrangler deploy` and copy the `*.workers.dev` (or custom domain)
   URL into the release `turnCredentialURL` ldflag.
6. Optional: custom hostname in front of the Worker. HTTPS only.
7. Coturn switchback: point the same ldflag at the Oracle credential
   URL (`https://turn-creds.example.com/turn-credentials`) instead. The
   sidecar JSON is unchanged.

## Oracle Cloud Always Free (coturn switchback)

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
go build -ldflags "-X github.com/rupret007/webjam/transport/internal/directstun.turnCredentialURL=https://turn-creds.example.com/turn-credentials" ./cmd/webjam-fabric
```

Confirm Always Free: one A1 VM, 1 OCPU, no paid load balancer, outbound
well under 10 TB/month (the daily credential budget enforces this).

## Harness

The two-NAT harness runs coturn in the public namespace with a per-run
secret. Packaged apps never see `WEBJAM_TEST_TURN_SECRET`.
