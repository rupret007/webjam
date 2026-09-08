# PRE_KAREN — independent admission and established-session lifetimes

Base: `2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`, fetched `origin/master`.
Branch: `codex/established-session-lifetime`; canonical WebJam checkout only.
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5589815415.
Review state: self-QA in progress; no Karen PASS or final-tip hosted claim.

## Leftover honesty

An active enrolled room was still governed by the original invitation expiry
in two independent places: reference-service retention and the native fabric
operation context. The service baseline accepted both peers' authenticated
keepalives at elapsed second 599 and removed the room at 600 with default
configuration. Its most recent valid activity was one second old, so this was
absolute admission expiry, not the 90-second idle timeout.

The correction keeps admission and live operation distinct. Unenrolled rooms
still expire at their original admission deadline; enrollment remains one use
for one guest. The service gives an enrolled room a maximum eight hours from
original registration, configurable downward, alongside its existing idle
timeout. Registration/enrollment response fields still carry the original
admission TTL, including zero whole seconds for valid enrollment in the final
fractional second. They never advertise eight hours as a fresh invitation TTL.

The service can observe successful enrollment, not native mutual authentication.
A bearer holder can still enroll and abandon proof. Finite active/idle limits,
authenticated host revocation, capacity/rate controls, and native admission
checks continue to bound that case. No bearer renewal or second guest is added.

## Native ownership and cleanup self-QA

- Native setup uses a child context bounded by the invitation deadline. Service
  bootstrap, mutual peer proof, and the room handshake must complete before
  admission succeeds. A late or canceled setup cannot become a live operation.
- After proof and the handshake, the setup child is canceled without canceling
  the established operation. Promotion is decided at that boundary, not by the
  later delivery of a buffered `peer_connected` event.
- The owned operation has a finite maximum eight-hour lifetime and is clamped
  to the actual ephemeral certificate expiry. A reused host identity can expire
  sooner. Parent cancellation, Leave/Reset, failure, and shutdown remain active.
- Live media and room-control workers share an owned pump context. Teardown
  cancels/interrupts that work and joins the workers before finishing. If bounded
  local Close fails, the runner retains the operation and identity for cleanup
  retry, fences callbacks/application authority, and refuses a second operation.
  It does not emit a successful close receipt for unfinished local work.
- Review reproduced a Python reset recovery gap: the generic invitation owner
  retires its bearer after failed revocation, so the native host needs separate
  cleanup ownership. Reset must retry the pending close before registration,
  keep the old room and Copy Invite unavailable, reject stale callbacks, and
  preserve ordinary retry after an opening failure that created no operation.
  Only a typed successful close receipt for the current host generation/profile
  clears pending ownership. An unavailable IPC client is not a reap receipt;
  explicit End Room retains responsibility for stopping that process.
- Host service cleanup makes one fresh authenticated `reference-local` control
  connection with the existing role token, generation, and next replay-safe
  control sequence. Dial and acknowledgment share a bounded shutdown budget;
  this is not an unbounded reconnect loop or a new enrollment.
- The service may have closed the original control connection after 30 idle
  seconds. The fresh attempt addresses that path, but a failed or uncertain
  response still leaves remote removal unconfirmed. `peer_closed` confirms local
  teardown only. Service idle and hard active limits remain the backstop; the
  code does not invent a remote-deletion acknowledgment.

## Security and ten-second UX self-QA

- The service hard ceiling is measured from original registration, not guest
  arrival, a later authenticated request, or a reset of its activity clock.
  Invalid authentication, replay, wrong generation, and changed endpoints cannot
  refresh idle activity. Expiry and authenticated host close wipe relay keys,
  endpoints, queued signaling, and release capacity while retaining bounded
  registration tombstones.
- Existing exact-peer routing, token separation, one-use enrollment, generation
  checks, replay windows, fixed errors, queue/memory limits, traffic budgets,
  and privacy-safe diagnostics remain in place. The new active-limit setting
  rejects nonintegers, booleans, nonfinite values, zero/negative values, values
  over eight hours, and values shorter than the maximum configured admission TTL.
- The compiled profile and endpoint allowlists are unchanged. No public
  rendezvous, short-code, configurable desktop address, listener change, secret
  logging, media capture, or new application/network service is introduced.
- The user-facing intent stays simple: once two people are connected, an
  invitation's admission deadline should not interrupt their room. Actual idle,
  identity, hard-cap, peer-failure, or explicit-close conditions can still end it;
  the app must not claim connection after those receipts say otherwise.
- The two product targets remain an easy two-artist shared painting lesson with
  conversation and a useful two-musician rehearsal with or without a reference
  track. This lifecycle slice does not add or prove video/audio feed controls,
  YouTube playback, low-latency physical rehearsal, or easy public-network join.
- Art retains exactly Make together + Paint along → Host/Join and the existing
  squirrel-with-fro mark. Conversation/Webex sharing stays beside WebJam;
  guest seek authority and existing media/meeting ownership stay unchanged.

## Verification status at documentation preparation

| Evidence | Status |
| --- | --- |
| Complete reference-service suite | **79 passed**; fake-clock lifetime tests plus existing controlled loopback/process tests |
| Reference-service Ruff, compileall, diff check | Passed |
| Initial focused native race tests | Passed; preliminary focused evidence |
| Python reset and invitation ownership | **64 passed**; four failed-close baseline cases reproduced before the fix, with strict receipt, stale callback, unavailable IPC and ordinary opening-failure controls |
| Full local application and native suites | Pending final frozen source |
| Real independent service/native integration | Focused race run passed: real mutual proof, data/control after setup-child cancellation, fresh-connection service close, and held-worker teardown; final frozen-source suite pending |
| Hosted workflows and four desktop builds | Pending final draft tip |
| Physical 60-minute Art/Music session, live audio/meeting, public reachability | Not run; no claim |

The strengthened real native integration deliberately closes the original host control connection, verifies authenticated data and room controls still work after enrollment, and checks that fresh host cleanup removes service state. A controlled endpoint holds one canceled worker: local Close times out without closing the operation receipts until that worker exits. This is isolated loopback transport evidence, not audio or physical duration proof.

New service evidence covers authenticated data and keepalive after admission,
late/failed enrollment, both default and lowered hard caps despite traffic,
fractional-second wire TTL compatibility, idle expiry after rejected traffic,
host-only close, state erasure, tombstones, and capacity release. Controlled
fake clocks establish the service boundary without waiting eight hours.

The PR body and coord AFTER must record final local/integration/hosted results
and the exact tip SHA. A green earlier draft or focused run does not establish
green checks for this tip. Do not treat this document as a completed physical
session test or independent review.

## Draft and release holds

This branch starts independently from master. Drafts #96–#101 remain unchanged;
#101's hosted-green handoff is separate evidence for its own tip. Parked #37/#49
remain untouched. Karen is unavailable; this work remains OPEN DRAFT PRE_KAREN
until independent leftover + security + ten-second UX review passes on the
matching tip. Codex self-QA is not Karen PASS.

No merge/squash/tag/sign/release/deploy/Pages/Release Trust/Publish/send/spend/live
Cisco. Unsigned 0.27.2 remains Jeff-only. No short-code/public endpoint/profile,
second video stack, Drawpile/shared-canvas work, other-repo lane, second WebJam
Goal, or parent injection. Keep this draft reviewable under the current holds.
