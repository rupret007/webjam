# WebJam short-code join and pre-connection chat — next architecture slice

> Status: transport foundation implemented; meeting identifier and help UI
> remain gated. The current product still joins with one complete private
> invitation and exposes no pre-Jamulus help control. This plan does not
> authorize a public service, deployment, credential, release, or UI claim.

## Product outcome

A guest should eventually be able to enter a human-friendly meeting identifier
and, when the host chooses, a PIN. A guest whose setup is failing should be able
to exchange a few bounded plain-text troubleshooting messages with the host
without opening another meeting app. Neither convenience may weaken the
current authenticated transport or turn a guessable number into a bearer
credential.

## Current truth

- A v3 invitation carries a 256-bit one-use capability, opaque references, the
  exact host pin, profile, and expiry. It is short-lived private material.
- The Join door accepts that complete invitation, never saves it, and bounds a
  remote attempt. There is no meeting-number lookup today.
- Jamulus chat exists only after an authenticated Jamulus RPC connection. The
  reference sidecar and desktop adapter now expose one bounded ephemeral help
  operation after mutual transport proof, but no product UI invokes it.
- The reference service is self-hostable test evidence. No public WebJam
  rendezvous or relay is deployed or production-approved.

## Secure meeting-identifier decision

A familiar six- or nine-digit number has too little entropy to replace the v3
capability. An optional human PIN is also too weak to be the only authenticator.
Before UI work, choose and threat-model one of these shapes:

1. A high-entropy, grouped, typo-resistant lookup handle (at least 64 bits;
   80-bit Crockford Base32 is the preferred starting point) that retrieves one
   short-lived encrypted invitation capsule.
2. A shorter numeric identifier backed by explicit host approval and a
   reviewed password-authenticated key exchange. The rendezvous must not learn
   a plaintext PIN or be able to perform an offline PIN search.

In either shape, bind the capsule to the exact session, host pin, profile,
generation, expiry, and one guest enrollment. Apply per-source and per-code
attempt budgets, constant public errors, short TTL, atomic consume, Reset
Invite revocation, overload bounds, and abuse monitoring that stores no
musician content. A lookup must never return an address, credential, or
reusable reconnect secret. Security review and deterministic replay/race/rate
tests precede any production endpoint or shortened UI label.

## Pre-connection text decision

Text is allowed only after mutual peer proof opens the authenticated data-plane
gate, even if Jamulus has not opened yet. The implemented foundation extends
the existing protocol rather than creating a second socket or cloud chatbot:

- one allowlisted message type with protocol version, session generation,
  monotonic request ID, direction, and acknowledgement;
- NFC-normalized plain UTF-8, no HTML or attachments, with a small fixed byte
  limit (500 bytes is the initial test bound);
- per-peer token bucket, bounded queue, backpressure, duplicate/replay
  rejection, and immediate retirement on reset, timeout, or generation change;
- ephemeral memory only: no logs, analytics, support bundles, notifications,
  transcripts, or offline delivery;
- a future simple UI surface showing send failure honestly and preserving
  unsent local text without claiming remote receipt.

Unit, adapter, race-detector, and independent local-relay integration tests now
cover authentication and generation gates, wrong role, replay, Unicode byte
bounds, markup, rate and queue exhaustion, receipt binding, send cleanup, IPC
redaction, and bidirectional transport. Disconnect/reset lifecycle coverage is
shared with the existing peer suite. A user-facing transition into Jamulus
chat, packaged two-Mac evidence, and public-service evidence remain **NOT RUN**
until explicitly implemented and performed.

## Delivery order

1. ADR and threat-model review for the identifier/capsule scheme.
2. Help frame, sidecar/IPC, and desktop adapter foundation, local and offline.
   **Implemented**, with no product UI.
3. Identifier lookup architecture and desktop adapter, after step 1.
4. One reviewed Join-door prototype with the help UI behind a development flag.
5. Public-service privacy/abuse review, infrastructure approval, and physical
   two-network testing.

Only after all five gates may the product replace the complete invitation with
a meeting identifier. The product must not advertise help before Jamulus until
steps 4 and 5 provide reviewed UI and real-path evidence.
