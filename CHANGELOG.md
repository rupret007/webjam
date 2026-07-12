# WebJam Changelog

All notable improvements and features for the WebJam music collaboration platform.

---

## [0.8.1] — Release candidate

### All-in-one hosting and the lobby redesign

- **WebJam now hosts the band server.** Enable "This Mac hosts the band
  server" in Setup and Start Audio verifies JamulusServer.app 3.12.2, checks
  ports, provisions the protected recorder secret and recordings folder in
  the server app's container, starts the server under a caffeinate sleep
  assertion, waits for its control port, and then connects the client. A
  crashed server is restarted automatically even when client auto-reconnect
  is disabled. An externally started server is adopted only after the
  configured secret authenticates and its recorder API responds; it is shown
  as `Server: External` and never terminated by WebJam. Stop Audio leaves the
  server running for the band. Quitting stops/finalizes only a server WebJam
  owns. The host client target is always loopback, unsupported platforms hide
  hosting, startup timeout cleans up owned processes, and Ready Check verifies
  the exact dedicated-server version.
- **The disconnected workspace is a real lobby.** The session card is
  centered on the stage with a display-size title, one obvious primary
  action (Start Audio, or Host & Start Audio when hosting), Practice Solo
  and Ready Check beside it, and a quiet hint naming the server audio will
  join. The Webex launch card is a slim bar, and the redundant STAGE header
  is gone.
- The in-app lifecycle was exercised against the installed official
  JamulusServer.app 3.12.2 on Apple Silicon: UDP 22124 and loopback RPC 22240
  opened, the secret remained mode 0600, recorder status authenticated, Record
  armed/stopped successfully, and shutdown left no server/caffeinate orphan.
- Final audit validation passes 985 tests with 12 expected skips and 6
  subtests, all 11 official-binary integration tests, Ruff, compile,
  dependency/vulnerability checks, UX smoke, and the 20-document link/security
  review. A clean ARM64 v0.8.1 bundle passes metadata/resource inspection,
  offscreen startup, TERM shutdown, and orphan-process checks.

### Two-lane Webex talkback

- WebJam now treats Jamulus music and native Webex speech as separate audio
  lanes. New configurations default to **Musician with talkback**; **Video
  only** and the advanced, mutually exclusive **Audience broadcast bridge**
  remain explicit alternatives.
- **Open Webex** now has a truthful external-launch lifecycle: Not opened,
  Opening, Opened externally, or Open failed. WebJam never claims meeting
  membership and does not inspect, mute, leave, or reconnect native Webex.
- In talkback mode, the self-mute control is **Talk Break**. It opens the speech
  lane only after Jamulus acknowledges that transmit is muted. **Resume Music**
  defaults to cancel until the musician confirms Webex is muted; RPC failure
  leaves the safer lane muted. Reconnect reapplies an active Talk Break.
- Transmit-mute state fails closed everywhere: Stop Audio clears Talk Break
  entirely (a relaunched Jamulus client always starts unmuted, so stale TALK
  could otherwise hide a live send), a crash-reconnect clears any confirmed
  mute in every Webex role until the new session re-acknowledges it, and the
  reconnect-failure banner now says exactly what to do — press Talk Break to
  retry. The Talk Break tooltip names its Ctrl+Shift+M shortcut and the
  launch card reads correctly to screen readers.
- Setup presents three accessible audio-role cards. Ready Check automates only
  what WebJam can observe and labels native Webex device, mute, Mic Mode, and
  Smart Audio confirmations as `VERIFY`; those confirmations reset on rerun.
  BlackHole/VB-CABLE is scanned only for an audience bridge, never for normal
  talkback or video-only use.
- Supplemental local stem capture is now controlled independently by
  `local_capture_enabled`. Its **Meter and local recording input**, 48 kHz
  support, writable takes folder, recovery, and validation do not depend on the
  selected Webex role.
- Existing settings migrate without losing intent: legacy bridge-on becomes an
  audience bridge with local capture enabled; bridge-off becomes video only;
  brand-new profiles use talkback. The old environment variable remains a
  one-release compatibility fallback behind `WEBJAM_WEBEX_AUDIO_MODE`.
- Setup no longer collects or saves legacy Guest Issuer credentials. Webex
  URLs reject user information and non-default ports, while logs and
  diagnostics retain at most a trusted Webex hostname and redact meeting
  paths, queries, and fragments.
- Added [`WEBEX_AUDIO_MODES.md`](WEBEX_AUDIO_MODES.md) as the canonical signal-
  flow and fail-safe guide. The closed pilot uses native Webex push-to-talk;
  Browser SDK/OAuth automation remains deferred.

### Session Pulse and brief export

- The Qt Session Canvas now presents a local, deterministic Session Pulse from
  the active mode, title, confirmed participants, and captured notes. It
  surfaces decisions, owned actions, blockers, questions, references, and the
  next checkpoint without sending session content to a network service.
- **Export… → Session brief…** exports a Markdown handoff through the existing atomic-write
  path. The brief includes the structured pulse and the raw notes, so no
  session context is discarded.
- Pulse content is rendered as plain text; preview cards are never presented
  as real attendees, and a brief is rebuilt immediately before export.
- A single responsive Export menu replaces the clipping notes/brief button
  pair at the supported 280 px canvas width. Cancellation and write failures
  are covered, and a failed Pulse refresh discards stale derived data while
  preserving raw notes.
