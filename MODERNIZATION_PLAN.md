# WebJam modernization plan

> Historical implementation log through the pre-v0.9 work. For the current
> Host/Join flow, release state, and acceptance gates, use `README.md`,
> `CHANGELOG.md`, `UX_ACCEPTANCE_CHECKLIST.md`, and `TEST_PROCEDURE.md`.

## Audited baseline

- Source baseline for this final audit is `origin/master` at `ad3b7ef`: the
  last pushed private-pilot candidate before the uncommitted v0.9 Host/Join,
  session-workspace, and Studio work. The existing modernization tree was
  preserved and audited in place; no reset, rebase, or discarded change was
  used.
- Before Session Pulse, local validation passed 823 tests with 12 expected
  skips, Ruff, the offscreen UX gate, and a clean macOS PyInstaller build.
- The older dirty checkout outside this repository remains
  untouched. This work continues only in the clean checkout.

## v0.8.1 work completed in the tree

- Added deterministic, local-only Session Pulse extraction for decisions,
  actions/owners, blockers, questions, references, and mode checkpoints.
- Added the plain-text Pulse card and atomic Markdown brief export while
  retaining the complete raw note body and excluding preview participants.
- Replaced the clipping notes/brief buttons with one accessible Export menu
  that fits the supported 280 px canvas width.
- Added fail-safe behavior: if derived Pulse generation fails, stale content
  is discarded and export falls back to current raw notes.
- Normalized action-owner whitespace without broadening the parser heuristic.
- Isolated the chat test's synchronous UI invoker from the background routing
  scan; production thread marshaling remains unchanged.
- Distinguished local Jamulus client control (22222) from same-Mac recorder
  control (loopback-only 22240), and corrected copy that implied audio-device
  detection also configured routing.
- Hardened CI artifact assertions for executable/resources/version/bundled
  Jamulus and changed tag releases to drafts pending hardware approval.
- Added the official Jamulus 3.12.2 same-Mac server procedure, external UDP
  gate, audience-bridge topology, and Logic stem check. The later two-lane
  round moved the musician pilot to direct Jamulus plus Webex speech talkback.
- Added recorder transition truth, elapsed time, duplicate-click protection,
  stable-file discovery, and post-stop take validation with direct Take Deck
  and Finder actions.
- Made Take Deck output-selectable and stereo for headphones, with persisted
  device choice, actionable playback errors, and take-health warnings.
- Replaced blocking configuration-only Ready Check with a rerunnable,
  role-aware report.
  Simplified navigation to Live/Notes workspaces plus Takes/Settings utilities
  and consolidated Ready/Practice under the accessible Checks menu.
- Removed simulated startup participants and animated fake meters. Idle,
  connecting, practice, reconnecting, and failed states now render persistent,
  actionable truth through a centralized UI-only session-state model.
- Reworked Ready Check into structured pass/fix/optional rows with an overall
  result, focus on the first required failure, and retained rerun/settings/
  practice actions.
- Added consistent Start Audio wording, workspace keyboard navigation, explicit
  tab order, dynamic accessible state descriptions, recording retry state, and
  Retry/Settings/Diagnostics recovery actions.
- Added Take Deck-specific empty states so the shared mixer grid never presents
  live-session actions while reviewing recordings.
- Added host-only supplemental SSL capture: inputs 1 and 2 finalize atomically
  as isolated guitar/vocal stems without changing Jamulus live audio.
- Added explicit Preflight/Validating/Complete/Needs Attention phases, bounded
  take discovery, stable-file enforcement, mandatory 48 kHz validation, and
  preservation of partial/recovered audio.
- Added versioned, secret-free take manifests with file evidence, source
  labels, offsets, alignment confidence, errors, and warnings. Take Deck now
  distinguishes verified/attention/legacy takes and server/local tracks.

## Verification completed so far

