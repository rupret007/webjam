# Worth-Building — return to the current Art room

2026-09-05 CT. Branch `codex/webjam-finish-product-art-continuity`; exact base
`91567b3c3ba6c33836bc67c040142ddd65702bc3` (post-#75 squash).
[Coord BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5556458377).
#3 body and latest BEFORE/AFTER were read before claiming the free four-hour
lane. Canonical checkout was clean. #75/#74 branches and four stashes are held.

**Source/product Worth-Building: PASS.** An artist taking notes or following a
process video needs an explicit way back to the current room. At compact
sizes, Notes hides the room. Paint along's **Back to room** restores only the
outer workspace container, so it returns that artist to Notes again. The room
and both of #75's activity actions remain hidden. Notes itself has no direct
room-return action; Conversation also changes the view, but exposes unrelated
meeting controls to reach the room.

| Artist task | Verified current behavior | Required behavior |
| --- | --- | --- |
| Open Paint along from Notes, then return to the room | Real LAN guest/controller, actual Notes action, actual Paint along action and actual Back button: the room stays hidden, Notes stays visible, and the selected content remains `canvas`. Both offered activity routes exist in the model but cannot be seen. | Back to room selects the existing full room view, showing current room/cleanup facts and available activity actions. |
| Return after the host shares a video and canvas while the guest is taking notes | A second real guest journey receives the first video offer while Notes is active, then a canvas receipt while Paint along is shown. Back to room again restores Notes. The room is CONNECTED and both activities exist; the wrong destination is navigation, not missing transport evidence. | Explicit return reveals the latest room state without starting either activity, reopening an external app or changing the guest's saved creator profile. |
| Leave Notes to inspect a changing room | The side rail is hidden, the More menu has Notes but no room route, and compact Notes hides the room stage. There is no direct room-return control on Notes. | Art Notes has a clear Back to room action. Notes, cursor/undo state and local persistence remain intact so the artist can continue later. |

The native baseline probe is `/private/tmp/test_webjam_post75_return_probe.py`,
with two passing assertions of the existing defect and JSON/PNG evidence at
`/private/tmp/webjam-post75-return-before-{False,True}.*`. It uses existing
isolated test fixtures, synthetic host receipts and no media decoding or
external app launch. The root independently reproduced and inspected the
compact result after a separate source audit found the same chain.

Build one return-navigation slice: a shared, current Art room return intent
for Notes and Paint along. Reuse the existing stage route and room projection;
reject callbacks from retired video panels and after profile/shutdown changes.
Returning must remain possible during room loss or unfinished cleanup so the
artist can see the real next action. Preserve notes, local copy/player state,
connection ownership and the existing separate Conversation visibility.

Proof: LAN and native guests; saved Music/Art preferences; explicit and first-
offer presentation from Notes; current room receipt, withdrawal, loss and
cleanup; retired callbacks; compact/normal keyboard and focus; no launch,
reload or lost notes. Focused Art/door/session/invitation tests, full raw pytest,
UX smoke, Ruff, compileall, pip check, PRE_KAREN leftover/privacy review and
exact-tip hosted SUCCESS with four desktop builds remain mandatory.

Compact self-QA also reproduced a base-branch Notes toolbar overlap: two
52-pixel tool rows received only 64 pixels of height. The new quiet return
keeps the original 34-pixel header height; Art-only compact tool sizing and
spacing keep every existing action reachable. Normal sizes and other profiles
restore their original geometry. This changes layout, not notes/chat/guidance
semantics.

Deferred distinct gaps: Art Notes chat/guidance still includes Music-shaped
behavior, host keyboard video seeking, and refreshing an open canvas after
local Drawpile installation. Do not combine these into this return slice.
#74 publication/share/change/stop/retry and #75 dual activity routes are baseline.

Holds: Art Preview; own tools; silent local-file Paint along; Webex talk/share
first-class beside WebJam, never on the Art door. No automatic meeting/canvas
launch, second video stack, short-code/public rendezvous/default Session Help
claim, merge/tag/sign/release/Pages/Release Trust. Unsigned0.27.2 Jeff-only;
physical/public/live-provider/installed-owner-package gates NOT RUN. #37/#49
parked; protected branches untouched. Canonical WebJam only. One OPEN DRAFT
for Karen, then coord AFTER and agent:none/FREE.
