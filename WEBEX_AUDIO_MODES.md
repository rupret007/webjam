# Webex companion guidance — v0.22.5

> **Pre-publication candidate guide:** this document targets v0.22.5. GitHub
> Latest remains immutable v0.22.4 until promotion. External Webex behavior
> remains a separate physical gate.

Webex is optional for talking or video. Jamulus carries the music.

The saved conversation link is no longer Webex-only: one hardened HTTPS
policy also accepts Zoom, Microsoft Teams, Google Meet, and FaceTime meeting
links. **Join / Open Meeting** hands whichever accepted link is saved to the
operating system exactly once, and WebJam still never claims join, mute, or
meeting state on any service. FaceTime links open only on a Mac and the app
says so instead of failing silently. Native app detection, bring-forward,
and publisher verification remain Webex-specific; other services open
through the default browser or their own installed app's link handler.

Use the direct **Webex Controls** action or **More → Webex Controls** only if
the band wants it. Both reveal the Conversation panel without opening or
rejoining a meeting. In **Settings → Conversation**, each musician can enter
their own **Meeting or Personal Room link**. WebJam displays the Webex site
hostname and offers **Open in Webex** to test the draft link. It saves only the
link and opens it externally only after an explicit user action.

In Conversation on macOS, **Show Webex App** re-verifies Cisco's exact bundle.
When Webex is running, every click dynamically finds and verifies the exact
Cisco PID. WebJam validates a retained Core Foundation file-reference URL
against Cisco's designated requirement and passes that same filesystem object
directly to `NSWorkspace`. If stopped, the same request launches the app itself
with no URL or document argument. Fresh observations then prove exact object
identity, PID, publisher, and foreground state; request acceptance alone is not
success. Webex chooses its own screen.
Only **Join / Open Meeting** performs the one explicit meeting-link handoff.
**Change Link** returns to Settings. **Open Webex to Mute** shows the verified native
app for its own Mute control. WebJam cannot verify or change mute in an
externally owned meeting, so it never sends a blind shortcut or reports
Webex—or Jamulus—as muted.

The current Windows and Linux packages can locate Webex but do not prove its
publisher identity, so **Show Webex App** and the focus-based mute guidance
remain unavailable there. **Join / Open Meeting** remains the supported
external handoff.

Webex handles sign-in, participant identity, camera, microphone, speakers, and
meeting controls. A WebJam musician name does not change the user's Webex
identity. “Opened externally—finish joining in Webex” reports only a successful
handoff to the operating system; WebJam never claims to have joined, muted, or
verified the Webex participant list.

WebJam detects a native Webex installation after startup. On macOS it verifies
the Cisco bundle identifier, Developer ID Team `DE8Y96K9QP`, deep signature,
and Apple notarization before reporting it as verified, then repeats that
verification immediately before activation. If Webex is missing or invalid,
WebJam offers the architecture-correct installer from
`https://binaries.webex.com/` (or Cisco's public downloads page on an
unsupported target) only after an explicit user confirmation. Cisco's app owns
its installation and automatic updates. WebJam does not redistribute, silently
install, launch an installer executable, or store Cisco credentials.

Diagnostics keep only a bounded allowlist of action and result categories such
as Conversation shown, running-app activation requested/confirmed/refused or
failed, and meeting handoff accepted/opened/failed. They never retain the
meeting URL or ID, Webex account, application path, participant identity, or
credential.

## Safe rehearsal habit

1. Get music working in Jamulus first.
2. Add or open Webex only if the band uses it.
3. Keep Webex muted while playing to avoid duplicate music and feedback.
4. If Webex fails, keep the Jamulus rehearsal running; Webex is not a music
   prerequisite.

Legacy audience-bridge preferences remain loadable for compatibility, but the
musician flow does not route system audio automatically or configure Webex
devices. WebJam Settings persists only the optional conversation link for
Webex; live behavior and device choices stay with their native apps.

WebJam does not bundle a Webex web widget, Chromium/WebEngine meeting runtime,
Guest Issuer token exchange, OAuth token, username, or password. A future
account connection is documented separately in
`docs/adr/0004-webex-external-launch-and-future-oauth.md`.

A future focused Webex Embedded App companion is separately described in
`docs/adr/0007-future-webex-embedded-app-companion.md`. It would require hosted
HTTPS/WSS infrastructure, Webex authorization and organization approval, and a
secure synchronization protocol. It must keep the desktop WebJam process as
the authoritative audio/session engine and must not duplicate the entire
desktop interface or place credentials, private links, paths, or media in the
embedded surface.

Use `docs/plans/webjam-webex-sandbox-demo-gate.md` to record a real packaged,
two-endpoint rehearsal. Its checks remain **NOT RUN** until completed against
an exact package after all previously disclosed test passwords are rotated.
