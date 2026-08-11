# Multi-service conversation — Phase 2+ plan

> Status: plan. Phase 1 (provider-neutral hardened public-HTTPS link policy,
> generic fallback, handoff, redaction, and the verified-identity registry in
> `core/meeting_link.py`) is
> implemented on the v0.24.0 source line. Nothing below is implemented unless a
> changelog entry says so.

WebJam's conversation model is one truthful action on every service:
validate a saved HTTPS link, hand it to the operating system once, claim
nothing about join, mute, provider identity, or meeting state. Known services
receive friendly labels; every other accepted provider remains neutral and
its URL/hostname is fully redacted from support surfaces. Phase 2 extends the *verified
native app* experience (detect, bring forward, honest installed/not-installed
status) from Webex to Zoom, Microsoft Teams, and FaceTime, using the same
codesign-anchored pattern. Phase 3 records the credentialed API tier as
deliberate future work.

## Phase 2 — service-neutral native detection and bring-forward

1. Parameterize `services/webex_app.py`'s runtime by identity instead of
   constants: the codesign requirement template
   (`identifier "<bundle>" and anchor apple generic and certificate
   leaf[subject.OU] = "<team>"`) takes its values from
   `core.meeting_link.MEETING_APP_IDENTITIES`. Apple system software
   (FaceTime) anchors to Apple proper with no team OU. Keep the Webex
   constants as the webex registry entry (contract-tested today).
2. The conversation card follows the saved link's service: title, "Show
   <Service> App", "Open <Service> to Mute", and status copy render from
   `meeting_service_label()`. Google Meet renders the browser-only truth
   ("Meet opens in your browser; there is no desktop app to verify").
3. Physical gate before shipping: re-verify the pinned Zoom and Microsoft
   Team IDs against real installed apps (`codesign -d -r -
   /Applications/zoom.us.app`, new Teams) and record the exact output in
   the test-procedure ledger. Registry values come from public MDM/PPPC
   documentation and are NOT RUN until then.
4. Keep the https link as the only handoff payload. Native URL schemes
   exist (`zoommtg://`, `msteams:`, `facetime:`) but add parsing/spoofing
   surface and skip each service's own browser-side consent; the https
   link already routes to the installed app via each vendor's handler.
   Decision stands unless a service breaks https-to-app routing.
5. Tests: parameterized detection/activation suites mirroring the Webex
   ones; card-label tests per service; registry contract test already in
   `tests/test_meeting_link.py`.

## Multi-platform detection matrix

| Service | macOS | Windows | Linux |
| --- | --- | --- | --- |
| Webex | codesign: Cisco `DE8Y96K9QP` | Authenticode CN "Cisco Systems, Inc." | native app |
| Zoom | codesign: `BJ4HAAB9B3` | Authenticode CN "Zoom Video Communications, Inc." | native app |
| Microsoft Teams | codesign: `UBF8T346G9` (teams2, classic fallback) | Authenticode CN "Microsoft Corporation" | browser (PWA; native client discontinued) |
| Google Meet | browser only | browser only | browser only |
| FaceTime | Apple system app | unavailable | unavailable |

Windows verification uses `WinVerifyTrust`/`Get-AuthenticodeSignature`
against the running executable's certificate CN — the moral equivalent of
the macOS codesign requirement. Browser-only cells render the honest
"opens in your browser" status instead of a fake install check. Every
pinned identity above is a physical verification gate before native
detection ships on that platform.

## Phase 3 — credentialed APIs (recorded, not planned for a near release)

These exist, are useful, and all violate the current credential-free
external-ownership posture; adopting any requires OAuth storage, consent
UX, and a privacy review:

- Zoom Meeting SDK / REST API: create/schedule meetings, join with
  context. OAuth app + review process.
- Microsoft Graph cloud communications: create Teams meetings
  (`onlineMeetings`), presence. Entra app registration + admin consent.
- Google Calendar API conference data: create Meet links. OAuth + Google
  verification.
- Webex Embedded Apps / REST: already recorded in ADR 0007 as the
  Cisco-oriented exploration path.
- FaceTime: no public automation or meeting-creation API; links are
  created by a human in the FaceTime app.

If any Phase 3 work starts, begin with "create a meeting link for the
band" (one OAuth scope, clear user value) rather than in-meeting control,
and keep tokens in the OS keychain with the existing redaction rules.

## Explicitly out of scope on every service

Embedding meetings in-window, reading rosters or mute state, sending mute
commands, auto-joining, or claiming any external state WebJam cannot
verify. These are the product's trust boundary, not a missing feature.
