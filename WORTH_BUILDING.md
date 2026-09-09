# Internet invitations: bounded setup and owned shutdown

Jeff approved private-invitation Internet implementation and isolated testing on
2026-09-09. This slice addresses one remaining service prerequisite for both Art
and Music; it adds no guest setup screen.

## Observed failure and priority

The completed-connection cap began after TLS. A stalled peer could therefore use
accepted sockets and handshake state outside that cap. A handshake deadline alone
is not a concurrency bound. Separately, Close could finish while startup was
creating a listener, leaving the late listener outside its cleanup snapshot.
Deterministic lifecycle baselines reproduce that race and missing synchronous
capacity reservations. These resource ownership gaps need fixing before an
ordinary Internet invitation can safely depend on the service.

## Before and after

Before: unfinished handshakes bypassed completed capacity, and successful Close
did not prove retirement of concurrently created listeners. After: a synchronous
acceptance gate bounds pending setup and its start rate before TLS work; verified
connections reserve active capacity before handler scheduling. Owned startup and
teardown retire late resources and remain responsible after caller cancellation.

This beats adding door copy while the authorized Internet path still lacks a
bounded service lifecycle. It is infrastructure progress, not proof that a person
joined, sees/hears the lesson, can pause or has acceptable musical latency.

## Acceptance and dependency

Branch `codex/service-connection-ownership` explicitly stacks on OPEN DRAFT #114,
`89274f756567ed6422b93e193560800ef126667f`, which supplies approved-host allocation
and invitation-only guests. Common fetched master remains
`2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`. Prior drafts remain untouched.

Real owned-loopback TLS cases hold unfinished peers at capacity, release one and
register an approved host, enroll a certificate-free guest, use fresh role-token
Close, and close a mixture of active TLS, incomplete TLS and HTTP peers. Gated
lifecycle tests cover late creation, canceled callers and honest cleanup failure.
Four desktop CI jobs run the complete service suite on actual Python 3.12 event
loops, including Windows Proactor, before the existing desktop packaging steps.
Results must be attached to this slice's exact frozen tip; a workflow edit alone
is not platform evidence.

Service bind settings intentionally accept only unscoped numeric addresses or
`localhost`, avoiding executor DNS work that cannot be reliably canceled. This
does not change certificate DNS identity or expose IPC endpoint overrides.
A broader ordinary-loop burst probe exposed Python's delayed acceptance before
transport attachment even though service counters looked clear. Control and HTTP
therefore need owned raw acceptance; the original failing evidence is retained.

Ordinary host credential provisioning, UDP return-path proof, a compiled Internet
profile, deployment authorization and physical two-home journeys remain open.
Native Art names and lesson requests remain open. Art's two-card door, squirrel,
guest-never-seek, Notes/Conversation and Music routing protections are inherited.
