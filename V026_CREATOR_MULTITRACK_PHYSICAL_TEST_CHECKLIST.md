# WebJam v0.26.0 creator-multitrack physical test checklist

> Physical-test status: **NOT RUN**. Immutable v0.26.0 **was** the GitHub
> **Latest** private test release when release `371442375` was published. Its
> automated release identity is verified below; no physical, hardware,
> provider, accessibility, or production-trust PASS is implied. Use only exact
> release assets whose hashes match the published checksum manifest.

Use only exact candidate packages whose filenames, SHA-256 values, app version,
and source build IDs agree with the published manifest. Record no invitation or
meeting links, credentials, device UIDs, private paths, participant names, or
raw exceptions. A result may change only after its observation and evidence
reference are recorded in the controlled test record.

Required bench: two physical computers, wired headphones for both people, one
real multichannel audio interface, at least two mono inputs, one stereo source,
and a second participant able to upload guest originals.

## Exact candidate identity

| ID | Evidence to freeze before physical testing | Result |
| --- | --- | --- |
| I01 | Annotated `v0.26.0` tag object and peeled commit | **VERIFIED — automated release evidence:** tag object `3989baadaaa00b4655115e23cf900ea2c1c7fd4c`; peeled candidate commit `4b5208098981943df8ddaf1fac31aa36c15146bb` |
| I02 | Candidate and unique successful native CI | **VERIFIED — automated release evidence:** candidate master CI `31971991226`; tag CI `31973256062`, attempt 1; release job `95231413287` |
| I03 | Exact Windows, macOS arm64/x64, Ubuntu, and manifest assets | **VERIFIED — automated release evidence:** IDs/names: `517251779` `WebJam-linux-x64.zip`; `517251778` `WebJam-macos-arm64-ADHOC-TEST-ONLY.zip`; `517251781` `WebJam-macos-x64-ADHOC-TEST-ONLY.zip`; `517251780` `WebJam-v0.26.0-macos-arm64-ADHOC-TEST-ONLY.dmg`; `517251786` `WebJam-v0.26.0-macos-x64-ADHOC-TEST-ONLY.dmg`; `517251782` `WebJam-v0.26.0-SHA256SUMS.txt`; `517251783` `WebJam-v0.26.0-windows-x64-UNSIGNED-TEST-ONLY-setup.exe`; `517251787` `WebJam-windows-x64-UNSIGNED-TEST-ONLY.zip` |
| I04 | Package sizes, SHA-256 values, app version, and source build | **VERIFIED — automated release evidence:** v0.26.0 from `4b5208098981943df8ddaf1fac31aa36c15146bb`; Linux `168211648` / `9b7216fa8591de0edb5e34dc45bb0b1a59e413bf9572c8e7c6c3c018ef72082e`; Mac arm64 ZIP `216225400` / `9c92fa23ba334166b5d3fac6f26965d3a59519af6707f3f7fb5c2abdca04a80b`; Mac x64 ZIP `222536890` / `e3d3a1875cedcd232fba6ed4ba22d99e8016d6bd717736f4b66c9757c3691da3`; Mac arm64 DMG `217302612` / `92ea140b1f5f820cae525f35b76e68af7c3d8a8d4fb330f200a3c40ec6659163`; Mac x64 DMG `223532070` / `043339f5f45858ab7eec0df0a884a50acd841056103303e320108f2f8b9abbe7`; Windows Setup `144846325` / `a3ec7711500836ced1bd0168107c441ef88681f1d48f770e31188cc9ed01b03d`; Windows ZIP `165555420` / `0a1df1d8868e3b687824b84ff0bf75af2d1b07ba4fdb2bc0e0870e530658df32` |
| I05 | Checksum manifest, body, and complete sorted inventory | **VERIFIED — automated release evidence:** manifest ID `517251782`, `749` bytes, SHA-256 `c5c9e07c33ac74a62110ef60442fe8994cc4512adfe6dfe70a43d1986da7d77e`, seven package lines verified; release-body SHA-256 `404c5378017a37df6c5813d39348d16c386492a7acccd23797a3659495dea4da`; inventory SHA-256 `e6c49c6568877961ce484fa9dc477d8939c8bf881dfd568497da5752199d3aa3` |
| I06 | Protected publisher, deployment, and immutable release | **VERIFIED — automated release evidence:** release-control commit `6b944ea1ef4693c85f4c9af453b56af38e0af8aa`; CI `31975672599`; publisher run `31976890936`, proof job `95237620181`, publish job `95237650912`; deployment `5936210571`, successful status `16891234364`; release `371442375`, published `2026-08-16T22:40:56Z`; <https://github.com/rupret007/webjam/releases/tag/v0.26.0> |
| I07 | Physical client/server Jamulus identity and package build IDs | **NOT RUN — no physical package run recorded** |
| I08 | Tester, machine, OS, interface, headphones, and channel-map record | **NOT RUN — no physical package run recorded** |

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

Release recommendation: **NOT RUN**. Publication already completed from the
verified automated identity above; do not infer demo readiness, replace release
bytes, or convert any physical/release-decision row to PASS until that exact
observation has dated evidence and explicit review.
