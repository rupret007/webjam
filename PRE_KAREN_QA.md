# PRE_KAREN — Art shared-lesson pause and ready requests

Branch: `codex/art-lesson-requests`; base #107 exact
`392c959a74af135fe2a92c13ef09fbfe541fb476` (`codex/recovery-mix-composition`).
BEFORE: https://github.com/rupret007/Bob-the-Bot/issues/3#issuecomment-5593972013
Verification is in progress. Exact final SHA/results belong in the draft PR
body. No physical acceptance or independent Karen PASS is implied.

## Leftover honesty and ten-second UX

A guest previously had only spoken pause requests while following the shared
browser lesson. The existing Art Conversation helper now offers **Ask for a
pause** and **Ready to continue**. The host sees a temporary named request and
chooses **Acknowledge request**. The host still operates the browser manually.
Requests do not seek, pause, resume, launch or affect any media or device.

Guest text distinguishes sending, delivery uncertain, delivered, acknowledged,
refused and expired. There is no claim of synchronization or everyone ready.
Old/native/unsupported rooms retain **Ask the host aloud**. Ordinary Art door,
Make together, Notes, focus and the current meeting remain intact. The host
may miss a notice while watching a browser; physical notice visibility and
response remain unverified. An in-app acknowledgement is insufficient proof
of actual video/narration stopping or of independent received audio mixing.

Accepted in-room meeting edits retire request authority and stale notices while
preserving the useful host/guest lesson guidance. Requests stay inactive until
explicit helper reentry. WebJam cannot detect an arbitrary source change in the
external browser: changing that lesson requires explicit helper exit/reentry to
retire its prior request context; no automatic browser-source detection is claimed.

## Security and ownership self-QA

- Reuse the existing private-LAN listener and guest worker. Authentication uses
  existing participant/bearer proof; no unauthenticated activation/ACK route.
  No change to LAN address validation, native transport or durable room schema.
- The fixed optional state extension carries only this guest's current receipt.
  Requests contain no URL, free text, media, name or chosen destination.
  DTO repr and fixed error messages reveal no identifiers or request contents.
- Retain at most 32 admissions per host context until retirement. Each keeps
  one high-water revision/latest receipt; no request-history or eviction cache.
  Duplicate intent/revision is idempotent even after acknowledgement/expiry;
  older revisions remain superseded. An invitation holder can consume slots;
  capacity refusal is honest, not a claim of perfect denial-of-service protection.
- Five-second authenticated-read freshness is rechecked under the model lock
  for requests and ACK. POST and ACK do not refresh room presence. Notices
  last 30 seconds; acknowledgement and retries never extend that deadline.
- Enforce strict object shape/types, 512-byte input, per-person two-second
  throttling and 20 new requests per rolling ten seconds globally.
- Keep at most one in-flight POST and one latest queued explicit intent in the
  existing poll worker. No automatic retry; uncertain retries keep exact tuple.
  Feature faults do not invalidate good room state; actual 401 remains terminal.
- Host activation requires the explicit current shared-lesson action. Captured
  owner/server/lifecycle/room/meeting/request identities fence queued UI work.
  Retire before source/meeting replacement, navigation and cleanup waits,
  including failed Leave and shutdown. Merely hiding the window keeps notices.
- Registry names use a bounded, nonblocking name-only host projection and Qt
  PlainText. No guest receives another guest's name, capability or receipt.
  Names are chosen labels, not proof of a unique human identity.
- The one-second POST socket timeout is not an absolute end-to-end deadline
  against a trickling peer. Existing bounded response and cleanup rules remain.

## Verification and remaining acceptance

New isolated model, authenticated localhost HTTP, worker and real widget tests
cover ordering, lost replies, expiry, capacity, limits and unsupported/refused
states. Combined real-controller journeys cover host ACK/ready, Notes, focus,
ordinary navigation versus OS hiding, both meeting replacement paths, route
loss, old signals, failed Leave, End-worker ordering and actual shutdown.
Controlled socket/OS/worker boundaries are explicit; no live provider or audio
was opened. Preserve fixture/setup errors separately from observed product failures.

Run the full application/native/service/static/UX bar and both hosted events
on the final tip, including four desktop builds. Preserve skips/retries/failures.
The draft body owns the final counts and source SHA, not this pre-freeze file.

Physical two-home joining, actual lesson/faces/narration, host response,
independent Art audio controls, Music routing/latency/endurance, package selection
and Karen review remain pending. The public-network decision is unanswered.
No automation result establishes any of those physical claims.

## Holds

OPEN DRAFT only. No merge/squash/tag/sign/release/Pages/Release Trust/Publish/
deploy/spend/live Cisco, automatic recording/capture, unsolicited messages,
short-code/public rendezvous, second media engine, other repo or new goal.
Unsigned 0.27.2 is Jeff-only; parked #37/#49 and dependency tips untouched.
Four-hour codex lease before push, timestamped BEFORE/AFTER, release at handoff.
Codex self-QA is separate from Karen leftover/security/ten-second UX PASS.
