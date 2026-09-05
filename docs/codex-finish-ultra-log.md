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

## Phase 2 — Worth-Building

- Clean product commit: 49acdb5b7f4293bb3ad95fd53c3125a4bf7a8fbb; based only on origin/master 68b9f292.
- W1–W10 PASS with direct source/test evidence in WORTH_BUILDING.md.
- Full local result: 6821 passed, 25 skipped, 3 warnings, 99 subtests passed in 207.97s (0:03:27).
- Broad touched-area proof: 2339 passed, 2 skipped, 3 warnings, 2 subtests passed in 49.74s.
- Ruff, compileall, pip check and UX smoke all passed; real Swift/Python WSS interoperability passed.
- Synthetic multitrack proof qualified 20/20 iterations (380 executions), with run cleanup verified and physical status not_run.
- Notes owner now preserves both silent restoration and accepted-edit autosave; Song-tools source/tests and its boundary gate remain unchanged.
- Next: final adversarial launch/profile review, scored Pre-Karen product QA and Integrity review, then repeat exact-tip local proof before hosted builds.

## Phase 3 — Pre-Karen product QA

- Final read-only launch/profile review revisited actual controls, compact geometry, own-tools intent, optional canvas and File bootstrap after Worth-Building passed; a rendered-mark palette finding returned to product correction and then passed pixel and full-suite proof.
- Recovery review verified real failed-cleanup history, same-revision Pocket action, silent Notes restore and editable-replacement autosave; no remaining confirmed product blocker.
- Studio/recording review verified exact-byte save ownership, stale-modal rejection, dirty/history retention and Setup retirement before another preflight.
- PRE_KAREN_QA.md records static, happy-path and failure-path evidence plus explicit physical/platform/device NOT RUN limits.
- Integrity review retains the unmodified Song-tools boundary gate; no test skips/deletions, dependency or workflow churn, mass renames or held-branch implementation.
- Rubric scores 68/74: all product/local criteria pass. B3 hosted green and B5 actual external handoff are honestly pending subsequent phases.
- BOB_HANDOFF.md contains review scope and ordered Karen/Bob/Jeff steps; it explicitly forbids treating this preparation as hosted-green.
- Phase verify: rubric/evidence paths and git diff --check validated. Next: commit QA artifacts, repeat required exact-tip local checks, push draft and fix hosted checks until green.

## Phase 4 — exact-tip local proof, first iteration

- Candidate af3b7f75 passed Ruff, compileall, pip check, UX smoke, 2,339 focused tests, Swift 20 tests, real Swift/Python WSS and synthetic 20/20 qualification.
- Its full suite reported 6,820 passed, 25 skipped, 99 subtests passed and one intermittent presence-snapshot test failure; no push or hosted-green claim followed the failure.
- The failing equality compared a freshly minted 15,000-ms lease with the same challenge 1 ms later. The runtime correctly reported 14,999 ms.
- Injected the existing frozen-clock fixture value into that exact-equality scenario. All original replay, generation, rotation and equality assertions remain unchanged.
- Added an advancing-clock test proving repeated identical roster installation preserves challenge identity and does not extend the remaining lease.
- No production presence behavior, held branch, authentication control, dependency or workflow is changed. Next: focused module and a complete clean-tip rerun before pushing the draft.
