# WebJam v0.17 two-Mac source-candidate worksheet

> Blank operator record for one exact v0.17.0 candidate. Fill identity fields
> only after the final commit and package exist. Every observation below starts
> as **NOT RUN** and stays that way until a musician performs it on the named
> artifact. Automated, simulated, loopback, container, and headless-Qt results
> do not fill physical or external-editor rows.

## Candidate identity

| Field | Record |
| --- | --- |
| App version | `0.17.0` |
| Source commit | _blank — final candidate not selected_ |
| Source branch / PR | _blank_ |
| Host artifact filename | _blank_ |
| Host artifact SHA-256 | _blank_ |
| Guest artifact filename | _blank_ |
| Guest artifact SHA-256 | _blank_ |
| Build workflow / run | _blank_ |
| Signing identity | `NOT RUN` |
| Notarization / platform trust | `NOT RUN` |
| Preserved rollback artifact | _blank_ |

Do not begin the physical run if either Mac reports a different version,
source commit, artifact hash, or build run. Preserve the v0.16.3 published
package separately; do not overwrite it in place.

## Machines and audio route

| Field | Host Mac | Guest Mac |
| --- | --- | --- |
| Model / architecture | _blank_ | _blank_ |
| macOS version | _blank_ | _blank_ |
| Interface / driver | _blank_ | _blank_ |
| Input channels | _blank_ | _blank_ |
| Headphone/output route | _blank_ | _blank_ |
| Jamulus sample rate / buffer | _blank_ | _blank_ |
| Operator | _blank_ | _blank_ |

## Host, join, and live music

| Observation | Status | Evidence / notes |
| --- | --- | --- |
| Host starts its private server before Jamulus opens | `NOT RUN` | _blank_ |
| Invitation is copied once and guest joins without connection details in WebJam UI | `NOT RUN` | _blank_ |
| Both musicians hear each other through the real Jamulus route | `NOT RUN` | _blank_ |
| Webex remains optional and muted while playing, if used | `NOT RUN` | _blank_ |
| Record choice clearly separates Shared Jam from Local Originals | `NOT RUN` | _blank_ |
| Stop/finalize preserves a reviewable multitrack take | `NOT RUN` | _blank_ |

## Studio Arrange, sections, and comping

| Observation | Status | Evidence / notes |
| --- | --- | --- |
| Completed take opens in Studio with correct tracks and progressive waveforms | `NOT RUN` | _blank_ |
| Arrange remains usable at 760×600, 1024×768, and 1440×900 | `NOT RUN` | _blank_ |
| Keyboard focus, track/region selection, nudge, trim, Undo, and Redo are usable | `NOT RUN` | _blank_ |
| Move, trim, split, duplicate, disable/delete, and snap edits sound as shown | `NOT RUN` | _blank_ |
| A named Verse/Chorus section moves as one all-track ripple edit and Undo restores it | `NOT RUN` | _blank_ |
| A compatible repeated take can be added and auditioned without changing the saved comp | `NOT RUN` | _blank_ |
| A quick-swipe or keyboard comp selection plays only the intended lane/range | `NOT RUN` | _blank_ |
| Closing and reopening Studio restores the exact saved arrangement and mix | `NOT RUN` | _blank_ |

## Real-output playback and cycle

| Observation | Status | Evidence / notes |
| --- | --- | --- |
| Studio output is explicitly chosen without changing Jamulus live output | `NOT RUN` | _blank_ |
| Arrange and comp playback is heard through the named physical output | `NOT RUN` | _blank_ |
| Scrub and seek land on the intended edit boundaries | `NOT RUN` | _blank_ |
| Cycle playback wraps at the selected project frames without an audible click | `NOT RUN` | _blank_ |
| Playback remains responsive on the representative long/multitrack take | `NOT RUN` | _blank_ |

## Evidence-rich export and external editor

| Observation | Status | Evidence / notes |
| --- | --- | --- |
| Export creates one complete new package and no partial success folder | `NOT RUN` | _blank_ |
| Edited PCM24 stems, aligned-unity originals, and rough mix have equal intended length | `NOT RUN` | _blank_ |
| Studio document, source manifests, markers/sections CSV, provenance, and SHA256SUMS are present | `NOT RUN` | _blank_ |
| Independent checksum verification matches every listed output | `NOT RUN` | _blank_ |
| External editor and version | `NOT RUN` | _blank_ |
| Import preserves project rate, track identity, alignment, edits, gaps, and markers | `NOT RUN` | _blank_ |
| Imported playback matches Studio through a named physical output | `NOT RUN` | _blank_ |

## Disruption, recovery, and trust

| Observation | Status | Evidence / notes |
| --- | --- | --- |
| Interface disconnect/reconnect recovers truthfully through Jamulus | `NOT RUN` | _blank_ |
| Sleep/wake does not reuse stale readiness and recovery is truthful | `NOT RUN` | _blank_ |
| Interrupted/failed recording remains preserved for review | `NOT RUN` | _blank_ |
| Failed Studio save blocks destructive close until retry succeeds | `NOT RUN` | _blank_ |
| Clean installation passes the platform trust prompts recorded above | `NOT RUN` | _blank_ |
| End/Leave stops only owned processes after recording finalization | `NOT RUN` | _blank_ |

## Final disposition

| Field | Record |
| --- | --- |
| Physical two-Mac result | `NOT RUN` |
| Real-output Arrange/comp result | `NOT RUN` |
| Real-output cycle/de-click result | `NOT RUN` |
| External-editor import result | `NOT RUN` |
| Signing/notarization result | `NOT RUN` |
| Candidate decision | _blank — do not promote while required rows are NOT RUN_ |
| Evidence attachment location | _blank_ |

Record a failure as **FAIL**, preserve its take/export/support evidence, and
change one variable at a time. An unchecked or unavailable observation is
**NOT RUN**, never an inferred pass.
