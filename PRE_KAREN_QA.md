# Pre-Karen QA — current Art guest guidance

2026-09-06 CT. Branch `codex/webjam-finish-product-art-creator-guidance`;
exact base `743ebb9b2068f2431cfa217876dc2473e8a7f3e4` (post-#77 squash).
Canonical `/Users/jeffstory/Documents/WebJam` only.
[BEFORE 5557402896](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5557402896)
claims 01:15:53–05:15:53 CT. The original base was verified `c1431851`;
Bob independently squashed #77 during this lease. All current files and the
source patch were preserved before moving only this branch to verified master;
#77's chat-generation guard was retained inside the new refresh deferral.
[DELTA 5557602239](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5557602239)
records the base update and scope. Protected branches and four stashes MATCH.

A guest joining Art with a saved Music/Voice profile now sees creative guidance
from the current Art Notes without having to type. Profile, restored Notes and
final title ownership settle before publication. The zero-payload restoration
signal does not synthesize an edit or save. Failed derivation clears stale
creative content without exposing local Notes in exception diagnostics.

The strict rejoin journey also found a previous Music client’s `Stopped` value
could fail a fresh invitation before its authenticated host profile arrived.
A live current guest owner now reports enrollment in progress and audio not
started during that discovery interval. It cannot claim Art, connected audio,
or participant presence. Profile acceptance, terminal failure, retirement and
cleanup retain their existing owners. Actual Music startup/launch/recovery
facts keep precedence. A captured immutable native snapshot remains valid when
its same-generation worker advances; no conductor reset or forced success was
added.

| Claim challenged | Evidence |
| --- | --- |
| Art adoption needs no repair edit | Actual ApplicationController LAN/native journeys cover saved Music/Voice, empty/saved/pending Art Notes, repeated receipts, Leave and rejoin. Current Art Pulse and accepted creative guidance are required, with zero post-authentication note edits. |
| Local draft ownership survives | Profile round trips, failed pending writes, same-profile ownership changes, cursor/selection/undo and save state are checked. Explicit Notes recovery revisions refresh derived content without fake edits/writes. Final title restoration and failed-restoration rollback remain coherent. |
| A prior stop cannot fail fresh discovery | Current LAN/native rejoin owners remain JOINING with enrollment in progress and no audio/participant success. A deterministic real worker advance during facts collection does not revive the stale stop. |
| Real failure still wins | Authenticated Music handoff and failure, native profile deadline, terminal LAN owner, retained-owner cleanup retry, retired callbacks and all three launch-error values retain their failure behavior. Existing unified Music startup generation/cancellation checks pass. |
| Private work stays local | Per-profile Notes bytes, pending drafts and title markers stay out of public guidance mappings, room projections, flash messages and diagnostics. No new transport payload, meeting launch, player or shared canvas launch is added. |
| Guidance stays visible | Root inspected four Cocoa/Inter 13px captures: actual controlled LAN/native guests at 760×600 and 1040×720. Art's current clay-work next step is visible beside unchanged restored Notes without typing after authentication. Existing compact layout and Talk & share come from #77. |

**Independent PRE_KAREN leftover/privacy/security source review: PASS.**
The review found and resolved the snapshot-object identity timing edge before
final verification. No remaining actionable finding. #77's chat-generation
boundary is preserved; no repaint/layout/new suggestion engine changes are
included. Suggestion’s inherited Notes promise versus its Krita image handoff
remains explicitly deferred. #74 publication, #75 dual Open, #76 return and
#77 Talk & share/layout are baseline, not claims of this slice.

**Pre-Karen local gate: PASS**, with failed-run history retained below.

Local proof (current slice only):

- Final consolidated native Cocoa run on frozen source: **48 passed in
  15.04s**, exit 0. `/private/tmp/webjam-post77-new-native-final.log`.

- 48 new cases: 17 profile/Notes, 14 authenticated room journeys and 17
  pending-profile owner/failure cases. Native Cocoa: local 17 passed in 3.81s;
  final owner/journey 31 passed in 8.41s. Logs:
  `/private/tmp/webjam-post77-guidance-local-cocoa-final.log` and
  `/private/tmp/webjam-post77-guidance-owner-boundary-cocoa-final.log`.
- Earlier combined native guidance/journey/existing Music run: 50 passed in
  12.20s before the final snapshot identity correction. This is supporting
  evidence; final broader verification includes that correction.
- Native capture: 2 passed in 3.52s, no post-authentication typing, no meeting
  or media launch. `/private/tmp/webjam-post77-guidance-capture-corrected.log`,
  `/private/tmp/webjam-post77-guidance-capture-{native,lan}.json` and four
  `/private/tmp/webjam-post77-guidance-{native,lan}-{760,1040}.png` files.
  Synthetic source/controller evidence only; not physical or two-device proof.
- Focused Art/door/session/invitation/guidance gate (77 modules): **2,882
  passed, 49 subtests passed in 121.69s**, exit 0.
  `/private/tmp/webjam-post77-focused-final.log`.
- Final complete raw suite, one process, `QT_QPA_PLATFORM=offscreen
  .venv/bin/pytest -vv`: **8,398 passed, 26 existing skips, 99 subtests
  passed and 3 dependency deprecation warnings in 424.37s**, exit **0**.
  `/private/tmp/webjam-post77-full-pytest-final.log`. No exclusions or
  forced conductor state; the first failed full run is retained below.
- Required Ruff scope plus all seven touched test modules, compileall, pip
  check, runtime dependency policy, UX smoke and git diff check: **PASS**.
  `/private/tmp/webjam-post77-final-static-all-fixtures.log`.

The first focused run had **2,878 passed, 4 failed and 49 subtests passed
in 104.65s** (`/private/tmp/webjam-post77-focused.log`). All four failures
were in an existing partial-controller Paint-along fixture with no Notes
renderer, newly reached by profile refresh. Its fixture now explicitly stubs
that unrelated renderer; no transport assertion changed. The affected module
then passed **37 tests in 1.08s**. Independent review approved the fixture
correction; full-controller guidance remains covered by the new journeys.
A bounded fixture audit identified and updated two analogous partial Shared
Track fixtures, also without changing their transport assertions. The full
Shared Track integration module passed **33 tests in 4.58s**
(`/private/tmp/webjam-post77-reference-track-seam.log`).

The first raw full run exited **1** with **8,395 passed, 3 failed, 26
skipped, 99 subtests passed and 3 warnings in 500.55s**.
`/private/tmp/webjam-post77-full-pytest.log`. The three failures were one
folder-repair and two native-startup cleanup checks, again partial fixtures
without Notes. Their fixture-local renderer stubs preserve the real peer stop
and every recovery/cleanup assertion. The two affected modules then passed
**116 tests in 1.86s** (`/private/tmp/webjam-post77-startup-seams.log`).
Independent source review confirmed the fixture boundary; production did not
change. A short read-only process sample during the late apparent pause found
Qt object destruction in interpreter shutdown; the run subsequently exited
normally with its failed-test status. It was not interrupted. Sample:
`/private/tmp/webjam-post77-full-ui-sample.txt`.

Failure history remains separate from passes. Baseline had two harness errors
(over-specific expected Music sentence and a nonexistent Leave helper), then
two corrected defect/control assertions passed on original master. Initial
new-module invocation referenced a mistyped filename and collected no tests.
The corrected run had 17 passes/13 failures: journey fixture patch-context and
unpersisted settings mistakes, plus real rejoin failures. After fixture fixes,
2 passed/12 failed on the genuine stale-conductor rejoin defect. The new
owner-boundary run had 30 passes/1 failure because the fixture sampled a native
worker connection before queued UI delivery; waiting for the actual strict
JOINING presentation corrected that harness ordering (final 31 pass). The
first temporary capture invocation could not import `tests.conftest`; using
`python -m pytest` resolved collection. All original logs remain under
`/private/tmp/webjam-post77-*.log`. No test exclusion, weakened expected phase,
forced conductor state or hidden product retry was used.

The earlier #77 ENOSPC/full-suite crash history remains in its immutable report
and PR evidence; it is not claimed fixed by this slice. Current full-run
results, including any failures, are reported above. Frozen product/test file
hashes are retained in `/private/tmp/webjam-post77-frozen-source.json`.

Final commit, Tip MATCH, hosted run/job links, exact checkouts and four desktop
artifact records belong in the OPEN DRAFT and coord AFTER. Local evidence does
not replace hosted SUCCESS. Then clear agent/lease and stop for Bob conductor.

Holds: Art Preview; own tools; silent local-file Paint along; Webex first-class
beside WebJam, never on Art door. No second video stack or automatic meeting/
canvas launch. Music audio remains evidence-based. No short-code/public
rendezvous, merge/tag/sign/Pages/Release Trust/release. Unsigned 0.27.2 Jeff-only.
Physical/live-provider/installed-owner-package gates NOT RUN. Parked #37/#49,
completed branches and four existing stashes untouched; stay off #67. WebJam
only. One OPEN DRAFT for Karen; never merge.
