# WebJam v0.26.0 creator-multitrack physical test checklist

> Physical-test status: **NOT RUN**. This ledger is provisional and must not be
> executed until an exact annotated `v0.26.0` tag, successful native tag CI,
> candidate packages, and a checksum manifest exist. Immutable v0.25.0 remains
> GitHub **Latest**; source metadata is not release or hardware evidence.

Use only exact candidate packages whose filenames, SHA-256 values, app version,
and source build IDs agree with the future manifest. Record no invitation or
meeting links, credentials, device UIDs, private paths, participant names, or
raw exceptions. A result may change only after its observation and evidence
reference are recorded in the controlled test record.

Required bench: two physical computers, wired headphones for both people, one
real multichannel audio interface, at least two mono inputs, one stereo source,
and a second participant able to upload guest originals.

## Exact candidate identity

| ID | Evidence to freeze before physical testing | Result |
| --- | --- | --- |
| I01 | Annotated `v0.26.0` tag object and peeled commit | **NOT RUN** |
| I02 | Unique successful `v0.26.0` native tag CI run and attempt | **NOT RUN** |
| I03 | Exact Windows, macOS arm64/x64, and Ubuntu package filenames | **NOT RUN** |
| I04 | Package sizes, SHA-256 values, app versions, and source build IDs | **NOT RUN** |
| I05 | Checksum-manifest filename, SHA-256, and complete sorted inventory | **NOT RUN** |
| I06 | Draft release ID/body hash and post-tag publisher pins | **NOT RUN** |
| I07 | Packaged Jamulus client/server versions and build identities | **NOT RUN** |
| I08 | Tester, machine, OS, interface, headphones, and channel-map record | **NOT RUN** |

## A. Native packages, clean start, and trust

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| A01 | Windows x64 Setup installs per-user and launches from Start | **NOT RUN** |
| A02 | Windows x64 portable ZIP launches after clean extraction | **NOT RUN** |
| A03 | macOS arm64 exact DMG launches through the documented Gatekeeper flow | **NOT RUN** |
| A04 | macOS x64 exact DMG launches through the documented Gatekeeper flow | **NOT RUN** |
| A05 | Both macOS ZIPs launch without Full Disk Access or other-app-data access | **NOT RUN** |
| A06 | Ubuntu 22.04 x64 ZIP launches with its complete bundled inventory | **NOT RUN** |
| A07 | Unsigned Windows and ad-hoc/unnotarized macOS trust copy is accurate | **NOT RUN** |
| A08 | Fresh install, upgrade from v0.25.0, restart, and user-data retention work | **NOT RUN** |

## B. Creator journeys and persisted boundaries

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| B01 | Music opens the correct profile, actions, terminology, and local defaults | **NOT RUN** |
| B02 | Music project, take, track names, edits, and mix reopen after restart | **NOT RUN** |
| B03 | Podcast & Voice opens its correct profile, actions, and voice terminology | **NOT RUN** |
| B04 | Podcast project, take, speaker labels, edits, and mix reopen after restart | **NOT RUN** |
| B05 | Review & Rehearsal is visibly Preview and permits live join plus read-only review | **NOT RUN** |
| B06 | Review blocks standalone creation, edit/comp/mix mutation, and export | **NOT RUN** |
| B07 | Switching profiles preserves separate scratchpads and never leaks them remotely | **NOT RUN** |
| B08 | Legacy unprofiled projects, takes, sessions, and preferences migrate to Music | **NOT RUN** |

## C. Meeting-platform and Shared Track boundary

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| C01 | Webex, Zoom, Teams, Meet, or FaceTime public-HTTPS links use explicit handoff | **NOT RUN** |
| C02 | Another safe public-HTTPS DNS-host provider remains neutral and still hands off | **NOT RUN** |
| C03 | Credentials, custom ports, IP literals, local hosts, and lookalikes fail closed | **NOT RUN** |
| C04 | Native discovery, focus, installer, and mute guidance remains Webex-only | **NOT RUN** |
| C05 | WebJam never claims meeting join/mute or automatically captures meeting/system audio | **NOT RUN** |
| C06 | Host loads a supported Shared Track without playback before route proof | **NOT RUN** |
| C07 | Both participants hear Shared Track as a separate controllable Jamulus source | **NOT RUN** |
| C08 | Replaced/regenerated Shared Track cannot finalize as the frozen planned source | **NOT RUN** |

