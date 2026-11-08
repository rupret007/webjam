# WebJam Webex sandbox demo gate

Status: **NOT RUN**

This is a worksheet, not a completed test record. Copy it for each actual
packaged candidate and keep its results separate from unit tests, local
workerd checks, generated audio, and earlier human listening tests.

This worksheet validates WebJam's external Webex handoff without storing a
Webex username, password, token, admin address, or private meeting path in the
repository or evidence bundle.

## Private preparation checkpoint (November 8, 2026)

- The staged Intel and Apple Silicon desktop packages are from
  `76b1fd74c0f8a77df2c9d6b42f7fde87dadfaf7c`. A newer source commit does not
  change their contents. Confirm the actual running package before testing.
- The private website source is
  `71bff2cd6a5e352492eb7aaa304385182b378652`, Worker version
  `2c5c81e5-1aa4-4ea7-be69-cacda13c20e8`. It does not embed Webex video or
  control Webex audio; browser guests use the separate Webex app or browser.
- The existing dedicated WebJam Personal Room was found through the signed
  Webex client's normal UI. Its private link and host details are not part of
  this record. Saving that room through normal WebJam Settings and testing
  actual video/audio handoff remain pending. There is no need to create a new
  room or ask the operator to send its private link in chat.
- No two-endpoint Webex video or exclusive-audio result is claimed here.
  Observe resource guards and use awake, consenting participants for live
  camera/microphone checks; stop an unattended attempt before capture starts.

## Before testing

1. Rotate every password that was shared in chat before using the sandbox.
2. Use the ordinary sandbox participant for meetings. Use the site
   administrator only when a site policy must be changed.
3. Use the exact packaged WebJam candidate intended for the demo. Do not infer
   package results from a source-tree run.
4. Connect wired headphones at both musician endpoints.
5. Record these non-secret identity facts:

| Fact | Evidence |
| --- | --- |
| WebJam version | NOT RUN |
| Build ID | NOT RUN |
| Package filename | NOT RUN |
| Package SHA-256 | NOT RUN |
| macOS/Windows version and architecture | NOT RUN |
| Webex client/browser version | NOT RUN |
| Jamulus version | NOT RUN |

Never paste the meeting path, credentials, cookies, tokens, or participant
email into this worksheet.

## Configuration and validation

Use the private Personal Room link locally, but identify it in evidence only by
the derived `*.webex.com` site hostname.

| Check | Result | Evidence/notes |
| --- | --- | --- |
| Settings accepts a valid HTTPS Webex Meeting or Personal Room link | NOT RUN | |
| Reopening Settings preserves the link | NOT RUN | |
| Settings shows only the derived Webex hostname | NOT RUN | |
| Malformed input is rejected | NOT RUN | |
| An HTTP Webex link is rejected | NOT RUN | |
| Supported non-Webex HTTPS meeting links retain their provider label and explicit external handoff | NOT RUN | |
| Unsafe or unsupported meeting links are rejected before any external handoff | NOT RUN | |
| Direct **Webex Controls** and **More → Webex Controls** show the same Conversation panel without a launch | NOT RUN | |
| On macOS, **Show Webex App** dynamically re-verifies the exact running Cisco PID and requests activation without opening a browser or meeting | NOT RUN | |
| A minimized-window check records what the musician sees without claiming WebJam can prove restoration | NOT RUN | |
| On macOS with Webex stopped, **Show Webex App** launches the verified app itself with no URL/document argument, then proves the exact path, PID, publisher, and foreground state; Webex chooses its own screen | NOT RUN | |
| Windows/Linux native focus stays unavailable without publisher proof | NOT RUN | |
| **Join / Open Meeting** opens the intended native Webex app or browser destination exactly once | NOT RUN | |
| WebJam says “Opened externally—finish joining in Webex” and never claims it joined | NOT RUN | |

## Two-endpoint meeting

Join from the Mac host and Windows guest using the verified private candidate.
Record additional device/browser combinations separately. Opening a link is
not proof that either participant joined. Do not record the meeting.

| Check | Result | Evidence/notes |
| --- | --- | --- |
| Second endpoint is admitted | NOT RUN | |
| Both participants are present | NOT RUN | |
| Camera works in both directions | NOT RUN | |
| Speech works in both directions | NOT RUN | |
| Mute, leave, and rejoin work | NOT RUN | |
| Webex identity comes from Webex, not the WebJam musician name | NOT RUN | |

## Jamulus coexistence

Webex microphone mute is not an exclusive-audio switch: meeting speakers can
still play. WebJam's monitor mute changes only the local mix and does not stop
the instrument from reaching the band. Current controls do not verify or
automatically change the external meeting's audio state.

Cisco documents audio connection and video as separate choices in the
[desktop and browser prejoin window](https://help.webex.com/en-us/article/ela6i8).
Choose **Don't connect to audio** for a video-only join; this is different from
the microphone's Mute control. Verify the result on the actual client rather
than assuming a saved preference took effect. Do not change global join defaults
on the participant's behalf.

Use this manual handoff at **both** endpoints:

1. For conversation, leave/end the WebJam music session before connecting
   Webex meeting audio. Confirm speech through the intended headphones.
2. Before music, use Webex's own **Disconnect audio** control (wording may
   vary). If unavailable, leave and rejoin with **Don't connect to audio**.
   Muting its microphone alone is insufficient.
   Confirm meeting sound has stopped. Video may remain on only with meeting
   audio disconnected; if that cannot be confirmed, leave Webex for the music
   test. Keep music idle until both people are ready.
3. Start a fresh private music session/invitation and join it. Listen through
   Jamulus/Web Join only. Check speech both ways before playing a short passage
   or a local WAV/MP3 backing track. Never infer hearing from a moving meter.
4. To return to Webex conversation, first leave/end music, then reconnect
   Webex audio. Before the next music session, disconnect Webex audio again
   and use a fresh invitation. An expired invitation is not a microphone fault.

This qualifies the existing manual workflow, not a one-click audio switch.
The approved private music trial remains limited to one native host and one
browser guest for at most ten minutes, with no recording. Do not extend the
trial or bypass relay/resource holds to finish a checklist.

| Check | Result | Evidence/notes |
| --- | --- | --- |
| Jamulus remains connected while Webex is open | NOT RUN | |
| Jamulus audio is clean through wired headphones | NOT RUN | |
| Webex creates no duplicated or feedback path | NOT RUN | |
| Music-to-conversation-to-music handoff works at both endpoints with exclusive audio | NOT RUN | |
| Webex does not replace the selected Jamulus devices | NOT RUN | |
| Closing/failing Webex does not end Jamulus | NOT RUN | |
| Quitting WebJam does not claim it closed externally owned Webex | NOT RUN | |

## Privacy and teardown

| Check | Result | Evidence/notes |
| --- | --- | --- |
| WebJam logs contain no password, token, or private meeting path | NOT RUN | |
| A saved support bundle contains no password, token, or private meeting path | NOT RUN | |
| Support material may retain only the Webex site hostname | NOT RUN | |
| All WebJam-owned processes and ports stop cleanly | NOT RUN | |
| Both people explicitly leave Webex and confirm its camera/microphone are off | NOT RUN | |

Ending music does not leave Webex; leaving Webex does not end music. Close
each explicitly, and do not terminate unrelated applications or shared
services during cleanup.

## Decision

Block the demo for a crash, wrong Webex destination, inability to join from a
real second endpoint, feedback under the documented wired-headphone setup, or
any Jamulus interruption. Record lesser presentation issues as follow-up work.

Final verdict: **NOT RUN**
