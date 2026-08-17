# WebJam architecture — v0.26.0

> This document describes the source behind the immutable v0.26.0 GitHub
> **Latest** private test release, published from exact tag commit
> `4b5208098981943df8ddaf1fac31aa36c15146bb`. Source text is not package
> evidence; verify an exact release asset against
> `WebJam-v0.26.0-SHA256SUMS.txt`. All v0.26.0 physical, credentialed, signing,
> and platform-trust gates stay **NOT RUN**.

## Product boundary

WebJam is an orchestration layer around unmodified Jamulus and optional
meeting services reached through hardened external links. The boundary is deliberate:

| Layer | Responsibility |
| --- | --- |
| `webjam_qt` | Host/Join launch, Session HUD, live Shared Track deck/transport, Record Session and session-Studio UI, standalone Reference Studio, recovery messages |
| `core/creative_modes.py` | Canonical Music and Podcast & Voice GA profiles, Review & Rehearsal Preview profile, safe defaults, legacy aliases, and cross-surface presentation vocabulary |
| `core/song_*`, `core/project_*`, schema-3 Studio | Portable Reference Studio project/media ownership, local playback/recording, non-destructive arrangement/mix, and bounce |
| `services/bridge_service.py` | Direct owned-process launch/stop, hosted-server supervision, authenticated Jamulus RPC, and verified managed/embedded/explicit/system component resolution |
| `core/jamulus_profile.py` | Dedicated Jamulus profile launch contract and private, allowlisted restart records |
| `core/jamulus_compatibility.py`, `core/component_*` | Exact approved Jamulus identities, signed-catalog verification, bounded downloads, per-user component storage, atomic pointers, and rollback |
| `services/jamulus_component_update.py`, `services/jamulus_component_platform.py` | Async update orchestration, idle proof, explicit OS approval, macOS upstream trust verification, and path-free presentation/diagnostics |
| `services/webex_app.py` | macOS exact-bundle launch/reopen, fresh foreground observation, exact-PID publisher re-verification, and typed result; cross-platform native-app detection; and explicit official Cisco installer-browser handoff; no meeting URL in Show, credentials, redistribution, or silent installation |
| `core/pocket_stage.py` | Strict mobile protocol, one-use capabilities, immutable paired projection, and semantic command/receipt contracts |
| `services/pocket_stage_gateway.py` / `services/pocket_stage_tls.py` | Explicit private-Wi-Fi WSS listener and ephemeral pinned TLS identity, separate from the Local API |
| `ios/` | XcodeGen app specification, native SwiftUI companion, strict Swift protocol/transport tests, and owner-device Personal Team workflow |
| `core/jamulus_roster_identity.py`, `core/session_transfer*.py` | Process-bound ordered-roster observations, cooperative Presence v2 correlation, Local Original obligations, and resumable verified delivery |
| Jamulus | Live devices, channels, buffer, jitter, quality, mix, and actual music connection |
| Meeting service | Conversation/video meeting state and device controls |

Creator profiles select presentation and safe workflow defaults; they do not
create a second recorder or weaken evidence. Profile keys persist in new
session metadata, takes, and standalone projects. A missing legacy key migrates
to Music. Review & Rehearsal is always visibly Preview: it allows live
WebJam-audio Host/Join, session recording, and playback/read-only completed-take
review, while refusing standalone project create/open, take editing/comp/mix
mutation, track export, shared notes, visual sync, and media-timecode behavior.
No profile directly or automatically taps a meeting app, browser, or system
output.

Standalone Reference Studio has its own project and local-audio lifecycle. It
does not start, join, stop, configure, or feed Jamulus. Its persistence,
migration, rendering, recording, and trust boundaries are defined in
[ADR 0006](docs/adr/0006-standalone-reference-studio-projects.md).

## Jamulus component trust and lifecycle

The desktop package always contains its reviewed Jamulus 3.12.2 fallback.
Independently updated Jamulus client/server packages are executable supply-chain
inputs, so availability alone is never approval:

