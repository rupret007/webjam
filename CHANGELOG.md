# WebJam Changelog

All notable improvements and features for the WebJam music collaboration platform.

---

## [0.4.4] — 2026-04-24

### Fixed — Session-control completeness

#### Toggle launch/stop and join/leave
- **Stop Audio button** (`services/bridge_service.py::stop_jamulus`, `webjam_qt/controllers/application_controller.py::_on_launch_audio`): The "Launch Audio" button now toggles. After Jamulus is running, clicking it prompts to stop; Yes terminates the subprocess (graceful terminate, force-kill at 2s) and stops the RPC/UDP monitoring threads. The auto-reconnect intent is cleared so the next reconnect tick doesn't immediately relaunch. Without this the conductor had to kill the app to end a session.
- **Leave Video button** (`services/bridge_service.py::leave_webex`, `application_controller.py::_on_join_video`): Same toggle treatment for the video button. `WebexEmbed.leave_meeting()` already existed but was never called from the UI; now it is. Bridge state is reset to "Not opened" and reconnect intent cleared.
- **Button labels reflect state**: `_refresh_readiness` now shows "Stop Audio" / "Leave Video" when active, and `_on_webex_state` shows the action-oriented "Leave Video" on the button while keeping the descriptive label ("In Meeting", "Lobby") in the status bar.
- **5 new tests** in `tests/test_reconnect_manager_edge.py` cover the new stop/leave paths: graceful termination, force-kill on timeout, clearing reconnect intent, monitoring stopped, leave_webex state reset.

#### Crash recovery is now visible
- **Reconnect banner** (`application_controller.py::_on_reconnect_tick`): When `jamulus_process.poll() is not None` is detected mid-session (Jamulus crashed), a flash message appears: "Jamulus disconnected — auto-reconnecting (attempt N/5)…". When the connection recovers, "Jamulus reconnected." flashes once. Previously the auto-reconnect machinery was completely silent.

#### App-close cleanup
- **Jamulus subprocess no longer survives app close** (`application_controller.py::shutdown`): The previous shutdown only stopped `JamulusController` monitoring threads — the Jamulus subprocess kept running and the user had to manually quit it. Shutdown now calls `bridge.stop_jamulus()` which terminates the subprocess too.

#### Discoverability
- **Tooltips on Launch Audio / Join Video** (`webjam_qt/widgets/session_strip.py`): Each button now hovers with a one-sentence explanation including the toggle behavior and how to access settings.
- **Log file path in error dialogs** (`application_controller.py::_show_actionable_error`): The actionable-error dialog now appends `For details, see the log file: ~/.webjam.log` so users know where to look when something goes wrong.
- **jamulus.io link in "Jamulus Not Found"** (`bridge_service.py::launch_jamulus`): The next-action text now points new users directly at https://jamulus.io to download Jamulus before falling back to the custom-location instructions.

---

## [0.4.3] — 2026-04-24

### Fixed — Critical mixer reliability + 4 UX improvements

#### Critical: mixer commands no longer silently dropped
- **`_check_participants` bypassed when RPC is active** (`jamulus_controller.py`): The UDP monitor loop ran every second and used the protocol adapter's cached participant list, which is always empty when UDP is disabled. This wiped `JamulusController.participants` each second, causing fader/mute/solo commands to hit an empty dict and be silently dropped between 5-second RPC poll cycles. Added an early-return guard matching the existing guard in `_on_udp_participants`.