- Setup now distinguishes the local Jamulus client RPC port (22222) from the
  band recorder RPC port (22240), and routing copy no longer claims that
  detecting BlackHole/VB-CABLE configures Jamulus or Webex.
- Release CI verifies the packaged executable, UI resources, version, and
  bundled Jamulus payload on every platform. Tagged builds create a draft
  GitHub release until physical pilot gates pass.
- Corrected the native macOS recording-server path after physical validation:
  use the official dedicated `JamulusServer.app`, with its secret and takes in
  real sandbox container storage rather than `~/Music`. The checked-in pilot
  launcher verifies version/ports and keeps recorder RPC loopback-only. Record
  setup and connection errors now distinguish this same-Mac path from an SSH
  tunnel to a remote Linux server.
- Recording now has explicit starting/recording/stopping/error states, elapsed
  time, duplicate-click protection, and post-stop verification of the new
  take's expected tracks, readability, duration, sample rate, and sampled
  signal. Completion can open Take Deck or reveal the take in Finder.
- Take Deck adds persistent output-device selection, actionable device errors,
  take-health warnings, Finder reveal, and stereo headphone playback instead
  of sending the rough mono mix only to output channel 1.
- Ready Check is non-blocking and rerunnable. Setup distinguishes talkback,
  video-only, and audience-bridge roles, so BlackHole/VB-CABLE is required only
  for the advanced program-feed path. Dead Chat/Roles navigation and the
  duplicate Stage/Mixer distinction were removed; Ready and Practice now live
  in the accessible Checks menu.

### Recording integrity hardening

- Stem alignment offsets are now signed end-to-end. Local capture arms before
  the server recorder starts, so isolated stems normally *lead* the server
  take; the previous clamp forced those negative offsets to zero and every
  supplemental stem played late by the recorder-start latency. Take Deck now
  plays negative-offset stems sample-aligned and labels the trimmed lead-in.
- Alignment correlation uses an alias-free 100 Hz block-mean envelope plus a
  bounded full-rate refinement pass, replacing raw stride decimation. The
  reported confidence is the refined normalized correlation (≈1.0 for a
  genuine match, ≈0 for unrelated audio), making the 0.15 acceptance floor
  meaningful. Manifests record `alignment_method: envelope+refine-v2`.
- The supplemental-capture audio callback is real-time safe: it only copies
  blocks into a bounded queue and a dedicated writer thread does all disk
  writes. Device status flags and write errors are deduplicated, counted, and
  capped, so a sustained fault can no longer grow an unbounded error list into
  the manifest.
- Partial recordings are always preserved: a failed stem attach moves the
  audio to a visible `Recovered-local-…` folder instead of deleting it,
  attaching never overwrites an existing take file (collisions get a
  `-local` suffix), and quitting mid-recording salvages the capture into a
  `Recovered-…` take instead of discarding it. Capture hand-off between the
  validation worker, stop-failure handling, and shutdown is now atomic and
  idempotent.
- Take Deck reuses recorded manifest findings when reviewing a finished take
  instead of re-probing every WAV, and shows transient `validating` manifests
  as "Checking…" rather than "Unchecked".
- Stopping audio while the band server is recording now warns that the server
  keeps recording and points at ■ Stop Rec first.
- Participant names and roles from the Jamulus roster render as plain text,
  so markup in a remote musician's name can no longer be interpreted as rich
  text in the mixer.
- Reconnect guidance and README_SIMPLE now name the real controls
  ("Start Audio", "Checks ▾") instead of stale labels.

---

## [0.8.0] — 2026-07-08

### Bundle Jamulus with downloadable builds (both platforms)

Removes the "leave WebJam, find jamulus.io, download, install, come back"
detour for most users. Both platforms bundle the same pinned Jamulus
version (`3.12.2` / tag `r3_12_2`) already used by the `integration-jamulus`
CI job, unmodified, under GPL/AGPL "mere aggregation" terms — see the new
`THIRD_PARTY_NOTICES.md` for the full licensing rationale.

- **macOS: zero-install.** CI downloads and checksum-verifies the official,
  Apple-signed and notarized `jamulus_3.12.2_mac.dmg`, extracts the
  unmodified `Jamulus.app`, and nests it (via `ditto`, never re-signed) into
  `WebJam.app/Contents/Resources/Jamulus.app`. A fresh install finds it
  automatically with zero configuration.
- **Windows: bundled installer.** Jamulus only ships an NSIS installer on
  Windows (no portable binary), so CI downloads and checksum-verifies
  `jamulus_3.12.2_win.exe` and `webjam.spec`'s new `Jamulus/` datas block
  (mirroring the existing `VB/` block) ships it inside the WebJam install
  directory. The Setup Wizard's Jamulus page now shows an **"Install
  Jamulus now"** button when no install is found — it launches the bundled
  installer and polls (non-blocking, via `QTimer`) for completion, filling
  in the executable path automatically once it lands.
- Added `services.bridge_service._bundled_jamulus_candidate()` (macOS) and
  `_bundled_jamulus_installer()` (Windows) — both frozen-build-aware and
  no-ops in dev checkouts. `find_jamulus()` now falls back to the bundled
  macOS candidate as a last resort after all configured/default candidates
  are exhausted.
- The manual override (Browse button, `WEBJAM_JAMULUS_CANDIDATES` env var)
  is unchanged and remains the escape hatch for anyone who needs a
  different Jamulus install than the bundled one.
