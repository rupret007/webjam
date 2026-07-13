# WebJam v0.10.0 two-Mac certification worksheet

**Status: NOT RUN.** Complete this worksheet with the exact fresh candidate.
Do not pre-check boxes from automated tests or from the preserved v0.9.0 build.

The goal is deliberately simple:

> Band Check → Host → Invite → Join → Play → Record → Review → Export → End

## Evidence header

Fill this in before opening either app. Do not invent or copy an old hash.

- Candidate version: `0.10.0`
- Artifact filename: _________________________________________________
- Artifact absolute path: ____________________________________________
- SHA-256: ___________________________________________________________
- Source commit: ____________________________________________________
- Test date/time/timezone: ___________________________________________
- Host Mac model / macOS: ___________________________________________
- Bandmate Mac model / macOS: _______________________________________
- Host interface / driver / connection: ______________________________
- Bandmate interface / driver / connection: __________________________
- Headphones used on both Macs: YES / NO
- LAN/router and connection type: ____________________________________
- Logic Pro version (when tested): ___________________________________

Both Macs must use the same artifact hash. If either filename/hash differs,
stop and replace it before testing.

## Know what this test proves

- Jamulus carries the live music. WebJam starts it, but WebJam's local input
  meter and isolated recorder open a **separate PortAudio/Core Audio stream**.
  A passing meter does not prove Jamulus chose the same device. Your ears and
  the resulting track inventory must prove the real route.
- Webex is optional for video/conversation. Keep its microphone muted while
  playing so it does not add a delayed copy of the music.
- Guest isolated-original delivery uses authenticated plain HTTP on the same
  private RFC1918 IPv4 LAN. It has no TLS, IPv6, Internet, VPN, NAT-traversal,
  or public-network claim. Keep VPNs off and do not expose the peer port.
- A v2 invite contains a random private enrollment credential. Send the full
  link only to the intended bandmate. Do not post it publicly or include it in
  screenshots/support notes. Ending/restarting the host peer session rotates
  the credential.
- Use wired headphones, not speakers. Set both interfaces to 48 kHz where the
  hardware control panel allows it.

## 1. Fresh install and launch

- [ ] Remove or rename every older WebJam copy on both Macs.
- [ ] Extract the exact candidate and put **WebJam.app** in `/Applications`.
- [ ] Verify the app reports **v0.10.0** on both Macs.
- [ ] First launch succeeds using the private-build Gatekeeper instructions.
  A “damaged” or “incomplete” app warning is a packaging failure.
- [ ] The app uses black, white, neutral gray, and burnt orange. There is no
  purple or teal, and the original three-part WebJam symbol replaces `WJ`.
- [ ] Resize to 760×600. Text, Band Check, participant cards, and essential
  controls remain reachable without horizontal clipping.

Notes/evidence: __________________________________________________________

## 2. Record the actual audio routes

Before trusting any meter, write down the choices visible in macOS and the
interface itself.

| Mac | macOS input | macOS output | Band Check input | Recording input | Jamulus route confirmed by ears? |
| --- | --- | --- | --- | --- | --- |
| Host | __________ | __________ | __________ | __________ | YES / NO |
| Bandmate | __________ | __________ | __________ | __________ | YES / NO |

- [ ] On both Macs, open Studio → **Recording Setup**.
- [ ] Choose the wired Studio playback output.
- [ ] If local originals are part of this test, enable **Keep interface inputs
  1 and 2 as isolated local originals** on both Macs and choose the intended
  two-channel input. This is explicit opt-in and requires a shareable 48-kHz
  two-channel device.
- [ ] If an interface has only one meaningful source, note which of input 1 or
  input 2 is expected to be silent; do not mislabel the silent lane.

Expected local-original setting:

- Host: ON / OFF; input 1 = ______________; input 2 = ______________
- Bandmate: ON / OFF; input 1 = ___________; input 2 = ______________

## 3. Band Check on each Mac

Run Band Check separately before joining. Do not let one person's confirmation
stand in for the other Mac.

- [ ] Music client/server checks finish without an unexplained blocker.
- [ ] The selected local input meter moves when that musician plays and rests
  near silence when they stop.
- [ ] Deliberately play too loudly once; clipping is identified in words, then
  reduce the interface gain and pass cleanly.
- [ ] Left/right output checks are heard in the named headphone side.
- [ ] The five-second recording plays back through the intended headphones.
- [ ] The musician explicitly chooses **That sounds right** only after hearing
  their own recording.
- [ ] Final result is recorded below. A warning is copied verbatim and not
  treated as an unexplained pass.

- Host Band Check result/details: ______________________________________
- Bandmate Band Check result/details: _________________________________

## 4. Host and private invite

- [ ] On the host, choose **Host a Jam** once.
- [ ] Wait until WebJam says the jam is ready before copying the invite.
- [ ] **Copy Invite** produces one complete `webjam://join?...` link.
- [ ] Do not paste the link into this worksheet. Confirm only that it begins
  with `webjam://join?` and send it privately to the bandmate.
- [ ] On the bandmate Mac, open the link from a cold start.
- [ ] Leave, confirm the host remains alive, then paste the same link through
  **Join a Jam** and rejoin.
- [ ] Both participant names appear once. Renaming/reconnecting does not create
  a duplicate musician card or duplicate recording identity.

Join time and any message shown: _________________________________________

## 5. Bidirectional acoustic proof

Meters are supporting evidence only. The other musician must confirm what
they actually hear.

- [ ] Host plays alone for 10 seconds; bandmate hears the intended source with
  no delayed copy, sustained crackle, echo, or clipping.
- [ ] Bandmate plays alone for 10 seconds; host hears the intended source with
  no delayed copy, sustained crackle, echo, or clipping.
