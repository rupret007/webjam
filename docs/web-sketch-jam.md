# Web Sketch Jam (Option B): browser-to-browser WebRTC

Status: spec, draft. No merges, tags or releases from this work.

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
