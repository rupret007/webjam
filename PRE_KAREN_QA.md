# Pre-Karen QA — Art host room connections

Branch `codex/webjam-finish-product-art-room-presence`, exact master base
`3f11f9d198c2ac7775c3b8b7730cb236fbf77c7b`. Canonical checkout:
`/Users/jeffstory/Documents/WebJam`. BEFORE
[5563025186](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5563025186).

## Worth-Building and product review — PASS

When Alex and Sam enroll but only Sam connects, the Art LAN host previously
saw only “An artist has connected.” The room now shows Sam's chosen name.
Enrollment alone adds no row. Fresh authenticated room reads supply the list;
the existing five-second expiry removes stale connections. Duplicate chosen
names remain distinct connections. Native hosts and guests retain their
honest unavailable-list guidance; this is not a complete cross-transport roster.

The list keeps every name in a viewport of at most four rows, preserves
keyboard selection and pointer scrolling as membership changes, and reveals
the focused action. A small room's names and Conversation fit at 760×600.
Names, existing activities, and Conversation remain reachable in a short room.
No Art door, Paint-along asset, canvas publication, meeting stack, or wire
protocol changes. The #81–#84 work and parked #37/#49 remain outside this slice.

## Ownership, privacy and leftover review — PASS

- Host, server, room generation and host lifecycle are captured before route
  and reader callbacks and checked again afterwards. Old work cannot overwrite
  the replacement room's state or lend it old connection evidence.
- Rendering reuses a recent route observation from the normal room tick; it
  performs no additional OS network probe. A busy enrollment lock yields
  immediately instead of holding the UI behind a disk write. Server expiry
  and stop are rechecked after the name projection.
- End, failed cleanup/Quit, changed network, room replacement and profile
  change retire names. The next valid observation can restore current names.
- The dedicated immutable projection constructs no credential-bearing
  enrollment records or tokens. Names stay in the private list, outside the
  public room summary, companion payloads, repr, logs and support facts.
  Existing printable, 80-character name bounds remain in force.
- Existing rotating/redacted logging and bounded network-transition messages
  remain unchanged. No per-poll log noise, new sink, private name/ID payload,
  automatic meeting launch, video player or canvas launch was introduced.
- Independent read-only security, ownership, leftover and UX reviews: PASS.

## Verification

Fixture-first failures reproduced missing names, lock blocking, expired
projection data, owner reentry, keyboard selection visibility and compact
layout. Final verification used frozen product/test file hashes.

- Native Cocoa: **33 passed**, 10.31s. Actual 1040×720 and 760×600 fixture
  windows captured; compact room, Conversation and keyboard return inspected.
- Focused 90-module Art/door/session/invite/Music recovery/privacy suite:
  **2,924 passed**, 2 existing skips, 18 subtests, 149.06s.
- Ruff, compileall, pip check, runtime dependency policy and UX smoke: PASS.
- Full pytest: **8,613 passed**, 26 existing skips, 99 subtests, 3 existing
  deprecation warnings, 452.15s.
- Exact-tip hosted tests/integrations and all four desktop builds are the final
  handoff gates. Completed run, tip/tree and artifact evidence belong in the
  PR description and coord AFTER; local success alone is not a green handoff.

## Holds

One OPEN DRAFT for Karen. Parked #37/#49 and predecessor branch refs/stashes
are preserved. Unsigned 0.27.2 is Jeff-only; no merge/tag/sign/Pages/Release
Trust/publish/GitHub Latest. No short-code, public rendezvous, live Cisco or
second video stack. Webex stays beside WebJam and off the Art door. Physical,
two-device, installed-release and live-provider verification remain NOT RUN.
