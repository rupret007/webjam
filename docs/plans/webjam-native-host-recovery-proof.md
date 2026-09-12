# Native host recovery in the combined Art and Music room

This dependent candidate combines #101 at
`5f09cf6fa7d9f747319c68c6b9b4f6be34aebae7` and #102 at
`4b7bf2ec6dd0b50ca8cfcb35b34b3145c4cdd511`, both rooted at master
`2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`. Source drafts stay unchanged.
The new production corrections are in the application controller, Shared Track
dialog, and existing track controller; the other production source is reused.
Final candidate SHA and completed local/hosted evidence belong in its PRE_KAREN
PR body. Original component green does not certify this composition.

## Reproduced failures

1. Art: failed Reset Invite followed by successful retry produces a new native
   room, but terminal conductor/lifecycle state can keep the room marked failed.
2. Music: even with a failed native close and no room identity or valid invite,
   a fresh Record reaches its recorder callback and Play reaches component
   lookup. This happens with an exception and a wrong-generation close receipt.
3. A Play worker queued for an old room can execute after replacement. Inside
   the core controller, cancellation while capability or paused-route health is
   being inspected previously did not prevent later preparing/resuming playback.

## Recovery boundaries

Reset does no work while Quit, session cleanup, invitation switching or another
Reset owns the change. A successful explicit current-native replacement with a
new identity/generation can retry the terminal room conductor. An owned Music
startup or unready primary engine retains its own token and recovery failure.
Healthy rotation preserves the active conductor token.

Failed native cleanup blocks new recording/track intent at the controller action
boundary before a queued UI receipt can render. Shared Track describes room
recovery separately from local engine readiness, disabling new Play/restart
while preserving eligible Pause, Stop and local trim. An independent primary
failure retains its more immediate guidance and existing control restrictions.
A current recording keeps its own Stop/Finish action.

Reset advances a room-intent counter checked before queued Play work executes.
It does not advance the general audio-session teardown counter: native room
reset must not make a late Play postcheck stop an already published track.
The core cancellation epoch also covers capability and resume-health work
before ROUTING. It retires an unpublished prepare, preserves already published
playback and recording ownership, and does not suppress real cleanup failure.
Recovery never replays an earlier refused Record/Play gesture.

## Meaningful automated journeys

- `tests/test_two_session_art_host_recovery.py`: actual controller/native owner,
  failed-close retry, Notes/edit state and persisted bytes, temporary Conversation,
  stale lesson/connection callbacks, correct room guidance, healthy rotation,
  cleanup guards, and established context after exact admission expiry.
- `tests/test_two_session_music_host_recovery.py`: invalid/failed close receipts,
  denied fresh intent, confirmed retry, real recorder Stop decision, synthetic
  active track Pause/trim/Stop, queued old Play and published-playback races,
  plus separate primary failure and owned startup boundaries.
- `tests/test_shared_track_start_cancellation.py`: deterministic held capability,
  health, prepare, published playback and backend cleanup boundaries using
  synthetic audio data and controlled in-process receipts.

Tests retain production ownership decisions while intercepting processes,
network, providers, devices and capture. Controlled recording receipts do not
record a performance. Existing native/reference-service loopback integration
and complete suite run separately on the frozen candidate.

## Still open

These checks do not prove two different homes can join, or that lesson narration,
faces, per-feed levels, Music audibility, sustained physical duration and latency
meet the user acceptance script. Public-network architecture is still awaiting
an explicit decision. No second media engine or public profile is introduced.
Karen is unavailable; independent security/leftover/ten-second UX review must
match the exact candidate. OPEN DRAFT only; no merge, signing or release.
