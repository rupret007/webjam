# WebJam closed-pilot handoff

> **Pre-publication candidate:** this pilot describes v0.22.3. The immutable
> v0.22.2 packages remain GitHub Latest until verified v0.22.3 promotion.

The current v0.22.3 private test candidate is pending its exact tag build,
draft verification, and promotion. It is intended to validate a simple live
rehearsal, session-arranging, and standalone Reference Studio experience:

1. Host or Join.
2. Native Jamulus sound setup; WebJam moves into the session automatically
   after fresh authenticated connection proof.
3. Play a note and have musicians verify that they hear each other.
4. Optional Webex through the direct **Webex Controls** action. Showing
   Conversation must not open a meeting; use **Join / Open Meeting** explicitly.
   On macOS, **Show Webex App** must activate or launch only the exact
   publisher-verified Cisco app with no meeting link, document argument, or
   browser; a fresh exact PID must own the foreground before success is shown.
5. Invite/play/record/review.
6. Arrange, move a named song section, audition/comp a repeated take, and test
   cycle playback through a real output.
7. Produce the evidence-rich export and import it into the named external
   editor.

Keep the scope honest. Automated checks validate source and package behavior.
Two-Mac audio, interface loss, sleep/wake, take recovery, Local Originals,
Arrange/comp playback, click-free cycle playback, and external-editor import
require real musician observations.

Use [`TEST_PROCEDURE.md`](TEST_PROCEDURE.md) for current candidate evidence.
Keep [`V017_TWO_MAC_PILOT.md`](V017_TWO_MAC_PILOT.md) as historical evidence,
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md) unchanged as the archived
v0.16.0 worksheet, and preserve published packages as immutable evidence.
