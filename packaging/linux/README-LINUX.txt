WEBJAM v0.22.5 FOR LINUX x64
=============================

PRE-PUBLICATION TEST CANDIDATE: use this v0.22.5 package only after its draft,
checksum, and verified GitHub promotion gates pass. Install only the exact
asset listed in WebJam-v0.22.5-SHA256SUMS.txt whose SHA-256 matches your download.
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
"Host a Jam" remains disabled on this platform; this package does not claim
to provide the macOS-only managed band server.

Standalone Reference Studio remains available for local songwriting,
recording, arranging, mixing, and WAV/FLAC bounce without joining Jamulus.

FIRST RUN

WebJam automatically checks a signed, version-specific component catalog; it
never follows an upstream "latest" download. An approved Jamulus update is
downloaded and verified in the background, but installing a system package
always requires your explicit approval. WebJam never invokes hidden sudo and
never installs while a jam, recording, Reference Track, reconnect, or Jamulus
launch is active.

1. When offline or when no approved update is ready, install the included,
   checksum-verified Jamulus 3.12.2 fallback package:

     ./install-jamulus.sh

   Or install it directly:

     sudo apt install ./Jamulus/jamulus_3.12.2_ubuntu_amd64.deb

2. Start WebJam:

     ./WebJam

3. Choose Join a Jam and paste the invitation from the Mac host. Jamulus owns
   the audio interface, channels, buffer, headphones, and monitor mix.

Webex is optional and is not bundled. Choose Webex Controls on WebJam's main
session rail or More > Webex Controls to show Conversation controls without
opening or rejoining the meeting. This Linux package can locate Webex but does
not establish a trusted publisher identity, so Show Webex App and the
focus-based Mute in Webex guidance stay unavailable. Join / Open Meeting hands
off the saved link to a supported browser. Cisco owns Webex installation,
sign-in, and updates. Keep Webex muted while playing because Jamulus remains
the music path.

This ZIP is not a distro-native signed package. Verify the published SHA-256
before extracting it. WebJam separately verifies its packaged transport hash,
x86-64 ELF architecture, safe file mode/owner, and embedded source build ID.
