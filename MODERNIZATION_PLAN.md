# WebJam modernization plan

## Audited baseline

- Source baseline is `origin/master` at `d667e3f`: published v0.8.0 plus
  post-release documentation. Its GitHub test, real-Jamulus integration, and
  three desktop build jobs passed.
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

## Verification completed so far

- Targeted parser, canvas/export, Pulse controller, debounce, and chat tests
  pass: 32 tests plus 3 width subtests.
- Brief cancellation and write failure, stale-state fallback, safe plain-text
  rendering, immediate pre-export refresh, real 200 ms debounce, participant
  filtering, and 280/360/900 px layouts are covered.
- Full local gate passes: `pip check`, Ruff, compile checks, offscreen UX
  smoke, and 844 tests with 12 expected skips plus 3 width subtests. The only
  pytest warning is the pre-existing Starlette/httpx deprecation; the prior
  test-induced Qt cross-thread timer warning is gone.
- `pip-audit` 2.10.1 reports no known vulnerabilities. Audit-only packages
  were removed afterward and `pip check` still passes.
- A clean dependency-only PyInstaller build produces v0.8.1 executables and
  the macOS bundle with correct plist version and UI resources; the frozen app
  remains running under an offscreen launch smoke. `git diff --check` passes.

## Remaining release gates

1. Push the validated candidate only after final diff review and wait for all
   GitHub jobs: tests, real Jamulus 3.12.2 integration, ARM Mac, Intel Mac,
   and Windows x64 builds.
2. On physical ARM Mac, Intel Mac, and Windows x64, validate clean first run,
   unsigned warning, Ready Check, bundled Jamulus, virtual-device detection,
   Ctrl+P Practice, and clean shutdown.
3. With the Mac mini hosting and playing, prove external UDP 22124 from the
   drummer's Windows machine while VPN is disabled. Never expose TCP 22222 or
   22240; a failed external path blocks the recording pilot.
4. Complete the full two-person gate: audio/latency, participant truth,
   fader/mute/solo/Mute Me, saved mix, chat, echo-safe Webex bridge, reconnect,
   Pulse/exports, server recording, non-empty per-player WAVs, Take Deck,
   Logic import, diagnostics, process cleanup, and a 45–60 minute soak.
5. Tag the exact validated commit as v0.8.1, inspect the draft release's exact
   artifacts, repeat the critical packaged workflow, then publish.

## Intentionally unchanged

- Jamulus client RPC/UDP behavior, Webex token/embed behavior, Take Deck data
  model, persistence/database schemas, Companion API, and mixer semantics are
  already sound and receive no speculative rewrite.
- The legacy Docker server image stays digest-pinned and is explicitly not the
  v0.8.1 pilot path; changing that third-party runtime requires a separate
  Linux server validation round.
- Code signing/notarization, Sentry rollout, broad coordinator refactoring,
  dependency churn, automatic router configuration, AI/network summarization,
  and native Logic integration remain outside the unsigned private pilot.