```text
separate component release
  -> release-locked Certifi TLS trust + bounded HTTPS origin/redirect policy
  -> canonical Ed25519 catalog signature
  -> expiry + monotonic sequence/rollback protection
  -> exact WebJam/target/architecture/role/capability policy
  -> streaming size/SHA-256 verification into private cache
  -> explicit platform approval and installed-result verification
  -> atomic current/previous pointer
  -> BridgeService resolution at the next idle launch
```

The catalog private key is release-operator material and is never stored in the
repository or package. The desktop embeds only a public key. The signed payload
must target the exact WebJam version and expires within 31 days. Repeated
sequences are accepted only for byte-identical signed payloads; older sequences
and same-sequence changes fail closed.

Frozen Python does not use an ambient OpenSSL CA location for this boundary.
`UrllibHttpsTransport` constructs a hostname-checking, `CERT_REQUIRED`,
TLS-1.2-or-newer context from the Certifi bytes shipped in the exact WebJam
package. `SSL_CERT_FILE` and `SSL_CERT_DIR` are not alternate updater trust
inputs. Transport diagnostics expose only finite trust source/status,
redirect-policy, and failure-category values—never a CA path, URL, proxy,
credential, or raw exception.

Download and activation are separate. Downloading may occur during a session,
but install, activation, or rollback requires proof that no participant client,
host/practice server, Reference Track, recording, reconnect, or launch owns the
Jamulus boundary. A cross-process lock closes the second-instance race. Each
use revalidates the managed result; failure falls back to embedded 3.12.2
without modifying `WebJam.app` or killing a process.

macOS keeps the unmodified upstream client/server pair in the per-user store.
The official DMG is not mounted until the user reviews the exact packaged
license and explicitly agrees. Deep signature, Team `V9ZZ6B9WH8`, bundle IDs,
version, architecture, notarization, inventory, internal signed symlinks, and
quarantine are verified. Windows and Linux retain OS-owned installation:
WebJam rehashes the approved upstream package immediately before explicit
handoff, never invokes a shell or hidden elevation, and uses the installed copy
only after runtime compatibility proof. The Windows upstream installer is
truthfully treated as unsigned.

The custom HEADLESS Reference Track client is a separate role and cannot be
substituted by a GUI client/server catalog entry. Its 3.12.3 build remains
evidence-only pending qualified review; production continues to use the
embedded reviewed 3.12.2 HEADLESS companion.

Jamulus participant identity is a separate exact contract:
`core/jamulus_name.py` accepts visible text up to 16 UTF-16 units, rejects
controls/newlines/overlength input, preserves valid Unicode, and exposes the
8+8 mixer-layout preview. Every editor, migration, profile, launch argument,
and RPC path passes through that same validator.

## Meeting-service and native Webex boundary

WebJam persists one meeting link only after a provider-neutral policy accepts
it as a public HTTPS URL with a DNS hostname. The policy rejects credentials,
custom ports, local/special-use names, IP literals, percent-encoded hosts, and
known-brand lookalikes. Known Webex, Zoom, Microsoft Teams, Google Meet, and
FaceTime origins map to friendly provider facts; any other accepted origin
maps to a neutral generic provider. This is external handoff—not provider
authentication, verification, or meeting membership. The selected service
owns participant identity, camera, microphone, speakers, mute, and meeting
state. Validation performs no DNS lookup, HTTP request, redirect, or
reachability probe; the operating system owns the later user-authorized
handoff. FaceTime keeps its Mac-only platform gate. Native app detection remains
a Webex-specific diagnostic convenience; an explicit install action opens
only an approved Cisco HTTPS URL and does not download or execute a package
itself.

Neither link handoff nor Webex-native focus creates a capture source, and
WebJam never directly or automatically taps a meeting app, browser, or system
output. WebJam's take evidence can include only Jamulus server stems, Shared
Track recorder identity, and explicitly planned Local Originals from input
devices the user selects. Input capture cannot classify externally routed
content, so users must not route meeting or system-output audio into those
inputs. Meeting-service recording remains owned by that service.

