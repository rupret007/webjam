# Worth-Building — Art room clarity and continuity

2026-09-05 CT. Branch `codex/webjam-art-room-clarity` from exact master
`c18e0b9ac039e8e99d3a5fa19305c155de3b160e` after #69 and #70.

**Worth-Building PASS. Product self-QA PASS. Full local suite PASS (7,078 tests).**
Exact-tip hosted success and four desktop builds remain required for handoff.
Bob-the-Bot #3 BEFORE: comment `5554058853`.

Current source exposes three connected problems in an artist's room journey:

1. A connected Art room still renders the empty Music participant stage.
   `ApplicationController._push_participants_to_grid` supplies only Music
   participants, and `_render_session_conductor` repeats the HUD into the empty
   card. `ParticipantGrid._sync_participant_accessibility` therefore announces
   zero artists while the authenticated room connection says connected. Make
   together has no useful body when no optional canvas or video exists.
2. Cold Join can overwrite the guest's saved workspace. The launch dialog saves
   its fallback Music profile even when a Podcast/Review guest never chose it;
   `app.py` saves an invitation title before marking it borrowed. Leave then
   restores the overwritten title/profile. Warm entry already protects borrowed
   titles, but cold bootstrap tests miss this boundary.
3. A safe native Art retry can connect while the conductor stays FAILED.
   `prepare_native` resets room state without opening a new conductor attempt;
   the conductor correctly refuses to leave its terminal failed generation.
   A pure transition check confirms that advancing the attempt accepts the new
   connected facts while the previous token does not.

The coherent improvement is a truthful Art room view with a working entry,
retry, and exit boundary. The room body will use actual role/connection and
existing activity facts, give direct access to existing optional activities,
and distinguish waiting, connected, reconnecting, closing, and closed rooms.
It will not display Music mixer counts as artist membership or invent a named
roster that the current peer protocol does not supply. Cold invitations must
preserve the artist's personal profile, activity, title, and notes through Leave;
native retry must bind its successful room to a fresh conductor attempt.

Required proof: actual widget/controller transitions and action routing;
cold door → authenticated Art entry → Leave → personal workspace restoration;
safe native failure → retry → connected, with stale callbacks rejected;
Art/door/session/invite regressions; full unfiltered local pytest, Ruff,
compileall, pip check, UX smoke; compact/normal native renders; Pre-Karen QA;
one mergeable OPEN DRAFT with exact-tip hosted SUCCESS and four desktop builds.

Art Preview labels from #70 remain. Music audio readiness stays evidence-based.
Two Art choices precede Host/Join; meeting/canvas launch stays explicit, Webex
sharing and silent local Paint along retain their existing owners. Black,
white, neutral gray, and burnt orange only. No short-code, public rendezvous,
default Session-help claim, second video stack, merge/tag/sign/release/Pages or
Release Trust. #37/#49 stay parked. Unsigned 0.27.2 and physical/public results
remain Jeff-only / NOT RUN. Final SHA and evidence belong in the draft and AFTER.