#### UX
- **Audio button is now gold, video button is teal** (`session_strip.py`, `conductor.qss`): Both action buttons previously used the same teal "PrimaryButton" style. The audio button now uses the `AudioButton` objectName, rendering gold — visually distinguishing "Launch Audio" from "Join Video" at a glance. The `AudioButton` QSS rule is extended with a full set of states (border, padding, focus, pressed, disabled) since QSS has no inheritance within selectors.
- **Embedded Webex join keeps "Video Active" label** (`application_controller.py`): `_refresh_readiness` checked for the bridge-state string `"Opened in browser"` only. After an embedded `QWebEngineView` join, the bridge state becomes `"In Meeting"`, `"Joining…"`, etc. The reconnect timer would then reset the video button to `"Join Video"`. The check now uses a frozen set of all active states.
- **SideRail selection restored after modal actions** (`side_rail.py`, `application_controller.py`): Clicking "Chat", "Roles", or "Settings" in the side rail used to leave that item checked even though the view didn't change, making the nav rail misleading. The controller now tracks the last active content key and restores the rail selection after any modal/placeholder action. `SideRail` gains `current_key()` and `set_active_key(key)` helpers.
- **Setup wizard routing scan uses Signal, not `QMetaObject.invokeMethod`** (`setup_wizard.py`): The background routing scan used `QMetaObject.invokeMethod(self, "_apply_routing", QueuedConnection)` to marshal back to the UI thread, which can silently fail in PySide6 for Python-defined slots. Replaced with a class-level `_scan_complete = Signal()` connected to `_apply_routing` — signal emission across threads is always safe.

---

## [0.4.0] — 2026-04-24

### Fixed — Jamulus mixer RPC signal chain
- **Mute and solo now reach Jamulus via JSON-RPC** (`jamulus_controller.py`): `set_mute()` and `set_solo()` previously only sent UDP; mute/solo state was silently lost when the JSON-RPC server was the primary interface. Both now call a new `_send_rpc_gain()` helper that translates mute/solo state to an effective gain level and forwards it over RPC.
- **All RPC calls moved off the UI thread**: `_send_rpc_gain()` spawns a daemon thread for every `set_channel_gain` call. A slow or unreachable RPC server no longer freezes the UI.

### Fixed — Production bugfixes
- **`WebJamEnhancedApp` constructor ordering**: Property-delegated attributes (`jamulus_state`, `webex_state`, `jamulus_process`, etc.) were assigned before `bridge_service` was created, causing `AttributeError` on startup. Removed the redundant early assignments; `BridgeService.__init__` already sets matching defaults.
- **`_on_theme_changed` callback**: `ThemeManager` registered this callback on `WebJamEnhancedApp` but the method was missing. Added implementation that updates `high_contrast_enabled` and calls `_apply_accessibility_mode()`.
- **`session_controller` initialization**: `SessionController` was referenced (e.g. in `quit_app`) but never instantiated in `__init__`. Added `self.session_controller = SessionController(self)` after `bridge_service` creation.
- **`MixerService._saved_mix_payload_for_load`**: `load_mix()` called this helper but it was not defined. Added implementation that checks signed-in user profile first, then falls back to local mix file.

### Added — Test suite (Part 2 of v0.4 sprint)
- **All 11 previously-ignored edge test files now pass** in CI. Methods that migrated to `MixerService`, `BridgeService`, or `ModeController` during the v0.3 refactor were re-tested against their new homes:
  - `test_listening_profiles_edge.py` → `MixerService` (17 tests)
  - `test_reconnect_manager_edge.py` → `BridgeService` (12 tests)
  - `test_help_and_permissions_edge.py` → `MixerService` + `WebJamEnhancedApp` (4 tests)
  - `test_startup_smoke_edge.py` → `MixerService._restore_startup_mix_default` (2 tests)
  - `test_app_polling_edge.py` → updated stubs for `bridge_service` / `session_controller` delegation (14 tests)
  - `test_jamulus_controller_edge.py` → added `rpc_client` stub (11 tests)
  - `test_mode_layout_edge.py` → rewritten against `ModeController` (8 tests)
  - `test_mode_templates_edge.py`, `test_diagnostics_bundle_export_edge.py`, `test_session_brief_export_edge.py`, `test_docs_parity_edge.py` → updated for `_save_notes` rename and new stubs
  - `test_setup_flow_edge.py` → 3 tests migrated to `MixerService`
- **`README_SIMPLE.md`** added — quick-start guide referenced by `test_docs_parity_edge.py`
- **CI `--ignore` flags removed** from `.github/workflows/ci.yml` — full test suite now runs with no exclusions (493 pass, 12 skip on macOS)

