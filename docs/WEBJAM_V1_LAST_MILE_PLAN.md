# WebJam v1 last-mile readiness record — v0.14 historical baseline

> **Superseded for active test nights.** This is the verified v0.14 package
> record retained as rollback evidence. Use [README.md](../../README.md),
> [CLOSED_PILOT_PLAYBOOK.md](../../CLOSED_PILOT_PLAYBOOK.md),
> [SUNDAY_TWO_MAC_PILOT.md](../../SUNDAY_TWO_MAC_PILOT.md), and
> [RECORDING_AND_STUDIO.md](../../RECORDING_AND_STUDIO.md) for the current
> v0.16 Jamulus-first test-night flow and Logic-like Studio boundary.

**Working branch at the time:** `master`
**Historical package candidate:** `0.14.0` private Apple-Silicon test-night
candidate, built from `045c5acb01687a4088b0bd618dab4d0ab6200804`; its package
verification was complete and its physical musician certification remained
**NOT RUN**.
**Exact historical artifact:** `WebJam-v0.14.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
`cbcbdc038ac3d663e15870990ae5fea2a09819cdd55adbaa7463a64405ef8321`
**Previous rollback candidate:** `WebJam-v0.13.0-TEST-NIGHT-macos-arm64.zip`,
SHA-256 `6b32a1d85cb64eb0bc97fecb7dadcd527159420a675358176cd75745d6565b3b`,
built from `4d09810d7fb3c7f7355ca1d88e8218bb8ea784dd`. It does not contain the
v0.14.0 Studio workspace.
**Historical package baseline:** v0.12.0 remains preserved rollback evidence.
**Exact historical artifact:** `WebJam-v0.12.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
`01427316820b884d61546d40a9327a49cedf43d6a60a4d88b5b29ab4c693a24c`
**v0.14.0 additions:** the shipped Studio workspace adds a seconds-only ruler,
inspector, gain/pan/mute/solo/export choices, and a durable per-take sidecar
keyed by schema-v2 IDs. Durable recovery, Logic-export truth, and one-use remote
invitation handling remain included. Package integrity/launch checks are
recorded below; physical-musician results are separate and **NOT RUN**.
**Current package gate:** **1,719 passed, 18 skipped, one known warning, and 6
subtests**; transport `go test ./...` and `go vet ./...`; fresh-extraction
strict/deep signature, nested-app, and exact fabric-ID checks; and two isolated
six-second offscreen launch/TERM cycles all passed.
**Baseline CI:** GitHub Actions run `29296365785` passed on 2026-07-14.

This is an implementation record, not a release claim. It separates source
behavior, package evidence, and physical musician evidence so a green unit
test can never be confused with a successful rehearsal.

## Product path

The ordinary supported path stays intentionally small:

1. Choose **Host a Jam** or **Join a Jam**.
2. Confirm the musician name and band sound in one concise screen.
3. Complete Band Check when the saved setup is missing or changed.
4. Host: WebJam starts and supervises the local Jamulus server and client,
   checks the private-LAN pre-share facts, then enables **Copy Invite**.
   If Wi-Fi, sleep/wake, or an interface change gives the host a new private
   address after a link was copied, WebJam stops calling the session simply
   ready and asks for one **Copy New Invite** action.
5. Join: paste/open the complete invitation, pass Band Check, and WebJam starts
   the client.
6. Play. Recording, Studio, conversation, settings, and diagnostics stay under
   **More**.

No ordinary musician is asked for an executable path, server address, UDP
port, router change, or Webex credential.

## Supported network and audio truth

- **Supported ordinary topology:** v1/v2 private RFC1918 IPv4 LAN. A host
  advertises only after the authenticated hosted server is alive, UDP 22124 is
  locally bound, and WebJam has a private LAN address. This is a pre-share
  local readiness check, not proof that an external home or NAT is reachable.
- **Not supported or claimed:** public Internet hosting, VPNs, IPv6,
  NAT traversal, router automation, public rendezvous, and a deployed relay.
  The v3 `reference-local` profile remains a loopback/CI lab boundary.
