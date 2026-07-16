# WebJam zero-friction, recording-grade reliability

**Status:** active implementation record
**Branch:** `codex/webjam-zero-friction-recording-ultimate`
**Started:** 2026-07-15

This document records the source baseline, findings, and evidence boundary for
the zero-friction session and recording-reliability work. It is not a release
claim and it does not authorize a tag, deployment, notarization, or installed
application replacement.

## Product boundary retained

WebJam owns private-session lifecycle, invitations, trustworthy session state,
recording, Studio, export, and recovery. Jamulus remains the live music engine
and owns its devices, channels, buffer, jitter, mix, and normal visible audio
settings. WebJam may observe readiness and bring Jamulus forward to fix a
problem; it must not silently write Jamulus audio settings or claim that a
meter, process, or socket proves human audibility.

The normal product surface remains Host or Join, black/white/burnt-orange, the
three-loop WebJam mark, one dominant action, and progressively disclosed
diagnostics. Webex remains optional conversation/video, not the music path.

## Starting baseline

- Repository: `/Users/jeffstory/Claude/Projects/WebJam/repo`
- Starting commit: `5fee7d096bf8fff3279240a7ed345aa2979a6204`
  (`docs: record v0.16.1 package evidence`)
- Product source candidate: `7c6e7e2533facdb6162d180d57256a5a101faad8`
- Starting worktree: clean; `master` matched `origin/master`.
- Dedicated branch: `codex/webjam-zero-friction-recording-ultimate`.
- No GitHub release, tag, deployment, notarization, or application installation
  was changed while establishing this record.

### Local source checks — 2026-07-15

| Check | Result |
| --- | --- |
| `ruff check webjam_qt/ core/ ui/ services/ api/` | pass |
| `python -m compileall -q core services webjam_qt` | pass |
| `pip check` | pass |
| `git diff --check` | pass before implementation |
| `QT_QPA_PLATFORM=offscreen python ux_smoke_test.py` | pass |
| `QT_QPA_PLATFORM=offscreen pytest -q` | **1,798 passed, 19 skipped, 1 known Starlette/httpx warning, 6 subtests** |
| Local type checker | not installed (`pyright` and `mypy`) |
| Local Jamulus binary | not installed |
| Go toolchain | Go 1.26.5 darwin/arm64 available |

The full CI workflow separately covers the real Jamulus/JACK integration,
native Go transport checks, reference-service/container checks, and package
builds. Its one-hour Jamulus/JACK soak is intentionally manual. Local source
results do not prove real two-Mac sound, device changes, sleep/wake, long
recording recovery, public remote networking, or Logic Pro import.

## Lifecycle findings before implementation

1. `core/session_conductor.py` has the strongest user-facing model: immutable
   facts, fact-derived presentation, and generation/revision guards. The
   production controller derives from it but does not yet use a live
   `SessionConductor` instance as the runtime authority.
2. Runtime truth is split between `SessionLifecycle`, `SessionUiState`,
   `ApplicationController._startup_attempt`, audio/remote booleans, and direct
   HUD writes. The startup journey can bypass the conductor, allowing two
   surfaces to disagree after late callbacks or recovery.
3. Host/Join already hides addresses and ports and guards duplicate activation.
   Native hosting correctly waits for server/RPC/listener evidence before
   presenting an invite, but the authority for startup/reconnect/recovery is
   spread across controller, bridge, and audio layers.
4. Band Check already keeps configuration, signal, transport, and human
   audibility evidence separate. The gap is a brief automatic readiness view,
   not a safer replacement for the existing evidence model.
5. The take pipeline is conservative and durable: private journal, capture
   gaps, recovery, immutable transfer metadata, aligned export checks, and
   explicit uncertainty already exist. Distributed local originals do not yet
   have a recording epoch/shared clock or production-wired drift alignment, so
   they must remain preserved-but-unverified until evidence supports alignment.
6. `core/session_transport.py` models quality and path transitions, but its
   real measurements are not yet the primary UI/session authority. The native
   remote profile remains a loopback/reference lab, not a public-Internet
   claim.

## Implementation sequence

1. Promote one typed, generation-guarded runtime snapshot to authoritative
   status; retain `SessionLifecycle` as the redacted journal and make UI
   objects render-only.
2. Route Host, Join, readiness, invite, reconnect, recording, stop, and
   recovery callbacks through that snapshot without weakening provider truth.
3. Add a passive automatic readiness summary using existing checks; healthy
   users do not see a wizard, while an unhealthy route exposes one useful
   action and optional details.
4. Bind real quality/transport evidence to the same snapshot, invalidate
   hearing proof on path changes, and prohibit unsafe transitions during a
   take.
5. Extend recording epochs, per-participant finalization, continuous integrity
   evidence, drift-aware post-take alignment, and DAW metadata only where the
   evidence is real.
6. Consolidate the UI around Host/Join, a single session action, and the
   existing Logic-like Studio; test compact geometry, keyboard access, and
   recovery states.

## Implementation ledger

### 2026-07-15 — guarded session handoff and guest-original timing gate

