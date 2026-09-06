# Worth Building — keep an open Art canvas panel tied to the room

A guest opens Shared Canvas, then loses the WebJam room connection while the
host changes or stops offering that canvas. The room overview correctly hides
Open, but the already-open canvas panel still offered its retained invitation.
A queued Open even launched Drawpile after a native runtime had failed or the
LAN observer's five-second receipt had expired before the UI caught up.

This fails the ten-second test: the room says reconnecting while its canvas
panel still invites the artist into an unconfirmed activity. Ten new actual
controller journeys failed on fetched master `171219a80935e80f4e00fbfeb59c8cc4f13eaee9`;
all ten passed with the initial fix. Raw baseline and follow-up results are in
`/private/tmp/webjam-post81-baseline.log` and `webjam-post81-first-fix.log`.

The guest panel now offers **Return to room** while its room or current canvas
receipt is unavailable. Returning hides only this WebJam panel, preserving the
room owner, notes, local video copy and any work already open in Drawpile. A
current host receipt restores the latest offered canvas; a withdrawal removes
Open. Dispatch checks the actual source, binding, generation, coordinator and
panel, including the interval before queued UI updates arrive.

This is a guest connection/receipt slice. Host canvas publication and its
share/change/stop/retry behavior remain the reviewed #74 implementation; #81's
Leave/rejoin/invitation/cleanup logic remains intact. No new transport or video
stack, automatic external launch, door copy or asset change. Webex stays beside
WebJam. Public diagnostics receive no invitation, canvas URL, Notes or raw
exception. Existing bounded rotating logs remain in use.

Reviewed but deferred: Add Link from Talk & share currently opens generic
Settings with an empty Conversation section collapsed. That is a separate
contextual-navigation improvement, not part of this draft.

Canonical checkout `/Users/jeffstory/Documents/WebJam`; branch
`codex/webjam-finish-product-art-room-actions`; BEFORE
[5559660806](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5559660806),
2026-09-06 08:50–12:50 CT. Bob's #81 squash tree matches the reviewed #81 tip;
its source branch and parked #37/#49 are preserved. Draft only; no merge,
tag, signing, Pages, release, publishing or GitHub Latest. Physical,
installed-package and live-provider proof remains NOT RUN.
