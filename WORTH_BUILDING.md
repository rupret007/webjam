# Worth building — invitation recovery and a stable Music listening mix

Branch: `codex/recovery-mix-composition`, canonical WebJam checkout.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5593490508

## Declared composition

Base #103 `1e35d7ac51a6ab9e5d95f3edbe2a8a68c0b748c5` already composes the
Art/Music journeys from #96–#102. This branch replays #104
`08efa86384bf6378ce49a2e3e3150444dc028081`, both #105 commits ending at
`3811d3f0179bc03e90f7490ed8a9469fd8fec607`, and #106's delta
`e1e4b2ba3c8b4c48f4fc1b246cb63b38a9ddce2d`. PR base is #103's branch,
`codex/two-session-recovery-integration`. Fetched master remains
`2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`. Original draft tips are unchanged.
Production hunks applied cleanly; overlapping handoff documents were rewritten
for this candidate. Separate component CI is not combined verification.

## Observed failure and before/after

The previous combined candidate lacked the newer invitation and listening-mix
fixes. A rejected invitation could still offer an ineffective retry; restoring
saved faders did not restore native gains or preserve personal mute choices
through Solo. The component fixes must coexist with real failed-room cleanup,
retained Notes/Conversation, fresh Music joining and replacement audio owners.

New composed tests found an additional failure: a queued roster from a retired
Music process replaced the replacement's participant cards before local proof,
and demoted a healthy replacement after proof. Native gain ownership was already
protected, but screen and recovery state still accepted obsolete evidence.

The UI now rejects a supplied roster unless its process generation, process ID
and monitor epoch still match the current owner. This check precedes participant,
recovery and recorder-presence changes. Current-owner failure evidence remains
usable even when the process has died or RPC is stale. It neither manufactures
connection proof nor starts audio.

After: a rejected invite leads to a fresh explicit Join; the joined room owns its
Conversation; a proved current Music connection restores saved personal listening
choices and cards. Failed room Reset still blocks fresh Play/Record while keeping
local listening controls and an existing recording's Stop. Retired connection
messages cannot undo the replacement's displayed state.

A broader combined run also exposed readiness work queued before successful
application shutdown executing afterward. It still changed closed-room UI and
read microphone permission state. An isolated reproduction confirmed the real
shutdown flag; completed shutdown now retires that queued readiness work.
Canceled shutdown retains its live readiness behavior.

## Acceptance and evidence

- Exercise full pasted invitation, cancellation, failed cleanup/retry, stale
  receipts, actual Music peer handoff and remote-only versus local roster proof.
- Exercise Save/Load with both Solo mute histories during failed native Reset,
  preserving Notes, Conversation, source ownership and Stop/fresh-intent fences.
- Replace the actual controller/RPC owner, hold old gain and UI deliveries, and
  verify restored native JSON commands and cards without replay or false recovery.
- Run the repository's full local bar and hosted CI on the frozen composed tip,
  including four desktop builds. Keep baseline failures and fixture corrections
  distinct. Final counts and exact SHA belong in the PR body.

Machine/process/authentication/socket receipts are controlled in new journeys;
no real device, meeting or recording starts. Physical two-home joining, faces,
lesson narration, independently audible levels, Music routing, latency/endurance
and laptop usability remain unverified. The remote-network decision remains
unanswered. This draft is a combined candidate, not completion of the goal.

## Holds

OPEN DRAFT PRE_KAREN only. Independent Karen review remains pending.
No merge/squash/tag/sign/release/Pages/Release Trust/Publish/deploy/spend/live
Cisco, unsolicited send, automatic capture, short codes/public rendezvous,
second media engine, other repo, parent injection or new Goal. Unsigned0.27.2
stays Jeff-only. Art's two-card door, squirrel and guest-never-seek remain.
Parked #37/#49 and source drafts remain untouched. Maintain the four-hour codex
lease, timestamped BEFORE/AFTER in America/Chicago and release at handoff.