- Shipped the "Violet Hour" visual identity: full token-palette replacement
  (violet surfaces, electric-violet primary, amber audio, neon-rose danger,
  mint success) with all WCAG AA contrast pins passing, gradient depth on
  the stage/strip/cards/Pulse, QPainter side-rail icons, participant avatar
  initials, first-time styling for menus/tooltips/message boxes/Take Deck,
  and bundled Inter 4.1 (OFL, attributed) with graceful font fallback.

- Fixed the two hosted-mode first-run blockers found in hands-on testing:
  Ready Check ran before Start Audio ever created the JamulusServer container,
  so it false-FIXed the Takes folder ("not writable") and the recorder secret.
  Ready Check now creates the hosted Takes folder itself and reports the
  pre-Start-Audio recorder as an OPTIONAL warning. Separately fixed the
  Settings wizard's permanently disabled Next: Qt mandatory ('*') fields only
  count as complete once their value changes, so pre-filled saved settings
  could never advance. Validation now runs on click with inline error copy
  and heals stale Jamulus paths. Styled the wizard (pages, spin boxes,
  chrome buttons, check/radio indicators), sized it to its densest page,
  aligned Ready Check's action buttons with the shared styles, and switched
  the default font from unbundled "Inter" to the platform UI font.

- Replaced the clipped, expert-heavy five-page first-run wizard with a
  responsive two-step Host/Join dialog. Standard ports, bundled component
  discovery, talkback mode, and recording locations are derived; advanced
  paths/routing remain in Settings. Endpoint parsing, keyboard/accessibility,
  atomic persistence, failures, Ready Check transition, and 560/680/900 px
  layouts have dedicated regression coverage.
  Local validation passes 1,037 tests with 12 expected skips and six subtests,
  all static/dependency/UX gates, responsive rendered-screen review, and a
  clean ARM64 dual-Jamulus build with packaged startup and orphan cleanup.

- The macOS artifact now includes both official Jamulus 3.12.2 application
  bundles, enabling a clean first-time host without a separate server install.
  Discovery prefers a compatible `/Applications` server and falls back to the
  nested server, including under App Translocation. CI preserves and verifies
  both upstream signatures/CDHashes while refreshing WebJam's outer seal.

- Targeted parser, canvas/export, Pulse controller, debounce, and chat tests
  pass: 32 tests plus 3 width subtests.
- Brief cancellation and write failure, stale-state fallback, safe plain-text
  rendering, immediate pre-export refresh, real 200 ms debounce, participant
  filtering, and 280/360/900 px layouts are covered.
- The previous full local gate passed `pip check`, Ruff, compile checks,
  offscreen UX smoke, and 857 tests with 12 expected skips plus 3 width subtests.
  The final recording/UI regression pass now passes 868 tests with 12 expected
  skips plus 3 width subtests. The only
  pytest warning is the pre-existing Starlette/httpx deprecation; the prior
  test-induced Qt cross-thread timer warning is gone.
- `pip-audit` 2.10.1 reports no known vulnerabilities. Audit-only packages
  were removed afterward and `pip check` still passes.
- A clean dependency-only PyInstaller build produces v0.8.1 executables and
  the macOS bundle with correct plist version and UI resources; the frozen app
  remains running under an offscreen launch smoke. `git diff --check` passes.
- The exact ARM64 CI artifact is installed on the host Mac and passes a
  packaged offscreen startup smoke. Official notarized Jamulus and
  JamulusServer 3.12.2 bundles pass signature verification.
- The macOS server sandbox constraint was reproduced and corrected: recorder
  secret and take storage now use the dedicated server app's real container
  directory. UDP 22124, loopback-only RPC 22240, authentication, recorder
  status, and record start/stop were validated on the host, followed by clean
  shutdown.
- The reliability/polish tree passes a clean ARM64 PyInstaller build, strict
  bundle verification, v0.8.1 plist/resource inspection, packaged offscreen
  startup, and termination without an orphan process.
