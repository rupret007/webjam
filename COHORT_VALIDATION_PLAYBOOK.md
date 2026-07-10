# Cohort Validation Playbook (Qt Conductor)

Closed-pilot validation for the **Qt Conductor** (`webjam_qt_main.py`). Replace Tkinter-era menu steps with the controls below.

## Band admin: share template (copy-paste to musicians)

Send this to every musician before the session so setup happens off the clock:

```
We're jamming on WebJam. One-time setup (~10 min):

1. Download the build for your OS from:
   https://github.com/rupret007/webjam/releases/latest
   - Windows: WebJam-windows-x64.zip
   - Mac (M1/M2/M3/M4): WebJam-macos-arm64.zip
   - Mac (Intel): WebJam-macos-x64.zip

2. First launch shows a security warning (WebJam isn't code-signed yet):
   - macOS: right-click the app -> Open -> Open (once).
   - Windows: "More info" -> "Run anyway" (once).

3. Install the virtual audio cable (the designated bridge feeds music into Webex):
   - Windows: run VBCABLE_Setup_x64.exe from WebJam's VB/ folder.
   - macOS: install BlackHole from https://existential.audio/blackhole
   Jamulus itself is already bundled — nothing to install on macOS; on
   Windows click "Install Jamulus now" in the wizard if prompted.
   Detection confirms the cable exists; it does not configure device routing.

4. In the Setup Wizard, enter:
   - Jamulus server host: <FILL IN>
   - Jamulus server port: 22124
   - Webex link: <FILL IN>

Questions? Reply here. Full walkthrough: README_SIMPLE.md
```

## Pre-session (each musician)

1. Downloadable WebJam builds bundle Jamulus (macOS: zero-install; Windows: use
   the Setup Wizard's "Install Jamulus now" button). Only install it yourself
   from [jamulus.io](https://jamulus.io) for source/unbundled builds.
2. Launch WebJam; complete Setup Wizard if prompted.
3. Press **F2** (Ready Check) — resolve any red items (virtual cable, Jamulus path, server).
4. Optional: **Ctrl+P** Practice — confirm your meter moves when you play.

## Two-person Jamulus smoke

1. Both musicians: **Launch Audio** — status should show **Connecting** then **Connected** with participant count.
2. Confirm fader/mute changes in WebJam affect heard levels.
3. One musician: **Stop Audio** — demo grid returns; other musician still connected on server.

## Video smoke

1. On the designated Mac bridge, use a physical-output + BlackHole
   Multi-Output Device for Jamulus, BlackHole as Webex microphone, and the
   physical device as Webex speaker.
2. Other musicians keep Webex microphone/speaker muted and use Jamulus for audio.
3. **Join Video** — embedded Webex loads or browser fallback opens; confirm
   the Mac carries the band mix without echo.
4. **Leave Video** — embed clears; status returns to not joined.

## Record button (band server)

1. Follow `server/README.md` one-time setup. The same-Mac pilot uses recorder
   RPC 22240 directly; a remote Linux server uses an SSH tunnel.
2. With audio connected, toggle **Record** — chip shows armed/recording state from server notifications.

## Post-session

1. **Ctrl+Shift+D** — paste diagnostics into pilot feedback channel (no secrets in export).
2. Note any crashes in `~/.webjam.log` and `~/.webjam_jamulus.log`.

## Pilot gate

Do not widen the pilot until: clean-machine install, Ctrl+P real-audio, two-person Jamulus, Record, take retrieval, and Take Deck playback all pass on target hardware.
