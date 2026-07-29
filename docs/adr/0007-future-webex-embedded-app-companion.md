# ADR 0007: Future Webex Embedded App companion

- Status: Proposed; not implemented
- Date: 2026-07-28
- Scope: Optional in-meeting companion surface

## Context

WebJam currently opens a musician-supplied Webex Meeting or Personal Room link
externally. Webex owns sign-in, membership, camera, microphone, speakers, and
leave state. Jamulus and the WebJam desktop remain the performance-audio path.
ADR 0004 preserves that truthful boundary.

A future Webex Embedded App could give participants a small shared WebJam
surface inside an existing Webex meeting. It must complement the desktop
conductor, not recreate WebJam or move music processing into Webex.

## Proposed decision

Limit the embedded surface to focused collaboration:

- truthful WebJam/Jamulus session status;
- bounded shared notes and timestamped markers;
- an invite handoff that never exposes a private session secret in the page;
- role-gated requests to start or stop the desktop-owned recording workflow.

The desktop remains authoritative for the session, musicians, recording,
Reference Track, files, and every audio route. A button press is only a
request; the embedded surface reports success after the desktop publishes the
result. It never captures, processes, relays, or monitors Jamulus/Webex media.

Use Cisco's current Webex Embedded Apps Framework 2.x, not the deprecated 1.x
SDK. Register a private development app in the Webex sandbox first, targeting
the in-meeting panel and Webex sidebar. The app needs a separately reviewed
registration, HTTPS start page, explicit valid-domain inventory, organization
administrator approval, responsive light/dark layouts, keyboard/screen-reader
support, and graceful operation when private Webex identity is unavailable.

This does not embed the native Webex application inside WebJam. The useful
direction is the inverse: a small hosted WebJam control surface appears inside
Webex while a signed desktop WebJam agent runs the real Jamulus, device,
recording, Reference Track, and file workflows. Once paired, the desktop may
minimize to the notification area, but it must remain independently visible
and recoverable for audio setup, permissions, diagnostics, and failure
recovery.

All browser-to-service traffic uses HTTPS or WSS. Account authorization uses
the documented Webex OAuth flow with minimum scopes, explicit consent, expiry,
revocation, and role checks. No client secret is shipped to browser code.
Durable tokens remain in a trusted service or OS credential store, never local
storage, URLs, invitations, notes, logs, or support bundles.

Desktop synchronization needs authenticated, short-lived session grants,
origin binding, replay protection, revisioned/idempotent commands, bounded
payloads and rates, reconnect recovery, and explicit host approval before a
meeting is linked to a desktop session. Provider identity alone does not grant
WebJam host or recording authority.

The embedded surface contains no Webex password, WebJam credential, OAuth
token, Jamulus RPC secret, private endpoint, filesystem path, raw diagnostic,
audio, video, waveform, take, or song media. Shared text is bounded, escaped,
retention-defined, and omitted from public diagnostics.

## Delivery phases

1. **Sandbox proof:** host a minimal HTTPS 2.x Embedded App with the trinity
   identity. Prove sidebar and in-meeting contexts, theme/accessibility,
   development-app discovery, organization approval, two participants, reload,
   sign-out, and failure recovery. It may show only synthetic session state.
2. **Secure pairing:** add an outbound-only desktop connection to a bounded
   WebJam relay. Pair through a one-time, short-lived code shown by the desktop;
   require explicit host approval and bind the grant to one Webex context and
   one WebJam session. Prove expiry, replay rejection, revocation, reconnect,
   rate limits, and complete redaction before enabling commands.
3. **Useful companion:** expose truthful connection/latency/recording and
   Reference Track status, shared notes/markers, invitation handoff, and
   role-gated requests. Every command is idempotent and reports requested,
   accepted, confirmed, rejected, or timed out; Webex presence is never treated
   as Jamulus membership.
4. **One-stop polish:** allow the native desktop to minimize after audio setup,
   surface an explicit **Open WebJam Audio Controls** recovery action, add
   support-bundle correlation IDs that reveal no secrets, and certify Windows,
   macOS, browser, iOS, Android, sidebar, meeting, offline, and managed-tenant
   behavior before broader distribution.

## Consequences and acceptance

External Webex launch remains the complete fallback and is unaffected by
embedded-app or sync failure. Before implementation, this proposal requires a
threat model, Cisco review requirements, OAuth/scope review, accessibility and
content-retention design, secure-sync protocol tests, two-participant physical
validation, and proof that disconnecting the companion cannot interrupt
Jamulus, recording, or the desktop session.

The v0.22 desktop release implements only native-app detection, official Cisco
installer handoff, validated external meeting launch, and privacy-safe
diagnostics. None of the hosted, OAuth, relay, embedded, or minimize-to-agent
phases above are represented as shipped.
