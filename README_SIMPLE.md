# WebJam — Quick Start

WebJam is designed around three simple moves:

**Host or Join. Band Check. Play.**

Musicians do not configure servers, ports, recording folders, virtual audio
devices, or routing modes. WebJam starts the bundled music engine, chooses the
safe defaults, and keeps the technical details out of the rehearsal.

The private v0.11.0 candidate is deliberately spare: a black and white session with burnt
orange reserved for the next important action. Host and Join are the only
choices at launch.

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

For a new or changed setup, choosing **Host a Jam** or accepting an invite opens
Band Check before the music session starts. It walks each musician through the
local input meter, left/right headphones, a five-second recording, and playback, then says
**Ready to Jam**, **Ready with a Warning**, or **Action Needed**. Press `F2` to
run it again. A passing local meter is useful, but only your bandmate's ears
prove the live Jamulus route.

## Host

1. Open WebJam.
2. Click **Host a Jam**.
3. Complete Band Check if WebJam asks, then choose **Start Session**. If macOS
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
3. Complete Band Check if WebJam asks, then choose **Start Session**.

Manual fallback:

1. Open WebJam and click **Join a Jam**.
2. Paste the invite link.
3. Click **Join Jam**.
4. Complete Band Check if WebJam asks, then choose **Start Session**.

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
- Settings (name, Band Check input, review playback, and an optional
  conversation link)
- Band Check

Webex is optional and opens externally. If the band uses it for conversation,
keep its microphone muted while playing to avoid delayed duplicate music.

Choose the interface WebJam listens to in **Settings**. Choose **Review
playback** there for Studio and Band Check playback; your live Jamulus output
continues to follow macOS Audio MIDI Setup.

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
