# PRE_KAREN — Music Mute and Solo reach the native mix

Independent master base: 2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2.
Branch: codex/music-effective-listening-mix.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5592279206

## Product delta and leftover honesty

Mute and Solo affect a musician's personal monitor mix. A fader edit must retain
the chosen level without undoing effective mute. Roster entry during Solo and
departure of the soloed musician must update the native mix as well as the
participant display. Existing code updated only the dormant UDP path in those
roster transitions.

The initial native-command regression suite reproduced 10 failures and 7
passing controls, using the actual controller, gain mapping and JSON framing
with an in-memory socket boundary. Real Qt Mute and keyboard-fader journeys
also reproduced nonzero gains while Mute stayed selected. Test-fixture setup
failures are retained separately from these valid product baselines.

Review additionally reproduced independent gain workers delivering an old
fader after a newer Mute, and a queued old write crossing into a replacement
RPC monitor epoch. Native dispatch must preserve current effective gain and
its originating connection/participant. Tests explicitly distinguish queued
cancellation from bytes already sent.

Real Qt Solo testing also found the existing-card projection copied identity
and connection fields but omitted current mute, Solo and fader state. Retain
the authority checks and refresh existing cards from the current local mixer,
not detached queued roster fields. Explicit Mute/Solo gestures refresh these
listening fields without changing connection proof. Blocked-signal card updates
show suppression/restoration without feedback commands.

The fix does not establish physical sound, independent narration/voice mixing,
different-home joining, or reference-track routing support. It is independent
of #96–#104; separate drafts' green checks do not compose this into #103.

## Ten-second UX

- A musician can move a muted listening fader to prepare the desired level.
  Mute remains selected; Unmute applies that chosen level.
- Solo suppresses other channels until explicitly released or its musician
  leaves. Prior mute choices and current fader levels survive restoration.
- A joining musician does not defeat Solo. An ordinary unchanged roster
  refresh does not reset the listener's mix.
- The guide distinguishes personal listening mix from outgoing microphone and
  host source trim. No new door control, configuration choice or media action.
- Tests exercise actual visible cards and keyboard input through application
  handlers. Offscreen automation is not installed-laptop feel or audibility.

## Security and ownership

Use the existing authenticated native command and participant model. No new
endpoint, transport, credentials, recording or meeting handoff. Native gain
remains bounded to the existing 0–127 model / 0–100 command mapping.
Missing channels and retired participant/monitor owners must not receive queued
writes. Preserve native method allowlisting and epoch/socket checks.

One coalesced worker holds at most one pending item per current channel.
Each item binds the RPC client, monitor identity and exact participant object.
Before sending, it rechecks those owners and reads the current effective level;
the actual RPC send also checks the originating epoch. Slow native writes hold
neither the participant nor dispatch lock. New intent during a write follows it
on the same worker. Stop retires pending intent, closes RPC, and performs a
bounded join. A still-owned worker prevents restart until its exit is confirmed.
An already-entered write cannot be described as canceled bytes.

No per-channel listening Mute is promoted to live microphone-send mute.
No fader movement supplies meter or connection proof. Art's two-card door and
host/guest playback authority remain unchanged. Saved-mix restoration and pan
are not certified by this slice; physical listening/routing remains NOT RUN.

## Verification and review

Run the repository's full local bar at the frozen tip, including Art start UX,
isolated application modules, native race/static/module/cross-build checks,
real sidecars, service tests and dependency/UX checks. Run both hosted workflows
at that same tip, including all four desktop builds. Exact results, retries,
skips, tip SHA and evidence links belong in the PR body and coord AFTER.
Do not infer final or hosted green from this source document.

Codex self-QA is not Karen PASS. Keep OPEN DRAFT, retain original component and
parked heads, release the lease after handoff, and continue useful independent
work under the sustained goal. No merge/squash/tag/sign/release/Pages/Release
Trust/Publish/deploy/spend/live Cisco/public rendezvous/short codes, automatic
capture, other repository or new goal. Unsigned 0.27.2 remains Jeff-only.
