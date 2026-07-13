# WebJam Vision & Roadmap

**Goal: Make WebJam unlike any collaboration app before or after.**

Not "video call + shared doc." WebJam is the app that **knows we're making something together**: one room, one goal, one shared artifact (the canvas), with context that carries across sessions. Modes, templates, and review states are the spine; low-latency audio and video are the senses.

---

## North Star

> **"The app knows we're making something together."**

- One room = one goal + one canvas + one conversation (audio + video).
- The canvas is the source of truth for what we decided and what we're doing next.
- Sessions have shape (rituals, checkpoints, handoffs), not endless calls.
- Modes change how the app looks and behaves so each discipline gets the right tool.

---

## Themes & Features

### 1. Audio as the main object

| Idea | Description | Phase |
|------|-------------|-------|
| **Per-participant "room sound"** | Each participant can pick a small room reverb (e.g. garage, studio, hall) so the band feels in the same space. | 2 |
| **Shared click / metronome + visual pulse** | One source of truth for tempo and downbeat; subtle visual pulse in the UI so everyone stays in time even when video lags. | 2 |
| **Listening profiles (first-class)** | Save and name mixes ("Rehearsal", "Recording", "Focus on drums"); share or attach to modes/templates. | 2 |
| **Direct Jamulus control** | Control mixer programmatically (already on README roadmap). | 1 |
| **Effects per channel** | Reverb, compression, EQ per channel (README roadmap). | 3 |

### 2. Creative modes that change the app

| Idea | Description | Phase |
|------|-------------|-------|
| **Mode-specific layouts** | Music Jam: big mixer, minimal canvas. Writer's Room: big notes, small audio. Design Critique: references + timestamps. Same app, different instrument per mode. | 2 |
| **One-click session templates** | "Band rehearsal", "Feedback on a track", "Co-writing a script" – each sets goal, template, and suggested defaults. | 1 |
| **In-session rituals** | Guided steps: "Sound check" → "First run" → "Feedback round" → "Save and close", with optional timers and checkboxes. | 2 |
| **Rituals drive next session** | On reopen: "Continue from 'In review'" or "Start a new round" so the canvas drives what happens next. | 2 |

### 3. Session canvas as the shared artifact

| Idea | Description | Phase |
|------|-------------|-------|
| **Time-linked notes and references** | Pin a note or link to "what's happening now" (e.g. "from 12:34" or "during chorus"); support recap/replay when recording exists. | 2 |
| **Exportable session brief** | Local Markdown brief now ships from the Session Canvas with decisions, actions, blockers, questions, references, and raw notes. PDF, artifact embedding, and richer attendee detail remain future work. | 1/2 |
| **Review states drive next session** | Draft → In review → Approved; on next open, suggest "Continue from In review" or "Start new round." (Partially in place.) | 1 |
| **Offline-first notes** | Notes and artifacts save locally and sync when back online so bad connectivity doesn't lose the "minutes" of the session. | 2 |

### 4. Technical differentiators

| Idea | Description | Phase |
|------|-------------|-------|
| **Companion API (documented & stable)** | Document and stabilize the local bridge API so DAWs, editors, and other tools can read room state, mode, goal, mixer. WebJam as hub. | 1 |
| **Optional E2E encrypted canvas** | Mode where only people in the room can ever read notes/artifacts; no server-side plaintext. | 3 |
| **Recording + timeline** | Optional recording; timeline for time-linked notes and replay. | 3 |

### 5. Community & human layer

| Idea | Description | Phase |
|------|-------------|-------|
| **Cohort & mode analytics** | Privacy-respecting dashboards: which modes are used, where Host/Join sessions stall, which templates win. (Build on existing metrics.) | 2 |
| **Community room templates** | Users can publish/share templates; small gallery so best practices spread without building every workflow. | 3 |
| **Accessibility as differentiator** | Double down on high contrast, scalable UI, keyboard flow, screen-reader-friendly structure; say it clearly in positioning. | 1 |

