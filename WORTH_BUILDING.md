# Worth-Building — Paint along copy recovery

2026-09-05 CT. Branch `codex/webjam-art-activity-clarity`, audit base
`origin/master cf311470fadcee1a688f3b675eb6d2ca4094926d`.
After the verified #72 squash, this unpublished branch was refreshed onto exact
`origin/master f27a6344abc18ec3af990d43827d4c74f869a088`.
[Coord BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5555193410).
#72 was subsequently squashed after Karen PASS; its branch remains untouched.
Its invitation/room retry/Leave changes are baseline, outside this slice.
[Refresh BEFORE](https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5555306061).

**Source/product Worth-Building: PASS.** When the host chooses another process
video, a guest should be able to open its matching local copy and continue.
The currently offered recovery action fails, and local playback failures can
still be presented as following the host. These are one guest player journey.

| Guest task | Current-code evidence | Required behavior |
| --- | --- | --- |
| Follow the host's next video | A direct real-coordinator probe opens copy A, observes host video B, and reaches `mismatched_file`. Opening matching copy B raises “Close the current Paint along video before changing players.” Exactly one cached player exists: `open_local_copy` reattaches that same instance, while `set_player` rejects it because an old identity exists. | The visible Open my copy action accepts a valid replacement using the same silent player; a different player still requires confirmed old-copy cleanup. |
| Recover when a local copy moved or playback failed | A moved copy keeps its identity and hits the same attachment guard. `apply` drops its playing flag after a failed operation; coordinator error handling resolves the retained identity back to FOLLOWING. A refused pause can also lose the obligation to stop the picture. | Show bounded local playback trouble and an action that works. Preserve stop obligations until confirmed; never claim the local video is following after a known player failure. |
| Replace without showing the wrong picture | The current load retains previous identity until load returns. Qt's duration wait pumps non-input events, so normal follower ticks can run during source replacement. | Retire previous proof before loading, keep reentrant ticks unable to play the changing source, and confirm the new proof only after successful loading in the same active operation. |

Scope is guest Paint along copy replacement and local-player recovery,
including real dialog/controller behavior and the existing Qt player boundary.
Host keyboard seek and optional-canvas publication were separately identified
but are deferred; they are not needed to complete this guest recovery path.
No new video stack, file transfer, public rendezvous, or physical-playback claim.

Required proof: model/coordinator replacement and fault regressions, actual
Open my copy UI/controller recovery, silent-player adapter checks, stale and
reentrant operation handling, compact/native UX, focused Art/session/invitation
checks, full raw pytest, Ruff, compileall, pip check, UX smoke, PRE_KAREN, and
exact-tip hosted SUCCESS including four desktop builds. An open draft and
coord AFTER with released lease complete the slice; never merge it.

Holds: #37/#49 parked; #67/#72 branches untouched. Art Preview, own tools,
silent local-file Paint along; Webex conversation/share stays separate and
never appears on the Art door. No automatic meeting/canvas launch, second
video stack, short-code/public rendezvous, merge/tag/sign/release/Pages/Release
Trust. Unsigned 0.27.2 Jeff-only. Physical/provider/installed-device gates NOT RUN.
Canonical WebJam only; pre-existing stashes preserved.
