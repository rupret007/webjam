# Worth building — one honest guest file-opening action

Base: `6484150117f949deacdbdb6f3a85017f1966994b`, fetched `origin/master`
(after #89 leftover-squash). Branch: `codex/art-guest-copy-opening`;
canonical WebJam checkout only.
Marker: `OVERNIGHT_WEBJAM_CONTINUE_20260907_0430` (rebased onto #89 master).
Rebase BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5573209855.

The guest panel keeps saying **Open my copy…** while the chosen file is opening.
Decoder loading can process Qt events, so another chooser can appear during
the existing attempt. A native chooser returning after its panel is deleted
also tries to emit a signal from that retired widget.

The valid pre-change UI/controller baseline was **8 failed / 2 passed** across
LAN and native guests: six loading cases and two retired-chooser returns failed.
Cancelled choices already preserved the action. The initial native fixture was
corrected to use a Paint along room before recording that baseline.

Before: the guest is told to choose a file while one is already opening.
After: **Opening your copy** names the current work, duplicate chooser entry is
suppressed, and completion renders this live panel's latest room snapshot.
Decoder failure offers retry; withdrawal leaves no offered video; cleanup keeps
the room-return action. A retired chooser cannot act on a replacement panel.

Make together already starts with the artist's own tools. #87 owns host opening;
#89 guest-work-visible is already on this master base. This is one explicit
guest opening lifecycle, with no new setup decision, player, protocol, public
rendezvous or shared-canvas work. Both behaviors must remain: busy guests keep
their Notes/Conversation visible on first offer, and an in-progress Open my
copy shows **Opening your copy** without a second chooser.

The initial sixteen regressions exercise real Qt/ApplicationController guest
journeys at a controlled decoder boundary. Final counts, commands, tip/tree and
actual hosted proof belong in the OPEN DRAFT and coord AFTER.

Hashing and decoding remain synchronous. This change makes their UI state
honest and handles callbacks safely; it does not claim background decoding,
performance improvements, live Webex behavior or installed-app feel.

Stop at one OPEN DRAFT PRE_KAREN for leftover + security + ten-second UX.
The two Art cards, Host/Join and squirrel-with-fro artwork remain unchanged.
Parked #37/#49 stay untouched. Unsigned 0.27.2 is Jeff-only.
No merge/squash/tag/sign/Pages/Release Trust/Publish/release/deploy/spend/live Cisco
or other-repo lane. Never merge unsigned WebJam. Stay DRAFT.
