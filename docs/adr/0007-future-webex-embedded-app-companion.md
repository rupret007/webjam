# ADR 0007: Future Webex Embedded App companion

- Status: **Reopened** as an optional companion, built on a separate track
- Date: 2026-07-28; rejected 2026-08-21; reopened 2026-08-21
- Scope: Optional in-meeting companion surface

> **v0.22.4 published boundary:** the current desktop labels and activation
> behavior are included in the immutable published v0.22.4 private test
> candidate.

## Reopening

An Embedded App companion is being built, on its own track. This section
records what that changes and, more importantly, what it does not.

**The rejection's central fact still holds.** Creating or loading a custom
Embedded App requires a licensed organization and Control Hub approval, and
WebJam's usual artist is on a free or personal Webex account with no
organization to administer. That has not changed, so the companion reaches
*some* users rather than all of them.

**Which is exactly why it is a companion and not a component.** The desktop
remains the whole product. Every feature works with nothing paired, and the
no-companion path is the only path the Art surfaces have -- they cannot read
the projection, so they cannot come to depend on it. A pairing is allowed to
change exactly one desktop behaviour: with a panel already showing this room,
opening an Art panel no longer takes focus from the meeting window.

**ADR 0004 still governs the boundary.** The companion does not embed the
meeting, own mute or camera or join, or tap meeting, browser, or system
output. Two mutes stay two mutes, and leaving a WebJam room is still not
leaving a meeting.

What Art contributes is the read-and-request seam rather than any Cisco
integration: a companion-safe status projection and a bounded command
contract, specified in **ADR 0013**. Implementing the iframe, the pairing
transport, and the hosted page belongs to the companion track.

## Rejection (historical)

Recorded when the proposal was closed; retained because the reasoning above
still sets the terms the companion is built on.

The proposal assumed the musician could load a custom embedded app into their
own Webex. They usually cannot. Creating or loading a custom Embedded App
requires a licensed organization and Control Hub administrator approval, and
WebJam's actual user is a musician or artist on a **free or personal Webex
account** with a Personal Room link and no organization to administer. An
add-on that most users cannot install is not a feature; building it would take
effort away from the product they can install.

The baseline therefore stands at **ADR 0004**: the desktop application is the
whole product, and Webex is a second window beside it, reached through **Show
Webex App**, **Join / Open Meeting**, and **Webex Controls**. WebJam's chrome
stays compact so meeting faces can sit next to it, and it never takes focus
except when the user asks for Webex Controls. Two mutes stay two mutes, and
leaving a WebJam room is not leaving a meeting.

Any companion starts from what a free account can actually do, and ADR 0004's
Connect Webex section remains the live entry point for account-backed Webex
capability.

## Context (historical)

WebJam currently opens a musician-supplied Webex Meeting or Personal Room link
externally. Webex owns sign-in, membership, camera, microphone, speakers, and
leave state. Jamulus and the WebJam desktop remain the performance-audio path.
ADR 0004 preserves that truthful boundary.

A future Webex Embedded App could give participants a small shared WebJam
surface inside an existing Webex meeting. It must complement the desktop
conductor, not recreate WebJam or move music processing into Webex.

## Proposed decision (historical, not built)

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

All browser-to-service traffic uses HTTPS or WSS. For the sandbox proof, use
the Embedded Apps Framework context plus WebJam's own short-lived pairing
grant; do not add Webex OAuth merely to identify the current embedded-app
context. In Framework 2.x, read the user state and its JWT from
`app.application.states.user`; the 1.x `context.getUser()` method is removed.
Verify that context JWT server-side with a strict Webex issuer allowlist,
expected app audience, expiry/issued-at checks, meeting/organization binding,
and a bounded cache of Cisco verification keys. That JWT establishes context
integrity but is not a Webex REST API access token.

Add the documented Webex OAuth flow only when a concrete feature requires a
Webex REST API. Then use minimum scopes, explicit consent, expiry, revocation,
and role checks. No client secret is shipped to browser code. Durable tokens
remain in a trusted service or OS credential store, never local storage, URLs,
invitations, notes, logs, or support bundles.

Desktop synchronization needs authenticated, short-lived session grants,
origin binding, replay protection, revisioned/idempotent commands, bounded
payloads and rates, reconnect recovery, and explicit host approval before a
meeting is linked to a desktop session. Provider identity alone does not grant
WebJam host or recording authority.

The embedded surface contains no Webex password, WebJam credential, OAuth
token, Jamulus RPC secret, private endpoint, filesystem path, raw diagnostic,
audio, video, waveform, take, or song media. Shared text is bounded, escaped,
retention-defined, and omitted from public diagnostics.

## Delivery phases (historical, not built)

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

The current desktop implements native-app detection, official Cisco installer
handoff, side-effect-free **Webex Controls** navigation, exact-PID-verified
**Show Webex App** with verified no-URL launch when stopped, explicit **Join /
Open Meeting**,
truthful Mute-in-Webex focus guidance, validated external meeting launch, and
privacy-safe diagnostics. Show passes no URL and never opens a browser or
meeting; it cannot control or verify external Webex mute.
None of the hosted, OAuth, relay, embedded, or minimize-to-agent phases above
are represented as shipped.

## Cisco references

- [Embedded Apps overview](https://developer.webex.com/create/docs/embedded-apps)
- [Embedded Apps developer guide](https://developer.webex.com/create/docs/embedded-apps-guide)
- [Embedded Apps Framework 2.x migration](https://eaf-sdk.webex.com/index.html#Get-user-API)
- [Framework 2.x user state](https://eaf-sdk.webex.com/interfaces/IWebexAppsUserState.html)
- [Webex App installation and automatic upgrades](https://help.webex.com/en-us/article/nw5p67g/Webex-App-%7C-Installation-and-Automatic-Upgrade)