---

## [0.4.2] — 2026-04-24

### Fixed / Added — Qt Conductor usability pass 2

#### Navigation
- **SideRail buttons wired**: clicking "Stage" or "Mixer" expands the participant grid; clicking "Canvas" expands the session notes panel; "Chat" and "Roles" flash a friendly "coming in a future update" message. Previously all four buttons did nothing. `ConductorWindow.center_splitter` is now a named attribute; both panels set collapsible so `setSizes` can resize them.

#### Participant metadata
- **`is_local` from Jamulus RPC**: `JamulusParticipant.is_local` field added and propagated from `ChannelInfo.is_local` (which is resolved via `getClientInfo` RPC). `ApplicationController._apply_jamulus_participants` uses the real flag instead of the `channel_id == 0` heuristic. Existing participants also get `is_local` refreshed on every RPC poll.
- **Role label refreshes for existing participants**: when an existing participant's instrument changes (e.g. mid-session Jamulus settings update), the role label is now updated in `self.participants` before the grid refresh, so the card reflects the new instrument.

#### Session canvas
- **Notes persist across launches**: `_load_notes` runs on startup, reading `~/.webjam_notes.md` into the canvas; `_save_notes` runs in `shutdown()` to write it back. Notes survive app restarts.
- **Timestamp button + Ctrl+T**: inserts the current time as a Markdown heading (`## HH:MM:SS`) at the cursor — useful for logging key moments during a session.
- **Export… button**: opens a Save-file dialog so you can write the session notes as a dated `.md` file (e.g. `webjam_session_2026-04-24.md`).
- **Clear button**: clears all notes after a confirmation prompt.

#### Status bar
- **Participant count replaces "—"**: the Latency status label now shows the live participant count ("3 participants") once Jamulus connects, rather than the static "—". Shows "Not connected" before first Jamulus update.

---

## [0.4.1] — 2026-04-24

### Fixed — Qt Conductor runtime gaps (weekend-usability sprint)

#### Signal wiring
- **Duplicate signal connections eliminated**: `ParticipantGrid` now declares `fader_changed / mute_toggled / solo_toggled` re-emit signals and wires them once per card in `_add_card`. `ApplicationController._wire_signals` connects to the grid once; the per-card loop in `_push_participants_to_grid` is removed. Previously, every participant update stacked new connections → N× callbacks per fader move.

#### Auto-reconnect
- **Auto-reconnect timer wired**: `ApplicationController` now starts a 3-second `QTimer` that calls `BridgeService.attempt_auto_reconnects()` on every tick. Previously, `attempt_auto_reconnects()` existed but was never called — dropped Jamulus processes were never retried.

#### Mix save / restore
- **Saved mix auto-restored on Jamulus connect**: when `JamulusController` fires its first real participant update (`_jamulus_connected` flips `True`), `_restore_saved_mix()` loads `~/.webjam_mix.json` and applies it. Fader layout comes back without manual action.
- **Ctrl+S / Ctrl+O (Save/Load Mix)**: new shortcuts in `ConductorWindow`; `ApplicationController` handlers call `JamulusController.serialize_mix` / `apply_mix_data` and flash a status-bar confirmation.

#### Jamulus path detection
- **macOS + Linux default candidates added** to `AppSettings.jamulus_candidates`: `/Applications/Jamulus.app/Contents/MacOS/Jamulus`, `/usr/bin/Jamulus`, `/usr/local/bin/Jamulus`, `/opt/homebrew/bin/Jamulus` — alongside the existing Windows paths. `find_jamulus()` now resolves on first run on common macOS/Linux installs.
- **Jamulus executable field in setup wizard**: the Jamulus page gains a path text field (pre-populated from first existing candidate) and a Browse button that resolves `.app` bundles to the binary. The chosen path is persisted at the front of `jamulus_candidates` in `~/.webjam_config.json`.

