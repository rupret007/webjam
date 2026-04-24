# WebJam

A creative-collaboration shell that orchestrates **Jamulus** (low-latency audio) and **Webex** (video) into a single session, with a shared canvas for notes and artifacts.

> **Status:** under active redesign. See [Current State](#current-state) for what actually works today vs. [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for the destination.

---

## Current State

Being honest about where this app is **right now** (2026-04-20):

| Area | Status |
|---|---|
| **Core data model** (participants, mixer, sessions, modes) | ✅ Works. 74 unit tests pass. |
| **Qt Conductor UI** | ✅ **Shipped in v0.3.0.** `webjam_qt_main.py` is the primary entry point. Run with `python webjam_qt_main.py`. Downloadable builds at [Releases](https://github.com/rupret007/webjam/releases). |
| **Legacy Tkinter UI** | ⚠️ Retained as fallback (`webjam_app_enhanced.py`). Not actively developed. Will be removed when Qt UI reaches full parity. |
| **Jamulus integration** | ⚠️ **Launch-only.** WebJam launches Jamulus as a separate process. Mixer faders in WebJam do **not** yet drive the running Jamulus server — full JSON-RPC control is planned for v0.4. **Jamulus must be installed separately.** |
| **Webex integration** | ⚠️ **Launch-only.** Opens your Webex meeting URL in a browser. Embedded view (Web SDK inside Qt) planned for v0.4. |
| **Audio routing** | ⚠️ **Semi-automatic.** Setup wizard detects VB-CABLE / BlackHole. If not installed, the wizard links to instructions. |
| **Builds** | ✅ Windows x64, macOS ARM64, macOS x64 — all three zips at [Releases](https://github.com/rupret007/webjam/releases). |
| **Local Companion API** | ✅ Localhost bridge for external tools. See [COMPANION_API.md](COMPANION_API.md). |

In practice today: WebJam is a **launcher + local note-taking UI** wrapped around two separate apps (Jamulus window, Webex browser). Users alt-tab between windows.

The redesign in progress replaces this with a unified conductor-style window (Qt/PySide6), real Jamulus protocol control, and embedded Webex via the Web SDK.

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

## In-App Menus (new Qt Conductor UI)

- **Session → Run Ready Check** — pass/fail readiness summary
- **Session → Open Diagnostics Panel** — endpoint checks, audio backend state, recovery hints
- **Session → Export Session Brief** — markdown handoff document
- **Help → Run Setup Wizard** — preflight checks for Jamulus path, server, Webex URL, audio
- **Help → View Usage Metrics** — local-only counters
- **View → High Contrast / Large Text / Text Size** — accessibility
- **Startup → Auto reconnect services / Reset UI** — startup preferences

---

## Project Structure

```
webjam/
├── webjam_app_enhanced.py   # Main app (being refactored)
├── core/                    # Domain: audio, modes, protocol, models, settings
├── ui/                      # Modular Qt UI components (being migrated)
├── services/                # BridgeService (Jamulus/Webex lifecycle)
├── api/                     # Local Companion API bridge
├── storage/                 # Settings, mix, user repositories
├── tests/                   # Unit tests (74 passing)
├── webjam_installer.py      # Windows/macOS installer
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