- A destructive clean-profile rehearsal exposed an artifact-only Gatekeeper
  defect: CI added Jamulus after PyInstaller created WebJam's outer resource
  seal. The build now shallow-signs only the completed outer WebJam bundle,
  verifies that seal, and proves Jamulus's notarized CDHash is unchanged.
  The corrected sequence passes a clean ARM64 rebuild, strict outer/nested
  signature verification, packaged first-run startup with an isolated home,
  and orphan-process cleanup. The replacement CI artifact must pass the same
  quarantined clean-install gate before pilot use.
- The recording candidate also passes known-offset alignment, atomic capture,
  device-busy cleanup, participant preflight, `pip check`, scoped Ruff,
  compile checks, UX smoke, `git diff --check`, and `pip-audit` with no known
  vulnerabilities. A clean ARM64 bundle contains the capture/manifest/session
  modules, v0.8.1 metadata, QSS/Webex resources, and the verified official
  Jamulus payload; packaged startup and process cleanup pass.

## Post-df0e623 recording-integrity round

- Fixed the systematic alignment defect: local capture arms before the server
  recorder starts, so isolated stems normally lead the server take, and the
  previous `max(0, offset)` clamps silently discarded that negative offset in
  the estimator, the manifest loader, and the player. Offsets are now signed
  end-to-end; Take Deck plays negative-offset stems sample-aligned and labels
  the trimmed lead-in. Regression tests cover leading/lagging stems, off-grid
  offsets, stereo server tracks, unrelated audio, and negative-offset
  playback and manifest round-trips.
- Replaced raw stride-480 decimation (aliasing-prone) with a 100 Hz
  block-mean envelope for the coarse search plus a bounded full-rate
  refinement pass for sample accuracy. Confidence is now the refined
  normalized correlation, so the 0.15 floor separates genuine matches from
  unrelated audio by orders of magnitude.
- Made the capture callback real-time safe (bounded queue + dedicated writer
  thread; no disk I/O, logging, or shared finalization lock on the audio
  thread) with deduplicated, counted, capped error accounting.
- Enforced keep-all-recordings: failed attaches preserve audio in a visible
  Recovered-local folder, attaches never overwrite existing take files, and
  quitting mid-recording salvages the capture into a Recovered take. The
  capture hand-off between validation worker, stop-failure handling, and
  shutdown is lock-protected and finalization is idempotent.
- Take Deck reuses manifest findings for finished takes instead of re-probing
  audio on selection, labels `validating` manifests as "Checking…", warns on
  Stop Audio during an active server recording, renders roster names as plain
  text (markup-injection fix), and reconnect/README_SIMPLE copy now names the
  real controls.
- Validation for this round: 887 tests pass with the same 12 expected skips
  plus 3 width subtests (19 new regression tests; warnings unchanged from the
  audited baseline), scoped Ruff, compile checks, `pip check`,
  `pip-audit`, offscreen UX smoke, `git diff --check`, clean ARM64 PyInstaller
  build with bundle/version inspection, packaged offscreen startup, and
  orphan-process check. Hardware gates below remain open and unclaimed.

## Two-lane music and talkback round

- Made `talkback` the default Webex role, with explicit `video_only` and
  advanced `audience_bridge` alternatives. Jamulus remains the only music
  path; native Webex is a muted-by-default speech lane for musicians.
- Replaced the bridge checkbox with three mutually exclusive, accessible Setup
  choices. Only audience-bridge mode scans for BlackHole/VB-CABLE.
- Separated supplemental local capture from the Webex role and renamed its
  selector **Meter and local recording input**. The selector remains available
  for metering even when local recording is disabled.
- Removed legacy Guest Issuer credential collection from Setup and omit those
  deprecated credentials on save. Added a first-class musician name instead.
- Added `WEBEX_AUDIO_MODES.md` as the canonical signal-flow and failure-safety
  guide. Aligned the pilot/runbook documentation and corrected the false claim
  that Jamulus never returns the local musician in the personal server mix.
