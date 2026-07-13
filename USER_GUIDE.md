# WebJam User Guide

## The whole idea

**Host. Share. Join. Play.**

WebJam wraps the low-latency Jamulus engine in a musician-facing session. It
starts the needed processes, chooses defaults, creates the invitation, watches
the connection, and records synchronized tracks without asking musicians to
understand the plumbing.

The v0.9.0 interface uses near-black surfaces, white text, and restrained burnt
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

## Start a jam

### Host

1. Launch WebJam.
2. Choose **Host a Jam**.
3. WebJam displays **Starting your jam** while it starts the bundled server and
   connects the host in the background.
4. When **Ready to share** appears, click **Copy Invite**.
5. Send the complete `webjam://join?...` link to the bandmate.

The test-night invitation is for Macs on the same Wi-Fi. The link never
contains recorder credentials or local file paths.

### Join from the link

Open the host's invite link. macOS launches WebJam, WebJam fills in the
connection, and the session starts automatically.

If clicking the link is unavailable, launch WebJam, choose **Join a Jam**, paste
the link into the single field, and click **Join Jam**.

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

Open **Recording Setup** inside Studio to select its playback output. A hosting
Mac can also enable two isolated interface inputs there—for example, guitar on
input 1 and vocal on input 2. WebJam saves that choice and adds the two local
24-bit/48 kHz stems to future takes; the live Jamulus path is unchanged.

For a reliable Logic handoff, select the finished take and click **Export for
Logic**. WebJam creates a new atomic export containing numbered, musician-named
24-bit WAV stems that all start at `0:00` and have the same length, a stereo
rough mix reflecting the current Studio controls, import instructions, and an
evidence manifest. Drag the numbered stems into a new Logic project together;
do not add the rough mix as another stem. The original take is never rewritten.
See [`RECORDING_AND_LOGIC.md`](RECORDING_AND_LOGIC.md).

A joining musician sees the Studio but does not control the host recorder.
Their live audio is captured as its own host-side track.

## More

The live session keeps secondary features in one **More** menu:

- **Live Session** — return to the band cards.
- **Session Notes** — capture notes and export a local brief.
- **Multitrack Studio** — record and review takes.
- **Add Video or Conversation** — optionally open a configured Webex link.
- **Talk Break** — appears only after conversation has been opened.
- **Settings** — change your displayed name or optional conversation link.
- **Troubleshooting** — open the detailed readiness report.

Webex is not part of the startup path. If used for conversation, keep the
Webex microphone muted while playing so its delayed audio does not duplicate
the low-latency music path.

## End the session

The host clicks **End Session**; a joined musician clicks **Leave Jam**. The
host action ends the jam for everyone, while Leave Jam disconnects only that
Mac. WebJam keeps **Ending…** or **Leaving…** on screen until work is complete.
For a host, it stops and saves an active take first, then stops the client, the
hosted server, and the macOS sleep assertion. Quitting the application uses the
same role-aware confirmation and owned-process cleanup.

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
access. Open **More → Troubleshooting** only if the meter still does
not move; it provides the technical detail without placing it in startup.

### Microphone access is off

Choose **Open System Settings**, enable WebJam under Privacy & Security →
Microphone, return to WebJam, and choose **Try Again**. Reinstalling Jamulus or
editing a port will not fix a macOS permission denial.

### Invite opens the wrong session

Opening a new invite while WebJam is running asks once before safely ending
the current jam and switching to the new one.

### App will not open

Control-click the app and choose **Open**. If macOS says the downloaded test
app is damaged, clear quarantine in Terminal:

```bash
xattr -dr com.apple.quarantine /path/to/WebJam.app
```

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
| `F2` | Open troubleshooting |
| `Ctrl+1` / `Ctrl+2` / `Ctrl+3` | Live / Notes / Studio |
| `F11` / `Esc` | Enter / leave fullscreen |

For the two-Mac physical acceptance run, use
[`SUNDAY_TWO_MAC_PILOT.md`](SUNDAY_TWO_MAC_PILOT.md).
