# PRE_KAREN — preserve Conversation in a complete invitation

Base: `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
Branch: `codex/invitation-conversation-context`.
Exact pushed tip and final local/hosted results belong in the OPEN DRAFT body.

## Product and leftover honesty

A complete copied invitation previously lost its optional meeting at Join.
Guests either needed a second paste or could open an unrelated personal meeting.
This slice carries that optional link as temporary room context through cold and
warm joining, native credential consumption, LAN discovery, explicit opening,
Retry, and Leave. It does not add remote reachability or a media feature.

#96 remains independent and untouched. Its own-space copy is not duplicated here.
The sustained Art/Music goal remains incomplete; see
[the capability map and physical scripts](docs/plans/webjam-two-session-proof.md).

## Ten-second UX self-QA

- One complete paste supplies the room and optional Conversation destination.
- Guests do not choose the host activity again or extract a second link.
- No meeting opens from paste, room arrival, navigation, or link editing.
- Conversation still requires the explicit Join / Open Meeting action.
- A bare/no-meeting invite does not select a previous personal meeting.
- Add Link / Change Link uses a compact masked room editor for guests; it says
  the personal meeting stays unchanged. Hosts retain the existing saved editor.
- Cancel, invalid input, navigation and Leave during editing cannot change the
  wrong room. Accepted edits return focus to the appropriate Conversation action.
- Door remains Make together + Paint along → Host/Join; squirrel mark and banned
  first-screen words are unchanged. Notes and local-file Paint along still work.

## Security and ownership self-QA

- The supplemental URL is local untrusted clipboard context, not authenticated
  host state. Room connection credentials and wire formats remain unchanged.
- Extraction is bounded, requires a valid single room invitation first, and
  accepts only the generated labeled optional block. Quoted forwarding and URL
  wrappers are supported. Unrelated signature URLs are ignored. Conflicting,
  malformed or mismatched labeled blocks produce fixed errors with no URL.
- Existing public-HTTPS meeting validation is reapplied before use. No arbitrary
  URL opens at ingress. Native/argv bearer restrictions remain unchanged.
- Personal AppSettings and its persisted file never receive the borrowed link.
  Current-room settings edits are memory-only and room/generation guarded.
- Native enrollment can discard its capability without discarding Conversation.
  Discovery/retry preserves the same room context; queued replacement carries
  the corresponding new link, including explicit absence.
- Bridge opening snapshots the explicit effective URL. Error Retry retains it
  and rechecks the request generation; a replaced/left room cannot reuse Retry
  or publish a stale launch result. Existing external meetings are never closed.
- Proved Leave restores personal context; failed cleanup preserves the current
  context until retry succeeds. Shutdown drops retained room/pending links.
- No permission, routing, guest playback authority, authentication, media capture,
  signing, package or release safeguards are relaxed.

## Verification

New regression modules cover ingress/dialog/bootstrap, actual controller and
Bridge handoff behavior, and native/LAN lifecycle ownership. Existing conversation
modal and navigation tests now assert temporary guest links versus personal host
settings, including actual modal events, keyboard focus and compact containment.

Run the entire repository test bar using its isolated-module pattern, including
new test files. Record final module/test counts and logs in the PR body. Hosted
CI must run on the final pushed tip with the four desktop builds. Local logs live
under `out/invitation-conversation-context/` and are not product artifacts.

Physical two-person Art/Music sessions, live Webex/Cisco, OS-native installed-app
feel, physical audio routing/latency, signing and release trust remain NOT RUN.
Codex self-QA and agent review are not independent Karen PASS.

## Holds and handoff

OPEN DRAFT only. Karen is unavailable; no merge until required independent
review and separate authorization. Additional independent product slices may
continue under the updated goal. No merge/squash/tag/sign/release/Pages/Publish/
deploy/spend/live Cisco or unsolicited send. Unsigned 0.27.2 remains Jeff-only.
No short-code/public rendezvous, second video stack, parked #37/#49 changes,
other-repository lane or parent-task injection. Publish coord AFTER and release
the codex lease when the draft's verification is ready.
