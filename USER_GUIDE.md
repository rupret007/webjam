# WebJam User Guide

## The whole idea

**Host or Join. Band Check. Play.**

WebJam wraps the low-latency Jamulus engine in a musician-facing session. It
starts the needed processes, chooses defaults, creates the invitation, watches
the connection, and records synchronized tracks without asking musicians to
understand the plumbing.

The v0.10.0 interface uses near-black surfaces, white text, and restrained burnt
orange for the next important action. Purple, teal, technical setup panels, and
competing session controls are not part of the normal path.

## Install

1. Download the build for your Mac and unzip it.
2. On the first launch, Control-click **WebJam.app**, choose **Open**, then
   confirm **Open**. The test build is ad-hoc signed, not notarized.
3. Allow microphone access if macOS asks. WebJam explains why before the
   system prompt. If access was denied previously, use **Open System Settings**
   in WebJam, enable it under Privacy & Security → Microphone, return, and
   choose **Try Again**.
4. Use wired headphones and connect the interface before launching.

The downloadable macOS app contains its own Jamulus client and server.
Ordinary rehearsals do not require BlackHole, VB-CABLE, Webex, a Terminal
command, or a separate server setup.

## Run Band Check

Band Check is the one readiness path for both musicians. After **Host a Jam**
or an invite is accepted, WebJam opens it before starting a new or changed
setup. Open it again at any time with `F2`, **More → Band Check**, or
**Settings → Run Band Check**.

Follow the prompts to confirm the local input meter, left/right headphones, a
five-second isolated recording, and playback. Band Check says **Ready to Jam**,
**Ready with a Warning**, or **Action Needed** in words. Its local input meter
proves only what WebJam's separate PortAudio stream can hear; the other
musician's ears must still confirm the live Jamulus route.

## Start a jam

### Host

1. Launch WebJam.
2. Choose **Host a Jam**.
3. Complete Band Check if WebJam asks, then choose **Start Session**.
4. WebJam displays **Starting your jam** while it starts the bundled server and
   connects the host in the background.
5. When **Ready to share** appears, click **Copy Invite**.
6. Send the complete `webjam://join?...` link to the bandmate.

The test-night invitation is for Macs on the same private IPv4 LAN. A v2 link
normally contains a random private enrollment credential for guest-original
delivery. It is reusable during this host-peer session, not a one-use token;
anyone holding it on the LAN can enroll. Send the whole link only to the
intended bandmate; do not put it in screenshots, notes, logs, or support
messages. It never contains the recorder RPC secret or local file paths, and
WebJam masks it when pasted. If the host
warns **Automatic Local Originals are off**, its v1 fallback still joins the
music session and receives a host-side server track, but WebJam provides no
local-original capture or delivery path on that guest Mac.

### Join from the link

Open the host's invite link. macOS launches WebJam and fills in and accepts the
connection; opening the link alone is not a readiness result. Complete Band
Check if WebJam asks, then choose **Start Session**.

If clicking the link is unavailable, launch WebJam, choose **Join a Jam**, paste
the link into the single field, and click **Join Jam**. Complete Band Check if
WebJam asks, then choose **Start Session**.

## Read the session state

The one readiness strip at the top is authoritative:

| Message | Meaning |
|---|---|
| Starting your jam | Host services are starting. |
| Ready to share | The host server passed its readiness check; copy the invite. |
| Joining your jam | The client is connecting. |
| You’re connected | The band connection exists; play a note to verify input. |
| You’re ready | WebJam detected a real local input signal. |
| Bandmate connected | Another musician is in the roster. |
| Ready to play | A bandmate is connected and local input has been detected. |
| Connection interrupted | The previous connection is no longer being treated as live; WebJam is reconnecting. |
| This jam isn’t available | Confirm the host is still running and ask for a fresh invite. |
| Microphone access is off | Open macOS microphone settings from the visible action, allow WebJam, then retry. |
| Something needs attention | Follow the one visible recovery action. |

WebJam does not claim that someone can hear audio merely because a process
started. Real participant data establishes the connection, and real observed
levels establish input activity. When local metering is unavailable, the meter
stays still instead of displaying synthetic motion.

## Your live mix

Each connected musician gets a card with:

- a real observed level meter;
- a fader that changes only your monitor mix;
- **Mute Monitor** and **Solo** for your monitor mix.

Your card is marked **You**. A moving local meter proves input activity WebJam
can observe; remote moving meters prove those musicians are sending signal.

Press `Ctrl+S` to save the current monitor mix and `Ctrl+O` to restore it.
WebJam also restores the saved default mix after reconnecting.

## Multitrack recording

Recording belongs to the host and requires no routing form.

1. Click **Record** in the bottom control bar, or open **More → Multitrack
   Studio** first if you want to watch the armed lanes.
2. Play.
3. Click **Stop Rec**.
4. WebJam waits for stable files, validates the take, and opens it in Studio.

The resulting take contains synchronized server tracks—one per musician—plus
a manifest that preserves the musician names WebJam observed. In Studio you
can play, pause, scrub, choose a wired output, and change each track's gain,
pan, mute, or solo without modifying the source WAVs.

