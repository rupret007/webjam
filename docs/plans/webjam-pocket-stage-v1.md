# Pocket Stage v1 developer-preview implementation and validation plan

> **v0.22.3 published boundary:** the current menu label is included in the
> immutable published v0.22.3 private test candidate. Physical validation
> remains **NOT RUN**.

- Date: 2026-07-21
- Status: Reproducible app vertical slice implemented; physical validation **NOT RUN**
- Distribution: Generated Xcode project in Mac candidates + Apple Personal Team development only

## Goal

Validate the smallest useful iPhone companion: the desktop stays at the
computer while an owner's iPhone on the same private Wi-Fi displays current
session/mix state, changes the current mix, marks a moment, and can request host
recording after desktop setup.

This is not the original full Pocket Stage roadmap. It deliberately excludes
phone audio, chat, reactions, solo control, a rehearsal plan, section/Studio
transport, editing, media transfer, Internet relay, and a durable reconnect
credential.

## Implemented source inventory

| Area | Current source |
| --- | --- |
| Protocol and one-use capability | `core/pocket_stage.py` |
| Private-Wi-Fi WSS gateway | `services/pocket_stage_gateway.py` |
| Ephemeral certificate and LAN validation | `services/pocket_stage_tls.py` |
| Desktop pairing dialog | `webjam_qt/windows/pocket_stage_pairing.py` |
| Desktop projection/command routing | `webjam_qt/controllers/application_controller.py` |
| More-menu entry/state | `webjam_qt/widgets/session_strip.py` |
| Swift protocol, transport, and state-model tests | `ios/Sources/` and `ios/Tests/` |
| SwiftUI app + reproducible target | `ios/PocketStage/` and `ios/project.yml` |

The desktop entry point is **More → Use iPhone as Pocket Stage…**. It starts a dedicated gateway
only when requested and displays a one-use QR code that expires in 120 seconds.
The gateway creates an ephemeral self-signed certificate, and the QR carries
the exact SHA-256 fingerprint of that leaf certificate's DER bytes.

The phone projection uses session-local slots and may attach bounded participant
display labels for the explicitly paired musician. Those labels are
paired-private content and must never enter logs, diagnostics, support bundles,
or the anonymous public Local Companion API. Current actions are fader,
mute, a Session Canvas marker, and host recording start/stop after the desktop
has completed Recording Setup. Solo may be displayed as state but cannot be
changed from the phone.

## Owner-device setup boundary

There is no packaged iOS app or App Store/TestFlight build. On the development
Mac, either use the setup kit carried by the matching current Mac candidate or
generate it from source:

1. Install Xcode and add the owner's Apple ID in **Xcode → Settings → Accounts**.
   In a downloaded candidate, open **Pocket Stage iPhone Setup** and run
   **Open Pocket Stage in Xcode.command**; the included project is the exact one
   generated and compiled by CI. Source developers may instead install
   XcodeGen and run `ios/Generate Pocket Stage Project.command`.
2. Choose an app identifier and the owner's Apple **Personal Team** for manual
   signing.
3. Install from Xcode onto the owner's unlocked iPhone. If prompted, enable
   **Settings → Privacy & Security → Developer Mode**, restart, and confirm;
   then approve only the required camera/local-network prompts.
4. Run the matching WebJam desktop build recorded in **Pocket Stage Build
   Info.txt**. The immutable published v0.18.1 packages predate Pocket Stage and
   cannot be used for this flow.

Personal Team provisioning is temporary development access, not a distribution
channel. No iOS binary should be published as a normal WebJam release asset.

## Manual pairing flow to validate

1. Put the development Mac and iPhone on the same private Wi-Fi. Do not test on
   a guest network that isolates clients or on a public/untrusted network.
2. Start or join the desktop jam and complete Jamulus audio setup normally.
3. If host recording will be tested, complete the first-record/Recording Setup
   choice on the desktop before using the phone control.
4. Choose **More → Use iPhone as Pocket Stage…**. Record the exact desktop commit/build and
   iPhone build used.
5. In Pocket Stage, choose **Scan Pairing QR**. The payload field is for
   Simulator/developer injection only; the desktop deliberately has no
   reveal/copy control. If Camera access was denied, restore it in iPhone
   Settings and scan a fresh code.
6. Confirm that one full, fresh snapshot appears with session-local slots and
   bounded display labels, while internal provider/channel identities remain
   absent.
7. Exercise only the current actions: fader, mute, marker, and—on a fully
   prepared host—record start/stop.
8. Disconnect the phone. Confirm the desktop jam continues. Create a **New
   Code** and pair again; the previous QR is not a reconnect credential.
9. Choose **Stop iPhone Sharing** and confirm the phone disconnects while the
   desktop session and Jamulus remain under their existing owners.

## Automated source checks

Run the focused desktop suite:

```bash
.venv/bin/python -m pytest -q \
  tests/test_pocket_stage.py \
  tests/test_pocket_stage_gateway.py \
  tests/test_pocket_stage_tls.py \
  tests/test_pocket_stage_controller.py
```

Run the Swift protocol package tests on macOS:

```bash
cd ios
swift test
```

Run the real Swift transport against the live Python pinned-WSS gateway:

```bash
WEBJAM_RUN_SWIFT_POCKET_STAGE_INTEGRATION=1 \
  .venv/bin/pytest -q tests/test_pocket_stage_swift_integration.py
```

CI additionally generates the Xcode project, compiles the complete unsigned
iOS Simulator app, and exercises Pocket Stage from each frozen desktop target.

Then run the repository's ordinary lint, compile, dependency, UX smoke, and
full Python gates. Automated success proves only the checked source contracts;
it does not convert any physical row below from **NOT RUN**.

## Physical acceptance worksheet

Every row starts **NOT RUN**. Record date, operator, Mac/iPhone models and OS
versions, Wi-Fi environment, desktop commit/build, iPhone build, outcome, and
notes before changing a status.

| Observation | Status | Pass condition |
| --- | --- | --- |
| Pair beside Mac | **NOT RUN** | One-use QR reaches a fresh anonymous snapshot without manual address entry. |
| Finder Local Network permission | **NOT RUN** | Launch the packaged Mac app from Finder, approve the Local Network request, pair, then prove denial recovers after enabling WebJam in System Settings → Privacy & Security → Local Network. Terminal-launched success is not evidence for this row. |
| macOS Application Firewall | **NOT RUN** | Independently deny and then allow WebJam in System Settings → Network → Firewall → Options; pairing recovers without disabling the firewall or widening unrelated access. |
| Windows Defender Firewall | **NOT RUN** | From the packaged Windows build, deny once, then allow WebJam on Private networks only and pair; Public networks remain unselected and WebJam creates no rule itself. |
| Ubuntu firewall | **NOT RUN** | With the packaged Linux build and the test host's firewall policy recorded, prove private-LAN allow/deny/recovery without a wildcard/public rule. |
| Two-minute expiry | **NOT RUN** | An expired code fails closed and New Code recovers. |
| One-use/replay | **NOT RUN** | A second claim cannot reuse a consumed code. |
| Wrong certificate | **NOT RUN** | The phone refuses a server whose leaf DER SHA-256 does not match the QR. |
| Paired-private projection | **NOT RUN** | Slots, bounded display labels, mix, and session state appear; provider IDs, paths, invitations, and credentials do not, and labels never enter logs/diagnostics/public API. |
| Mix controls | **NOT RUN** | Fader and mute affect the intended current slot and report fresh state. Pan is absent until Jamulus exposes a proven provider command. |
| Marker | **NOT RUN** | One phone action creates one bounded timestamped Session Canvas marker. |
| Host record precondition | **NOT RUN** | Unprepared/non-host use rejects; prepared host start/stop follows desktop recorder truth. |
| Disconnect/fresh-code recovery | **NOT RUN** | Desktop jam continues; old QR cannot reconnect; a fresh QR can pair. |
| Stop sharing | **NOT RUN** | Listener/phone close and temporary material is cleaned up; Jamulus keeps running. |
| Phone lock/background/return | **NOT RUN** | No stale command is presented as current; recovery requires truthful re-pairing. |
| Mac sleep/wake and IP change | **NOT RUN** | The old phone link closes, the old code/address is not reused, and reopening Pocket Stage produces a fresh listener, certificate, and working code. Repeat with VPN/interface change. |
| Accessibility | **NOT RUN** | Pair, session, mix, marker, recording, and failure states work with VoiceOver and large Dynamic Type. |
| 60-minute rehearsal | **NOT RUN** | Resource use stays bounded with no companion-caused audio dropout or recording gap. |
| Two-way audibility | **NOT RUN** | Musicians independently confirm Jamulus audio; Pocket Stage supplies no audio proof. |

## Exit criteria for this preview

The developer preview is ready for an owner-device rehearsal only after:

- focused Python and Swift tests pass at one recorded commit;
- an exact desktop source/build and Xcode iPhone build are recorded;
- actual iPhone pairing, expiry, pin mismatch, one-use behavior, and fresh-code
  recovery pass;
- paired-label mix-slot changes and markers are observed on the correct desktop;
- host recording preconditions and pending/confirmed behavior are observed;
- phone interruption and Stop Sharing leave the Jamulus session running;
- accessibility and a real-instrument rehearsal are observed;
- the Finder-launched packaged Mac Local Network and Application Firewall
  allow/deny/recovery rows pass (including one ad-hoc rebuild/re-prompt
  observation), and any claimed Windows/Linux support has its firewall row;
- all untested rows remain visibly **NOT RUN**, never inferred from automation.

Passing this worksheet would validate only the current private-Wi-Fi,
owner-device vertical slice. Any broader feature or distribution path requires
a separate plan and threat-model update.
