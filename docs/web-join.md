# Web Join (Option A): browser joins a desktop WebJam host

Status: spec, draft. Build after Sketch Jam (Option B). No merges, tags or releases.

## Goal
A bandmate without the app opens an invite link and joins a Mac-hosted WebJam session.
Desktop players keep Jamulus latency; the browser player is labelled "Web Join, higher delay (about 60-120 ms)".

## Architecture
- Host side: new Pion (Go) WebRTC endpoint inside `webjam-fabric` (`transport/`), off by default, enabled per session
  by the host ("Allow a web guest").
- Bridge: the browser's Opus track is decoded and re-encoded as a Jamulus client connection to the host's loopback
  Jamulus server (one bridged Jamulus client per web guest); the host mix for that guest is encoded back to Opus.
  (Prior art: dtinth/rtcjam + dtinth/webjam PoC; ours is production-hardened and host-local, no public gateway.)
- Signaling and ICE: same Worker mailbox and `/turn-credentials` as Sketch Jam; same CORS allowlist and web quota scope.

## Security model (matches the pinned design)
- Invite capability stays in the URL fragment; mailbox id and keys derived with HKDF-SHA256 using the existing
  domain-separation labels plus a new `webjam/web-join/1` label.
- Host publishes its DTLS certificate fingerprint inside the AEAD-sealed offer; the browser pins it (rejects any other).
- The browser generates an ephemeral `RTCCertificate`. WebCrypto cannot export or sign with that private key
  (`generateCertificate()` is non-extractable; `RTCPeerConnection` cannot import an external key pair). Possession
  is proved by the DTLS handshake itself: the enrollment transcript includes
  `RTCCertificate.getFingerprints()`, sealed with the HKDF-derived session/enrollment key (`webjam/web-join/1`),
  binding session, role, nonce, generation and expiry. The Go Pion endpoint verifies the enrollment MAC/AEAD tag
  **and** that the active DTLS transport's peer certificate fingerprint matches the enrolled fingerprint before
  unquarantining media/data (same quarantine model as v3 QUIC).
- Same ICE policy: only public-unicast srflx or allowlisted relay candidates; host/private candidates rejected.
- IPC stays fixed-shape: no URL, credential or address supplied from the browser. Host can kick; generation change tears down.
- 256 MiB/session quota and Worker caps apply; fail closed.

## Audio
Same music-mode constraints, Opus settings, headphones prompt and iOS notes as `docs/web-sketch-jam.md`.

## Cost
$0: Workers Free + Realtime TURN free allowance under the existing 800 GB cap; no card.

## Tests (never loosen existing ones)
- Go: Pion endpoint enrollment accept/reject (bad pin, replay, expired, wrong generation), candidate policy, quota close;
  `TestPionEndpointRejectsMismatchedDTLSFingerprint`.
- Bridge: loopback Jamulus server lab test, browser fake-media sine in -> Jamulus mix out, measure added latency.
- Worker: shared with Sketch Jam.
- Playwright (Chromium + WebKit) against a lab host; manual Mac host + Windows Chrome + iPhone Safari.
