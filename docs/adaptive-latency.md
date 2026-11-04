# Adaptive latency (Jamulus + Web Join)

Jamulus remains the live audio path. WebJam adds **defaults**, a **measurement-driven
policy**, room **presentation**, and browser **WebRTC jitter adaptation** without
replacing Jamulus.

## Static vs dynamic

| Layer | Static (launch) | Dynamic (during session) |
| --- | --- | --- |
| Native Jamulus client | Fill **unset** ini keys before `Popen` | Policy reads ping; may write `prefsndcrdbufidx` to the profile with logging |
| Web Join browser | Initial `jitterBufferTarget` 20 ms | `getStats()`-driven target within 20–120 ms |

Static defaults (when keys are absent in the WebJam-owned profile):

- `autojitbuf=1` — automatic network jitter buffer ([`src/settings.cpp` `autojitbuf`](https://github.com/jamulussoftware/jamulus/blob/r3_12_2/src/settings.cpp))
- `prefsndcrdbufidx=2` — 128-sample device frame ([`FRAME_SIZE_FACTOR_DEFAULT`](https://github.com/jamulussoftware/jamulus/blob/r3_12_2/src/global.h))
- `audioquality=1` — normal quality (0..2 in [`audioquality`](https://github.com/jamulussoftware/jamulus/blob/r3_12_2/src/settings.cpp))

Disabled tuning or the player override performs no profile writes. Existing
frame factors are used for launch-based delay estimates; a persisted policy
recommendation does not represent a live buffer change. Unknown profile formats
are preserved. A musician edit without matching provenance is never overwritten.

Musician-set values are left untouched; provenance is stored in
`WebJam-adaptive-latency-provenance-v1.json` next to the profile.

## Jamulus mechanisms (3.12.2)

| Mechanism | Use |
| --- | --- |
| Ini `autojitbuf`, `jitbuf`, `prefsndcrdbufidx`, `audioquality` | Launch defaults and logged policy steps |
| CLI `--jsonrpcport`, `--jsonrpcsecretfile` | Unchanged; see [Command-Line Options](https://jamulus.io/wiki/Command-Line-Options) |
| Notification `jamulusclient/serverInfoReceived` | `params.pingtime` (ms) — [JSON-RPC.md](https://github.com/jamulussoftware/jamulus/blob/r3_12_2/docs/JSON-RPC.md) |
| Server `jamulusserver/getClients` | Optional `jitterBufferSize` per client (blocks) |

## Gaps (explicit)

Jamulus **3.12.2 JSON-RPC does not expose**:

- Overall delay (GUI metric)
- Dropout counters
- Any method to change buffer/frame size at runtime

WebJam therefore:

1. Estimates overall delay for the **local** musician from ping + frame factor (+ server jitter blocks when known).
2. Logs and persists profile changes on policy steps; **live** buffer changes still require Jamulus Settings or reconnect.
3. Disables automatic stepping when `adaptive_latency_player_override` is set in WebJam settings
   (`WEBJAM_ADAPTIVE_LATENCY_PLAYER_OVERRIDE=1`) or `adaptive_latency_enabled` is off.

## Web Join

See `web/join/audio.js`: `adaptJitterBufferTarget()` uses `candidate-pair.currentRoundTripTime`,
`inbound-rtp` `jitter`, `packetsLost` / `packetsReceived`, and `jitterBufferDelay`, with
5 s rate limiting and 20–120 ms bounds.

## Settings

- `WEBJAM_ADAPTIVE_LATENCY_ENABLED` (default on)
- `WEBJAM_ADAPTIVE_LATENCY_PLAYER_OVERRIDE` (default off)
- `WEBJAM_AUDIO_LATENCY` still maps default frame/quality presets (`low` / default / `safe`)
