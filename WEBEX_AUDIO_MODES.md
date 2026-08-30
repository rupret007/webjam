# Meeting-platform companion guidance — v0.27.2 source

> This document describes current unsigned v0.27.2 source. GitHub **Latest** is
> immutable unsigned/ad-hoc private test release `379360694`, published from
> lightweight tag `v0.27.2` at exact commit
> `9c6ca3de96aa7eb261c65b7dee768ab48144169c`, with seven packages plus
> `WebJam-v0.27.2-SHA256SUMS.txt`. Every external meeting-app behavior remains
> a separate physical gate.
> Unsigned v0.27.2 authorizes the existing exact Jamulus 3.12.2 and 3.12.3
> records for live audio, but the checkout itself is not a package or physical
> session result.

Any meeting service is optional for talking or video. Jamulus carries the
music.

The saved conversation link is provider-neutral. WebJam accepts a meeting
platform when it supplies a public HTTPS URL with a DNS hostname that passes
the shared hardening policy: no embedded credentials, custom port,
local/special-use name, IP literal, percent-encoded host, or known-brand lookalike.
Known Webex, Zoom, Microsoft Teams, Google Meet, and FaceTime links receive
friendly labels. Any other accepted provider uses neutral meeting-service
wording; acceptance is not native provider verification. **Join / Open
Meeting** hands whichever accepted link is saved to the operating system
exactly once, and WebJam never claims join, mute, or meeting state on any
service. FaceTime links open only on a Mac and the app says so instead of
failing silently. Native app detection, Cisco installer guidance,
bring-forward, and publisher verification remain explicitly Webex-only;
other services open through the default browser or their installed link
handler.

WebJam never directly or automatically taps a meeting app, browser, or system
output. Record Session captures the authoritative Jamulus server stems and only
explicitly planned Local Originals from input devices the user selects. Do not
route meeting or system-output audio into those inputs; use the meeting
service's own recorder if that audio is needed.

Use the direct **Conversation** action or **More → Conversation** only if the
group wants meeting controls. Both reveal the Conversation panel
without opening or rejoining a meeting. In **Settings → Conversation**, each
participant can enter their own **Meeting link**. WebJam names a recognized
service or uses neutral wording for another accepted provider, and offers
**Open Meeting Link** to test the draft link. It saves only the link and opens
it externally only after an explicit user action.

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

The selected meeting service handles sign-in, participant identity, camera,
microphone, speakers, and meeting controls. A WebJam display name does not
change the user's meeting identity. “Opened externally—finish joining in
Zoom,” for example, reports only a successful handoff to the operating system;
WebJam never claims to have joined, muted, or verified the participant list.

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
credential. Known allowlisted services may be represented only by a redacted
origin. For every unknown provider, the full URL and hostname are removed from
logs, mappings, diagnostics, and Support Bundles.

## Safe rehearsal habit

1. Get music working in Jamulus first.
2. Add or open a meeting service only if the group uses it.
3. Keep the meeting service muted while playing to avoid duplicate music and
   feedback.
4. If the meeting service fails, keep the Jamulus rehearsal running; it is not
   a music prerequisite.

Legacy audience-bridge preferences remain loadable for compatibility, but the
creator flow does not route system audio automatically or configure meeting
devices. WebJam Settings persists only the optional conversation link; live
behavior and device choices stay with their native apps.

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
The broader
[`V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md`](V026_CREATOR_MULTITRACK_PHYSICAL_TEST_CHECKLIST.md)
may be executed only against the exact published v0.26.0 packages and checksum
manifest recorded in its verified identity section; its meeting-platform rows
remain **NOT RUN** until that physical evidence is collected.