## D. Authoritative multitrack capture and guest transfer

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| D01 | Readiness freezes roster, server stems, local rows, guest obligations, and Shared Track | **NOT RUN** |
| D02 | Start preflight blocks invalid paths, unavailable inputs, collisions, and low disk | **NOT RUN** |
| D03 | One selected mono row produces exactly one correctly named PCM-24 mono original | **NOT RUN** |
| D04 | Two or more mono rows map to the intended interface channels without swaps | **NOT RUN** |
| D05 | One stereo row produces exactly one true two-channel PCM-24 original | **NOT RUN** |
| D06 | Mixed mono/stereo rows preserve order, channel map, and opt-in selections | **NOT RUN** |
| D07 | Opting out all local rows creates no host local original | **NOT RUN** |
| D08 | Server client stems remain isolated and include the routed Shared Track stem | **NOT RUN** |
| D09 | Timer, capture indicators, readiness summary, and stop state match real capture | **NOT RUN** |
| D10 | Guest records the frozen obligation and uploads exact expected originals | **NOT RUN** |
| D11 | Guest retry/resume after interruption completes without duplicate or substituted files | **NOT RUN** |
| D12 | Under-delivery, over-delivery, topology drift, and source substitution fail closed | **NOT RUN** |
| D13 | Reconnect/presence-generation drift cannot impersonate the frozen guest obligation | **NOT RUN** |
| D14 | Stop moves through Stopping and Finalizing; Ready requires every verified source | **NOT RUN** |
| D15 | Every opted-in guest opens its exact stream and ACKs the frozen arm before Jamulus recording starts | **NOT RUN** |
| D16 | Missing/stale/wrong/late guest ACK cancels safely; zero-track opt-outs do not block | **NOT RUN** |
| D17 | Shutdown under uncertain host commit preserves guest audio locally without uploading it | **NOT RUN** |

## E. Recovery, Studio, and export

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| E01 | Forced app exit during recording leaves recoverable sources and no false Ready take | **NOT RUN** |
| E02 | Restart recovery clearly explains and deterministically repairs or quarantines the take | **NOT RUN** |
| E03 | Missing, corrupt, duplicate, or mismatched guest files remain actionable and fail closed | **NOT RUN** |
| E04 | Ready take opens with every intended source as a distinct Studio track | **NOT RUN** |
| E05 | Rename, trim, split, move, fade, gain, pan, mute, solo, and undo/redo persist | **NOT RUN** |
| E06 | Playhead, waveform, zoom, selection, meters, and transport remain synchronized | **NOT RUN** |
| E07 | Music edit/comp/mix survives close/reopen without changing original media | **NOT RUN** |
| E08 | Podcast edit/comp/mix survives close/reopen without changing original media | **NOT RUN** |
| E09 | WAV and FLAC mix exports render audibly correct timing, balance, and duration | **NOT RUN** |
| E10 | Individual track export preserves identity and never overwrites without confirmation | **NOT RUN** |

## F. Workstation UI, accessibility, and compact layouts

| ID | Physical / packaged observation | Result |
| --- | --- | --- |
| F01 | Primary next action is obvious from launch through readiness, record, finalize, and Studio | **NOT RUN** |
| F02 | Recording state, timer, finalization, recovery, and errors are calm and unmistakable | **NOT RUN** |
| F03 | Dense track controls remain scannable and operable at minimum supported window size | **NOT RUN** |
| F04 | Keyboard-only traversal reaches transport, track controls, dialogs, and recovery actions | **NOT RUN** |
| F05 | Focus indicators, accessible names, reading order, and status announcements are correct | **NOT RUN** |
| F06 | Text scaling and high-contrast states do not clip essential controls or status | **NOT RUN** |
| F07 | Destructive actions require clear confirmation and preserve a recoverable path | **NOT RUN** |
| F08 | Music and Podcast wording stays creator-specific without exposing implementation jargon | **NOT RUN** |

## Release decision summary

| Gate family | Result |
| --- | --- |
| Exact tag, CI, packages, manifest, and publisher pins | **NOT RUN** |
| Native install, clean start, upgrade, and platform trust | **NOT RUN** |
| Music journey and persistence | **NOT RUN** |
| Podcast & Voice journey and persistence | **NOT RUN** |
| Review & Rehearsal Preview boundary | **NOT RUN** |
| Generic meeting handoff and native Webex boundary | **NOT RUN** |
| Shared Track audibility and source identity | **NOT RUN** |
| Mono, multichannel, stereo, server-stem, and guest capture | **NOT RUN** |
| Failure recovery and fail-closed finalization | **NOT RUN** |
| Studio editing, persistence, and export | **NOT RUN** |
| Workstation UI, compact layout, and accessibility | **NOT RUN** |

Release recommendation: **NOT RUN**. Do not tag, publish, promote, or call
v0.26.0 demo-ready from this checklist until every required row has exact
evidence and every release-decision gate is explicitly reviewed.