#### Error handling
- **`NameError` in BridgeService error dialogs fixed**: lambdas capturing `exc` from `except` blocks (Python 3 deletes `exc` after the block) caused a `NameError` when the actionable-error dialog was shown after a Jamulus or Webex launch failure. Fixed with `lambda m=str(exc): ...` captures.
- **Video button re-enable**: in direct-URL Webex mode the `meeting_state_changed` signal emits `"joining"` and then nothing (no JS bridge). The "Join Video" button was permanently disabled. A 6-second `QTimer.singleShot` now re-enables it as "Video Active".

#### Participant metadata
- **Instrument pass-through**: `_on_rpc_participants` now builds an `instrument_map` from `ChannelInfo` objects and writes each participant's `instrument` field after `_sync_participants_from_protocol`. Role labels in `ParticipantCard` automatically show the instrument (e.g., "Guitar", "Piano") instead of the generic "Musician" fallback.

#### Code quality
- Removed unused `webbrowser`, `Callable`, `Any` imports from `bridge_service.py`; split two single-line compound statements that ruff flagged as E701.

---

## [Unreleased]

### Added — Post-v0.3.0 gap fixes
- **Qt widget test suite** (`tests/test_qt_widgets.py`): 45 headless smoke tests covering `LevelMeter`, `ParticipantCard`, `SessionStrip`, `ParticipantGrid`, `SideRail`, and `ConductorWindow`
- **Qt setup wizard tests** (`tests/test_qt_setup_wizard.py`): 18 tests covering `should_show_on_startup`, Jamulus/Webex page validation, settings save/round-trip
- **Ruff linting gate** added to CI (lint step runs before tests; 8 auto-fixed unused imports)
- **`python3-tk` added to CI** apt-get — unblocks 11 previously-ignored Tkinter edge test files; only `test_elevation_edge.py` remains ignored (Windows ctypes.windll)
- **`test_elevation_edge.py`**: Windows-only skip guard — deferred imports prevent `ImportError` on macOS/Linux
- **`ui/mixer_service.py`**: `MIX_FILE` TODO resolved — path now sourced from `AppSettings.mix_file` via `settings=` constructor param; default is `~/.webjam_mix.json`
- **Setup wizard Done page**: explicit "Jamulus must be installed separately" note with link to jamulus.io
- **README status table**: updated to reflect v0.3.0 shipped Qt UI, correct limitation descriptions, and links to Releases page

---

## [0.3.0] — 2026-04-21

### Added — Phase 6: Onboarding, Shortcuts & Build
- **Setup Wizard** (`webjam_qt/windows/setup_wizard.py`): 5-page first-run wizard (Welcome, Jamulus server, Webex URL, audio routing, Done). Saves to `~/.webjam_config.json`. Auto-shown on first run.
- **Keyboard shortcuts**: Ctrl+L (focus session title), F11 (fullscreen), Escape (leave fullscreen), Ctrl+, (open settings)
- **Accessibility**: `setAccessibleName()` on all major panels, focus rings in QSS, screen-reader-compatible labels
- **PyInstaller spec** (`webjam.spec`): Production macOS/Windows bundle with QSS + HTML assets, Info.plist camera/mic usage strings

### Added — Phase 5: Audio Device Auto-Detection
- **`core/audio_routing.py`**: `scan_loopback_devices()` auto-detects VB-CABLE, BlackHole, Loopback Audio, JACK, Soundflower
- **`AudioRoutingStatus`** / **`LoopbackDevice`** dataclasses with device metadata (name, index, channel counts)
- **Setup wizard routing page**: shows detected device name or install instructions with link
- **`RealAudioEngine._resolve_device()`**: uses loopback scan to prefer virtual cable over system mic

