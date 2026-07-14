# WebJam — Quick Start

WebJam is designed around four short moves:

**Host or Join. Confirm your sound. Band Check. Play.**

Musicians do not configure servers, ports, virtual audio devices, or routing
modes. WebJam starts the bundled music engine and keeps technical details out of
the rehearsal. In current macOS source, the two Settings choices—**Band input**
and **Band output & review**—are the Jamulus route WebJam stages for the next
session. WebJam still asks musicians to confirm what they hear.

The private v0.11.0 candidate is deliberately spare: a black and white session with burnt
orange reserved for the next important action. Host and Join are the only
choices at launch.

> **Artifact scope:** The frozen
> `WebJam-v0.11.0-TEST-NIGHT-macos-arm64.zip` package built from `1a03927`
> remains tonight's test artifact. It goes directly from Host/Join to Band
> Check; the short sound-confirmation screen, CoreAudio route preflight, and
> recording-storage guard below are current-source behavior only until a new
> package is built and tested.

This quick start covers the ordinary same-private-LAN flow. The v3
`reference-local` profile is a loopback-only developer lab, not a deployed
Internet service.

## What you need

- A Mac running macOS 13 or newer for the current host build
- Wired headphones and an audio interface
- Both musicians on the same Wi-Fi for the test-night invite flow
- The WebJam app on both Macs

The downloadable macOS build already contains the Jamulus client and server.
BlackHole, VB-CABLE, and Webex are not required to start playing.

## Band Check

In current source, a new or changed setup opens one short screen to confirm
your name, **Band input**, and **Band output & review** before Band Check. The
frozen v0.11 app opens Band Check directly. Band Check walks each musician through the
local input meter, left/right headphones, a five-second recording, and playback, then says
**Ready to Jam**, **Ready with a Warning**, or **Action Needed**. Press `F2` to
run it again. A passing local meter is useful, but only your bandmate's ears
prove the live Jamulus route.

## Host

1. Open WebJam.
2. Click **Host a Jam**.
3. In current source, confirm your name and band sound; in the frozen v0.11
   app, continue directly to Band Check. Complete Band Check and choose
   **Start Session**. If macOS
   asks for microphone access, allow it. If access was previously
   denied, use WebJam's **Open System Settings** action, allow access, return,
   and choose **Try Again**.
4. Wait for **Ready to share**.
5. Click **Copy Invite** and send that link to your bandmate.

WebJam starts the server and music engine in the background. The invite looks
like `webjam://join?...`; it normally contains the connection and a private v2
enrollment credential, so nobody has to copy an IP address or port separately.
It is reusable during this host session, not a one-use token; anyone holding it
on the LAN can enroll. Send the whole link only to the intended bandmate and
keep it out of screenshots, notes, and support text. If WebJam warns
**Automatic Local Originals are off**, the v1
fallback still lets the bandmate join and play and still creates a host-side
server track, but WebJam provides no local-original capture or delivery path on
that guest Mac.

## Join

Preferred:

1. Open the host's invite link.
2. WebJam launches and fills in and accepts the connection.
3. In current source, confirm your name and band sound; in the frozen v0.11
   app, continue directly to Band Check. Complete Band Check and choose
   **Start Session**.

Manual fallback:

1. Open WebJam and click **Join a Jam**.
2. Paste the invite link.
3. Click **Join Jam**.
4. In current source, confirm your name and band sound; in the frozen v0.11
   app, continue directly to Band Check. Complete Band Check and choose
   **Start Session**.

## Know when it is working

The readiness strip uses plain language:

- **Starting your jam** — WebJam is starting the host services.
- **Ready to share** — the host server is ready and the invite can be sent.
- **You’re connected** — the app reached the band; play a note to verify input.
- **You’re ready** — a real input signal was detected.
- **Bandmate connected** — another musician joined.
- **Connection interrupted** — WebJam is reconnecting and is not claiming the
  stale session is ready.
- **Something needs attention** — follow the single recommended action.

Participant cards show the real levels WebJam can observe. If metering is not
available, WebJam stays silent; it does not animate a fake signal.

## Record a multitrack take

The host can click **Record** in the bottom control bar. The host server records
one synchronized track per connected musician. Open **More → Multitrack
Studio** to see live lanes and completed takes with waveform playback,
scrubbing, selectable headphone output, gain, pan, mute, and solo.

For the frozen test-night ZIP, check that the selected drive has enough free
space before you start the session. If you need a different Takes folder, choose
it before starting the jam; a running session does not allow the folder to
change. The source-only guard noted above will block an unsafe start and warn
about low space only after it is shipped in a new candidate.

In Studio, **Recording Setup** can optionally keep this Mac's first two
interface inputs as separate PCM24/48-kHz originals. The host can opt in; a
guest can do so only with an active v2 private invite. Those guest originals
remain on the guest Mac through an outage and transfer a verified copy to the
host when the private-LAN connection returns. A v1 guest has no
WebJam-orchestrated local-original capture or delivery.

After a take verifies, select it and press **Export for Logic**. WebJam writes
new numbered PCM24 stems that all start at `0:00` and have the same length,
plus server/Studio references, reports, analysis, checksums, and import
instructions. Drag the numbered stems into Logic together. The original
recorder files remain unchanged. See
[`RECORDING_AND_LOGIC.md`](RECORDING_AND_LOGIC.md).

The host controls the shared take. A joining musician's network audio becomes a
separate host-side server track automatically. With an active v2 invite, that
musician may separately opt in to local interface originals.

## Optional tools

Everything that is not required to play is under **More**:

- Session Notes
- Multitrack Studio
- Add Video or Conversation
- Settings (in current source: name, Band input, Band output & review, and an
  optional conversation link; in frozen v0.11: name and optional conversation)
- Band Check

Webex is optional and opens externally. If the band uses it for conversation,
keep its microphone muted while playing to avoid delayed duplicate music.

Choose **Band input** and **Band output & review** in **Settings** before a
session. Current macOS source verifies a unique 48-kHz CoreAudio pair and uses
it to stage Jamulus; the review choice follows the selected output. A moving
local meter still is not proof that your bandmate hears you. The frozen v0.11
test-night ZIP predates this source-only routing behavior.

## End the jam

The host clicks **End Session**; a joined musician clicks **Leave Jam**. The
different labels are intentional: the host action ends the jam for everyone,
while Leave Jam disconnects only that Mac. WebJam keeps **Ending…** or
**Leaving…** visible until cleanup finishes. **End Session** does not stop an
active host take for you: press **Stop Rec**, wait for **Take saved**, then
choose **End Session** again. Leaving finalizes any active opted-in guest
original, persists its transfer queue, and attempts a final upload before
disconnecting; if the host is unavailable, the original and resumable queue
remain on that guest Mac. Closing WebJam uses the same role-aware cleanup.
If guest originals are enabled, wait until Studio reports them verified and
arrived before **End Session**. Otherwise, preserve the originals on the guest
Mac for recovery.

## If joining fails

Make sure both Macs are on the same Wi-Fi, then click **Try Again**. The current
invite is a local-network flow; a home router, firewall, or guest Wi-Fi that
isolates devices can prevent the two Macs from reaching each other.

For test-night installation and acceptance steps, see
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
