# Pocket Stage iPhone app

This folder contains a reproducible XcodeGen app specification, the complete
iPhone SwiftUI source, and a strict cross-platform protocol package. Generated
Xcode project and user-state files stay untracked, so each musician can choose
their own Apple team and bundle identifier without changing shared source.

## What is here

- `PocketStageProtocol`: a macOS/iOS Swift Package with strict QR parsing,
  version validation, endpoint validation, certificate fingerprints, JSON wire
  models, and deterministic protocol, transport, and connection-state tests.
- `PocketStage/`: SwiftUI screens for **Pair**, **Live / Now**, **Band**,
  **My Mix**, and **Cues**; connection state; and a
  `URLSessionWebSocketTask` client that
  authenticates the desktop's self-signed leaf certificate using the QR pin.
- `Fixtures/pocket_stage_v1_golden.json`: canonical pair, snapshot, fader
  command, and confirmed-receipt envelopes shared with Python compatibility
  tests.

The package implements the WebJam core v1 envelope and commands using exact
snake_case wire keys. It deliberately rejects unsupported envelope versions.
The protocol reserves a pan command, but the app presents only fader and mute
because the pinned desktop Jamulus client has no proven live pan provider path.

## Verify the protocol package

From this folder:

```sh
swift test
```

This is deliberately macOS-testable and does not need Xcode, an iPhone, or a
network connection. The state-model tests use injected socket and monotonic-time
drivers; the separate opt-in integration still exercises the real pinned WSS
transport.

## Install on your iPhone with a Personal Team

1. Install the full **Xcode** app from Apple and open it once. In **Xcode →
   Settings → Accounts**, add the Apple ID that will own the free Personal
   Team.
2. From a matching v0.22.0 Mac candidate, open **Pocket Stage iPhone Setup** and
   double-click **Open Pocket Stage in Xcode.command**. That folder already
   contains the exact generated project compiled by CI, so release users do not
   need XcodeGen. Source developers instead install XcodeGen 2.45.4 or newer
   (`brew install xcodegen`) and run **Generate Pocket Stage Project.command**
   to regenerate the project from `project.yml`.
3. In the PocketStage target's **Signing & Capabilities**, set its bundle
   identifier to one you control, for example
   `com.yourname.pocketstage`, then select your **Personal Team** in
   *Signing & Capabilities*. Xcode manages a development provisioning profile
   for a connected personal device.
   A paid Apple Developer Program membership is not required for this
   owner-device test. Apple's free Personal Team profiles expire after seven
   days, so Xcode must periodically rebuild and reinstall the app. See Apple's
   [developer account overview](https://developer.apple.com/help/account/basics/about-your-developer-account).
4. Connect and trust your iPhone, select it as the run destination, and press
   **Run**. If Xcode asks for Developer Mode, open **Settings → Privacy &
   Security → Developer Mode** on the iPhone, enable it, restart the phone as
   requested, and confirm after restart. The generated target already includes
   the local protocol package,
   camera and Local Network descriptions, and only the narrow
   `NSAllowsLocalNetworking` ATS setting. Do not enable
   `NSAllowsArbitraryLoads`; transport is still WSS and authenticated with the
   exact leaf pin. See Apple's
   [`NSAllowsLocalNetworking`](https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsallowslocalnetworking)
   documentation.
5. In Pocket Stage, tap **Scan Pairing QR** and point the in-app
   scanner at the desktop code. VisionKit stops after the shared strict parser
   accepts one Pocket Stage payload. Text injection remains available only as
   a Simulator/developer aid; physical users should restore Camera permission
   and scan a fresh code rather than copying bearer material.

The pair payload format is:

```text
pocketstage://pair?v=1&session=<canonical-uuid>&endpoint=wss%3A%2F%2Fhost%2Fv1%2Fpocket&token=<43-char-base64url>&fingerprint=<64-hex>&expires=<unix-seconds>&name=<optional>
```

`fingerprint` is the SHA-256 fingerprint of the expected leaf certificate's
DER bytes. `token` is a one-use pairing capability and `expires` is its Unix
expiry time. The app creates a separate canonical UUID `claim_id`, sends one
pair envelope at generation 0 / sequence 0, and starts commands at sequence 1.
It rejects expired codes, plaintext `ws://` endpoints, credentials embedded in
URLs, malformed tokens or pins, and unknown/duplicated QR fields.

## Security and limits

The socket is data/control only. It does not request microphone, Bluetooth, or
background-audio permissions, and it does not capture media. Camera access is
requested only while the user opens the QR scanner; Pocket Stage does not store
or transmit a photo or video. It also requires Local Network permission. For
each server-trust challenge the
client hashes only the presented leaf certificate's DER bytes and compares it
to the required QR pin. After a match, it temporarily anchors that exact leaf,
applies an SSL policy for the endpoint host, and evaluates validity, hostname,
and signature before accepting the self-signed certificate. A matching
intermediate is never sufficient, and missing/mismatched pins fail closed.

The checked-in opt-in integration gate has exercised this real Swift transport
against a live WebJam gateway with the ephemeral self-signed certificate and
exact pin. The generated app also compiles without signing in CI. VisionKit on
a physical iPhone, VoiceOver/Dynamic Type, Local Network and firewall prompts,
background execution, Live Activities, and reactions remain either **NOT RUN**
or intentionally out of scope. A consumed QR token is never put in Keychain
and cannot reconnect. After disconnection, the desktop jam keeps running and
the user must create and scan a fresh code. The current v1 core exposes `solo`
in snapshots but has no `set_participant_solo` command, so the client displays
solo state without presenting a control for it.