### Added — Phase 3: Embedded Webex Meeting Pane
- **`webjam_qt/widgets/webex_embed.py`**: `QWebEngineView` embedded meeting pane (lazy-init, Chromium only started on first join)
- **`webjam_qt/webex_widget.html`**: Local HTML template loading Webex Meetings Widget from CDN; dark theme; loading spinner
- **`_WebexBridge(QObject)`**: QWebChannel bridge for bidirectional JS↔Qt communication (`on_page_ready`, `on_state`)
- **Guest-widget mode**: generates HS256 JWT, exchanges for access token, loads widget in embedded view
- **Direct-URL mode**: fallback — loads meeting URL directly using Chrome user-agent + persistent `webjam_webex` profile
- **Auto-grants** camera, mic, screen capture, notification permissions
- **`core/webex_guest_token.py`**: `generate_guest_jwt()` (stdlib HMAC-SHA256) + `exchange_guest_jwt()` (httpx POST)

### Added — Phase 2: Jamulus Protocol Integration
- **`core/jamulus_rpc_client.py`**: HTTP JSON-RPC 2.0 client with polling loop + SSE stream; `set_channel_gain()`, `set_channel_mute()`; non-blocking `stop()` via `httpx.Client.close()`
- **`core/jamulus_protocol.py`**: Full binary UDP adapter — CRC-16-CCITT (poly=0x1021), CONN_CLIENTS_LIST parser, CHANNEL_GAIN/CHANNEL_PAN commands, CLT_CHANNEL_LEVEL_LIST
- **JSON-RPC launch flag**: `services/bridge_service.py` adds `--jsonrpcport 22222` to Jamulus startup command
- **`services/bridge_service.py`**: `threading.Lock` guards reconnect-in-flight flags; exponential backoff for Jamulus/Webex reconnection
- **Real fader dB math**: `20*log10(level/100)` for 1..100; `(level-100)/27*6` for 101..127; `−∞ dB` for 0
- **Gain wire range fixed**: UDP gain mapped correctly as `int(fader_level / 127.0 * 32767)` (was /100 causing scale error)

### Fixed
- `@Slot()` missing on `_RoutingPage._apply_routing` — wizard routing scan result was silently dropped
- `QWebEnginePage` parented to profile (not widget) — eliminates "profile requested but page not deleted" warning
- SSE stream `stop()` now calls `httpx.Client.close()` to immediately unblock the reader thread
- `QSS`: added `QLabel#BodyLabel`, `QWidget#WebexPlaceholder`, `:focus` and `:disabled` states for all interactive widgets

### Changed
- `RealAudioEngine.stop()` thread join timeout: 1.5s → 3.0s for cleaner shutdown
- `WebexEmbed.load_meeting_with_guest_token()`: stays on placeholder until token arrives (was racing to show page before token fetch)

---

## Unreleased - Reliability and Hardening Rollup

### Security and Data Integrity
- Added serialized lockout mutation flow in `WebJamRepository.authenticate_with_status()` to avoid race-driven counter drift under concurrent failed authentication attempts.
- Switched password hash comparison to constant-time `hmac.compare_digest()` during authentication checks.

### Stability and Runtime Safety
- Hardened `JamulusController.load_mix()` against malformed files and invalid payload shapes with bounded coercion/clamping.
- Added atomic mix save behavior (`tempfile` + replace) to reduce partial-write corruption risk.
- Added participant-state synchronization (`RLock`) across controller and monitor paths to avoid cross-thread mutation hazards.
- Fixed participant auto-ID allocation after removals to avoid channel ID collisions.
- Added explicit sqlite connection management helper to prevent lingering connection warnings and improve cleanup reliability.
- Added sqlite runtime defaults for local repository usage:
  - `busy_timeout=5000`
  - best-effort `journal_mode=WAL`
- Added bounded retention for cohort telemetry events (latest 1000 kept per cohort key).
- Updated settings increment and cohort event append paths to run atomically under concurrency.

### Local API Bridge Resilience
- Added explicit bridge shutdown signaling and thread join behavior.
- Wrapped `/participants` and `/diagnostics` callback errors into HTTP 500 responses with actionable details.
- Added lightweight app-construction helper used by integration tests.

