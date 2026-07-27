# Webex companion guidance — v0.20.0

Webex is optional for talking or video. Jamulus carries the music.

Use **More → Webex / Conversation** only if the band wants it. In **Settings →
Conversation**, each musician can enter their own **Meeting or Personal Room
link**. WebJam displays the Webex site hostname and offers **Open in Webex** to
test the draft link. It saves only the link and opens it externally only after
an explicit user action.

Webex handles sign-in, participant identity, camera, microphone, speakers, and
meeting controls. A WebJam musician name does not change the user's Webex
identity. “Opened externally—finish joining in Webex” reports only a successful
handoff to the operating system; WebJam never claims to have joined, muted, or
verified the Webex participant list.

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

Use `docs/plans/webjam-webex-sandbox-demo-gate.md` to record a real packaged,
two-endpoint rehearsal. Its checks remain **NOT RUN** until completed against
an exact package after all previously disclosed test passwords are rotated.
