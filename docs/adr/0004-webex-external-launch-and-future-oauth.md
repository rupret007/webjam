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

## Coexistence with Art (2026-08-21)

Webex is the primary meeting platform and other providers stay supported, and
Art does not change that boundary in either direction. Art keeps
`meeting_handoff` like every other profile: the conversation and the faces are
somebody else's window, configured once and owned there.

What Art added has to be true *beside* that window:

- **Two windows, not one.** The shared canvas, the reference video, and the AI
  image panels are non-modal and narrow enough to leave a conversation beside
  them, and none of them is opened on anyone's behalf. A talk-only room leaves
  nothing on screen at all.
- **The conversation keeps its sound.** A reference video is silent from its
  first frame. The file is never routed anywhere, so every computer holds its
  own copy, and an unmuted one would put a second soundtrack over the
  conversation on every machine at once. The music path and the meeting app own
  audio; the video is the picture. No Art surface selects an audio device.
- **The conversation keeps its focus.** Only the existing Webex Controls and
  Show Webex App may focus or launch the meeting app. No Art snapshot handler,
  tick, or notice raises a window. A chosen start card offers the host a
  control in the room chrome rather than opening a panel at them, and pressing
  it is their decision.
- **The handoff is never rewritten.** Nothing in Art reads or writes the saved
  meeting URL, and no Art module imports the meeting-app service. This is
  enforced structurally rather than by review.
- **Nothing is captured.** Art does not screen-share the reference video into a
  meeting, does not put the canvas into a meeting as the product, and taps no
  meeting, browser, or system output.
- **Art never claims to be in the meeting.** Its copy states what this computer
  did and nothing about membership, mute, or who can hear whom.
- **Two mutes stay two mutes.** No Art surface offers a control whose label
  contains "mute", so Art cannot add a third thing a person might mistake for
  either one. The reference video's silence is explained as a fact about the
  video, never as control of the call.
- **Advice is a claim.** Native activation is disabled on Windows and Linux
  because their detection does not establish publisher proof, so nothing there
  may *point at* Show Webex App either. The suggestion appears only where the
  publisher is verified, and disappears with the capability rather than
  outliving it.
- **Ending one thing never ends another.** No Art panel intercepts its own
  close, so dismissing a window cannot withdraw a share or leave a room.
  Closing Drawpile ends no session, hiding the video leaves nobody, and no Art
  surface offers to end, leave, or disconnect anything.
- **Opening a painting program takes nobody's microphone.** Neither the
  Drawpile nor the Krita launch vector carries an audio, device, or mic flag,
  and no Art module imports an audio library or the mixer. The live audio path
  and the meeting app own every device between them.
- **A hosted canvas is personal.** WebJam lands the artist on Drawpile's own
  Host page and recommends a password-protected Personal session. It never
  requests a public listing or an adult-content flag, and it says so when a
  pasted invitation carries no password.

Hiding the reference video is local to that artist's own player, so it costs
them neither the live audio nor the meeting faces.

Non-Webex links keep the identical handoff through `core.meeting_link`. Webex is
primary in copy and in control labels; it is not the only accepted host.

This section adds no OAuth, no embedded web runtime, and no blind mute
shortcut. A companion panel is now being built on a separate track (ADR 0007,
reopened); Art's read-and-request seam is ADR 0013, and every rule above holds
on it too — the projection has no field for a mute or a meeting, and the
command contract has no verb for ending, leaving, or withdrawing anything.

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
