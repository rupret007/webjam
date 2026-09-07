# PRE_KAREN — guest copy-opening honesty

Base `6484150117f949deacdbdb6f3a85017f1966994b` (master after #89 leftover-squash);
branch `codex/art-guest-copy-opening`; canonical WebJam checkout.
Marker: `OVERNIGHT_WEBJAM_CONTINUE_20260907_0430` (rebased onto #89 master).
Rebase BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5573209855.

## Leftover and ten-second self-QA

The valid baseline reproduced eight failures in ten guest journeys. A selected
copy still offered another chooser during decoding; a retired native chooser
could raise a deleted-widget error. Guests now see **Opening your copy** during
the explicit attempt, with file/visibility controls withheld until completion.
The existing Back to room route can retain its navigation when decoder
callbacks process UI events. Completion restores current truth.

Sixteen real Qt/ApplicationController regressions cover both LAN and native
guests: success, bounded decoder error, host withdrawal, picker cancellation,
retired picker returns, Back to room, incomplete cleanup and panel replacement
inside a load callback. Duplicate chooser entry is suppressed; room and local
notes stay owned; no guest seek signal or meeting handoff is introduced.

#89 guest-work-visible behavior is already on this base (leftover-squashed). A
first host video offer still keeps Notes/Conversation/dialogs visible and
reachable through the existing room action; returning to Room does not replay
a deferred automatic presentation. This PR adds only the guest opening lifecycle.

## Security and ownership self-QA

- The existing controller still validates the current coordinator, panel,
  room generation and authenticated connection before opening the local copy.
- The change adds a panel-local in-progress flag. It creates no player stack,
  protocol field, timer, remote action, credential or log sink.
- The existing follower owns exact-file proof, silent playback, host position
  and invalidation when its room ends.
- A try/finally restores only a still-live panel. Native picker returns check
  widget lifetime before emitting. Cleanup takes precedence over the opening
  presentation; completion does not force a navigated-away panel forward.
- Paths stay in the existing explicit local chooser signal. UI status uses
  fixed text; tests use temporary synthetic files and controlled decoders.
- Guest position controls remain disabled, with no new transport authority.
- #89's automatic presentation deferral remains the owner of busy-workspace
  interruption; this PR does not reopen that path.

## Verification and honest limits

The focused Art/video/door suite passed **424 tests** on the pre-rebase tip.
Commands, final full-suite counts, exact tip/tree and hosted test/integration/four
desktop results belong in the OPEN DRAFT and coord AFTER. The new journeys use
the existing controlled player at the decoder boundary, without live meetings
or personal files.

Hashing/decoding remain synchronous. This is not an asynchronous-loading or
performance claim. Physical playback, live Webex/Cisco, native OS focus,
installed-app feel, signing/notarization and platform trust are **NOT RUN**.
Codex self-QA is not independent review or Karen PASS.

#89 is on master via leftover-squash; #37/#49 stay parked. No Music or other-repo
lane, public rendezvous, Drawpile/shared-canvas work or second video stack. The
two Art cards, Host/Join and squirrel-with-fro artwork remain unchanged.
Unsigned 0.27.2 stays Jeff-only. No merge/squash/tag/sign/Pages/Release
Trust/Publish/release/deploy/spend/live Cisco. Never merge unsigned WebJam.
Stay OPEN DRAFT. Stop for Karen leftover + security + ten-second UX on the
exact draft tip after rebase (tip MATCH required if tip changes).
