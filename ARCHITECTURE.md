# WebJam Architecture

> **Last updated:** 2026-04-24 (v0.4.0)

## Overview

WebJam is a creative-collaboration shell that orchestrates **Jamulus** (low-latency audio) and **Webex** (video) into a single window, with a shared session canvas for notes, artifacts, and mode-aware templates.

The primary runtime is a **Qt/PySide6 Conductor UI**. A legacy Tkinter UI (`webjam_app_enhanced.py`) is retained as a fallback until the Qt port reaches full parity.

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
                                ├─ BridgeService          (Jamulus + Webex lifecycle)
                                ├─ JamulusController      (mixer state + RPC/UDP)
                                ├─ WebexController         (meeting URL + browser)
                                └─ ParticipantGrid         (Qt mixer UI)
```

---

## Entry Points

| File | Purpose |
|------|---------|
| `webjam_qt_main.py` | **Primary.** Bootstraps PySide6, shows setup wizard on first run, then opens `ConductorWindow`. |
| `webjam_app_enhanced.py` | **Legacy fallback.** Tkinter/customtkinter UI. Not actively developed. |

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
| `widgets/webex_embed.py` | Lazy-init `QWebEngineView` for embedded Webex (Phase 2) |
| `controllers/application_controller.py` | Wires `ConductorWindow` to `BridgeService`; drives polling loop |
| `theme.py` | QSS stylesheet loader |

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

Currently opens `settings.webex_url` in the system browser. Embedded `QWebEngineView` (via `webex_embed.py`) is built but not yet wired to the main launch flow.

### `ui/` — Tkinter service layer (shared with legacy UI)

| Module | Responsibility |
|--------|----------------|
| `mixer_service.py` | Save/load mix, listening profiles, startup mix restore, reset faders |
| `auth_controller.py` | Sign-in / authorize gate |
| `mode_controller.py` | Mode layout spec + sash computation |
| `session_controller.py` | Session canvas dirty-state tracking |
| `theme.py` / `accessibility.py` | High-contrast and text-size helpers |

### `core/` — Domain models

| Module | Responsibility |
|--------|----------------|
| `settings.py` | `AppSettings` dataclass, `load_settings()` / `save_settings()` — `~/.webjam_config.json` |
| `creative_modes.py` | `CreativeMode` registry (Music Jam, Visual Studio, Writer's Room, Design Critique, Storyboard/Film Room) |
| `templates.py` | Per-mode quick-start templates |
| `audio_routing.py` | Loopback device detection (VB-CABLE, BlackHole, JACK) |
| `jamulus_protocol.py` | Low-level Jamulus UDP packet encode/decode |
| `metrics_service.py` | Local counters, session-brief export, diagnostics-bundle export |

### `storage/` — Persistence

SQLite via `WebJamRepository`. Stores users, mix profiles, room context, canvas notes, artifacts, audit log, settings.

### `admin/` — RBAC

Hand-rolled role/permission engine. `AuthController.authorize()` checks `role → action` policy. Bootstrap admin credentials written to a temp file on first run; cleared after password change.

### `api/` — Companion API

Optional FastAPI localhost bridge. Off by default; enabled via env var. Allows external tools to read session state.

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

## Current Limitations (as of v0.4)

- Qt level meters run on **demo jitter** — real audio-level data from Jamulus RPC is not yet wired to the Qt widgets.
- **Webex embed** (`QWebEngineView`) is implemented but the main launch button still opens a browser tab.
- **Qt UI has no Save/Load Mix shortcuts** (Ctrl+S/O) — those exist only in the Tkinter fallback.
- **Listening profiles** (named mix snapshots) exist in `MixerService` but are not surfaced in the Qt Conductor UI yet.
- macOS code signing is not yet set up; downloaded `.app` requires manual Gatekeeper override.
