# WebJam Architecture

> **Last updated:** 2026-07-14 (current source; physical gates remain **NOT RUN**)

## Overview

WebJam is a creative-collaboration shell that runs **Jamulus** as its
low-latency music engine and can optionally launch native **Webex**
(speech/video) alongside its Conductor window. In current source, the normal
product flow is one Host/Join choice, a concise sound confirmation, then Band
Check when the setup is new or changed—not music-engine configuration. Current
source follows that same flow.
Notes, multitrack Studio, settings, conversation, and diagnostics remain
progressively disclosed under **More**. WebJam does not embed or control the
native meeting.

The primary runtime is a **Qt/PySide6 Conductor UI**. A legacy Tkinter UI (`legacy/webjam_app_enhanced.py`) is quarantined for archive/fallback use and is not part of the pilot release path.

---

## High-Level Component Map

```
webjam_qt_main.py          ← entry point
        │
        └─ webjam_qt/app.run()
                │
                ├─ LaunchDialog           (Host or one-link Join)
                │
                └─ ConductorWindow        (responsive live session)
                        │
                        └─ ApplicationController
                                ├─ AudioCoordinator       (launch/practice/live truth)
                                ├─ VideoCoordinator       (Webex lifecycle)
                                ├─ RecordingCoordinator   (RPC/state/take verification)
                                ├─ BridgeService          (process lifecycle/reconnect)
                                ├─ JamulusController      (mixer state + RPC/UDP)
                                ├─ WebexController         (meeting URL + browser)
                                ├─ SessionHud              (one status/recovery surface)
                                └─ ParticipantGrid         (responsive Qt mixer UI)
```

---

## Entry Points

| File | Purpose |
|------|---------|
| `webjam_qt_main.py` | **Primary.** Bootstraps PySide6, shows Host/Join on every launch, then opens `ConductorWindow`. |
| `legacy/webjam_app_enhanced.py` | **Legacy fallback.** Tkinter/customtkinter UI. Not actively developed. |

---

## Core Layers

### `webjam_qt/` — Qt Conductor UI

| Module | Responsibility |
|--------|----------------|
| `app.py` | `QApplication` bootstrap, bundled font, stylesheet, Host/Join gate, and fatal-error boundary |
| `windows/launch_dialog.py` | Responsive two-choice launch plus one-field invitation validation |
| `windows/conductor_window.py` | Main application window — header, `SessionHud`, `ParticipantGrid`, workspaces, and bottom controls |
| `windows/simple_settings.py` | Progressive preferences for displayed name, Band input, Band output & review, and an optional conversation URL; macOS saves stable route IDs for the next Jamulus launch without claiming audibility. |
| `windows/recording_setup.py` | Focused Studio playback-output/Takes-folder selection and explicit two-input local-original consent for the host or an active-v2 guest |
| `windows/ready_check.py` | Permanent guided Band Check; pre-session actions and non-invasive live observation |
| `windows/support_bundle_preview.py` | Preview and save one immutable allowlisted, redacted support artifact |
| `windows/setup_wizard.py` | Legacy detailed configuration surface; not part of the current musician path |
| `widgets/participant_card.py` | Per-channel fader, monitor mute, solo, accessible state, and observed level meter |
| `widgets/participant_grid.py` | Viewport-driven participant layout plus intentional empty/recovery states |
| `widgets/level_meter.py` | Truthful observed level meter with coarse accessible signal descriptions |
| `widgets/session_hud.py` | One authoritative readiness/invitation/recovery summary |
| `widgets/session_strip.py` | Header metadata plus persistent Copy Invite / Record / More / End-or-Leave controls |
| `widgets/recording_studio.py` | Live participant lanes, take library, waveform transport, stereo output, gain/pan/mute/solo, non-destructive per-track Logic-export selection, and background Logic export |
| `widgets/webex_embed.py` | Compact native-Webex launch/status card; legacy embed code is inactive |
| `controllers/application_controller.py` | Wires `ConductorWindow` to services; delegates audio/video/recording to coordinators |
| `controllers/audio_coordinator.py` | Permission gate, launch/recovery/end-or-leave lifecycle, and participant-grid transitions |
| `controllers/video_coordinator.py` | External Webex-launch compatibility wrapper; no meeting control |
| `controllers/recording_coordinator.py` | Recorder state machine, RPC worker, take discovery/validation, durable recovery publication, and completion actions |
| `invitation_ingress.py` | Typed v1/v2/v3 activation boundary; v3 is accepted only through secret-safe paste or Qt file-open ingress, never process arguments |
| `session_state.py` | UI-only truth for connect, reconnect, permission, error, ending, and leaving states |
| `platform_permissions.py` | Dependency-free macOS microphone authorization query; unavailable elsewhere without blocking |
| `windows/take_deck.py` | Validated take library, selectable stereo playback output, review mixer |
| `theme/tokens.py` | Color tokens and QSS stylesheet |