- [ ] Both play together for at least one minute and call the result playable.
- [ ] Each musician changes the remote fader and hears only their own monitor
  mix change.
- [ ] **Mute Monitor**, solo, gain, and pan do not claim to mute outgoing audio
  or change the other musician's personal mix.
- [ ] A local meter never appears as fake remote activity. A remote meter is
  not treated as proof of audibility.

- Host heard bandmate clearly: YES / NO
- Bandmate heard host clearly: YES / NO
- Playable together: YES / NO
- Dropout/echo/noise notes: _____________________________________________

If either musician answers NO, the acoustic gate fails. Preserve evidence
before changing a device, cable, or network.

## 6. Record through one interruption

This section proves local preservation, reconnect truth, and later delivery.

- [ ] Start one take on the host. Wait until recording is confirmed.
- [ ] Play a short identifiable phrase on each source and say “before outage.”
- [ ] While recording, turn Wi-Fi off on the bandmate Mac for 10–15 seconds.
- [ ] WebJam shows an interruption/reconnect state instead of stale readiness.
- [ ] Keep playing a steady identifiable pattern on the bandmate interface
  during the outage so its opted-in local original can be checked later.
- [ ] Restore Wi-Fi. The same participant identity returns without duplicating
  the musician.
- [ ] Say “after reconnect,” play another phrase, then stop the take on the
  host and wait for validation and transfer to settle.
- [ ] If transfer is interrupted, retry/resume without deleting the bandmate's
  original. Repeating the action does not create duplicate attached media.

- Interruption start/end time: _________________________________________
- Reconnect time/message: ______________________________________________
- Transfer status/message: _____________________________________________

## 7. Studio and source-truth review

- [ ] The take appears once in Studio and opens without changing source files.
- [ ] Expected Jamulus server tracks are present for both musicians.
- [ ] For each Mac where local originals were enabled, its input 1/input 2
  stems are present or explicitly marked missing/partial/failed. Nothing
  silently disappears while the take says complete.
- [ ] The outage appears as a truthful gap/segment/transfer finding where
  applicable; later audio is not pulled earlier to hide missing time.
- [ ] Waveforms span the full take and show the outage/reconnect placement.
- [ ] Play, pause, stop, scrub/seek, gain, pan, mute, and multi-solo work.
- [ ] Playback reaches both headphone channels through the chosen output.
- [ ] Closing/leaving Studio stops playback and releases that output.
- [ ] Reopen the take and repeat a seek/play check.

Track inventory (one row per displayed track):

| Track name | Source type | Rate/channels | Status | Expected musician/input heard? |
| --- | --- | --- | --- | --- |
| __________ | __________ | __________ | __________ | YES / NO |
| __________ | __________ | __________ | __________ | YES / NO |
| __________ | __________ | __________ | __________ | YES / NO |
| __________ | __________ | __________ | __________ | YES / NO |

Take ID/folder: __________________________________________________________

## 8. Export and Logic Pro

- [ ] Export the completed take. An uncertain/missing required source blocks
  a false Logic-ready package and explains what must be fixed.
- [ ] A successful package contains numbered PCM24 stems of equal project
  length, `WebJam Server Reference.wav`, `WebJam Studio Reference.wav`,
  `MARKERS.csv`, alignment/recording reports, source manifest, independent
  audio analysis, SHA checksums, and import instructions that name the tempo
  and time signature.
- [ ] Check the SHA file before importing. Record any mismatch as a failure.
- [ ] Create an empty Logic project at the sample rate named in the package.
- [ ] Drag all numbered stems together at `0:00`, one per track. Do not drag
  either reference mix in as another performance stem.
- [ ] Confirm names/order, duration, musician/input identity, outage placement,
  marker/tempo information, and audible alignment.
- [ ] Compare against `WebJam Server Reference.wav`, understanding that it is an
  offline unity mix of post-network Jamulus server tracks, not an independent
  acoustic/live-output recording.
- [ ] Save the Logic project without changing the WebJam source take.

- Logic export folder: _________________________________________________
- Export project rate / frames: ________________________________________
- Logic import result: PASS / FAIL / **NOT RUN**
- Alignment/identity notes: ____________________________________________

If Logic was not actually opened, leave the result **NOT RUN**.

## 9. Support evidence and cleanup

- [ ] Open the support preview before choosing a save location.
- [ ] Confirm it contains bounded technical facts but no audio, notes,
  transcript, meeting link, invitation token, secret, home path, or arbitrary
  personal file.
- [ ] Save the bundle and record its path/hash below.
- [ ] End the host session only after recording/transfer is settled.
- [ ] Leave/end messages remain visible until real cleanup finishes.
- [ ] Quit both apps. No WebJam-owned Jamulus client, JamulusServer, recorder,
  transfer worker, or `caffeinate` process remains.
- [ ] Relaunch both apps. A new host starts without a port-in-use error and an
  old invite cannot silently resume the old private recording session.

- Support bundle path: _________________________________________________
- Support bundle SHA-256: ______________________________________________
- Cleanup/process evidence: ____________________________________________

## Pass decision

The physical gate passes only when both musicians hear playable two-way audio,
the actual routes are recorded, the outage is represented truthfully, every
expected original is present or explicitly disclosed, Studio survives reopen,
the exact export imports correctly into Logic, and cleanup leaves no owned
processes. Automated tests cannot fill these fields.

- Overall result: PASS / FAIL / **NOT RUN**
- Blocking defect: ____________________________________________________
- Failed section: _____________________________________________________
- Take and support bundle preserved before retry: YES / NO
- Tester names/sign-off: ______________________________________________

On failure, change one variable at a time. Keep the original take, local
originals, Logic export, and support bundle until the defect is understood.
