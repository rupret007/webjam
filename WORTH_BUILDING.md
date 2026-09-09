# Internet invitations: approved host allocation

Jeff approved private-invitation Internet implementation and isolated testing on
2026-09-09. This slice addresses a service prerequisite for both Art and Music.

## Observed failure and priority

The service accepted configurations exposing control or relay without host
admission. Any reachable caller could allocate rooms using newly chosen tokens;
room tokens protected later operations but did not authorize allocation. Seven
configuration baseline cases reproduced the gap. Bounded connection tests also
reproduced ten unbounded response/cleanup or closed-dispatch failures, plus a
real loopback shutdown failure. Fixture and sandbox failures are recorded apart.

## Before and after

Before: an anonymous remote client could reserve room capacity. After: an exposed
service requires direct TLS and an approved, currently valid host certificate to
register. Each approved host has a fixed rate bucket and room quota. Guests still
join with their existing private invitation and need no client certificate.
Fresh room-token-authenticated Close also remains certificate-free. The app's
ordinary host credential provisioning remains unfinished; this is not yet a
user-accessible Internet hosting flow.

Response and retirement waits now have deadlines. Shutdown rejects late handlers,
stops listeners, wipes rooms and aborts tracked peers before joining tasks within
one overall budget, including pending TLS handshakes.

## Acceptance and dependency

Fresh branch `codex/internet-host-admission` explicitly stacks on OPEN DRAFT #113,
`68deee7eb21c584866f894fb2401da2b3dc569b0`, which supplies verified native TLS and
fresh authenticated cleanup. Common fetched master remains
`2aba2f2f72f94d56f5c7f810fbe7ca326c5502b2`. Prior drafts remain untouched.

Real temporary loopback TLS tests exercise approved hosts, absent/unapproved and
invalid certificates, invitation-only guests, role-token boundaries, fresh Close,
expiry during a connection, per-host quota, restart revocation and shutdown with
an unfinished handshake. No system trust or live infrastructure changes occur.

Remaining prerequisites: pending-handshake concurrency bounds, UDP return-path
proof, ordinary host credential issuance/storage/recovery, an approved Internet
profile, deployment authorization and physical two-home journeys. Admission does
not prove return-path reachability or public service readiness. Native Art names
and lesson requests also remain open. Existing Art door/squirrel, guest-never-seek,
Notes/Conversation and Music routing protections remain inherited unchanged.
