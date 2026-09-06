# Worth Building — keep an open Art Paint along panel tied to the room

A guest opens Paint along, then loses the WebJam room connection. The room
overview correctly hides Open, but the already-open panel still said
**Following the host** or **You are still in the room** and still offered
Show/Hide or Open my copy. A queued Hide or file chooser could change local
follow state after a native runtime had failed or the LAN observer's
five-second receipt had expired before the UI caught up.

This fails the ten-second test: the room says reconnecting while Paint along
still invites the artist to follow an unconfirmed host pulse. Fifteen new
actual controller journeys failed on fetched master
`6073a30a51cd1f616527c4b376caeeaaf9cb6037`; they pass with this fix.

The guest panel now offers **Return to room** while its room or current video
receipt is unavailable. Returning hides only this WebJam panel, preserving the
room owner, notes, and the local silent copy. A current host receipt restores
the latest follow action, including replacement or withdrawal. Dispatch checks
the actual source, binding, generation, coordinator and panel, including the
interval before queued UI updates arrive. Auto-open waits for a confirmed room.

This is a guest connection/receipt slice, the Paint along sibling of #82's
canvas binding. Host Paint along transport, #82 canvas recovery, and #81
Leave/rejoin/invitation/cleanup remain intact. No new transport or video
stack, automatic external launch, door copy or asset change. Webex stays beside
WebJam. Public diagnostics receive no invitation, canvas URL, Notes, filename
or raw exception. Existing bounded rotating logs remain in use.

Reviewed but still deferred: Add Link from Talk & share currently opens generic
Settings with an empty Conversation section collapsed. That remains a separate
contextual-navigation leftover, not part of this draft.

Canonical checkout `/workspace`; branch
`cursor/art-paint-along-room-binding-f095`; BEFORE
[5562041853](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5562041853),
2026-09-06 15:41 CDT. Parked #37/#49 are preserved. Draft only; no merge,
tag, signing, Pages, release, publishing or GitHub Latest. Physical,
installed-package and live-provider proof remains NOT RUN.
