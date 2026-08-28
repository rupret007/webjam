WebJam v0.27.1 private test candidate for Windows x64
======================================================

PRIVATE TEST CANDIDATE: use this package only when its exact filename appears
in the v0.27.1 GitHub release and its SHA-256 matches that release's manifest.
Do not use the published v0.27.1 checksum manifest for this post-tag build.
Physical audio, hardware, SmartScreen, publisher-signing, and managed-device
gates remain NOT RUN unless the release evidence names this exact file and
SHA-256.

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
is not supported by this Windows release. Music and Podcast & Voice retain
standalone Reference Studio for local songwriting, recording, arranging,
mixing, and WAV/FLAC bounce without joining Jamulus.

Music and Podcast & Voice are GA creator profiles. Review & Rehearsal is
Preview: live WebJam-audio Join, participation in a host-controlled Record
Session, and playback/read-only completed-take review are available, while
standalone projects, take edit/comp/mix mutation, track export, shared notes,
visual sync, and media timecode are blocked. No profile directly or
automatically taps a meeting app, browser, or system output. Local scratchpad
notes remain profile-scoped on this computer only; they are never shared or
media-timecoded.

Jamulus is required for live music. WebJam automatically checks its signed,
version-specific component catalog; it never follows an upstream "latest"
download. When an approved Jamulus update is available, WebJam can download
and verify it in the background, but Windows installation still requires your
explicit approval and may display UAC or SmartScreen. WebJam verifies the
installed version after setup. It never hides elevation or installs while a
jam, recording, Shared Track, reconnect, or Jamulus launch is active.

The sealed v0.22.5 catalog does not authorize v0.27.1 and is rejected. That
catalog pin is unchanged. Shared Track play uses this Mac's official BlackHole
16ch/64ch route at 48 kHz and the bundled headless client; it does not wait
for a signed catalog pin. This candidate uses the embedded Jamulus 3.12.2
fallback rather than offering a managed 3.12.3 download.

If an update is unavailable or you are offline, WebJam offers the exact
bundled Jamulus 3.12.2 fallback installer from the Host/Join screen. Jamulus is
a separate application. On a managed work PC, your organization's application
policy may require IT approval even though WebJam itself installs per user.

Conversation/video is optional and is not bundled. Any meeting platform can
use the explicit Join / Open Meeting handoff when its link is public HTTPS with
a DNS hostname and passes WebJam's safety checks. Known Webex, Zoom, Teams,
Google Meet, and FaceTime links receive friendly labels; another accepted
provider remains neutral and is not natively verified. FaceTime is Mac-only,
and WebJam never claims join or mute. This Windows package can locate Webex but
does not yet perform the required Authenticode publisher verification, so the
Webex-only Show Webex App and focus-based Mute in Webex guidance stay
unavailable. Join / Open Meeting is the only saved-link handoff. If the native
Webex app is missing, Get Webex opens Cisco's official Windows x64 installer
after you confirm. Cisco owns that download, license, installation, sign-in,
and updates. Jamulus remains the music path; keep the selected meeting service
muted while playing to avoid delayed duplicate audio. WebJam never directly or
automatically taps a meeting app, browser, or system output. Local Originals
record explicitly selected input devices, so do not route meeting or
system-output audio into those inputs.

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
