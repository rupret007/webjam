# Worth building — native-host recovery in the combined creative room

Branch: `codex/two-session-recovery-integration`, canonical WebJam checkout.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5590738249

## Declared dependency

This candidate starts from #101 `5f09cf6fa7d9f747319c68c6b9b4f6be34aebae7`
and replays independent #102 `4b7bf2ec6dd0b50ca8cfcb35b34b3145c4cdd511`.
Fetched master remains `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`.
#101 already composes #96–#100. Original component tips are unchanged.
Only handoff documents overlap between #101 and #102; production changes are
reused, not rebuilt. The PR base is #101's branch, with #102 declared explicitly.

## Observed gap and before/after

Both components are green separately. #101's combined journeys mostly exercise
guest and LAN cleanup; its native-host lesson tests prove successful reset.
#102 adds native-host failed-close ownership and retry. Those results do not yet
prove the combined Art Notes/Conversation or Music track/recording experience
when a host resets an invitation and cleanup fails.

Combined tests reproduced two product failures. Art's successful second Reset
created native generation 2 but left the conductor and room guidance FAILED.
Music's failed close (exception or wrong-generation receipt) retired the room
and invitation yet still let Record create a take intent and Play reach audio
component lookup. An already active recording retained its Stop correctly.

Before: recovered Art transport could still show failure, and Music accepted
fresh audio intent during unresolved room cleanup. After: only a successful
explicit retry of the current native host can recover terminal room guidance;
fresh Record/Play/restart are blocked while owned room cleanup is unresolved.
Notes, temporary Conversation, loaded source and active Stop remain owned.
An old queued Play cannot run after recovery, and canceling an unpublished
start must not stop audio already playing. Admission expiry alone retains a
proved established room. These are concrete failure/recovery corrections,
with component source reused on the declared stack.

## Acceptance and review

- Art: preserve Notes and temporary Conversation through failure/retry; retire
  old local-file playback authority and callbacks; never start a meeting/player
  or give a guest seek authority as a recovery side effect.
- Music: failed native cleanup blocks fresh track and Record intent, retains
  active-take Stop, and does not advertise a replacement room before cleanup.
- Established invitation expiry does not falsely clear live room context.
- Run focused combined journeys, the full application and service suites,
  native race/static checks and real sidecars; hosted CI must run on the frozen
  draft tip, including all four desktop builds.

All external effects are intercepted or synthetic loopback fixtures. Automated
proof is not physical two-home joining, video/audio mixing, audibility, latency,
hour-long duration or independent Karen approval. The remote-joining decision
remains unanswered and public-rendezvous constraints remain in force.

## Holds

OPEN DRAFT PRE_KAREN only. No merge/squash/tag/sign/release/Pages/Release Trust/
Publish/deploy/spend/live Cisco, unsolicited send or automatic capture. Unsigned
0.27.2 stays Jeff-only. Parked #37/#49 and original #96–#102 remain untouched.
No short codes/public endpoint, second media engine, other repo, parent
injection or second WebJam goal. Maintain four-hour codex lease, BEFORE/AFTER
in America/Chicago and release at handoff. Self-QA is not Karen PASS.
