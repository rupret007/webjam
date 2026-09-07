# PRE_KAREN — guest work stays visible when Paint along arrives

Base `50e035e09997e891f08520d80d60cfae3383ce27`; fresh
`codex/art-guest-video-offer`; canonical WebJam checkout.
Marker: `OVERNIGHT_WEBJAM_CONTINUE_20260907_0215`.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5566904922.
Base-advance BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5567377754.

## Product and ten-second self-QA

The first host video offer previously replaced the guest’s embedded workspace,
even when the guest was already writing in Notes or using Conversation. The
pre-change baseline on master `159f4447` reproduced six such failures, with two untouched-Room cases
already passing. This was a workspace replacement despite the old request’s
no-activation flag.

Now the first automatic guest presentation checks the actual visible work.
Notes, Conversation, another workspace, an active dialog, or a menu keeps its
place. The existing room/presence action carries the offer. The automatic
announcement is consumed when deferred, so closing a dialog or returning to
Room later does not create a delayed navigation surprise. One deliberate
Paint along action opens the existing file controls.

Cold entry from the untouched Room and the host’s selected Paint along start
still open directly. No extra door, confirmation, prompt or player is added.
If canvas recovery owns the room chip, Paint along remains a separate existing
action in Room. Withdrawal removes the video route; a replacement does not
interrupt the guest’s notes.

## Ownership and security self-QA

- The controller still requires the current authenticated video binding and a
  connected, unblocked guest room before considering automatic presentation.
  The window answers only whether presentation would replace current work.
- Deferral changes no room/profile identity, peer payload, media state, URL,
  launcher, private field, timer or log sink. Existing explicit activity
  dispatch re-reads the current room before opening the panel.
- Opening a panel loads no file and launches no meeting. The existing file
  chooser, matching-copy checks, silent player and host transport remain the
  owners of those actions; guests receive no seek authority.
- Real Qt/controller journeys preserve focus, local notes, selection, undo and
  save state on compact and wide windows. A pending meeting handoff is driven
  through the existing explicit controller action with a controlled producer;
  a background video offer does not issue another handoff or change its state.
- Dialog/menu focus stays with its current owner. Dismissal, later room ticks,
  withdrawal and replacement do not replay a deferred automatic presentation.
- Tests use temporary settings/notes/databases, isolated credential storage,
  synthetic room receipts and controlled launch/player fixtures. Private marker
  names stay out of room projections, accessibility descriptions and logs.

## Verification and honest limits

New regressions cover both LAN and native guests, Notes/Conversation/pending
handoffs at two widths, modal/nonmodal dialogs and menus, withdrawal and
replacement, a secondary video action behind canvas recovery, cold Room entry
and the host’s selected start. Existing Art room return, activity, meeting,
Paint along opening and guest seek regressions remain in the focused suite.
Final counts, commands, exact tip/tree and actual hosted test/integration/four
desktop results belong in the OPEN DRAFT and coord AFTER.

Live meetings, actual Webex activation, native OS focus behavior, installed-app
feel, physical playback, signing/notarization and platform trust are **NOT RUN**.
A local focus/workspace test does not prove an external meeting’s membership,
selected window or screen sharing. This is Codex self-QA, not independent review
or Karen PASS.

#88 passed Karen and was leftover-squashed by Bob onto this base. Its reviewed
tip was `db82cb1f4fae04dced16a1785a6f82cada8ec7b5`; its completed handoff is
https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5566652085.
The saved-meeting behavior comes from that reviewed base. #37/#49 stay parked;
#86/#87/#88 remain merged. Make together + Paint along → Host/Join and the
squirrel-with-fro art are unchanged. No merge/squash/tag/sign/Pages/Release
Trust/Publish/release/deploy/spend/live Cisco/public rendezvous/other-repo lane.
Unsigned 0.27.2 stays Jeff-only. Stop for Karen leftover + security + WebJam
ten-second UX on the exact draft tip; Bob only after PASS with tip MATCH.
