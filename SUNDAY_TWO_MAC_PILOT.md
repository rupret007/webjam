# Tonight's two-Mac pilot

This is the physical acceptance gate for the private macOS v0.9.0 test build.
The goal is deliberately small:

> Host → Copy Invite → open or paste → Play → Record → End Session

Do not publish or call the build release-ready until the checks below have
passed with the exact ZIP being handed to the musicians.

## Pilot boundary

- Use two Apple Silicon Macs on the same home or studio network. For the
  simplest test, put both on the same Wi-Fi. This build does not provide
  internet, VPN, NAT, or router traversal.
- Use wired headphones on both Macs. Do not monitor through speakers.
- Before opening WebJam, select the intended audio interface as the macOS Sound
  input and output. The host can use the SSL 2+; the drummer can use the TD-27
  in VENDOR USB-audio mode at 48 kHz. If TD-27 USB audio is unreliable, use its
  analog master outputs through a known-good interface.
- The test ZIP is ad-hoc signed, not notarized. Extract it, then Control-click
  **WebJam.app** → **Open** on first launch. If macOS still blocks it, use
  System Settings → Privacy & Security → Open Anyway. A “damaged” or
  “incomplete” warning is a packaging failure, not an expected warning.
- Webex or another conversation app is optional and stays outside the core
  music test. If used, keep its microphone muted while playing so it cannot
  create delayed duplicate music.

Record the exact ZIP filename and SHA-256 here:

- Artifact: `WebJam-v0.9.0-TEST-NIGHT-macos-arm64.zip`
- SHA-256: `9d8476167191c99ad311fcb2a33bd3b9aa1e4aacd9ce8043ddd3386864dc49e5`
- Host Mac / macOS: ____________________
- Drummer Mac / macOS: ____________________

## 1. Clean launch

- [ ] Delete or replace every older **WebJam.app** on both Macs, then copy the
  v0.9.0 app into `/Applications`. Do not leave a second older copy in
  Downloads; macOS could otherwise send an invitation to the wrong app.
- [ ] Launch the new app once from `/Applications` and confirm the live window
  title reports **v0.9.0** before testing invitation links.
- [ ] Open WebJam on each Mac. The first screen shows only **Host a Jam** and
  **Join a Jam** as primary choices.
- [ ] The interface is black, white, and restrained burnt orange. Purple, teal,
  red danger styling, neon glow, and competing launch controls are absent.
- [ ] There is no setup wizard, Ready Check, server-address field, port field,
  executable picker, or visible second music-client window.
- [ ] If macOS asks for microphone access, WebJam first explains why, then
  macOS asks. Allow it. On one Mac, optionally prove the denied path: WebJam
  shows **Microphone access is off**, opens the correct System Settings pane,
  and offers **Try Again** after returning.
- [ ] If macOS asks whether the host may accept incoming network connections,
  click **Allow**. Turn off VPN software for this same-network pilot.
- [ ] Resize the launch window and live session down to 760×600. Text, cards,
  status, and Copy Invite / Record / More / End-or-Leave remain usable without
  horizontal clipping.

Evidence or failure notes: ________________________________________________

## 2. Host and invite

- [ ] On the host, click **Host a Jam** once. Do not configure anything else.
- [ ] The HUD moves from **Starting your jam** to **Ready to share**. It must
  not show an invitation before the hosted service is alive.
- [ ] Click **Copy Invite**. The clipboard contains one `webjam://join?...`
  link. It contains the session name and network destination, but no password,
  secret, filesystem path, or musician-private data.
- [ ] Send that complete link to the drummer without editing it.

Evidence or failure notes: ________________________________________________

## 3. Join and play

Test both supported invitation paths, one at a time:

- [ ] With WebJam closed on the drummer Mac, open the invitation link. WebJam
  launches and joins that jam.
- [ ] Click **Leave Jam** on the drummer Mac, confirm the host is still running,
  reopen WebJam, choose **Join a Jam**, paste the same link into the single
  field, and click **Join Jam**.