The direct Live **Conversation** action and **More → Conversation** are
navigation only. They reveal the Conversation panel without opening the link.
On macOS, **Show Webex App** dynamically finds the exact Cisco process when
running and re-verifies that PID. It creates a retained Core Foundation
file-reference URL, validates that bound filesystem object against Cisco's
designated requirement, and passes the same reference directly to
`NSWorkspace`. If stopped, the same no-document request launches that object.
WebJam then proves the exact path identity, PID, Cisco publisher, and foreground
state; pathname replacement cannot redirect launch, and native request
acceptance is not success. Webex decides which of its screens appears. This
action passes no meeting URL and opens no browser; **Join / Open Meeting**
performs the sole explicit URL handoff. **Open Webex to Mute** can
only show the verified app for its own Mute control because this external-app
integration cannot verify mute state.
Windows and Linux currently detect only an executable location, not a verified
publisher identity, so native focus and focus-based mute guidance fail closed
there. None of those actions alter Jamulus. The direct
**Studio** action reuses the existing session/offline Studio route rather than
creating another editor lifecycle.

The controller retains at most 12 allowlisted conversation action/result events for
diagnosis: Conversation navigation, show-app or mute guidance, and meeting
handoff. The Support Bundle sanitizer revalidates those finite values and
optional reason codes; no URL, meeting ID, account, participant, app path,
credential, or raw exception crosses the boundary. Known allowlisted services
may retain only an origin-level redaction where that bounded projection is
needed. An unknown provider's URL and hostname are fully redacted from logs,
mappings, diagnostics, and Support Bundles; generic acceptance never promotes
it to natively verified status.

The v0.26.0 source carries forward one canonical **Shared Track** workflow; existing
`ReferenceTrack*` types, paths, tests, and the ADR remain compatibility names
for the established route engine, not a second live feature. Shared Track
separates source and route authority. A host can load, decode the first bounded
block, and inspect a source while route capability is unavailable. The
immutable v0.22.2 production backend refused route capability
before device scanning, so BlackHole setup and **Recheck Route** cannot unlock
that downloaded package. The published v0.22.4 production factory instead
derives prerequisite authority from an official, unambiguous 48-kHz BlackHole
16ch/64ch route on the Mac. That machine check may make Play available; it does
not start playback or bypass the exact live-process proofs at startup.
The source snapshot exposes only bounded transport facts plus a fixed-size
waveform summary prepared away from the realtime callback. The callback uses
preallocated buffers/rings and scalar generation counters; it performs no
filesystem I/O, UI work, network call, logging, or blocking wait. The support
projection exports allowlisted source format/rate/channel/duration,
count-in/cleanup/dropout counters, and finite route state, never a source name
or path.

The retained controlled macOS pilot uses a separately owned Jamulus process,
session-unique descriptor-pinned profile and RPC-secret files, and one global
WebJam lifecycle claim shared across eligible BlackHole devices. A kernel
socket inherited by the child preserves that claim if the parent exits.
Playback authority requires fresh PID-bound CoreAudio proof for both primary
and backing clients. Source replacement/removal requires the route to be
stopped; an active owner is never implicitly torn down by a load command.
Cleanup retains every owner and reports a retryable `cleanup_pending` state
until the process, RPC, private files, and route lease are all proved retired.
The production path remains fail-closed on absent, changed, or ambiguous
evidence. The host publishes a strict, generation-monotonic, path-free
`SharedTrackSessionSnapshot` through the authenticated peer state; guest
renderers observe it without transport authority. Playback position is
memory-only rather than repeatedly persisted. Legacy peers may fall back to
bounded dedicated-channel presence, but roster presence alone is never
promoted to transport, synchronization, isolation, or audibility truth.
Machine-derived route authority is not physical audibility, direct-monitor,
independent-mix, or long-session proof; those acceptance gates remain **NOT
RUN**.

