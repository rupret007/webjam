# WebJam

A creative-collaboration shell that orchestrates **Jamulus** (low-latency audio) and **Webex** (video) into a single session, with a shared canvas for notes and artifacts.

> See [Current State](#current-state) for what works today and [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for what's planned next.

---

## Current State

Being honest about where this app is **right now** (2026-04-24):

| Area | Status |
|---|---|
| **Core data model** (participants, mixer, sessions, modes) | ✅ Works. 493 tests pass. |
| **Qt Conductor UI** | ✅ **Shipped in v0.3.0, fully usable as of v0.4.7.** `webjam_qt_main.py` is the primary entry point. Run with `python webjam_qt_main.py`. Downloadable builds at [Releases](https://github.com/rupret007/webjam/releases). |
| **Legacy Tkinter UI** | ⚠️ Retained as fallback (`webjam_app_enhanced.py`). Not actively developed. Will be removed when Qt UI reaches full parity. |
| **Jamulus integration** | ✅ **Full RPC + UDP.** Fader, mute, and solo all reach Jamulus via JSON-RPC (background threads, non-blocking). Real audio-level meters wired — polls `RealAudioEngine` every 100 ms once Jamulus connects. Participant instruments shown in role labels. Auto-reconnect retries dropped sessions. **Jamulus must be installed separately.** |
| **Webex integration** | ⚠️ **Embedded + browser fallback.** "Join Video" loads your Webex URL in the embedded `QWebEngineView`. Falls back to system browser if the embed fails. Guest-token flow optional (requires Webex developer account). |
| **Audio routing** | ⚠️ **Semi-automatic.** Setup wizard detects VB-CABLE / BlackHole. If not installed, the wizard links to instructions. |
| **Builds** | ✅ Windows x64, macOS ARM64, macOS x64 — all three zips at [Releases](https://github.com/rupret007/webjam/releases). |
| **Local Companion API** | ✅ Localhost bridge for external tools. See [COMPANION_API.md](COMPANION_API.md). |

In practice today (v0.4.7): WebJam is a **unified Qt Conductor** — one window that launches Jamulus, embeds Webex, and gives you a live mixer for every participant. The Jamulus window still appears separately (it must be installed independently), but fader/mute/solo controls in WebJam drive it in real time. The audio button is gold, the video button is teal; the status bar shows the connected server address once Jamulus is running. Click the audio button again to stop, the video button again to leave; the conductor's session title persists across launches.

Future phases: full embedded Webex video tiles per-participant, macOS code signing, and listening profiles in the Qt UI.

---

## What's Planned

See [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for the long-form vision. Near-term engineering phases:

1. **Phase 0** — Doc truth-up (in progress)
2. **Phase 1** — Refactor god-file into `ApplicationController` + thin shell
3. **Phase 4-preview** — Conductor-style UI mockup in Qt
4. **Phase 2** — Implement Jamulus UDP protocol (real fader sync, real participant reconciliation)
5. **Phase 3** — Webex Web SDK embedded in `QWebEngineView`
6. **Phase 5** — Automatic VB-CABLE / BlackHole detection + routing
7. **Phase 6** — Onboarding, recovery, a11y, signed installer

---

## Running from Source

```bash
git clone https://github.com/rupret007/webjam.git
cd webjam
pip install -r requirements.txt
python webjam_qt_main.py  # First-run: a setup wizard will guide you through configuration
```

System requirements:
- Python 3.10+
- Windows 10/11 or macOS 12+
- Jamulus client + Webex (web or desktop) installed separately
- Broadband network with <30 ms latency to your Jamulus server

---

## Configuration

App settings live in:
- **Config:** `~/.webjam_config.json`
- **Mix:** `~/.webjam_mix.json` (anonymous/local fallback)

Environment overrides:
- `WEBJAM_JAMULUS_SERVER` — Jamulus server host
- `WEBJAM_JAMULUS_PORT` — Jamulus port (default 22124)
- `WEBJAM_WEBEX_URL` — Webex meeting URL
- `WEBJAM_JAMULUS_CANDIDATES` — `;`-separated list of Jamulus executable paths

---

## Qt Conductor Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| **Ctrl+L** | Focus / edit the session title |
| **Ctrl+S** | Save current mixer state to `~/.webjam_mix.json` |
| **Ctrl+O** | Load and apply saved mix from `~/.webjam_mix.json` |
| **Ctrl+M** | Mute all / Unmute all (toggles) |
| **Ctrl+Shift+M** | Mute / unmute yourself (toggles) |
| **Ctrl+T** | Insert timestamp heading into Session Canvas |
| **Ctrl+Shift+R** | Reset all faders to 0 dB (with confirmation) |
| **Ctrl+Shift+D** | Copy diagnostics summary to clipboard |
| **Ctrl+,** | Open Settings wizard |
| **F11** | Toggle fullscreen |
| **Escape** | Exit fullscreen |
| **F1** | Show in-app help (shortcut & getting-started reference) |
| **Double-click fader** | Reset to 0 dB (unity gain) |

The session timer, mode picker, "Launch Audio", and "Join Video" buttons are in the top strip. The left rail switches between Stage/Mixer view, Canvas view, and Settings. Session notes are saved to `~/.webjam_notes.md` on exit and restored automatically on next launch. Saved mixes are auto-restored when Jamulus first connects.

---

## Project Structure

```
webjam/
├── webjam_qt_main.py        # Primary entry point — Qt Conductor UI
├── webjam_qt/               # Qt application (windows, widgets, controllers)
├── webjam_app_enhanced.py   # Legacy Tkinter UI (fallback, not actively developed)
├── jamulus_controller.py    # Mixer state + RPC/UDP integration
├── core/                    # Settings, modes, templates, protocol, metrics
├── ui/                      # Service layer shared with legacy UI (MixerService etc.)
├── services/                # BridgeService (Jamulus/Webex process lifecycle)
├── storage/                 # SQLite repository (users, mixes, room context, canvas)
├── admin/                   # RBAC policy engine and admin panel
├── api/                     # Optional FastAPI companion API
├── tests/                   # 493 passing tests
├── webjam_installer.py      # Windows/macOS installer script
└── VB/                      # VB-Cable installer payload (Windows)
```

Additional reading:
- [ARCHITECTURE.md](ARCHITECTURE.md) — system diagram and component responsibilities
- [DEVELOPMENT.md](DEVELOPMENT.md) — dev environment setup
- [CHANGELOG.md](CHANGELOG.md) — release history
- [CODE_REVIEW_FINDINGS.md](CODE_REVIEW_FINDINGS.md) — open issues
- [COMPANION_API.md](COMPANION_API.md) — localhost API for external tools

---

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/your-change`)
3. Commit with a clear message
4. Open a PR against `master`

---

## License

MIT. See `LICENSE`.

## Acknowledgments

- **Jamulus** — open-source low-latency audio
- **Webex** — video conferencing
- **VB-Audio Software** — virtual audio cable
