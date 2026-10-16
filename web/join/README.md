# Web Join browser client

Static, opt-in guest client for the desktop host's Pion endpoint. Serve this
directory at the compiled invitation address `https://stalemate.band/join/`.
This change does not deploy the page or Worker. The desktop control is disabled
until the host selects **Allow a web guest** for a music session.

The required shared Worker update lives in `deploy/relay/cloudflare-worker/`.
It must return `client: "web"` from `/turn-credentials`, account for its separate
web key and persistent quota, and allow the page's exact origin. An older or
misconfigured Worker fails closed. No provider key, API token, analytics token,
or long-lived credential belongs in these static files. TURN credentials stay
in memory and expire in at most one hour.

The fragment contains `k` (32-byte unpadded base64url capability), `s` (32-byte
lowercase hexadecimal session), `g` (generation), and `e` (Unix expiry seconds).
The HTTP request never includes the fragment. `crypto.js` implements the shared
`webjam/web-join/1` HKDF/AES-GCM and fingerprint-bound enrollment protocol;
`test/vectors.json` is also consumed by Go and Python tests. Mailbox chunks use
derived IDs, are at most 4,000 encoded characters, and reassemble to at most
8,192 encrypted bytes. The assembled envelope authenticates all chunk content.

ICE gathers public server-reflexive and TURN candidates together, letting ICE
prefer direct connectivity and fall back to UDP/TCP/TLS TURN. Candidate policy
rejects host, private, reserved, multicast, documentation, mDNS, and unlisted
relay candidates. The reviewed Cloudflare IPv4 relay allowlist is
`141.101.90.1` and `162.159.207.1`; provider changes fail closed and require a
reviewed update to browser and Go policy. See the
[provider's address maintenance guidance](https://developers.cloudflare.com/realtime/turn/faq/).
IPv6-only paths are not yet supported by the browser policy.

Microphone capture requires the headphones checkbox and a tap. Music mode
requests stereo 48 kHz with processing disabled and reports ignored settings.
Both audio directions remain quarantined until the host acknowledges the
certificate-bound proof over DTLS. Displayed delay is an estimate from network
RTT, receiver buffering, and local playback latency; it excludes capture and
the host bridge. iOS/Safari and Firefox limitations are shown in the page.

## Checks

From the repository root:

```sh
node --test deploy/relay/cloudflare-worker/test/*.test.js
npm --prefix web/join ci --ignore-scripts
npm --prefix web/join test
cd web/join
npx playwright install chromium webkit
npm run test:e2e
```

Playwright starts its own static HTTP server on `127.0.0.1:4174` and the Go
test-only endpoint on `127.0.0.1:4175`. Go and a supported Chromium/WebKit
installation are required. The standalone Go harness command is:

```sh
cd transport
WEBJAM_BROWSER_LAB=1 go test ./internal/webjoin -run '^$' -count=1
```

The lab fixture is compiled only into the Go test binary. It permits local
host candidates and echoes real Opus media through an in-memory bridge.
Playwright validates headphones gating, ephemeral browser certificates,
cross-language AEAD/proof vectors, bad-pin cleanup, and browser-to-Pion
enrollment plus returned audio. The production candidate policy has separate
reject/accept unit coverage and no browser runtime bypass.

The echo fixture does not prove Jamulus mixing or cross-network TURN reachability.
The Python Jamulus lab covers the host-local codec/UDP bridge independently.
Manual Mac host + Windows Chrome + iPhone Safari sessions on separate networks
remain required before release; record measured latency and confirm TURN
fallback, kick, generation reset, quota closure, and iOS foreground recovery.
