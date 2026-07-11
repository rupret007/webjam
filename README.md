# WebJam

A creative-collaboration shell that orchestrates **Jamulus** (low-latency audio) and **Webex** (video) into a single session, with a shared canvas for notes and artifacts.

> See [Current State](#current-state) for what works today and [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for what's planned next.
>
> **Getting your band on it?** Start with the [Quick Start for Your Band](README_SIMPLE.md) — install, setup, and your first jam, step by step.

---

## Current State

Being honest about where this app is **right now** (2026-07-10): the source
tree is the **v0.8.1 release candidate**; v0.8.0 remains the latest published
download until the real-hardware pilot gates pass.

| Area | Status |
|---|---|
| **Core data model** (participants, mixer, sessions, modes) | ✅ Works. Full suite is 800+ tests plus real-Jamulus integration (see `CHANGELOG.md` for the exact current count). |
| **Qt Conductor UI** | ✅ **Primary app.** `webjam_qt_main.py` is the entry point. Downloadable builds at [Releases](https://github.com/rupret007/webjam/releases). |
| **Legacy Tkinter UI** | ⚠️ Quarantined in `legacy/`. Not part of the pilot release path. |
| **Jamulus integration** | ✅ **JSON-RPC (matching shipping Jamulus 3.9–3.12) + UDP fallback.** Faders (`setFaderLevel`), real self-mute (`setMuted`), per-channel mute, live participant list and 0–9 level meters, and incoming chat all over authenticated newline-delimited JSON-RPC on TCP (Jamulus is launched with `--jsonrpcsecretfile`). Auto-reconnect retries dropped sessions. **Bundled with downloadable builds** — macOS is zero-install, Windows offers an in-wizard installer (see `THIRD_PARTY_NOTICES.md`); running from source still requires installing it separately. |
| **Webex integration** | ⚠️ **Embedded + browser fallback.** "Join Video" loads your Webex URL in the embedded `QWebEngineView`. Falls back to system browser if the embed fails. Guest-token flow optional (requires Webex developer account). |
| **Session canvas + Pulse** | ✅ **v0.8.1 candidate.** Notes persist locally. Session Pulse derives decisions, actions, blockers, questions, references, and next checkpoints locally; **Export… → Session brief…** writes a Markdown handoff without sending notes to a service. |
| **Audio routing** | ⚠️ **Detection only.** Setup finds VB-CABLE / BlackHole and links to installation help; users must still configure Jamulus, OS, and Webex device routing. |
| **Builds** | ✅ Three release artifacts: Windows x64, macOS ARM64, and macOS Intel x64. |
| **Local Companion API** | ⚠️ Read-only localhost bridge, off by default and opt-in. See [COMPANION_API.md](COMPANION_API.md). |

In practice today: WebJam is a **closed-pilot-ready Qt Conductor** — one window that launches Jamulus, embeds Webex, runs Ready Check, and gives you a live mixer for every participant. The Jamulus window still appears separately (downloadable builds bundle it — see `THIRD_PARTY_NOTICES.md` — a source checkout still needs it installed independently), but fader/mute/solo controls in WebJam drive it in real time. The audio button is gold, the video button is teal; the status bar shows “Connecting” until participant/RPC truth arrives. Click the audio button again to stop, the video button again to leave; the conductor's session title persists across launches.

Before widening the closed pilot or calling the release broadly ready, validate on real hardware: clean-machine Windows/macOS installs, Ctrl+P real-audio smoke, two-person Jamulus, Record button, take retrieval, and Take Deck playback. Demo Deck Level 2 should wait until those pass.

---

## What's Planned

See [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for the long-form vision. Near-term engineering phases:

1. **Pilot gates** — real-audio Ctrl+P, two-person Jamulus, Record, take retrieval, Take Deck playback
2. **Supportability** — signed/notarized builds, diagnostics redaction, recording retention guidance
3. **Architecture cleanup** — split `ApplicationController` into session/audio/video/recording/settings/API coordinators
4. **Post-pilot expansion** — Demo Deck Level 2, overdub/export, richer creative modes

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
- Jamulus client installed separately (running from source doesn't bundle it —
  see `THIRD_PARTY_NOTICES.md` for how downloadable builds do) + Webex (web or desktop)
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
| **F2 / Checks → Ready Check** | Run Ready Check before a jam |
| **Ctrl+1 / Ctrl+2 / Ctrl+3** | Open Live / Notes / Takes |
| **F11** | Toggle fullscreen |
| **Escape** | Exit fullscreen |
| **F1** | Show in-app help (shortcut & getting-started reference) |
| **Double-click fader** | Reset to 0 dB (unity gain) |

The session timer, mode picker, Record, Mute Me, Start Audio, and Join Video
controls are in the top strip; Ready Check and Practice are under **Checks ▾**.
The left rail switches between the Live and Notes workspaces; Takes and
Settings are utility actions that open their dedicated windows.
Session notes are saved to `~/.webjam_notes.md` and restored automatically.

---

## Project Structure

```
webjam/
├── webjam_qt_main.py        # Primary entry point — Qt Conductor UI
├── webjam_qt/               # Qt application (windows, widgets, controllers)
├── legacy/                  # Quarantined Tkinter app, admin RBAC, old tests
├── server/                  # Native macOS + Linux band-server runbooks
├── jamulus_controller.py    # Mixer state + RPC/UDP integration
├── core/                    # Settings, modes, protocol, metrics, take library
├── services/                # BridgeService (Jamulus/Webex process lifecycle)
├── storage/                 # SQLite repository (users, mixes, room context)
├── api/                     # Optional FastAPI companion API
├── tests/                   # Unit/UI/integration regression suite
├── build_webjam.py          # Legacy build helper; releases use webjam.spec
├── THIRD_PARTY_NOTICES.md   # Bundled Jamulus/VB-CABLE attribution + licensing
├── licenses/                # Bundled third-party license texts (JAMULUS_COPYING.txt)
└── VB/                      # VB-Cable installer payload (Windows)
```

Additional reading:
- [ARCHITECTURE.md](ARCHITECTURE.md) — system diagram and component responsibilities
- [DEVELOPMENT.md](DEVELOPMENT.md) — dev environment setup
- [CHANGELOG.md](CHANGELOG.md) — release history
- [COMPANION_API.md](COMPANION_API.md) — localhost API for external tools
- [legacy/CODE_REVIEW_FINDINGS.md](legacy/CODE_REVIEW_FINDINGS.md) — archived Tkinter-era review (not current open issues)

---

## Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/your-change`)
3. Commit with a clear message
4. Open a PR against `master`

---

## License

MIT. See `LICENSE`. WebJam bundles third-party software (Jamulus,
VB-CABLE) under their own licenses — see `THIRD_PARTY_NOTICES.md`.

## Acknowledgments

- **Jamulus** — open-source low-latency audio. WebJam bundles an
  unmodified, official copy (macOS: zero-install nested app; Windows:
  bundled installer) — see `THIRD_PARTY_NOTICES.md` for licensing detail.
- **Webex** — video conferencing
- **VB-Audio Software** — virtual audio cable