- **One-use remote invitation boundary:** this applies only to the v3 laboratory
  profile, not the ordinary v1/v2 LAN flow. WebJam may retry the same invitation
  only when the native sidecar failed before `open_guest` began enrollment. Once
  enrollment was attempted, it clears the invitation and requires a fresh one.
  It never falls back to a legacy LAN launch or implies that a consumed
  credential remains valid.
- **Live audio truth:** Jamulus is the performance-audio path. Webex is
  optional conversation/video and should keep its microphone muted while
  musicians play. The v0.14.0 candidate resolves a stable CoreAudio pair,
  rejects missing/ambiguous/non-48-kHz hardware, and stages a WebJam-owned
  filename-only Jamulus config before launch. A WebJam PortAudio meter or
  scratch recording still does not prove a bandmate can hear the route; real
  two-way audibility remains a human gate.

## Current implementation milestones

| Milestone | Source status | Evidence / boundary |
| --- | --- | --- |
| Minimal Host/Join, sound confirmation, and progressive Band Check | Included in v0.14.0 | Qt coverage exercises the first screen, confirmation, invitation ingress, and preflight gate; the exact package contains this flow. Physical two-Mac confirmation remains NOT RUN. |
| Owned Jamulus host/client lifecycle | Implemented | Host server authentication, private secret permissions, duplicate prevention, idempotent stop, and reconnect coverage exist. A long event-loop pause (including likely sleep/wake) clears the connected/roster claim until fresh live evidence arrives. |
| Authoritative session lifecycle | Implemented on this branch | `core/session_lifecycle.py` records preflight, host/join, private-LAN share readiness, roster-confirmed connection, degraded/reconnect, recording finalization, cleanup, and recoverable failure. An active cancellation closes through ending/completed/idle rather than leaving stale state. It supplies the diagnostics timeline. |
| Honest pre-share readiness | Implemented on this branch | `core/host_share_readiness.py` fails closed when the server, UDP listener, or private Wi-Fi address is missing. It does not call that an Internet reachability test. |
| LAN address-change truth | Implemented on this branch | A copied v1/v2 LAN invite is process-locally tied to its advertised address. A changed private address produces a plain **Copy New Invite** recovery state; no address is persisted or added to diagnostics. |
| Band Check | Implemented | Ready / Warning / Action Needed results retain independent local, production, and musician-confirmed evidence. |
| macOS Jamulus device route | Included in v0.14.0 | Settings persists CoreAudio UIDs; launch uses a protected owned config and filename-only `--inifile`, freezes that plan through reconnect, and fails closed if hardware changes. It is OS preflight, not graph/hearing proof. |
| Recording and Logic handoff | Included in v0.14.0 | The candidate checks a writable recording folder and a conservative PCM24 reserve before a take; Record recalculates for the actual roster and starts nothing if storage is unsafe. Its schema-v2 session-evidence portion writes WebJam-observed UTC start/end timestamps only after recorder-server confirmation, host/protocol, and a bounded redacted lifecycle/recovery timeline—never an invite, address, credential, or raw device identifier. Atomic output/recovery, alignment evidence, common-origin PCM24 stems, checksums, and import instructions exist. Physical recording recovery and Logic import are still NOT RUN. |
| Durable local recovery | Included in v0.14.0 package | Local writers periodically flush and synchronize audio before recording opaque identity, durable-frame, gap, and capture facts. Startup turns abandoned safe-to-adopt captures into visible **Needs Attention** recovery projects; audio beyond the last durable checkpoint is marked unverified/partial instead of being silently treated as complete. A recovered guest original stays local and is not automatically transferred. Package integrity/launch checks pass; physical interruption evidence is **NOT RUN**. |
| Conservative Logic-export selection | Included in v0.14.0 package | Studio keeps per-track export selection outside the immutable manifest. A selected explicitly silent track, or an unaligned guest/local original, blocks a misleading package until the musician deselects it, uses the aligned server track, or aligns and verifies it. Package integrity/launch checks pass; Logic Pro import remains **NOT RUN**. |
| Studio review workspace | Included in v0.14.0 package | A shared elapsed-seconds ruler, selected-track inspector, compact gain/pan/mute/solo/export controls, and atomic schema-v2-ID sidecar are shipped. It is not a tempo grid or DAW claim; physical Studio use remains **NOT RUN**. |
| One-use remote invitation retry | Included in v0.14.0 package | A retry is safe only before the v3 sidecar begins `open_guest` enrollment. After that attempt, WebJam clears the invite and requires a fresh one. This remains loopback/CI laboratory behavior, not public remote-session support or a physical remote-session result. |
| Privacy-safe diagnostics | Implemented | Preview, clipboard, JSON, and ZIP derive from one allowlisted/redacted snapshot. The lifecycle timeline contains no invitation, address, device, or path data. |
| v0.14 macOS arm64 package | Exact private test-night artifact | Fresh extraction passes strict/deep signature, nested-app inspection, exact fabric build-ID validation, and two isolated six-second offscreen launch/TERM cycles. The package is arm64, bundles Jamulus/JamulusServer 3.12.2, and remains ad-hoc signed. Physical musician results are NOT RUN. |

