# WebJam — Taking It to the Next Level

**Date:** 2026-06-29
**Status:** Historical memo. Its market comparison and protocol history remain
useful, but current status lives in `README.md` and `MODERNIZATION_PLAN.md`.
The v0.8.1 candidate uses native two-lane Webex talkback and adds opt-in
same-Mac JamulusServer hosting; it still awaits the full two-Mac hardware
gates. Do not treat the old release recommendations below as current work.
An evaluation of what WebJam needs to become a tool a band actually relies on, with the audio engine assessed against the current open-source landscape — plus the headline fix implemented this round.

---

## TL;DR

1. **WebJam's Jamulus control was built against a dead API.** Its RPC client spoke HTTP+SSE with method names (`jamulus/getChannelClients`, `jamulus/setChannelGain`) that match an *early experimental fork*, not shipping Jamulus. Current Jamulus (3.12.0) uses **newline-delimited JSON-RPC over TCP**, a mandatory **`jamulus/apiAuth`** handshake, and **`jamulusclient/*`** methods. So "real mixer control" could never have worked against a real Jamulus. **This round I rebuilt the client against the real API and verified it end-to-end** against a fake Jamulus server.
2. **Stay on Jamulus.** For a band wanting *one shared mix*, *low bandwidth*, and a *scriptable control API*, Jamulus is still the best open-source fit. SonoBus is easier (no server) but has no shared mix and no control API; JackTrip has the best quality but is hard to set up and leans commercial.
3. **The biggest remaining levers** are: ship a signed/notarized release, leverage the now-correct API (self-mute, chat, server recording), and lower the setup barrier (optionally a serverless SonoBus backend).

---

## 1. The audio engine: was WebJam's Jamulus integration current?

**No.** Verified against the authoritative API doc ([JSON-RPC.md, jamulussoftware/jamulus](https://github.com/jamulussoftware/jamulus/blob/main/docs/JSON-RPC.md)) and the 3.12.0 release:

| Aspect | Real Jamulus (3.9–3.12) | WebJam (before) | Status |
|---|---|---|---|
| Transport | NDJSON over **raw TCP** | HTTP POST + SSE `/events` | **Fixed** |
| Auth | `jamulus/apiAuth` + `--jsonrpcsecretfile` | none | **Fixed** |
| Participants | `jamulusclient/getClientList` / `clientListReceived` | `jamulus/getChannelClients`, SSE `channelConnected` | **Fixed** |
| Fader | `jamulusclient/setFaderLevel` (0–100) | `jamulus/setChannelGain` (0–10000) | **Fixed** |
| Levels | `channelLevelListReceived` (0–9) | range 0–32767 | **Fixed** |

**Implemented this round:** rewrote `core/jamulus_rpc_client.py` to the real protocol (TCP, apiAuth, `jamulusclient/*`, correct ranges); `services/bridge_service.py` now writes a JSON-RPC secret and launches Jamulus with `--jsonrpcsecretfile`. Verified by a fake-Jamulus TCP server test (auth handshake, `getClientList` parsing, `setFaderLevel` wire format, level normalization, wrong-secret rejection) — `tests/test_jamulus_rpc_tcp.py`, plus updated parsing/heartbeat/concurrency tests. Full suite: 576 passing.

---

## 2. Is there something better, open-source?

| Engine | Model | Strengths | Weaknesses for WebJam | License |
|---|---|---|---|---|
| **Jamulus** 3.12 | Client→**server** mix | One shared mix (server = time reference), low bandwidth, **scriptable JSON-RPC** (faders, mute, chat, recording), simplest of the three to set up | Needs a server; lossy (Opus) | GPL |
| **SonoBus** | **Serverless** P2P | No server to run, very easy, great quality, mobile-friendly | **No shared mix** (everyone hears something different), bandwidth grows with group size, **no control API**, NAT/port-forwarding pain | GPLv3 |
| **JackTrip** | P2P / hub | **Best latency & uncompressed quality**, DAW plugins, AI packet-loss concealment, recording/livestream | Hard to set up (JACK), pushes a **paid** "Virtual Studio"/hardware, not band-friendly | GPL (core) |

**Verdict: keep Jamulus.** It's the only one of the three that gives a band a *single shared mix* **and** a *programmatic control surface* — which is exactly what WebJam is built around (the Conductor's per-participant faders, mute, levels). SonoBus would mean abandoning the shared-mix model and the control API; JackTrip would trade WebJam's ease-of-use for a much harder setup and a commercial tilt.

**But** WebJam's single biggest real-world friction is *requiring a Jamulus server*. The highest-value future option is a **pluggable "no-server" backend (SonoBus)** for bands who can't run/find a server — offered as an alternative, not a replacement.

Sources: [JSON-RPC.md](https://github.com/jamulussoftware/jamulus/blob/main/docs/JSON-RPC.md) · [Jamulus 3.12.0 release](https://github.com/jamulussoftware/jamulus/releases/tag/r3_12_0) · [Jamulus vs SonoBus discussion](https://github.com/orgs/jamulussoftware/discussions/1313) · [JackTrip](https://github.com/jacktrip/jacktrip) · [Comparison of remote music performance software (Wikipedia)](https://en.wikipedia.org/wiki/Comparison_of_Remote_Music_Performance_Software)

---

## 3. What WebJam needs to take it to the next level (prioritized)

**P0 — make the core actually work & ship**
- ✅ **Correct Jamulus control API** (done this round).
- **Historical note:** this memo predated the v0.7.x releases. Signed/notarized builds still matter, but downloadable Windows x64, macOS ARM64, and macOS Intel x64 artifacts now exist.

**P1 — leverage the now-correct API (concrete, high-value features the fix unlocks)**
- ✅ **Talk Break / Mute Jamulus Send** via `jamulusclient/setMuted` is shipped;
  it deliberately never changes native Webex mute state.
- **In-session chat** via `jamulusclient/sendChatText` + `chatTextReceived` → put it in the session canvas (low effort, high "we're together" value).
- **Server-side recording** (for the band admin's server) via `jamulusserver/startRecording` / `getRecorderStatus` → one-click "record this rehearsal."

**P2 — lower the setup barrier (the real adoption blocker)**
- ✅ **In-app macOS band-server hosting** now verifies and supervises the
  official JamulusServer.app, provisions recorder control, and connects the
  host client over loopback. A serverless backend remains a different future
  option for users who cannot host or reach UDP 22124.
- **Pluggable audio backend** abstraction, with **SonoBus** as a serverless option for bands without a Jamulus server.
- **Advanced audience bridge:** bundled VB-CABLE installers lower Windows
  setup friction, but ordinary musician talkback intentionally needs no
  virtual cable.

**P3 — the "shared artifact" vision (from VISION_AND_ROADMAP.md)**
- One-click **session templates** ("Band Rehearsal"), in-session **rituals**, exportable **session brief**, recording timeline.

---

## Recommended next step

Current recommendation: use the v0.8.1 candidate privately, complete the
two-Mac workflow in `SUNDAY_TWO_MAC_PILOT.md`, and publish only after the exact
artifact passes the recording, reconnect, talkback, and soak gates.
