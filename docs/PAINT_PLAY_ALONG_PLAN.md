# Paint Along and Play Along

The next goal is to make following a lesson together feel like one task. Art
already has rooms, YouTube picture-following, pause requests, shared-canvas
handoff and saved projects. Music already has live Jamulus audio and a separate
Shared Track participant. The missing connection is understandable setup:
which video to open, where sound comes from, who controls playback, and what
each person does next.

The assessment used verified source `465bc17` and official provider documentation
checked on 2026-10-03. Implementation is in progress on
`codex/paint-play-along-20261003`; this document does not claim final verification.

## Sound and video decision

| Activity | Lesson/song | Faces and conversation | Timing expectation |
| --- | --- | --- | --- |
| Paint Along | Host opens YouTube in the browser and shares its window/tab with computer sound | Webex cameras and conversation audio; keep faces beside shared content | Follow one presentation, pause when anyone needs time |
| Video practice | Host shares a YouTube song or demonstration; people listen and take turns | Webex handles the presentation and voices | Guided practice with conferencing delay |
| Live ensemble | One permitted local audio file enters the existing supported Shared Track Jamulus route | Webex supplies faces with meeting audio disconnected during playing | Listen to the Jamulus return; physical latency and isolation must be measured |

This is an architecture decision inferred from provider capabilities, not a
physical acceptance result. Webex supports application/tab sharing with sound
and participant video layouts. Its music mode does not establish ensemble
timing. Jamulus says conferencing video trails its audio. Starting separate
YouTube players cannot establish a common musical clock.

Keep the existing silent embedded Paint Along reference for people who want
it. Its approximately one-second picture-following is useful for process
reference. The audible meeting route must remain available when that player is
selected, blocked or unavailable. No YouTube extraction, background player,
automatic loopback route, device change, meeting capture or conferencing SDK is
part of this milestone.

## Implementation order

1. Repair the demonstrated hidden-handoff defect. Keep Watch a shared lesson
   available for host/guest YouTube ready and failure states. Carry a canonical
   selected link and an explicit return position into Conversation. Retain a
   failed native-view attempt without resurrecting withdrawn or replaced media.
2. Add an explicit host lesson chooser/browser-open action and compact role
   guidance beside the existing meeting controls. Guests watch the shared
   meeting copy; they retain existing pause/ready requests where supported.
   Opening a browser or meeting proves only the handoff, not playback, sharing,
   admission, cameras or audibility. Music/native unsupported request paths use
   speaking or meeting chat.
3. Give Music a Play Along entry with video-practice and ensemble choices.
   Hosts reach the existing Shared Track route; guests reach their Jamulus mix.
   Keep host-platform limits and isolation checks. A musician connected to
   Jamulus may still transmit while listening to a video: WebJam mix mute never
   means the outgoing instrument is muted.
4. Remember an explicitly selected Art lesson through the existing workspace
   editor and save authority. Keep other drafts intact, retain failed saves,
   and make reopening deliberate. Saved browser links carry only the chosen
   starting position; WebJam does not read external browser playback.
5. Bind each action to its current room, profile, role, source, meeting and
   workspace. Adding a meeting should preserve the lesson while retiring old
   queued clicks. Ordinary Music peer refreshes must not retire a meeting
   helper simply because Art video is unsupported in that profile.
6. Verify host/guest actions, failure recovery, close/reopen, saving/restart,
   compact layouts and enlarged text. Review independently, run the full
   required final software gates, update actual-click guides and deliver an
   identified draft Mac test build. Cleanup follows verified replacement.

## Two-person acceptance

Use one identified build and a room both computers can reach. Record its commit,
package hash, OS/client versions, display size, network relationship and audio
hardware. Use headphones. No recording is required.

For Art, paste a lesson, open it deliberately, join the same Webex meeting,
share the lesson with sound and keep both faces visible. Confirm narration and
both voices by listening. Request a pause, pause the browser player, then
resume. Try failed embedding, a late guest, reconnect, Notes and return, save
the lesson, restart and explicitly reopen it. Confirm one audible lesson and
preserved work. Changing meeting speaker volume may change voices and lesson
together; independent guest narration/voice mixing is not promised.

For Music, first try video practice: confirm the host shares one song and that
the group understands turn-taking and outgoing-instrument control. Then try a
live ensemble with the supported Shared Track setup. Disconnect Webex audio,
confirm one backing source plus instruments in each Jamulus mix, adjust their
levels independently, pause/seek/reconnect and verify cleanup. Judge timing by
the sound, not Webex camera motion. Measure latency using the existing physical
pilot before claiming tight ensemble performance.

Physical sound, Webex sharing/cameras, native YouTube behavior, separate-home
reachability, sustained routing and feel start **NOT RUN**. Existing LAN or
synthetic tests do not satisfy these observations. Record concrete failures for
the next small repair. Keep all PRs draft; Jeff owns Latest, merges, tags,
releases and feel. No Barker/Wildflower.

## Official sources

- [Webex content sharing](https://help.webex.com/article/i62jfl) and
  [participant video layout](https://help.webex.com/article/n4f1ptt).
- [Webex audio tests](https://help.webex.com/article/nt5e0df) and
  [music mode](https://help.webex.com/en-us/article/h7tezj).
- [Jamulus getting started](https://jamulus.app/wiki/Getting-Started),
  [latency and video FAQ](https://jamulus.app/wiki/FAQ), and
  [client troubleshooting](https://jamulus.app/wiki/Client-Troubleshooting).
- [YouTube IFrame API](https://developers.google.com/youtube/iframe_api_reference),
  [embedding restrictions](https://support.google.com/youtube/answer/171780?hl=en),
  and [developer policies](https://developers.google.com/youtube/terms/developer-policies).
