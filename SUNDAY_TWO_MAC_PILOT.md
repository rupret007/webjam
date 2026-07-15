# WebJam v0.15.0 two-Mac pilot worksheet

**Status: NOT RUN.** This worksheet is the only record that can turn a real
two-Mac, audio-interface, interruption, recording, recovery, or optional
external-editor observation into a pass. Source tests, screenshots, package
checks, and an earlier build cannot pre-check a box.

WebJam Studio intentionally borrows the useful, familiar parts of a multitrack
editor—transport, a seconds ruler, track headers, mute, solo, gain, pan, and a
focused inspector. It is a standalone WebJam feature. It does **not** launch,
control, read, write, or integrate with Logic Pro (or any other editor).

The musician path should stay simple:

> Host or Join → Confirm sound → Band Check → Play → Record → Review → Export Tracks → End

## 1. Evidence header

Fill the hardware and human-observation fields before opening either app. Do
not copy an identity from the v0.14.0 rollback package. The verified v0.15.0
artifact record below must be the package used on both Macs.

- Installed app version: `0.15.0` (verify on each Mac)
- Artifact filename: `WebJam-v0.15.0-TEST-NIGHT-macos-arm64.zip`
- Artifact absolute path: `/Users/jeffstory/Documents/WebJam 2/WebJam-v0.15.0-TEST-NIGHT-macos-arm64.zip`
- SHA-256: `58ff7a6071d319a11119547028f454b579fd149912d17dfc0fc20ef3cef10152`
- Source/build commit: `30ece85eb6a555dbcb2ef35753e4c6c9e8679770`
- Package-gate result/reference: PASS — ad-hoc signed/not notarized; fresh extraction, strict/deep signatures, nested Jamulus 3.12.2 apps, arm64 fabric/build ID, and two isolated launch/cleanup cycles passed. Not an audio pass.
- Test date/time/timezone: _____________________________________________
- Host Mac model / macOS: ______________________________________________
- Bandmate Mac model / macOS: __________________________________________
- Host interface / driver / connection: _________________________________
- Bandmate interface / driver / connection: _____________________________
- Headphones used on both Macs: YES / NO
- LAN/router and connection type: ______________________________________
- External editor/version (optional manual-import check): _______________

Both Macs must use the same artifact hash. If the filename, hash, version, or
source record differs, stop and replace the app before testing.

> **Release boundary:** The package-only gate above is complete. This worksheet
> remains **NOT RUN** until real musicians complete the physical CoreAudio,
> two-Mac audibility, recording/recovery, interruption, and manual
> external-editor-import rows below.

## What this test does and does not prove

- Jamulus carries the live music. WebJam’s local meter and isolated recorder
  use a separate CoreAudio/PortAudio stream, so a moving meter is not proof of
  the Jamulus route or of what the other musician heard.
- **Band input** and **Band output & review** are route-preflight evidence, not
  audibility. Headphones and both musicians’ ears are the audibility evidence.
- Webex is optional for faces/conversation. Keep its microphone muted while
  playing so it cannot add a delayed music copy.
- A v2 invite is a private session credential on a trusted same-LAN IPv4
  rehearsal. Do not post it, include it in evidence, use VPN/Internet routing,
  or expose the peer service through a router.
- The v3 profile remains a loopback developer lab, not an Internet/public
  hosting feature. It is not part of this two-Mac musician pass.

## 2. Before launch

- [ ] Both Macs are on the same private IPv4 LAN; guest isolation and VPN are
      off for this test.
- [ ] Both musicians use wired headphones, not room speakers.
- [ ] Each interface is set to 48 kHz where its control panel allows it.
- [ ] The exact v0.15.0 candidate is extracted and `WebJam.app` is installed.
- [ ] First launch succeeds using the private-build Gatekeeper instructions. A
      “damaged” or “incomplete” app is a packaging failure, not a workaround.
- [ ] The app uses black, white, neutral gray, and burnt orange; it has no
      purple or teal. The original three-part WebJam symbol replaces `WJ`.
- [ ] At 760×600, text, Band Check, participant cards, Studio, and essential
      actions are reachable without horizontal clipping.

Notes/evidence: _________________________________________________________

## 3. Record the actual audio routes

Write down the device choices visible in macOS and in WebJam. A meter or a
configured device is not proof that Jamulus used the intended route; the two
musicians must confirm that by ear.

| Mac | macOS input | macOS output | Band Check input | Recording input | Heard by bandmate? |
| --- | --- | --- | --- | --- | --- |
| Host | __________ | __________ | __________ | __________ | YES / NO |
| Bandmate | __________ | __________ | __________ | __________ | YES / NO |

