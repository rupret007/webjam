WEBJAM v0.27.2 UNSIGNED SOURCE CANDIDATE — NOT BUILD-ELIGIBLE
==============================================================

NO PACKAGE IS AUTHORIZED: v0.27.2 source is not GitHub Latest. Every approved
Jamulus compatibility range ends at v0.27.1, so required package CI and live
Host/Join fail closed. Do not build or use this candidate until separate
compatibility evidence is reviewed and an exact release manifest exists.
Do not use the published v0.27.1 checksum manifest for this post-tag build.
Physical audio, hardware, signed-install, and distribution-policy gates
remain NOT RUN unless the release evidence names this exact file and SHA-256.

Everything below describes a future package only after the blocked
compatibility boundary is resolved. The intended target is 64-bit Ubuntu
22.04; no v0.27.2 Linux build is currently certified. A future extracted
package must keep the entire WebJam folder together because the files under
_internal are required at runtime.

This candidate upgrades cryptography to 50.0.0 for CVE-2026-69247,
CVE-2026-69248, and CVE-2026-69249 using an exact hash-locked upstream Linux
wheel.

The future artifact is a portable ZIP, not a distro package. It does not install an
application-menu entry, .desktop launcher, or system icon. Start the included
WebJam executable from this folder; the running application uses WebJam's
continuous trefoil identity in its own windows.

The released v0.27.1 Linux and Windows packages can join a jam hosted from the macOS build.
The profile-specific Host action remains disabled on this platform; this
package does not claim to provide the macOS-only managed server.

Music and Podcast & Voice retain standalone Reference Studio for local
songwriting, recording, arranging, mixing, and WAV/FLAC bounce without joining
Jamulus.

The source retains Music and Podcast & Voice as GA creator profiles. Review &
Rehearsal is Preview. Only after compatibility authorization would live
WebJam-audio Join, participation in a host-controlled Record Session, and
playback/read-only completed-take review be available; meanwhile
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

The sealed v0.22.5 catalog does not authorize v0.27.2 and is rejected. Every
immutable Jamulus range also ends at v0.27.1. Presence of embedded 3.12.2 bytes
does not authorize them for this source identity. Shared Track uses a separate
headless path, but live client/server selection and package builds remain
blocked until separate compatibility evidence exists.

1. Only after that separate compatibility authorization and exact release
   evidence, install the included, checksum-verified Jamulus 3.12.2 package:

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
