# WebJam v0.13.0 two-Mac certification worksheet

**Status: NOT RUN.** Complete this worksheet with the exact fresh candidate.
Do not pre-check boxes from automated tests or an earlier rollback build. This
worksheet is the only place to turn physical two-Mac audio, interruption, and
Logic observations into a pass.

The goal is deliberately simple:

> Host choice → Confirm sound → Band Check → Invite → Join choice → Confirm sound → Band Check → Play → Record → Review → Export → End

## Evidence header

Fill this in before opening either app. The source target is v0.13.0; do not
invent, copy, or reuse an earlier archive identity.

- Installed app version: ______________________________________________
- Artifact filename: ___________________________________________________
- Artifact absolute path: ______________________________________________
- SHA-256: _____________________________________________________________
- Source/build commit: _________________________________________________
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

> **Artifact boundary:** This exact ZIP must include the confirmation screen,
> CoreAudio route preflight, recording-storage guard, private in-progress
> evidence journal, periodic durable local-capture checkpoints, recovery
> reconciliation, and conservative Logic-export checks. Automated source/package
> checks establish only that these features are present; this worksheet is where
> the actual hardware, musician, interruption, crash-recovery, and Logic
> outcomes are recorded.
>
> **Studio v0.14 boundary:** The source-next Studio workspace adds a shared
> elapsed-seconds ruler, selected-track inspection, compact lanes, and a
> non-destructive per-take sidecar. It is not evidence about this recorded
> v0.13.0 ZIP. Leave every Studio v0.14 physical result **NOT RUN** until the
> exact newer package identity is written in this worksheet and musicians run
> the rows below.

## Know what this test proves

- Jamulus carries the live music. WebJam starts it, but WebJam's local input
  meter and isolated recorder open a **separate PortAudio/Core Audio stream**.
  A passing meter does not prove Jamulus chose the same device. Your ears and
  the resulting track inventory must prove the real route.
- **Band input** and **Band output & review** resolve their stable CoreAudio
  UIDs, reject an ambiguous or non-48-kHz pair, and stage a WebJam-owned
  Jamulus config before launch. That is configuration/preflight evidence, not
  audibility.
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
- The separate v3 path is lab-only and loopback-profiled; it is not an Internet
  or public-hosting feature for this pilot. If a v3 trial is explicitly added,
  the same invitation may be retried only when WebJam says the sidecar failed
  before enrollment began. Any later or uncertain failure must show **Fresh
  invitation required**; ask the host for a new link and do not fall back to a
  legacy/local connection.
- Use wired headphones, not speakers. Set both interfaces to 48 kHz where the
  hardware control panel allows it.

## 1. Fresh install and launch

- [ ] Remove or rename every older WebJam copy on both Macs.
- [ ] Extract the exact candidate and put **WebJam.app** in `/Applications`.
- [ ] Verify both Macs report the installed version recorded above.
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

## 3. Host confirmation and Band Check

On the host, choose **Host a Jam** once, confirm the name and band sound, then
complete Band Check. If the stored verification is still valid, WebJam may keep
the check short; do not skip an action that is shown.

- [ ] The confirmation screen appears after **Host a Jam** and records the
  intended name and band-sound choices before Band Check.
- [ ] If a route is changed, **Band input** and **Band output & review** name
  the intended CoreAudio devices; the app rejects an ambiguous/missing/non-48-
  kHz selection instead of silently substituting another device.
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

## 4. Private invite, bandmate confirmation, and Band Check

- [ ] Wait until WebJam says the host jam is ready before copying the invite.
- [ ] **Copy Invite** produces one complete `webjam://join?...` link.
- [ ] Do not paste the link into this worksheet. Confirm only that it begins
  with `webjam://join?` and send it privately to the bandmate.
- [ ] If the host shows **Automatic Local Originals are off**, record that the
  v1 fallback can join/play and receives a host-side server track, but WebJam
  guest local-original capture and delivery are unavailable.
- [ ] If an explicitly approved lab-only v3 invitation is tested, record the
  exact state: **Try Again** is acceptable only for a sidecar failure before
  guest enrollment begins. A later/uncertain failure must say **Fresh invitation
  required** and use a new host link; it must not silently join a legacy or
  localhost session. This v3 observation is optional and does not substitute
  for the same-LAN two-Mac test.
- [ ] On the bandmate Mac, open the link from a cold start. Confirm WebJam fills
  and accepts the connection before the readiness/start step; opening the link
  alone does not count as joined.
- [ ] Confirm the bandmate name and band sound after Join, then complete Band
  Check: verify its music engine, input/clipping, left/right headphones,
  five-second recording, and **That sounds right** confirmation. Then choose
  **Start Session**.
- [ ] Record the bandmate result below; do not reuse the host's confirmation.
- [ ] Leave, confirm the host remains alive, then paste the same link through
  **Join a Jam**, confirm sound, complete any required Band Check, choose
  **Start Session**, and rejoin.
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
- [ ] Confirm the selected Takes drive has enough free storage and record the
  amount below. WebJam must allow Record only after its writable-folder and
  roster-aware storage preflight passes; record any warning or block verbatim.
  Do not deliberately exhaust the drive merely to force a warning during this
  musician run.
