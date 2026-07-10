# Sunday two-Mac pilot checklist

This is the release gate for the private v0.8.1 candidate. Both musicians use
Apple Silicon Macs, wired headphones, Ethernet, and 48 kHz audio. Do not tag or
publish a release before this checklist passes.

## Before Sunday

### Host Mac: SSL 2+, server, and Webex bridge

1. Install BlackHole 2ch and restart macOS.
2. In Audio MIDI Setup, confirm the SSL 2+ and BlackHole are both at 48 kHz.
3. Create a Multi-Output Device named **WebJam Bridge** with SSL 2+ and
   BlackHole. Use SSL as clock source and enable drift correction on BlackHole.
4. Connect guitar to SSL input 1 in instrument mode and the dynamic vocal mic
   to input 2 in microphone mode. Keep 48 V phantom power off.
5. In Jamulus select the combined sound card
   **in: SSL 2+/out: WebJam Bridge** and **Mono-in/Stereo-out**. Map input 1
   left and input 2 right. Start at the 128-sample **Default** buffer; test the
   64-sample **Preferred** buffer only after the stable path passes.
6. In Webex select BlackHole 2ch as microphone and SSL 2+ as speaker. Never
   select the built-in microphone for this topology.
7. Install the official Jamulus 3.12.2 `JamulusServer.app`, then start it from
   the repository root with `server/start_macos_pilot.sh`. In a second Terminal
   verify:

   ```bash
   lsof -nP -iUDP:22124
   lsof -nP -iTCP:22240 -sTCP:LISTEN
   ```

   UDP must listen on 22124. Recorder RPC must listen only on
   `127.0.0.1:22240`. Stop with Ctrl+C once; do not force-kill it.
8. Configure WebJam for `127.0.0.1:22124`, local client RPC 22222, recorder RPC
   22240, and the secret/take paths printed in `server/README.md`.

### Drummer Mac: Roland TD-27

1. On a candidate containing the role-aware Ready Check, leave **This Mac sends
   the Jamulus mix into Webex** disabled; BlackHole is then optional. The older
   `1d3a3d3` artifact still requires BlackHole to pass its legacy Ready Check.
2. Install Roland TD-27 driver 1.0.2 for macOS Sonoma 14 or later from Roland's
   official support page.
3. On the module set **SYSTEM → USB AUDIO → Driver Mode → VENDOR**, then restart
   the module and Mac as Roland directs.
4. Confirm macOS exposes TD-27 audio input and output—not MIDI alone—and set
   both to 48 kHz.
5. In Jamulus use the combined TD-27 input/output sound card, its master stereo
   channels, and **Mono-in/Stereo-out** for this first pilot. Start at the
   128-sample **Default** buffer; test 64-sample **Preferred** only after the
   stable path passes.
6. Use wired headphones and Ethernet. During playing, join Webex for video with
   Webex microphone and speaker muted; all rehearsal audio stays in Jamulus.

### Network and solo gates

1. Invite the drummer's Mac to the host's Tailscale network. `tailscale ping`
   must report a direct path, not DERP.
2. Reserve the host Mac's LAN address and forward router **UDP 22124 only**.
   Never forward TCP 22222 or 22240. Keep the VPN disconnected.
3. Test the public route from a genuinely external network.
4. Compare Tailscale and direct UDP for ten minutes each. Use the route with no
   recurring crackle/dropout and the lower stable Jamulus delay.
5. On each Mac independently, complete first-run Setup, pass every F2 Ready
   Check item, run Ctrl+P Practice, verify real meters/headphones/mute, then
   confirm WebJam and Jamulus shut down cleanly.
6. Join the Webex room from a phone or second device with headphones. Confirm
   the host's Jamulus mix reaches Webex without feedback.

## Sunday acceptance test

1. Start the server and verify UDP 22124 plus loopback-only TCP 22240.
2. Connect both Macs; require real participant cards within about ten seconds.
3. Confirm guitar, vocal, and drums are audible without clipping or lost input.
4. Test per-listener faders, mute, solo, Mute Me, mix save/restore, and chat.
5. Measure ten minutes on Tailscale and direct UDP; record delay and dropouts.
6. Join Webex on both Macs. Only the host bridges audio; confirm no delayed echo.
7. Briefly interrupt the drummer's network and verify reconnect and restored
   participant state.
8. Exercise structured notes, Session Pulse, notes export, and brief export.
9. Record at least 60 seconds through RPC 22240. Confirm WebJam reports the
   expected track count, duration, 48 kHz rate, and no missing/unreadable track.
   Confirm one non-empty WAV per
   participant; the host stem intentionally combines guitar and vocal.
10. Play the take in Take Deck and import both WAVs into Logic. Confirm duration,
    alignment, and audibility; the `.rpp` file is optional and belongs to Reaper.
11. Continue for 45–60 minutes, then collect redacted diagnostics, review logs,
    stop cleanly, and check for orphan Jamulus processes.

A route passes only when it has no persistent crackle/dropout for ten minutes
and both musicians consider it playable. Prefer network delay below 30 ms;
recurring dropouts or delay above roughly 45 ms blocks that configuration.

If TD-27 USB audio fails after the official driver and VENDOR mode, use its
analog master outputs through a separate interface. If BlackHole/Webex feeds
back, mute Webex audio and continue the Jamulus test. Never change the live
path during a recording.
