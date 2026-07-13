# WebJam — Quick Start

WebJam is designed around four words:

**Host. Share. Join. Play.**

Musicians do not configure servers, ports, recording folders, virtual audio
devices, or routing modes. WebJam starts the bundled music engine, chooses the
safe defaults, and keeps the technical details out of the rehearsal.

The v0.9.0 app is deliberately spare: a black and white session with burnt
orange reserved for the next important action. Host and Join are the only
choices at launch.

## What you need

- A Mac running macOS 13 or newer for the current host build
- Wired headphones and an audio interface
- Both musicians on the same Wi-Fi for the test-night invite flow
- The WebJam app on both Macs

The downloadable macOS build already contains the Jamulus client and server.
BlackHole, VB-CABLE, and Webex are not required to start playing.

## Host

1. Open WebJam.
2. Click **Host a Jam**.
3. If macOS asks for microphone access, allow it. If access was previously
   denied, use WebJam's **Open System Settings** action, allow access, return,
   and choose **Try Again**.
4. Wait for **Ready to share**.
5. Click **Copy Invite** and send that link to your bandmate.

WebJam starts the server and music engine in the background. The invite looks
like `webjam://join?...`; it contains the connection, so nobody copies an IP
address or port.

## Join

Preferred:

1. Open the host's invite link.
2. WebJam launches, fills in the connection, and joins automatically.

Manual fallback:

1. Open WebJam and click **Join a Jam**.
2. Paste the invite link.
3. Click **Join Jam**.

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

In Studio, **Recording Setup** can optionally add the host interface's first
two inputs as separate 24-bit stems. This is useful for guitar on input 1 and
vocal on input 2; it is not required for the normal per-musician recording.

After a take verifies, select it and press **Export for Logic**. WebJam writes
new numbered 24-bit stems that all start at `0:00` and have the same length,
plus a stereo rough mix and import instructions. Drag the numbered stems into
Logic together. The original recorder files remain unchanged. See
[`RECORDING_AND_LOGIC.md`](RECORDING_AND_LOGIC.md).

Joining musicians do not configure recording. Their audio becomes a separate
track on the host automatically.

## Optional tools

Everything that is not required to play is under **More**:

- Session Notes
- Multitrack Studio
- Add Video or Conversation
- Settings (name and optional conversation link only)
- Troubleshooting

Webex is optional and opens externally. If the band uses it for conversation,
keep its microphone muted while playing to avoid delayed duplicate music.

## End the jam

The host clicks **End Session**; a joined musician clicks **Leave Jam**. The
different labels are intentional: the host action ends the jam for everyone,
while Leave Jam disconnects only that Mac. WebJam keeps **Ending…** or
**Leaving…** visible until cleanup finishes. On the host, it safely stops and
saves an active take before stopping the client, hosted server, and sleep
assertion. Closing WebJam uses the same role-aware confirmation and cleanup.

## If joining fails

Make sure both Macs are on the same Wi-Fi, then click **Try Again**. The current
invite is a local-network flow; a home router, firewall, or guest Wi-Fi that
isolates devices can prevent the two Macs from reaching each other.

For test-night installation and acceptance steps, see
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
