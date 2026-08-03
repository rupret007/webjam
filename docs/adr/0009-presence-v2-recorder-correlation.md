# ADR 0009: Presence v2 uses ordered-roster correlation for recorder media

- Status: Accepted in source; physical multi-endpoint acceptance is **NOT RUN**
- Date: 2026-08-03
- Scope: Jamulus participant-to-recorder correlation and Local Original obligations

## Context

Pinned Jamulus client RPC exposes mixer channel numbers in a namespace local to
that client. Every musician can therefore truthfully see themselves as channel
zero. Jamulus server RPC and recorder files use the server's namespace. A
client-local channel number, a display name, or either value together cannot
safely identify a server recorder stem.

WebJam separately enrolls an installation as a durable, session-scoped
participant. That authentication proves which invited WebJam peer sent a
message; it does not cryptographically prove which Jamulus server row belongs
to a remote peer. Invitations are private capabilities for trusted
collaborators, not an admission mechanism for hostile participants.

## Decision

Presence v2 correlates the two Jamulus RPC views through their common ordered
roster, bounded by the canonical Jamulus limit of 256 rows:

1. The process-bound client RPC observation validates every row and hashes the
   ordered common profile fields (name, instrument, city, and skill) with
   SHA-256. Client-local channel numbers are excluded from that common digest.
2. The single local-zero row gives that exact Jamulus process its own server
   ordinal. Process, RPC-connection, and audio-connection generations bind the
   observation to one live lifecycle. For the host, this local-zero ordinal is
   exact and locally proven.
3. The host installs the digest, count, generations, its private client-layout
   fingerprint, and the ordinals whose complete common profiles are identical.
   It issues a fresh, short-lived, session-scoped challenge and topology epoch.
4. An authenticated guest submits its matching digest, count, lifecycle
   generations, presence generation, capture preference, and self ordinal.
   The host treats the remote ordinal as a cooperative claim. It is not
   cryptographic Jamulus identity and must not be described that way.
5. Only fresh Presence v2 claims may correlate a durable WebJam participant to
   a server recorder ordinal. Legacy channel-based presence remains compatible
   with UI continuity and Local Original transfer, but it is recorder-ineligible
   and is never silently promoted to v2.

The host keeps a complete active lease visible while an unchanged-roster
pending lease renews. It promotes the pending lease atomically once all
required owners renew. At the hard expiry it promotes only fresh claims and
reports absent required owners as missing; stale claims never receive an
unbounded grace period. A digest, count, process/RPC/audio generation, private
layout fingerprint, or ambiguity-set change starts a new topology epoch and
invalidates every prior claim.

## Ambiguity and conflicts

Duplicate display names are supported when the remaining common profile fields
make the full rows distinct. If two complete public profiles are identical,
the host cannot independently distinguish those remote ordinals. Guest claims
for those rows fail closed. The host may still use its own ambiguous ordinal
because local zero proves that process's exact row.

If two enrolled peers claim one ordinal, or one peer changes ordinals within a
topology, WebJam removes all related active and pending claims and tombstones
the participants and ordinals until the host proves a topology change. This is
conservative collision handling, not malicious-peer protection. An invited,
modified peer could still claim an otherwise-unclaimed unique ordinal; without
server-issued per-client attestation, the host cannot detect that lie. That
residual risk is why the invitation boundary remains trusted-collaborator only.

## Recording and reconnect behavior

Recorder attribution and Local Original obligations are intentionally
separate:

- A take unions every accepted `capture=true` preference observed during its
  lifetime, even if the peer later opts out. A take beginning during lease
  rollover also considers current active and pending capture preferences.
- A legacy opted-in peer missing fresh v2 evidence remains a conservative
  expected Local Original and produces a readiness warning, but its local
  channel is not used to select a recorder stem.
- A reconnect retains the durable participant and adds immutable, explicitly
  timed `MediaSegment` records. It does not overwrite an earlier segment or
  guess across a missing, ambiguous, stale, or conflicting correlation.
- Missing or late media remains visible as missing/receiving/needs-attention
  evidence rather than being silently attached to another participant.

## Privacy boundary

Challenges, topology state, accepted claims, ambiguity ordinals, private roster
fingerprints, conflict tombstones, and capture-history cursors are memory-only.
The private host fingerprint and ambiguity set never enter the guest wire
shape. Raw Jamulus profiles, client-local channel lists, network addresses,
operating-system process IDs, credentials, and tokens never enter Presence v2
disk state, logs, or support output. Python `repr` output for transfer proof and
challenge objects is redacted; durable take data retains only the participant
and media facts the recording product requires.

## Consequences and evidence boundary

The design can correlate cooperative musicians without treating client-local
IDs or names as global keys, survives bounded lease rollover, and preserves
truthful reconnect media. It adds short-lived control-plane renewal and can
refuse attribution when evidence is incomplete.

Automated unit, HTTP, lifecycle, privacy, and real-Jamulus/JACK harness tests do
not prove physical recorder attribution or audibility. Two independent
machines, duplicate/identical-profile operation, lease rollover during a real
take, disconnect/reconnect with multiple audible segments, Local Original
delivery, server-stem inspection, and support-bundle privacy against an exact
packaged build remain **NOT RUN** until the release runbook records them.
