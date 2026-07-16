# Recording and Studio — v0.16

## Recording is separate from live music

Jamulus owns the live interface and mix. WebJam’s optional Local Originals are
a separate capture path for this Mac’s first two interface inputs. They are not
a prerequisite for a jam and they do not change Jamulus settings.

When the host presses **Record** for the first time, WebJam asks:

- **Record Shared Jam Only** — start the synchronized host take now.
- **Also Keep This Mac’s Inputs** — open Recording Setup and explicitly choose
  a valid two-channel Local Originals input before recording.

The host keeps shared recording authority. Guests can opt into Local Originals
only when the active private session supports them; they are never interrupted
while joining music.

## Recording readiness

WebJam checks takes storage, recorder control, the known roster, and any
explicit local-capture setting when Record is requested. It does not perform
those checks during Host or Join. A failed record preflight preserves the live
jam and explains the next safe action.

## Guest Local Originals

A guest's Local Original is preserved as soon as WebJam has verified its
transfer. That confirms the file arrived intact; it does not by itself put the
recording on the host take's timeline.

WebJam marks a guest original ready for an aligned export only after it finds
that same musician's verified Jamulus server reference and the recordings pass
a strict timing check. WebJam records which reference it used and checks that
reference is still intact when exporting. If the reference is missing or
changed, the capture has gaps, or the timing evidence is inconclusive, the
original stays available in Studio with waiting or unverified timing evidence.
It is kept for listening and manual review, but a selected aligned export waits
until it is verified or deliberately deselected rather than guessing where it
belongs. A manual nudge alone cannot turn an uncertain guest original into an
export-ready one.

This is software evidence, not a claim that two physical systems were sample
synchronized or that an external editor has been tested.

## Studio

Studio opens from **More → Studio**. It is a Logic-like review workspace, not
a Logic integration:

- review verified takes and per-track truth;
- choose playback output only while reviewing a take;
- use non-destructive level, pan, mute, and solo choices;
- inspect waveforms, gaps, and source state;
- export aligned 24-bit stems and a rough mix for another editor.

Studio does not alter source recordings. Export never rewrites the original take.
Local Originals remain separate, preserved media and appear only when real
files are available.
