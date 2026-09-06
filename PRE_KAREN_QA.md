# Pre-Karen QA — current-room canvas recovery for Art guests

Canonical checkout `/Users/jeffstory/Documents/WebJam`; fresh branch
`codex/webjam-finish-product-art-room-actions`; fetched master base
`171219a80935e80f4e00fbfeb59c8cc4f13eaee9`. Bob landed #81 with the reviewed
`b94e8b53` tree before this branch; its source ref remains unchanged.
BEFORE [5559660806](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5559660806),
2026-09-06 08:50–12:50 CT; marker `OVERNIGHT_NEXT_ART_DOOR_20260906_0840`.

## Product and ownership

An open guest canvas panel follows actual room availability. During loss it
shows **Return to room**, hides an unconfirmed clock, and preserves the current
room, local notes, video copy and external Drawpile work. Return hides this
WebJam panel and exposes the existing room recovery guidance. A current receipt
restores the latest canvas offer, including replacement or withdrawal.

Open rechecks current source availability, room generation, binding, current
panel/coordinator, and observed canvas projection. Native availability checks
can report loss; ownership is rechecked afterward. LAN receipt freshness also
covers the interval before queued UI callbacks. Rendering does not probe the
native backend, launch a process, or alter conductor facts. Public diagnostics
receive no new field, invitation, URL, Notes, filename or raw exception. Existing
rotating/redacted logging remains in use; no additional payload logging.

## Verification

- Fixture-first: ten failures on master; ten passed after the initial fix.
- Expanded actual-controller matrix: 15 cases. Existing canvas/host/activity
  regression selection: **103 passed in 9.07s**.
- Native Cocoa: **15 passed in 5.79s**. Synthetic controlled room and launcher;
  no real provider or hardware. Root inspected actual native recovery-panel
  and 760×600 return screenshots. Visible readable action, Space-key activation,
  stable repeated renders, and preserved workspace all pass.
- Independent read-only leftover/security and UX audits: **PASS** on the same
  four source/test hashes frozen in `/private/tmp/webjam-post81-frozen-source.json`.
- Ruff, compileall, pip check, runtime dependency policy, UX smoke: **PASS**.
- Focused suite: **73 modules; 2,515 passed, 2 existing skips, 5 subtests,
  131.04s**, exit 0.
- Full unfiltered pytest: **8,526 passed, 26 existing skips, 3 dependency
  deprecation warnings, 99 subtests, 418.40s**, exit 0. Source/test hashes
  remained unchanged throughout all final gates.
- Hosted proof will be added to the PR body for the exact committed tip;
  no hosted success is inferred from local results.
- Preservation: 75 original local branch/tag refs audited; 74 unchanged, only
  local master advanced to the verified base. All four stashes match.

## Scope and limits

Three product files plus one new regression module. Host publication, #81
Leave/rejoin/cleanup, parked #37/#49, door copy and artwork remain outside the
diff. The coordinator retains only its already-private typed canvas projection;
the UI caches the existing follow snapshot. No second video stack, new peer
protocol, short-code, public rendezvous, automatic canvas or meeting launch.
Webex remains first-class beside WebJam and off the Art door.

Unsigned 0.27.2 is Jeff-only. Draft for Karen leftover/security/UX; no Codex
merge, tag, signing, Pages, Release Trust, publish or GitHub Latest. Physical,
two-device, installed-package and live-provider checks **NOT RUN**. Raw local
logs, manifests, screenshots and intermediate failures are retained under
`/private/tmp/webjam-post81-*`; initial failures are evidence, not final gates.
