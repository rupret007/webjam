# Your first WebJam

WebJam's normal path is four verbs:

> Host → Share → Join → Play

There is no server setup for the band to perform. Band Check guides each
musician through input, headphones, and a short recording before playing.
The private macOS v0.10.0 candidate starts the music service in the background.

## Before anyone opens WebJam

1. Put both Macs on the same private IPv4 home or studio network. For the
   simplest first test, use the same Wi-Fi and turn VPNs off. Live music and
   private-recording delivery are not Internet, VPN, NAT, or IPv6 services.
2. Connect each musician's interface and wired headphones. In macOS System
   Settings → Sound, choose that interface for both input and output.
3. Turn speakers off. If you use Webex or another conversation app, keep its
   microphone muted while music is playing.
4. Extract the test ZIP. Because the private build is ad-hoc signed, first open
   it with Control-click **WebJam.app** → **Open**. A “damaged app” message is
   not expected; report it with the artifact filename instead of bypassing it.

## Host

1. Open WebJam and click **Host a Jam**.
2. If WebJam needs microphone access, follow its permission explanation. If
   access was denied earlier, use **Open System Settings**, enable WebJam under
   Privacy & Security → Microphone, return, and choose **Try Again**.
3. Wait for **Ready to share**. WebJam is starting its bundled music service
   while the HUD says **Starting your jam**.
4. Click **Copy Invite** and send the entire `webjam://join?...` link to the
   other musician. Do not edit it or extract an address from it.

The invitation appears only after WebJam has confirmed that the hosted service
is alive. A current invite contains the Jamulus destination plus a random
credential that enrolls this bandmate in the same-LAN private recording plane.
Treat the whole link as private: send it only to the intended bandmate and do
not paste it into screenshots or support notes.

## Join

The bandmate can either:

- open the invitation link, which launches WebJam and joins the jam; or
- open WebJam, click **Join a Jam**, paste the complete link into the one field,
  and click **Join Jam**.

If joining takes more than 30 seconds, WebJam stops the attempt and offers one
**Try Again** button. Confirm both Macs are still on the same network, then try
once more. If WebJam says the jam is unavailable, ask the host to create and
copy a fresh invitation. Do not hunt for ports or executable paths.

## Play

When connected, participant cards appear for the musicians. Play a note on
each Mac and confirm what the other person actually hears.

- A local meter means WebJam observed input on this Mac.
- A remote meter means WebJam observed band audio for that participant.
- A meter is not proof that someone else heard the signal. Use your ears and
  confirm with the other musician.

Faders, **Mute Monitor**, and Solo change the current listener's monitor mix.
They do not mute outgoing audio or rewrite the other musician's mix.

WebJam's input meter and optional local-original recorder use a separate Core
Audio/PortAudio stream from Jamulus. A passing meter is useful, but only the
other musician's ears prove the Jamulus route. Use wired headphones and confirm
both directions out loud.

## Record a multitrack take

The host can press **Record** in the bottom control bar, then open **More →
Multitrack Studio** to watch lanes or review the take.

1. Confirm there is a lane for each connected musician.
2. On every Mac that should keep interface originals, open **Recording Setup**,
   enable **Keep interface inputs 1 and 2 as isolated local originals**, and
   choose a shareable two-channel 48-kHz input. This is explicit opt-in.
3. Click **Record**, play, then stop. Wait while WebJam validates and saves the
   take.
4. Select the take in the Studio library to view its waveforms, choose the
   wired playback output, and test gain, pan, mute, and solo.
5. Press **Export for Logic**, then **Show Logic Export**. Drag every numbered
   stem WAV into separate Logic tracks together at `0:00`. The stems are
   rendered onto the same project timeline and length. Keep `WebJam Server
   Reference.wav`, `WebJam Studio Reference.wav`, the reports, and checksums
   as evidence, not as extra performance stems. See
   [`RECORDING_AND_LOGIC.md`](RECORDING_AND_LOGIC.md).

Do not quit or end the session while a take is still being checked. If a
capture problem occurs, WebJam preserves recoverable audio rather than
silently deleting it.

## Optional tools

Everything beyond the live jam is under **More**: notes, Multitrack
Studio, an optional video/conversation link, Talk Break when applicable,
Settings, and Troubleshooting. Settings intentionally contains only ordinary
preferences such as your display name and optional conversation link.

## Reconnect

A short network interruption can reconnect automatically. During the attempt,
WebJam says it is reconnecting rather than showing stale readiness. An opted-in
local original keeps recording even while the peer control plane is offline;
after reconnect, WebJam resumes verified delivery without deleting that local
file. If the attempt times out, restore the same-network connection and use the
single **Try Again** action.

## Finish

The host clicks **End Session**; the bandmate clicks **Leave Jam**. If a take is
active, WebJam stops and saves it before stopping the local music client and
the server it owns. Ending the host session ends the jam for everyone; leaving
disconnects only that Mac. **Ending…** or **Leaving…** remains visible until
cleanup is actually finished. Quit both copies of WebJam when finished.

On the next launch, the same two choices—**Host a Jam** and **Join a Jam**—are
shown again.

For tonight's evidence checklist, use
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
