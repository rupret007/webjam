# Pre-Karen QA — current-room Paint along recovery for Art guests

Canonical checkout `/workspace`; branch
`cursor/art-paint-along-room-binding-f095`; fetched master base
`6073a30a51cd1f616527c4b376caeeaaf9cb6037` (post-#82). BEFORE
[5562041853](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5562041853),
2026-09-06 15:41 CDT; grok Cloud lease through 19:41 CDT.

## Product and ownership

An open guest Paint along panel follows actual room availability. During loss
it shows **Return to room**, hides follow/open/hide chrome and the unconfirmed
clock, and preserves the current room, local notes and silent local copy.
Return hides this WebJam panel and exposes the existing room recovery
guidance. A current receipt restores the latest video offer, including
replacement or withdrawal. Auto-open does not present Paint along while the
room is unconfirmed.

Open rechecks current source availability, room generation, binding, current
panel/coordinator, and observed video projection. Native availability checks
can report loss; ownership is rechecked afterward. LAN receipt freshness also
covers the interval before queued UI callbacks. Rendering does not probe the
native backend, launch a process, or alter conductor facts. Public diagnostics
receive no new field, invitation, URL, Notes, filename or raw exception.
Existing rotating/redacted logging remains in use; no additional payload
logging.

## Leftover notes

- This is the Paint along sibling of #82 canvas room binding. Host canvas
  publication (#74), host Paint along transport, and #81 Leave/rejoin/cleanup
  are unchanged.
- Talk & share **Add Link** still opens generic Settings with Conversation
  collapsed when no meeting link is saved. Still deferred; not this slice.
- Art door remains Make together + Paint along, then Host/Join. Preview/Webex
  stay off the door. No harvest/copy change.
- Art still does not show a named artist roster. Connection copy stays honest
  about that limit.
- Host Paint along while the host LAN route is interrupted is out of scope.

## Security notes

- Return-to-room copy is generic. It does not name the file, invitation,
  session key, Drawpile URL or meeting link.
- Companion projection and room overview stay on the existing allowlists.
- Queued Hide / Open my copy / Close my copy cannot act on a retired panel or
  an unconfirmed receipt.
- No new log sink. Existing rotating redacted logging is unchanged. Native
  exceptions are not interpolated into diagnostics.
- Local copies are not closed, moved or re-read as a side effect of room loss.
  Drawpile and meeting launch stay off this path.

## UX notes

- Loss replaces follow chrome with a 48 px **Return to room** action that
  Space-key activates. Repeated HUD ticks do not churn the wording.
- Compact 760×600 keeps the action on-screen beside the retained workspace.
- Restored receipts restore the current follow action: same offer, replacement
  (mismatch/open), or withdrawal (no follow action).
- Back to room / stage navigation still returns without ending the room or
  discarding the local copy.
- Keyboard and mouse both use the same return signal as the existing Back
  control.

## Verification

- Fixture-first: fifteen controller failures plus UI/coordinator failures on
  master; they pass after the fix.
- Related Art/Paint along/canvas/native selection: **399 passed**.
- New Paint along + video UI/coordinator: **106 passed**.
- Full `pytest` (`testpaths = tests`): **8,542 passed**, 27 existing skips,
  99 subtests, 7 environmental warnings, 487.04s, exit 0.
- compileall and `pip check`: **PASS**. Ruff is not installed in this Cloud
  runner; hosted CI owns that gate.
- Hosted proof will be added to the PR body for the exact committed tip; no
  hosted success is inferred from local results.

## Scope and limits

Three product files, one new regression module, and two focused test updates.
Host publication, #81 Leave/rejoin/cleanup, parked #37/#49, door copy and
artwork remain outside the diff. Unsigned 0.27.2 is Jeff-only. Draft for Karen
leftover/security/UX; no merge, tag, signing, Pages, Release Trust, publish or
GitHub Latest. Physical, two-device, installed-package and live-provider
checks **NOT RUN**.
