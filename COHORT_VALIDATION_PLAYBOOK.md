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

3. Use wired interface headphones and a dedicated webcam/USB/headset speech
   mic when possible. BlackHole/VB-CABLE is not required for musician talkback.
   Jamulus itself is bundled; on Windows click "Install Jamulus now" if prompted.

4. In the Setup Wizard, enter:
   - Jamulus server host: <FILL IN>
   - Jamulus server port: 22124
   - Webex link: <FILL IN>

   Designated macOS host only: install JamulusServer.app 3.12.2 and select
   "This Mac hosts the band server" instead of entering an external host.

Questions? Reply here. Full walkthrough: README_SIMPLE.md
```

## Pre-session (each musician)

1. Downloadable WebJam builds bundle Jamulus (macOS: zero-install; Windows: use
   the Setup Wizard's "Install Jamulus now" button). Only install it yourself
   from [jamulus.io](https://jamulus.io) for source/unbundled builds.
2. Launch WebJam; complete Setup Wizard if prompted.
3. Choose **Musician with talkback**, then press **F2**. Resolve automated
   failures and manually VERIFY the native Webex device/mute settings.
4. Optional: **Ctrl+P** Practice — confirm your meter moves when you play.

## Two-person Jamulus smoke

1. Designated host: **Host & Start Audio** and require `Server: Hosting :22124`.
   Other musicians: **Start Audio**. Status should show **Connecting** then
   **Connected** with participant count.
2. Confirm fader/mute changes in WebJam affect heard levels.
3. Host musician: **Stop Audio** — the lobby returns but `Server: Hosting`
   remains and the other musician stays connected. Rejoin with Start Audio.

## Video smoke

1. Both musicians set Webex speaker to their wired interface and microphone
   to the intended speech mic. Enable mute-on-join and Optimize for My Voice.
2. **Open Webex** — confirm WebJam reports only `Opened externally`.
3. With Webex muted, play through Jamulus and require no delayed duplicate music.
4. Use **Talk Break**, hold Spacebar in Webex, and confirm one clear speech path.
5. Release Spacebar, confirm Webex is muted, then **Resume Music**.

## Record button (band server)

1. Follow `server/README.md` one-time setup. The same-Mac host uses in-app
   hosting and recorder RPC 22240 directly; a remote Linux server uses an SSH
   tunnel.
2. With audio connected, toggle **Record** — chip shows armed/recording state from server notifications.

## Post-session

1. **Ctrl+Shift+D** — paste diagnostics into pilot feedback channel (no secrets in export).
2. Note any crashes in `~/.webjam.log` and `~/.webjam_jamulus.log`.

## Pilot gate

Do not widen the pilot until: clean-machine install, Ctrl+P real-audio, two-person Jamulus, Record, take retrieval, and Take Deck playback all pass on target hardware.
