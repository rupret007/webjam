# Worth building — preserve the guest’s chosen work

Base: `50e035e09997e891f08520d80d60cfae3383ce27`, fetched `origin/master`.
Fresh branch: `codex/art-guest-video-offer`; canonical WebJam checkout.
Marker: `OVERNIGHT_WEBJAM_CONTINUE_20260907_0215`.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5566904922.
Base-advance BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5567377754.

## Demonstrated leftover

A host’s first Paint along offer calls automatic presentation without window
activation. The production window nevertheless replaces its selected workspace
with the embedded video page. A guest writing local notes or using Conversation
loses sight of that work because somebody else offered an optional activity.

The pre-change baseline on master `159f4447` reproduced **6 failures / 2 passes** through actual LAN and
native guest controllers: Notes, Conversation and a pending meeting handoff were
replaced; direct entry from the untouched Room already worked. Earlier native
fixture construction errors were corrected before recording this baseline.

## Why this slice

This makes Make together usable while an artist is already working or talking.
The existing LAN named-artist list already has authenticated freshness, privacy
and keyboard coverage. Native names require additional authenticated facts.
Make together already starts with the artist’s own tools. Another roster or
canvas setup claim would not address the observed interruption.

Before: a background video offer replaces the guest’s current work.
After: keep that work and focus visible; the existing room action offers the
video. Deliberate entry reaches Open my copy without loading or sending anything.
Returning to Room alone does not replay an offer that was deferred while busy.
Untouched-Room first entry and the host’s selected start keep their behavior.

## Boundaries and review

The change is limited to automatic guest presentation. It adds no player,
network protocol, room field, timer, launcher or door. Existing room binding,
connection checks and guest transport restrictions remain the action owners.
Notes, selection, undo, meeting state and authenticated identity stay intact.
This is distinct from #81/#82/#86/#87/#88.

The regression suite also covers compact/wide views, active dialogs and menus,
withdrawal/replacement, and a video offered behind higher-priority canvas
recovery. Full-suite counts, exact final tip/tree and actual hosted evidence,
including all four desktop builds, belong in the OPEN DRAFT and coord AFTER.

#88 passed Karen and was leftover-squashed by Bob onto this base. Its completed
handoff remains at https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5566652085.
#37/#49 remain parked; #86/#87/#88 stay merged. Stop at one OPEN DRAFT for Karen
leftover + security + ten-second UX. No merge, squash, tag, signing, Pages,
Release Trust, Publish, release, deploy, spend, live Cisco, public rendezvous
or other-repo lane. Unsigned 0.27.2 remains Jeff-only.
