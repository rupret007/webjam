# Worth building — prepare a guest copy before the host shares

Base: `e35f7352b42280814be0ea02f77b412f68231706`, fetched `origin/master`
after #94; the fresh branch started at that exact tip.
Branch: `codex/art-guest-prepare-copy`; canonical WebJam checkout only.
Marker: `OVERNIGHT_WEBJAM_ART_20260907_2307`.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5579240270.

#93 gives a waiting guest an **Open Paint along** room action and says they can
load a copy before the host shares. The panel actually hid **Open my copy…**
until an offer arrived. The existing follower already supports holding a local
copy without showing or playing it. This is a missing functional step, with a
short path through existing code.

The valid baseline reproduced **16 failed / 2 passed** in real Qt/controller
journeys. The native fixture was corrected to carry an empty video snapshot
before that baseline. All sixteen preparation journeys stopped at the missing
file action; both Make together controls already passed.

Before: a guest follows the waiting-room action into a panel with no file
action. After: the guest can open a copy early and see **Your copy is open**,
explicitly waiting for WebJam to check it against the host's eventual offer.
Matching, host state and connection still own following. A changed or failed
copy offers another try. More → Close my copy works before a share.

Make together already starts with each artist's own tools; #94 supplies its
Conversation next-click label. Completing an action that currently dead-ends
beats adding more wording or another door choice. This slice changes only the
existing local follower projection and Paint along panel, plus tests/docs.

The early room cue uses the existing host-start fact available on native
rooms. LAN guests can use the existing explicit Paint along entry; this slice
does not add a host-start field or pretend LAN publishes one. Neither route
adds a required video step to Make together.

Focused proof: **425 tests passed**, including **24 new preparation journeys**
across LAN/native, 720/1100 widths, later match/mismatch, repeated empty room
state, withdrawal, changed/deleted copies, cancellation, decoder failure,
connection loss during the chooser, closing a prepared copy and Make together.
Actual Qt captures at 720×560 were inspected using synthetic files and a
controlled decoder. Full local and hosted results, exact tip/tree and four
desktop build evidence belong in the OPEN DRAFT and coord AFTER.

No download, file transfer, protocol, player, timer, meeting action or guest
transport input is added. Hashing and decoding remain synchronous. Physical
playback, live meetings, OS focus/installed-app feel and signing are NOT RUN.

One OPEN DRAFT PRE_KAREN, then stop for Karen leftover + security + ten-second
UX. No rework of #89/#90; parked #37/#49 untouched. Art door retains exactly
Make together + Paint along → Host/Join and the squirrel-with-fro artwork.
Unsigned 0.27.2 is Jeff-only. No merge/squash/tag/sign/Pages/Release Trust/Publish/
release/deploy/spend/live Cisco, short-code/public rendezvous, Music, Drawpile,
shared-canvas work, second video stack, other-repo lane or parent injection.
