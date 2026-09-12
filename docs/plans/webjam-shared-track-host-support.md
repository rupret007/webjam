# Shared Track: supported hosting and deliberate recording

## Worth building

A Windows/Linux host can load a valid reference track but cannot send it with
this build. The existing panel nevertheless promised that setup and Recheck
Route would enable Play. Its close action was also below the visible compact
viewport. This turns the first rehearsal into a setup loop without a solution.

The recording refusal hardcoded Mac setup for every unavailable reason. Review
also reproduced a behavioral gap: replacing a valid track with an invalid file
retains the original source in FAILED, but the READY/PAUSED-only refusal lets
Record proceed without planning that retained track. A pending first load can
similarly be omitted before a decoder supplies the source.

The change makes unavailable host support explicit, keeps local inspection and
removal useful, and returns to rehearsal without discarding the track. A new
recording waits for a selected track to finish loading or recover; only explicit
removal permits deliberately recording without that retained source. Existing
recording Stop and supported active-track requirements remain intact.

This is independent of pending #96/#97/#98, based on master
`2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`. It does not add sending support or
claim the physical rehearsal is complete.

## Acceptance and platform truth

- Windows/Linux/unknown hosts see unavailable sender support, safe file
  inspection/removal and Back to rehearsal. Recheck does not imply a solution.
- Missing-route Mac hosts retain actual device setup and Recheck. Eligible
  16ch/64ch Mac capability remains subject to primary ownership, route and
  cleanup checks. No check is bypassed to enable Play.
- The same compact-layout close action is visible, keyboard reachable and
  preserves the loaded file and primary rehearsal.
- Failed replacement preserves the original source and refuses new recording.
  Pending or loading replacement cannot record the old source; first loading
  cannot silently record without the selected track. Completion never starts
  recording later; a fresh explicit Record is required.
- Routing/playing keep the track required in the recording plan. Active
  recording Stop remains available even if the track subsequently fails.
- Remove never changes the user's original media file. If cleanup is pending,
  removal remains unavailable until the existing Stop/retry succeeds.

| Role/platform | Current support | Evidence limitation |
| --- | --- | --- |
| Supported Mac sends Shared Track | Existing factory and runtime route checks | Exact candidate physical routing/audibility NOT RUN |
| Windows/Linux sends Shared Track | No sender backend in this build | Cannot be repaired by installing a driver/rechecking |
| Guest receives host track | Ordinary Jamulus participant audio | Cross-platform physical listening/mixing NOT RUN |
| Any desktop build completes | Packaging/build evidence only | Does not certify routing, audibility or latency |

## PRE_KAREN self-QA

**Leftover honesty.** A decoded source, a routing capability and an audible track
remain separate. Timing copy no longer promises equal delay merely because the
song uses the music path. No supported-platform claim is inferred from four
builds or from an unavailable backend's previous “needs physical proof” text.

**Security and ownership.** Three existing bounded capability reason codes select
presentation only. Backend preparation, current host/primary process ownership,
RPC freshness, device isolation and teardown still decide whether audio runs.
No new setting, permission, token, device, network endpoint or external launch.
New Record refusal clears a prospective track plan without touching an active
take; it cannot schedule delayed playback or recording after recovery.

**Ten-second UX.** The unavailable host sees the actual support limit and a
close-only return to rehearsal. Supported hosts retain their real next setup
step. Removing the selection is explicit and separate from closing the panel.
Guests keep ordinary receive/mixer controls and gain no transport authority.

**Automated evidence.** The new tests use production capability selection,
actual Qt controller/dialog/deck actions and decoded synthetic WAV files.
Device inventories, owned-primary/RPC evidence and recording/backend actions
are controlled test fixtures; no live device, real meeting or recording is used.
Final counts and exact local/hosted tip evidence belong in the PR body.

**Independent review.** Codex self-QA is not Karen PASS. Karen remains unavailable.
The draft must stay open and unmerged for independent leftover/security review.

## Executable owner checks, not yet run

After Jeff explicitly selects an authorized unsigned candidate:

1. Record exact build ID, full source SHA, platform and architecture. On a
   Windows/Linux host, load a rights-cleared track. Read the support limit,
   tab to Back to rehearsal at ordinary laptop size, activate it and verify
   rehearsal remains connected. Reopen and verify the source remains selected.
2. Attempt Record with that unplayable source: no recorder starts. Remove the
   selection, verify the disk file remains, then explicitly choose Record if
   recording has been authorized for this test.
3. On an eligible Mac, verify the normal setup/route workflow still applies.
   Load a valid source, attempt an invalid replacement, and verify the source
   is retained with error and new Record is refused. Load a valid replacement
   and verify recovery does not start recording or playback automatically.
4. Follow the current physical reference-track pilot for two-endpoint listening,
   independent faders, count-in, pause/restart, device loss and teardown. Record
   candidate identity; historical v0.22.4 instructions are historical evidence,
   not approval to publish or install a new candidate.
5. Compare controlled click timing using one-clock physical measurements;
   subjective late playing, roster presence or moving meters are not latency
   proof. No two-home audibility or latency result is claimed here.

## Holds

OPEN DRAFT only. No merge/squash/tag/sign/release/Pages/Release Trust/Publish,
deploy/send/spend/live Cisco or automatic media capture. Unsigned 0.27.2 remains
Jeff-only. Parked #37/#49 and pending #96 stay untouched. No public rendezvous,
short code, other repository or additional audio/video engine.
