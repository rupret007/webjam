# Worth Building — a host can safely open a Paint along video

Base: post-#86 master `9845fc9069fa180ee7cf772a27069cc26ae27f2d`.
Branch: `codex/paint-along-host-opening`. Checkout:
`/Users/jeffstory/Documents/WebJam` only.
Marker: `WEBJAM_NEW_SESSION_POST86_20260906_2213`.

## The leftover and its value

While the host's player waited for a video's duration, WebJam still presented
the previous idle/playing state. Reentrant controls could drive the changing
source. A cancelled load could publish the old video into a replacement room
and deliver a stale Ready snapshot. The fixture-first baseline reproduced
15 failures and one pass before the fix.

This outranks adding guest roster detail: it changes whether the host's next
action works and whether another room receives the right video. #86 already
fixed timeline seeking; another seeking or door-decoration change would not
address this failure. No stronger Music defect was needed to justify this
Art slice.

## Before and after

- Before: Choose or Play remained the apparent action during loading. A late
  load or native file-picker result could outlive its room or dialog.
- After: the view says **Opening process video…** and offers **Cancel opening**.
  **Back to room** remains usable during the Qt duration wait. The old picture
  is withdrawn while the new file opens; success offers **Play**, and a
  missing or failed file returns to **Choose process video…** with the error.
- Ending, withdrawing, replacing the room, or retiring the dialog invalidates
  the pending result. The operation's return value, visible controls and peer
  state all follow the current owner. An unavailable host control offers
  **Return to room**. Guests still have no seek authority.

This uses the existing silent player and existing idle/ready peer states.
Loading is local state, not a new transport or media distribution format.
The Art door remains Make together + Paint along, then Host/Join. External
Conversation/Webex talk and screen sharing remain available beside WebJam.

## Evidence and limits

`tests/test_paint_along_host_opening.py` exercises reentrant loading,
cancellation, replacement, failed loading, player-factory retirement and
withdrawal during publication. `tests/test_paint_along_host_opening_ui.py`
uses the real application, room controls, view and queued Qt mouse events,
including cancellation inside the actual duration wait, destroyed-dialog
file-picker return, End Room followed by a new host, and compact layout.

The final focused/full results, exact tip and hosted checks belong in the
draft PR and coord AFTER. Source fixtures use synthetic media/backends.
Actual codecs, installed packages, two-computer playback and Jeff's subjective
feel remain **NOT RUN**. File fingerprinting remains synchronous; this slice
does not claim background hashing or an instant cancellation of that scan.

BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5564597608.