- [ ] Start one take on the host. Wait until recording is confirmed.

- Host free storage before take: ___________________________________________
- Storage-preflight message/warning: _______________________________________
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
- [ ] If either app unexpectedly stops, relaunch before attempting another take.
  Preserve the recovered local folder/project, which must remain **NEEDS
  ATTENTION** until manually reviewed. Do not call it a completed take, an
  aligned source, or a delivered guest original merely because media files are
  present.

- Interruption start/end time: _________________________________________
- Reconnect time/message: ______________________________________________
- Transfer status/message: _____________________________________________

## 7. Studio and source-truth review

All checks in this section are physical observations. They start **NOT RUN**;
automated source tests, a screen render, or a previous package cannot pre-fill
them.

- [ ] The take appears once in Studio and opens without changing source files.
- [ ] Expected Jamulus server tracks are present for both musicians.
- [ ] For each Mac where local originals were enabled, its input 1/input 2
  stems are present or explicitly marked missing/partial/failed. Nothing
  silently disappears while the take says complete.
- [ ] The outage appears as a truthful gap/segment/transfer finding where
  applicable; later audio is not pulled earlier to hide missing time.
- [ ] Waveforms span the full take and show the outage/reconnect placement.
- [ ] The Studio ruler labels elapsed seconds only. It shares one duration with
  every displayed completed lane and does not pretend the take has bars, beats,
  a tempo map, or an automation grid.
- [ ] Click or drag to seek. The transport, ruler, and each playable lane land
  on the same audible elapsed point; unavailable media remains truthfully
  unavailable.
- [ ] Select each important lane. Its inspection shows the actual source,
  media/alignment evidence, recorded gaps, and next-export inclusion. Do not
  call a waveform or meter proof that the other musician heard it.
- [ ] At 760×600, the compact Studio layout keeps track identity, mute/solo,
  level, gain, pan, transport, and export controls reachable. A contextual
  panel may collapse rather than clip the core workspace.
- [ ] Play, pause, stop, scrub/seek, gain, pan, mute, and multi-solo work.
- [ ] Playback reaches both headphone channels through the chosen output.
- [ ] Closing/leaving Studio stops playback and releases that output.
- [ ] Change a harmless Studio review choice (for example gain, pan, mute, or
  one Logic-export inclusion), close Studio, and reopen the same take. The
  choice follows the same identified source through the hidden
  `.webjam-studio-state.json` sidecar; it does not follow a neighboring display
  position.
- [ ] Compare `webjam-take.json` and one source WAV before/after the Studio
  review. Their hashes/bytes are unchanged; only the Studio sidecar may change.
- [ ] Reopen the take and repeat a seek/play check.
- [ ] If crash recovery occurred, the recovered project remains visibly **NEEDS
  ATTENTION** with its checkpoint/gap evidence available for manual review.
  It is a recovery result, not proof of complete capture or automatic guest
  transfer.

Track inventory (one row per displayed track):

| Track name | Source type | Rate/channels | Status | Expected musician/input heard? |
| --- | --- | --- | --- | --- |
| __________ | __________ | __________ | __________ | YES / NO |
| __________ | __________ | __________ | __________ | YES / NO |
| __________ | __________ | __________ | __________ | YES / NO |
| __________ | __________ | __________ | __________ | YES / NO |

Take ID/folder: __________________________________________________________
Studio workspace result: PASS / FAIL / **NOT RUN**
Manifest hash before/after: ______________________________________________
Source WAV hash before/after: ____________________________________________
Studio sidecar/reopen notes: _____________________________________________

## 8. Export and Logic Pro

- [ ] Export the completed take. An uncertain/missing required source, selected
  explicitly silent performance track, or selected local original without
  verified timeline alignment blocks a false Logic-ready package and explains
  what must be fixed.
- [ ] If Studio offers **Logic export** track checkboxes, use them only after
  reviewing the affected source. Deselecting a track changes only this export,
  never the recorded take. Keep the Jamulus server track or align and verify a
  local original before calling the export timing-ready; disclose any expected
  track intentionally left out.
- [ ] For a schema-v2 take, close/reopen Studio after leaving one reviewed
  source out. Verify the durable identified source—not merely the lane in that
  display position—remains the excluded one in the next Logic handoff. Record
  the source identity below; this does not replace the later Logic import.
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
- Durable-ID export-selection notes: ___________________________________

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
  Crash-recovered guest media remains local for manual review; do not claim an
  automatic transfer or a completed shared take.
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
its sidecar leaves source evidence unchanged, the exact export imports
correctly into Logic, and cleanup leaves no owned processes. Automated tests
cannot fill these fields.

- Overall result: PASS / FAIL / **NOT RUN**
- Blocking defect: ____________________________________________________
- Failed section: _____________________________________________________
- Take and support bundle preserved before retry: YES / NO
- Tester names/sign-off: ______________________________________________

On failure, change one variable at a time. Keep the original take, local
originals, Logic export, and support bundle until the defect is understood.