- Made native Webex launch truthful (`Not opened`, `Opening…`, `Opened
  externally`, `Open failed`), removed fake leave/reconnect/participant/media
  controls, and reduced the former video pane to a role-aware launch card.
- Added Jamulus-only **Talk Break / Resume Music** with default-cancel resume,
  persistent PLAY/TALK guidance, reconnect intent, and a dedicated global
  transmit state that cannot be confused with the local mixer-card mute.
  `setMuted` now waits for Jamulus `ok`; error, timeout, and disconnect all
  fail closed instead of inviting Webex speech on an unconfirmed mute.
- Added manual VERIFY rows, readable scrolling, focus/keyboard styling,
  platform-specific guidance, a Takes-folder chooser for local capture, and
  worst-state strip coverage at the supported 1100 px width.
- Final integrated validation passes 948 tests with 12 expected skips, six
  subtests, the single pre-existing Starlette/httpx deprecation warning, Ruff,
  compile checks, `pip check`, `pip-audit` with no known vulnerabilities,
  offscreen UX smoke, and `git diff --check`.
- The installed official JamulusServer 3.12.2 passes all 11 real integration
  tests when temporary secrets and recordings use its permitted macOS sandbox
  container. This validates API authentication, client/server RPC, recorder
  start/restart/stop, client shape, and the exact practice command locally in
  addition to the Linux CI gate.
- Final audit corrections prevent an unacknowledged reconnect from rendering
  TALK, make the new audio-mode environment variable unconditionally outrank
  the legacy bridge flag, block duplicate Webex launches while Opening, redact
  the retained compatibility widget's failure URL, require a real first-run
  participant name, and remove stale camera privacy metadata. Windows builds
  now carry and verify the same 0.8.1 ProductVersion as macOS bundles.
- A clean ARM64 PyInstaller build produces v0.8.1 with the required QSS/Webex
  resources. The packaged app starts offscreen and exits on TERM without an
  orphan WebJam or Qt WebEngine process. Hardware evidence remains required.

## Talk Break fail-closed hardening round

- An independent review of the two-lane surface found one fail-open path:
  Stop Audio during a Talk Break preserved the confirmed-mute flags, so the
  next Start Audio rendered "Resume Music"/TALK while the fresh Jamulus
  client transmitted live, and the reconnect reapply guard then refused to
  re-mute because the stale flag claimed the send was already muted. Session
  teardown (`reset_to_idle`) now clears both transmit flags, and the
  reconnect proof-loss reset applies in every Webex role instead of only
  talkback. Two regression tests pin the stop-then-start and non-talkback
  reconnect cases.
- Corrected the reconnect-failure banner ("WebJam will retry" promised a
  timer that does not exist), moved the launch card's external-launch truth
  from the accessible name to the accessible description (screen readers no
  longer hear "Opening… externally"), added the Ctrl+Shift+M shortcut to the
  Talk Break tooltip, and rewrote `webex_embed.py`'s stale module docstring
  to describe the launch card and mark the retained WebEngine machinery as
  unreachable legacy.
- Validation: 950 tests with 12 expected skips and 6 subtests (warnings
  unchanged), Ruff, compile checks, `pip check`, `pip-audit`, UX smoke,
  `git diff --check`, clean ARM64 PyInstaller build with version/resource
  inspection, and packaged offscreen startup/TERM with no orphan process.
  CI run 29179609441 passed tests, real Jamulus integration, and all three
  desktop builds at `2b9e165`.

## Lobby + in-app hosting round

- Redesigned the disconnected workspace as a centered lobby: the session
  card floats centered over the stage (it previously sat top-left inside the
  flow layout), with display-size title, centered actions including a
  first-class Practice Solo, and a server hint. The Webex launch card became
  a slim bar and the STAGE header was removed.
