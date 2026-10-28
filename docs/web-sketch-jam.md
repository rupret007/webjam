# Web Sketch Jam (Option B): browser-to-browser WebRTC

Status: implemented for draft review, unreleased and not deployed. Source spec:
[#385](https://github.com/rupret007/webjam/pull/385), Option B. No merges,
tags or releases from this work. Option A (browser-to-desktop Web Join)
remains separate future work.

## Goal
Let up to 4 people open a link and jam loosely in the browser with no install.
It is labelled **"Sketch Jam"** and tells people plainly: *"Great for ideas and
loose jams. Expect about 60-120 ms delay. For tight rehearsals use the WebJam app."*

## Architecture
- Static web client (no build secrets): mesh WebRTC, 1 audio track per peer, max 4 peers (6 links).
- Signaling: the existing Worker Durable Object mailbox (`deploy/relay/cloudflare-worker/src/mailbox.js`).
  Clients post only an HKDF-derived mailbox id plus AEAD ciphertext (WebCrypto HKDF-SHA256 + AES-GCM),
  derived from the invite secret in the URL fragment (`#k=...`, never sent to the server).
- ICE: direct first; TURN credentials from the existing `POST /turn-credentials`.

## Worker changes (reuse, do not fork)
- CORS: exact allowlist of origins (`https://stalemate.band`, preview origin, `http://localhost:*` in dev only).
  No `*`. Preflight handled; non-allowed origin returns 403 before touching a Durable Object.
- Browser token: the same session/invite token shape the desktop uses (UUID or 64-hex `session_id`, `invite_token`
  `^[A-Za-z0-9_-]{32,256}$`), plus a `client=web` field counted under its own quota scope.
- Fail closed under free limits: keep the 800 GB/month TURN cap and the 10k/10k/1k daily DO counters; add a web
  sub-cap (default 200 GB/month TURN, 3k req/day). Analytics unreadable -> 503. Cap hit -> 429 and the client
  says "relay busy, try same Wi-Fi or the app". $0, no card, Workers Free plan only.
- Credentials short-lived (<= 1 h for web), never cached in localStorage. No API token or key in the client.

## Audio (music mode)
- `getUserMedia({audio:{echoCancellation:false,noiseSuppression:false,autoGainControl:false,channelCount:2,sampleRate:48000}})`;
  log `track.getSettings()` and warn if the browser ignored any of them.
- Opus SDP: `stereo=1;sprop-stereo=1;maxaveragebitrate=128000;usedtx=0;useinbandfec=1`, ptime 20 (10 where accepted).
- `RTCRtpReceiver.jitterBufferTarget` set low (e.g. 20 ms) where supported; show live latency from `getStats()`
  (RTT/2 + jitterBufferDelay/jitterBufferEmittedCount + baseLatency).
- AudioWorklet only for metering/monitor mix; no ScriptProcessor.
- **Headphones prompt** is required before joining: "Wear headphones. Echo cancelling is off for music quality."

## iOS / Safari limits (shown in UI)
- Start audio from a tap; resume AudioContext on visibility change.
- Safari/iOS plays mono; extra playback buffering; WebRTC+WebAudio stutter history (WebKit 225880).
- iOS is labelled "listen and play along, best effort". Firefox may send low bitrate: show a notice.

## Security
- Invite secret only in the URL fragment; mailbox sees ciphertext only; per-IP limits in DO storage.
- DTLS-SRTP fingerprints are carried inside the AEAD signaling, so a mailbox operator cannot MITM.
- CSP on the page; no third-party scripts; no analytics.

## Tests (never loosen existing ones)
- Worker: CORS allow/deny, preflight, web token shape, web sub-cap 429, analytics-fail 503 (extend `test/*.test.js`).
- Client unit: SDP munging, constraint fallback warnings, AEAD round trip, mailbox id derivation vectors shared with desktop.
- Playwright (Chromium + WebKit, fake media): 2-4 peers connect via loopback mailbox; headphones prompt blocks join; label present.
- Manual: Mac Safari, iPhone Safari, Windows Chrome across networks; record measured latency.

## Using this implementation

The static page lives in `web/sketch-jam/`. It has no build step, runtime
dependencies, third-party scripts, analytics, or embedded service credentials.
Open the page, enter a display name, confirm that you are wearing headphones,
and tap Join. Share the displayed invite with up to three other people.
The page reports connection state and an estimated receive latency for each
peer; this is not a physical end-to-end instrument latency measurement.

An invite's ten-minute join window matches the existing mailbox lifetime.
Established media can continue after joining closes. Slots are write-once:
create a new invite to replace someone who leaves or reloads. Four people
produce six independent WebRTC audio connections. Shared-network rate limits
can delay signaling; a rate response permits one bounded retry after one
minute, while daily/monthly caps and unavailable analytics fail closed.

`config.js` contains only the public relay origin. Serve the static directory
over HTTPS, keeping its CSP and security headers. Changing the relay origin
also requires changing the page's `connect-src` CSP. Browser access requires
the reviewed Worker changes and an exact allowed page origin; the existing
live Worker is not changed by this PR. Local integration testing uses the
loopback harness below without contacting that live service.

## Signaling and free limits

The invite contains `#k=<32-byte capability>&s=<UUID>&t=<creation time>`.
The capability stays in the fragment and browser memory; the session UUID
also accompanies TURN credential requests. The client derives
address-specific child capabilities using HKDF-SHA256 and the
`webjam/sketch/1/` domain. Child mailboxes reuse the desktop's
`webjam/v3/mailbox/id` and `webjam/v3/mailbox/aead` derivation labels.
AES-GCM authenticates the session, logical address, and offer/answer slot.
The Worker receives a derived mailbox locator and base64url ciphertext,
never the capability, display names, SDP, or DTLS fingerprints.

Four encrypted membership records claim slots atomically through the
existing write-once POST behavior. For each pair, the higher slot makes the
offer. SDP is split into bounded encrypted chunks, with the first chunk
published last; chunk counts and content are authenticated. Direct and relay
attempts use different addresses. A separate HKDF-derived token satisfies
the existing TURN token shape without disclosing the invite capability.

The Worker adds CORS before Durable Object access. Its exact allowlist
defaults to `https://stalemate.band`; preview origins must be named in full.
HTTP localhost origins require explicit ports and `ENVIRONMENT=development`.
Browser requests count toward both the original quota scope and a combined
durable web scope capped at 3,000 requests/day. The existing 10k/10k/1k
daily defaults and per-IP limits remain in place.

Web TURN uses a distinct Worker-only key so analytics can attribute its
200 GB monthly sub-cap. Desktop plus web usage is checked against the
existing 800 GB cap. Both limits may be lowered. Credentials are minted only
when both analytics results are readable and under their caps. Missing web
secrets return 503; caps return 429. Web credentials last at most one hour,
default ten minutes, and are kept only in memory.

**Client-side session limits:** The client sums transport bytes across relay
peers and stops below 90% of the credential's session allowance (at most
256 MiB), reserving room for in-flight audio. Unreadable relay byte counters
close the session; direct peers can continue with unavailable latency
statistics. The credential response includes `allowed_relay_ips` with
Cloudflare's documented TURN allocation IPs for informational purposes.
**Neither the per-session byte cap nor the relay IP allowlist is enforced
server-side**; a modified client could exceed the session cap (consuming
monthly quota faster) or connect through arbitrary relay IPs. The monthly
aggregate caps and per-IP rate limits remain the server-side controls.

