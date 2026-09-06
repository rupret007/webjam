# Worth-Building — shared canvas publication and retry

2026-09-05 CT. Branch `codex/webjam-art-making-honesty`, exact base
`origin/master 95536f31041199b63f1c3d962f87387af2905130`.
[Coord BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5555625262).
GitHub and git confirm this is the #73 squash, with tree MATCH to its reviewed
f5260311 tip. #73's branch is untouched; #72/#73 behaviors are baseline.

**Source/product Worth-Building: PASS.** An Art host must know whether the
room is actually offering the canvas they chose. A guest's explicit Open must
use the invitation accepted by the room, not a different one the host thinks
was shared. Failed sharing or withdrawal needs a useful retry in the same room.

| Task | Current-code proof | Required behavior |
| --- | --- | --- |
| Share a valid canvas invitation | The real Drawpile parser accepts a 135-character invitation with a 101-character hostname. The real wire snapshot caps display labels at 80 and rejects the server label. Coordinator catches the rejection, but returns shared=True and needs_attention=False. Native UI says the room can open the canvas and hides the paste field. No retry exists. | Keep the full capability URL intact, bound display labels to the existing wire contract, and claim publication only after a matching typed acceptance. |
| Change the room's canvas | The pure probe first publishes A through real SessionControlState, then selects the long-address B. Host says B is shared; accepted room state remains A and a real guest follower's explicit Open still launches A. | Distinguish the last accepted room canvas from the pending replacement. Show a bounded failure and retry the retained private candidate without re-pasting or auto-launching anything. |
| Stop offering the canvas | Native button probe rejects withdrawal after successful share. Host says No shared canvas and removes Stop; actual guest state still contains the old invitation. | Keep the last accepted offer visible while withdrawal is unconfirmed and expose an explicit retry. A confirmed stop removes the invitation; Drawpile remains the artist's own program. |
| Keep rejected native state out of later updates | NativeRoomPublisher assigns its canvas cache before calling owner.publish_room_state and ignores False. A later unrelated full-room publication can carry that rejected candidate. | Build a candidate without modifying accepted state; commit only on True acceptance from the same current owner and operation. Preserve the old canvas across rejection and unrelated updates. |

The probes use current parser/schema/coordinator/controller/widgets with
controlled publishers and launchers, not live Drawpile sessions. Evidence:
`/private/tmp/webjam-post73-canvas-publication-proof.json` and
`/private/tmp/webjam-post73-surface-probe.log` with native screenshots.

Scope: one canvas share/change/withdraw publication-and-retry journey,
including native acceptance, bounded display labels, persistent room/UI truth,
private payload handling and stale/reentrant operation guards. Transport
acceptance is not guest delivery, Drawpile connection or evidence of painting.
Do not widen the wire label contract or alter the complete joining URL.

Deferred independently proven gaps: Art Notes offers a Jamulus-only message
input while Art deliberately starts no Jamulus; its compact Notes layout also
needs attention. Host Paint along keyboard seeking remains deferred. None is
needed for this canvas publication slice; do not rehash #72/#73.

Required proof: real-schema host→guest propagation, failed share/change/stop
and explicit retry, native cache rejection, stale/reentrant owners/operations,
actual Art room actions, privacy/no automatic launches, compact native UX,
focused Art/door/session/invitation tests, full raw pytest, Ruff, compileall,
pip check, UX smoke, PRE_KAREN and exact-tip hosted SUCCESS with four desktop
builds. Finish one OPEN DRAFT for Karen and coord AFTER with agent=none/FREE.

Holds: Art Preview; own tools; silent local-file Paint along; Webex conversation
and share separate, never on the Art door. No second video stack, automatic
meeting/canvas launch, short-code/public rendezvous/default Session Help claim,
merge/tag/sign/release/Pages/Release Trust. Unsigned 0.27.2 Jeff-only; physical,
public, live-provider and installed-device checks NOT RUN. #37/#49 parked;
#67/#73 branches untouched. Canonical WebJam only; all four stashes preserved.
