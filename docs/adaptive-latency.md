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

Each launch retires the previous RPC binding, ping, participant IDs and policy
streaks. Stop drains an entered policy write before returning; saved callbacks
from a retired binding cannot write the next profile. A late bind for a retired
RPC identity cannot replace the current binding. File/format errors disable
optional stepping for the remainder of that launch without breaking monitoring.
Runtime opt-out/override is checked before each recommendation.

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

The notification includes an `address`; it is not proof of the currently
connected audio server merely because its monitor identity matches. Accept
only an exact numeric IPv4/port match to the actual launch target, excluding
loopback. Do not resolve a hostname or assume a local sidecar ping measures
the remote Pion path. Those routes remain unknown until qualified route
measurements exist. Readings expire after eight seconds and are hidden when
the bound RPC monitor disconnects or changes identity. Invalid readings clear
the earlier estimate. This is not measured end-to-end audible delay.

The optional server-jitter ingestion hook currently has no production caller;
it does not establish per-remote-player latency. Real device buffer selection,
physical dropouts and WAN audibility remain separate test requirements.

## Web Join

See `web/join/audio.js`: `adaptJitterBufferTarget()` uses `candidate-pair.currentRoundTripTime`,
`inbound-rtp` `jitter`, `packetsLost` / `packetsReceived`, and `jitterBufferDelay`, with
5 s rate limiting and 20–120 ms bounds.

## Settings

- `WEBJAM_ADAPTIVE_LATENCY_ENABLED` (default on)
- `WEBJAM_ADAPTIVE_LATENCY_PLAYER_OVERRIDE` (default off)
- `WEBJAM_AUDIO_LATENCY` still maps default frame/quality presets (`low` / default / `safe`)