Retain the Workers Free plan, SQLite Durable Objects, and the shared free
Realtime allowance, with no paid add-on or card. Configuration details are in
[the relay guide](../deploy/relay/README.md#sketch-jam-browser-access-unreleased).

## Repeatable checks

Use Node 22.13 or newer (the browser harness uses built-in SQLite), Python
3.11+, and the repository's Python dependencies. From the repository root:

```sh
node --test deploy/relay/cloudflare-worker/test/*.test.js
npm ci --prefix web/sketch-jam
npm test --prefix web/sketch-jam
cd web/sketch-jam
npx playwright install chromium webkit
npm run test:e2e
```

On Linux, install browser system dependencies with
`npx playwright install --with-deps chromium webkit`. CI runs the Worker,
client unit, and both browser suites. Desktop compatibility checks, run
from the repository root:

```sh
python -m pytest -q tests/test_mailbox_crypto_vectors.py \
  tests/test_signaling_mailbox.py tests/test_cloudflare_turn_credentials.py \
  tests/test_offline_invite_path.py tests/test_remote_invitation.py \
  tests/test_remote_invitation_owner.py tests/test_direct_stun_host_join.py \
  tests/test_direct_stun_host_join_flow.py
python tools/gates/test_integrity_guard.py fake-clock
python tools/gates/test_integrity_guard.py loosening --base origin/master
```

The browser harness executes the real Worker, Mailbox, and SQLite quota
classes on loopback. Chromium uses synthetic native Web Audio tracks;
WebKit uses Playwright's built-in mock microphone with test media permission,
which enables usable host ICE candidates. Neither uses a real microphone.
Tests remove external ICE servers; native WebRTC still performs ICE, DTLS-SRTP,
Opus encoding, and reception. Isolated participants model distinct IPs under
the default limits. WebKit uses separate browser processes because native
capture in another tab can mute an earlier tab's microphone. These checks
establish local mesh connectivity and received audio bytes, not public NAT traversal or
physical browser audio performance. Shared crypto fixtures are consumed
by both JavaScript and the desktop Python implementation.

## Physical evidence

These gates require actual devices and networks and remain **NOT RUN**:

| Gate | Status | Evidence to record |
| --- | --- | --- |
| Mac Safari across networks | NOT RUN | OS/browser, audio interface, ICE path, measured latency, dropouts |
| iPhone Safari | NOT RUN | iOS/browser, tap/visibility recovery, mono playback, measured latency |
| Windows Chrome across networks | NOT RUN | OS/browser, interface, ICE path, measured latency, dropouts |
| Live Cloudflare TURN fallback | NOT RUN | Free-plan configuration, direct failure, relay connection, quota response |

No deployment or credentialed Cloudflare request is part of the local test
suite. The advertised 60–120 ms range is expectation-setting from the spec,
not a measurement produced by these tests.
