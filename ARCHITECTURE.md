# WebJam Architecture

> **Last updated:** 2026-07-10 (v0.8.1 release candidate)

## Overview

WebJam is a creative-collaboration shell that orchestrates **Jamulus** (low-latency audio) and **Webex** (video) into a single window, with a shared session canvas for notes, artifacts, and mode-aware templates.

The primary runtime is a **Qt/PySide6 Conductor UI**. A legacy Tkinter UI (`legacy/webjam_app_enhanced.py`) is quarantined for archive/fallback use and is not part of the pilot release path.

---

## High-Level Component Map

```
webjam_qt_main.py          ← entry point
        │
        └─ webjam_qt/app.run()
                │
                ├─ SetupWizard            (first-run config)
                │
                └─ ConductorWindow        (main window)
                        │
                        └─ ApplicationController
                                ├─ AudioCoordinator       (launch/practice/live truth)
                                ├─ VideoCoordinator       (Webex lifecycle)
                                ├─ RecordingCoordinator   (RPC/state/take verification)
                                ├─ BridgeService          (process lifecycle/reconnect)
                                ├─ JamulusController      (mixer state + RPC/UDP)
                                ├─ WebexController         (meeting URL + browser)
                                └─ ParticipantGrid         (Qt mixer UI)
```

---

## Entry Points

| File | Purpose |
|------|---------|
| `webjam_qt_main.py` | **Primary.** Bootstraps PySide6, shows setup wizard on first run, then opens `ConductorWindow`. |
| `legacy/webjam_app_enhanced.py` | **Legacy fallback.** Tkinter/customtkinter UI. Not actively developed. |

---

## Core Layers

### `webjam_qt/` — Qt Conductor UI

| Module | Responsibility |
|--------|----------------|
| `app.py` | `QApplication` bootstrap, font, stylesheet, setup-wizard gate |
| `windows/conductor_window.py` | Main application window — `SessionStrip`, `ParticipantGrid`, `SideRail` |
| `windows/setup_wizard.py` | 5-page first-run wizard: server, Webex URL, audio check |
| `widgets/participant_card.py` | Per-channel fader, pan, mute, solo, level meter |
| `widgets/level_meter.py` | Animated RMS level meter widget |
| `widgets/session_strip.py` | Top control bar: mode, title, launch buttons |
| `widgets/side_rail.py` | Right-side settings and diagnostics panel |
| `widgets/webex_embed.py` | Lazy-init `QWebEngineView` for embedded Webex with browser fallback |
| `controllers/application_controller.py` | Wires `ConductorWindow` to services; delegates audio/video/recording to coordinators |
| `controllers/audio_coordinator.py` | Launch/Stop Audio, practice mode, participant grid transitions |
| `controllers/video_coordinator.py` | Join/Leave Webex (first extraction step) |
| `controllers/recording_coordinator.py` | Recorder state machine, RPC worker, take discovery/validation, completion actions |
| `windows/ready_check.py` | Non-blocking required/optional readiness report |
| `windows/take_deck.py` | Validated take library, selectable stereo playback output, review mixer |
| `theme/tokens.py` | Color tokens and QSS stylesheet |

### `services/` — Process lifecycle

| Module | Responsibility |
|--------|----------------|
| `bridge_service.py` | Jamulus process management, Webex browser-open, auto-reconnect with exponential backoff |

### `jamulus_controller.py` — Mixer state

Owns the participant map and all mixer operations. Three communication paths:

1. **JSON-RPC** (`rpc_client`) — primary, background-threaded (`_send_rpc_gain`)
2. **UDP** (`_apply_mixer_setting`) — secondary, direct channel gains
3. **Process poll** — tracks Jamulus process health via `jamulus_process.poll()`

Since v0.4: `set_mute()` and `set_solo()` now use `_send_rpc_gain()` so mute/solo reach Jamulus over RPC instead of UDP-only.

### `webex_integration.py` — Webex

Browser fallback controller for Webex meeting URLs. The main Conductor flow uses `widgets/webex_embed.py` first and falls back to this browser path when needed. Webex URLs must be HTTPS `webex.com`.

### `legacy/ui/` — Tkinter service layer (legacy UI only)

| Module | Responsibility |
|--------|----------------|
| `mixer_service.py` | Save/load mix, listening profiles (Tkinter app) |
| `auth_controller.py` | Sign-in / authorize gate (legacy) |
| `mode_controller.py` | Mode layout spec + sash computation (legacy) |

The Qt Conductor uses `webjam_qt/controllers/mix_manager.py` and `jamulus_controller.py` directly instead.

### `server/` — Band-server recipe

The pilot uses official `JamulusServer.app` 3.12.2 with recorder data in its
real sandbox container. A remote Linux server remains available through the
legacy-compatible recipe and SSH tunnel. Both use `core/jamulus_server_rpc.py`.

## Data Flow: Recording completion

```
Record button → RecordingCoordinator (starting)
  → authenticated loopback JSON-RPC → recorderState (recording + timer)
  → Stop RPC → wait for stable files → validate expected WAVs
  → completion summary → Take Deck / Finder
```

### `core/` — Domain models

| Module | Responsibility |
|--------|----------------|
| `settings.py` | `AppSettings` dataclass, `load_settings()` / `save_settings()` — `~/.webjam_config.json` |
| `creative_modes.py` | `CreativeMode` registry (Music Jam, Visual Studio, Writer's Room, Design Critique, Storyboard/Film Room) |
| `templates.py` | Per-mode quick-start templates |
| `audio_routing.py` | Loopback device detection (VB-CABLE, BlackHole, JACK) |
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
  └─ drives level-meter demo data (until real Jamulus audio stream)

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
| `~/.webjam_config.json` | Server, port, Webex URL, log level, misc prefs |
| `~/.webjam_mix.json` | Anonymous local default mix (fader/mute/solo state) |
| SQLite DB (path in settings) | Users, mixes, room context, canvas, audit |

---

## Current Limitations

- Closed-pilot-ready, not broad-release-ready; real-hardware gates still need
  the two-Mac Jamulus/Webex/recording/Take Deck soak and exact-artifact checks.
- Downloadable builds bundle Jamulus (macOS: zero-install nested `Jamulus.app`; Windows: bundled installer the Setup Wizard can launch — see `THIRD_PARTY_NOTICES.md`); the first-run wizard still requires a resolvable executable path before setup can complete, and running from source needs Jamulus installed separately since the bundling only happens in the PyInstaller build.
- The bundled Jamulus version is pinned to WebJam's own release cadence — an upstream Jamulus fix won't reach bundled-copy users until the next WebJam release; the Browse-button/`WEBJAM_JAMULUS_CANDIDATES` manual override remains available.
- Webex embed is constrained to HTTPS `webex.com` URLs and mic/camera permissions only; some meetings may still require the browser fallback.
- Listening profiles and deeper creative-mode workflows exist conceptually but are not first-class pilot workflows.
- macOS code signing/notarization is not yet set up; downloaded `.app` requires manual Gatekeeper override.
