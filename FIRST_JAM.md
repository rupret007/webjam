# Your first WebJam

WebJam's normal path is four short moves:

> Host or Join → Confirm your sound → Band Check → Play

There is no server setup for the band to perform. Band Check guides each
musician through input, headphones, and a short recording before playing.
The private macOS v0.12.0 candidate starts the music service in the background.
This first-jam guide remains the ordinary same-private-LAN path. The separate
v3 `reference-local` profile is a loopback developer lab, not a public remote
service.

**Artifact scope:** This guide's test-night steps apply to
`WebJam-v0.12.0-TEST-NIGHT-macos-arm64.zip`, built from `796e9a4`. It includes
the short sound-confirmation screen, CoreAudio route preflight,
recording-storage guard, and private in-progress recording-evidence journal.
The v0.11.0 ZIP is preserved only as a rollback artifact.

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
2. Confirm your name and band sound. Use **Band input** and **Band output &
   review** when you need to change the intended CoreAudio route.
3. Complete Band Check, then choose **Start Session**.
4. If WebJam needs microphone access, follow its permission explanation. If
   access was denied earlier, use **Open System Settings**, enable WebJam under
   Privacy & Security → Microphone, return, and choose **Try Again**.
5. Wait for **Ready to share**. WebJam is starting its bundled music service
   while the HUD says **Starting your jam**.
6. Click **Copy Invite** and send the entire `webjam://join?...` link to the
   other musician. Do not edit it or extract an address from it.

The invitation appears only after WebJam has confirmed that the hosted service
is alive. A v2 invite normally contains the Jamulus destination plus a random
credential that enrolls this bandmate in the same-LAN private recording plane.
It is reusable during this host-peer session, not a one-use token; anyone
holding it on the LAN can enroll. Treat the whole link as private: send it only
to the intended bandmate and do not paste it into screenshots or support notes.
If WebJam warns **Automatic
Local Originals are off**, its v1 fallback still joins the music session and
receives a host-side server track, but WebJam provides no local-original
capture or delivery path on that guest Mac.

## Join

The bandmate can either:

- open the invitation link, which launches WebJam and accepts the connection
  details before the readiness/start step; or
- open WebJam, click **Join a Jam**, paste the complete link into the one field,
  and click **Join Jam**.

Confirm your name and band sound, then complete Band Check and choose **Start
Session**.

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

In the v0.12.0 candidate, **Settings → Band input / Band output & review**
preflights a stable CoreAudio pair and stages it for Jamulus before launch.
WebJam's input meter and optional local-original recorder still use a separate
Core Audio/PortAudio stream. A passing meter is useful, but only the other
musician's ears prove the Jamulus route. Use wired headphones and confirm both
directions out loud.

## Record a multitrack take

The host can press **Record** in the bottom control bar, then open **More →
Multitrack Studio** to watch lanes or review the take.

1. Confirm there is a lane for each connected musician.
2. On the host and each v2-connected guest that should keep interface
   originals, open **Recording Setup**,
   enable **Keep interface inputs 1 and 2 as isolated local originals**, and
   choose a shareable two-channel 48-kHz input. This is explicit opt-in.
   Automatic guest-original delivery also requires the active v2 invite.
3. WebJam checks that the selected Takes folder is writable and has enough
   conservative free space before it arms Record. If you need another folder,
   choose it before starting the session; that setting stays fixed while a jam
   is running.
4. Click **Record**, play, then stop. Wait while WebJam validates and saves the
   take.
5. Select the take in the Studio library to view its waveforms, choose the
   wired playback output, and test gain, pan, mute, and solo.
6. Press **Export for Logic**, then **Show Logic Export**. Drag every numbered
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
Studio, an optional video/conversation link, Settings, and Band Check.
Settings covers your display name, Band input, Band output & review, and an
optional conversation link. It stages that verified CoreAudio pair for the
next Jamulus session; it never claims the route is audible until the musicians
confirm it.

## Reconnect

A short network interruption can reconnect automatically. During the attempt,
WebJam says it is reconnecting rather than showing stale readiness. With an
active v2 invite, an opted-in local original keeps recording even while the
peer control plane is offline; after reconnect, WebJam resumes verified
delivery without deleting that local file. If the attempt times out, restore
the same-network connection and use the single **Try Again** action.

## Finish

The host clicks **End Session**; the bandmate clicks **Leave Jam**. If the host
take is recording or validating, WebJam blocks End Session: press **Stop Rec**
if needed, wait for **Take saved**, then end the jam. Ending the host session
ends the jam for everyone. Leaving disconnects only that Mac after finalizing
any active opted-in guest original, persisting its transfer queue, and trying
one final upload. **Ending…** or **Leaving…** remains visible until
cleanup is actually finished. If guest originals are enabled, wait until
Studio reports them verified and arrived before ending; otherwise preserve
them on the guest for recovery. Quit both copies of WebJam when finished.

On the next launch, the same two choices—**Host a Jam** and **Join a Jam**—are
shown again.

For tonight's evidence checklist, use
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