A future Webex Embedded App is described in
[ADR 0007](docs/adr/0007-future-webex-embedded-app-companion.md). It is a
separate hosted and authorized product surface, not a hidden desktop webview.
It may expose focused status and approved controls through a secure
synchronization boundary, while the desktop remains the authoritative
audio/session engine.

## Unified creator guidance

`core/musician_guidance.py` is a pure projection, not another lifecycle or
state machine. It accepts one already generation/revision-guarded
`SessionConductorSnapshot`, optional bounded lifecycle transitions, and the
local creative pulse. It returns an immutable `MusicianGuidanceSnapshot` used
by every renderer.

| Owner | Facts it owns | Guidance responsibility |
| --- | --- | --- |
| `SessionConductor` | canonical role, phase, one action, evidence limit, attempt generation/revision | operational backbone and stale-observation rejection |
| `SessionLifecycle` | bounded accepted transition history and cleanup/recovery phase | reason-free recent events only |
| `RecordingCoordinator` | idle/preparing/count-in/recording/stopping/finalizing/ready/attention state, take availability, validation result | Record Session and take output status |
| `GuestPeerSession` / transfer store | active local capture, durable queue, verified receipt, missing/recovered media | bounded guest-media and preservation status |
| `RecordingStudio` / `StudioProjectController` | selected-take revision, validation, dirty/save state, export eligibility | review, non-destructive edit, and export readiness facts |
| Studio export worker | exporting/completed/needs-attention result | export output status; completion is cleared by a new take or edit |
| `session_intelligence` | deterministic structures extracted from intentional local notes | creative suggestion only; never operational evidence |
| `ApplicationController` | fact collection, topology-specific fixed display overrides, semantic command routing | distributes one snapshot; it does not manufacture success |

Dependency direction is core facts → conductor → pure guidance → controller →
Qt/public renderers. Core imports no Qt. Renderers do not derive their own next
action. Fixed native-setup and topology recovery wording uses
`GuidanceDisplayOverride`: it may clarify title, message, action ID, and local
label, while the accepted conductor phase, evidence, outputs, generation, and
revision remain authoritative.

Operational and creative information remain separate. Notes can create local
decisions, actions, blockers, questions, references, and checkpoints, but are
excluded from `to_public_dict()` and cannot change connection, audibility,
recording, take, transfer, Studio, export, or cleanup facts.

`SessionPersistence` keeps that scratchpad profile-scoped on the local
computer. Profile switches atomically save/load fixed mode-0600 files. Reads
use regular-file, no-follow descriptors with a 1 MiB ceiling. Notes remain
strictly local-only: they are never shared, session-synchronized, or
media-timecoded.

Refreshes are semantic and idempotent. Recorder phases, accepted connection or
lifecycle transitions, Studio selection/dirty/save/export changes, and a
debounced note edit can produce a new snapshot. Meter, waveform, playhead,
animation, audio callback, capture callback, and playback callback loops do not
derive or announce guidance. A projection or renderer exception is logged and
discarded; it cannot interrupt audio, recording, transfer, or playback.

The public representation contains only finite enum values, booleans,
non-negative generation/revision numbers, fixed output keys/states, and up to
five reason-free ISO-timestamped lifecycle transitions. Diagnostics re-sanitize
that allowlist. The optional localhost Companion API additionally anonymizes
participants into session-local slots. Neither surface receives notes, titles,
participant names, channel IDs, invitations, addresses, device names, paths,
tokens, credentials, or raw exceptions.

No model SDK or cloud assistant is part of v0.26.0. A future model-assisted
creative feature may be considered only as explicit opt-in, off the real-time
path, read-only, privacy-gated, unable to issue session commands or create
operational facts, and visibly labeled as a suggestion. The deterministic
offline path must remain available.

## Frozen dependency boundary