### Studio v0.14 review workspace — packaged; physical evidence pending

The exact v0.14.0 artifact recorded above contains this work and passed its
source, transport, fresh-extraction, and bounded-launch package gates. It does
not revise the earlier v0.13.0 rollback artifact. Two-Mac review, CoreAudio,
recording/interruption recovery, and Logic Pro import remain **NOT RUN** until
the worksheet is completed.

- **Truthful time axis:** Studio draws one shared elapsed-seconds ruler for the
  recorded project. It does not invent bars, beats, a tempo map, automation, or
  DAW editing capability the take does not contain.
- **Aligned review:** Seeking aligns the transport, ruler, and displayed lane
  playheads. Selecting a lane exposes recorded source, media/alignment evidence,
  gap truth, and next-export inclusion rather than inferring these from a
  waveform.
- **Compact lanes:** fixed, readable track identity and control space survives
  the 760×600 floor; contextual inspection may collapse before the transport or
  mute/solo/gain/pan controls become clipped.
- **Non-destructive persistence:** `.webjam-studio-state.json` is an atomic,
  private, take-bound sidecar for gain, pan, mute, solo, and export inclusion.
  It is keyed by schema-v2 durable `track_id`, rejects malformed/mismatched
  state, and never changes `webjam-take.json` or source WAV bytes.
- **Durable Logic handoff:** schema-v2 mix and export state resolves by track
  identity, so a revised/reordered track list cannot silently assign one
  musician's choice to another. This does not weaken existing media, silence,
  or alignment export blocks.

## Highest-risk failure modes and response

| Condition | Product response | Evidence level |
| --- | --- | --- |
| Duplicate Host/Join click | One owned launch/reconnect path; repeated starts are ignored while a gate or process is active. | Automated |
| Host server cannot start or UDP listener is absent | Invite stays unavailable; the HUD provides one next action. | Automated local state |
| No private Wi-Fi address | Invite stays unavailable and asks the host to connect to the band Wi-Fi. | Automated local state |
| Wi-Fi/interface change after sharing | HUD calls out the old LAN invite and requires one new copy action before calling the host ready again. | Automated local state |
| Sleep/wake or long app pause | Live roster/connected evidence is cleared and WebJam rechecks the music connection before returning to a connected state. | Automated source state; physical sleep/wake remains NOT RUN |
| Process exit or silent RPC | Lifecycle becomes degraded/reconnecting; bounded retry keeps mix state and ends in one retry action if exhausted. | Automated |
| Stale terminal callback | Lifecycle rejects a transition that would resurrect a completed/failed-final session. | Automated |
| End Session during an active host take | Recording finalizes before owned processes are released; a failed finalization leaves the session protected. | Automated; physical media review pending |
| Recording folder unavailable or storage dangerously low | v0.14.0 Band Check reports one corrective action; Record rechecks before any local capture or server recorder starts, then starts nothing if unsafe. Low storage warns to make room before a long rehearsal. | Source and package inspection pass; physical drive-full recovery remains NOT RUN |
| App interruption during an in-progress take | The v0.14.0 package retains bounded/redacted session evidence and durable audio-frame checkpoints. It exposes recoverable media as **Needs Attention**; unverified post-checkpoint data is partial, never a completed take. | Source/package integrity passes; physical interruption recovery is NOT RUN |
| Selected silent or unaligned original in Logic export | v0.14.0 blocks the export and gives the musician one corrective choice: deselect the track, keep the aligned server track, or align and verify the local original. | Packaged behavior is included; Logic Pro import is NOT RUN |
| Studio lane reorder or a newly reconciled track | v0.14.0 state follows durable schema-v2 `track_id`, not a display index; new tracks receive defaults. | Source/package coverage passes; physical review is NOT RUN |
| Studio review changes source evidence | v0.14.0 writes only an atomic, private `.webjam-studio-state.json` sidecar; manifest/WAV bytes remain untouched. | Source/package coverage passes; physical review is NOT RUN |
| Studio time display implies unrecorded musical facts | v0.14.0 labels a shared elapsed-seconds ruler and omits bars/beats/tempo/automation fiction. | Source/UI review passes; physical usability remains NOT RUN |
| v3 sidecar fails before enrollment | v0.14.0 may offer the same one-use invite again because no enrollment was attempted. | Included package behavior; v3 remains a loopback/CI laboratory profile |
| v3 guest fails after enrollment begins | v0.14.0 removes the invite and asks for a fresh one; it does not retry a potentially consumed credential or fall back to LAN. | Included package behavior; v3 remains a loopback/CI laboratory profile |
| Webex duplicate music path | Webex stays optional and its role is explained as conversation/video, not performance audio. | UI/source evidence |
| Remote home/NAT reachability | Not claimed. The user gets no public-share promise from the private-LAN flow. | Physical/deployment gate NOT RUN |

