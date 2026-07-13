# WebJam Architecture

> **Last updated:** 2026-07-13 (v0.9.0 test-night candidate)

## Overview

WebJam is a creative-collaboration shell that runs **Jamulus** as its
low-latency music engine and can optionally launch native **Webex**
(speech/video) alongside its Conductor window. The normal product flow is
Host/Join, not music-engine configuration. Notes, multitrack Studio, settings,
conversation, and diagnostics remain progressively disclosed under **More**.
WebJam does not embed or control the native meeting.

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
| `windows/simple_settings.py` | Progressive preferences for the displayed name and optional conversation URL |
| `windows/recording_setup.py` | Focused Studio output and optional two-input host-capture preferences |
| `windows/setup_wizard.py` | Legacy detailed configuration surface; not part of the v0.9.0 musician path |
| `widgets/participant_card.py` | Per-channel fader, monitor mute, solo, accessible state, and observed level meter |
| `widgets/participant_grid.py` | Viewport-driven participant layout plus intentional empty/recovery states |
| `widgets/level_meter.py` | Truthful observed level meter with coarse accessible signal descriptions |
| `widgets/session_hud.py` | One authoritative readiness/invitation/recovery summary |
| `widgets/session_strip.py` | Header metadata plus persistent Copy Invite / Record / More / End-or-Leave controls |
| `widgets/recording_studio.py` | Live participant lanes, take library, waveform transport, stereo output, gain/pan/mute/solo, and background Logic export |
| `widgets/webex_embed.py` | Compact native-Webex launch/status card; legacy embed code is inactive |
| `controllers/application_controller.py` | Wires `ConductorWindow` to services; delegates audio/video/recording to coordinators |
| `controllers/audio_coordinator.py` | Permission gate, launch/recovery/end-or-leave lifecycle, and participant-grid transitions |
| `controllers/video_coordinator.py` | External Webex-launch compatibility wrapper; no meeting control |
| `controllers/recording_coordinator.py` | Recorder state machine, RPC worker, take discovery/validation, completion actions |
| `session_state.py` | UI-only truth for connect, reconnect, permission, error, ending, and leaving states |
| `platform_permissions.py` | Dependency-free macOS microphone authorization query; unavailable elsewhere without blocking |
| `windows/ready_check.py` | Non-blocking required/optional readiness report |
| `windows/take_deck.py` | Validated take library, selectable stereo playback output, review mixer |
| `theme/tokens.py` | Color tokens and QSS stylesheet |

### `services/` — Process lifecycle

| Module | Responsibility |
|--------|----------------|
| `bridge_service.py` | Jamulus client/practice/hosted-server process ownership, reconnect/supervision, and truthful external Webex launch |

### `jamulus_controller.py` — Mixer state

Owns the participant map and all mixer operations. Three communication paths:

1. **JSON-RPC** (`rpc_client`) — primary, background-threaded (`_send_rpc_gain`)
2. **UDP** (`_apply_mixer_setting`) — secondary, direct channel gains
3. **Process poll** — tracks Jamulus process health via `jamulus_process.poll()`

Since v0.4: `set_mute()` and `set_solo()` now use `_send_rpc_gain()` so mute/solo reach Jamulus over RPC instead of UDP-only.

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
reconnect preference. **End Session** stops a take, client, owned server, and
sleep assertion in ownership-aware order; **Leave Jam** stops only the guest's
client.

If TCP 22240 is already occupied, WebJam adopts the endpoint only after the
configured secret authenticates and `getRecorderStatus` proves it is a
Jamulus recorder. Adopted processes are reported as external and are never
terminated—or have recording stopped—when WebJam quits. Remote Linux hosting
remains a developer/legacy-compatible recipe, not the v0.9.0 same-LAN pilot
path. Both paths use `core/jamulus_server_rpc.py`.

## Data Flow: Recording completion

```
Record button → RecordingCoordinator (preflight)
  → host only: LocalInputCapture opens inputs 1–2 at 48 kHz
  → authenticated loopback JSON-RPC → recorderState (recording + timer)
  → Stop RPC → finalize atomic host WAVs → wait for stable server files
  → align local stems → write secret-free webjam-take.json → validate WAVs
  → completion summary → integrated Studio

Studio Export for Logic
  → render each signed-offset source onto one zero-based timeline
  → atomically publish equal-length 24-bit PCM stems
  → stream a stereo rough mix using gain/pan/mute/solo state
  → write import instructions + secret-free export evidence manifest
```

Supplemental host capture never participates in the live mix. If it fails,
Jamulus audio and server-recorder shutdown remain authoritative; WebJam keeps
the available files and blocks take approval with `Needs Attention`.

### `core/` — Domain models

| Module | Responsibility |
|--------|----------------|
| `settings.py` | `AppSettings` dataclass, `load_settings()` / `save_settings()` — `~/.webjam_config.json` |
| `local_capture.py` | Atomic two-channel host capture with explicit failure cleanup |
| `take_library.py` | Take discovery, alignment, manifest evidence, and validation |
| `take_export.py` | Atomic zero-aligned 24-bit stem package and streaming stereo rough mix for Logic/DAW handoff |
| `take_player.py` | Non-destructive stereo review transport with gain, pan, mute, solo, and signed-offset playback |
| `creative_modes.py` | `CreativeMode` registry (Music Jam, Visual Studio, Writer's Room, Design Critique, Storyboard/Film Room) |
| `templates.py` | Per-mode quick-start templates |
| `audio_routing.py` | Loopback-device detection for advanced audience-bridge mode only |
| `jamulus_protocol.py` | Low-level Jamulus UDP packet encode/decode |
| `ui/services.py` / `MetricsService` | Local counters, session-brief export, diagnostics-bundle export |

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

- Closed-pilot-ready, not broad-release-ready; real-hardware gates still need
  the two-Mac audio/reconnect/recording/Studio soak and exact-artifact checks.
- Downloadable builds bundle Jamulus (macOS: zero-install client/server apps;
  earlier Windows artifacts supplied the official client installer). v0.9.0's
  private pilot is Apple Silicon macOS only. `LaunchDialog` offers Host or one
  invitation field; `SimpleSettingsDialog` contains only the musician name and
  optional conversation link. Source runs still require compatible Jamulus
  apps separately.
- The bundled Jamulus version is pinned to WebJam's own release cadence — an upstream Jamulus fix won't reach bundled-copy users until the next WebJam release; the Browse-button/`WEBJAM_JAMULUS_CANDIDATES` manual override remains available.
- Native Webex is launched externally. WebJam cannot observe its participant,
  device, microphone, video, leave, or reconnect state. Troubleshooting can
  present manual checks, but the primary session never invents Webex state.
- Listening profiles and deeper creative-mode workflows exist conceptually but are not first-class pilot workflows.
- macOS code signing/notarization is not yet set up; downloaded `.app` requires manual Gatekeeper override.
