# Pre-Karen QA — Paint along guest copy recovery

2026-09-05 CT. Branch `codex/webjam-art-activity-clarity`; verified base
`f27a6344abc18ec3af990d43827d4c74f869a088` (post-#72 squash).
[Initial BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5555193410)
and [master-refresh BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5555306061).
The source audit started at cf311470. #72 landed during implementation; only
this new unpublished branch was refreshed. Its invitation/retry/Leave work
is baseline and its branch remains untouched.

The guest can use **Open my copy…** to follow the host's next video or recover
a moved or failed local copy while staying in the Art room. A known local
player error cannot be presented as **Following the host**. This is one
copy-replacement and playback-recovery slice; host keyboard seeking and
optional-canvas publication remain deferred.

| Claim challenged | Product evidence |
| --- | --- |
| The offered action accepts a replacement | Real coordinator and actual Qt chooser/button journeys replace host video A with B and reopen a moved copy. One existing muted player is reused; a different player still requires cleanup. |
| A local error stays local and visible | Load, play, seek, pause and position failures produce bounded local attention and an actionable retry. The Art overview keeps the room connected, offers the video route, and projects only finite state. Raw backend details and filenames stay out of diagnostics and public projections. |
| A copy is closed only after confirmed stopping | A refused pause retains the copy and its retry obligation. Close remains available for retained/failed attempts even when hidden, the host withdraws the video, or the host needs attention. Successful explicit reopen or close clears local failure. |
| Loading cannot resurrect an old room or picture | Old proof is retired before a changing source can be driven. Nested ticks cannot play it. Close, End and rebind invalidate pending load completion and callbacks. The original descriptor token binds the hashed bytes through signing and loading; path substitution at either boundary fails closed without committing proof. |
| The existing Qt adapter detects delayed failure | Controlled Qt event delivery covers error codes, InvalidMedia, subsequent position reads and playback/seek checks. Explicit load clears and reopens even the same source. Pause, stop and close stay usable after faults. Audio remains muted. |
| A guest remains an artist in the same room | Eight actual ApplicationController journeys cover saved Music/Art preferences, next-video/moved-copy replacement and four local playback faults. Real LAN observer receipts establish room state. Recovery preserves observer ownership and generation, saved profile and Art context, and starts neither Music audio nor a Webex call. |
| Recovery fits the supported compact window | Thirty geometry cases exercise recovery/following at 720×560, 760×600 and 1040×720 with normal and 125% font width. Video, actions and footer do not overlap. Long filenames are elided with full accessible/tooltip text. Actual mouse/keyboard Open and More→Close actions are covered. |

**Pre-Karen local gate: PASS.** Final verification on the post-#72 base:

- Focused Art/Paint along/door/session/invitation set: **739 passed**.
- Full raw `.venv/bin/pytest -q`: **7,342 passed, 26 existing skips,
  99 subtests passed, 3 dependency deprecation warnings** in 298.96 seconds.
  No module isolation, exclusions, extra skips, warning filters or retries.
- Required Ruff scope, compileall, pip check, whitespace and UX smoke: **PASS**.
- Controlled core/coordinator set: **131 passed**; Qt adapter health/seam/
  starts-muted set: **31 passed**. These are included in the focused/full proof.

The final native set passed **117 tests**, including guest journeys, compact
UI and command authority. Eight final Cocoa renders at 720×560 and 1040×720
were inspected; these use synthetic host/player facts and establish widget
usability, not physical codec or multi-computer playback performance.
Independent source and token-boundary reviews found no actionable issue.

Self-QA retained the existing compact-label length limit and fixed the new
label to fit it. A partial-controller test now spies on immediate presentation
intent while real-window tests exercise actual recovery; existing no-wrong-
surface assertions remain. Known false-Following expectations now require
local attention, retained ownership, confirmed pause and successful recovery.
The old 220px embedded-video minimum assertion was replaced by actual
video/transport/action/footer nonoverlap checks. No new skip or test exclusion. Final review also reproduced four post-hash
substitution failures before the fix; the verified descriptor token now spans
that boundary, without adding private identity data to wire state or diagnostics.

The open draft will record its final SHA, matching hosted run/job links and
four desktop artifact records. Exact-tip hosted SUCCESS with all four builds
is mandatory before the Bob handoff; local results do not substitute for it.

Holds: Art Preview; own tools and existing silent local-file Paint along;
Webex conversation/share separate and never on the Art door. No automatic
meeting/canvas launch, second video stack, short-code/public rendezvous or
default Session Help product claim. Music audio remains evidence-based.
Physical/public/live-provider/installed unsigned-package checks NOT RUN.
Unsigned 0.27.2 remains Jeff-only. No merge/tag/sign/release/Pages/Release Trust.
#37/#49 parked; #67 untouched. Canonical WebJam only; all prior stashes and
protected branch tips preserved.
