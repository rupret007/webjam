# Pre-Karen QA — Art participant, door, and End/Leave

## Art Preview honesty hotfix (post-#69 squash)

**What failed:** #69 landed on master as `230ef498` while Art stayed
`release_tier=PREVIEW` / `is_preview=True` and docs still claimed Art is
visibly Preview. The in-session strip buried that status with
`profile.is_preview and profile.key != "art"`, so End Room / Leave Room chrome
read **Art · Ready**. Launch copy also said `Art rooms are live for now`.
Karen fail-closed on that honesty gap. Art was not promoted to GA.

**What changed:** Art uses the same Preview status path as other Preview
profiles on the surfaces that claim status: session-strip subtitle, conductor
window title, and notes header. Launch standalone copy now mirrors Review
(`Standalone Art Unavailable`) and no longer claims live/GA. HUD/empty-stage
copy still omits Review's meeting-capture lecture. End Room / Leave Room
behavior from #69 is untouched. Art remains Preview. Parked #37/#49 untouched.

**How verified:** Exact tip `3b29198547dfc006447029fa704152e600d4edc6`.
`QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -v tests/test_art_session_strip.py
tests/test_art_profile.py tests/test_art_start_ux.py tests/test_art_participant_door.py
tests/test_creator_live_presentation.py tests/test_feel_pass.py tests/test_qt_widgets.py
-k "art or preview or creator_profiles or launch_copy or strip_status"`:
161 passed, 93 deselected. Isolated modules after that: Art strip 6, Art
profile 47, start UX 43, participant door 33 (End Room / Leave Room still
compact and accessible), qt widgets 100 + 42 subtests, feel 14, live
presentation 11, Art cleanup 15, Art recovery 6, UI redesign 34. Combined
Art/feel/UX modules: 390 passed, 42 subtests. Remaining Art/honesty modules
plus `python ux_smoke_test.py`: 367 passed and UX smoke PASS. Ruff,
compileall, pip check, and `git diff --check HEAD` passed.

Raw one-interpreter `pytest -q` reached 6986 passed / 27 skipped / 99
subtests, matching #69's passed count. Two unrelated one-process flakes
appeared across two full runs (Pocket Stage live WSS against websockets 17.1
instead of lockfile 16.1.1; one Studio seek checksum). Isolated re-runs of
those modules passed; no failing assertion was removed or weakened.

No merge, tag, sign, release, or Pages.

---

2026-09-05 CT; branch `codex/webjam-art-participant-door`; base
`7f38ba20eb71afffdb37a8d03d29248abfdee1de`.

**Product QA PASS. Integrity PASS.** This reviews the current Art slice,
not historical #68. Final full-suite and hosted results must be attached to the
exact committed tip before handoff. The immutable final SHA,
local results, hosted run URLs, and Bob handoff belong in the open draft PR.
Hosted SUCCESS including four desktop builds is required before lease release.

| Claim challenged | Direct proof and failure coverage |
| --- | --- |
| An Art guest can join without Music audio | Actual controller tests discover Art from a saved Music profile, enter from the first idle LAN snapshot or native host state, and leave without starting Jamulus or a recording owner. The same discovery hands a confirmed Music host back to existing audio startup. |
| Connected means current room evidence | LAN membership requires a fresh authenticated state read, not enrollment alone. Native receipt/source/generation/revision checks run before queueing and delivery. First-profile timeout and stale initial state give bounded update/rejoin guidance. UI ticks do not refresh video receipt age. |
| Follow-along does useful work | Two real controllers use existing same-file matching and silent video follow. Wrong files remain blocked. Canvas invitation follows without auto-launch, then withdraws. Reset retires old video/canvas identity before creating the replacement invitation. |
| Native transport survives a real reset | Two real sidecar processes exercise cached initial state, live follow/withdrawal, Help in both directions, audio datagrams, rate limits, replay rejection, reset, and loss. Reset uses an invitation-scoped wire epoch; a fresh guest need not know the host's local generation. |
| End/Leave is truthful | One cleanup worker retains failed owners for retry, fences late callbacks and pending host construction, and preserves real residual recording/server ownership. Pure Art skips recorder finalization. Failed profile restoration remains retryable without mixing local Notes namespaces. |
| Artists understand the door | Actual native widget tests cover exact control inventory, click/Space selection, compact geometry, nonoverlap, accessible invitation error replacement, and white focus versus burnt-orange selection. The Paint along mark stays neutral in all native icon modes. |
| Claims stay within evidence | Private payloads remain out of representations, diagnostics, support history, and persistence. Art is Preview; Music readiness remains based on audio evidence. Optional meeting/canvas launch remains explicit. |

## Verification record

- Initial full integration run: 7 failed, 6973 passed, 26 skipped, 99 subtests.
  Two legacy test doubles lacked the new host-profile discovery contract;
  five failures exposed a Qt thread lookup on an uninitialized fixture owner.
  The doubles now exercise the real discovery ordering and retain their
  original assertions; the controller dispatches through its actual UI invoker.
- Subsequent unfiltered full run: 6985 passed, 26 skipped, 3 warnings,
  99 subtests in 256.18 seconds. Ruff, compileall, pip check, and UX smoke passed.
- Final review additionally fixed pending-profile cleanup dispatch and failure
  rollback of the borrowed profile, Notes namespace, and title. Focused proof:
  60 passed, 2 subtests; final recovery module: 6 passed. A real Notes edit during
  failed Art cleanup stays in Art and leaves the saved Music notes intact.
- The final source rerun had 6985 passed, 26 skipped, 99 subtests, and one guide
  wording failure. The guide now retains the exact two-Art-choice and Music
  Host/Join instructions; the unchanged guide contract module passes all 11
  tests. A full unfiltered rerun on the committed tip remains mandatory; its
  final result is recorded in the draft PR alongside hosted evidence.
- Go `make check`, `go test -race -count=1 ./...`, `go mod verify`, and
  `go mod tidy -diff` passed (12 tested packages). The two opt-in real-process
  modules passed together: 2 passed in 1.09 seconds. Their hosted invocation
  builds the sidecar from the exact checked-out commit.

No failing test was removed, skipped, weakened, or retried to obtain green.
The full local command is raw `pytest -q` with Qt offscreen; no custom plugin,
warning filter, or exclusion was added. Hosted CI retains its existing
per-module Qt process isolation and now includes the new real-process module.
No dependency or lockfile changes. All pre-existing local work and stashes
remain preserved; only the authorized branch is a commit/push target.

**NOT RUN / Jeff-only:** physical two-computer audio/output and interfaces;
external-editor/device pairing; installed-phone behavior; public rendezvous;
live meeting-provider calls; immutable unsigned v0.27.2 package click/feel;
platform trust, signing, notarization, tags, and releases. No merge, release,
or public-service activation is part of this draft. #37/#49 remain parked;
the #67 branch remains untouched.