Open **Recording Setup** inside Studio to select its playback output. The host,
or a guest connected through an active v2 invite, can explicitly keep two
isolated interface inputs there—for example, guitar on input 1 and vocal on
input 2. WebJam saves that choice and records two local
PCM24/48-kHz originals while the host's take is active; the live Jamulus path is
unchanged. With an active v2 private invite, a guest original stays on that
guest Mac, continues through a peer outage, and transfers a verified copy to
the host when the private-LAN service is available again. A v1 guest has no
WebJam-orchestrated local-original capture or delivery.

For a reliable Logic handoff, select the finished take and click **Export for
Logic**. WebJam creates a new atomic export containing numbered, musician-named
PCM24 WAV stems that all start at `0:00` and have the same length, server and
Studio references, markers, recording/alignment reports, independent audio
analysis, source evidence, checksums, and import instructions. Drag the numbered
stems into a new Logic project together; do not add either reference as another
performance stem. The original take is never rewritten.
See [`RECORDING_AND_LOGIC.md`](RECORDING_AND_LOGIC.md).

A joining musician sees the Studio but does not control the host recorder.
Their live audio is captured as its own host-side server track. With an active
v2 invite, they can separately opt in to keeping this Mac's two local interface
originals.

## More

The live session keeps secondary features in one **More** menu:

- **Live Session** — return to the band cards.
- **Session Notes** — capture notes and export a local brief.
- **Multitrack Studio** — record and review takes.
- **Add Video or Conversation** — optionally open a configured Webex link.
- **Talk Break** — appears only after conversation has been opened.
- **Settings** — change your displayed name or optional conversation link.
- **Band Check** — rerun or observe the guided readiness check (`F2`).

Webex is not part of the startup path. If used for conversation, keep the
Webex microphone muted while playing so its delayed audio does not duplicate
the low-latency music path.

## End the session

The host clicks **End Session**; a joined musician clicks **Leave Jam**. The
host action ends the jam for everyone, while Leave Jam disconnects only that
Mac. WebJam keeps **Ending…** or **Leaving…** on screen until work is complete.
If the host take is recording or still validating, **End Session** is blocked:
press **Stop Rec** if needed, wait for **Take saved**, then end the jam. A guest
**Leave Jam** finalizes any active opted-in local original, persists its upload
queue, and makes a final upload attempt before disconnecting. If the host is
unavailable, that original and its resumable queue stay on the guest Mac.
Quitting uses the same ownership-aware cleanup.
If guest originals are enabled, wait until Studio reports them verified and
arrived before **End Session**. Otherwise, preserve the originals on the guest
Mac for recovery.

## Recovery

### Could not reach the jam

Make sure both Macs are on the same Wi-Fi and that guest-device isolation is
off, then click **Try Again**. WebJam stops an unproductive connection attempt
after 30 seconds rather than spinning forever.

### Connection interrupted

Do not rely on the old participant cards or meters. WebJam clears stale audio
truth and reconnects automatically. It reports ready again only after the local
session is actually present. If automatic recovery times out, use the single
**Try Again** action.

### This jam isn't available

Ask the host to confirm WebJam is still running and send a fresh invite. A
stale link cannot create a session after its host has ended the jam.

### Input meter does not move

Play directly into the connected interface. If macOS asks, allow microphone
access. Open **More → Band Check** if the meter still does not move; it keeps
technical detail collapsed unless you choose to inspect it.

### Microphone access is off

Choose **Open System Settings**, enable WebJam under Privacy & Security →
Microphone, return to WebJam, and choose **Try Again**. Reinstalling Jamulus or
editing a port will not fix a macOS permission denial.

### Invite opens the wrong session

Opening a new invite while WebJam is running asks once before safely ending
the current jam and switching to the new one. If a hosted take is active or
validating, WebJam refuses the switch until **Stop Rec** and **Take saved**.

### App will not open

Control-click the app and choose **Open**. If macOS says the downloaded test
app is damaged or incomplete, stop: that is a packaging failure. Record the
exact ZIP filename and SHA-256, preserve the app unchanged, and report the
message instead of removing quarantine or bypassing the bundle check.

## Useful shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+L` | Edit the session title |
| `Ctrl+S` / `Ctrl+O` | Save / load the default monitor mix |
| `Ctrl+Shift+S` / `Ctrl+Shift+O` | Save / load a named monitor mix |
| `Ctrl+M` | Mute or unmute all remote channels in your monitor |
| `Ctrl+Shift+M` | Talk Break / Resume Music when conversation is active |
| `Ctrl+T` | Insert a timestamp in Session Notes |
| `Ctrl+Shift+R` | Reset all faders to unity |
| `Ctrl+Shift+D` | Copy redacted diagnostics |
| `Ctrl+,` | Open preferences |
| `F2` | Open Band Check |
| `Ctrl+1` / `Ctrl+2` / `Ctrl+3` | Live / Notes / Studio |
| `F11` / `Esc` | Enter / leave fullscreen |

For the two-Mac physical acceptance run, use
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