### `services/` — Process lifecycle

| Module | Responsibility |
|--------|----------------|
| `bridge_service.py` | Jamulus client/practice/hosted-server process ownership, reconnect/supervision, and truthful external Webex launch |
| `transport_runtime.py` | Strict ownership of the packaged `webjam-fabric` child process, bounded IPC, fixed profile selection, and secret-free events |
| `remote_session_runtime.py` | UI-neutral lab-only v3 guest lifecycle, immutable connection snapshots, and conservative one-use-invitation retry truth |
| `remote_invitation_owner.py` | Host-side one-use v3 invitation issuance, copy-time serialization, reset, expiry, consume, and revocation |
| `native_remote_transport.py` | Concrete lab-only guest/host adapters for the fixed `reference-local` profile and authenticated connection snapshots |

### `transport/` and `reference_service/` — remote v3 development boundary

`transport/` contains the static Go sidecar and its protocol/security labs. It
owns loopback Jamulus UDP proxying and the encrypted peer session so live
packets do not cross Python IPC. Process readiness or an open loopback proxy is
not connection evidence; the desktop may report a remote connection only after
the authenticated peer session is established and its pumps are running.
Frozen desktop builds ignore sidecar/build-ID environment overrides and accept
only the packaged executable after signed-bundle manifest SHA-256, thin
architecture, safe owner/mode, native platform signature, and embedded build-ID
validation. macOS seals that manifest under `Contents/Resources`; Windows keeps
it beside the executable in its flat bundle.

`reference_service/` is the smallest self-hostable control and exact-pair relay
used by local and CI proof. Its native protocol is bounded TCP NDJSON plus an
authenticated UDP wrapper. It is not HTTP/WebSocket signaling and is not a
general TURN service. The only compiled desktop profile in this slice is
`reference-local`, which fixes all service endpoints to loopback and is marked
lab-only. IPC cannot supply or override an address, URL, credential,
certificate, or path.

Pion ICE/TURN direct and relay behavior remains separately proven in a
deterministic virtual-network lab. That evidence does not mean the native
reference service implements TURN, nor does either lab prove ordinary Internet
NATs. No public profile, public service, production credential, or two-home
result is part of this repository slice.

### `jamulus_controller.py` — Mixer state

Owns the participant map and all mixer operations. Three communication paths:

1. **JSON-RPC** (`rpc_client`) — primary, background-threaded (`_send_rpc_gain`)
2. **UDP** (`_apply_mixer_setting`) — secondary, direct channel gains
3. **Process poll** — tracks Jamulus process health via `jamulus_process.poll()`

Since v0.4: `set_mute()` and `set_solo()` now use `_send_rpc_gain()` so mute/solo reach Jamulus over RPC instead of UDP-only.

Pinned Jamulus 3.12.2 has no client live-send mute method. The controller
declares `live_send_mute = False`; no UI, shortcut, or reconnect path sends or
optimistically renders one. Participant-card mute remains a local monitor-mix
fader operation. For Webex conversation, the musician uses the interface mute
or ends the WebJam session first.

For v3, neither host nor guest places the musician name in Jamulus argv. The
authenticated loopback JSON-RPC connection applies `jamulusclient/setName`
after local-session proof. Legacy v1/v2 keeps its pre-RPC `--clientname`
behavior for compatibility.

### `webex_integration.py` — Webex

External-launch controller for Webex meeting URLs. The compact
`widgets/webex_embed.py` card presents instructions and launch truth, but does
not host a meeting. The only public lifecycle is Not opened, Opening, Opened
externally, or Open failed. Webex URLs must use HTTPS on a trusted `webex.com`
host, without user information or a non-default port; meeting paths are
redacted from logs and diagnostics.