- Added `licenses/JAMULUS_COPYING.txt` (the exact GPL text from the pinned
  Jamulus release tag) and `THIRD_PARTY_NOTICES.md`; CI places a copy
  alongside the bundled Jamulus in every build (macOS:
  `WebJam.app/Contents/Resources/THIRD_PARTY_LICENSES/`; Windows:
  `Jamulus/` next to the installer).
- Updated the Setup Wizard's Welcome-page notice (no longer an "install
  this yourself" warning) and the Jamulus page (pre-fills + notes the
  bundled macOS copy; shows the install button on Windows).
- Updated README, README_SIMPLE, DEVELOPMENT, ARCHITECTURE, USER_GUIDE,
  FIRST_JAM, COHORT_VALIDATION_PLAYBOOK, TEST_PROCEDURE, and
  VISION_AND_ROADMAP to reflect per-platform bundling instead of a blanket
  "install Jamulus separately" requirement (still true for source
  checkouts, which don't go through the PyInstaller bundling step).
- Added `TestBundledJamulusCandidate`, `TestBundledJamulusInstaller`, and
  `TestJamulusPageBundling` test suites (28 + new wizard cases) covering
  frozen/non-frozen and platform-gating branches, the `find_jamulus()`
  fallback, and the install-button launch/poll/failure paths. Full suite:
  823 tests passing (0 regressions).
- Known trade-off, not blocking: bundling ties the shipped Jamulus version
  to WebJam's own release cadence — see `THIRD_PARTY_NOTICES.md`'s
  "Staying current" note.

---

## [0.7.3] — 2026-07-08

### Test isolation fix and doc cleanup

- Fixed a test-isolation bug in
  `tests/test_application_controller_demo_to_real_transition.py`: the
  audio "stopping" latch set by `AudioCoordinator.stop()` wasn't reset in
  `setUp()`, so a prior test's stop() could leak into the next test and
  make `apply_participants()` silently no-op.
- Fixed `DEVELOPMENT.md`'s "Adding a Jamulus JSON-RPC method call"
  tutorial, which still described the pre-rewrite RPC client (separate
  poll/SSE threads, a synchronous `_call()` helper) and referenced a
  nonexistent `GAIN_RANGE_MAX` attribute in its example code. Rewritten
  to match the current single-thread NDJSON reader and fire-and-forget
  `_send()`.

---

## [0.7.2] — 2026-07-06

### Pilot readiness hardening

- Added a session-health snapshot so the Conductor distinguishes a launched
  Jamulus process from proven RPC/participant/meter truth.
- Made Ready Check visible in the session strip and run it automatically after
  first-run setup completes.
- Hardened first-run setup: Jamulus executable presence is required, Webex
  links must be HTTPS `webex.com`, and setup completion copy no longer implies
  the rig is jam-ready before Ready Check passes.
- Made `Mute Me` truthful: it only changes local UI state after Jamulus RPC
  accepts `setMuted`, and reverts on failure.
- Tightened recorder status parsing, Webex permissions/token injection, log and
  diagnostics redaction, Companion API opt-in behavior, and Jamulus RPC secret
  fail-closed launch.
- CI desktop builds now wait for the real-Jamulus integration job.
- Restored an Intel Mac release artifact (`WebJam-macos-x64.zip`) using
  GitHub's current `macos-15-intel` hosted runner.

---

## [0.7.1] — 2026-07-05

### Deep code + logic review — hardening pass

A four-reviewer deep audit of the audio engine, RPC layer, and controller
state machines. Confirmed the security model is sound (0o600 secrets, no
command injection, loopback-only RPC + SSH tunnel, Host-header guard). Fixes:

- **Take Deck plays at the take's real samplerate.** A 44.1 kHz take no
  longer plays pitch-shifted / misaligned through a fixed 48 kHz device.
  Replaying a finished take rewinds instead of sitting silent, and finishing
  a take now releases the audio stream + file handles.
- **RPC framing is stall-proof.** Both the Record-button transport and the
  live client now frame NDJSON from raw sockets, so a response split across a
  network stall no longer hard-fails a call or drops notifications.
- **No zombie RPC reader** after a fast Stop Audio → Launch Audio; sends are
  serialised; channel meters map by channel id, not list position.
- **Record button polls** the server recorder until it actually arms/disarms
  (Jamulus does it asynchronously), and resets on Stop Audio.
- **Reconnect** shows a clear "couldn't reconnect after 5 tries" instead of
  hanging on "Reconnecting…" forever.
- **Practice mode** cleans up its private server if the client launch fails,
  and never freezes the UI during teardown.
- Webex button can't get stuck lying "Leave Video"; shutdown is re-entrant;
  companion-API reads are race-safe; diagnostics redaction is future-proofed.
- 12 regression tests added; suite at 754.

---

## [0.7.0] — 2026-07-05

### The Take Deck — play back and mix your jams, in-app

- **Take Deck (side-rail "Takes")** — the recordings the ● Record button
  captures are now reviewable *inside WebJam*: pick a take, hit play, and
  mix it with the very same console the live session uses (per-track
  faders, mute, solo, live meters, scrub). Musicians who connect mid-jam
  line up correctly — track start offsets are read from the take's
  Audacity `.lof`. This is the first half of the "Demo Deck": review now,
  overdub next.
- **Multitrack playback engine** (`core/take_player.py`) — streaming
  per-track mixing on a numpy bus with gain/mute/solo/offsets and a
  transport, behind a sink abstraction so the whole engine is unit-tested
  headless (no audio hardware in CI).
- **Take library** (`core/take_library.py`) — discovers take folders and
  parses `.lof` offsets; robust to missing/garbled metadata.
- **Review-only, on purpose** — no editing/plugins here; every take keeps
  its Reaper-project escape hatch for the DAW.
- New dependency: `soundfile`. New setting: `takes_directory`. Suite +34.

---

## [0.6.0] — 2026-07-05

### The Record Button

- **● Record in the Conductor** — one press arms the band server's
  multitrack recorder; one press stops it. Every musician gets their own
  track and every take lands as a ready-to-open Reaper project on the
  server. The whole band sees the red ● REC chip while tape rolls.
- **Band-server RPC transport** (`core/jamulus_server_rpc.py`) — reaches
  the server's loopback-only JSON-RPC through an SSH tunnel; new settings
  `server_rpc_port` (default 22240) and `server_rpc_secret_file` (a local
  copy of the server's jsonrpc.secret). Unconfigured? The button tells you
  exactly how to set it up.
- **Machine-verified against real Jamulus** — the Record cycle (arm →
  new-take → stop), roster query, and wrong-secret rejection all run
  against the shipping jamulus-headless binary in CI on every push.
- Suite at 719 (+16 unit, +3 real-binary integration).

---

## [0.5.0] — 2026-07-04

### The "make it amazing" release — practice mode, recording awareness, band server

- **Practice mode (Ctrl+P / Practice button)** — WebJam starts a private
  Jamulus server on your own machine and connects to it: hear yourself,
  watch your meter, test the mixer — zero internet, zero band-server
  dependency. Works on a fresh unconfigured install. Stop Audio tears the
  local server down with the client.
- **● REC indicator** — when the band server's multitrack recorder is
  rolling, every member sees a red ● REC chip in the status bar (wired to
  Jamulus `recorderState` notifications).
- **Stage cards v2** — cards now show each musician's skill level from
  their Jamulus profile alongside the instrument ("Bass · Intermediate").
- **Band server recipe (`server/`)** — one `docker compose up -d` gives the
  band a private server with multitrack recording armed: every take is one
  WAV per musician plus a ready-to-open Reaper project. JSON-RPC stays on
  loopback (SSH-tunnel only) — the foundation for the upcoming Record
  button.
- **Vision** — see WEBJAM_VISION.md for the roadmap this release starts
  (Session Record concept, server browser, Webex intelligence).

- **Fresh installs start unconfigured** — the dead default Jamulus server
  (a private LAN IP) and sandbox Webex link are gone. The wizard requires
  real values; Launch Audio without a server now shows an actionable error
  instead of spawning `Jamulus --connect :22124`; the empty default no
  longer crashes the app at startup.
- **FIRST_JAM.md** — staged runbook for the band's first session (solo
  smoke test → two-person → full band) with a failure playbook.
- **Download & security-warning docs** — README_SIMPLE now covers grabbing
  release zips and getting past Gatekeeper/SmartScreen (builds are unsigned).
- **Legacy Tkinter app quarantined** — `webjam_app*.py`, the Tkinter `ui/`
  modules, `admin/`, `session_templates`, old installer scripts, and their
  tests moved to `legacy/` (see `legacy/README.md`). CI no longer needs
  tkinter to collect the active suite; `ui/services.py` (live MetricsService)
  stays. Active suite: 674 tests, zero collection errors.

---

## [0.4.10] — 2026-07-04

### First shippable v0.4.x build — release pipeline unblocked

- **CI: release pipeline fixed** — every tag run since v0.4.5 was killed at
  the 24h wall because the build matrix still listed `macos-13` (Intel), a
  runner type GitHub has retired; the release job never fired. The Intel
  entry is removed (Intel Macs: run from source) and jobs now carry real
  timeouts. This is the first v0.4.x tag whose build can actually publish.
- **Fix: routing-scan shutdown race** — the background audio-routing scan no
  longer dies with a `RuntimeError` traceback if the app shuts down while the
  scan is in flight (the status is quietly dropped instead).
- **Tests** — live-session engine coverage push: `application_controller`
  69%→86%, `jamulus_controller` 63%→88%, `bridge_service` →91%. New suites for
  the Join/Leave Video flow, Webex state machine, token refresh, Launch/Stop
  Audio toggle, crash-reconnect banner, settings wizard round-trip,
  diagnostics export, JamulusController lifecycle, and BridgeService launch
  failure paths + Jamulus command-line contract. Suite at 720.

---

## [0.4.9] — 2026-06-29

### Live-session features + build correctness

- **In-session chat both ways** — a chat box in the session canvas sends to the
  band (`jamulusclient/sendChatText`) and echoes locally; incoming chat appends
  to the shared canvas.
- **Name sync** — on connect, WebJam pushes your display name to Jamulus
  (`jamulusclient/setName`) so bandmates see a real name, not a blank.
- **Ready Check (F2)** — `core/preflight.py` reports what's missing before you
  jam (Jamulus installed, server/port set, virtual audio cable detected, Webex
  link), surfaced via an F2 shortcut + F1 help.
- **Build correctness** — macOS bundle version now tracks `__version__` (was
  pinned to 0.3.0); Windows builds bundle the VB-CABLE installers; added
  `api.local_bridge` / `core.file_io` to PyInstaller hiddenimports.
- **Tests** — suite at 620 (fake-Jamulus TCP server, preflight, chat send,
  build data-file guards). `__version__` → 0.4.9.

---

## [0.4.8] — 2026-06-29

### Real-world hardening, correct Jamulus control, and onboarding

The headline: WebJam's Jamulus control was rebuilt against the **actual** current
Jamulus JSON-RPC API, plus a multi-round audit fixed real bugs and the CI/release
pipeline. First release intended for live band use.

#### Jamulus integration (correctness)
- **Rebuilt the JSON-RPC client against shipping Jamulus (3.9–3.12).** The old
  client spoke an experimental HTTP+SSE fork (`jamulus/getChannelClients`,
  gain 0–10000) that never matched released Jamulus. It now uses
  newline-delimited JSON-RPC over **TCP**, the `jamulus/apiAuth` handshake
  (`--jsonrpcsecretfile`, generated at launch), and the real `jamulusclient/*`
  methods (`getClientList`, `setFaderLevel` 0–100, `setMuted`) and notifications
  (`clientListReceived`, `channelLevelListReceived` 0–9, `connected`/`disconnected`).
- **Real "Mute Me"** via `jamulusclient/setMuted` — previously it zeroed your own
  fader, which only muted you in your *own* monitor; the band still heard you.
- **In-session chat** — incoming Jamulus chat (`chatTextReceived`) is appended to
  the shared session canvas; `sendChatText` is wired.

#### Reliability / security fixes (from the audit rounds)
- RPC heartbeat no longer false-fires "Jamulus stopped responding" after a restart.
- Mix auto-save safety net no longer disarmed by a failed save.
- Background audio-routing scan no longer dies silently when PortAudio is missing.
- Companion API: added a loopback-only `Host`-header check (DNS-rebinding defense),
  redacted `sentry_dsn`, and **actually wired it into the app** (it was documented
  as auto-starting but never instantiated).
- Python 3.10 compatibility fix; unknown-msg-id log-flood cap; assorted Lows.

#### Pipeline / docs
- **CI no longer cancels branch/tag runs**, so `master` can go green and produce builds.
- **`README_SIMPLE.md` rewritten** as an accurate band onboarding guide for the Qt app.
- **`WEBJAM_NEXT_LEVEL.md`** added: engine evaluation (stay on Jamulus; SonoBus/JackTrip considered) + roadmap.

#### Tests
- Suite expanded to **600+** (incl. a fake-Jamulus TCP server verifying the real
  wire protocol). `__version__` → 0.4.8.

---

## [0.4.7] — 2026-04-24

### Round 4 deep-dive — controller refactor, telemetry expansion, multi-mix, audio device picker

6 parallel implementation agents in isolated worktrees, plus follow-up wiring and a user-journey audit.

#### Refactor
- **`ParticipantStateManager` extracted** from `JamulusController` (new `jamulus_state_manager.py`, 349 LOC).  Owns `participants`, `_pre_solo_mute`, and `_participants_lock` plus all mutator helpers (`set_fader_level`, `set_mute`, `set_solo`, `serialize_mix`, `apply_mix_data`, `sync_from_protocol`).  `JamulusController` shrinks 803 → 545 LOC and now delegates; backward-compat properties on the controller keep older test fixtures working.
- **`unregister_callback()`** added to `JamulusController`; `stop()` warns if monitor thread didn't exit, then clears the callbacks list to drop dangling references.

#### New features
- **Multi-mix save/load** — `Ctrl+Shift+S` ("Save Mix As…") and `Ctrl+Shift+O` ("Load Mix From…") open `QFileDialog`s so users can keep one mix per song / per band-mate.  New `MixManager.save_to(path)` / `load_from(path)` paired methods.
- **Audio input device picker** in the wizard's Routing page (`AppSettings.audio_input_device_index`).  `core/audio_engine.py::_resolve_device` now prefers an explicit setting over auto-detect, so users with multiple interfaces can pin the right one.

#### Telemetry expansion (7 new metrics)
- `metric_jamulus_hang_detected` — incremented when the RPC heartbeat first crosses the >15s silence threshold.
- `metric_webex_token_refresh_attempt` / `_success` — wired through `WebexEmbed.on_refresh_metric` callback.
- `metric_audio_device_blackhole_found` / `_audio_device_missing` — emitted from the routing-status apply path so we know how often the bundled BlackHole route succeeds.
- `metric_mix_corruption_recovered` — incremented on `JSONDecodeError` in `MixManager.load`.
- `metric_session_started` — first-time-this-session participant arrival, paired with a "Connected to {server}. Waiting for band members…" flash.

#### Memory + concurrency hardening
- **`_unknown_msg_ids_seen` capped** at 256 entries in `core/jamulus_protocol.py` so unknown-message logging can't grow without bound on a misconfigured server.
- **`_request_counter` reset** in `JamulusRpcClient.stop()` — prevents wraparound state leaking across reconnects.
- **47 new tests** across 7 files covering the state-manager extraction, multi-mix round-trip, telemetry expansion, audio device picker validation, and concurrency stress (RPC client + JamulusController under daemon-thread Barrier/Event harness).

#### User-journey polish
- **Jamulus install warning relocated** from the Done page (page 4) to the Welcome page (page 1) of the setup wizard, with an amber notice box — users now discover the prerequisite before configuring anything.

#### Versioning
- `__version__` 0.4.6 → 0.4.7.  Suite total: **647 pass, 12 skipped** (was 611; +36 net; 0 failures).

---

## [0.4.6] — 2026-04-25

### Round 3 deep-dive — refactors, new shortcuts, audit fixes

10 parallel agents (6 implementation, 4 investigative) plus follow-up fixes.

#### New features
- **Ctrl+Shift+R — Reset all faders to 0 dB** (`application_controller.py::_on_reset_all_faders`).  Confirmation dialog; saved mix on disk untouched (Ctrl+O still restores).
- **Ctrl+Shift+D — Copy diagnostics summary** (`webjam_qt/controllers/diagnostics.py`).  New 129-LOC `DiagnosticsExporter` builds a Markdown summary (versions, service state, server config, log paths, last 30 lines of `~/.webjam.log`, sanitised settings — `webex_guest_issuer_secret` redacted) and pastes to clipboard.
- **Auto-save mix on shutdown** when the user touched the mix and Jamulus was connected. `_mix_dirty` flag flips True on any fader/mute/solo change, False after explicit save. Shutdown auto-saves so mid-session tweaks survive even if the user forgets Ctrl+S.

#### Wizard polish
- **Live validation hints** in the Jamulus and Webex pages.  Type-as-you-go feedback ("Host shouldn't contain spaces", "Will auto-prepend https://", "URL needs a domain"), no Next-button bouncing.

#### Refactor
- **`MixManager` extracted** from `ApplicationController` (`webjam_qt/controllers/mix_manager.py`, 124 LOC).  Owns `~/.webjam_mix.json` save/load/auto-restore. `_on_save_mix`/`_on_load_mix`/`_restore_saved_mix` retained as thin delegates.

#### State machine + correctness
- **`JamulusState` str-enum** in `services/bridge_service.py` (8 raw string assignments converted).  `_set_jamulus_state` writes under `_reconnect_lock`; `jamulus_process` writes likewise locked.  Inheritance from `str` keeps existing equality checks working transparently.
- **Memory leak: signal disconnect** in `ParticipantGrid._remove_card`.  Without this, `card.fader_changed.connect(self.fader_changed)` connections from `_add_card` survived `deleteLater()` and accumulated over join/leave churn.
- **Missing METRIC_KEYS added** (`ui/services.py`): `metric_jamulus_stop`, `metric_jamulus_port_conflict`, `metric_webex_leave`, `metric_session_completed` were incremented in code but absent from the canonical list.

#### macOS shortcut consistency
- **Ctrl+Shift+R / Ctrl+Shift+D bind to literal Control on macOS** (Qt.MetaModifier), matching the existing macOS-safe pattern used for Ctrl+M / Ctrl+Shift+M.  Avoids any potential Cmd+key system conflicts.

#### Tests
- **46 new tests** across 11 new files — port conflict detection, log capture, UDP protocol robustness, RPC hang banner, atomic notes export, MixManager round-trip, mix-dirty auto-save, diagnostics summary, wizard live validation.
- Suite total: **611 pass, 12 skipped** (was 565; +46 net; 0 failures).

#### Versioning
- `__version__` 0.4.5 → 0.4.6, surfaced in title bar and F1 help.

---

## [0.4.5] — 2026-04-25

### Deep-dive pass — data integrity, accessibility, performance, robustness

Synthesised from 17 parallel investigative + implementation agents across
two rounds covering architecture, performance, tests, real-world failures,
accessibility, integrations, persistence, docs, state machines, network
protocol robustness, cross-platform pitfalls, and error UX.

#### Data integrity
- **Atomic writes** for all persistent JSON/text via new `core/file_io.py::atomic_write_text` (temp file + fsync + `os.replace`).  Five call sites converted: setup wizard config, mix file, session notes, session metadata, canvas notes export.  8 new tests in `tests/test_file_io.py`.
- **Config file mode `0o600`** for `~/.webjam_config.json` (which can hold the `webex_guest_issuer_secret`).  Was world-readable.

#### Reliability + leak fixes
- **Subprocess log file leak fixed** in `bridge_service.launch_jamulus` — new `_close_jamulus_log_file()` helper called on shutdown-mid-launch and exception paths; idempotent.
- **State-machine bug**: `jamulus_reconnect_inflight` now cleared on the manual-launch failure paths (Not Found, Port In Use), so subsequent reconnect ticks aren't stuck on a stale True flag.
- **Bounded `_levels` dict** in `RealAudioEngine` (cap 1024 entries via LRU-trim); new `clear_level_overrides()` called from `JamulusController.stop()` so stale per-channel meter data doesn't leak between sessions.
- **RPC heartbeat** detects hung Jamulus (process alive but RPC silent for >15s).  Surfaces "Jamulus stopped responding" banner; auto-clears when activity resumes.

#### Real-world failure handling
- **Port conflict detection** before launching Jamulus.  Bind-tests `127.0.0.1:RPC_PORT`; if in use, shows actionable error pointing at `WEBJAM_JAMULUS_RPC_PORT` env var instead of silently leaving an uncontrollable Jamulus running.
- **Mix save/load specificity**: distinguishes OSError ("Permission denied. Check folder permissions and disk space"), JSONDecodeError ("Mix file is corrupted. Save a fresh one with Ctrl+S"), and generic exceptions.  All three flash for 6s and log full traceback.

#### UDP protocol hardening
- **`_parse_level_list`** capped at 500 entries (was unbounded — a hostile/malformed `CLT_CHANNEL_LEVEL_LIST` could allocate tens of thousands of dict entries).
- **Unknown msg_id logs deduped** — each unknown msg_id is logged once per session, preventing log floods from packet storms.

#### Cross-platform fixes
- **Windows `CREATE_NO_WINDOW`** in `subprocess.Popen` so the launched Jamulus doesn't pop up a spurious console alongside its GUI.
- **macOS Cmd+M conflict resolved** — Ctrl+M / Ctrl+Shift+M now bind to literal Control on macOS (via `Qt.MetaModifier`) so they don't collide with Cmd+M = system minimize.  Other platforms unchanged.  F1 help and shortcut labels reflect this.
- **Font fallback chain reordered** — Inter is not bundled, so `-apple-system, 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, Inter, sans-serif` resolves correctly per platform.

#### Accessibility
- **`TEXT_MUTED` `#5F6B85 → #7A8AA0`** (was 2.93:1 contrast on BG_CARD — WCAG AA fail).  `TEXT_SECONDARY` bumped for safety margin.
- **Fader keyboard step**: `setSingleStep(5)` / `setPageStep(15)` (was default 1, made keyboard nav unusable).
- **Participant-context accessible names**: "Volume fader for Alice (decibels)", "Mute Alice", "Solo Alice".  Fader's accessible description includes the current dB value and updates on each change.
- **Side-rail focus border** 1px → 2px for visible keyboard navigation.

#### Performance
- **Single global LevelMeter timer** (was N per-card).  20 participants: 500 events/sec → 25/sec (-95%).  `level_meter.py::external_tick` flag, `participant_grid.tick_all_meters()`, driven by ApplicationController's `_meter_tick_timer`.

#### Webex integration
- **Token refresh on TTL approach** — 5-min safety margin before 1-hour expiry, polled every 60s.  Long rehearsals no longer silently lose Webex auth.
- **`mute_webex_self()` JS bridge** — Mute Me / Ctrl+Shift+M now silences the user in BOTH Jamulus AND Webex (was Jamulus-only).
- **Auto-restore placeholder** when Webex URL fails to load (404/DNS/blocked) — emits `error` state, restores placeholder, shows hint pointing at "Open video call in browser" fallback.

#### Architecture refactor
- **`SessionPersistence` extracted** from `ApplicationController` — `webjam_qt/controllers/session_persistence.py` (111 lines) owns notes + title + mode I/O.  Public methods on ApplicationController retained as thin delegates so existing tests pass unchanged.

#### Developer experience
- **`DEVELOPMENT.md`** +191 lines: 3 contributor tutorials (add a `ParticipantPresentation` field, add a Jamulus JSON-RPC method, wire a new keyboard shortcut) + sections on running tests / ruff / smoke-gate locally.
- **`.github/ISSUE_TEMPLATE/`** — `bug_report.yaml` + `feature_request.yaml` + `config.yml` with structured fields for OS/version/log excerpts.
- **Public docstrings** on `JamulusController.set_fader_level / set_mute`, `BridgeService.launch_jamulus / attempt_auto_reconnects`.
- **Friendly Python version error** in `webjam_qt_main.py` instead of cryptic `SyntaxError` on Python 3.9.
- **Wizard hints at `directory.jamulus.io`** for users without a server.

#### Tests
- **30 new tests** across these new files: `test_file_io`, `test_jamulus_rpc_fallback`, `test_jamulus_concurrent_mixer`, `test_webex_embed_lifecycle`, `test_bridge_reconnect_max_attempts`, `test_repository_mix_migration`, `test_application_controller_demo_to_real_transition`, `test_application_controller_signal_wiring`, `test_settings_corruption_recovery`, `test_audio_engine_levels_bound`, `test_session_persistence`, `test_level_meter_external_tick`, `test_webex_token_refresh`, `test_rpc_heartbeat`.
- **Suite total: 565 pass, 12 skipped** (was 523 at v0.4.4 release; +42 net).

#### Versioning
- **`__version__` → 0.4.5**, surfaced in title bar and F1 help.

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
- **F1 in-app help dialog** (`webjam_qt/windows/conductor_window.py`): F1 now opens a small dialog listing every keyboard shortcut, the colour-coded launch-button semantics, and a 4-step getting-started flow. Useful when users forget shortcuts mid-rehearsal without leaving the app to consult the README.

#### Mid-session settings changes are now context-aware
- **Targeted "leave/relaunch to apply" hints** (`application_controller.py::_open_settings_wizard`): The wizard used to flash a generic "take effect on next Launch Audio / Join Video" message after every save. It now snapshots `webex_url` + `jamulus_server` before the wizard, compares after, and shows specific actions if needed: "Leave Video and re-join to apply the new Webex URL" and/or "Stop Audio and re-launch to connect to the new Jamulus server".

#### Audit-found bugfixes
- **Reconnect-banner latch** (`application_controller.py::_stop_audio`): The `_reconnect_banner_shown` flag was set True on Jamulus crash and reset only when state went back to "Running". If the user clicked "Stop Audio" during reconnect attempts, the latch stayed True and future crash banners were silent. Cleared in `_stop_audio` so subsequent crashes flash again.

#### Tests
- **`tests/test_application_controller_toggle.py`** — 15 tests for `_is_jamulus_running`, `_is_video_active` predicates, button-label transitions, server:port in status bar, and self-mute behaviour.
- **`tests/test_reconnect_manager_edge.py`** — 8 new tests for `stop_jamulus` (terminate, force-kill, idempotency, dead-process), `leave_webex` (state reset, swallow controller errors).
- **`tests/test_qt_setup_wizard.py`** — 3 new tests for forgiving Webex URL validation (auto-prepend, scheme-prefixed bare-word rejection) and skip_welcome.
- **`tests/test_application_controller_toggle.py`** also covers: alone-on-server status, multi-participant counting, muted-card Qt property, session metadata round-trip.
- Suite total: **523 pass, 12 skipped**.

#### More live-session quality-of-life
- **'Mute Me' button + Ctrl+Shift+M** (`webjam_qt/widgets/session_strip.py`, `application_controller.py::_on_mute_self`): A new ghost button between the mode picker and audio button toggles mute on the local user's channel, with a Ctrl+Shift+M keyboard shortcut. Useful when the conductor needs to silence themselves quickly (answering a phone, talking off-mic) without finding their card in the grid. The button syncs in both directions with the local-user card's MUTE button.
- **Restore demos after Stop Audio** (`application_controller.py::_reset_to_demo_state`): When the user clicks Stop Audio, the (now-stale) real-participant cards are replaced with the demo placeholders and the demo-level animation restarts. The status-bar latency label resets to "Not connected". Gives a clear visual signal that audio is off.
- **Forgiving Webex URL validation** (`webjam_qt/windows/setup_wizard.py::_WebexPage.validatePage`): If the user types `org.webex.com/meet/foo` without a scheme, the wizard auto-prepends `https://` rather than silently refusing to advance. Bare words like "not-a-url" still fail (the auto-prepend only triggers on inputs containing a dot before any slash, and a final netloc-dot check rejects scheme-prefixed bare words too).

#### Layout density + session persistence
- **Per-card video tile shrunk to 6px accent bar** (`participant_card.py`, `conductor.qss`): The 'Video arrives when Webex is connected' placeholder used to occupy 120px+ of vertical space on every card, even though per-channel video isn't implemented (Webex video shows in the embedded view at the bottom of the stage). The tile is now a fixed-height 6px accent bar in brand colours (teal for remote, gold for local user). Card minimum height drops from 220px to 150px, fitting roughly 40% more participants on screen.
- **In-session Settings skips Welcome page** (`webjam_qt/windows/setup_wizard.py`): `SetupWizard` accepts a new `skip_welcome=True` keyword arg. When the user reopens Settings via Ctrl+, mid-session, the wizard now starts at the Jamulus page (skipping the welcome) and the title becomes 'WebJam Settings'. First-run flow is unchanged.
- **Session title persists across launches** (`application_controller.py::_load_session_title` / `_save_session_title`): The session title (e.g. 'Tuesday Practice') was lost on every close and reset to 'Band Rehearsal' on next launch. Now persisted to `~/.webjam_session.json` on title change and on shutdown; restored on startup.

#### At-a-glance state visualization
- **Muted participant cards fade visually** (`participant_card.py`, `conductor.qss`): Previously only the MUTE button changed colour when a channel was muted. The card itself now sets a `muted="true"` Qt property when muted, and QSS dims the background to BG_INPUT and the name/role text to TEXT_MUTED — making it easy to scan a busy stage and see who's silent.
- **Friendlier 'alone on server' status** (`application_controller.py::_apply_jamulus_participants`): When the user is the only channel on the server, the Session label now shows "1 participant · waiting for others" instead of the cold "1 participant". 2+ participants show "{N} participants" as before.
- **Last blocking 'Already running' dialog removed** (`services/bridge_service.py::launch_jamulus`): Re-clicking Launch Audio while Jamulus is already running used to throw a modal QMessageBox.information; now flashes a non-blocking status banner.

#### Webex embed resilience
- **Auto-restore placeholder when Webex URL fails to load** (`webjam_qt/widgets/webex_embed.py::_on_view_load_finished`, `application_controller.py::_on_webex_state`): When `QWebEngineView.loadFinished(ok=False)` fires (404, DNS, blocked, network), the embed emits a new "error" state. The controller restores the placeholder, resets the button to "Join Video", and flashes a hint pointing at the 'Open video call in browser' fallback button. Skips false positives from about:blank/data: navigations.

#### Troubleshooting infrastructure
- **Jamulus stdout/stderr captured to `~/.webjam_jamulus.log`** (`services/bridge_service.py::launch_jamulus`): Used to be discarded via `subprocess.DEVNULL`. Now line-buffered, overwritten per launch, closed on `stop_jamulus`. Falls back to DEVNULL if the file can't be opened.
- **Both log paths surfaced in error dialogs** (`application_controller.py::_show_actionable_error`): Lists `~/.webjam.log` (always) and `~/.webjam_jamulus.log` (only when it exists, to avoid confusion in 'Not Found' errors).
- **F1 help dialog mentions log paths** so users can find them without triggering an error first.

#### Versioning + onboarding
- **Bumped `__version__` 0.1.0 → 0.4.4** in `webjam_qt/__init__.py` (was stale across 4 minor releases).
- **Version surfaced in window title** (`WebJam — Conductor (v0.4.4)`) and **F1 help dialog header** (`WebJam — Conductor UI v0.4.4`).
- **Wizard now hints at directory.jamulus.io** for users who don't yet have a Jamulus server.
- **Friendly Python version error** in `webjam_qt_main.py` instead of cryptic `SyntaxError` on Python 3.9.
- **Red 'Unmute Me' button** when self is muted — `QPushButton#GhostButton:checked` paints in danger red (was visually identical to unmuted state).
- **Session mode persists** alongside title in `~/.webjam_session.json`. Bands using the same mode no longer need to re-select it on every launch.

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