- Added in-app band-server hosting (`host_server_enabled`): BridgeService
  supervises JamulusServer.app 3.12.2 with the exact validated flag set from
  `server/start_macos_pilot.sh`, including version gate, UDP/TCP port
  preflight, 0600 secret provisioning in the server container, a
  caffeinate-per-pid sleep assertion, append-mode server log, adoption of an
  externally started server, crash restart from the reconnect tick, and
  strict decoupling from Stop Audio. Shutdown stops an active recording via
  RPC before terminating the server; quit/stop dialogs are hosting-aware.
- Hardened ownership boundaries: the host client is forced to loopback;
  external listeners are adopted only after secret authentication and
  recorder verification; adopted servers are never stopped by WebJam; crash
  recovery is independent of the client reconnect preference; readiness
  timeout cleans up owned server/caffeinate processes; unsupported platforms
  do not offer the hosting control.
- The real installed JamulusServer.app 3.12.2 now passes the supervised
  lifecycle on Apple Silicon: UDP 22124 plus loopback RPC 22240, 0600 secret,
  recorder initialisation, Record start/stop acknowledgement, expected sandbox
  take path, clean server termination, and no caffeinate orphan. Still open:
  quit-while-recording finalisation with live participant WAVs and the full
  two-Mac/Logic/soak gate.
- Final local regression evidence for the hardened tree: 985 tests pass with
  12 expected skips and 6 subtests; the only pytest warning is the existing
  Starlette/httpx deprecation. Ruff, compile checks, `pip check`, `pip-audit`
  with no known vulnerabilities, UX smoke, 20-document link/stale-term/secret
  review, and `git diff --check` all pass. The official server binary also
  passes all 11 real integration tests locally.
- A clean ARM64 PyInstaller rebuild from the hardened tree produces an arm64
  v0.8.1 bundle with matching plist versions, current microphone privacy copy,
  no camera claim, required QSS/Webex resources, no missing first-party module,
  valid local bundle seal, successful offscreen startup, clean TERM exit, and
  no WebJam/JamulusServer/caffeinate orphan.

## v0.9.0 Studio and Logic handoff round

- Completed the unfinished integrated Multitrack Studio rather than adding a
  second recording surface. Studio now honors the persisted wired output,
  plays a real stereo bus, and adds non-destructive pan alongside gain, mute,
  solo, waveform transport, and scrub.
- Corrected a data-loss-risk regression in the simplified settings migration:
  an explicitly enabled two-input host capture and its selected device now
  survive application reload and edits to unrelated musician preferences.
  Legacy bridge-only state still cannot silently arm local recording. A
  focused **Recording Setup** dialog lives inside Studio, keeps Host/Join
  onboarding simple, and prevents guests from enabling host-only capture.
- Added an atomic Logic/DAW interchange path instead of attempting to write
  proprietary `.logicx` files. **Export for Logic** streams each signed-offset
  source to a zero-based, equal-length 24-bit PCM stem, preserves channel
  layout and source files, creates a stereo rough mix from current mix state,
  and records instructions plus a secret-free evidence manifest. Negative
  local pre-roll is trimmed; positive offsets become leading silence.
- Take manifests now retain the user-visible session title in addition to
  roster-derived track names, and Studio displays that title without renaming
  or mutating the server's take directory.
- Added focused coverage for signed alignment, 24-bit output, equal-length
  stems, rough-mix gain/pan/mute/solo, mixed-rate rejection, repeated-export
  non-overwrite, source immutability, stereo playback, device persistence,
  host-only capture setup, and session-title round trips. Canonical workflow:
  `RECORDING_AND_LOGIC.md`.
- Final source validation passes 1,157 tests with 12 expected platform skips
  and six subtests. Scoped Ruff, compile checks, `pip check`, `pip-audit` (no
  known vulnerabilities), UX smoke, 22-document relative-link validation,
  secret/private-network scan, and `git diff --check` all pass. The only pytest
  warning remains the audited Starlette/httpx deprecation; the three Qt
  WebEngine profile notices occur at process teardown and are unchanged.
