# Pre-Karen QA — return to the current Art room

2026-09-05 CT. Branch `codex/webjam-finish-product-art-continuity`; exact base
`91567b3c3ba6c33836bc67c040142ddd65702bc3` (post-#75 squash).
[Coord BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5556458377).
Canonical `/Users/jeffstory/Documents/WebJam` was clean before this slice.
The #75/#74 branches, protected branches and four prior stashes remain held.

An artist can choose **Back to room** from Art Notes or Paint along to reveal
the full current room, including changed activities or connection recovery.
Previously, Paint along restored the outer workspace container while compact
Notes still hid the room. Notes had no direct room-return action. This slice
selects the existing stage route and focuses its current overview; it adds no
transport or creative service. Escape and lifecycle release retain their
existing return to the previous workspace.

| Claim challenged | Product evidence |
| --- | --- |
| Explicit return reaches the room the artist is in now | Actual LAN and native ApplicationController journeys open Notes, then return directly or through Paint along. The full room is visible and focused, Notes is hidden, and the current activity actions are reachable. LAN journeys also cover the first authenticated video offer appearing while Notes is active. |
| Returning does not discard local work | Saved Music/Art preferences, notes text, selection, cursor and undo remain intact. Tests retain saved or failed local drafts and unsent message text. A proven local video copy, player, playback position and the guest's hidden-video choice survive navigation without another load. |
| Loss and cleanup retain a useful destination | LAN withdrawal, reconnecting, terminal failure and unfinished cleanup render current room facts on return. Native failure safely releases its old video panel to the previous Notes workspace; explicit Notes Back then shows the failed room. No native RECONNECTING behavior is invented. |
| Retired callbacks cannot redirect a later workspace | Current Art profile and shutdown checks precede navigation. A captured return from a released video panel is rejected by object identity after replacement, native Leave/rejoin or profile return. A stale Notes click after leaving Art is also rejected. |
| Optional tools remain optional and private | Return changes neither connection owner nor generation, creates no extra player, and launches no Drawpile, Jamulus or Webex. New signals carry no text or invitation. Private draft, invitation, filename and identity markers remain absent from room projections, accessibility and diagnostics. Conversation keeps its existing separate visibility. |
| Compact Notes keeps its existing tools and recovery | The quiet return fits the existing 34-pixel header. An Art-only compact adaptation prevents the two Notes tool rows from overlapping; every existing action remains available. Expansion or a switch to another profile restores inherited button styles and normal spacing. Save-failure visibility and persistence logic are unchanged. |

**Independent source/privacy/leftover review: PASS.** The product diff is
limited to the SessionCanvas return control/compact layout and the controller's
shared return intent and two signal connections. Existing dismissal, local
notes ownership, media ownership and canvas publication paths are unchanged.
No actionable introduced privacy, stale-owner, profile/style restoration or
save-failure issue was found in the final source review.

**Pre-Karen local gate: PASS, with the unreproduced focused-run crash recorded
below.** Final proof on this source:

- New return tests: **50 cases** — 17 UI, 24 LAN journeys and 9 native journeys.
- Final native Cocoa verification: **61 tests passed, 5 subtests passed** in
  **12.42 seconds**. This combines the 50 new cases with 11 existing
  SessionCanvas export cases; the five are subtests, not additional tests.
  Exact output was reported by Surface's tool exec session **95115**. That
  run had no separate raw log file. Source was frozen after that run.
- A subsequent diagnostic run of the new 50 cases plus 23 existing session
  transfer cases passed **73 tests in 11.63 seconds**:
  `/private/tmp/webjam-post75-return-transfer-diagnostic.log`.
- The **first raw full-suite attempt**, in one process, passed **8,239 tests,
  26 existing skips, 99 subtests and 3 dependency deprecation warnings** in
  **302.57 seconds**, exit **0**. This was a raw full run, not a reconstruction
  from isolated module results:
  `/private/tmp/webjam-post75-full-pytest.log`.
- Final focused diagnostic across the same **57 modules**: **2,352 tests and
  5 subtests passed in 69.51 seconds**, exit **0**, with verbose ordering and a
  read-only GC/thread callback probe. Assertions, module selection and GC
  settings were unchanged: `/private/tmp/webjam-post75-focused-diagnostic.log`.
- Final required Ruff scope plus all three new test modules, compileall,
  pip check, UX smoke and `git diff --check`: **PASS**, each exit **0**.
  Commands and results: `/private/tmp/webjam-post75-static-ux.log`.

The initial broad focused run ended in a **SIGSEGV**, with the macOS crash
report showing Shiboken's `mainThreadDeletionHandler(void*)`. Its failed run
is retained at `/private/tmp/webjam-post75-focused.log`; the crash report path
is recorded in `/private/tmp/webjam-post75-crash-report-path.txt`. Subsequent
73-case and first raw full-suite passes are evidence of those runs only.
They do **not** establish the crash's cause or prove it fixed. The same-module
focused diagnostic also completed without reproducing the crash. Its read-only
probe recorded 386 GC events, including 68 on worker threads across existing
Art, remote and transfer tests; it identified neither the deleted object nor a causal fix.
Probe evidence is `/private/tmp/webjam-post75-focused-gc-events.log`. This was
a proportionate investigation of an unreproduced failure. No speculative
product/test changes, assertion weakening or GC workarounds were added, and
the initial failure remains recorded separately from the passing runs.

Four final Cocoa screenshots were inspected: compact Notes, compact connected
room, compact reconnecting room and normal connected room. Evidence:
`/private/tmp/webjam-post75-return-after-capture.json` and
`/private/tmp/webjam-post75-return-after-*.png`. Controlled room/launcher facts
created no player and launched no Drawpile. This proves widget/navigation
usability, not physical decoding, canvas participation or two-computer behavior.

Inherited Art Notes pulse/guidance and Jamulus-shaped chat remain outside this
slice; preserving an unsent draft is not a claim that Art chat now works.
The existing status-strip priority can still show missing-copy guidance while
the returned room reports Reconnecting. This change exposes that current room
and its recovery; it does not rewrite strip priority. Drawpile installation
refresh, host keyboard video seeking, #74 canvas publication and #75 dual
activity routing are separate or baseline work, not newly implemented claims.

The open draft must record its final SHA, exact-tip hosted run/job links and
all four desktop artifact records. Hosted **SUCCESS remains required** before
the Bob handoff; local proof does not substitute for it.

Holds: Art Preview; own tools; existing silent local-file Paint along; Webex
talk/share first-class and separate, never on the Art door. No automatic
meeting/canvas launch, second video stack, short-code/public rendezvous or
default Session Help product claim. Music audio remains evidence-based.
Physical/public/live-provider/installed-owner-package gates NOT RUN. Unsigned
0.27.2 remains Jeff-only. No merge/tag/sign/release/Pages/Release Trust.
#37/#49 parked; protected branches untouched. Canonical WebJam only. One
OPEN DRAFT for Karen, then coord AFTER and agent:none/FREE.
