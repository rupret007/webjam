WEBJAM FOR LINUX x64
====================

This test build is certified only for 64-bit Ubuntu 22.04. Other Ubuntu
versions and Linux distributions are not certified. Keep the entire WebJam
folder together; the files under _internal are required at runtime.

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

Webex is optional and is not bundled. Choose More > Webex / Conversation in
WebJam to open your configured meeting externally. On platforms where WebJam
cannot verify a native Webex app, a supported browser remains the fallback.
Cisco owns Webex installation, sign-in, and updates. Keep Webex muted while
playing because Jamulus remains the music path.

This ZIP is not a distro-native signed package. Verify the published SHA-256
before extracting it. WebJam separately verifies its packaged transport hash,
x86-64 ELF architecture, safe file mode/owner, and embedded source build ID.
