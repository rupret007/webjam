# Web Sketch Jam (Option B): browser-to-browser WebRTC

Status: spec, draft. No merges, tags or releases from this work.

## Goal
Let up to 4 people open a link and jam loosely in the browser with no install.
It is labelled **"Sketch Jam"** and tells people plainly: *"Great for ideas and
loose jams. Expect about 60-120 ms delay. For tight rehearsals use the WebJam app."*

## Architecture
- Static web client (no build secrets): mesh WebRTC, 1 audio track per peer, max 4 peers (6 links).
- Signaling: the existing Worker Durable Object mailbox (`deploy/relay/cloudflare-worker/src/mailbox.js`).
  Each mailbox id is a 32-byte HKDF-SHA256 locator (hex-encoded, 64 chars), never the invite secret.
  The invite secret stays in the URL fragment (`#k=...`, 32 bytes, never sent to the server).
- The mailbox DO exposes one write-once `offer` slot and one write-once `answer` slot per id
  (`409 duplicate` on a second write; `MAX_WAITERS = 2`; `DELETE` clears the mailbox). A single
  derived id cannot carry a 4-peer mesh. From the shared `#k=...` secret the client derives:
  - **Seat claim (4 ids):** HKDF-SHA256 IKM = invite secret, salt = empty, info =
    `webjam/sketch-jam/seat/{n}` for `n` in `0..3`. Each joiner posts a sealed `offer` to claim a
    seat; on `409 duplicate` it tries the next seat in rotation `((join_nonce[0] % 4) + k) % 4` for
    `k = 0..3` (`join_nonce` is 32 random bytes). The first successful `offer` fixes that peer's
    seat index `0..3`.
  - **Pairwise signaling (6 ids):** for each ordered pair `(i, j)` with `0 <= i < j < 4`, HKDF with
    info = `webjam/sketch-jam/pair/{i}/{j}` (same IKM/salt). Peers `i` and `j` use that mailbox for
    their single offer/answer exchange (lower index posts `offer`, higher posts `answer`).
  - **Per-mailbox AEAD key:** HKDF IKM = invite secret, salt = 32-byte mailbox id, info =
    `webjam/sketch-jam/aead/1`. Ciphertext uses AES-GCM with AAD binding seat or pair context.
- ICE: direct first; TURN credentials from the existing `POST /turn-credentials`.

## Worker changes (reuse, do not fork)
- CORS: exact allowlist of origins (`https://stalemate.band`, preview origin, `http://localhost:*`
  and `http://127.0.0.1:*` in dev only). No `*`.
  - Any `OPTIONS` preflight or any request that carries an `Origin` header or `Sec-Fetch-Mode` is
    treated as a browser request: its `Origin` must match the allowlist or the Worker returns `403`
    **before** touching a Durable Object. Requests with no `Origin` and no `Sec-Fetch-Mode` (desktop
    mailbox/TURN) stay on the existing desktop path.
  - Allowlisted browser requests are forced into the **web** quota scope on both
    `/mailbox/:id/:slot` and `/turn-credentials` (in addition to the global mailbox/TURN scopes).
- Browser token: the same session/invite token shape the desktop uses (UUID or 64-hex `session_id`,
  `invite_token` `^[A-Za-z0-9_-]{32,256}$`), plus `client=web` on `/turn-credentials` whenever
  `Origin` is present (`client` must be exactly `"web"`; missing or other values -> `403`).
- Fail closed under free limits: keep the 800 GB/month global TURN cap and the 10k/10k/1k daily DO
  counters for mailbox/TURN. Browser traffic also hits a **web sub-cap** enforced **in addition** to
  those global caps (default 200 GB/month TURN, 3k req/day per web scope, separate row-write/GB-s
  shares). Analytics unreadable -> 503. Any cap hit -> 429 and the client says "relay busy, try same
  Wi-Fi or the app". $0, no card, Workers Free plan only.
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
- Worker: CORS allow/deny, preflight, web token shape, web sub-cap 429, analytics-fail 503 (extend `test/*.test.js`);
  `Origin-present POST /turn-credentials missing client=web -> 403`;
  `Origin-present /mailbox/:id/:slot charges web sub-cap and global daily cap` (`test/browser.test.js`).
- Client unit: SDP munging, constraint fallback warnings, AEAD round trip, mailbox id derivation vectors shared with desktop;
  `four peers derive six distinct pairwise mailbox ids and claim seats without slot collision`
  (`tests/test_web_sketch_jam_signaling.py`).
- Playwright (Chromium + WebKit, fake media): 2-4 peers connect via loopback mailbox; headphones prompt blocks join; label present.
- Manual: Mac Safari, iPhone Safari, Windows Chrome across networks; record measured latency.
