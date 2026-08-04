WebJam v0.22.3 for Windows x64
==============================

PRE-PUBLICATION CANDIDATE: use only the exact v0.22.3 asset after its draft,
checksum, and verified GitHub promotion gates pass. Physical audio, hardware,
SmartScreen, publisher-signing, and managed-device gates remain NOT RUN unless
the release evidence names this exact file and SHA-256.

This package is an unsigned private test candidate. Verify its filename and
published SHA-256 before running it. Windows SmartScreen or organizational
policy may block it; WebJam does not bypass those controls.

This candidate upgrades cryptography to 50.0.0 for CVE-2026-69247,
CVE-2026-69248, and CVE-2026-69249 using an exact hash-locked upstream
Windows wheel.

This installer places WebJam in your Windows user profile. Installing or
updating WebJam itself does not require administrator access.

After installation, open WebJam from the Start menu. You may also choose the
optional desktop shortcut during setup. WebJam does not start automatically
when setup finishes.

WebJam's Windows build can join a jam hosted by the macOS build. Hosting a jam
is not supported by this Windows release. Standalone Reference Studio remains
available for local songwriting, recording, arranging, mixing, and WAV/FLAC
bounce without joining Jamulus.

Jamulus is required for live music. WebJam automatically checks its signed,
version-specific component catalog; it never follows an upstream "latest"
download. When an approved Jamulus update is available, WebJam can download
and verify it in the background, but Windows installation still requires your
explicit approval and may display UAC or SmartScreen. WebJam verifies the
installed version after setup. It never hides elevation or installs while a
jam, recording, Reference Track, reconnect, or Jamulus launch is active.

If an update is unavailable or you are offline, WebJam offers the exact
bundled Jamulus 3.12.2 fallback installer from the Host/Join screen. Jamulus is
a separate application. On a managed work PC, your organization's application
policy may require IT approval even though WebJam itself installs per user.

Webex is optional and is not bundled. In a session, choose Webex Controls on
WebJam's main rail or More > Webex Controls to show Conversation controls; this
does not open or rejoin the meeting. This Windows package can locate Webex but
does not yet perform the required Authenticode publisher verification, so Show
Webex App and the focus-based Mute in Webex guidance stay unavailable. Join /
Open Meeting is the only saved-link handoff. If the native app is missing, Get
Webex opens Cisco's official Windows x64 installer after you confirm. Cisco owns
that download, license, installation, sign-in, and updates. Jamulus remains the
music path; keep Webex muted while playing to avoid delayed duplicate audio.

This candidate has no trusted WebJam publisher signature. A future signed
release must show the expected publisher, but even a valid signature would not
override your organization's application-control policy. Use your approved
software catalog or contact IT when Windows or company policy blocks the
installer.

Uninstall WebJam from Windows Settings > Apps > Installed apps. Uninstalling
removes WebJam's installed application files and shortcuts. It deliberately
preserves your WebJam settings, recordings, exports, and other user data.

WebJam is distributed under the MIT License. Third-party notices and license
information are installed alongside the application.