### Configuration and Operational Updates
- Added admin endpoint validation for empty host and out-of-range/non-numeric port values.
- Added warning logging when settings JSON is malformed and defaults are used.
- Added env bounds validation for `WEBJAM_JAMULUS_PORT` (`1..65535`) and sanity checks for numeric audio env values.
- Added env-gated startup debug logging controls:
  - `WEBJAM_AGENT_DEBUG_LOG`
  - `WEBJAM_AGENT_DEBUG_LOG_PATH`
- Updated diagnostics timestamp generation to timezone-aware UTC.

### Tests and Verification
- Expanded modernization and integration coverage:
  - auth lockout behavior under concurrency
  - bounded cohort event retention
  - API bridge callback error wrapping
  - TestClient endpoint integration checks (`/health`, `/participants`, `/diagnostics`)
  - malformed mix payload resilience and clamping/coercion behavior
- Full regression suites pass:
  - `python -m unittest test_modernization`
  - `python -m unittest test_webjam`

### Legacy Launcher Maintenance
- Extracted low-risk shared installer helpers into `utils/installer_helpers.py`.
- Rewired legacy launcher paths to use shared helper implementations to reduce maintenance drift.

---

## Version 2.0 - Enhanced Edition (Current Release)

### 🎉 Major New Features

#### Virtual Mixing Console
- **Professional mixer interface** with individual channel strips for each musician
- **Vertical faders** with dB scale (-∞ to 0dB) for precise volume control
- **Real-time VU meters** showing audio levels with color-coded indicators (green/yellow/red)
- **Pan controls** for stereo positioning (L-C-R) of each musician
- **Mute/Solo buttons** for quick channel control
- **Channel status indicators** showing connection state

#### Modern GUI Application
- **Complete rewrite** with modern tkinter/customtkinter interface
- **Dark theme** optimized for studio environments
- **Intuitive layout** familiar to musicians and audio engineers
- **Responsive design** that works on various screen sizes
- **Professional typography** and visual hierarchy

#### Session Management
- **Save/Load mix presets** for different songs or configurations
- **Automatic settings persistence** across sessions
- **Mix profiles** stored in user directory
- **Quick reset functions** for faders, pans, and mutes
- **Configuration backup** and restore

#### Jamulus Integration
- **Real-time participant detection** (foundation for future implementation)
- **Per-channel level control** via intuitive faders
- **Audio monitoring system** with simulated levels (ready for actual audio analysis)
- **Automatic channel creation** when musicians join
- **Connection status tracking** with visual indicators

#### Webex Integration
- **Browser-based meeting access** with one-click launch
- **Participant synchronization** framework (ready for SDK integration)
- **Embedded view preparation** for future Webex SDK implementation
- **Configuration management** for meeting preferences

### 🛠️ Technical Improvements

#### Architecture
- **Modular design** with separate controllers for Jamulus and Webex
- **Event-driven updates** using callback system
- **Threading** for non-blocking audio monitoring
- **Clean separation** of UI and business logic
- **Extensible framework** for future enhancements

#### Installation System
- **Enhanced installer** (`webjam_installer.py`) with better error handling
- **Progress indicators** for long-running operations
- **Smart dependency detection** and installation
- **Desktop and Start Menu shortcuts** created automatically
- **Application directory** in LocalAppData for clean installation

#### Build System
- **Automated build script** (`build_webjam.py`) for creating executables
- **PyInstaller integration** with proper bundling
- **Distribution package creation** with all necessary files
- **ZIP archive generation** for easy distribution

### 📚 Documentation

#### New Documentation Files
- **README.md**: Complete project overview and quick start
- **USER_GUIDE.md**: Comprehensive 30+ page user manual
- **CHANGELOG.md**: This file, tracking all changes
- **Code documentation**: Extensive docstrings and comments

#### User Guide Includes
- Installation instructions with screenshots
- Step-by-step first session tutorial
- Mixer control reference
- Troubleshooting section
- Professional mixing tips
- Keyboard shortcuts
- Technical appendix

### 🎨 User Interface Enhancements

