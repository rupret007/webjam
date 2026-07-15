# WebJam — Quick Start

WebJam is designed around four short moves:

**Host or Join. Confirm your sound. Band Check. Play.**

Musicians do not configure servers, ports, virtual audio devices, or routing
modes. WebJam starts the bundled music engine and keeps technical details out of
the rehearsal. **Band input** and **Band output & review** are the route WebJam
checks before the next session; only musicians can confirm what they hear.

The private v0.15.0 candidate is deliberately spare: black, white, and burnt
orange, with Host and Join as the only launch choices and one clear next action
while a session is running.

> **Tonight's candidate:**
> `WebJam-v0.15.0-TEST-NIGHT-macos-arm64.zip` is verified from
> `30ece85eb6a555dbcb2ef35753e4c6c9e8679770` (SHA-256
> `58ff7a6071d319a11119547028f454b579fd149912d17dfc0fc20ef3cef10152`). It is
> ad-hoc signed, not notarized, and its package-only checks passed. Physical
> CoreAudio, two-Mac, recording/recovery, and external-editor import results
> remain **NOT RUN**. v0.14.0 is the rollback artifact.

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

After Host or Join, confirm your name and band sound, then complete Band Check.
Band Check walks each musician through the
local input meter, left/right headphones, a five-second recording, and playback, then says
**Ready to Jam**, **Ready with a Warning**, or **Action Needed**. Press `F2` to
run it again. A passing local meter is useful, but only your bandmate's ears
prove the live Jamulus route.

## Host

1. Open WebJam.
2. Click **Host a Jam**.
3. Confirm your name and band sound. Complete Band Check and choose **Start
   Session**. If macOS
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
3. Confirm your name and band sound. Complete Band Check and choose **Start
   Session**.

Manual fallback:

1. Open WebJam and click **Join a Jam**.
2. Paste the invite link.
3. Click **Join Jam**.
4. Confirm your name and band sound. Complete Band Check and choose **Start
   Session**.

## Know when it is working

The readiness strip uses plain language:

- **Preparing the invite** — WebJam is verifying the host before it shares a
  private invite.
- **Invite ready** — copy the invite when the bandmate is ready.
- **Joining the jam** — WebJam is connecting but has not claimed a live band.
- **You’re connected** — the app has authenticated the music path.
- **You’re live** — confirmed roster facts are present; musicians still confirm
  what they hear.
- **Reconnecting** — WebJam is not claiming stale readiness.
- **Action needed** — follow the one recommended action.

Participant cards show the real levels WebJam can observe. If metering is not
available, WebJam stays silent; it does not animate a fake signal.

## Record a multitrack take

The host can click **Record** in the bottom control bar. The host server records
one synchronized track per connected musician. Open **More → Multitrack
Studio** to see live lanes and completed takes with waveform playback, a shared
elapsed-time timeline, track selection details, selectable headphone output,
gain, pan, mute, and solo.

Before recording, WebJam checks that the selected Takes folder is writable and
has a conservative amount of free space. It blocks an unsafe start and warns
when space is low; Record recalculates against the actual roster. If you need a
different Takes folder, choose it before starting the jam; a running session
does not allow the folder to change. This package behavior still needs the
physical two-Mac recording run; do not infer it from a meter or an automated
test alone. While recording, local-input writers periodically flush and fsync
their WAVs before advancing a recovery checkpoint. That checkpoint carries
opaque IDs and a durable-frame boundary only; it never turns an interrupted
capture into a completed take.

In Studio, **Recording Setup** can optionally keep this Mac's first two
interface inputs as separate PCM24/48-kHz originals. The host can opt in; a
guest can do so only with an active v2 private invite. Those guest originals
remain on the guest Mac through an outage and a normal transfer resumes a
verified copy to the host when the private-LAN connection returns. Recovered
guest media stays on that guest Mac for manual review; WebJam does not
automatically upload or reconcile recovery media with the host take. A v1
guest has no WebJam-orchestrated local-original capture or delivery.

After a take verifies, open it in Studio and read the shared elapsed-time
ruler. Select a lane to inspect its source, timing, and any known gaps, then
use gain, pan, mute, or solo to review the mix without changing the recording.
Each **Track Export** choice is saved for that take and used by future exports
until you change it; it never changes the WAVs or `webjam-take.json`. WebJam
writes new numbered PCM24 stems that all start at `0:00` and have the same
length, plus server/Studio references, reports, analysis, checksums, and import
instructions. An explicitly silent selected performance track pauses export
until you review it or deliberately deselect it. An unaligned or unverified
selected guest/local original also pauses a timing-ready export: keep the
Jamulus server track, or align and verify the original first. Studio states
those actions without exposing local paths. It is a focused recording-review
workspace, not a DAW: it does not invent tempo, bars, beats, or beat editing.
Import the numbered stems together at `0:00` in your editor. See
[`RECORDING_AND_STUDIO.md`](RECORDING_AND_STUDIO.md).

The host controls the shared take. A joining musician's network audio becomes a
separate host-side server track automatically. With an active v2 invite, that
musician may separately opt in to local interface originals.

## Optional tools

Everything that is not required to play is under **More**:

- Session Notes
- Multitrack Studio
- Add Video or Conversation
- Settings (name, Band input, Band output & review, and an optional
  conversation link)
- Band Check

When explicitly launched with `--test-night`, **More** also contains the hidden
operator-only Test Night checklist. Normal rehearsals do not show it.

Webex is optional and opens externally. If the band uses it for conversation,
keep its microphone muted while playing to avoid delayed duplicate music.

Choose **Band input** and **Band output & review** in **Settings** before a
session. The v0.15.0 candidate verifies a unique 48-kHz CoreAudio pair and uses
it to stage Jamulus; the review choice follows the selected output. A moving
local meter still is not proof that your bandmate hears you.

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
Mac for recovery. On the host, recovered local audio is reopened as a visible
Studio project marked **Needs Attention**, not as a completed take or a
timing-ready track export.

## If joining fails

Make sure both Macs are on the same Wi-Fi, then click **Try Again**. The current
invite is a local-network flow; a home router, firewall, or guest Wi-Fi that
isolates devices can prevent the two Macs from reaching each other.

For test-night installation and acceptance steps, see
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
