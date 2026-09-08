# PRE_KAREN — combined two-session integration

Branch: `codex/two-session-integration`, canonical WebJam checkout only.
This is a declared dependent stack rooted at #97
`14aa874aa28cc3bc8cdfe647c888bc61547daa89`. The approved sustained goal permits
stacks for dependent work. Original drafts remain untouched.

## Existing source being reused

- #96 `41a26652387c855059402ae8986c4b0f91a0531f`: Make together own-space first action.
- #97 `14aa874aa28cc3bc8cdfe647c888bc61547daa89`: temporary room Conversation context.
- #98, both source commits ending at `830bed1fe2591c44d2bafc136b5ee84633e96c67`:
  shared-lesson navigation, source-owned guidance and compact Conversation layout.
- #99 `c2a088fdbfd537d7ad342fda2a6810cab2bd9ca5`: Shared Track support and recording intent.
- #100 `0cf1f2c6103fd10a5b507e3ff378dc4c92ff1bb8`: offline audio round-trip evidence tools.

Their common original master was `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
Original green CI and local results are historical evidence for those heads.
They do not establish the behavior of this combined application.

## Combined verification — PENDING

Source replay is in progress. New integration regressions, relevant suites,
full local required checks and hosted CI including all four desktop builds
are PENDING. Record the final combined SHA and actual results in the draft body
and coord AFTER; do not inherit green status or aggregate old test counts.

The new regression scope is composition of existing behavior, not a claimed
new feature or an already-proven product bug:

1. Full Art invitation with a guest's different personal meeting, then Paint
   along → Watch a shared lesson → Conversation. Verify room provider/link,
   explicit-only opening, room-neutral accessibility and guest-never-seek.
2. Replace/remove the room meeting while lesson guidance is active. Missing
   context must not select the personal meeting. Preserve Settings, Notes,
   local video selection, compact layout and intentional keyboard focus.
3. Invalid/cancelled edits, failed Open/Retry, queued room replacement,
   successful/failed Leave, return and profile changes must preserve current
   room/generation ownership. Old URLs, callbacks and lesson roles cannot
   affect the next room. Successful Leave restores personal context; failed
   cleanup preserves current context until its existing recovery completes.
4. Music in that room context must preserve supported host/primary/RPC/route
   gates, unsupported-host inspection/return, queued/loading/retained FAILED
   source refusal, cleanup/STOPPING, active-recording Stop and ROUTING/PLAYING
   track requirements. A refused Record must never replay after recovery.
5. Preserve shared fixtures, existing assertions and tests/test_art_start_ux.py.
   Document any newly reproduced integration failure and bounded fix separately.

## Security, product and human boundaries

Supplemental meeting links remain bounded, validated, untrusted clipboard
context; existing session authentication owns authority. Borrowed links stay
outside persisted personal Settings. No ingress, edit or navigation opens media.
Recording and route cleanup retain their existing owners and worker guards.
Offline tools remain explicit-file only and cannot enable audio or certify
physical routing, device accuracy, drift, one-way latency or playability.

Keep Art's Make together + Paint along → Host/Join door and squirrel mark.
Non-painters and talk-only guests need no canvas, instrument or local file to
enter Make together. External browser playback and meeting audio remain
source-owned; guest pause requests and narration/voice/personal-volume behavior
still need human proof. The local-file Paint along option remains silent.

Two-home joining still needs the pending network architecture decision.
LAN/loopback tests and meeting admission do not prove remote WebJam connection.
Art faces/audio and Music monitoring, per-player reference timing, jitter,
dropouts, sustained timing, 25 transport cycles and a 60-minute rehearsal remain
NOT RUN. The sustained goal is INCOMPLETE. Codex self-QA is not Karen PASS;
independent leftover/security/ten-second UX review must match the combined tip.

## Holds and handoff

OPEN DRAFT only. Original #96–#100 and parked #37/#49 remain untouched.
No merge/squash/tag/sign/release/Pages/Release Trust/Publish/deploy/spend/live
Cisco, unsolicited send, secrets/customer data or automatic media capture.
Unsigned 0.27.2 stays Jeff-only. No short-code/public rendezvous, second media
engine, other repository, parent injection or second WebJam goal. Maintain the four-hour codex lease
before push; coord #3 BEFORE/AFTER use America/Chicago; release lease at handoff.
