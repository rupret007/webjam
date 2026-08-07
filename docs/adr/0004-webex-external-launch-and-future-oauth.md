# ADR 0004: External Webex launch and future OAuth connection

- Status: Accepted for external launch; OAuth is roadmap-only
- Date: 2026-07-29
- Scope: Optional Webex conversation/video companion

> **v0.22.4 published boundary:** the current decision is included in the
> immutable published v0.22.4 private test candidate. External Webex behavior
> remains a separate physical gate.

## Context

Jamulus is WebJam's performance-audio path. Webex is an optional, independently
owned conversation/video application. An earlier prototype embedded a Webex
widget through Qt WebEngine and included deprecated Guest Issuer token code.
The musician product never used that path, while shipping it increased package,
permission, credential, and shutdown risk.

## Current decision

WebJam stores only `AppSettings.webex_url`. Each musician enters their own
HTTPS `webex.com` Meeting or Personal Room link. The UI shows only its
validated site hostname. The direct **Webex Controls** action and **More →
Webex Controls** only reveal/focus Conversation; they never open the saved
link. On macOS,
**Show Webex App** dynamically verifies the exact Cisco bundle. If Webex is
running, it verifies the exact PID and requests activation. If Webex is
stopped, the same native request launches the verified app itself with no URL
or document argument. Verification and `NSWorkspace` launch share one retained
Core Foundation file-reference URL that identifies the filesystem object, not
its mutable pathname. WebJam then re-enumerates the exact object and PID,
re-verifies Cisco's running-process requirement, and proves foreground state
before returning a distinct typed result. Webex chooses which of its own
screens appears. Request acceptance alone is never foreground proof. This action
never passes a URL or opens a browser or meeting. Explicit **Join / Open
Meeting** is the sole full-link handoff and remains bound to the immutable
authorized URL for that click. Windows and Linux keep native focus disabled
because their current detection does not establish publisher proof.

The file reference prevents rename/path-substitution races, and static-code
validation proves the bound bundle at check time. It does not make a user-owned
bundle immutable between validation and native launch. WebJam verifies the
resulting PID again, but a hostile process already able to rewrite both Webex
and WebJam under the same user account is treated as endpoint compromise and
is outside this integration's trust boundary.

The native Webex app or browser owns authentication, participant identity,
camera, microphone, speaker, join state, meeting controls, and leave state.
WebJam reports only `Not opened`, `Opening…`, `Opened externally`, or
`Open failed`. It cannot interpret a successful browser handoff as meeting
membership.

The external app does not expose verifiable mute control to this integration.
**Open Webex to Mute** therefore shows the verified Webex app for its own Mute control and
truthfully says that WebJam did not change or verify mute. It does not send a
blind system-wide shortcut or alter Jamulus controls.

No Webex username, password, admin-site setting, access token, refresh token,
client secret, Guest Issuer material, browser profile, or WebEngine runtime is
collected or bundled. Legacy credential fields and the retired separate Webex
config-file path are ignored when loading old settings and disappear on the
next save. Secret-name redaction remains so old diagnostics cannot expose
historical values.

## Future Connect Webex decision

If product research proves that account-backed Webex capabilities materially
help musicians, implement **Connect Webex** as a separate, optional feature:

- use the system browser and OAuth 2.0 Authorization Code with PKCE;
- treat WebJam as a public desktop client and never embed a client secret;
- bind callback, state, PKCE verifier, and expiry to one short-lived attempt;
- request only the smallest documented scopes required by the specific
  user-visible API calls; do not request site-admin scopes;
- store refresh/access tokens only in the operating-system credential store,
  never in `AppSettings`, logs, support bundles, invitations, or session notes;
- retain the meeting-link external-launch path as a fully functional fallback;
- show API-derived account/meeting state only when Webex actually returns it,
  and keep Jamulus operation independent of OAuth or Webex failure.

OAuth implementation requires a separate threat model, provider registration,
token revocation/disconnect flow, expiry and offline tests, scope review, and
packaged cross-platform acceptance. This ADR does not add OAuth code or imply
that WebJam can currently observe an external meeting.

## Consequences

The shipped desktop app has one smaller, truthful Webex boundary and no
embedded web/media runtime. Users finish sign-in and joining in Webex. Richer
provider integration remains possible without weakening the current
credential-free meeting-link workflow.