### 6. Positioning & messaging

| Idea | Description | Phase |
|------|-------------|-------|
| **"We're making something together"** | Marketing and in-app copy that frames WebJam as the room with a goal and a shared artifact, not "another meeting app." | 1 |

---

## Delivery Status

### ✅ Implemented in source — v0.10.0 certification candidate (2026-07-13)

- **Five-second launch** — Host a Jam is the clear primary action; Join a Jam
  opens one invitation field. A new or changed setup proceeds through Band
  Check and **Start Session**; a matching stored check starts the bundled
  server/client directly.
- **Permanent Band Check** — required checks after Host/Join on a new or changed
  setup, plus F2, Settings, and live troubleshooting, share one guided input/
  headphone/scratch-recording/Studio path with typed Ready, Warning, and Action
  Needed outcomes. A separate footer action previews the support bundle.
- **Distinct visual identity** — near-black surfaces, white type, and official
  burnt orange (`#BF5700`) replace the previous purple/teal control palette.
  An original three-part mark represents conversation, live music, and
  production without reusing a third-party logo.
- **Meeting-style live hierarchy** — responsive musician tiles and one bottom
  control bar for Copy Invite, Record, More, and role-aware End Session or
  Leave Jam.
- **Lifecycle truth** — permission, connecting, interrupted, unavailable,
  offline, ending/leaving, recoverable, and fatal states have plain-language
  next actions. A running process is not treated as proof of connection.
- **Private local originals** — the host, or a guest connected through an active
  v2 private invite, can explicitly retain its first two interface inputs. That
  guest keeps recording through peer outage and resumes a size/SHA/PCM-verified
  same-LAN transfer without deleting its original. A v1 guest has no
  WebJam-orchestrated local capture or delivery.
- **Schema-v2 Studio and Logic handoff** — stable identities, segments, gaps,
  media truth, offset/drift evidence, mixed-rate playback, common-origin PCM24
  stems, references, reports, analysis, and checksums share one project model.
- **Privacy-safe support bundle** — preview and save use one immutable
  allowlisted artifact that excludes recordings, notes, transcripts, Webex
  content, private invites, secrets, and home paths by default. The diagnostics
  shortcut separately creates a short sanitized clipboard summary.
- **Accessibility and narrow-window support** — visible focus, task-ordered
  keyboard navigation, accessible descriptions/announcements, state meaning
  beyond color, and a 760×600 live-session floor.
- **Tonight's boundary** — private Apple Silicon two-Mac same-LAN candidate.
  Exact-source native one-hour longevity and fresh-package runtime pass.
  Physical audio/reconnect/guest-original delivery, Logic import, and observed
  cleanup remain explicit gates; no automated result fills those physical
  fields.

### ✅ Shipped — v0.8.0 bundled Jamulus (2026-07-08)

- **Bundled Jamulus** — downloadable builds ship Jamulus itself: macOS is zero-install (unmodified, notarized `Jamulus.app` nested in the bundle), Windows offers an in-wizard "Install Jamulus now" button that runs the bundled official installer. Removes the "leave WebJam, find jamulus.io, download, come back" step for most users; the manual Browse/`WEBJAM_JAMULUS_CANDIDATES` override remains for anyone who needs a different install. See `THIRD_PARTY_NOTICES.md`.

### Historical implementation checkpoint — v0.8.1

