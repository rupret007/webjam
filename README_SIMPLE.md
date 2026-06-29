# WebJam — Quick Start for Your Band

WebJam puts your band's **live audio (Jamulus)** and **video call (Webex)** in one window, with a shared notes canvas — so rehearsals start in seconds instead of juggling apps.

This guide gets your whole band connected for the first time. It's written for the **Conductor** app (the main WebJam). Launch it with:

```
python webjam_qt_main.py
```

(or open the downloaded **WebJam** app if you're using a release build).

---

## What each person needs (once)

Every band member installs three things on their own computer:

1. **Jamulus** — the live-audio engine. Free at [jamulus.io](https://jamulus.io). WebJam launches and manages it for you, but doesn't bundle it, so install it first.
2. **A virtual audio cable** — this is how your playing gets *into* the Webex call.
   - **Windows:** VB-CABLE — from [vb-audio.com](https://vb-audio.com), or use the installer bundled in WebJam's `VB/` folder (`VBCABLE_Setup_x64.exe`).
   - **macOS:** BlackHole — free at [existential.audio/blackhole](https://existential.audio/blackhole).

   WebJam auto-detects this during setup; you don't have to configure it by hand.
3. **WebJam** itself, plus a Webex account/app for the video side.

> **Why the virtual cable?** Jamulus carries the low-latency music between players. The virtual cable feeds that combined sound into Webex as a "microphone," so anyone on the video call hears the band play. Without it, Webex only hears your computer's regular mic.

---

## Band admin: set this up once and share it

One person (the "band admin") decides two things and shares them with everyone:

1. **A Jamulus server** — either run your own (see jamulus.io) or pick a free public one from [directory.jamulus.io](https://directory.jamulus.io). Share the **server host** (e.g. `myband.example.com` or an IP) and **port** (usually `22124`).
2. **A Webex meeting link** — create a meeting and share the URL (e.g. `https://yourco.webex.com/meet/yourband`).

Send the band the **Jamulus server host + port** and the **Webex link**. That's everything a member needs for the setup wizard.

---

## First launch (each member)

The first time you open WebJam, a short setup wizard runs:

1. **Welcome** — confirms you've installed Jamulus.
2. **Jamulus Server** — enter the **host** and **port** your band admin shared. Leave "Server control port" at `22222` unless told otherwise (it lets WebJam show participant names and control the mixer). If Jamulus isn't auto-found, point it at the app — macOS: `/Applications/Jamulus.app/Contents/MacOS/Jamulus`, Windows: `C:\Program Files\Jamulus\Jamulus.exe`.
3. **Webex Meeting** — paste the meeting link.
4. **Audio Routing** — WebJam scans for your virtual cable. A green check means you're good. If it's not found, click **"Show me how to set this up,"** install the cable, and relaunch. You can also **Skip for now** and set it up later.
5. **You're all set** — click Finish.

You can rerun this any time from **Settings** (`Ctrl+,`).

---

## In a session

The Conductor window has two big buttons at the top:

1. **Launch Audio** (gold) — starts Jamulus and connects to your band's server. Each member appears as a card as they join.
2. **Join Video** (teal) — opens your Webex meeting.
3. Adjust each player's **fader / mute / solo** on their card to build *your own* monitor mix — it only changes what you hear, not what others hear.
4. Click the same buttons again to **stop audio** / **leave video**.

**Save your mix** so you don't rebuild it every time: `Ctrl+S` saves, `Ctrl+O` restores. WebJam also auto-restores your last mix when the band reconnects.

---

## Keyboard shortcuts

| Key | Action |
|-----|--------|
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

## Before your first real jam — a 60-second check

1. Everyone: **Launch Audio** and confirm you see each other's cards and hear each other in Jamulus.
2. One person: **Join Video** and confirm Webex hears the band (the virtual cable is working).
3. Set and **save** your monitor mix (`Ctrl+S`).

---

## Troubleshooting

- **"Jamulus Not Found" when I click Launch Audio** — Jamulus isn't installed, or WebJam can't find it. Install from [jamulus.io](https://jamulus.io), or set the path in Settings → Jamulus.
- **No one can hear the band in Webex** — the virtual audio cable isn't set as Webex's microphone, or isn't installed. Reinstall it (see above), relaunch WebJam, and in Webex pick the cable ("VB-Cable" / "BlackHole") as your mic.
- **"No audio routing device found" in setup** — install the virtual cable for your OS and relaunch.
- **Can't connect to the server** — double-check the host and port with your band admin, and that everyone is pointed at the *same* server.
- **Something's off and you want help** — press `Ctrl+Shift+D` to copy a diagnostics summary, or grab the log files: `~/.webjam.log` (WebJam) and `~/.webjam_jamulus.log` (Jamulus output).

---

## For tinkerers

WebJam exposes an optional read-only **Companion API** on `http://127.0.0.1:8765` (participants, session state) for DAWs/editors/scripts — see `COMPANION_API.md`. It starts automatically when WebJam launches.

Questions or bugs: [github.com/rupret007/webjam](https://github.com/rupret007/webjam).