## Acceptance evidence

Run from the repository root with the project virtual environment:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m ruff check webjam_qt/ core/ ui/ services/ api/
git ls-files '*.py' -z | xargs -0 ./.venv/bin/python -m py_compile
make -C transport check
(cd transport && go test -race -count=1 ./... && go mod verify && go mod tidy -diff)
QT_QPA_PLATFORM=offscreen ./.venv/bin/python ux_smoke_test.py
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -m pytest -q \
  tests/test_recording_studio.py tests/test_studio_state.py tests/test_take_export.py
```

The historical baseline source checks covered lifecycle transitions,
support-bundle redaction, pre-share refusal when a local fact is missing, and a
copied LAN invite after a private Wi-Fi address changes. Those earlier counts
are historical only. The final v0.14.0 full source gate completed with **1,719
passed, 18 skipped, one known warning, and 6 subtests**, with no failures or
errors. Transport `go test ./...` and `go vet ./...` also passed. The exact
v0.14.0 package was then built from
`045c5acb01687a4088b0bd618dab4d0ab6200804` and passed its documented
fresh-extraction/signature/nested-app/fabric-ID/two-launch gates; that remains
distinct from physical musician certification.
GitHub Actions run
`29311760834` passed its then-current reference service, Python/UX, transport,
real-Jamulus integration, and macOS arm64/x64 plus Windows x64 packaging jobs.
The downloaded CI artifacts identify build `e4172a84cdbddbfe34e9e9d89ba61c245d00551c`;
they are candidate evidence, not test-night certification. The full CI matrix
remains the authoritative cross-platform build evidence. The v0.14.0 local
artifact evidence above is deliberately kept distinct from historical CI and
from the physical musician certification still required.

## Manual certification gates — never inferred from source tests

- Two independent Apple-Silicon Macs, the exact v0.14.0 packaged candidate,
  same private LAN
- Both musicians hear each other through the actual Jamulus route
- Host/link/join and reconnect after outage/interface changes
- Host server take, guest original delivery, Studio playback, alignment/drift
- Studio's seconds-only shared ruler, seek alignment, selected-track
  source/alignment/gap inspection, compact layout, and sidecar reopen without
  changing manifest/WAV evidence
- A durable-ID Logic export selection that stays with the identified source,
  not a reordered display lane
- Logic Pro import/playback of the exported package
- Packaged VoiceOver/NVDA review
- Developer ID signing and notarization before broad distribution

Use [SUNDAY_TWO_MAC_PILOT.md](../SUNDAY_TWO_MAC_PILOT.md) for the operator
worksheet. Mark every unobserved physical result **NOT RUN**.

## Rollback

The v0.14.0 private artifact is ad-hoc signed and has no public release tag.
For a test-night rollback, quit WebJam, preserve diagnostics/takes, remove the
candidate app, and restore the preserved v0.13.0 rollback artifact described
above. Do not overwrite an installed app in place.
