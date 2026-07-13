# Recording and Logic Pro workflow

WebJam records a rehearsal as a **take**: one synchronized Jamulus server WAV
for every connected musician, a validation manifest, and—when the host enables
it—two additional isolated interface-input stems. Recording belongs to the
host so every musician lands against the same server timeline.

## What WebJam records

| Source | Result | Required? |
|---|---|---|
| Jamulus server recorder | One WAV per connected musician | Yes |
| Host isolated capture | Input 1 and input 2 as separate 24-bit/48 kHz WAVs | Optional |

The optional host capture is useful when the host sends a combined performance
to Jamulus but wants separate guitar/vocal files afterward. It does not enter
or alter the live Jamulus mix.

Open **More → Multitrack Studio → Recording Setup** to:

- choose the wired playback output used by Studio;
- enable or disable the two isolated host inputs;
- choose the two-channel 48 kHz interface used for those inputs;
- reveal the take folder.

WebJam preserves this recording choice. The first-run Host/Join screen stays
small; recording details live with the recorder.

## Record and verify a take

1. Start the session and wait until the real musicians appear as armed Studio
   lanes.
2. The host presses **Record** or **Record Take**.
3. Play, then press **Stop Rec** or **Stop Recording**.
4. WebJam waits for stable server files, attaches any local stems atomically,
   aligns them, validates track count/readability/48 kHz, and writes
   `webjam-take.json`.
5. Studio opens the completed take. A failed stop remains visibly recording
   until the host retries; a partial capture is preserved instead of deleted.

The manifest stores the session title and the musician names WebJam observed,
so Studio does not have to expose Jamulus recorder filenames as track names.

## Review in Studio

Studio provides a non-destructive rehearsal mix:

- waveform lanes and a shared time ruler;
- play, pause, stop, and scrub;
- per-track gain, pan, mute, and solo;
- a selectable wired playback output;
- validation errors/warnings next to the take.

These controls never rewrite the original recorder WAVs.

## Export for Logic Pro

Select a completed take and press **Export for Logic**. WebJam creates a new,
numbered `Logic Exports/Logic Export…` folder inside that take. It is published
atomically only after every output succeeds and contains:

- one numbered, musician-named **24-bit PCM WAV stem** per source track;
- every stem padded or trimmed onto the same zero-based timeline and written
  to exactly the same length;
- `WebJam Rough Mix.wav`, reflecting the current gain/pan/mute/solo controls;
- `webjam-logic-export.json`, recording source names, original signed offsets,
  format, and mix settings;
- `IMPORT INTO LOGIC PRO.md` with the handoff steps.

The raw take remains unchanged. WebJam intentionally does not generate a
proprietary `.logicx` package or automate Logic with keystrokes.

To import:

1. Create an empty Logic project at the sample rate named in the export
   instructions (the pilot requires 48 kHz).
2. Select all **numbered stem WAVs** and drag them together into the empty
   Tracks area at `0:00`, one file per new audio track.
3. Do not include `WebJam Rough Mix.wav` as another stem; use it only as a
   listening reference.
4. Play from the beginning and confirm every track is audible and aligned.

Logic supports WAV/Broadcast Wave audio and creates regions for imported audio
files; Apple documents dragging files into the Tracks area in the
[Logic Pro User Guide](https://support.apple.com/guide/logicpro/import-media-files-lgcp71b8397b/mac).

## Acceptance gate

A candidate recording path passes only when:

- the manifest is **complete** with the expected musician count;
- every expected WAV is non-empty, readable, and 48 kHz;
- optional input 1/input 2 stems exist when enabled and have confident
  alignment;
- Studio playback reaches both headphone channels and pan/mute/solo/gain work;
- the Logic export contains equal-length numbered stems and a readable 24-bit
  rough mix;
- the numbered stems import at `0:00` and remain audible and aligned;
- End Session leaves no recorder, Jamulus, server, or `caffeinate` process.

Any missing track, mixed sample rate, failed stop, unaligned local capture, or
unreadable export blocks the recording gate. Keep the take folder and logs for
diagnosis; do not delete the partial audio.
