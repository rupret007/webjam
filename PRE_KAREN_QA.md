# Pre-Karen QA — shared canvas publication and retry

2026-09-05 CT. Branch `codex/webjam-art-making-honesty`; exact base
`95536f31041199b63f1c3d962f87387af2905130`, the verified #73 squash with tree
MATCH to reviewed f5260311. [Coord BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5555625262).
#72/#73 behavior is baseline; their branches and all four stashes are preserved.

An Art host can change the canvas invitation while the previous offer stays
available. Unconfirmed sharing or stopping retains that offer and exposes an
explicit retry, both in the panel and through the room overview. Publication
acceptance does not establish guest delivery, Drawpile connection or painting.

| Claim challenged | Product evidence |
| --- | --- |
| The host and guest use an accepted invitation | Real parser, wire model and host/follower journeys carry a valid long hostname with the complete joining URL. Only display labels are bounded; the existing wire limits remain unchanged. Guests open the accepted invitation only on explicit action. |
| Failed sharing or stopping stays recoverable | None, False, exceptions, unavailable publishers and mismatched typed receipts preserve the accepted canvas plus a private pending intent. Explicit retry confirms or remains pending. Stop can replace a pending share; a confirmed stop removes the offer without closing Drawpile. |
| Native receipts reflect retained room state | Rejected candidates never replace the accepted cache or leak into unrelated room/video publications. The receipt also checks the latest accepted full-state revision: an older canvas completion cannot claim success after a newer accepted update supersedes it. |
| An old operation cannot affect a new room | Duplicate retry, current-owner changes, End, guest/new-host rebinding and newer intents are covered. Retry is visibly disabled before external calls; callbacks can retire or replace the intent before an old send. End forgets the pending capability. |
| Recovery belongs to the same Art room | Eight actual ApplicationController host/guest journeys cover saved Music/Art preferences, long invitations and rejected first-share/replacement/withdrawal. Room identity, generation and saved profile remain stable; Music audio and Webex are not launched. |
| Private payloads remain private | The masked paste clears after submission/cancel. Pending URLs are absent from labels, accessibility text, diagnostics and finite companion projections. Unexpected canvas action failures log only bounded text. Local Drawpile launch failure cannot erase an accepted room offer. |
| The controls remain usable in compact windows | Actual Change, Cancel, Share, Retry, Stop and guest Open actions are exercised. Twenty-four geometry cases cover compact/normal parents and 100/125% font width. Long labels use right elision with the Canvas offered prefix and full bounded tooltip/accessibility text. |

**Worth-Building + Pre-Karen local: PASS.** Final frozen-source verification:

- Focused Art/door/session/invitation/canvas set: **1,205 passed**.
- Full raw `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q`: **7,477 passed,
  26 existing skips, 99 subtests passed, 3 dependency deprecation warnings**
  in 253.94 seconds. No module isolation, exclusions, extra skips, warning
  filters or retries in this full run.
- Native Cocoa application journeys and UI: **93 passed**. Twelve final native
  recovery/accepted/change renders at 400/600px were inspected with compact
  720×560 and normal 1040×720 parents. These use controlled publishers and
  launchers; they do not certify physical Drawpile sessions.
- Pure coordinator/core set: **124 passed**; native publication set: **44
  passed**, including twelve nested full/video publication cases. These are
  included in the focused/full proof, not additional test totals.
- Required Ruff scope, compileall, pip check, whitespace and UX smoke: **PASS**.
  Final independent source/receipt/privacy review found no actionable issue.

Self-QA reproduced four false native receipts before the latest-revision fix.
Visual review replaced clipped wrapping, then preserved the status prefix
through right elision. Existing fake publishers now return typed receipts;
old false-success expectations require pending recovery. Assertions were
strengthened; no new skip or exclusion was added.

The draft records its exact SHA and hosted run/job/artifact links. Exact-tip
hosted SUCCESS, including Windows x64, Linux x64, macOS arm64 and macOS x64
Build Desktop, is mandatory before coord AFTER and lease release.

Holds: Art Preview; own tools; silent local-file Paint along; Webex conversation
and share separate, never on the Art door. No automatic meeting/canvas launch,
second video stack, short-code/public rendezvous/default Session Help claim,
merge/tag/sign/release/Pages/Release Trust. Music audio remains evidence-based.
Physical/public/live-provider/installed-package checks NOT RUN. Unsigned 0.27.2
Jeff-only. #37/#49 parked; #67 untouched. Canonical WebJam only.
