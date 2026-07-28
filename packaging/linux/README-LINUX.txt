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

FIRST RUN

1. Install the included, checksum-verified Jamulus 3.12.2 package:

     ./install-jamulus.sh

   Or install it directly:

     sudo apt install ./Jamulus/jamulus_3.12.2_ubuntu_amd64.deb

2. Start WebJam:

     ./WebJam

3. Choose Join a Jam and paste the invitation from the Mac host. Jamulus owns
   the audio interface, channels, buffer, headphones, and monitor mix.

This ZIP is not a distro-native signed package. Verify the published SHA-256
before extracting it. WebJam separately verifies its packaged transport hash,
x86-64 ELF architecture, safe file mode/owner, and embedded source build ID.
