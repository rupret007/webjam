# PRE_KAREN — Paint along host opening

Base `9845fc9069fa180ee7cf772a27069cc26ae27f2d`; branch
`codex/paint-along-host-opening`; canonical checkout
`/Users/jeffstory/Documents/WebJam`. Marker
`WEBJAM_NEW_SESSION_POST86_20260906_2213`.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5564597608.

## Product and ten-second self-QA

The host sees one primary action: Choose process video when empty or failed,
Cancel opening during the duration wait, Play when ready/paused, Pause when
playing, or Return to room after losing the current binding. At 720×560,
the real Qt view retains its action, navigation and explanatory line.
Success never automatically starts playback. Back to room preserves the
pending current-room load and local notes.

The two-card Art door, squirrel-with-fro artwork, Music door and external
Conversation/Webex demonstration workflow are unchanged. A guest remains a
follower; #86's host keyboard/wheel/mouse seek behavior remains covered by
its existing tests.

## Ownership and security self-QA

- Host core state becomes LOADING before the Qt wait. It clears old source
  facts and rejects competing share/play/pause/stop/seek operations. Withdraw
  and close retire the load generation; neither success nor failure can
  restore a cancelled source.
- The coordinator checks host object and room generation before publishing
  or rendering. A newer publication callback wins over an older notification
  and over the operation's returned result. Player creation that outlives its
  room cannot install that player into a replacement coordinator role.
- All six host intent routes check the current dialog, coordinator and room
  binding. A native file picker also checks that its Qt dialog still exists
  before emitting its result. Actual End Room/new Host and stale-picker
  regressions exercise these paths.
- The existing Qt duration wait processes user input. A posted mouse press
  and release cancels it through the real button and adapter, clears the
  backend source, and returns to Choose. The same muted player can reopen
  the same file afterward. Existing guest loading/close/room-replacement
  tests cover the shared wait's follower boundary.
- Loading maps to the existing unshared idle wire state: no new public field,
  name, path, token, digest, diagnostic payload, persistence format or timer.
  Backend failures stay bounded; the new retired-player cleanup log has no
  raw exception. No live provider, customer data or credentials are used.

## Findings corrected during self-QA

The initial 15-failure baseline proved stale loading and room replacement.
Qt tests then reproduced a deleted-dialog exception on a late file-picker
return. Publication reentrancy also exposed a stale returned Ready value
after withdrawal, even when the view had already changed. Both have direct
regressions and corrections. The new UI module explicitly activates the
existing temporary database/credential fixtures; tests do not use Jeff's
persistent application database.

## Verification and handoff boundary

Final local counts and exact tip/tree belong in the OPEN DRAFT and coord AFTER,
together with actual hosted results for tests, integrations and all four
desktop builds. A pending or failed hosted check is never called green.
This document records Codex self-QA, not independent review or Karen's verdict.

File hashing remains synchronous; the responsive cancellation proof covers
the Qt duration wait. Physical codec playback, installed-app feel, two-machine
sync, live meetings and platform trust are **NOT RUN**. Stop at one draft for
Karen leftover + security + UX. Parked #37/#49 remain untouched; #86 stays
merged. No merge/squash/tag/release/deploy/Pages, signing, Release Trust,
Publish, spend, live Cisco, public rendezvous or second video stack. Unsigned
0.27.2 remains Jeff-only.
