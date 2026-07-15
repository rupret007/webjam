# WebJam

A musician-facing rehearsal and multitrack recording app built around one
flow: **Host or Join. Confirm your sound. Band Check. Play.** WebJam runs Jamulus as a
background engine; video, notes, and Studio remain optional session tools.

> See [Current State](#current-state) for what works today and [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for what's planned next.
>
> **Getting your band on it?** Start with the [Quick Start for Your Band](README_SIMPLE.md) — install, setup, and your first jam, step by step.

---

## Current State

As of 2026-07-14, `master` contains the **v0.15.0 private test-night source
candidate**. The exact package identity is recorded only after the package is
built and verified; v0.14.0 remains the rollback candidate. WebJam's supported
musician path is still one private LAN, using v1/v2. The v3 path remains a
loopback-only development profile—not public hosting or an Internet service.

The normal flow is deliberately small:

> **Host or Join → Confirm Sound → Band Check → Play → Record → Review → Export → End**

| Area | Status |
|---|---|
| **Session Conductor** | ✅ Derives one musician-facing state and one dominant action from real host, Jamulus, recorder, take, Studio, export, and cleanup facts. It never treats a process, meter, or button press as proof of connection, audibility, saved media, or export validity. |
| **Meeting-style session UI** | ✅ Black, white, neutral, and burnt orange only. The original three-path WebJam mark replaces the old abbreviation. The focused HUD owns the next action; details live under **More**. |
| **Band Check** | ✅ Stable local route evidence is reused only while it remains valid; changed, missing, stale, or reconfigured routes require an honest new check. It still distinguishes setup evidence from human two-way audibility. |
| **Multitrack Studio** | ✅ A simple, editor-like review workspace: transport, seconds-only ruler, horizontal waveform lanes, track headers, mute/solo/gain/pan, and selected-track inspection. It is intentionally not a DAW and never invents bars, beats, plug-ins, or edits. |
| **Track Export** | ✅ WebJam produces an atomic, portable package of aligned PCM24 WAV tracks, references, reports, analysis, and checksums. It does **not** create, launch, control, or integrate with Logic or any other editor. |
| **Closed-pilot evidence** | ✅ `--test-night` exposes a hidden operator checklist with a bounded, local-only append-only ledger. Automatic facts and explicit human observations remain separate; a missing second Mac or audio interface stays **NOT RUN** or **BLOCKED**. |
| **Physical certification** | ⚠️ This Mac has no proven two-Mac or recording-interface setup. Two-way audibility, physical recording/recovery, interruption, and external-editor import remain **NOT RUN** until observed and recorded. |

For a musician, WebJam keeps Jamulus and the host service in the background,
shows only the next safe action, and preserves recording truth when something
is missing or uncertain. It does not claim public Internet, VPN, NAT traversal,
IPv6, or remote v3 readiness.

---

## What's Planned

See [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for the long-form vision. Near-term engineering phases:

1. **Certification gates** — two-Mac bidirectional audio/outage/originals and exact-package external-editor import using the v0.15.0 candidate
2. **Distribution** — signing/notarization and a published artifact only after the private gates pass
3. **Architecture cleanup** — continue splitting `ApplicationController` into session/audio/video/recording/settings/API coordinators
4. **Remote v3 external validation** — keep the reference path local/CI until a separately reviewed public profile, service deployment, ordinary-home NAT tests, and physical acoustic evidence exist
5. **Post-pilot expansion** — overdub workflows, deeper editing, broader editor-friendly interchange, and richer creative modes

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
- `WEBJAM_LOCAL_CAPTURE_ENABLED` — keep this Mac's local interface originals independently of Webex mode
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
| **Ctrl+T** | Insert timestamp heading into Session Canvas |
| **Ctrl+Shift+R** | Reset all faders to 0 dB (with confirmation) |
| **Ctrl+Shift+D** | Copy diagnostics summary to clipboard |
| **Ctrl+,** | Open name, basic audio, and optional conversation preferences |
| **F2** | Open permanent Band Check |
| **Ctrl+1 / Ctrl+2 / Ctrl+3** | Open Live / Notes / Studio |
| **F11** | Toggle fullscreen |
| **Escape** | Exit fullscreen |
| **F1** | Show in-app help (shortcut & getting-started reference) |
| **Double-click fader** | Reset to 0 dB (unity gain) |

The primary session surface follows a familiar meeting layout: a restrained
header, one readiness surface, large responsive musician tiles, and one bottom
control bar for **Copy Invite**, **Record**, **More**, and the role-aware
**End Session** or **Leave Jam** action. Notes, Studio, video/conversation,
Settings, and Band Check are under **More**. The visual system uses
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
├── transport/               # Static Go v3 sidecar, protocol, and security labs
├── reference_service/       # Self-hostable local/CI rendezvous + exact-pair relay
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
- [v0.14 historical last-mile record](docs/WEBJAM_V1_LAST_MILE_PLAN.md) — retained rollback package evidence
- [DEVELOPMENT.md](DEVELOPMENT.md) — dev environment setup
- [CHANGELOG.md](CHANGELOG.md) — release history
- [COMPANION_API.md](COMPANION_API.md) — localhost API for external tools
- [Remote transport ADR](docs/adr/0001-remote-session-transport.md) — v3 sidecar decision, proof boundary, and rejected alternatives
- [Remote threat model](docs/security/remote-session-threat-model.md) — v3 security contract and required evidence
- [Reference service](reference_service/README.md) — local/CI native rendezvous and exact-pair relay; no public deployment
- [WEBEX_AUDIO_MODES.md](WEBEX_AUDIO_MODES.md) — safe Jamulus music, Webex conversation, and audience-bridge signal flows
- [RECORDING_AND_STUDIO.md](RECORDING_AND_STUDIO.md) — take integrity, Studio mixing, per-Mac isolated originals, and portable track export
- [CLOSED_PILOT_PLAYBOOK.md](CLOSED_PILOT_PLAYBOOK.md) — hidden Test Night, local evidence, and truthful human observations
- [SUNDAY_TWO_MAC_PILOT.md](SUNDAY_TWO_MAC_PILOT.md) — exact-artifact two-Mac certification worksheet
- [QUICK_HELP_MAP.md](QUICK_HELP_MAP.md) — concise musician paths through the app
- [TEST_PROCEDURE.md](TEST_PROCEDURE.md) — repeatable source and package checks
- [FIRST_JAM.md](FIRST_JAM.md) and [USER_GUIDE.md](USER_GUIDE.md) — musician setup and everyday workflow
- [COHORT_VALIDATION_PLAYBOOK.md](COHORT_VALIDATION_PLAYBOOK.md) — facilitated pilot evidence
- [HELP_ROUTING_MAP.md](HELP_ROUTING_MAP.md) — where support and operator questions belong
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
