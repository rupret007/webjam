# WebJam — Quick Start for Your Band

WebJam puts your band's **live audio (Jamulus)** and **video call (Webex)** in one window, with a shared notes canvas — so rehearsals start in seconds instead of juggling apps.

This guide gets your whole band connected for the first time. It's written for the **Conductor** app (the main WebJam). Launch it with:

```
python webjam_qt_main.py
```

(or open the downloaded **WebJam** app if you're using a release build).

---

## Downloading WebJam (and getting past the security warnings)

Grab the newest release from **[github.com/rupret007/webjam/releases](https://github.com/rupret007/webjam/releases)**:

- **Windows:** `WebJam-windows-x64.zip`
- **Mac (Apple Silicon — M1/M2/M3/M4):** `WebJam-macos-arm64.zip`
- **Mac (Intel, 2020 or earlier):** `WebJam-macos-x64.zip`

Unzip it and run the **WebJam** app inside. **The first launch will trigger a security warning — this is expected.** WebJam isn't code-signed yet (that requires a paid developer certificate), so your OS flags it like any app from an "unidentified developer." It's the same file the build robot published on GitHub; here's how to open it:

- **macOS:** don't double-click the first time. **Right-click (or Ctrl-click) the app → Open → Open.** You only have to do this once. If macOS says the app "is damaged," clear the download quarantine flag instead: open Terminal and run `xattr -dr com.apple.quarantine /path/to/WebJam.app`
- **Windows:** when SmartScreen shows "Windows protected your PC," click **More info → Run anyway.** Once, then it remembers.

---

## What each person needs (once)

Every band member installs WebJam and uses its bundled Jamulus client:

1. **Jamulus** — the live-audio engine. WebJam's downloadable builds bundle it: on **macOS** it's zero-install (nothing to do — WebJam finds its own bundled copy automatically); on **Windows** the Setup Wizard's Jamulus page has an "Install Jamulus now" button that runs the bundled installer for you. Only grab it yourself from [jamulus.io](https://jamulus.io) if you're building/running from source.
2. **A virtual audio cable, only on the designated Webex bridge Mac** — this
   feeds the full Jamulus mix into the video call.
   - **Windows:** VB-CABLE — from [vb-audio.com](https://vb-audio.com), or use the installer bundled in WebJam's `VB/` folder (`VBCABLE_Setup_x64.exe`).
   - **macOS:** BlackHole — free at [existential.audio/blackhole](https://existential.audio/blackhole).

   WebJam detects this during setup, but detection is not routing. Before the
   session, configure Jamulus and Webex as described below.
   Other musicians use Jamulus for audio and keep Webex microphone/speaker
   muted while playing; they do not need BlackHole merely to pass Ready Check.
3. **WebJam** itself, plus a Webex account/app for the video side.

> **Why the virtual cable?** Jamulus carries the low-latency music between players. The virtual cable feeds that combined sound into Webex as a "microphone," so anyone on the video call hears the band play. Without it, Webex only hears your computer's regular mic.

---

## Band admin: set this up once and share it

One person (the "band admin") decides two things and shares them with everyone:

1. **A Jamulus server** — either run your own (see jamulus.io) or pick a free public one from [explorer.jamulus.io](https://explorer.jamulus.io). Share the **server host** (e.g. `myband.example.com` or an IP) and **port** (usually `22124`).
2. **A Webex meeting link** — create a meeting and share the URL (e.g. `https://yourco.webex.com/meet/yourband`).

Send the band the **Jamulus server host + port** and the **Webex link**. That's everything a member needs for the setup wizard.

---

## First launch (each member)

The first time you open WebJam, a short setup wizard runs:

1. **Welcome** — a quick overview; downloadable builds bundle Jamulus, so most people can just continue.
2. **Jamulus Server** — enter the **host** and **port** your band admin shared. Leave **Local Jamulus control port** at `22222`; WebJam assigns it to the client it launches so participant names and mixer controls work. It is not the band's server or recorder-control port. The Jamulus executable path is usually pre-filled (macOS: the bundled copy; Windows: click **"Install Jamulus now"** if it's blank and let the installer finish). If you need to point at a different install, browse to it — macOS: `/Applications/Jamulus.app/Contents/MacOS/Jamulus`, Windows: `C:\Program Files\Jamulus\Jamulus.exe`.
3. **Webex Meeting** — paste the meeting link.
4. **Audio Routing** — select whether this Mac is the Webex audio bridge. The
   bridge Mac must have BlackHole/VB-CABLE; a Jamulus-only musician does not.
   Detection confirms availability but does not configure routing.
5. **Configuration saved** — click Finish. WebJam opens the Conductor and runs **Ready Check**; fix anything it flags before your first jam.

You can rerun this any time from **Settings** (`Ctrl+,`).

### Echo-safe Webex routing for the pilot

Use one designated Mac as the only Webex audio bridge. In **Audio MIDI
Setup**, create a Multi-Output Device containing the physical
headphones/interface and BlackHole. Set Jamulus output to that Multi-Output
Device, Webex microphone to BlackHole, and Webex speaker to the physical
output. Never select the Mac's real microphone in Webex during the jam.

Other musicians keep all rehearsal audio in Jamulus. They may join Webex for
video, but mute Webex microphone and speaker unless their audio-interface
loopback has been separately verified. This prevents Webex from feeding a
delayed copy of the band back into Jamulus.

---

## In a session

The Conductor keeps the live actions at the top:

1. **Launch Audio** (gold) — starts Jamulus and connects to your band's server. Each member appears as a card as they join.
2. **Join Video** (teal) — opens your Webex meeting.
3. Adjust each player's **fader / mute / solo** on their card to build *your own* monitor mix — it only changes what you hear, not what others hear.
4. Click the same buttons again to **stop audio** / **leave video**.

Use **Test ▾** for Ready Check or Practice. The left rail contains only the
working Live, Canvas, Takes, and Settings views.

When the host stops recording, WebJam verifies the newly created tracks and
shows track count, duration, sample rate, and any missing/silent-track warning.
**Takes** opens Take Deck, where you can choose the physical playback output,
review both headphone channels, and reveal the take in Finder.

**Save your mix** so you don't rebuild it every time: `Ctrl+S` saves, `Ctrl+O` restores. WebJam also auto-restores your last mix when the band reconnects.

---

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `Ctrl+P` | **Practice solo pilot check** — private local server, hear yourself, no internet; include this in the real-hardware gate. |
| `● Record` button | **Pilot/server-admin feature** — band-server multitrack recording after `server/README.md` setup; validate on real hardware before relying on it for a session. |
| **Takes** (side rail) | **Pilot validation** — play back copied/downloaded take folders; include take retrieval + Take Deck playback in the real-hardware gate. |
| `F2` / `Test → Ready Check` | Is my setup ready to jam? |
| `Ctrl+S` / `Ctrl+O` | Save / load your mixer state (default slot) |
| `Ctrl+Shift+S` / `Ctrl+Shift+O` | Save Mix As… / Load a named mix file |
| `Ctrl+M` | Mute / unmute **all** |
| `Ctrl+Shift+M` | Mute / unmute **yourself** (in both Jamulus *and* Webex) |
| `Ctrl+Shift+R` | Reset all faders to 0 dB |
| `Ctrl+T` | Insert a timestamp in the notes canvas |
| `Ctrl+,` | Open Settings |
| `Ctrl+Shift+D` | Copy diagnostics to clipboard (for support) |
| `F1` | Help |
| `F11` / `Esc` | Fullscreen on / off |
| Double-click a fader | Reset that channel to 0 dB |

> **On macOS**, the mute shortcuts use the literal **Control** key (shown as `⌃M` / `⌃⇧M`), not Cmd — so they don't clash with Cmd+M (minimize).

---

## Planning the band's first session?

Follow **[FIRST_JAM.md](FIRST_JAM.md)** — a staged runbook (solo smoke test → two-person test → full band) with a failure playbook.

## Before your first real jam — a 60-second check

Fastest confidence builder: press **Ctrl+P** (Practice). WebJam starts a private Jamulus server *on your own computer* and connects to it — if you can hear yourself and see your meter move, your audio setup works, full stop. Then:

1. Everyone: **Launch Audio** and confirm you see each other's cards and hear each other in Jamulus.
2. One person: **Join Video** and confirm Webex hears the band (the virtual cable is working).
3. Set and **save** your monitor mix (`Ctrl+S`).

---

## Troubleshooting

- **"Jamulus Not Found" when I click Launch Audio** — WebJam can't find (or wasn't shipped with) a Jamulus install. Reopen Settings → Jamulus: on Windows click **"Install Jamulus now"** if it's offered, otherwise install from [jamulus.io](https://jamulus.io) and set the path.
- **No one can hear the band in Webex** — detection alone is not enough. On the designated bridge Mac, confirm Jamulus outputs to the physical-output + BlackHole Multi-Output Device and Webex uses BlackHole as its microphone.
- **"No audio routing device found" in setup** — install the virtual cable for your OS and relaunch.
- **Can't connect to the server** — double-check the host and port with your band admin, and that everyone is pointed at the *same* server.
- **Something's off and you want help** — press `Ctrl+Shift+D` to copy a diagnostics summary, or grab the log files: `~/.webjam.log` (WebJam) and `~/.webjam_jamulus.log` (Jamulus output).

---

## For tinkerers

WebJam exposes an optional read-only **Companion API** on `http://127.0.0.1:8765` (participants, session state) for DAWs/editors/scripts — see `COMPANION_API.md`. It is off by default; enable it only if you need an external tool to read session state.

Questions or bugs: [github.com/rupret007/webjam](https://github.com/rupret007/webjam).
