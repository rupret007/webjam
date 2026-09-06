# Pre-Karen QA — Art Notes to Talk & share

2026-09-06 CT. Branch `codex/webjam-finish-product-art-next-action`; exact base
`c143185193d2722027a58a621e83d955ce7b977a` (post-#76 squash).
[Coord BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5556911937),
23:32:43–03:32:43 CT. Canonical `/Users/jeffstory/Documents/WebJam` only.
This report covers the current slice; no earlier PR's test results count as
its proof.

Art Notes now offers **Talk & share**, revealing the existing Conversation
controls without opening a meeting or sending notes. Previously, its enabled
“Message artists” field sent only through Jamulus, then instructed an Art guest
to reconnect to a band. Two actual-controller baseline assertions reproduced
that dead end and stale chat text entering Art Notes; they are defect evidence,
not post-change passes (`/private/tmp/webjam-post76-chat-before.log`).

| Claim challenged | Current evidence |
| --- | --- |
| The advertised Art communication action reaches a usable destination | Actual ApplicationController host and LAN/native guest journeys with controlled backends exercise the actual Talk & share button with configured/unconfigured meeting controls. The existing panel is revealed and focused; no meeting, Jamulus, player or canvas is launched. |
| Local work stays local and intact | Notes bytes, cursor/selection, undo and save state survive navigation. Profile changes retain per-profile Notes bytes/pending drafts and preserve the hidden non-Art message draft and its selection. Art hides/disables the Jamulus composer. Failed non-Art sends retain their existing restoration behavior; shutdown restoration preserves a draft without stealing focus or overwriting newer typing. |
| Queued chat cannot cross into Art | Art rejects hidden keyboard callbacks and direct send dispatch before clearing/restoring text. Incoming chat checks current profile and shutdown at receipt and UI delivery, retains existing RPC source validation, and rejects an earlier profile generation even after Music→Art→Music. |
| Notes and Conversation remain usable at supported sizes | Actual room guidance and transition text are covered at 720×560, 760×600 and 1040×720, including 125% font widths and save failure. The same tools share a row only when their full widths fit; below 400px Suggestion retains its separate row. Focus, button identity and recovery visibility survive reflow and font changes. Readout content is retained and editor minimum size is asserted. |
| Compact navigation preserves the independent meeting | Below 900px, Art Notes takes the full workspace and hides only WebJam's Conversation card, including on resize. The card's identity, link and status remain intact; Talk & share reveals it again. Wider Notes preserves current Conversation visibility. Art's narrow Conversation layout adapts its text/control arrangement and height without inventing a meeting state. |
| Keyboard navigation preserves editing | Art's physical Control+Tab/Control+Shift+Tab leaves the editor in either direction without altering bytes, selection or undo. Plain Tab still inserts text and Music behavior is retained. Cocoa verified physical Control as `⌃⇥`; Command is `⌘⇥`. |

**Independent source/privacy/leftover review: PASS.** Current-profile, queued
receipt, save-recovery and optional-launch boundaries were reviewed. Creative
Pulse's stale Music-derived content and Suggestion's Notes promise versus its
Krita image handoff remain explicitly deferred. This slice does not fix them
or add Art chat. Existing #74 publication, #75 dual activity routes and #76
Back to room are baseline, not new claims.

**Pre-Karen local gate: PASS, with failed-run history retained below.**
Passing current-slice evidence:

- **111 new tests:** 48 communication UI/dispatch, 18 real room journeys,
  13 real-controller Notes layout and 32 Conversation layout/state cases.
- Final consolidated native Cocoa run: **155 passed in 24.54s**, comprising
  those 111 plus 17 existing return UI and 27 Webex lifecycle cases.
  `/private/tmp/webjam-post76-communication-final-cocoa.log`.
- Final focused run: **2,671 passed and 7 subtests passed in 99.17s**.
  `/private/tmp/webjam-post76-focused.log`.
- Native screenshot capture: **1 passed in 3.89s**. Root inspected four final
  Cocoa/Inter 13px views: Notes and Conversation at 720×560 and 1040×720.
  `/private/tmp/webjam-post76-native-capture.log`,
  `/private/tmp/webjam-post76-communication-capture.json`, and
  `/private/tmp/webjam-post76-communication-{notes,conversation}-{720,1040}.png`.
  These are synthetic controller/desktop captures, not physical or two-computer
  proof. The explicit note edit refreshed Pulse for the screenshots; that is
  not a fix for its deferred profile-adoption refresh gap.

The **first raw full-suite run failed**: **7,964 passed, 110 failed, 279 errors,
26 skipped, 84 subtests passed and 3 warnings in 488.40s**. The audit found
explicit `ENOSPC` in 109 failing tests. The 279 errors comprised 270
temp-directory setup errors, 8 SQLite errors and 1 soundfile error. The other
failure was the stale accessibility-copy expectation at
`tests/test_qt_widgets.py:1114`. Root updated that exact assertion to the
current Talk & share contract, with no product change. This failed run remains
recorded; the correction and storage-error audit do not constitute a full
pass. Raw log: `/private/tmp/webjam-post76-full-pytest.log`.

A **second attempted raw full run exited 139** around 3%, reporting a
segmentation fault in existing `test_art_activity_guest_journey.py:33` during
its theme fixture teardown. This coincided with root's Ctrl+C request after
the stale assertion was identified. Ordering and cause remain unresolved;
the exit is neither a clean full run nor a proven fixed crash. Raw log:
`/private/tmp/webjam-post76-full-pytest-retry.log`.

The ordered full-suite prefix plus the corrected `test_qt_widgets` module
passed **427 tests and 53 subtests in 42.34s**, with one dependency warning,
exit **0**. `/private/tmp/webjam-post76-crash-diagnostic.log` records verbose
ordering; its 16-module inventory is retained beside it. The teardown crash
did not reproduce. Native crash evidence is
`/Users/jeffstory/Library/Logs/DiagnosticReports/python3.12-2026-09-06-003133.ips`:
`QApplication::setStyle` / stylesheet restoration, `EXC_BAD_ACCESS`. Neither
this diagnostic nor a later pass proves its cause or a fix; no speculative
style/GC cleanup, suite exclusion or assertion weakening was added.

The final complete raw suite, one process with verbose ordering,
`QT_QPA_PLATFORM=offscreen .venv/bin/pytest -vv`, passed **8,350 tests,
26 existing skips, 99 subtests and 3 dependency deprecation warnings in
451.74s**, exit **0**. Log:
`/private/tmp/webjam-post76-full-pytest-final.log`. It includes all 111 new
cases and the corrected existing accessibility contract. Storage was checked
before this run (4.69 GiB free) and remained sufficient. The first failed log
and attempted rerun remain separate evidence.

Required Ruff scope plus all four new modules and `test_qt_widgets`: **PASS**,
`/private/tmp/webjam-post76-final-static.log`. Compileall, pip check and UX
smoke: **PASS**, `/private/tmp/webjam-post76-compile.log`,
`/private/tmp/webjam-post76-pip-check.log`,
`/private/tmp/webjam-post76-ux-smoke.log`. `git diff --check`: **PASS**. The
final legacy assertion update changes only the expected accessibility string;
production code and the four new modules match the native/focused proof.

Final commit SHA, exact-tip hosted run/job links and four desktop artifact
records are recorded in the open draft PR and coord AFTER. Hosted **SUCCESS**
remains required before handoff; local proof does not substitute for it.

Holds: Art Preview; own tools; silent local-file Paint along; Webex talk/share
first-class and separate, never on the Art door. No automatic meeting/canvas
launch, new video stack, short-code/public rendezvous or default Session Help.
Music audio remains evidence-based. Physical/public/live-provider/installed
owner-package gates **NOT RUN**. Unsigned 0.27.2 remains Jeff-only. No
merge/tag/sign/release/Pages/Release Trust. #37/#49 parked; #67, completed PR
branches, protected branches and four existing stashes untouched. One OPEN
DRAFT for Karen, then verified coord AFTER and agent:none / lease cleared.
