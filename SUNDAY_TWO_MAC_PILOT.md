# Sunday two-Mac pilot checklist

This is the private v0.8.1 acceptance gate. Both musicians use Apple Silicon
Macs, wired headphones, Ethernet, and 48 kHz audio. Jamulus carries music;
native Webex carries muted-by-default speech talkback. Do not tag or publish a
release before this checklist passes.

Read [WEBEX_AUDIO_MODES.md](WEBEX_AUDIO_MODES.md) before configuring devices.
For this pilot choose **Musician with talkback** on both Macs. BlackHole and
`WebJam Bridge` are not in the live signal path.

## Host Mac: SSL 2+, server, and talkback

1. Connect guitar to SSL input 1 in instrument mode and the dynamic vocal mic
   to input 2 in microphone mode. Keep 48 V phantom power off.
2. In Jamulus select SSL 2+ for input and output, **Mono-in/Stereo-out**, and
   the intended input mapping. Start at the 128-sample Default buffer; try the
   64-sample Preferred buffer only after the stable path passes.
3. In Webex select SSL 2+ as speaker. Prefer a separate webcam, USB, or headset
   mic for talkback. The SSL mic is an acceptable fallback only if Webex stays
   muted while playing and no instrument is played while Space is held.
4. Join Webex muted. Choose macOS **Standard** Mic Mode and Webex **Optimize for
   My Voice**. Do not use Music Mode for speech talkback.
5. Install the official Jamulus 3.12.2 `JamulusServer.app`, start it from the
   repository root with `server/start_macos_pilot.sh`, and verify:

   ```bash
   lsof -nP -iUDP:22124
   lsof -nP -iTCP:22240 -sTCP:LISTEN
   ```

   UDP must listen on 22124. Recorder RPC must listen only on
   `127.0.0.1:22240`. Stop with Ctrl+C; never force-kill a recording server.
6. Configure WebJam for `127.0.0.1:22124`, client RPC 22222, recorder RPC
   22240, and the secret/take paths printed in `server/README.md`.
7. Enable supplemental local recording only if Ready Check proves the chosen
   **Meter and local recording input** can open at 48 kHz alongside Jamulus.

## Drummer Mac: Roland TD-27 and talkback

1. Install Roland TD-27 driver 1.0.2 for macOS Sonoma 14 or later.
2. Set **SYSTEM → USB AUDIO → Driver Mode → VENDOR**, then restart the module
   and Mac as Roland directs.
3. Confirm macOS exposes TD-27 audio input and output—not MIDI alone—and set
   both to 48 kHz.
4. In Jamulus choose TD-27 input/output, master stereo channels, and
   **Mono-in/Stereo-out**. Start at the 128-sample Default buffer.
5. In Webex use the Mac microphone or a dedicated headset/USB mic for speech
   and TD-27 as speaker if it returns computer audio to wired headphones.
6. Join Webex muted with Standard Mic Mode and Optimize for My Voice. Hold
   Space only for conversation. BlackHole is not required.

## Network and solo gates

1. Invite the drummer's Mac to Tailscale. `tailscale ping` must report a direct
   path, not DERP.
2. Reserve the host LAN address and forward router **UDP 22124 only**. Never
   forward TCP 22222 or 22240. Keep the VPN disconnected.
3. Test the public route from a genuinely external network.
4. Compare Tailscale and direct UDP for ten minutes each. Use the route with no
   recurring crackle/dropout and the lower stable Jamulus delay.
5. On each Mac, complete Setup in talkback mode, run F2 Ready Check, manually
   confirm every Webex `VERIFY` row, run Ctrl+P Practice, and prove meters,
   headphones, Talk Break, Resume Music, and clean shutdown.
6. In a one-person rehearsal, verify Webex push-to-talk from a phone or second
   device using headphones. Instruments must not leak into Webex while playing.

## Sunday acceptance test

1. Start the server and verify UDP 22124 plus loopback-only TCP 22240.
2. Connect both Macs; require real participant cards within about ten seconds.
3. Confirm guitar, vocal, and drums through Jamulus without clipping or a lost
   channel. Confirm each musician hears their own Jamulus server return.
4. Test per-listener faders, mute, solo, Talk Break, Resume Music, mix
   save/restore, and chat. Talk Break must mute Jamulus only, never Webex.
5. Join native Webex on both Macs. Keep both Webex microphones muted during ten
   minutes of playing; require no delayed duplicate music or echo.
6. Each musician holds Space and speaks between takes. Speech must be clear in
   Webex. Release Space before selecting Resume Music.
7. Briefly interrupt the drummer's network and verify Jamulus and Webex recover
   independently without stale participant state.
8. Exercise structured notes, Session Pulse, notes export, and brief export.
9. Record at least 60 seconds through recorder RPC 22240. Require the expected
   track count, duration, 48 kHz rate, and no missing/unreadable track. If local
   capture is enabled, require its stems and manifest evidence independently of
   the selected Webex mode.
10. Play the take in Take Deck and import the WAVs into Logic. Confirm duration,
    alignment, and audibility; the optional `.rpp` file belongs to Reaper.
11. Continue for 45–60 minutes, collect redacted diagnostics, review logs, stop
    cleanly, and check for orphan Jamulus processes.

A network route passes only when it has no persistent crackle/dropout for ten
minutes and both musicians consider it playable. Prefer delay below 30 ms;
recurring dropouts or roughly 45 ms-plus delay blocks that configuration.

If TD-27 USB audio fails after the official driver and VENDOR mode, use analog
master outputs through a separate interface. If speech or music appears in both
Jamulus and Webex, mute Webex first and return to the documented talkback path.
Never redesign routing during a recording.
