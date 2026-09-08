# PRE_KAREN — an early copy waits for the host

Base: `e35f7352b42280814be0ea02f77b412f68231706` (master after #94).
Branch: `codex/art-guest-prepare-copy`; canonical WebJam checkout only.
Marker: `OVERNIGHT_WEBJAM_ART_20260907_2307`.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5579240270.

## Leftover honesty

#93 names a waiting Paint along guest's next click, but that click led to a
panel that hid its file action until a host offer. The existing follower
already accepts an early copy and waits for an identity match. This slice
completes that explicit path; it does not revisit guest opening/busy handling
from #90 or first-offer navigation from #89.

An empty guest panel now offers Open my copy. The local snapshot gains one
bounded boolean for a retained, unchanged copy before an offer. Your copy is
open means exactly that; it grants no following or play permission. The new
status disappears when the file changes, fails, closes or the room is lost.
Withdrawal pauses and retains a valid copy as waiting.

## Ten-second UX self-QA

- Before a share: one file action, with permission to keep making instead.
- During the existing local check: Opening your copy; no duplicate chooser.
- After opening: Your copy is open; the next dependency is explicitly the
  host's matching offer. Back to room remains available.
- After the offer: a matching copy follows; a different one offers the existing
  matching-copy recovery. No picture is shown before proof.
- Changed/deleted or failed copies offer another try. Close my copy works while
  waiting. Lost-room return takes priority over any stale chooser result.
- Make together keeps Bring your own tools and no automatic video requirement.
  Native room-start evidence supplies the early room cue. LAN is tested via its
  existing explicit in-room Paint along entry; no new LAN start fact is claimed.
- The real compact Qt window was captured at 720×560 and visually inspected.
  New journey assertions also cover 1100-pixel width, keyboard activation and
  visible controls staying within the embedded panel.

## Security and ownership self-QA

- Production changes are confined to core/reference_video.py and the existing
  Paint along dialog. The controller, coordinator, transport and player are
  unchanged.
- local_copy_prepared is a path-free boolean. It checks the retained identity,
  current file token and absence of loading/player attention/pending pause.
  It never replaces the host's per-session matching proof.
- NO_VIDEO still means can_follow=false and should_play=false. The surface
  stays detached, position stays disabled/hidden, and the guest has no seek,
  play, pause, stop or publication authority.
- Existing controller checks still validate the current panel/coordinator,
  authenticated connection, room generation and current video state after
  the native chooser returns. Both transports reject the new action after loss.
- The early file uses the sole silent player and the same explicit local picker.
  No path, file bytes, secret or new field crosses the room protocol. No extra
  persistence, logging, process, thread, timer, URL, download or meeting handoff.
- Tests use temporary synthetic files and controlled decoders. Private markers
  are checked absent from logs. Make together and Conversation remain independent.

## Verification

The valid pre-change baseline was 16 failed / 2 passed. After implementation,
the focused suite passed **425 tests in 19.41s**, including 24 new preparation
journeys. A separate capture run passed its selected real Qt journey.

Ruff across core/webjam_qt/ui/services/api/tests, compileall, pip check, runtime
dependency policy and git diff --check passed. Final full local and hosted
evidence on the frozen tip is recorded in the PR body and coord AFTER; do not
infer hosted green from this document.

The full local run uses the repository CI procedure: every tracked test module,
sorted by git, in a fresh Python interpreter, fail-fast with no retries. The
workflow documents Qt's process-lifetime reason for this existing isolation.
No tests are omitted and the workflow is unchanged.

## Limits and holds

Hashing/decoding remain synchronous; this is not a responsiveness or background
loading claim. Physical playback, live Webex/Cisco, OS-native focus,
installed-app feel, signing/notarization/platform trust are **NOT RUN**.
Codex self-QA is not independent review or Karen PASS.

Keep the PR OPEN DRAFT. Stop for Karen leftover + security + ten-second UX on the
exact final tip. No merge/squash/tag/sign/Pages/Release Trust/Publish/release/
deploy/spend/live Cisco. Unsigned 0.27.2 stays Jeff-only; never merge unsigned
WebJam. #89/#90 are not reworked and parked #37/#49 remain untouched. No
short-code/public rendezvous, Music, Drawpile/shared-canvas work, second video
stack, other repo, second Goal or parent injection. Door stays exactly Make
together + Paint along → Host/Join with the squirrel-with-fro artwork.