v0.22.5 pins `cryptography` 50.0.0 to remediate CVE-2026-69247,
CVE-2026-69248, and CVE-2026-69249. Windows, Linux, and Apple-silicon macOS
use exact upstream wheels. Intel macOS has one explicit native x86_64
source-build exception because upstream removed that wheel: WebJam verifies
the official source archives and build locks, statically links its private
OpenSSL 3.5.7 LTS input, and proves architecture, linkage, runtime paths,
license evidence, and frozen-package inventory. That exception is not a
general permission to resolve or build source dependencies during packaging.

## Pocket Stage developer-preview boundary

Pocket Stage is an owner-device iPhone companion vertical slice. It is activated
by **More → Use iPhone as Pocket Stage…** and binds a dedicated WSS gateway to
one current private IPv4 interface and a random port. The normal Host/Join path
does not start it. The existing loopback, read-only Local Companion API is
neither modified nor exposed to the LAN.

```text
generated native SwiftUI app
  -> pinned WSS protocol v1
  -> PocketStageGateway
  -> immutable MobileSessionProjection / finite PocketCommand
  -> ApplicationController on the Qt owner thread
  -> existing Jamulus controller or RecordingCoordinator
```

Each sharing session creates an ephemeral self-signed certificate/key. A
one-use, 120-second QR capability contains the endpoint and the exact SHA-256
fingerprint of the leaf certificate's DER bytes. The phone authenticates the
server with that pin and submits one atomic capability claim. There is no
server-issued reconnect credential: the capability authorizes the active
socket only, and disconnection requires a fresh QR.

`MobileSessionProjection` carries session generation/revision, role, phase,
primary guidance, recording state, and session-local participant slots. An
explicitly paired phone may receive a bounded participant display label plus
fader, pan, mute, solo, local-slot, and connection state. Labels are
paired-private content: they are excluded from gateway logs, diagnostics,
support bundles, and the anonymous public Local API. Provider IDs, paths,
invitations, credentials, device names, and raw exceptions never enter the
projection.

The implemented command path permits fader, mute, a timestamped Session
Canvas marker, and host recording start/stop after desktop setup. Commands are
finite, bounded, rate-limited, idempotent by command ID, and guarded by expected
generation/revision and pairing scope. Solo is read-only. Rehearsal-plan and
section transport are not granted/applied, and Studio has no mobile command
surface.

Pan remains in the bounded projection and reserved protocol vocabulary, but
the desktop rejects the command and the iPhone does not present it because the
pinned Jamulus client has no proven pan provider path.

Pocket Stage carries no audio or media. It does not enter Jamulus packets,
device selection, capture, meters, playback, waveforms, Studio export, or other
realtime callbacks. A checked-in XcodeGen spec reproducibly generates the
native app target, which CI builds without signing; owner-device installation
still requires selecting an Apple Personal Team in Xcode. It is not a packaged
iOS release. The real Swift transport has paired with the live Python gateway
under automation, but physical iPhone pairing, OS permission/firewall behavior,
interruption, accessibility, recording, long-session resource use, and Jamulus
non-interference are **NOT RUN**.

## Jamulus-native launch

On macOS `JamulusNativeProfileManager` safely selects the Jamulus configuration
directory and passes only:

```text
--inifile WebJam-native-v0.16.ini
```

Jamulus alone creates and writes that profile. WebJam does not write profile
content or any device/channel/buffer/jitter/quality value. The normal
`Jamulus.ini` is never overwritten. WebJam launches the client directly with
normal GUI visibility, `--connect`, and an authenticated localhost JSON-RPC
surface. It uses no coordinate automation, UI scraping, or undocumented audio
RPC calls.

## Startup state machine

`ApplicationController` projects a role-aware startup attempt through the
shared guidance contract and into `SessionHud`, the stage, Canvas, and Studio:

1. host server start (host only);
2. visible native Jamulus launch and sound setup;
3. process/RPC/connection/local-identity proof;
4. automatic handoff to the ordinary Session HUD and safe invite readiness.