- A clean local Apple Silicon PyInstaller build produces an arm64 v0.9.0
  bundle with Inter/QSS/Webex resources and prepared official Jamulus 3.12.2
  client/server apps. Outer and nested seals verify independently and outer
  signing preserves both nested CDHashes. An isolated frozen Host lifecycle
  starts both bundled apps, proves UDP 22124 plus loopback RPC 22222/22240,
  follows the same close path as an affirmative musician confirmation, releases
  every port, and leaves no WebJam or Jamulus process. The exact post-push CI
  artifacts and two-Mac hardware gates remain open until recorded below.

## Remaining release gates

1. Put both Apple Silicon Macs on the same local network, install the exact
   versioned candidate ZIP, then prove the complete **Host a Jam → Copy Invite
   → Join a Jam → Play** path. Tonight does not claim VPN, internet, NAT
   traversal, port forwarding, Windows, or Intel support.
2. With wired interfaces/headphones at 48 kHz, prove real bidirectional audio,
   truthful local/remote meters, participant identity, mix controls, optional
   Webex talkback, and recovery after a forced disconnect. The app must never
   substitute a process or animated meter for acoustic proof.
3. Record a named two-musician take. Require one non-empty server WAV per
   musician, any explicitly enabled host input stems, verified Studio status,
   stereo playback, and an **Export for Logic** package whose numbered,
   equal-length 24-bit stems import together at `0:00`. Any capture, validation,
   or export failure preserves the source audio but blocks this gate.
4. End and relaunch the jam, then confirm no owned WebJam, Jamulus,
   JamulusServer, or `caffeinate` process remains and the ports are reusable.
   Finish with a 45–60 minute same-LAN soak using the exact artifact.
5. Keep the candidate private. If the closed pilot passes, tag the exact
   validated commit as v0.9.0, inspect the draft release artifacts, repeat the
   critical packaged workflow, then decide whether to widen distribution.

## Post-pilot Session Copilot

After the two-Mac pilot passes, add an optional live text coach without
changing the Jamulus or recording path:

- `Ask Copilot` is always user-triggered and previews the complete outbound
  payload before transmission. Context is limited to explicitly selected
  session title/mode, raw notes, deterministic Pulse, and optional participant
  display names/session state; never send audio, takes, manifests, chat, file
  paths, network addresses, diagnostics, logs, or credentials.
- Suggestions render as plain text in a cancelable side panel. Nothing changes
  the notes until the user inserts an individual item, which is prefixed
  `Copilot suggestion:` to preserve provenance.
- Use a provider-neutral typed coaching interface with an OpenAI Responses API
  adapter first, strict structured output, `OPENAI_API_KEY` environment-only
  credentials, `gpt-5.4-mini` default, and `WEBJAM_OPENAI_MODEL` override.
- Do not enable tools, web search, file access, persistent provider threads,
  automatic calls, audio analysis, repository edits, or autonomous actions.
  WebJam must remain fully functional offline and without an API key.
- CI uses a deterministic fake provider and never requires credentials or paid
  network calls. Ship the Copilot disabled by default only after its privacy,
  cancellation, stale-response, schema, prompt-injection, and UI gates pass.

## Intentionally unchanged

- Jamulus client RPC/UDP behavior, persistence/database schemas, Companion API,
  and live mixer semantics receive no speculative rewrite. Legacy Webex guest
  tokens and embedded meeting state are intentionally retired from the normal
  pilot path in favor of truthful native Webex launch.
- The legacy Docker server image stays digest-pinned and is explicitly not the
  v0.8.1 pilot path; changing that third-party runtime requires a separate
  Linux server validation round.
- Code signing/notarization, Sentry rollout, broad coordinator refactoring,
  dependency churn, automatic router configuration, and native Logic
  integration remain outside the unsigned private pilot. The optional,
  preview-first Improvement Center is explicitly post-pilot and cannot edit
  code, run commands, submit issues, or transmit diagnostics automatically.
- Windows remains CI-built but physical Windows validation is intentionally
  deferred; Sunday approves only the two-Apple-Silicon-Mac closed pilot.
