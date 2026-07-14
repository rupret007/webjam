# WebJam

A musician-facing rehearsal and multitrack recording app built around one
flow: **Host or Join. Confirm your sound. Band Check. Play.** WebJam runs Jamulus as a
background engine; video, notes, and Studio remain optional session tools.

> See [Current State](#current-state) for what works today and [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for what's planned next.
>
> **Getting your band on it?** Start with the [Quick Start for Your Band](README_SIMPLE.md) — install, setup, and your first jam, step by step.

---

## Current State

Being honest about where this app is **right now** (2026-07-14): `master`
contains the **v0.13.0 test-night candidate**, built from
`4d09810d7fb3c7f7355ca1d88e8218bb8ea784dd`. The exact private Apple-Silicon
ZIP includes the sound-confirmation screen, CoreAudio route preflight,
recording-storage guard, durable local-capture recovery, and conservative
Logic-export safety checks. Its ordinary musician path remains the proven v1/v2 same-LAN flow;
v3 is still a loopback-only development slice, not an “anywhere” service. The
preserved v0.11.0 and v0.10.0 ZIPs remain rollback history, and v0.8.0 is still
the latest published download. The two-Mac acoustic test and Logic Pro import
are still **NOT RUN**.

| Area | Status |
|---|---|
| **Core data model** (participants, mixer, sessions, modes) | ✅ The current source gate completed with 1,706 passed, 18 skipped, one existing Starlette/httpx deprecation warning, and 6 subtests; there were no failures or errors. Exact-package and two-Mac physical evidence remain separately tracked and are never inferred from unit tests. |
| **Qt Conductor UI** | ✅ **Primary app.** `webjam_qt_main.py` opens a black, white, neutral, and burnt-orange Host/Join experience with a responsive meeting-style stage and an original three-part WebJam mark. There is no purple or teal. Downloadable builds remain at [Releases](https://github.com/rupret007/webjam/releases). |
| **Legacy Tkinter UI** | ⚠️ Quarantined in `legacy/`. Not part of the pilot release path. |
| **Jamulus integration** | ✅ **Authenticated JSON-RPC with the pinned Jamulus 3.12.2.** Faders (`setFaderLevel`), per-channel monitor mute, live participant list, honest 0–9 level meters, and incoming chat use the client’s authenticated local interface. On current macOS source, WebJam resolves stable CoreAudio IDs, stages its own filename-only Jamulus route config, and fails closed for a missing, ambiguous, or non-48-kHz chosen device. The pinned build has no live-send mute API; use the audio-interface mute or end the session before speaking in Webex. Auto-reconnect retries dropped sessions without silently changing the selected route. **Bundled with downloadable builds** — macOS is zero-install; running from source still requires installing Jamulus separately (see `THIRD_PARTY_NOTICES.md`). |
| **Band Check** | ✅ **Permanent readiness path.** In v0.13.0, Host/Join first confirms the musician's name and band sound, then a new or changed setup runs the guided engine/server, local input, headphone output, five-second recording/playback, and Studio checks before the session starts. Its stored evidence includes a non-identifying macOS route fingerprint when WebJam manages Jamulus. It also checks recording storage and renders low space as a warning. A separate footer action opens the support-bundle preview. F2 and Settings reopen Band Check; live troubleshooting observes without restarting. It reports Ready to Jam / Ready with a Warning / Action Needed. A separate PortAudio meter is never presented as proof that a bandmate can hear the route. |
| **Host → Share → Join** | ✅ **Verified v0.10.0 v1/v2 baseline.** Launch shows **Host a Jam** and **Join a Jam**. Current source follows either choice with one short name and band-sound confirmation before Band Check and **Start Session**; a matching stored check continues directly. For the supported private-LAN host flow, **Copy Invite** stays unavailable until WebJam sees an authenticated hosted server, its expected UDP listener, and a private Wi-Fi address. That is a local pre-share check—not a public-Internet reachability claim. A cold-opened `webjam://join?...` link is parsed before that confirmation and readiness/start step. A v2 link normally includes a random private same-session enrollment credential. Treat the whole invite as private. If the host warns **Automatic Local Originals are off**, its legacy v1 fallback still joins Jamulus and receives a host-side server track, but WebJam provides no guest local-original capture or delivery path. |
| **Remote v3 development slice** | ⚠️ Strict invitations, the native sidecar/security core, a bounded self-hostable reference service, authenticated native loopback integration, and deterministic direct/relay/impairment evidence exist in this source tree. The compiled `reference-local` profile is loopback-only and lab-only. **No public rendezvous/relay is deployed**, and local/CI evidence is not a two-home, real-Jamulus, packaged, or acoustic result. The ordinary musician path remains v1/v2 on one private LAN. |
| **All-in-one band-server hosting** | ✅ WebJam verifies the bundled JamulusServer.app 3.12.2, binds recorder RPC to loopback, stores its mode-0600 secret/takes under Application Support, and supervises both processes. **End Session** is blocked while a host take is recording or validating; press **Stop Rec**, wait for **Take saved**, then end to clean up the client, server, and `caffeinate`. |
| **Multitrack Studio** | ✅ JamulusServer records one synchronized post-network track per musician. v0.13.0 checks the selected recording folder and a conservative free-storage reserve before a take; it starts nothing when unsafe, renders low space as a warning, and checks again using the actual roster at Record time. The host—and a guest connected through an active v2 private invite—can explicitly keep interface inputs 1 and 2 as local PCM24/48-kHz originals. Schema-v2 projects retain stable identities, segments, gaps, device/hash/media status, and non-destructive offset/drift evidence. Session evidence contains only WebJam-observed UTC timestamps recorded after server confirmation, host/protocol, and a bounded redacted lifecycle/recovery timeline—never an invite, address, credential, or raw device identifier. Local capture periodically flushes and fsyncs audio; a crash recovery is published as a **NEEDS ATTENTION** project, never as a completed take. Studio supports mixed-rate multi-segment waveforms/playback, seek, gain, pan, mute, and multi-solo. **Export for Logic** refuses an explicitly silent selected performance track or an unverified/unaligned guest-local original until it is reviewed, intentionally deselected, or aligned and verified; it otherwise produces common-origin PCM24 stems, references, reports, analysis, source evidence, checksums, and nonempty session evidence without changing the source take. Physical recording/recovery and Logic import remain **NOT RUN**. |
| **Private guest-original delivery** | ⚠️ Implemented and deterministically tested for the private pilot when the v2 peer service is active. Capture continues through peer outage and resumes size/SHA/PCM-verified transfer without deleting the guest original. **Leave Jam** finalizes an active guest original, persists its queue, and attempts one last upload; an unavailable host leaves resumable media on the guest Mac. A v1 fallback has no WebJam-orchestrated guest local capture or delivery. The authenticated service is plain HTTP on the same RFC1918 IPv4 LAN—no TLS, IPv6, Internet, VPN, NAT traversal, quota/rate limiting, or public-exposure claim. The v2 link is a reusable session bearer, not a one-use token; anyone holding it on that LAN can enroll until the host peer restarts. Physical two-Mac proof is **NOT RUN**. |
| **Support bundle** | ✅ The preview and saved ZIP share one immutable allowlisted artifact. `Ctrl+Shift+D` separately creates a short sanitized clipboard summary. The private archive excludes audio, notes, transcripts, Webex content, meeting/invite links, settings/environment dumps, secrets, home paths, and arbitrary personal files by default; bounded log text is recursively redacted. Fresh packaged-button verification is pending. |
| **Webex integration** | ✅ **Optional external launch.** Video/conversation is absent from startup and lives under More. WebJam opens a configured room in native Webex/default browser and truthfully reports only that it opened. |
| **Session canvas + Pulse** | ✅ Notes persist locally. Session Pulse derives decisions, actions, blockers, questions, references, and next checkpoints locally; **Export… → Session brief…** writes a Markdown handoff without sending notes to a service. |
| **Audio defaults and truth** | ⚠️ In v0.13.0, **Settings → Band input / Band output & review** resolves persistent CoreAudio IDs, verifies a unique 48-kHz pair, writes only WebJam’s protected route file, and launches Jamulus with that file’s basename. That is configuration and OS preflight—not graph or hearing proof. The local meter/isolated recorder remains a separate PortAudio/Core Audio stream, so only musicians hearing both directions prove the live route. |
| **Certification evidence** | ⚠️ For the v1/v2 engine baseline, GitHub Actions run [`29269188463`](https://github.com/rupret007/webjam/actions/runs/29269188463) passed the native Ubuntu Jamulus 3.12.2/JACK certification: 3,602.851 seconds of measured transport, 396 cycles, three recording/restart cycles, forced reconnect in 34.526 seconds, zero decoded dropout windows, bounded resources/xruns, 12 WAV stems, and zero cleanup errors. That run is not evidence for remote v3, CoreAudio, or the exact v0.13 package. The earlier 667.201-second Docker ARM failure remains preserved evidence, not a pass. Two-Mac audibility and Logic Pro import remain **NOT RUN**. |
| **Builds** | ⚠️ The fresh private Apple Silicon artifact is `WebJam-v0.13.0-TEST-NIGHT-macos-arm64.zip`, SHA-256 `6b32a1d85cb64eb0bc97fecb7dadcd527159420a675358176cd75745d6565b3b`, built from `4d09810d7fb3c7f7355ca1d88e8218bb8ea784dd`. It is arm64, bundles official Jamulus/JamulusServer 3.12.2 with the upstream DMG checksum verified, and passes fresh-extraction strict/deep signature checks, nested-app inspection, exact sidecar build/hash/IPC validation, and two isolated six-second offscreen launch/TERM cycles. Physical CoreAudio input, roster, two-Mac, reconnect, recording, and Logic results remain **NOT RUN**. It is not Developer ID signed or notarized; v0.12.0, v0.11.0, and v0.10.0 remain rollback artifacts. |
| **Local Companion API** | ⚠️ Read-only localhost bridge, off by default and opt-in. See [COMPANION_API.md](COMPANION_API.md). |

In practice today: v0.13.0 takes a musician from two launch choices
through sound confirmation, any required Band Check, and into a hosted or joined
rehearsal without a server form. Jamulus client/server processes stay in the
background when a usable wired audio interface is attached. The exact package
still needs its separate two-Mac and Logic gates before those physical outcomes
can be credited.
The live window shows aggregate readiness, one private invite action, real band
members, and one More menu. The same-RFC1918-LAN flow is implemented; public
Internet, VPN, NAT traversal, and IPv6 are not claimed for the ordinary app.
The separate v3 work has a loopback-only reference profile for automated and
developer evidence; it does not create a public service or an “anywhere” claim.
The candidate itself checks storage and checkpoints redacted in-progress
recording evidence. The remaining boundary is physical: actual interfaces,
musician audibility, interruption recovery, a real roster, and Logic import
must be recorded in the two-Mac worksheet.

Before widening the closed pilot, validate the exact artifact on two Macs:
link launch, bidirectional physical audio, a named two-musician take, Studio
playback, aligned Logic export/import, reconnect, and owned-process cleanup.

---

## What's Planned

See [VISION_AND_ROADMAP.md](VISION_AND_ROADMAP.md) for the long-form vision. Near-term engineering phases:

1. **Certification gates** — two-Mac bidirectional audio/outage/originals and exact-package Logic import using the v0.13.0 candidate
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
- [v1 last-mile readiness record](docs/WEBJAM_V1_LAST_MILE_PLAN.md) — current trust, recovery, and physical-certification boundary
- [DEVELOPMENT.md](DEVELOPMENT.md) — dev environment setup
- [CHANGELOG.md](CHANGELOG.md) — release history
- [COMPANION_API.md](COMPANION_API.md) — localhost API for external tools
- [Remote transport ADR](docs/adr/0001-remote-session-transport.md) — v3 sidecar decision, proof boundary, and rejected alternatives
- [Remote threat model](docs/security/remote-session-threat-model.md) — v3 security contract and required evidence
- [Reference service](reference_service/README.md) — local/CI native rendezvous and exact-pair relay; no public deployment
- [WEBEX_AUDIO_MODES.md](WEBEX_AUDIO_MODES.md) — safe Jamulus music, Webex conversation, and audience-bridge signal flows
- [RECORDING_AND_LOGIC.md](RECORDING_AND_LOGIC.md) — take integrity, Studio mixing, per-Mac isolated originals, and the aligned Logic handoff
- [SUNDAY_TWO_MAC_PILOT.md](SUNDAY_TWO_MAC_PILOT.md) — exact-artifact two-Mac and Logic certification worksheet
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
