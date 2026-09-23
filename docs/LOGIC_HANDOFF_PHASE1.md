# Record along: Logic handoff, phase one

WebJam is one arc: **Jamulus band-together → Play along (Music) → Paint along
(Art) → Record along into Logic**. This writer supplies the Record-along star:
**SMF Type 1 MIDI plus aligned 24-bit stems**, with tempo and markers. MIDI is an
editable, playable part of the handoff, alongside the audio. Jeff’s Logic / Front
Stage direction is represented by portable files only; there is no deep Front
Stage integration here. Other DAWs/apps must support the exported SMF/WAV files.

This is an explicit offline export command for a stopped session. It writes a new
handoff folder containing aligned 24-bit PCM WAV stems, a Standard MIDI File,
constant-tempo metadata, and an import README. It does not open audio or MIDI
devices, play audio, record, or use the network. It is not wired to the Studio UI.

Run commands from the repository root with the project's Python environment:

```bash
python -m tools.logic_handoff --stub
```

The default destination root is `~/Music/WebJam/LogicHandoff`. To use a different
root:

```bash
python -m tools.logic_handoff --stub --destination-root /tmp/webjam-logic-handoffs
```

The stub creates four seconds at 48 kHz and 120 BPM: one mono bass stem, one stereo
melody stem, eight explicitly pitched MIDI notes, and Start/Middle markers. Its
source audio is generated in a private temporary directory and removed after the
export. The handoff outputs remain. The command prints a JSON receipt listing the
created folder and files, sample rate, and common frame count. Repeating the
command creates a separate handoff folder.

## Export a real stopped session

Stop recording and playback, finish any source-file writes, and identify the
actual audio and note data to export. Create a JSON manifest with the session's
sample rate, exact shared duration in frames, constant BPM, and selected sources:

```json
{
  "name": "Evening rehearsal",
  "sample_rate": 48000,
  "frames": 192000,
  "bpm": 120,
  "numerator": 4,
  "denominator": 4,
  "stems": [
    {"name": "Guitar", "path": "audio/guitar.wav", "start_frame": 0},
    {"name": "Keys", "path": "audio/keys.aiff", "start_frame": 24000}
  ],
  "midi_tracks": [
    {
      "name": "Keys notes",
      "notes": [
        {"start_frame": 24000, "duration_frames": 18000, "note": 60, "velocity": 100, "channel": 0},
        {"start_frame": 48000, "duration_frames": 18000, "note": 64, "velocity": 95, "channel": 0}
      ]
    }
  ],
  "markers": [
    {"name": "Start", "frame": 0},
    {"name": "Middle", "frame": 96000}
  ]
}
```

```bash
python -m tools.logic_handoff --session /path/to/session.json
```

Audio paths may be absolute or relative to the manifest's directory. All sources
must already be mono or stereo WAV/AIFF files at the declared sample rate.
`start_frame` positions the first frame of the entire source on the session
timeline; it defaults to zero. Each source must fit inside `frames` after this
offset. In the example, `keys.aiff` may contain at most 168000 frames.

The caller is responsible for choosing the correct source versions and supplying
their exact timeline alignment. The exporter does not infer timing from file
names, recording timestamps, or a Studio project; it does not compensate for
latency, render region edits, crop sources, or resample. Render edits and align
sources beforehand where needed. Each output WAV begins at the shared session
origin and contains exactly `frames` samples per channel, including leading or
trailing silence around its source. Mono stays mono; stereo stays stereo. Import
all exported WAVs at the same project origin.

The required top-level fields are `name`, `sample_rate`, `frames`, `bpm`, and
`stems`. Optional `midi_tracks` and `markers` default to empty arrays; the time
signature defaults to 4/4. Each MIDI track requires a `name` and `notes` array.
Each note requires integer `start_frame`, `duration_frames`, and MIDI `note`;
`velocity` defaults to 100 and zero-based `channel` defaults to 0. Each marker
requires a `name` and integer `frame`. All timing values use the session sample
rate and origin. MIDI pitches are 0–127, velocities are 1–127, and channels are
0–15. Note durations must be positive and notes must fit inside the session.
Overlapping notes with the same pitch and channel within a track are rejected.

Malformed JSON, duplicate fields, unknown fields, invalid types, unsupported
sources, and timing that cannot fit the session fail the command. In particular,
a tempo map cannot be passed as an extra field and silently ignored. `--stub` and
`--session` are mutually exclusive. Usage errors return exit status 2; export or
manifest errors return status 1; success returns status 0.

## Output and import boundary

The output folder contains numbered WAV stems, `session.mid`, `tempo.json`, and
`README.md`. The MIDI file contains tempo and time-signature metadata, marker
events, and supplied pitched notes. Audio-only sessions can omit MIDI tracks;
the MIDI file still carries the session timing metadata. Audio is not converted
to MIDI, and no live MIDI is captured. Instrument sounds and synthesizer patches
are not part of the MIDI handoff.

Phase one supports one constant BPM (5–990) and one time signature. Project
sample rates are 44100, 48000, 88200, 96000, 176400, or 192000 Hz. MIDI stores tempo as
an integer number of microseconds per quarter note; the generated README records
requested and effective BPM. Frame-based note and marker times are quantized to
MIDI ticks, and the final MIDI endpoint is within half a tick of the common audio
duration. Audio stem alignment remains exact in frames.

Follow the generated README: open `session.mid` as a **new project** in Logic to
load the tempo, meter, and markers; set the project sample rate before adding audio;
then drag all WAV stems together to bar 1 / time zero as separate tracks. Keep
original audio timing and disable automatic tempo matching/Flex stretching. Verify the resulting audio and MIDI alignment
in Logic, including the end of the session and any named markers.

Automated tests verify export structure, audio metadata/alignment, MIDI events,
and CLI behavior. Separately, the four-second stub was cold-imported into Logic
Pro 12.3.1: both WAVs and the MIDI occupied separate tracks from bar 1 to bar 3,
with a 48 kHz project, 120 BPM, 4/4, and Start/Middle markers. See
[evidence](evidence/logic-handoff-phase1-20260922/PRE_KAREN.md) for the observed
limits. Listening, other DAWs/apps and Front Stage integration were not tested.

## Focused checks

```bash
python -m pytest -q tests/test_logic_handoff.py tests/test_logic_handoff_cli.py
python -m tools.logic_handoff --stub --destination-root /tmp/webjam-logic-handoff-check
```

The stub is explicit synthetic evidence only. A real stopped-session export uses
`--session` with actual authorized local sources and actual note data.

Apple documents opening Standard MIDI Files as new projects with tempo and markers
in [Standard MIDI files](https://support.apple.com/guide/logicpro/standard-midi-files-lgcpdf6a3851/mac),
and the accepted BPM range in [Set the project tempo](https://support.apple.com/guide/logicpro/set-the-project-tempo-lgcpce0ff024/mac).
Set the sample rate before adding audio as described in
[Set the project sample rate](https://support.apple.com/guide/logicpro/set-the-project-sample-rate-lgcpce0958b8/mac).
These format references are not a substitute for observing the actual Logic import.