- The controller now owns one live `SessionConductor` attempt boundary.
  Startup and retry receive role-bound generation tokens, a failed Host attempt
  can safely become a new Join attempt, and stale startup callbacks are ignored
  before they can redraw a replacement attempt. Confirmed cancellation and
  session cleanup advance the token before returning the lobby to idle.
- Automatic reconnect may reopen a failed attempt only after fresh
  authenticated Jamulus roster evidence returns (including this Mac's local
  participant proof for a host); a process restart or meter alone cannot
  resurrect a failed session view.
- A completed native startup hands immediately to the ordinary session HUD.
  That path uses the existing host-share gate, so **Copy Invite** appears only
  after verified server/RPC/listener/private-LAN evidence instead of being
  offered transiently during startup.
- A remote runtime callback is accepted only from the currently installed
  runtime object. Remote Guest intent wins over an old Host profile, and a late
  failure from a replaced runtime cannot overwrite the active session.
- A checksum-verified guest Local Original can now receive an automatic timing
  transform only against exactly one checksum-verified Jamulus server reference
  for that same participant. Promotion requires an aligned result, at least
  three anchors, confidence of at least 0.85, residual at most 2 ms, and no
  analysis issues. Missing, ambiguous, gapped, malformed, or weak evidence
  remains preserved but explicitly uncertain and is blocked from aligned
  export. A Local Original that arrives before its same-participant server
  reference stays in a retryable waiting state; it is rechecked when that
  reference arrives. Reconciliation serializes per take and retries if the
  manifest changes during analysis. The accepted transform records the exact
  reference fingerprint and export rechecks it against the retained media.
- Source evidence: **118 focused tests passed** across startup, host-share,
  remote-callback, conductor, transfer, and export paths. The complete local
  source suite then passed: **1,817 passed, 19 skipped, 1 Starlette/httpx
  deprecation warning, 6 subtests**. The offscreen UX smoke gate passed. Ruff,
  Python compile, dependency, and whitespace checks pass for the changed
  files.

This is still source-level evidence. Provider callbacks do not yet carry an
independent source revision, so the live conductor protects attempt generation
but does not claim source-order proof among same-generation callbacks. Real
two-Mac Jamulus audio, capture-clock drift over a long take, interruption
recovery, and external-editor import are **NOT RUN**.

### 2026-07-15 — simpler normal handoff and finalization race hardening

- Normal Host and Join no longer ask the musician to click **I Finished Sound
  Setup**, answer a sound-confirmation prompt, choose a startup Webex option,
  or press **Enter Jam**. Jamulus remains visibly available for its own audio
  setup; WebJam watches for fresh authenticated connection/local-identity
  proof, then hands directly to the normal Session HUD and host-share gate.
  The setup HUD’s only action is **Bring Jamulus Forward**. Musicians still
  play a note and verify each other; software connection proof is not shown as
  hearing proof. Webex is optional under **More**.
- An authoritative roster recovery now retires a stale failed startup attempt
  only after it has created and observed a fresh conductor generation. This
  prevents an old **Try Again** screen from owning a connected session or
  cancelling it on the next action.
- Host peer-take registration now wakes the existing owned maintenance worker
  instead of hashing, copying, or timing-aligning Local Originals on the Qt
  recording-completion path. A direct reconciliation API remains available to
  non-UI callers. End/Leave still invalidates that worker generation before it
  may publish a late update.
- Project-manifest writers now share a short per-take lock. Peer reconciliation
  performs its expensive media work outside that lock, then uses an exact-byte
  conditional replace; a competing cooperative writer makes it reload and
  retry instead of overwriting the newer project. Changed schema-v2 project
  payloads must use the exact next revision, so a delayed or arbitrarily
  higher-revision writer fails closed rather than silently replacing newer
  truth.
- Active musician guides, help maps, pilot playbooks, UX acceptance criteria,
  architecture, roadmap, and Webex guidance now describe the same automatic
  handoff. Historical package records remain marked historical and were not
  rewritten as current-package claims.

Final local source gate after these changes:

| Check | Result |
| --- | --- |
| Changed Python files: `ruff check` | pass |
| `python -m compileall -q core services webjam_qt` | pass |
| `pip check` | pass |
| `git diff --check` | pass |
| `QT_QPA_PLATFORM=offscreen python ux_smoke_test.py` | pass |
| `QT_QPA_PLATFORM=offscreen pytest -q` | **1,833 passed, 19 skipped, 1 known Starlette/httpx warning, 6 subtests** |

Qt emitted three existing WebEngine-profile teardown messages after the green
test run; they did not fail a test or change source state. This remains
source-level evidence, not a physical two-Mac run.

## Evidence rules

- A process, meter, file, packet, or RPC result is never presented as proof
  that musicians can hear each other.
- An integrity failure in capture, transfer, or export is marked **Needs
  Attention**, not complete. A hash-verified transfer may remain preserved
  while it waits for or lacks timing evidence; it is not eligible for a
  timing-ready export until that evidence is verified.
- Automated, loopback, JACK, package, and physical evidence retain separate
  labels. Physical test items are **NOT RUN** until actually performed.