#### Visual Design
- **Color-coded controls**: Mute (red), Solo (green), Status (green/gray)
- **Professional meters**: VU meters with proper ballistics
- **Clear typography**: Arial font with appropriate sizing
- **Visual feedback**: Button states, hover effects, active indicators
- **Consistent spacing**: Professional layout with proper padding

#### Usability Features
- **Menu bar** with File, Session, and Help menus
- **Status bar** showing participant count and server info
- **Control bar** with quick-access buttons
- **Tooltips** and labels for all controls
- **Keyboard shortcuts** for common operations
- **Modal dialogs** for confirmations and errors

### 🔧 Developer Experience

#### Code Quality
- **Type hints** throughout codebase
- **Dataclasses** for clean data structures
- **Descriptive naming** following Python conventions
- **Error handling** with try-except blocks
- **Logging and debugging** print statements

#### Project Structure
```
WebJam/
├── webjam_app_enhanced.py      # Main GUI application (New)
├── webjam_app.py               # Basic GUI version
├── jamulus_controller.py       # Jamulus integration module (New)
├── webex_integration.py        # Webex integration module (New)
├── webjam_installer.py         # Enhanced installer (New)
├── build_webjam.py             # Build automation (New)
├── requirements.txt            # Python dependencies
├── README.md                   # Project documentation (Enhanced)
├── USER_GUIDE.md              # Comprehensive user manual (New)
├── CHANGELOG.md               # This file (New)
├── webjam_launch_session.py   # Legacy launcher
├── webjam_win_oneclick.py     # Legacy installer
└── VB/                        # VB-Cable drivers
```

---

## Version 1.0 - Initial Release

### Core Features

#### Basic Functionality
- **One-click installer** for Jamulus and VB-Cable
- **Automatic audio routing** setup
- **Desktop shortcut** creation
- **Simple launcher** script

#### Components
- VB-Cable installation with driver detection
- Jamulus installation with multiple installer support
- Audio device configuration via PowerShell
- Webex meeting launcher

#### Limitations of v1.0
- ❌ No mixer controls (used Jamulus built-in mixer)
- ❌ No GUI application (command-line only)
- ❌ No session management
- ❌ Manual participant management
- ❌ Limited configuration options

---

## Migration Guide: v1.0 → v2.0

### For End Users

#### What Changed
1. **New Application**: Launch "WebJam" instead of old launcher
2. **Mixer Interface**: Control levels in WebJam, not Jamulus window
3. **Better Integration**: Automatic participant detection

