# Pre-Karen QA — Music Host/Join network recovery

Canonical checkout `/Users/jeffstory/Documents/WebJam`; branch
`codex/webjam-finish-product-music-recovery`; exact master base
`8d708d568ff20d43c4850b44729f5957226c8e6d` after Bob's #79 squash.
BEFORE [5558440677](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5558440677)
and [addendum 5558444758](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5558444758)
claim this same task through 08:52 CT on 2026-09-06. Marker
`OVERNIGHT_NEXT_MUSIC_RECOVER_20260906_0449`. Local master was fast-forwarded
as requested; old #79, other original branches and four stashes are preserved.

## Product and ownership

A Music host's copyable invite now matches its actual listener. Missing or
changed Wi-Fi suppresses stale Copy. Same-address return preserves the listener
and credentials; Try Again can replace an idle listener on a usable changed
address only after stop returns True and the retained listener is inactive.
Failed restart cannot silently downgrade a previously authenticated room to a
legacy invite. Missing-address retry does not retire the current room.

Audio and the current conductor attempt are preserved. Recording, capture arm,
completed takes and outstanding Local Originals obligations prevent peer
replacement; the existing Stop Recording/finalization or End Session action
owns that work. Failed cleanup points to the actual Try End Session/Try Leave
Jam header action. New Record and Shared Track playback cannot start during
listener replacement or cleanup; existing Stop Recording remains usable.

A running guest's retry enters the existing bounded Bridge supervisor. Repeated
clicks reserve one request, retain capture/invitation ownership and never create
a sixth retry after exhaustion. Authenticated Music discovery requires confirmed
observer cleanup and checks generation/owner identity before handing off. End,
Quit or replacement invitation cannot be undone by a late callback. Central
peer cleanup checks captured identities between owners before clearing state.
Native invitation ownership and Art's existing routes stay separate.

Logs use the existing `webjam` hierarchy and rotating redacted handler (1.5 MB,
three backups). Fixed events distinguish loss/return, deferred retry,
requested/completed/abandoned replacement and unconfirmed cleanup. An isolated
real file-handler round trip checks event counts and absence of private invite,
address, Notes and exception payloads. Independent source review traced lower
stop exceptions through the configured handler's traceback/exception redaction.

## Verification

Final native Music journeys: **51 passed in 9.75s, Cocoa, exit 0**. The tests
exercise real controllers, conductor snapshots, invite serialization and Bridge
retry accounting; OS listener/process boundaries are controlled. Two asserted
760×600 captures cover Conversation closed/open. Root inspected readable,
reachable Try Again and End with no clipped recovery copy. No meeting launches.

**Final local gate and PRE_KAREN leftover/security/UX: PASS.**

- Full unfiltered suite: **8,471 passed, 26 existing skips, 99 subtests,
  3 dependency deprecation warnings, 397.75s, exit 0**. One process:
  `QT_QPA_PLATFORM=offscreen .venv/bin/pytest -vv`.
- Final focused Music/Art/room/invite/transport/recording/logging suite:
  **64 modules, 2,268 passed, 2 existing skips, 2 subtests, 116.58s, exit 0**.
- Required Ruff, compileall, pip check, runtime dependency policy, UX smoke
  and diff checks passed. All six source/test hashes stayed fixed throughout.
- Independent leftover/security and UX reviews matched that same six-file
  freeze; both PASS. Both native compact captures were independently inspected.
- All 45 protected original branch refs and four stashes match the pre-work
  record. Local master alone was advanced to the authorized base before the
  new branch was created. The old #79 branch remains `d1314d3d8db3b545c75fe570802169b04c5d26d6`.

Exact-tip hosted SUCCESS and four desktop artifacts remain a separate gate.
Checkout SHAs, trees, job IDs and artifacts will be verified and recorded in the
OPEN DRAFT and coord AFTER. Local checks are not release-package evidence.

## Failure and review history

- Before implementation: guest handoff/retry matrix **8 expected failures,
  2 passes**; native host baseline **1 expected failure**, returning a nonempty
  invite for a changed address while the old listener remained bound.
- First corrected guest matrix: **10 passed**. Expanded native matrix exposed
  stale retained-work HUD Copy, nested End/new-invite cleanup races and fixture
  header refresh issues. Those were fixed; the stable expanded run passed
  **44 cases**. The earlier mixed run is not final proof.
- UX review regressions reproduced **8 failures / 2 passes**: lost network
  displaced Stop/finalization, failed cleanup offered an ineffective Try Again,
  and retained-work wording differed from End Session. Corrected native run:
  **46 passed**.
- Guest cleanup label regression reproduced **1 failure**, then was aligned to
  the actual Try Leave Jam action; next native run **47 passed**.
- Recording safety review reproduced **2 failures / 2 Stop-preservation passes**:
  new-take dispatch remained possible during failed listener cleanup and a
  synchronous replacement callback. The idle Record owner and existing Shared
  Track lifecycle gate now block those transitions. Final native **51 passed**.

Raw logs, source hashes, screenshots and verification manifests are retained
under `/private/tmp/webjam-post79-*`. The temporary capture plugin initially
hooked a duplicate imported module and produced no captures; it was corrected
to use the actual pytest module, then both images were captured and inspected.
No tests were excluded or weakened to obtain final passing evidence.

Draft only for Karen leftover+security+UX; AFTER and agent:none, stop for Bob.
Parked #37/#49 untouched; stay off #67. No short-code/public rendezvous,
merge/tag/sign/Pages/Release Trust/publish/GitHub Latest. Unsigned 0.27.2 is
Jeff-only. Art Preview, own tools, silent local Paint along and Webex beside
WebJam remain unchanged. Physical/live-provider/two-device/installed-package
checks NOT RUN. Black/white/neutral gray/burnt orange. WebJam only.