Expected local-original setting:

- Host: ON / OFF; input 1 = ______________; input 2 = ______________
- Bandmate: ON / OFF; input 1 = ___________; input 2 = ______________

## 4. Host, invite, and join

### Host

- [ ] Choose **Host a Jam** once, confirm name and band sound, complete Band
      Check, then choose **Start Session**.
- [ ] Band Check identifies missing, ambiguous, or non-48-kHz routes rather
      than silently substituting a device.
- [ ] The input meter moves when the host plays, clipping is described in
      words, left/right headphones are heard, and the five-second recording is
      played back before **That sounds right** is selected.
- [ ] **Copy Invite** appears only after the hosted service is actually ready.
- [ ] The invite is sent privately. Do not place the full `webjam://` link in
      this worksheet, screenshots, notes, or support material.

Host Band Check result/details: _________________________________________

### Bandmate

- [ ] Open the invite from a cold start, or use **Join a Jam** and paste the
      complete link into its one field.
- [ ] Confirm name and band sound, complete Band Check, then choose **Start
      Session**.
- [ ] Leave and rejoin once. The host stays alive and neither Mac creates a
      duplicate participant or recording identity.
- [ ] If the host reports **Automatic Local Originals are off**, mark guest
      local-original capture/delivery **NOT AVAILABLE**; do not count a server
      track as a guest original.

Bandmate Band Check result/details: _____________________________________
Join/rejoin timing and messages: ________________________________________

## 5. Bidirectional acoustic proof

Meters are supporting evidence only. The other musician must say what they
hear.

- [ ] Host plays alone for 10 seconds; bandmate hears the intended source with
      no delayed duplicate, sustained crackle, echo, or clipping.
- [ ] Bandmate plays alone for 10 seconds; host hears the intended source with
      no delayed duplicate, sustained crackle, echo, or clipping.
- [ ] Both play together for at least one minute and call the result playable.
- [ ] Each musician changes a remote fader, **Mute Monitor**, and Solo; only
      that musician’s monitor mix changes.

- Host heard bandmate clearly: YES / NO
- Bandmate heard host clearly: YES / NO
- Playable together: YES / NO
- Dropout/echo/noise notes: ____________________________________________

If either musician answers NO, the acoustic gate fails. Preserve evidence
before changing a device, cable, or network.

## 6. Record through one interruption

This section proves local preservation and truthful recovery. It requires the
private v2 peer service for guest-original delivery. If that service is off,
run the reconnect/audibility observations but mark guest local-original capture
and delivery **NOT AVAILABLE**.

- [ ] On both Macs, open **More → Multitrack Studio → Recording Setup** and
      choose the wired Studio playback output.
- [ ] If testing local originals, explicitly enable interface inputs 1 and 2
      and choose the intended shareable two-channel 48-kHz input.
- [ ] Confirm the selected Takes folder is writable and has enough free space.
      Record any warning or block verbatim; do not deliberately exhaust a drive
      during this musician run.
- [ ] Start one host take and wait until recording is confirmed.
- [ ] Play an identifiable phrase on each source and say “before outage.”
- [ ] Turn Wi-Fi off on the bandmate Mac for 10–15 seconds while they continue
      playing a steady identifiable pattern.
- [ ] WebJam shows an interruption/reconnect state instead of stale readiness.
- [ ] Restore Wi-Fi, confirm the same participant identity returns, say “after
      reconnect,” then stop the take and wait for validation/transfer to settle.
- [ ] If an app stops unexpectedly, preserve the recovered local folder. It
      must remain **NEEDS ATTENTION** until a person reviews its checkpoint and
      gap evidence; it is not a completed, aligned, or delivered take.

- Host free storage before take: ________________________________________
- Storage-preflight message/warning: ___________________________________
- Interruption start/end time: __________________________________________
- Reconnect/transfer message: __________________________________________

## 7. Studio review

All rows in this section are physical observations and start **NOT RUN**.

- [ ] The completed take appears once and opens without changing source files.
- [ ] Expected Jamulus server tracks are present for both musicians. Every
      opted-in local original is present or explicitly marked missing, partial,
      failed, or transferred.
- [ ] The outage is shown as a truthful gap/segment/transfer finding; later
      audio is not pulled earlier to hide missing time.
- [ ] The shared Studio ruler shows elapsed seconds only—no invented bars,
      beats, tempo map, or automation grid.
- [ ] Seek, transport, and playable lanes land on the same audible elapsed
      point. Unavailable media remains visibly unavailable.
- [ ] Selecting a lane shows its source, media/alignment evidence, recorded
      gaps, and next-export inclusion.
