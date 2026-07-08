# Cohort Validation Playbook (Qt Conductor)

Closed-pilot validation for the **Qt Conductor** (`webjam_qt_main.py`). Replace Tkinter-era menu steps with the controls below.

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

1. **Join Video** — embedded Webex loads or browser fallback opens.
2. **Leave Video** — embed clears; status returns to not joined.

## Record button (band server)

1. Follow `server/README.md` one-time setup (SSH tunnel + `jsonrpc.secret`).
2. With audio connected, toggle **Record** — chip shows armed/recording state from server notifications.

## Post-session

1. **Ctrl+Shift+D** — paste diagnostics into pilot feedback channel (no secrets in export).
2. Note any crashes in `~/.webjam.log` and `~/.webjam_jamulus.log`.

## Pilot gate

Do not widen the pilot until: clean-machine install, Ctrl+P real-audio, two-person Jamulus, Record, take retrieval, and Take Deck playback all pass on target hardware.