### `legacy/ui/` — Tkinter service layer (legacy UI only)

| Module | Responsibility |
|--------|----------------|
| `mixer_service.py` | Save/load mix, listening profiles (Tkinter app) |
| `auth_controller.py` | Sign-in / authorize gate (legacy) |
| `mode_controller.py` | Mode layout spec + sash computation (legacy) |

The Qt Conductor uses `webjam_qt/controllers/mix_manager.py` and `jamulus_controller.py` directly instead.

### `server/` — Band-server recipe

The packaged macOS pilot uses prepared official Jamulus 3.12.2 client/server
apps with recorder data under WebJam's Application Support directory. With
`host_server_enabled`, `BridgeService` enforces a loopback client target,
verifies the exact server version and port ownership, creates the 0600 recorder
secret, starts the server and per-PID sleep assertion, authenticates recorder
readiness, and supervises crashes independently of the musician client's
reconnect preference. An active or validating host take blocks **End Session**
until **Stop Rec** and **Take saved**; the clear End path then stops the client,
owned server, and sleep assertion in ownership-aware order. **Leave Jam** stops
the guest client after its v2 peer worker finalizes active local capture,
persists the resumable queue, and attempts a final upload.

If TCP 22240 is already occupied, WebJam adopts the endpoint only after the
configured secret authenticates and `getRecorderStatus` proves it is a
Jamulus recorder. Adopted processes are reported as external and are never
terminated—or have recording stopped—when WebJam quits. Remote Linux hosting
remains a developer/legacy-compatible recipe, not the current same-LAN pilot
path. Both paths use `core/jamulus_server_rpc.py`.

## Data Flow: Recording completion

```
Record button → RecordingCoordinator (preflight + roster-aware storage reserve)
  → host opt-in: open LocalInputCapture inputs 1–2 at 48 kHz
  → authenticated loopback JSON-RPC → recorderState (recording + timer)
  → on recorder-confirmed start/end only: retain WebJam-observed UTC timestamps,
    host/protocol, and bounded redacted lifecycle/recovery evidence; atomically
    checkpoint it in a private in-progress journal below Takes
  → guest opt-in: observe authenticated host state, then open LocalInputCapture
  → each local writer periodically flushes + fsyncs stems before advancing its
    opaque take/session-ID, durable-frame recovery checkpoint
  → peer outage never deliberately stops that guest's local writer
  → Stop RPC → finalize atomic host WAVs → wait for stable server files
  → finalize host local segments and write the initial schema-v2 manifest,
    then remove the journal only after publication
  → register expected guest media as missing/receiving truth in that manifest
  → resume guest upload from its verified byte offset
  → require size/SHA/PCM agreement, attach the host copy, and revise atomically
  → refresh the integrated Studio with the current media truth
  → next host startup: publish readable abandoned local media as a recovery-only
    NEEDS_ATTENTION project; recovered guest media remains local for manual
    handling and is not automatically uploaded

Studio Export for Logic
  → apply non-destructive per-track Studio export selection, hash-check sources,
    and reject selected explicit-silence or unaligned/unverified local originals
  → apply immutable offset/drift transforms only after those truth gates pass
  → render each source onto one common-origin project timeline
  → atomically publish equal-length PCM24 stems plus server/Studio references
  → write markers, recording/alignment reports, independent analysis,
    source/export manifests (including nonempty session evidence), checksums,
    and import instructions
```

Local-original capture never participates in the live mix. If it fails,
Jamulus audio and server-recorder shutdown remain authoritative; WebJam keeps
the original/recovered/partial files visible and blocks false completion or
export while required media needs attention. A recovery project is not a
completed multitrack take or timing-ready Logic export.

The recording-session evidence is separate from media metadata: it accepts no
invitation, network address, credential, or raw device identifier. Its UTC
timestamps are recorded when WebJam observes server confirmation, not asserted
server-clock timestamps.

### `core/` — Domain models

