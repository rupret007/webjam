WEBJAM v0.27.0 PRIVATE TEST CANDIDATE FOR LINUX x64
===================================================

PRIVATE TEST CANDIDATE: use this package only when its exact filename appears
in the v0.27.0 GitHub release and its SHA-256 matches that release's manifest.
Do not use the immutable v0.26.0 checksum manifest for this build.
Physical audio, hardware, signed-install, and distribution-policy gates
remain NOT RUN unless the release evidence names this exact file and SHA-256.

This test build is certified only for 64-bit Ubuntu 22.04. Other Ubuntu
versions and Linux distributions are not certified. Keep the entire WebJam
folder together; the files under _internal are required at runtime.

This candidate upgrades cryptography to 50.0.0 for CVE-2026-69247,
CVE-2026-69248, and CVE-2026-69249 using an exact hash-locked upstream Linux
wheel.

This is a portable ZIP, not a distro package. It does not install an
application-menu entry, .desktop launcher, or system icon. Start the included
WebJam executable from this folder; the running application uses WebJam's
continuous trefoil identity in its own windows.

Linux and Windows builds currently join a jam hosted from the macOS build.
The profile-specific Host action remains disabled on this platform; this
package does not claim to provide the macOS-only managed server.

Music and Podcast & Voice retain standalone Reference Studio for local
songwriting, recording, arranging, mixing, and WAV/FLAC bounce without joining
Jamulus.

Music and Podcast & Voice are GA creator profiles. Review & Rehearsal is
Preview: live WebJam-audio Join, participation in a host-controlled Record
Session, and playback/read-only completed-take review are available, while
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

The sealed v0.22.5 catalog does not authorize v0.27.0 and is rejected. Until a
new signed v0.27 catalog exists, this candidate uses the embedded Jamulus
3.12.2 fallback rather than offering a managed 3.12.3 download.

1. When offline or when no approved update is ready, install the included,
   checksum-verified Jamulus 3.12.2 fallback package:

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
and WebJam never claims join or mute. This Linux package can locate Webex but
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
