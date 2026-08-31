WEBJAM v0.27.2 UNSIGNED PRIVATE TEST PACKAGE
============================================

PACKAGE BOUNDARY: GitHub Latest is immutable release 379360694, published
2026-08-30T18:06:14Z from lightweight tag v0.27.2 at exact commit
9c6ca3de96aa7eb261c65b7dee768ab48144169c. It has seven packages plus
WebJam-v0.27.2-SHA256SUMS.txt. Use this private test package only when its exact
filename and SHA-256 appear in that manifest. A checkout or branch build is not
a substitute. The existing exact Jamulus 3.12.2 and 3.12.3 records are approved
through v0.27.2 for live Host/Join.
Physical audio, hardware, signed-install, and distribution-policy gates
remain NOT RUN unless the release evidence names this exact file and SHA-256.

Everything below describes the published unsigned private test package. The
intended target is 64-bit Ubuntu 22.04; no v0.27.2 Linux build is currently
physically certified. An extracted
package must keep the entire WebJam folder together because the files under
_internal are required at runtime.

This release upgrades cryptography to 50.0.0 for CVE-2026-69247,
CVE-2026-69248, and CVE-2026-69249 using an exact hash-locked upstream Linux
wheel.

The artifact is a portable ZIP, not a distro package. It does not install an
application-menu entry, .desktop launcher, or system icon. Start the included
WebJam executable from this folder; the running application uses WebJam's
continuous trefoil identity in its own windows.

The v0.27.2 Linux and Windows packages are built for joining a jam hosted from
the macOS build; physical cross-platform joining remains NOT RUN.
The profile-specific Host action remains disabled on this platform; this
package does not claim to provide the macOS-only managed server.

Music and Podcast & Voice retain standalone Reference Studio for local
songwriting, recording, arranging, mixing, and WAV/FLAC bounce without joining
Jamulus.

The source retains Music and Podcast & Voice as GA creator profiles. Review &
Rehearsal is Preview. Live WebJam-audio Join, participation in a host-controlled
Record Session, and playback/read-only completed-take review are source-eligible;
meanwhile
standalone projects, take edit/comp/mix mutation, track export, shared notes,
visual sync, and media timecode are blocked. No profile directly or
automatically taps a meeting app, browser, or system output. Local scratchpad
notes remain profile-scoped on this computer only; they are never shared or
media-timecoded.

FIRST RUN

WebJam automatically checks a signed, version-specific component catalog; it
never follows an upstream "latest" download. An approved Jamulus update is
downloaded and verified in the background, but installing a system package
always requires your explicit approval. WebJam never invokes hidden sudo and
never installs while a jam, recording, Shared Track, reconnect, or Jamulus
launch is active.

The sealed v0.22.5 catalog does not authorize v0.27.2 and is rejected. The baked
registry separately authorizes the existing exact Jamulus 3.12.2 and 3.12.3
records through v0.27.2. Presence of embedded 3.12.2 bytes does not authorize
an identity mismatch. Shared Track uses its separate approved headless path;
unlisted client/server versions still fail closed.

1. Only from an exact package verified against the v0.27.2 release manifest,
   install the included Jamulus 3.12.2 package:

     ./install-jamulus.sh

   Or install it directly:

     sudo apt install ./Jamulus/jamulus_3.12.2_ubuntu_amd64.deb

2. Start WebJam:

     ./WebJam

3. Choose the selected profile's Join, Join Recording, or Join Review action
   and paste the invitation from the Mac host. Jamulus owns the
   audio interface, channels, buffer, headphones, and monitor mix.

Conversation/video is optional and is not bundled. Any meeting platform can
use the explicit Join / Open Meeting handoff when its link is public HTTPS with
a DNS hostname and passes WebJam's safety checks. Known Webex, Zoom, Teams,
Google Meet, and FaceTime links receive friendly labels; another accepted
provider remains neutral and is not natively verified. FaceTime is Mac-only,
and WebJam never claims join or mute. A future authorized Linux package can locate Webex but
does not establish a trusted publisher identity, so the Webex-only Show Webex
App and focus-based Mute in Webex guidance stay unavailable. Join / Open
Meeting hands off the saved link to the default browser or installed link
handler. Cisco owns Webex installation, sign-in, and updates. Keep the selected
meeting service muted while playing because Jamulus remains the music path.
WebJam never directly or automatically taps a meeting app, browser, or system
output. Local Originals record explicitly selected input devices, so do not
route meeting or system-output audio into those inputs.

This ZIP is not a distro-native signed package. Verify the published SHA-256
before extracting it. WebJam separately verifies its packaged transport hash,
x86-64 ELF architecture, safe file mode/owner, and embedded source build ID.