| Module | Responsibility |
|--------|----------------|
| `settings.py` | `AppSettings` dataclass, `load_settings()` / `save_settings()` — `~/.webjam_config.json` |
| `band_check.py` / `band_check_audio.py` | Typed readiness outcomes plus explicit input, output, scratch-recording, recording-storage, host, and Studio checks |
| `recording_readiness.py` | Path-safe writable-folder and conservative PCM24 storage-reserve checks; Band Check estimates a small band and Record rechecks against the actual roster before arming |
| `local_capture.py` | PCM24/48-kHz two-channel local originals with absolute-frame gaps, writer ownership, periodic flush/fsync durability checkpoints, opaque recovery IDs, and crash recovery |
| `session_transfer.py` / `session_transfer_runtime.py` | Authenticated same-RFC1918-LAN presence and resumable verified guest-original delivery; recovered guest captures remain local for manual handling |
| `take_project.py` / `take_library.py` | Schema-v2 identity, segments, media truth, discovery, validation, and optional bounded/redacted session evidence |
| `recording_manifest_journal.py` | Private, crash-safe in-progress checkpoint below the selected Takes folder; stores typed session evidence only and fails closed to recovery-needed truth |
| `take_alignment.py` | Non-destructive bounded offset/drift evidence and manual restoration |
| `take_export.py` | Atomic common-origin PCM24 Logic package with references, reports, analysis, checksums, and silent/unaligned-selected-track truth gates |
| `take_player.py` | Non-destructive multi-segment/mixed-rate project-clock review with seek, gain, pan, mute, and solo |
| `support_bundle.py` | Immutable allowlist artifact, recursive redaction, and private atomic ZIP publication |
| `creative_modes.py` | `CreativeMode` registry (Music Jam, Visual Studio, Writer's Room, Design Critique, Storyboard/Film Room) |
| `templates.py` | Per-mode quick-start templates |
| `audio_routing.py` | Loopback-device detection for advanced audience-bridge mode only |
| `audio_route_profile.py` | Immutable OS-stable route identity, evidence levels, deterministic invalidation, and strict Jamulus 3.12.2 CoreAudio/ASIO/JACK config adapter |
| `coreaudio_devices.py` / `macos_audio_route.py` | Read-only CoreAudio UID discovery plus macOS-only resolution, protected WebJam route staging, and frozen reconnect plans for Jamulus |
| `jamulus_protocol.py` | Low-level Jamulus UDP packet encode/decode |
| `ui/services.py` / `MetricsService` | Local counters, session-brief export, diagnostics-bundle export |

### Audio-route truth boundary

`AudioRouteProfile` is the immutable route contract used by the current macOS
Jamulus launch boundary. Its invalidation fingerprint uses stable OS device
identities, channel maps, buffer request/observation, device generation,
app/Jamulus binary versions, and Linux JACK ownership. Display names remain
part of the fingerprint because Jamulus 3.12.2 can select CoreAudio and ASIO
only by name.

Evidence is deliberately explicit:

- `configured` means WebJam wrote a versioned route configuration;
- `preflighted` means the OS accepted the stable device/format selection;
- `graph_confirmed` is available only when WebJam owns and enumerates the
  Linux JACK graph;
- `musician_confirmed` is the final acoustic proof.

Jamulus 3.12.2 cannot report the active CoreAudio/ASIO device, channel map,
sample rate, or buffer through JSON-RPC, so macOS and Windows are never called
graph-confirmed. On macOS, `MacOSJamulusRouteManager` re-resolves persisted
CoreAudio UIDs before launch, rejects missing/duplicate/non-48-kHz selectors,
writes only `WebJam-route-v1.ini` below Jamulus's Data container, launches with
the filename and that directory as `cwd`, and revalidates the frozen plan on
reconnect rather than switching defaults. `Jamulus3122AudioRouteAdapter` still
requires one Windows ASIO identity and gives Linux explicit
`JACK_DEFAULT_SERVER`, `--nojackconnect`, and desired graph connections; those
platforms are not yet runtime-managed in the same way.

### `storage/` — Persistence

SQLite via `WebJamRepository`. Stores users, mix profiles, room context, canvas notes, artifacts, audit log, settings.

### `legacy/admin/` — RBAC (legacy Tkinter only)

Hand-rolled role/permission engine used by the quarantined Tkinter app. **Not wired into the Qt Conductor** — the shipping pilot is a single-user desktop app with no in-app RBAC.

### `api/` — Companion API

Optional FastAPI localhost bridge. Off by default; enabled via settings/env var. Allows external tools to read session state.

---

## Data Flow: Fader Move

```
User drags fader in ParticipantCard (Qt)
        │
        └─ JamulusController.set_fader_level(channel_id, level)
                ├─ Updates participants[channel_id].fader_level
                ├─ _send_rpc_gain(channel_id, level)   ← background thread → JSON-RPC
                └─ _apply_mixer_setting(channel_id)    ← UDP
```

## Data Flow: Mute Toggle

```
User clicks Mute in ParticipantCard
        │
        └─ JamulusController.set_mute(channel_id, muted)
                ├─ Updates participants[channel_id].muted
                ├─ effective_level = 0 if muted else fader_level
                ├─ _send_rpc_gain(channel_id, effective_level)   ← background thread
                └─ _apply_mixer_setting(channel_id)              ← UDP
```

---

## Threading Model

```
Main thread (Qt event loop)
  └─ ConductorWindow + all Qt widgets

ApplicationController polling timer (Qt timer, main thread)
  ├─ calls BridgeService.attempt_auto_reconnects()
  └─ drives real participant meter decay after Jamulus connection

BridgeService launch threads (daemon)
  ├─ _launch_jamulus_thread(): Popen + state machine
  └─ _launch_webex_thread(): webbrowser.open()

JamulusController RPC threads (daemon, fire-and-forget)
  └─ _send_rpc_gain(): one thread per fader/mute/solo event

JamulusController background thread
  └─ check_participants(): polls Jamulus for participant list
```

---

## Configuration Files

| File | Contents |
|------|---------|
| `~/.webjam_config.json` | Server/ports, Webex URL and role, independent local-capture settings, misc prefs |
| `~/.webjam_mix.json` | Anonymous local default mix (fader/mute/solo state) |
| SQLite DB (path in settings) | Users, mixes, room context, canvas, audit |

---

## Current Limitations

- Closed-pilot source, not broad-release-ready. Storage, recording-provenance,
  durable local-capture, recovery, and Logic-export hardening have automated
  coverage, but no documentation claim substitutes for a recorded package gate.
  Exact-package live CoreAudio, two-Mac audio/reconnect/originals, human Studio
  checks, interruption recovery, and Logic import remain physical evidence
  gates.
- Downloadable builds bundle Jamulus (macOS: zero-install client/server apps;
  Windows CI artifacts supply the official client installer). The private
  physical pilot targets Apple Silicon macOS. `LaunchDialog` offers Host or one
  invitation field, then opens `SimpleSettingsDialog` for name, Band input,
  Band output & review, and an optional conversation link. The package controls
  the next macOS Jamulus route through the preflighted WebJam-owned config.
  Source runs still require compatible Jamulus apps separately.
- The bundled Jamulus version is pinned to WebJam's own release cadence — an upstream Jamulus fix won't reach bundled-copy users until the next WebJam release; the Browse-button/`WEBJAM_JAMULUS_CANDIDATES` manual override remains available.
- Guest-original control and transfer use authenticated plain HTTP on one
  private RFC1918 IPv4 LAN. There is no TLS, IPv6, Internet, VPN, NAT-traversal,
  or public-deployment claim. The complete v2 invite is a reusable,
  session-scoped bearer credential: anyone who has it on that LAN can enroll
  until the host peer service restarts. Peer uploads have no quota or rate
  limiting, so this is for trusted bandmates on a trusted private LAN, not
  untrusted users or hostile networks.
- A legacy v1 guest can join/play and is still represented by a host-side server
  track, but has no WebJam-orchestrated guest local-original capture or delivery.
- The v3 `reference-local` transport remains loopback-only developer-lab code.
  It has no public remote profile. A one-use v3 invitation may be retried only
  if the sidecar fails before `open_guest`; any later uncertainty requires a
  fresh invite and never falls back to legacy or localhost routing.
- Native Webex is launched externally. WebJam cannot observe its participant,
  device, microphone, video, leave, or reconnect state. Band Check can present
  manual checks, but the primary session never invents Webex state.
- Listening profiles and deeper creative-mode workflows exist conceptually but are not first-class pilot workflows.
- Private macOS test packages are ad-hoc signed, not Developer ID signed or
  notarized; a damaged/incomplete-app warning is a packaging failure, not a
  prompt to bypass the bundle seal.
