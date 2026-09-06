# Worth-Building — keep offered Art activities reachable

2026-09-05 CT. Real WebJam Goal; branch
`codex/webjam-finish-product-art-guest`, verified post-#74 base
`2b84e5acd94ff7dee327e79480351334c5e15977`.
[Coord BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5556059873).
#74 landed during this slice. Only this new unpublished branch was refreshed
from the original 95536f31 base after [refresh BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5556160339).
Its canvas publication/change/stop/retry is now baseline; its protected branch
remains untouched at 67a76ed1. All local changes and four stashes preserved.

**Source/product Worth-Building: PASS.** An artist should be able to return to
an offered process video or canvas from the room without discovering a Tools
menu or installing an unrelated drawing app. Both optional activities already
coexist; the room currently advertises and dispatches only one of them.

| Guest task | Current-code evidence | Required behavior |
| --- | --- | --- |
| Return to a hidden Paint along video beside a canvas | Real LAN guest/controller/player journey opened the signed matching local copy, hid it, received a canvas offer and returned to the room. With Drawpile installed, the overview showed only Shared canvas/Open canvas. Its video dispatch was rejected even though the follower was HIDDEN and the room CONNECTED. | Keep a visible, explicit route to the offered video while retaining the canvas route. Opening its panel must not automatically show the hidden picture or load another file. |
| Follow a process video without installing the painting app | The same actual guest journey with Drawpile unavailable showed only Install Drawpile/Open canvas. The offered video remained hidden and its room-level dispatch was rejected. | A missing canvas app must not hide another offered activity or its recovery. Give each activity its own factual status and panel action. |
| Open the canvas while the video needs attention | Pure current model puts video NEEDS_FILE and other attention states ahead of a ready canvas. The same one-action overview/dispatcher then omits the ready canvas route. | Preserve the primary attention priority, but keep the other actually offered activity reachable. |

Pure source derivation independently confirmed HIDDEN video maps to its own
route when canvas is NONE, but maps only to canvas when it is READY or
MISSING_APP. The actual probe is `/private/tmp/webjam-post74-activities-probe.py`;
results are `/private/tmp/webjam-post74-activities-proof.json`, with compact
native screenshots. These use controlled launchers/players/peer responses,
not physical Drawpile or multi-computer playback.

The existing Tools menu remains a working fallback. This is a room-navigation
and task-continuation gap, not a missing video capability. It is independent
of #74's publication receipts and pending canvas intents.

Build one bounded product slice: retain the strip's single priority status;
show the other actually offered canvas/video as an additional route in the
room overview, with its own status. Reuse existing panels and explicit
Open/Show/Hide/Install controls. Derive routes from current room facts, not a
guest's saved start preference. Revalidate connection, cleanup and offered
activity at dispatch; withdrawal and closure retire unavailable actions.

Proof must cover both directions, hidden and attention states, missing app,
withdrawal, stale dispatch, saved Music/Art preferences, no automatic launch,
private-payload-free projections, and compact/native keyboard usability beside
Conversation. Focused Art/door/session/invitation tests, full raw pytest, UX
smoke, Ruff, compileall, pip check, PRE_KAREN and exact-tip hosted SUCCESS with
all four desktop builds remain mandatory.

Deferred independently verified gap: Art Notes advertises Message artists,
but its composer and queued incoming chat still use the Jamulus-only path.
Correcting that private-note/messaging boundary is a separate slice; do not
invent chat or substitute opt-in Session help here.

Holds: Art Preview; own tools; silent local-file Paint along; Webex talk/share
first-class beside WebJam, never on the Art door. No automatic meeting/canvas
launch, second video stack, short-code/public rendezvous/default Session Help
claim, merge/tag/sign/release/Pages/Release Trust. Unsigned 0.27.2 Jeff-only;
physical/public/live-provider/installed-owner-package gates NOT RUN. #37/#49
parked; #67/#74 branches untouched. Canonical WebJam only. One OPEN DRAFT for
Karen, then coord AFTER and agent:none/FREE.
