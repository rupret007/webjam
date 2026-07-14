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
| **Multitrack Studio review workspace** | Shipped in the private v0.14.0 candidate: a shared seconds-only track ruler, truthful source/alignment/gap inspection, compact controls, durable non-destructive mix state, and a safer Logic handoff. It is not a tempo grid or a full DAW claim. | 1 |
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

### ✅ Packaged in the private v0.14.0 candidate (2026-07-14)

The exact v0.14.0 test-night ZIP is
`WebJam-v0.14.0-TEST-NIGHT-macos-arm64.zip`, SHA-256
`cbcbdc038ac3d663e15870990ae5fea2a09819cdd55adbaa7463a64405ef8321`, built
from `045c5acb01687a4088b0bd618dab4d0ab6200804`.

Its package-only evidence is complete: **1,719 passed, 18 skipped, one known
warning, and 6 subtests**; transport `go test ./...` and `go vet ./...`;
fresh-extraction strict/deep signature, nested-app, and exact fabric-ID checks;
and two isolated six-second offscreen launch/TERM cycles. This is not a claim
that physical two-Mac audio, CoreAudio routing, recording/recovery, or Logic
Pro import has run; all of those results are **NOT RUN**.

- **Five-second launch and Band Check** — Host a Jam is the clear primary
  action; Join a Jam opens one invitation field. One concise name-and-band-sound
  confirmation leads into the guided Band Check and **Start Session** path.
- **Distinct visual identity** — near-black surfaces, white type, and burnt
  orange (`#BF5700`) replace the former purple/teal palette. An original
  three-part mark represents conversation, live music, and production without
  reusing a third-party logo.
- **Studio review workspace** — every completed lane shares an elapsed-seconds
  ruler and seek point; no bars, beats, tempo, automation, or full-DAW claim is
  invented. Selecting a track exposes its source, media/alignment evidence,
  recorded gaps, and export inclusion. Compact controls remain usable at
  760×600.
- **Safe Studio continuity** — gain, pan, mute, solo, and export inclusion live
  in an atomic private `.webjam-studio-state.json` sidecar keyed by schema-v2
  `track_id`. It never rewrites the take manifest or source WAVs, and a
  reordered/reconciled lane cannot inherit another musician's export choice.
- **Durable local recovery and Logic truth** — interrupted media remains a
  visible **Needs Attention** recovery project. Explicitly silent or unaligned
  selected tracks block misleading Logic export until the musician makes the
  safe choice; source media stays unchanged.
- **Private local originals and lifecycle truth** — supported v2 guests can
  retain local originals through a peer outage and resume a verified same-LAN
  transfer. A v1 guest has no false local-capture claim; a running process is
  never presented as proof of connection.
- **Privacy, accessibility, and narrow-window support** — support output is
  allowlisted/redacted; focus and keyboard order are explicit; state meaning is
  not color-only; and the live-session floor is 760×600.

The v0.13.0 ZIP is preserved rollback history, not evidence that the v0.14.0
Studio workspace existed in that earlier artifact. The v0.12.0 ZIP remains an
older historical rollback artifact.

### ✅ Shipped — v0.8.0 bundled Jamulus (2026-07-08)

- **Bundled Jamulus** — downloadable macOS builds nest the official Jamulus
  release app after preparing and re-signing it ad hoc for WebJam's
  loopback-only orchestration. It is not a notarized nested app or a claim that
  the private WebJam artifact is Developer ID signed. Windows carries the
  official installer as a distribution dependency. This removes the "leave
  WebJam, find jamulus.io, download, come back" step for the supported package;
  the manual Browse/`WEBJAM_JAMULUS_CANDIDATES` override remains for anyone who
  needs a different install. See `THIRD_PARTY_NOTICES.md`.

### Historical implementation checkpoint — v0.8.1

Everything below entered the source tree during the v0.8.1 release-candidate
work. It is retained as implementation history; current status is the v0.14.0
package, with v0.13.0 and v0.12.0 retained as historical rollback artifacts.
v0.8.0
remains the latest published build at
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

- Complete the planned two-Apple-Silicon-Mac
  same-LAN physical pilot. The v1/v2 engine's native Jamulus/JACK 3,600-second
  evidence remains historical engine evidence, not package certification.
- Physically verify the exact v0.14.0 candidate's seconds-only ruler, seek
  alignment, selected-track inspection, compact layout, immutable-sidecar
  reopen behavior, and durable-ID Logic selection. Those observations are
  **NOT RUN** until recorded against that exact package.
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