#### Migration Steps
1. Uninstall old WebJam (optional - won't conflict)
2. Run new WebJam_Installer.exe
3. Launch from new Desktop shortcut
4. Enjoy enhanced features!

#### Settings Migration
- Old settings are not migrated automatically
- Recreate your mix preferences in new interface
- Save your mix using the new Save Mix feature

### For Developers

#### API Changes
- `JamulusController` class replaces direct subprocess calls
- `WebexController` provides structured meeting access
- Event-driven architecture with callbacks
- Configuration via JSON files instead of constants

#### Code Migration
```python
# Old approach (v1.0)
subprocess.Popen([jamulus_path, "--connect", server])

# New approach (v2.0)
controller = JamulusController(server, port)
controller.start()
controller.add_participant("Musician", channel_id)
controller.set_fader_level(channel_id, 75)
```

---

## Roadmap - Future Versions

### Version 2.1 (Planned)

#### Features
- [ ] **Direct Jamulus Protocol**: Implement full Jamulus UDP protocol
- [ ] **Real audio monitoring**: Use PyAudio to analyze actual audio levels
- [ ] **Participant auto-detection**: Automatically discover musicians from Jamulus
- [ ] **Effects processing**: Per-channel EQ, compression, reverb
- [ ] **Recording**: Multi-track recording directly in WebJam

#### Improvements
- [ ] **Performance optimization**: Reduce CPU usage
- [ ] **Better error messages**: User-friendly error dialogs
- [ ] **Config GUI**: Settings panel for advanced options
- [ ] **Server selection**: Choose from multiple Jamulus servers

### Version 3.0 (Future)

#### Major Features
- [ ] **Webex SDK Integration**: Embedded video within WebJam window
- [ ] **MIDI Control**: Use physical faders/controllers
- [ ] **Mobile Companion**: iOS/Android remote control app
- [ ] **Cloud Sync**: Sync settings across devices
- [ ] **AI-Powered Mixing**: Automatic level balancing

#### Professional Features
- [ ] **VST Plugin Support**: Load audio effects plugins
- [ ] **Multi-server**: Connect to multiple Jamulus servers simultaneously
- [ ] **Advanced Routing**: Custom audio routing matrix
- [ ] **Metering**: Professional audio meters (PPM, RMS, LUFS)
- [ ] **Time Alignment**: Compensate for latency differences

### Community Wishlist

Vote for features you want to see:
- [ ] Linux and macOS support
- [ ] Standalone mode (Jamulus+Webex in one)
- [ ] Practice room scheduling
- [ ] Integrated chat
- [ ] Sheet music viewer
- [ ] Metronome with sync
- [ ] Latency testing tools
- [ ] Performance analytics

---

## Known Issues

### Current Limitations

#### Jamulus Integration
- ~~**Participant detection** is currently manual~~ — **Resolved** (Phase 2): Full Jamulus UDP protocol + JSON-RPC client auto-detects participants via CONN_CLIENTS_LIST
- ~~**Audio levels** are simulated~~ — **Resolved** (Phase 2): Real fader dB math and UDP gain wiring implemented in `core/jamulus_protocol.py`
- ~~**Mixer commands** don't yet control actual Jamulus mixer~~ — **Resolved** (Phase 2): `set_channel_gain()` and `set_channel_mute()` wired to live Jamulus JSON-RPC endpoint

#### Webex Integration
- ~~**Browser-based** video (not embedded in app)~~ — **Resolved** (Phase 3): `QWebEngineView` embedded meeting pane with `webex_widget.html` template
- ~~**Participant sync** is name-based matching only~~ — **Resolved** (Phase 3): Bidirectional JS↔Qt bridge via `_WebexBridge(QObject)` + QWebChannel
- **No video controls** from within WebJam — still managed via the embedded Webex widget UI

#### Audio Routing
- ~~**VB-Cable required**: No built-in virtual audio device~~ — **Resolved** (Phase 5): `scan_loopback_devices()` auto-detects VB-CABLE, BlackHole, Loopback Audio, JACK, and Soundflower
- ~~**Manual device setup**: May need manual configuration~~ — **Resolved** (Phase 5/6): Setup wizard routing page auto-detects and configures the preferred virtual device
- **Single audio stream**: Can't separate audio and video audio — still a system-level constraint

### Bug Reports

Found a bug? Please report:
1. Go to: https://github.com/yourusername/webjam/issues
2. Click "New Issue"
3. Describe the problem with steps to reproduce
4. Include your system info (Windows version, audio interface, etc.)

---

## Credits and Acknowledgments

### WebJam Team
- **Development**: [Your Name]
- **UI/UX Design**: [Designer]
- **Testing**: [Testers]
- **Documentation**: [Writers]

### Open Source Projects
- **Jamulus**: Low-latency audio - [jamulus.io](https://jamulus.io)
- **VB-Audio**: Virtual audio cables - [vb-audio.com](https://vb-audio.com)
- **CustomTkinter**: Modern tkinter - [github.com/TomSchimansky/CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)
- **PyInstaller**: Python packaging - [pyinstaller.org](https://pyinstaller.org)

### Special Thanks
- Jamulus community for inspiration
- Beta testers for valuable feedback
- Musicians who tried early versions
- Open source community for tools and libraries

---

## License

WebJam is released under the MIT License.

Copyright (c) 2024 WebJam Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

**Last Updated**: October 9, 2024
**Version**: 2.0.0
**Status**: Release Candidate

For the latest updates, visit: **[github.com/yourusername/webjam](https://github.com/yourusername/webjam)**

