# WebJam modernization plan

## Audited baseline

- Source baseline is `origin/master` at `fd3d313`: the untagged v0.8.1 pilot
  candidate. GitHub tests, real-Jamulus integration, and all three desktop
  build jobs passed for that exact commit.
- Before Session Pulse, local validation passed 823 tests with 12 expected
  skips, Ruff, the offscreen UX gate, and a clean macOS PyInstaller build.
- The older dirty checkout at `/Users/jeffstory/Documents/webjam` remains
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
  gate, BlackHole Multi-Output/Webex bridge topology, and Logic stem check.
- Added recorder transition truth, elapsed time, duplicate-click protection,
  stable-file discovery, and post-stop take validation with direct Take Deck
  and Finder actions.
- Made Take Deck output-selectable and stereo for headphones, with persisted
  device choice, actionable playback errors, and take-health warnings.
- Replaced blocking configuration-only Ready Check with a rerunnable report
  that distinguishes the designated Webex bridge from Jamulus-only musicians.
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
- The recording candidate also passes known-offset alignment, atomic capture,
  device-busy cleanup, participant preflight, `pip check`, scoped Ruff,
  compile checks, UX smoke, `git diff --check`, and `pip-audit` with no known
  vulnerabilities. A clean ARM64 bundle contains the capture/manifest/session
  modules, v0.8.1 metadata, QSS/Webex resources, and the verified official
  Jamulus payload; packaged startup and process cleanup pass.

## Remaining release gates

1. Install BlackHole with administrator approval, restart the host, create and
   verify the 48 kHz SSL + BlackHole Multi-Output bridge, then pass F2 Ready
   Check and Ctrl+P Practice with real guitar/vocal input.
2. On the drummer's Apple Silicon Mac, install Roland's TD-27 driver, select
   VENDOR mode, verify 48 kHz USB audio, install the exact ARM64 candidate, and
   pass Ready Check/Practice over Ethernet and wired headphones. BlackHole is
   optional because this Mac is not the Webex audio bridge.
3. Invite the drummer to Tailscale and prove a direct peer path. Reserve the
   host LAN address, forward UDP 22124 only, prove the public path externally,
   and compare both routes for ten stable minutes. Never expose TCP 22222 or
   22240.
4. On the host, prove that SSL 2+ can be opened concurrently by Jamulus and
   WebJam supplemental capture. Require separate audible guitar/vocal WAVs,
   confident manifest alignment, Verified Take Deck status, and aligned Logic
   import. Any isolated-stem failure preserves the server take but blocks the
   recording gate.
5. Complete the full two-Mac gate: audio/latency, participant truth,
   fader/mute/solo/Mute Me, saved mix, chat, echo-safe Webex bridge, reconnect,
   Pulse/exports, server recording, non-empty per-player WAVs, Take Deck,
   Logic import, diagnostics, process cleanup, and a 45–60 minute soak.
6. Keep the CI artifact private for Sunday. If the closed pilot passes, tag the
   exact validated commit as v0.8.1, inspect the draft release's exact
   artifacts, repeat the critical packaged workflow, then publish.

## Intentionally unchanged

- Jamulus client RPC/UDP behavior, Webex token/embed behavior, persistence/
  database schemas, Companion API, and live mixer semantics are
  already sound and receive no speculative rewrite.
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
