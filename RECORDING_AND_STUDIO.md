# Recording and Studio workflow

WebJam records a rehearsal as one shared **take**. JamulusServer contributes a
post-network track for each musician. A host—and a guest with an active v2
private invite—may explicitly keep interface inputs 1 and 2 as local originals.

WebJam is standalone. Studio intentionally uses familiar multitrack ideas—a
transport, track headers, a shared ruler, meters, mute/solo, gain, pan, and an
inspector—but it does not create projects for, launch, control, or depend on
any external editor.

**Physical status:** source and package checks do not prove physical input,
two-Mac audibility, interruption recovery, or import in another editor. Those
rows remain **NOT RUN** until the closed-pilot checklist records direct human
observations.

## What each source means

| Source | Truth it represents | Where it is kept |
|---|---|---|
| Band-server track | What that musician delivered through the network/server recorder | Host take |
| Local original | What the selected two-channel capture stream recorded on one Mac | Originating Mac; a verified copy can attach to the host take |
| Server reference | Offline unity mix of exported band-server tracks | Track Export |
| Studio reference | Offline rough mix using Studio's non-destructive review controls | Track Export |

A meter or local capture does not prove that a bandmate heard the route. Both
musicians must confirm that directly during Test Night.

## Record a take safely

1. Confirm the actual musicians appear once in the live room.
2. Let WebJam check the selected Takes folder and conservative free-space
   reserve. An unsafe result starts no take.
3. Start recording once, wait for recorder confirmation, play, then stop once.
4. Keep both Macs open while the take validates and any guest-original transfer
   settles.
5. Open Studio only after WebJam calls the take ready, or review a visible
   **Needs Attention** project without calling it complete.

WebJam keeps durable project identities, source hash/status, disclosed gaps,
alignment facts, and a bounded redacted lifecycle timeline. Local capture
flushes and checkpoints. A crash recovery is published as **Needs Attention**;
files existing never by themselves prove a complete take.

## Review in Studio

Studio is a simple review workspace, not a recording editor:

- the center shows horizontal waveform lanes on one elapsed-seconds ruler;
- the left side of each lane contains a track number/name/source plus meter,
  mute, solo, gain, and pan controls;
- the top transport plays, pauses, stops, and seeks the shared timeline;
- the selected-track inspector explains source, media status, alignment, gaps,
  and whether the track is included in the next export;
- a private `.webjam-studio-state.json` sidecar saves review choices by durable
  track ID without changing WAVs or `webjam-take.json`.

Studio intentionally has no beat grid, plug-ins, automation, destructive edits,
or fabricated tempo. Its ruler uses seconds because that is the shared timing
fact WebJam can verify for a rehearsal take.

## Export tracks

Choose **Export Tracks** only after reviewing the take. WebJam creates a new
atomic `Track Export`, `Track Export 2`, and so on. A package may include:

- numbered, aligned PCM24 WAV stems of one common length;
- `WebJam Server Reference.wav` and `WebJam Studio Reference.wav` when
  applicable;
- `MARKERS.csv`, alignment and recording reports, source evidence, independent
  analysis, and `CHECKSUMS.sha256`;
- `webjam-track-export.json`; and
- `IMPORT TRACKS.md`.

The export fails closed when selected media is missing, changed, incomplete,
explicitly silent, or lacks verified timeline alignment. The musician can keep
the trusted band-server track, deliberately leave out a reviewed source, or
resolve alignment first. Export never rewrites the original take.

## Use the package in another editor

1. Verify `CHECKSUMS.sha256`.
2. Create an empty project at the documented sample rate.
3. Import all numbered WAVs together at `0:00`, one per audio track.
4. Use reference WAVs for comparison only, not as additional performance
   tracks.
5. Review names, duration, gap placement, and alignment with the musicians.

The package is portable WAV interchange. WebJam makes no claim that another
editor opened it until a human records that observation in Test Night.

## Safe retry rules

- Re-run export after correcting the stated source/alignment issue; earlier
  folders stay intact.
- Never retry by deleting or overwriting a prior export.
- After a crash or interrupted recording, preserve the recovery project and
  inspect its gap evidence before making another take.
- End only after recording validation and any chosen transfer have settled.

See [CLOSED_PILOT_PLAYBOOK.md](CLOSED_PILOT_PLAYBOOK.md) for physical
certification and [SUNDAY_TWO_MAC_PILOT.md](SUNDAY_TWO_MAC_PILOT.md) for the
printable two-Mac worksheet.
