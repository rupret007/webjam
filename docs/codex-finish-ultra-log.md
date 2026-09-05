# WebJam Ultra phase log

## Phase 0 — inventory

- Confirmed canonical origin and fetched master a004bbbce20a8ced3b67f7ec89798e0a9416f208; created the new master-based Ultra branch.
- Preserved pre-existing local edits before switching; held PR branches are not implementation inputs.
- Read the owner contracts and split read-only audits across Art/launch, recovery/guidance/notes, and recording/Studio/Pocket Stage.
- Recorded ranked, path-specific product gaps in QUALITY_GAP_LIST.md before changing source code.
- Created a weighted binary rubric across File Change, Spec Alignment, Integrity, Runtime/UX and Build/Test; every score remains unverified.
- Baseline dependency check passes; Ruff passes with cache disabled for the restricted audit environment.
- Baseline full offscreen pytest: 6,662 passed, 25 skipped, 97 subtests passed, 20 teardown errors; failing widget disposal is under diagnosis and remains a failed gate.
- Phase verify: rubric structure/weights/IDs and current-source evidence paths validated; git diff --check passes. Next: regressions and serial fixes for Art, shared recovery, notes, recording readiness and Studio retry; retain the full failed baseline as evidence.

## Phase 1 — product behavior and regression coverage

- Make together now starts with each artist's own tools; optional canvas and existing invitation/profile compatibility remain intact (128 focused Art tests passed).
- Launch has the locked Art/Music controls; File opens local Music/Podcast/Review before startup. Windows component setup has its own explicit page; the door harvester no longer excludes visible controls (123 initial launch tests passed).
- Shared recovery now exposes Paste New Invite and Close Setup consistently. Failed cleanup retains its owner and cannot launch a replacement; Art Conversation explains meeting demonstrations beside silent local Paint along.
- Notes autosave preserves failed/oversized drafts and unreadable originals, offers per-workspace recovery/export without changing the session, guards stale modal edits, and includes accepted Song suggestions (167 focused tests passed before final inventory refresh).
- Studio preserves oversized primaries until replacement, retains dirty/history after unconfirmed publication, still rejects intervening writers, and gives visible long-name recovery (158 focused Studio tests passed).
- Recording readiness checks storage honestly and routes blocked local inputs to Recording Setup only after retiring the exact pending plan. Stale modal decisions cannot affect a new or active take.
- Final focused recording/launch/notes regressions: 214 passed, 2 subtests passed. Earlier startup/recording/door slice: 273 passed. Full-suite teardown fault fixed at its producer by scoping finite mocks and disposing its window; producer+consumer 21 passed.
- Phase verify: Ruff, compileall, pip check, ux_smoke_test.py and git diff --check pass. No dependency, held-branch, release, or device changes. Next: full local suite and broad focused proof, then score Worth-Building and adversarial QA against actual evidence.

### Phase 1 — full-suite and adversarial follow-up

- First complete post-change suite: 6,707 passed, 25 skipped, 99 subtests passed, eight failures in retired launch/canvas assertions; no tests skipped or removed to hide them.
- Updated those contracts while retaining optional canvas publication/persistence and every geometry ceiling; new Windows setup-page geometry and full visible-control inventories strengthen the checks (97 focused tests passed).
- Fixed completed shutdown asking a second question using stale process information; scoped the baseline finite mock and dispose its owning window. Temporary diagnostic plugin is not repository code and is absent from final runs.
- Compact Art Conversation title now fits; File-menu bootstrap has actual offline application integration tests (69 bootstrap tests passed).
- Pocket accepts the new recovery snapshot facts without adding commands; Swift protocol/transport suite passed 20 tests.
- Independent review found stale Pocket action propagation and false safe-retry history. Exact generation/revision matching and real cleanup transition tests now cover both (88 guidance/Pocket tests passed).
- Added Studio failure-with-different-published-bytes coverage: pending edits/history and old token survive; retry conflicts without overwriting the intervening writer.
- Next: rebase this new branch only onto updated origin/master, then run broad focused, synthetic integration and full local proof before scoring Worth-Building.

### Phase 1 — repository boundary follow-up

- Rebased cleanly onto origin/master 68b9f292; no held branch was used.
- Ruff, compileall, pip check and UX smoke passed; 81 focused modules passed 2,279 tests, two skips and two subtests.
- Real Swift/Python WSS interoperability passed; the fixed synthetic multitrack matrix passed 20/20 clean-process iterations (380 test executions) with cleanup verified.
- Full suite at 2a5a2e0: 6,818 passed, 25 skipped, 99 subtests passed, one boundary-test failure. That check intentionally forbids simultaneous Song-tools and session-authority changes.
- Restored Song-tools production and tests byte-for-byte from master. Notes now owns notifications for editable replacements; a distinct silent restore method loads persistence bytes. Accepted suggestions still autosave through the unchanged caller.
- The boundary test is unchanged. Added direct Notes edit-versus-restore signal regression; rerun full proof before scoring the gate.

## Phase 2 — Worth-Building

- Clean product commit: 1e31e1d447a69e4f29914168a1602af8cfb13cac; based only on origin/master 68b9f292.
- W1–W10 PASS with direct source/test evidence in WORTH_BUILDING.md.
- Full local result: 6820 passed, 25 skipped, 3 warnings, 99 subtests passed in 218.80s (0:03:38).
- Broad touched-area proof: 2338 passed, 2 skipped, 3 warnings, 2 subtests passed in 51.29s.
- Ruff, compileall, pip check and UX smoke all passed; real Swift/Python WSS interoperability passed.
- Synthetic multitrack proof qualified 20/20 iterations (380 executions), with run cleanup verified and physical status not_run.
- Notes owner now preserves both silent restoration and accepted-edit autosave; Song-tools source/tests and its boundary gate remain unchanged.
- Next: final adversarial launch/profile review, scored Pre-Karen product QA and Integrity review, then repeat exact-tip local proof before hosted builds.

### Phase 3 finding — return to product correction

- Actual offscreen renders showed the Paint along face contained blue/green despite passing stylesheet color tests.
- Kept the locked illustration and 72×48 geometry; native Qt presents the app-owned icon in neutral grayscale in every state. The source artwork is unchanged.
- Added pixel-level checks of the real card icon across Normal/Disabled/Active/Selected and Off/On, including retained face detail.
- Worth-Building is explicitly marked for revalidation; the failed visual finding is not hidden behind an earlier test pass.
- No product media, external tool, capture path, dependency or held branch is changed. Next: focused palette/launch proof, then full local revalidation and final Pre-Karen score.
