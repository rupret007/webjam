# WebJam

A musician-facing rehearsal and multitrack recording app built around one
flow: **Host. Share. Join. Play.** WebJam runs Jamulus as a background engine;
video, notes, and Studio remain optional session tools.

> See [Current State](#current-state) for what works today and [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for what's planned next.
>
> **Getting your band on it?** Start with the [Quick Start for Your Band](README_SIMPLE.md) — install, setup, and your first jam, step by step.

---

## Current State

Being honest about where this app is **right now** (2026-07-13): the source
tree is the **v0.9.0 test-night candidate**; v0.8.0 remains the latest published
download until the real-hardware pilot gates pass.

| Area | Status |
|---|---|
| **Core data model** (participants, mixer, sessions, modes) | ✅ Works. The release gate runs the full automated suite plus packaged-runtime and two-Mac physical checks. |
| **Qt Conductor UI** | ✅ **Primary app.** `webjam_qt_main.py` opens a black, white, and burnt-orange Host/Join experience with a responsive meeting-style stage. Downloadable builds remain at [Releases](https://github.com/rupret007/webjam/releases). |
| **Legacy Tkinter UI** | ⚠️ Quarantined in `legacy/`. Not part of the pilot release path. |
| **Jamulus integration** | ✅ **Authenticated JSON-RPC with the bundled Jamulus 3.12.2.** Faders (`setFaderLevel`), real self-mute (`setMuted`), per-channel mute, live participant list and honest 0–9 level meters, and incoming chat all use the client’s authenticated local interface. Auto-reconnect retries dropped sessions. **Bundled with downloadable builds** — macOS is zero-install; running from source still requires installing Jamulus separately (see `THIRD_PARTY_NOTICES.md`). |
| **Host → Share → Join** | ✅ **macOS v0.9.0 candidate.** Launch shows only **Host a Jam** and **Join a Jam**. Hosting derives and starts every server/client default automatically. A strict, non-secret `webjam://join?...` invitation is available only after server readiness; clicking or pasting it configures and starts the guest. |
| **All-in-one band-server hosting** | ✅ WebJam verifies the bundled JamulusServer.app 3.12.2, binds recorder RPC to loopback, stores its mode-0600 secret/takes under Application Support, supervises both processes, and cleans up recording, client, server, and `caffeinate` on End Session or quit. |
| **Multitrack Studio** | ✅ The host records one synchronized server track per musician. Studio adds live armed lanes, take history, waveforms, stereo output selection, scrub/playback, gain, pan, mute, and solo. **Export for Logic** produces equal-length, zero-aligned 24-bit stems, a stereo rough mix, instructions, and an evidence manifest without changing the source take. |
| **Webex integration** | ✅ **Optional external launch.** Video/conversation is absent from startup and lives under More. WebJam opens a configured room in native Webex/default browser and truthfully reports only that it opened. |
| **Session canvas + Pulse** | ✅ **v0.9.0 candidate.** Notes persist locally. Session Pulse derives decisions, actions, blockers, questions, references, and next checkpoints locally; **Export… → Session brief…** writes a Markdown handoff without sending notes to a service. |
| **Audio defaults and truth** | ✅ Jamulus runs headlessly with the system/default interface. WebJam never requires a virtual device for ordinary rehearsal and never fabricates meter motion. Roster presence proves connection; observed levels separately prove input activity. |
| **Builds** | ✅ CI targets Windows x64, macOS ARM64, and macOS Intel x64. The private v0.9.0 test-night handoff is Apple Silicon only. |
| **Local Companion API** | ⚠️ Read-only localhost bridge, off by default and opt-in. See [COMPANION_API.md](COMPANION_API.md). |

In practice today: the **v0.9.0 test-night candidate** takes a musician from two
launch choices to a hosted or joined rehearsal without a setup form. Jamulus client
and server processes stay in the background. The live window shows aggregate
readiness, one clear invite action, real band members, and one More menu. The
same-LAN invitation flow is implemented; remote NAT traversal is not
claimed.

Before widening the closed pilot, validate the exact artifact on two Macs:
link launch, bidirectional physical audio, a named two-musician take, Studio
playback, aligned Logic export/import, reconnect, and owned-process cleanup.

---

## What's Planned

See [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for the long-form vision. Near-term engineering phases:

1. **Pilot gates** — two-Mac invite link, real bidirectional audio, Studio take, exact-artifact Logic import
2. **Supportability** — signed/notarized builds, diagnostics redaction, recording retention guidance
3. **Architecture cleanup** — split `ApplicationController` into session/audio/video/recording/settings/API coordinators
4. **Post-pilot expansion** — overdub workflows, deeper editing, broader DAW handoffs, richer creative modes

---

## Running from Source

```bash
git clone https://github.com/rupret007/webjam.git
cd webjam
pip install -r requirements.txt
python webjam_qt_main.py  # Choose Host a Jam or Join a Jam
```

System requirements:
- Python 3.10+
- Windows 10/11 or macOS 13+
- Jamulus client installed separately when running from source (downloadable
  macOS builds bundle it)
- Downloadable macOS builds include official Jamulus client/server 3.12.2;
  source runs use compatible apps in `/Applications`
- Broadband network with <30 ms latency to your Jamulus server

---

## Configuration

Normal musicians do not edit configuration. Developer/runtime state lives in:
- **Config:** `~/.webjam_config.json`
- **Mix:** `~/.webjam_mix.json` (anonymous/local fallback)

Environment overrides:
- `WEBJAM_JAMULUS_SERVER` — Jamulus server host
- `WEBJAM_JAMULUS_PORT` — Jamulus port (default 22124)
- `WEBJAM_MUSICIAN_NAME` — participant name shown through Jamulus
- `WEBJAM_WEBEX_URL` — Webex meeting URL
- `WEBJAM_WEBEX_AUDIO_MODE` — `talkback` (default), `video_only`, or `audience_bridge`
- `WEBJAM_LOCAL_CAPTURE_ENABLED` — enable supplemental local stem capture independently of Webex mode
- `WEBJAM_HOST_SERVER_ENABLED` — macOS only; supervise the same-Mac band server and connect the host client over loopback
- `WEBJAM_JAMULUS_CANDIDATES` — `;`-separated list of Jamulus executable paths

---

## Qt Conductor Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| **Ctrl+L** | Focus / edit the session title |
| **Ctrl+S** | Save current mixer state to `~/.webjam_mix.json` |
| **Ctrl+O** | Load and apply saved mix from `~/.webjam_mix.json` |
| **Ctrl+M** | Mute all / Unmute all (toggles) |
| **Ctrl+Shift+M** | Talk Break / Resume Music in talkback mode; otherwise mute/unmute Jamulus send |
| **Ctrl+T** | Insert timestamp heading into Session Canvas |
| **Ctrl+Shift+R** | Reset all faders to 0 dB (with confirmation) |
| **Ctrl+Shift+D** | Copy diagnostics summary to clipboard |
| **Ctrl+,** | Open name / optional conversation preferences |
| **F2** | Open secondary troubleshooting |
| **Ctrl+1 / Ctrl+2 / Ctrl+3** | Open Live / Notes / Studio |
| **F11** | Toggle fullscreen |
| **Escape** | Exit fullscreen |
| **F1** | Show in-app help (shortcut & getting-started reference) |
| **Double-click fader** | Reset to 0 dB (unity gain) |

The primary session surface follows a familiar meeting layout: a restrained
header, one readiness surface, large responsive musician tiles, and one bottom
control bar for **Copy Invite**, **Record**, **More**, and the role-aware
**End Session** or **Leave Jam** action. Notes, Studio, video/conversation,
Settings, and troubleshooting are under **More**. The visual system uses
near-black surfaces, white text, and burnt orange (`#BF5700`) for deliberate
emphasis—no purple or teal.
Session notes are saved to `~/.webjam_notes.md` and restored automatically.

---

## Project Structure

```
webjam/
├── webjam_qt_main.py        # Primary entry point — Qt Conductor UI
├── webjam_qt/               # Qt application (windows, widgets, controllers)
├── legacy/                  # Quarantined Tkinter app, admin RBAC, old tests
├── server/                  # Native macOS + Linux band-server runbooks
├── jamulus_controller.py    # Mixer state + authenticated RPC integration
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
- [WEBEX_AUDIO_MODES.md](WEBEX_AUDIO_MODES.md) — safe Jamulus music, Webex talkback, and audience-bridge signal flows
- [RECORDING_AND_LOGIC.md](RECORDING_AND_LOGIC.md) — take integrity, Studio mixing, isolated host inputs, and the aligned Logic handoff
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

- **Jamulus** — open-source low-latency audio. WebJam bundles the official
  release (macOS: prepared zero-install nested apps; Windows: unmodified
  installer) — see `THIRD_PARTY_NOTICES.md` for exact signature and licensing
  details.
- **Webex** — video conferencing
- **VB-Audio Software** — virtual audio cable