- [ ] At 760×600, track identity, mute, solo, level, gain, pan, transport, and
      export controls remain usable; detail may collapse rather than clip.
- [ ] Play, pause, stop, scrub/seek, gain, pan, mute, and multi-solo work and
      playback reaches both chosen headphone channels.
- [ ] Change a harmless review/export choice, close Studio, and reopen the
      take. The choice follows the same durable source identity in
      `.webjam-studio-state.json`, not a neighboring display position.
- [ ] `webjam-take.json` and a source WAV have unchanged hashes/bytes before
      and after review. Only the Studio sidecar may change.

| Track name | Source type | Rate/channels | Status | Expected musician/input heard? |
| --- | --- | --- | --- | --- |
| __________ | __________ | __________ | __________ | YES / NO |
| __________ | __________ | __________ | __________ | YES / NO |
| __________ | __________ | __________ | __________ | YES / NO |
| __________ | __________ | __________ | __________ | YES / NO |

Take ID/folder: _________________________________________________________
Studio result: PASS / FAIL / **NOT RUN**
Manifest hash before/after: _____________________________________________
Source WAV hash before/after: ___________________________________________
Studio sidecar/reopen notes: ____________________________________________

## 8. Export Tracks and optional external-editor import

**Export Tracks** creates a portable WebJam package. It has no editor
integration and makes no claim that another app was opened or accepted it.

- [ ] Export the completed take. A selected explicitly silent performance
      track, uncertain required media, or selected local original without
      verified timeline alignment blocks a false timing-ready export and says
      what must be reviewed.
- [ ] Review each export checkbox. Deselecting a track changes only this
      export, never the recorded take. Record any intentionally omitted track.
- [ ] Close/reopen Studio after excluding a reviewed source. Its durable
      identity—not lane position—remains excluded for the next export.
- [ ] The package contains equal-length numbered PCM24 stems, server and Studio
      reference mixes, `MARKERS.csv`, recording/alignment reports, source
      manifest, audio analysis, SHA checksums, and generic import instructions.
- [ ] Check the SHA file before any manual import. A mismatch is a failure.

Optional manual import in an external multitrack editor (not an integration):

- [ ] Create an empty project at the package sample rate.
- [ ] Drag all numbered performance stems together at `0:00`, one per track.
      Do not use either reference mix as an additional performance stem.
- [ ] Confirm names/order, duration, musician/input identity, outage placement,
      marker information, and audible alignment.
- [ ] Save the editor project without changing the WebJam source take.

- Track Export folder: _________________________________________________
- Export project rate / frames: ________________________________________
- Manual external-editor import result: PASS / FAIL / **NOT RUN**
- Alignment/identity notes: ____________________________________________
- Durable-ID export-selection notes: ___________________________________

If no external editor was actually opened, leave that optional result **NOT
RUN**. A successful Track Export by itself is still a useful WebJam result.

## 9. Support evidence and cleanup

- [ ] Open the support preview before choosing a save location.
- [ ] Confirm it contains bounded technical facts but no audio, notes,
      transcript, meeting link, invitation token, secret, home path, or
      arbitrary personal file.
- [ ] Save the bundle and record its path/hash below.
- [ ] Press **Stop Rec** and wait for **Take saved** before **End Session**.
      End Session must be blocked while a take is recording or validating.
- [ ] Guest **Leave Jam** finalizes an active opted-in original, persists its
      resumable queue, and attempts a final upload. If the host is unreachable,
      media and queue remain on the guest for manual review.
- [ ] Quit both apps. No WebJam-owned Jamulus client, JamulusServer, recorder,
      transfer worker, or `caffeinate` process remains.
- [ ] Relaunch both apps. A new host starts without a port-in-use error and an
      old invite cannot silently resume the old private recording session.

- Support bundle path: _________________________________________________
- Support bundle SHA-256: ______________________________________________
- Cleanup/process evidence: ____________________________________________

## Pass decision

The physical gate passes only when both musicians hear playable two-way audio,
the actual routes are recorded, interruption/recovery is represented
truthfully, expected originals are present or disclosed, Studio survives
reopen without altering source evidence, Track Export is correct, and cleanup
leaves no owned processes. Manual import is an optional separately recorded
check, not a WebJam integration requirement.

- Overall result: PASS / FAIL / **NOT RUN**
- Blocking defect: _____________________________________________________
- Failed section: ______________________________________________________
- Take and support bundle preserved before retry: YES / NO
- Tester names/sign-off: _______________________________________________

On failure, change one variable at a time. Keep the original take, local
originals, Track Export, and support bundle until the defect is understood.
