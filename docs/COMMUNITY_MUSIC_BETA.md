# Music in the Stalemate community beta

The private IRC-style website and native WebJam music have separate setup.
Invited visitors can use website chat and creative rooms without installing
WebJam. A successful chat or drawing session does not establish music readiness.

The complete Mac/Windows/Linux music beta is still being qualified. Source
tests and successful builds do not prove installation, physical audio or
playing together from different houses.

## Choose the right build

Use the package supplied and identified by the test organizer. Match its CPU
architecture and checksum. The public download and a newer private candidate
can both say `0.29.0` while accepting different invitation formats; the version
number alone is insufficient. Ask for the qualified candidate before scheduling
a remote music test.

| Computer | Native Music in this source | Remaining beta qualification |
| --- | --- | --- |
| Apple Silicon Mac | Host and Join paths | Qualified arm64 package, normal launch/permissions and physical two-way audio/recovery |
| Intel Mac | Host and Join paths | Qualified x64 package, normal launch/permissions and physical two-way audio/recovery |
| Windows x64 | Join path; Music Host is unavailable | Current unsigned remote transport is blocked by its signature policy; a qualified signed build and physical acceptance are required |
| Linux x64 | Join path; Music Host is unavailable | Ubuntu 22.04 is the package baseline; actual installation/audio/recovery remain separate from build checks |

Windows opening the app after SmartScreen does not make the native remote
transport signature valid. An unsigned launch, installer checksum, same-network
session or source test is not a Windows remote Music pass. Ask the organizer for
a qualified build; keep the existing signature check enabled.

Anyone admitted to the website can still host a website room. Native Music
hosting is currently Mac-only. The website integration candidate keeps the
optional, higher-delay browser music path disabled. It does not replace
the required Windows native music qualification.

## Host, invite and join

Once the organizer confirms the candidate and test network:

1. The Mac host follows the [creator guide](../USER_GUIDE.md#host-a-live-session)
   and keeps WebJam open. Each guest uses a build qualified for the same test.
2. Use **Copy Invite** and share the complete message with the intended guest.
   A bare link can omit required signaling. Treat the invitation as private;
   leave it out of screenshots, logs and bug reports.
3. The guest follows [Join a live session](../USER_GUIDE.md#join-a-live-session).
   If the message says same network, both computers must use the host's local
   network. Different home Wi-Fi networks are not the same network.
4. Follow the actual recovery action: **Try Again** only when offered, or ask
   for a fresh full invitation and choose **Paste New Invite**. A previous
   attempt may have consumed the invitation. Do not repeatedly reuse it.
5. With the intended audio devices and headphones, confirm that both people
   actually hear each other. Record timing, dropouts, Leave, a second Join and
   device cleanup. A connected indicator or moving meter is insufficient.

A hotspot can be part of the planned two-network trial, but successful traversal
and usable audio must be observed. Tailscale may carry test-control traffic;
its connection status does not prove the path used by music or ordinary home
NAT traversal. The organizer must distinguish that path in the test record.

Keep Mac, Windows and Linux observations separate. Until the qualified package,
trust, device and cross-network checks pass, describe this as an integration
preview rather than a completed cross-platform music beta.
