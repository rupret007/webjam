# Internet joining: verified service control

Jeff authorized private-invitation Internet joining on 2026-09-09. This is the
first transport slice toward ordinary people joining from different homes.

## Observed failure and priority

The current native client only opens plaintext `reference-local` control.
It cannot authenticate a provisioned Internet service, and its fresh host-close
path also targets localhost. A service's existing TLS listener therefore cannot
be used by the native client. This blocks the approved remote joining path before
Art or Music can exchange room state. More invitation copy cannot supply it.

## Before and after

Before: initial control and fresh authenticated removal hardcode local dialing.
After: an immutable connector supports verified TLS 1.3 control, and both initial
connection and fresh close use the same bound endpoint. Unknown/modified profiles
are rejected before work starts. Existing local behavior is retained.

The actual Python service and Go client are exercised with temporary test trust
and a loopback DNS route: host registration, invitation-only guest enrollment,
role authorization, bidirectional opaque signaling, failed unverified close,
then successful fresh close with the original authority and observed removal.
No system trust, real DNS, live server, user credential or media is involved.

## Scope and dependency

Fresh branch `codex/internet-control-tls` explicitly depends on OPEN DRAFT #112,
`df18426dd195dcda8265d6979d869eaad8a463f8`. It needs the unmerged lifetime and
fresh authenticated cleanup already composed there. Earlier drafts are unchanged.

The shipped profile registry remains local-only until provisioning, host
admission and relay protections are implemented and reviewed. This slice does
not make Internet joining available in the app. That larger outcome remains the
active goal; this is its necessary secure control foundation.

Next stages: host admission; relay return-path/reachability and resource bounds;
provisioned profile/ordinary Host routing; native Art names and lesson requests;
full same-invitation Art/Music journeys; separately approved deployment and
physical two-home tests. Existing local Notes/Conversation, two Art cards/squirrel,
Music routing and guest-never-seek protections remain.
