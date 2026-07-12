# WebJam — Quick Start for Your Band

WebJam coordinates your band's **music (Jamulus)**, **speech/video (native Webex)**, recording, and shared notes so rehearsals start with one clear workflow.

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

1. **Jamulus** — the live-audio engine. WebJam's downloadable builds bundle it: on **macOS** it's zero-install (nothing to do — WebJam finds its own bundled copy automatically); on **Windows** first-run setup offers **Install Jamulus** when needed. Only grab it yourself from [jamulus.io](https://jamulus.io) if you're building/running from source.
2. **Native Webex**, preferably with a dedicated webcam/USB/headset speech mic.
   The existing music interface is an acceptable push-to-talk fallback when
   nobody is playing. Both applications send output to wired interface headphones.
3. **WebJam** itself. BlackHole/VB-CABLE is needed only for the optional
   advanced audience-broadcast role, not ordinary musician talkback.

---

## Band admin: set this up once and share it

One person (the "band admin") decides two things and shares them with everyone:

1. **A Jamulus server** — for the two-Mac pilot, install the official dedicated
   `JamulusServer.app` 3.12.2 on one Mac, select **This Mac hosts the band
   server** in Setup, and let WebJam run it. Alternatives are a separately
   managed server or a public server from
   [explorer.jamulus.io](https://explorer.jamulus.io). Share the host Mac's
   Tailscale/public address and port `22124` with remote musicians; the host
   itself always connects to `127.0.0.1`.
2. **A Webex meeting link** — create a meeting and share the URL (e.g. `https://yourco.webex.com/meet/yourband`).

Send the band the **Jamulus server host + port** and the **Webex link**. That's everything a member needs for the setup wizard.

---

## First launch (each member)

The first time you open WebJam, two focused steps appear:

1. **Choose your setup** — select **Host the band** or **Join a band**, then
   enter your musician name. A host uses the included server automatically;
   a joining musician enters the single address shared by the host.
2. **Webex and recording** — paste the meeting link and optionally select an
   isolated local recording input.

Click **Finish Setup**. WebJam opens the lobby and runs **Ready Check**
automatically. Ports, executable paths, alternative Webex modes, recording
folders, and public-server options remain in **Settings** (`Ctrl+,`).

### Echo-safe Webex talkback for the pilot

Both musicians choose **Musician with talkback**. Jamulus carries music;
native Webex carries speech. Set Webex speaker to the wired interface, select
the intended speech mic, enable **Mute me when I join**, use Standard macOS
Mic Mode plus **Optimize for My Voice**, and hold Space only while speaking.
Keep Webex muted while playing. BlackHole and `WebJam Bridge` are not in this
signal path. See [WEBEX_AUDIO_MODES.md](WEBEX_AUDIO_MODES.md).

---

## In a session

The Conductor keeps the live actions at the top:

1. **Start Audio** (gold) — starts Jamulus and connects to your band's server.
   On the designated host this reads **Host & Start Audio** and starts/verifies
   the server first. Each member appears as a card as they join. **Stop Audio**
   disconnects the host musician but deliberately leaves the band server up.
2. **Open Webex** (teal) — opens the meeting externally; finish joining there.
3. Adjust each player's **fader / mute / solo** on their card to build *your own* monitor mix — it only changes what you hear, not what others hear.
4. Use **Talk Break** between takes. It mutes only your Jamulus send; hold
   Spacebar in Webex to speak. Release Spacebar before **Resume Music**.

Use **Checks ▾** for Ready Check or Practice. The left rail contains only the
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
| `F2` / `Checks → Ready Check` | Is my setup ready to jam? |
| `Ctrl+S` / `Ctrl+O` | Save / load your mixer state (default slot) |
| `Ctrl+Shift+S` / `Ctrl+Shift+O` | Save Mix As… / Load a named mix file |
| `Ctrl+M` | Mute / unmute **all** |
| `Ctrl+Shift+M` | Start **Talk Break** / resume the Jamulus send; Webex is never changed |
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

1. The host selects **Host & Start Audio**; everyone else selects **Start
   Audio**. Confirm the status bar reads `Server: Hosting :22124` on the host
   and that both musicians see each other's cards and hear Jamulus.
2. Both: **Open Webex**, join muted, and verify push-to-talk speech reaches the other interface headphones.
3. Play with both Webex mics muted and confirm there is no delayed duplicate music.
4. Set and **save** your monitor mix (`Ctrl+S`).

---

## Troubleshooting

- **"Jamulus Not Found" when I click Start Audio** — WebJam can't find (or wasn't shipped with) a Jamulus install. Reopen Settings → Jamulus: on Windows click **"Install Jamulus now"** if it's offered, otherwise install from [jamulus.io](https://jamulus.io) and set the path.
- **No Webex talkback** — confirm Webex uses the intended speech mic and the wired interface as speaker, then test temporary unmute by holding Spacebar.
- **Delayed duplicate music** — mute Webex immediately. Webex is receiving an instrument/music input; select a dedicated speech mic and keep it muted while playing.
- **"No audio routing device found" in setup** — this applies only to advanced audience-bridge mode; talkback does not require a virtual device.
- **Can't connect to the server** — double-check the host and port with your band admin, and that everyone is pointed at the *same* server.
- **"Band Server Could Not Start" on the host** — install the dedicated
  `JamulusServer.app` 3.12.2, keep UDP 22124 and loopback TCP 22240 free, then
  retry. If TCP 22240 belongs to a manual server, WebJam adopts it only when
  the configured secret authenticates its recorder. See `server/README.md` and
  `~/Library/Logs/WebJam/jamulus-server.log`.
- **Something's off and you want help** — press `Ctrl+Shift+D` to copy a diagnostics summary, or grab the log files: `~/.webjam.log` (WebJam) and `~/.webjam_jamulus.log` (Jamulus output).

---

## For tinkerers

WebJam exposes an optional read-only **Companion API** on `http://127.0.0.1:8765` (participants, session state) for DAWs/editors/scripts — see `COMPANION_API.md`. It is off by default; enable it only if you need an external tool to read session state.

Questions or bugs: [github.com/rupret007/webjam](https://github.com/rupret007/webjam).