Everything below entered the source tree during the v0.8.1 release-candidate
work. It is retained as implementation history; current status is the v0.10.0
section above. v0.8.0 remains the latest published build at
[Releases](https://github.com/rupret007/webjam/releases) until all closed-pilot
gates pass.

- **Qt Conductor UI** — `webjam_qt_main.py` is the primary entry point; legacy Tkinter UI is quarantined under `legacy/`
- **Focused first run** — two-step Host/Join, identity, Webex, and optional
  capture. Conversation is the default; video-only and advanced audience-bridge
  roles remain in Settings.
- **Jamulus protocol layer** — `core/jamulus_rpc_client.py` (JSON-RPC) + `core/jamulus_protocol.py` (UDP binary adapter, CRC-16-CCITT, fader/mute commands)
- **Native Webex handoff** — opens the configured room externally and reports
  only launch truth; it never claims to inspect or control meeting membership,
  devices, mute, leave, or reconnect state
- **Two-lane conversation safety** — Jamulus remains the only music path and
  native Webex stays muted while playing. Jamulus 3.12.2 has no live-send mute
  API, so speaking requires an audio-interface mute or ending the WebJam
  session first
- **Role-aware readiness** — native Webex selections are manual `VERIFY` rows;
  `core/audio_routing.py` scans VB-CABLE / BlackHole / JACK / Loopback only for
  advanced audience-bridge mode
- **Independent local capture** — supplemental isolated input stems can be
  enabled in any Webex mode and retain recovery, alignment, and take validation
- **macOS all-in-one host** — a centered lobby can start/supervise the official
  JamulusServer.app 3.12.2 before the host client joins. Stop Audio leaves the
  server available; authenticated external servers are observed but not owned
- **Session canvas** — shared notes, local Session Pulse, Markdown brief export, artifact types, review states (Draft→In review→Approved)
- **Session repository** — `increment_setting`, mix profiles, audit log, room context persistence
- **Companion API** — localhost bridge (`api/local_bridge.py`) for DAW/editor integration
- **Three downloadable builds** — Windows x64, macOS ARM64, and macOS Intel x64
- **CI/CD** — Ruff, UX smoke, full pytest, real Jamulus integration, and
  PyInstaller artifacts on every push; tag pushes additionally create drafts
- **Accessibility** — `setAccessibleName` on all major panels, keyboard shortcuts, focus rings in QSS

### 🔜 Next — closed pilot gates

- Native Jamulus/JACK 3,600-second certification plus exact v0.10.0 artifact
  startup/resource/signature/runtime inspection and the planned
  two-Apple-Silicon-Mac same-LAN physical pilot.
- Host/link/paste/deep-link paths, real bidirectional audio, one server track
  per musician, Studio stereo playback, aligned Logic-package import,
  reconnection truth, guest-original outage delivery, role-aware End/Leave,
  and clean relaunch.
- Developer ID macOS signing/notarization and Windows signing before broad
  distribution; the private candidate remains ad-hoc signed.
- Architecture split after pilot-readiness fixes: audio/session, video, recording, settings, and companion API coordinators.

### Phase 2 – Differentiation (mid term)

- Mode-specific layouts (UI changes per mode — Music Jam vs Writer's Room vs Design Critique).
- Per-participant room sound; shared click/metronome + visual pulse.
- In-session rituals (sound check, first run, feedback round, save and close).
- Time-linked notes and references; exportable session brief (PDF/doc).
- Offline-first session notes (local-first sync).
- Cohort & mode analytics (dashboards from existing metrics).

### Phase 3 – Ecosystem (longer term)

- Effects per channel (reverb, compression, EQ).
- Optional E2E encrypted canvas.
- Recording + timeline; replay and recap.
- Community room templates gallery.

---

## How to use this doc

- **Product / prioritization:** Use themes and phases to decide what to build next.
- **Implementation:** Each row can become an issue or spec; Phase 1 items are the first batch.
- **Consistency:** New features should align with the North Star: one room, one goal, one canvas, sessions with shape.

---

## Related docs

- `CREATIVE_MODES_MVP_SPEC.md` – Mode metadata, canvas, room context (already implemented).
- `COHORT_VALIDATION_PLAYBOOK.md` – Pilot and validation.
- `WEBEX_AUDIO_MODES.md` – Canonical music/conversation signal flows and safety rules.
- `README.md` – User-facing roadmap and quick start.

*WebJam – The app that knows we're making something together.*