- [ ] Within about 30 seconds both named participant cards appear. The host HUD
  reports the bandmate connection and the drummer HUD reports the joined state.
- [ ] Play guitar, vocal, and drums in turn. Both musicians hear the intended
  music return without sustained crackle, clipping, echo, or a delayed copy.
- [ ] Adjust a bandmate fader on one Mac and confirm it changes only that
  listener's monitor mix.
- [ ] Use **Mute Monitor** on the local card and confirm it changes only what
  that musician hears; it must not imply or cause outgoing transmit mute.

Meter truth matters:

- [ ] A local meter moves only when WebJam observes input on that Mac.
- [ ] A remote meter moves only when WebJam observes band audio for that
  participant; local input is never copied onto remote cards as a placeholder.
- [ ] Treat a moving meter as observed signal, not proof that the other
  musician heard it. Confirm audibility with the other musician out loud.

Evidence or failure notes: ________________________________________________

## 4. Studio two-track take

- [ ] On the host, open **More → Multitrack Studio**. Both musicians
  have distinct lanes.
- [ ] Click **Record**, play together for at least 60 seconds, then stop. Wait
  for validation to finish before ending the session or quitting.
- [ ] The completed take appears in the Studio library and plays in WebJam.
  Select SSL 2+ as the output, scrub the waveform, and exercise gain, pan,
  mute, and solo on both lanes.
- [ ] Reveal the take and confirm there are two isolated, readable, non-empty
  musician WAVs—one for each participant—and a take manifest. Record the take
  folder name: ____________________
- [ ] Press **Export for Logic** and require numbered 24-bit stems of identical
  length, `WebJam Rough Mix.wav`, import instructions, and the export manifest.
- [ ] Import every numbered stem together at `0:00` in a new 48 kHz Logic
  project. Confirm duration, alignment, and the expected musician on each
  track. Do not import the rough mix as another stem. The optional `.rpp` file
  is for Reaper and is not a Logic project. Follow
  [`RECORDING_AND_LOGIC.md`](RECORDING_AND_LOGIC.md).

Evidence or failure notes: ________________________________________________

## 5. Reconnect

- [ ] While both are connected and not recording, disable Wi-Fi on the drummer
  Mac for 5–10 seconds, then restore it.
- [ ] WebJam shows reconnection truthfully—no stale “ready” claim—and restores
  both participant cards and audible music without relaunching the host.
- [ ] If the attempt reaches the 30-second limit, it stops spinning and offers
  one **Try Again** action with same-network guidance. Restore the network,
  click it once, and confirm recovery.

Evidence or failure notes: ________________________________________________

## 6. End Session and cleanup

- [ ] If recording, let WebJam stop, save, and validate the take first.
- [ ] On the drummer Mac, click **Leave Jam** and confirm the dialog says only
  this Mac will disconnect. **Leaving…** remains until cleanup finishes; the
  host remains ready and can copy the same invite again.
- [ ] Rejoin the drummer before the host-ending check.
- [ ] Click **End Session** on the host and confirm the warning that the jam
  ends for everyone.
- [ ] **Ending…** remains visible until recording/client/server cleanup really
  completes. A failure must not be replaced by an ended success message.
- [ ] The host returns to an ended state; the joined Mac no longer pretends it
  is connected.
- [ ] Quit both apps. Activity Monitor shows no WebJam-owned background music
  client, dedicated server, or `caffeinate` process left behind.
- [ ] Reopen WebJam. It again starts at the two-choice Host/Join screen, and a
  new host session can be created without a port-in-use error.

Evidence or failure notes: ________________________________________________

## Pass decision

The pilot passes only if both musicians consider the music playable, both
invitation paths work, readiness/meters remain honest, the two isolated tracks
survive a Logic import, reconnect recovers (automatically or through the one
retry), and End Session leaves no owned processes behind.

- Result: PASS / FAIL
- Blocking defect: _______________________________________________________
- Logs/take copied before retrying: YES / NO

If anything fails, preserve the take and use **More →
Troubleshooting** before changing devices, network topology, or app settings.
Change one variable at a time and rerun the failed section.
