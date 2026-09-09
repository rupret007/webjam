# PRE_KAREN — composed invitation recovery and Music listening

Branch: `codex/recovery-mix-composition`; canonical WebJam checkout.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5593490508
State: combined verification in progress. Exact final tip, local results and
hosted checks belong in the draft PR body. No physical or Karen PASS implied.

## Dependencies and leftover honesty

Base #103 `1e35d7ac51a6ab9e5d95f3edbe2a8a68c0b748c5` includes #96–#102.
Replayed #104 `08efa86384bf6378ce49a2e3e3150444dc028081`, #105 ending at
`3811d3f0179bc03e90f7490ed8a9469fd8fec607`, and #106's delta
`e1e4b2ba3c8b4c48f4fc1b246cb63b38a9ddce2d`. Master was fetched and matched
`2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`. The original drafts are unchanged;
this declared dependent branch is allowed by the sustained goal.

Clean production composition alone was insufficient. New replacement-process
journeys reproduced two actual failures: obsolete cards before new local proof,
and false disconnection after the replacement was proved. The fix rejects a
retired process/monitor's queued roster before changing UI or recording presence.
Current-owner negative evidence still reaches recovery. Component proof and
fixture setup errors are recorded separately from these product reproductions.

A broader combined run also exposed readiness work queued before successful
application shutdown executing afterward. It still changed closed-room UI and
read microphone permission state. An isolated reproduction confirmed the real
shutdown flag; completed shutdown now retires that queued readiness work.
Canceled shutdown retains its live readiness behavior.

## Security and ownership

- Revalidate process generation, PID and RPC monitor epoch at UI consumption,
  before even an empty retired roster can change current ownership or cards.
  Source-less calls retain the existing compatibility seam; the registered
  production callback carries typed identity. Current proof still requires a
  real local row, live intended process and fresh RPC under existing gates.
- A remote-only roster never auto-restores a mix or claims local connection.
  Exact participant/client/epoch checks and the final socket fence retain native
  gain ownership. Saved mix matching never authorizes a connection or identity.
- Saved Solo includes optional personal mute metadata; old snapshots only restore
  information they contain. Whole mix state is resolved before native commands;
  multi-channel native writes remain sequential, not an atomic audio transaction.
- Rejected private-network admission stops the old attempt. Full-message Join
  still requires explicit acceptance and successful owned cleanup. Temporary
  Conversation never falls back to an unrelated personal link.
- Native room Reset ownership is distinct from primary audio process ownership.
  Failed room cleanup retains active Stop and local listening but blocks fresh
  Play/Record. Primary cleanup keeps its stronger teardown fences. Stale callbacks
  must not release either owner or replay media intent.
- Existing invitation validation, bounded workers, authenticated native transport,
  admission/lifetime bounds, certificate expiry and acknowledged cleanup remain.
  No public endpoint, secret logging, browser/meeting launch or capture is added.

## Ten-second UX and testing limits

A rejected invitation offers Paste New Invite; temporary network faults retain
Try Again. Cancel/retry preserves work. A fresh Music room shows the current
participants and their saved listening choices after actual local proof; an old
connection cannot replace those cards or falsely send the room into recovery.
Art retains Make together/Paint along, its squirrel and host-authoritative
playback. External lesson and camera/microphone controls remain with the existing
meeting/browser provider; this draft claims no control over them.

New journeys use production controllers, handlers, parsers, Qt cards and native
JSON serialization with controlled machine/readiness/socket/worker boundaries.
They do not prove real acoustic output or two-home connectivity. Real sidecar
checks and desktop builds are separate evidence, also not physical audio proof.
Run the full application/native/service bar and both hosted events on the final
tip; record failures and reruns honestly. Short two-person acceptance remains
NOT RUN until explicitly performed. Public-network design decision, Art sound/
faces/pause usability, Music routing/latency/endurance and Karen are still open.

## Holds and handoff

All PRs remain OPEN DRAFT. No merge/squash/tag/sign/release/Pages/Release Trust/
Publish/deploy/spend/live Cisco, automatic recording, unsolicited messages,
short-code/public rendezvous, second video/music engine, other repo or new Goal.
Unsigned0.27.2 is Jeff-only. Parked #37/#49 and dependency tips stay untouched.
Four-hour codex lease before push; timestamped BEFORE/AFTER; release at handoff.
Codex self-QA is distinct from independent Karen leftover/security/ten-second UX
PASS and from physical acceptance. The sustained goal remains incomplete.
