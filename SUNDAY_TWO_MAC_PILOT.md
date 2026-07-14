# WebJam v0.11.0 two-Mac certification worksheet

**Status: NOT RUN.** Complete this worksheet with the exact fresh candidate.
Do not pre-check boxes from automated tests or from the preserved v0.10.0 rollback build.

The goal is deliberately simple:

> Host choice → Band Check → Invite → Join choice → Band Check → Play → Record → Review → Export → End

## Evidence header

Fill this in before opening either app. Do not invent or copy an old hash.

- Candidate version: `0.11.0`
- Artifact filename: `WebJam-v0.11.0-TEST-NIGHT-macos-arm64.zip`
- Artifact absolute path: `/Users/jeffstory/Documents/WebJam 2/WebJam-v0.11.0-TEST-NIGHT-macos-arm64.zip`
- SHA-256: `11bc573a28c9804163d34deb5fbf3779dd6aaa2338f3a25e6e70819776b41e4f`
- Source commit: `1a03927e3ea8eb76557617aa59e985a551c35e0b`
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
  upload quota/rate limiting, or public-network claim. Use only the intended
  trusted bandmate, keep VPNs off, and do not expose the peer port.
- A v2 invite contains a random private enrollment credential. Send the full
  link only to the intended bandmate. Do not post it publicly or include it in
  screenshots/support notes. It is reusable for that host-peer session, not a
  one-use token; anyone holding it on the LAN can enroll. Ending/restarting the
  host peer session rotates the credential.
- Use wired headphones, not speakers. Set both interfaces to 48 kHz where the
  hardware control panel allows it.

## 1. Fresh install and launch

- [ ] Remove or rename every older WebJam copy on both Macs.
- [ ] Extract the exact candidate and put **WebJam.app** in `/Applications`.
- [ ] Verify the app reports **v0.11.0** on both Macs.
- [ ] First launch succeeds using the private-build Gatekeeper instructions.
  A “damaged” or “incomplete” app warning is a packaging failure.
- [ ] The app uses black, white, neutral gray, and burnt orange. There is no
  purple or teal, and the original three-part WebJam symbol replaces `WJ`.
- [ ] Resize to 760×600. Text, Band Check, participant cards, and essential
  controls remain reachable without horizontal clipping.

Notes/evidence: __________________________________________________________

## 2. Record the actual audio routes

Before trusting any meter, write down the choices visible in macOS and the
interface itself. Fill the Band Check and Recording input columns later when
those controls appear.

| Mac | macOS input | macOS output | Band Check input | Recording input | Jamulus route confirmed by ears? |
| --- | --- | --- | --- | --- | --- |
| Host | __________ | __________ | __________ | __________ | YES / NO |
| Bandmate | __________ | __________ | __________ | __________ | YES / NO |

Expected local-original setting:

- Host: ON / OFF; input 1 = ______________; input 2 = ______________
- Bandmate: ON / OFF; input 1 = ___________; input 2 = ______________

## 3. Host Band Check

On the host, choose **Host a Jam** once. If its stored verification is missing
or changed, WebJam opens Band Check before starting the session.

- [ ] Host music-engine and server checks finish without an unexplained blocker.
- [ ] The host's selected local input meter moves when the host plays and rests
  near silence when they stop.
- [ ] Deliberately play too loudly once; clipping is identified in words, then
  reduce the interface gain and pass cleanly.
- [ ] Left/right output checks are heard in the named headphone side.
- [ ] The five-second recording plays back through the intended headphones.
- [ ] The host chooses **That sounds right** only after hearing the recording.
- [ ] Record the result below. Copy any warning verbatim; do not treat it as an
  unexplained pass.
- [ ] Choose **Start Session** after Band Check.

Host Band Check result/details: _________________________________________

## 4. Private invite and bandmate Band Check

- [ ] Wait until WebJam says the host jam is ready before copying the invite.
- [ ] **Copy Invite** produces one complete `webjam://join?...` link.
- [ ] Do not paste the link into this worksheet. Confirm only that it begins
  with `webjam://join?` and send it privately to the bandmate.
- [ ] If the host shows **Automatic Local Originals are off**, record that the
  v1 fallback can join/play and receives a host-side server track, but WebJam
  guest local-original capture and delivery are unavailable.
- [ ] On the bandmate Mac, open the link from a cold start. Confirm WebJam fills
  and accepts the connection before the readiness/start step; opening the link
  alone does not count as joined.
- [ ] If WebJam asks, complete Band Check on that Mac before joining: verify its
  music engine, input/clipping, left/right headphones, five-second recording,
  and **That sounds right** confirmation. Then choose **Start Session**.
- [ ] Record the bandmate result below; do not reuse the host's confirmation.
- [ ] Leave, confirm the host remains alive, then paste the same link through
  **Join a Jam**, complete any required Band Check, choose **Start Session**,
  and rejoin.
- [ ] Both participant names appear once. Renaming/reconnecting does not create
  a duplicate musician card or duplicate recording identity.

Bandmate Band Check result/details: _____________________________________

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
It requires the v2 peer service. If the host showed **Automatic Local Originals
are off**, run the reconnect/audibility observations but mark WebJam guest
local-original capture and delivery **NOT AVAILABLE** and do not pass this
section.

- [ ] On both connected Macs, open Studio → **Recording Setup** and choose the
  wired Studio playback output.
- [ ] If local originals are part of this test, enable **Keep interface inputs
  1 and 2 as isolated local originals** on both Macs and choose the intended
  shareable two-channel 48-kHz input.
- [ ] If an interface has only one meaningful source, record which input is
  expected to be silent; do not mislabel that lane as another source.
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
- [ ] Press **Stop Rec** and wait for **Take saved** before **End Session**.
  Confirm End Session is blocked while the take is recording or validating.
- [ ] When the guest chooses **Leave Jam**, confirm WebJam finalizes any active
  opted-in guest original, persists its resumable queue, and attempts a final
  upload. If the host is unreachable, the media and queue remain on the guest.
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