Jamulus setup is not a WebJam approval gate: WebJam watches for fresh,
authenticated connection proof and moves into the session automatically. It
does not call that proof audibility; participants make sound and verify that
they can hear one another, with the profile's Check action available if help
is needed. The direct
**Conversation** action and its **More → Conversation** alias reveal the same optional
Conversation panel without opening a meeting, and no meeting service delays
the session or invite. Direct **Shared Track** and **Studio** actions likewise
reuse their existing live-session destinations.

The persisted attempt record holds only a digest ID, generation, role, safe
server/client phases, profile fingerprint, connection state, compatibility
confirmation/conversation state, and next-action enum. It stores no invite,
credential, URL, device data, path, or notes. A restart resumes only after an
exact profile match and new live proof.

## Recording and Studio

`RecordingCoordinator` owns host recorder state, storage readiness, take
validation, recovery journals, and Local Originals handoff. Its work begins at
**Record Session** time, not at live-audio startup. The creator-facing projection
separates Preparing, Count-in, Recording, Stopping, Finalizing, Ready, Needs
attention, and cleanup pending. One accepted generation owns the request;
duplicate Record/Stop commands and late callbacks cannot authorize a new or
older generation.

One durable `SessionRecordingPlan` is authoritative for each accepted take. It
binds the exact roster and server stem IDs, Shared Track source fingerprint and
playback generation, host logical input topology, take-scoped guest Local
Original count/map obligations and presence generations, count-in/pre-roll,
storage verdict, and expected source count. The invariant is exact: expected
sources equal server stems plus enabled host logical tracks plus every planned
guest logical track. Finalization rechecks the plan and rejects changed maps,
reconnect substitution, under/over-delivery, or a different Shared Track
generation.

Host and guest Local Originals use logical mono/stereo tracks. One mono mapping
produces one PCM-24 mono WAV. One stereo mapping owns adjacent input channels
and produces one PCM-24 two-channel WAV; recovery, take topology, declared
gaps, Studio rendering, and export preserve both channels as one source. An
all-opted-out map means no capture. Only a genuinely empty legacy map receives
the compatible two-mono-track default.

When a loaded Shared Track is route-ready, confirmed recorder start triggers
its count-in/play transition. One Stop Recording request asks both the recorder
and Shared Track owners to retire, but completion remains conjunctive: a clean
recorder cannot hide pending route cleanup, and clean route teardown cannot
hide missing or unverified recording media. Shared Track playback remains on
the separate `WebJam Track` participant and never enters the participant output
through an unproved direct-monitor path.

Jamulus client channel IDs are local mixer coordinates: every client may see
itself as channel zero, so those IDs never identify server recorder stems.
Presence v2 instead binds a process/RPC/audio-generation-scoped ordered-roster
digest to a short host challenge and server-row ordinal. The host's own ordinal
is exact from its process-bound local-zero observation. A remote ordinal is a
cooperative claim by an authenticated, invited WebJam peer—not cryptographic
Jamulus identity. Identical full public profiles and detected collisions fail
closed; duplicate names with otherwise distinct profiles remain valid. Legacy
presence can preserve UI and Local Original delivery but is recorder-ineligible.
Lease rollover, reconnect segments, capture obligations, privacy boundaries,
and the trusted-invite residual risk are specified in
[ADR 0009](docs/adr/0009-presence-v2-recorder-correlation.md).

The authoritative recorder source for `WebJam Track` is classified as
`LIVE_REFERENCE` internally and presented as **Shared Track** in Studio. Its
stable source identity derives from the session/source contract rather than a
take-local filename, so repeated takes do not masquerade as different
participants. Local Originals retain their explicit source class and are never
used as substitutes for missing server stems. Ambiguous filenames, roster
collisions, missing timing references, transfer gaps, and incomplete
publication remain typed failure/waiting states; the recorder does not copy a
stereo mix into several tracks and call it multitrack.

The Studio boundary is layered:

