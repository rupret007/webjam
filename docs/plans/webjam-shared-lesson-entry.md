# Paint along: reach a shared lesson

Independent draft slice from fetched `origin/master`
`2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
Branch: `codex/art-shared-lesson-entry`.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5587102705.

This document records #98's original entry slice. The later Art lesson-request
slice on combined #107 adds fixed **Ask for a pause / Ready to continue** and
host acknowledgement in supported LAN rooms. Its code/evidence is separate;
see [the current two-session proof plan](webjam-two-session-proof.md).
All browser operation, actual attention, narration and mixing remain physical gates.
Accepted meeting edits retain the role-specific guidance but retire requests until
explicit helper reentry. A browser-only lesson change is unobservable to WebJam;
exit and reenter the helper to retire the old request context before a new lesson.

## Worth building

The goal is two people following a YouTube painting lesson while seeing and
talking to each other. On this base, both host and guest Paint along panels
offer local-file actions only. A person following that door cannot find the
existing meeting-sharing path from the video panel. The real empty-panel
baseline failed for both roles because no shared-lesson action existed.

Before: Paint along leads to Choose process video or Open my copy, even if
the intended lesson is on YouTube. After: Watch a shared lesson reaches the
existing Conversation card and its current primary action. Neither a file
nor a working local decoder is needed for that navigation.

This closes a missing step in the Art acceptance journey. It does not add a
player, download, public service, media-control protocol, or door card. The
existing local-file route stays available with its matching and host checks.

## Ownership and recovery

- The action only changes WebJam's view and guidance. Add Link, Join / Open
  Meeting and Show App retain their separate explicit handoff behavior.
- Host guidance describes sharing the browser lesson and controlling its
  player. Guest guidance describes asking for a pause or resume in the
  meeting. No guest seek, play, pause, stop or host publication is added.
- The local player and its retained copy are unchanged, including loading,
  mismatch, failure, hiding, and silent playback. Navigation does not stop or
  replace an already-running local video.
- Current coordinator, panel, room identity, generation, authenticated guest
  source, cleanup state and current workspace are checked when dispatched.
  A host can prepare Conversation while waiting for the first guest.
- A queued click after Notes navigation, a modal or popup, Leave, profile
  change, loss or room replacement cannot take over the newer work.
- Lesson-specific role guidance clears on normal content navigation and
  room release; the ordinary Conversation card remains the same component.
- No room URL, note text, local path, file bytes or new credential is sent or
  persisted by this action. Tests use synthetic room and media fixtures.

## Provider-supported path and its limits

Official documentation checked on 2026-09-08:

- [Webex App: share content in a meeting](https://help.webex.com/article/i62jfl)
  supports sharing an application window with computer sound in the desktop
  app. The web client requires a tab or screen with its audio option enabled
  for shared sound. The same article describes the floating participant
  videos and meeting controls while sharing. On macOS, the audio extension
  may require an administrator to install it.
- [Webex App: audio settings](https://help.webex.com/en-us/article/n139bv9)
  describes personal microphone, speaker and volume settings.

The host shares a lesson from their own browser. WebJam does not download
YouTube media, embed another video service, detect the currently shared
window, or confirm that a guest is hearing it. Meeting sharing can include
other computer sound; before sharing, the host should silence unrelated
apps and inspect the selected window. No live meeting is opened by this work.

The host's YouTube player volume changes the lesson being shared. The
meeting's speaker volume changes that person's received listening level;
microphone mute controls that person's voice. No claim is made that this
provider path supplies independently adjustable narration and per-person
voice channels. A complete volume requirement remains open until measured
on the actual provider/client pair, or resolved by a separate design.

The meeting's pause-sharing command is not a promise that YouTube playback
or narration stopped. Pause the YouTube player itself. WebJam's host-only
local-file transport does not control the browser. The guest's current
request path in the original #98 slice is speaking or using the meeting's chat.
The later in-room request path still does not control browser playback.

## Automated and physical evidence are separate

The new tests exercise real offscreen Qt host/LAN/native user journeys,
keyboard and pointer activation, focus, compact layouts, local-file health,
queued-action rejection, Notes preservation, and lesson-guidance lifetime.
They trap unhandled Qt slot exceptions so silent callback failure cannot
pass a rejection test. The existing Art start and host/guest media suites
remain part of the full local test bar.

The first hosted run found a Linux font-metric case where the two-column
Conversation actions forced a requested 320-pixel card wider. The same
buttons now stack only when their measured widths require it and return to
two columns when space permits. A larger-font regression reproduces the
overflow locally, then verifies 320 → 760 → 320 without shrinking the font,
losing focus, replacing widgets or emitting an action. The original
320-pixel acceptance assertion remains unchanged.

Exact-tip local totals, hosted runs and the four desktop builds belong in the
OPEN DRAFT PRE_KAREN body and coord AFTER. This document alone does not claim
the final hosted checks passed. Karen review is pending while unavailable.

## Short two-person test after the relevant drafts are reviewed

Run only with Jeff's authorization for the installed candidate and live
meeting. Use a shared session both machines can actually reach. Different-home
WebJam joining remains an unresolved transport requirement; a LAN run is not
remote proof. Pending draft #97 separately carries the accepted invitation's
Conversation link. This slice does not copy that change, and its independent
base still uses the existing saved-link behavior.

1. Record the exact candidate SHA, OS/client versions, network relationship
   and display sizes. Use headphones. Do not record people by default.
2. Host chooses Art → Paint along → Host. Guest joins the full invitation.
   Confirm room connection independently from the meeting.
3. Without choosing any local video, both choose Watch a shared lesson.
   Confirm the next action is visible and keyboard reachable. Join the same
   meeting explicitly. Confirm both faces and both voices with each person.
4. Host opens a Bob Ross mountain-painting lesson on YouTube. Share that
   window with computer sound in the desktop app, or the tab with tab audio
   in the web client. Complete required OS permissions explicitly.
5. Guest confirms lesson picture and narration while the host speaks. Keep
   faces visible on an ordinary laptop. Resize and switch back to Notes,
   then return; verify the written notes, meeting and lesson remain usable.
6. Guest asks for a pause. Host pauses YouTube, both confirm picture and
   narration stop, then resume together. Repeat from the other person's
   request; confirm there is only one person operating the source player.
7. Host changes YouTube volume and guest confirms that narration changes.
   Each person changes their own meeting speaker volume and microphone mute;
   confirm exactly whose listening level or voice changed. Record any lack
   of separate narration/voice control as an unmet acceptance item.
8. Check a late meeting join, temporary network interruption and changed
   shared source. Describe observed recovery without calling the external
   video synchronized. Leave WebJam and the meeting separately; verify each
   application's teardown and that no old WebJam button reopens guidance.

Report pass/fail for each observation and the actual permission or UI step
needed. Physical faces, audio, playback, remote joining and OS-native feel
remain NOT RUN in Codex self-QA. Both full creative journeys remain incomplete.

## Holds

OPEN DRAFT only; no merge/squash/tag/sign/release/Pages/Publish/deploy/spend.
Unsigned 0.27.2 remains Jeff-only. Parked #37/#49 and pending #96 are untouched.
No short codes, public rendezvous, other repo, parent-task injection, new
conference stack, live Cisco, automatic media capture or unsolicited send.
The Art door and squirrel-with-fro mark remain unchanged. Codex self-QA is
not Karen PASS; independent review still requires the exact submitted tip.
