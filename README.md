# WebJam

A musician-facing rehearsal and multitrack recording app built around one
flow: **Host or Join. Band Check. Play.** WebJam runs Jamulus as a
background engine; video, notes, and Studio remain optional session tools.

> See [Current State](#current-state) for what works today and [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for what's planned next.
>
> **Getting your band on it?** Start with the [Quick Start for Your Band](README_SIMPLE.md) — install, setup, and your first jam, step by step.

---

## Current State

Being honest about where this app is **right now** (2026-07-13): this source
tree is the private **v0.11.0 test-night candidate**. Its ordinary musician
path remains the proven v1/v2 same-LAN flow; v3 is still a loopback-only
development slice, not an “anywhere” service. The preserved v0.10.0 fallback
ZIP remains intact, and v0.8.0 is still the latest published download. The
two-Mac acoustic test and Logic Pro import are still **NOT RUN**.

| Area | Status |
|---|---|
| **Core data model** (participants, mixer, sessions, modes) | ✅ Works. The release gate runs the full automated suite plus packaged-runtime and two-Mac physical checks. |
| **Qt Conductor UI** | ✅ **Primary app.** `webjam_qt_main.py` opens a black, white, neutral, and burnt-orange Host/Join experience with a responsive meeting-style stage and an original three-part WebJam mark. There is no purple or teal. Downloadable builds remain at [Releases](https://github.com/rupret007/webjam/releases). |
| **Legacy Tkinter UI** | ⚠️ Quarantined in `legacy/`. Not part of the pilot release path. |
| **Jamulus integration** | ✅ **Authenticated JSON-RPC with the bundled Jamulus 3.12.2.** Faders (`setFaderLevel`), per-channel monitor mute, live participant list, honest 0–9 level meters, and incoming chat use the client’s authenticated local interface. The pinned build has no live-send mute API; use the audio-interface mute or end the session before speaking in Webex. Auto-reconnect retries dropped sessions. **Bundled with downloadable builds** — macOS is zero-install; running from source still requires installing Jamulus separately (see `THIRD_PARTY_NOTICES.md`). |
| **Band Check** | ✅ **Permanent readiness path.** After Host/Join is chosen, a new or changed setup runs the guided engine/server, local input, headphone output, five-second recording/playback, and Studio checks before the session starts. A separate footer action opens the support-bundle preview. F2 and Settings reopen Band Check; live troubleshooting observes without restarting. It reports Ready to Jam / Ready with a Warning / Action Needed. Its separate PortAudio meter is never presented as proof of the Jamulus route. |
| **Host → Share → Join** | ✅ **Verified v0.10.0 v1/v2 baseline.** Launch shows **Host a Jam** and **Join a Jam**. A new or changed setup requires Band Check and **Start Session**; a matching stored check continues directly. A cold-opened `webjam://join?...` link fills and accepts the connection before that readiness/start step. A v2 link normally includes a random private same-session enrollment credential. Treat the whole invite as private. If the host warns **Automatic Local Originals are off**, its legacy v1 fallback still joins Jamulus and receives a host-side server track, but WebJam provides no guest local-original capture or delivery path. |
| **Remote v3 development slice** | ⚠️ Strict invitations, the native sidecar/security core, a bounded self-hostable reference service, authenticated native loopback integration, and deterministic direct/relay/impairment evidence exist in this source tree. The compiled `reference-local` profile is loopback-only and lab-only. **No public rendezvous/relay is deployed**, and local/CI evidence is not a two-home, real-Jamulus, packaged, or acoustic result. The ordinary musician path remains v1/v2 on one private LAN. |
| **All-in-one band-server hosting** | ✅ WebJam verifies the bundled JamulusServer.app 3.12.2, binds recorder RPC to loopback, stores its mode-0600 secret/takes under Application Support, and supervises both processes. **End Session** is blocked while a host take is recording or validating; press **Stop Rec**, wait for **Take saved**, then end to clean up the client, server, and `caffeinate`. |
| **Multitrack Studio** | ✅ JamulusServer records one synchronized post-network track per musician. The host—and a guest connected through an active v2 private invite—can explicitly keep interface inputs 1 and 2 as local PCM24/48-kHz originals. Schema-v2 projects retain stable identities, segments, gaps, device/hash/media status, and non-destructive offset/drift evidence. Studio supports mixed-rate multi-segment waveforms/playback, seek, gain, pan, mute, and multi-solo. **Export for Logic** produces common-origin PCM24 stems, server/Studio references, reports, independent analysis, source evidence, and checksums without changing the source take. |
| **Private guest-original delivery** | ⚠️ Implemented and deterministically tested for the private pilot when the v2 peer service is active. Capture continues through peer outage and resumes size/SHA/PCM-verified transfer without deleting the guest original. **Leave Jam** finalizes an active guest original, persists its queue, and attempts one last upload; an unavailable host leaves resumable media on the guest Mac. A v1 fallback has no WebJam-orchestrated guest local capture or delivery. The authenticated service is plain HTTP on the same RFC1918 IPv4 LAN—no TLS, IPv6, Internet, VPN, NAT traversal, quota/rate limiting, or public-exposure claim. The v2 link is a reusable session bearer, not a one-use token; anyone holding it on that LAN can enroll until the host peer restarts. Physical two-Mac proof is **NOT RUN**. |
| **Support bundle** | ✅ The preview and saved ZIP share one immutable allowlisted artifact. `Ctrl+Shift+D` separately creates a short sanitized clipboard summary. The private archive excludes audio, notes, transcripts, Webex content, meeting/invite links, settings/environment dumps, secrets, home paths, and arbitrary personal files by default; bounded log text is recursively redacted. Fresh packaged-button verification is pending. |
| **Webex integration** | ✅ **Optional external launch.** Video/conversation is absent from startup and lives under More. WebJam opens a configured room in native Webex/default browser and truthfully reports only that it opened. |
| **Session canvas + Pulse** | ✅ Notes persist locally. Session Pulse derives decisions, actions, blockers, questions, references, and next checkpoints locally; **Export… → Session brief…** writes a Markdown handoff without sending notes to a service. |
| **Audio defaults and truth** | ⚠️ Jamulus runs headlessly using its own saved/default hardware route. WebJam does not inspect or choose that PCM device. Its local meter/isolated recorder opens a separate PortAudio/Core Audio stream, so only a musician hearing both directions proves the live route. Roster presence proves connection; observed levels prove only the source they actually measure. |
| **Certification evidence** | ⚠️ GitHub Actions run [`29269188463`](https://github.com/rupret007/webjam/actions/runs/29269188463) passed the exact-source native Ubuntu Jamulus 3.12.2/JACK certification: 3,602.851 seconds of measured transport, 396 cycles, three recording/restart cycles, forced reconnect in 34.526 seconds, zero decoded dropout windows, bounded resources/xruns, 12 WAV stems, and zero cleanup errors. The earlier 667.201-second Docker ARM failure remains preserved evidence, not a pass. Two-Mac audibility and Logic Pro import remain **NOT RUN**. |
| **Builds** | ⚠️ CI passes Windows x64, macOS ARM64, and macOS Intel x64 for source `8ee89081802fe5998f71299c4755b21ae5218cb9`. The fresh private Apple Silicon artifact is `WebJam-v0.10.0-TEST-NIGHT-macos-arm64.zip`, SHA-256 `f955419909dc014b7172032b00524417983c09e8586c2217691c19838a0b3411`; strict/deep ad-hoc signature verification, fresh extraction, and two packaged Host lifecycle runs pass. It is not Developer ID signed or notarized. Do not reuse the preserved v0.9.0 handoff. |
| **Local Companion API** | ⚠️ Read-only localhost bridge, off by default and opt-in. See [COMPANION_API.md](COMPANION_API.md). |

In practice today: the preserved **v0.10.0 candidate** takes a musician from two
launch choices through any required Band Check and into a hosted or joined
rehearsal without a server form. Jamulus client/server processes stay in the
background.
The live window shows aggregate readiness, one private invite action, real band
members, and one More menu. The same-RFC1918-LAN flow is implemented; public
Internet, VPN, NAT traversal, and IPv6 are not claimed for the ordinary app.
The separate v3 work has a loopback-only reference profile for automated and
developer evidence; it does not create a public service or an “anywhere” claim.

Before widening the closed pilot, validate the exact artifact on two Macs:
link launch, bidirectional physical audio, a named two-musician take, Studio
playback, aligned Logic export/import, reconnect, and owned-process cleanup.

---

## What's Planned

See [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for the long-form vision. Near-term engineering phases:

1. **Certification gates** — two-Mac bidirectional audio/outage/originals and exact-package Logic import using the verified v0.10.0 artifact
2. **Distribution** — signing/notarization and a published artifact only after the private gates pass
3. **Architecture cleanup** — continue splitting `ApplicationController` into session/audio/video/recording/settings/API coordinators
4. **Remote v3 external validation** — keep the reference path local/CI until a separately reviewed public profile, service deployment, ordinary-home NAT tests, and physical acoustic evidence exist
5. **Post-pilot expansion** — overdub workflows, deeper editing, broader DAW handoffs, and richer creative modes

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
| **Ctrl+,** | Open name / optional conversation preferences |
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
- [DEVELOPMENT.md](DEVELOPMENT.md) — dev environment setup
- [CHANGELOG.md](CHANGELOG.md) — release history
- [COMPANION_API.md](COMPANION_API.md) — localhost API for external tools
- [Remote transport ADR](docs/adr/0001-remote-session-transport.md) — v3 sidecar decision, proof boundary, and rejected alternatives
- [Remote threat model](docs/security/remote-session-threat-model.md) — v3 security contract and required evidence
- [Reference service](reference_service/README.md) — local/CI native rendezvous and exact-pair relay; no public deployment
- [WEBEX_AUDIO_MODES.md](WEBEX_AUDIO_MODES.md) — safe Jamulus music, Webex conversation, and audience-bridge signal flows
- [RECORDING_AND_LOGIC.md](RECORDING_AND_LOGIC.md) — take integrity, Studio mixing, per-Mac isolated originals, and the aligned Logic handoff
- [SUNDAY_TWO_MAC_PILOT.md](SUNDAY_TWO_MAC_PILOT.md) — exact-artifact two-Mac and Logic certification worksheet
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