| Layer | Responsibility |
| --- | --- |
| `core.studio_project` | Immutable, path-free schema-v2 arrangement in durable IDs and project frames |
| `core.studio_history` | Thread-safe, entry/byte-bounded exact undo/redo snapshots and gesture coalescing |
| `core.studio_store` | Exact-token, locked, atomic sidecar persistence, migration, backup, and recovery |
| `core.studio_controller` | Selection, dirty state, autosave coalescing, conflicts, and async generation cancellation |
| `core.studio_comping` / `core.studio_source_catalog` | Same-session repeated-take lanes, quick-swipe selections, and full take/track/segment trust binding |
| `core.studio_renderer` | Shared gap-aware frame/rate/DSP truth for arranged playback and export |
| `core.take_player` | Exact transport, output-device lifecycle, cycle delivery, and typed playback failures |
| `core.studio_waveform` | Cancellable visible-range tiles, bounded cache, and descriptor-bound source verification |
| `core.studio_export` | Fail-closed source snapshots and descriptor-relative evidence-rich 24-bit publication on supported macOS/Linux runtimes; unsupported platforms use the separate aligned-originals UI path |
| `webjam_qt.widgets.studio_arrange` | Fixed-header Arrange viewport, frame-domain selection/gestures, keyboard editing, and bounded painted waveform bindings |
| `webjam_qt.widgets.studio_editing` | Arrange, comp, section, snap, fade, and crossfade command surfaces |
| `webjam_qt.widgets.studio_review` | Track review lanes, mixer controls, meters, timeline ruler, and waveform overview widgets |
| `webjam_qt.widgets.studio_waveforms` | Generation-bound visible-tile scheduling and UI-thread delivery |
| `webjam_qt.widgets.studio_arrangement_workflow` | Arrangement/controller synchronization, take lanes, autosave, arranged playback reloads, and Studio export coordination |
| `RecordingStudio` | Take library, live-recording shell, transport/output UI, responsive composition, and worker shutdown ownership |

Studio state is a sidecar beside the take, never an update to `webjam-take.json`
or recorder WAVs. Region removal and source-inventory reconciliation use durable
tombstones so a later rescan cannot silently recreate a user deletion. The same
renderer drives playback and export; a cross-take comp must resolve through the
trusted source catalog before either path opens its media.

Autosave is requested by the framework-neutral controller and scheduled on the
Qt event loop. Save conflicts and disk failures leave the document dirty and
retryable. Switching takes cancels generation-bound waveform work and rejects
late waveform or export results that no longer belong to the selected take;
starting another export or shutting down cancels export work. Studio playback
output remains a review-only choice and is not part of Jamulus configuration.
The extracted arrangement-workflow mixin coordinates those services without
owning widget construction; the shell remains responsible for live capture,
take switching, worker lifetime, and the responsive library/editor/inspector
composition.

The live Recording Studio shell and offline arrangement controller are views
of the same immutable take/project boundary. A finalized take is eligible for
Studio only after its required manifest/media checks settle. Studio track
headers distinguish participant, Shared Track, and Local Original sources while
retaining the existing arrangement, comping, mixer, autosave, recovery, and
export systems; v0.26.0 does not introduce another editor or duplicate audio
engine.

The guest projection is host-state continuity, not distributed local playback,
sample-clock synchronization, or audibility proof. Strict validation rejects
invalid names/timing/loop ranges and stale generations; it contains no control
or audibility field. Before host validation begins, the peer state moves to
Finalizing; only the later terminal result can report Ready or attention. A
host take becoming Ready still does not fabricate completion/alignment for an
outstanding guest Local Original transfer.

## Truth and failure behavior

Jamulus RPC supplies process/authentication/roster/connection facts, never an
invented audio-device or meeting-state claim. A human confirmation supplies audibility.
End/Leave stops only WebJam-owned processes and hosts finalize recording before
server shutdown. The profile-specific Band, Sound, or Session Check is an
optional live observer; it does not restart or configure the live audio engine.
