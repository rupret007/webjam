WebJam v0.27.2 unsigned private test package
============================================

PACKAGE BOUNDARY: GitHub Latest is immutable release 379360694, published
2026-08-30T18:06:14Z from lightweight tag v0.27.2 at exact commit
9c6ca3de96aa7eb261c65b7dee768ab48144169c. It has seven packages plus
WebJam-v0.27.2-SHA256SUMS.txt. Use this private test package only when its exact
filename and SHA-256 appear in that manifest. A checkout or branch build is not
a substitute. The existing exact Jamulus 3.12.2 and 3.12.3 records are approved
through v0.27.2 for live Host/Join.
Physical audio, hardware, SmartScreen, publisher-signing, and managed-device
gates remain NOT RUN unless the release evidence names this exact file and
SHA-256.

Everything below describes the published unsigned private test package. Its
exact filename and published SHA-256 still require verification.
Windows SmartScreen or organizational policy may block it; WebJam does not
bypass those controls.

This release upgrades cryptography to 50.0.0 for CVE-2026-69247,
CVE-2026-69248, and CVE-2026-69249 using an exact hash-locked upstream
Windows wheel.

This installer places WebJam in your Windows user profile. Installing or
updating WebJam itself does not require administrator access.

After installation, open WebJam from the Start menu. You may also choose the
optional desktop shortcut during setup. WebJam does not start automatically
when setup finishes.

The v0.27.2 Windows private test package is built for joining a jam hosted by
the macOS build; physical cross-platform joining remains NOT RUN. Hosting a jam
is not supported by the Windows line. Music and Podcast & Voice retain
standalone Reference Studio for local songwriting, recording, arranging,
mixing, and WAV/FLAC bounce without joining Jamulus.

The source retains Music and Podcast & Voice as GA creator profiles. Review &
Rehearsal is Preview. Live WebJam-audio Join, participation in a host-controlled
Record Session, and playback/read-only completed-take review are source-eligible;
meanwhile
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

The sealed v0.22.5 catalog does not authorize v0.27.2 and is rejected. The baked
registry separately authorizes the existing exact Jamulus 3.12.2 and 3.12.3
records through v0.27.2. Presence of embedded 3.12.2 bytes does not authorize
an identity mismatch. Shared Track uses its separate approved headless path;
unlisted client/server versions still fail closed.

With exact release evidence, WebJam may offer the bundled Jamulus 3.12.2
installer from Host/Join. Jamulus is a separate application. On a managed work
PC, your organization's application policy may require IT approval even though
WebJam itself installs per user.
Installation may require IT approval.

Conversation/video is optional and is not bundled. Any meeting platform can
use the explicit Join / Open Meeting handoff when its link is public HTTPS with
a DNS hostname and passes WebJam's safety checks. Known Webex, Zoom, Teams,
Google Meet, and FaceTime links receive friendly labels; another accepted
provider remains neutral and is not natively verified. FaceTime is Mac-only,
and WebJam never claims join or mute. A future authorized Windows package can locate Webex but
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
